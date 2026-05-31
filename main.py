import json
import math
import csv
import io
import requests
from flask import Flask, request, render_template_string, Response, jsonify

app = Flask(__name__)

ESI_NAMES_URL = "https://esi.evetech.net/latest/universe/names/"
ESI_IDS_URL   = "https://esi.evetech.net/latest/universe/ids/"
BP_FILE       = "blueprints.jsonl"

# ---- NAME RESOLVER ----
class NameResolver:
    def __init__(self):
        self.cache = {}
    def resolve(self, ids):
        ids = [i for i in set(ids) if i not in self.cache]
        if not ids:
            return
        r = requests.post(ESI_NAMES_URL, json=ids)
        r.raise_for_status()
        for x in r.json():
            self.cache[x["id"]] = x["name"]
    def get(self, id_):
        if id_ not in self.cache:
            self.resolve([id_])
        return self.cache.get(id_, str(id_))

RESOLVER = NameResolver()

# ---- LOAD RECIPES ----
def load_recipes(file=BP_FILE):
    recipes = {}
    try:
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except:
                    continue
                m = obj.get("activities", {}).get("manufacturing")
                if not m:
                    continue
                materials = m.get("materials")
                products  = m.get("products")
                if not materials or not products:
                    continue
                product = products[0]
                recipes[product["typeID"]] = {
                    "output_qty":        product.get("quantity", 1),
                    "materials":         materials,
                    "time":              m.get("time", 0),
                    "skillRequirements": m.get("skillRequirements", []),
                }
    except FileNotFoundError:
        pass
    return recipes

RECIPES = load_recipes()

# ---- ESI SEARCH ----
def search_bp(name: str):
    try:
        r = requests.post(ESI_IDS_URL, json=[name])
        r.raise_for_status()
        types = r.json().get("inventory_types", [])
        if len(types) == 1:
            return types[0]["id"]
    except:
        pass
    return -1

# ---- MATERIAL FORMULA ----
def apply_me(quantity, me):
    waste = 0.1 / (1 + max(me, 0))
    return math.floor(quantity * (1 + waste))

def apply_modifiers(qty, me=0, structure_bonus=0.0, skill_bonus=0.0):
    base = apply_me(qty, me)
    return math.floor(base * (1 - structure_bonus) * (1 - skill_bonus))

# ---- TIME FORMULA ----
def compute_time(base_seconds, runs, te=0, structure_time_bonus=0.0,
                 industry_level=0, adv_industry_level=0):
    te_mult     = 1 - (max(0, min(20, te)) * 0.02)
    struct_mult = 1 - structure_time_bonus
    ind_mult    = 1 - (industry_level * 0.04)
    adv_mult    = 1 - (adv_industry_level * 0.03)
    return max(1, math.floor(base_seconds * runs * te_mult * struct_mult * ind_mult * adv_mult))

def fmt_time(seconds):
    if not seconds:
        return ""
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

# ---- TREE ----
def compute_tree(item_id, qty, recipes, me=0, structure_bonus=0.0, skill_bonus=0.0,
                 te=0, structure_time_bonus=0.0, industry_level=0, adv_industry_level=0):
    if item_id not in recipes:
        return {"id": item_id, "qty": qty, "raw": True, "children": []}
    recipe = recipes[item_id]
    runs   = math.ceil(qty / recipe["output_qty"])
    time_s = compute_time(recipe["time"], runs, te, structure_time_bonus, industry_level, adv_industry_level)
    node = {
        "id": item_id, "qty": qty, "runs": runs, "raw": False,
        "time_secs": time_s, "time_fmt": fmt_time(time_s),
        "skillRequirements": recipe["skillRequirements"],
        "children": [],
    }
    for mat in recipe["materials"]:
        base_qty  = mat["quantity"] * runs
        final_qty = apply_modifiers(base_qty, me, structure_bonus, skill_bonus)
        child = compute_tree(mat["typeID"], final_qty, recipes, me, structure_bonus, skill_bonus,
                             te, structure_time_bonus, industry_level, adv_industry_level)
        node["children"].append(child)
    return node

def attach_names(node, resolver):
    node["name"] = resolver.get(node["id"])
    for sk in node.get("skillRequirements", []):
        sk["name"] = resolver.get(sk["typeID"])
    for c in node["children"]:
        attach_names(c, resolver)
    return node

def build_tree(item_id, qty, recipes, me=10, structure_bonus=0.1, skill_bonus=0.04,
               te=0, structure_time_bonus=0.0, industry_level=5, adv_industry_level=5):
    tree = compute_tree(item_id, qty, recipes, me, structure_bonus, skill_bonus,
                        te, structure_time_bonus, industry_level, adv_industry_level)
    return attach_names(tree, RESOLVER)

def flatten_tree(node, depth=0, rows=None):
    if rows is None:
        rows = []
    rows.append({
        "depth": depth, "name": node.get("name", str(node["id"])),
        "id": node["id"], "qty": node["qty"],
        "runs": node.get("runs", ""), "raw": node["raw"],
        "time_fmt": node.get("time_fmt", ""), "time_secs": node.get("time_secs", 0),
    })
    for c in node.get("children", []):
        flatten_tree(c, depth + 1, rows)
    return rows

def collect_skills(node, seen=None):
    if seen is None:
        seen = {}
    for sk in node.get("skillRequirements", []):
        tid = sk["typeID"]
        if tid not in seen or seen[tid]["level"] < sk["level"]:
            seen[tid] = {"typeID": tid, "name": sk.get("name", str(tid)), "level": sk["level"]}
    for c in node.get("children", []):
        collect_skills(c, seen)
    return seen

def collect_all_ids(node, ids=None):
    if ids is None:
        ids = set()
    ids.add(node["id"])
    for c in node.get("children", []):
        collect_all_ids(c, ids)
    return ids

# -----------------------------------------------------------------------
# TEMPLATE
# -----------------------------------------------------------------------
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>EVE Industry</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #111111;
  --bg2:      #191919;
  --bg3:      #1f1f1f;
  --border:   #2e2e2e;
  --border2:  #3a3a3a;
  --text:     #d4d4d4;
  --dim:      #6b6b6b;
  --dim2:     #4a4a4a;
  --hi:       #c9a84c;   /* gold — ISK / values */
  --hi2:      #7aab8a;   /* green — crafted */
  --red:      #b05252;   /* raw */
  --blue:     #5b8db8;   /* links / actions */
  --purple:   #8878b0;   /* time */
  --mono: 'IBM Plex Mono', monospace;
  --sans: 'IBM Plex Sans', sans-serif;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.5;
  padding: 28px 32px;
  max-width: 1100px;
}

/* ---- HEADER ---- */
.page-title {
  font-size: 11px;
  color: var(--dim);
  letter-spacing: .12em;
  text-transform: uppercase;
  margin-bottom: 20px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

/* ---- SEARCH ---- */
.search-row {
  display: flex;
  gap: 6px;
  margin-bottom: 14px;
  align-items: center;
}
.search-row input {
  width: 280px;
  background: var(--bg2);
  border: 1px solid var(--border2);
  color: var(--text);
  font-family: var(--mono);
  font-size: 12px;
  padding: 5px 8px;
  outline: none;
}
.search-row input:focus { border-color: var(--blue); }
#search-status { font-size: 11px; color: var(--dim); margin-left: 4px; }

/* ---- FORM ---- */
form {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
  background: var(--bg2);
  border: 1px solid var(--border);
  padding: 14px 16px;
  margin-bottom: 18px;
  align-items: flex-end;
}
.field { display: flex; flex-direction: column; gap: 3px; }
label {
  font-size: 10px;
  color: var(--dim);
  text-transform: uppercase;
  letter-spacing: .1em;
}
input[type=number] {
  background: var(--bg);
  border: 1px solid var(--border2);
  color: var(--text);
  font-family: var(--mono);
  font-size: 12px;
  padding: 4px 7px;
  width: 88px;
  outline: none;
}
input[type=number]:focus { border-color: var(--blue); }
.form-btns { display: flex; gap: 6px; align-items: flex-end; }
button {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: .06em;
  padding: 5px 14px;
  border: 1px solid var(--border2);
  cursor: pointer;
  background: var(--bg3);
  color: var(--text);
  transition: background .1s, border-color .1s;
}
button:hover { background: var(--bg2); border-color: var(--blue); color: var(--blue); }
button.primary { background: var(--bg3); border-color: var(--blue); color: var(--blue); }
.sep { width: 1px; height: 32px; background: var(--border); margin: 0 2px; }

/* ---- PRICE STATUS ---- */
#price-bar {
  display: none;
  font-size: 11px;
  color: var(--dim);
  margin-bottom: 10px;
  padding: 6px 10px;
  background: var(--bg2);
  border: 1px solid var(--border);
}
#price-bar.loaded { color: var(--hi2); }
#price-bar.err    { color: var(--red); }

/* ---- SKILLS ---- */
.skills-block {
  border: 1px solid var(--border);
  margin-bottom: 16px;
  background: var(--bg2);
}
.block-head {
  font-size: 10px;
  color: var(--dim);
  letter-spacing: .1em;
  text-transform: uppercase;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--bg3);
}
.skills-inner {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px;
}
.skill-pill {
  font-size: 11px;
  padding: 2px 8px;
  border: 1px solid var(--border2);
  background: var(--bg);
  color: var(--text);
}
.skill-pill span { color: var(--dim); margin-left: 4px; }

/* ---- TABS ---- */
.tab-bar {
  display: flex;
  border-bottom: 1px solid var(--border);
  margin-bottom: 0;
}
.tab {
  font-size: 11px;
  padding: 6px 14px;
  cursor: pointer;
  color: var(--dim);
  background: none;
  border: 1px solid transparent;
  border-bottom: none;
  letter-spacing: .06em;
  text-transform: uppercase;
  transition: color .1s;
}
.tab:hover { color: var(--text); background: none; border-color: transparent; }
.tab.active { color: var(--text); border-color: var(--border); background: var(--bg); margin-bottom: -1px; }
.panel { display: none; }
.panel.active { display: block; }

/* ---- SHARED TABLE WRAPPER ---- */
.panel-wrap {
  border: 1px solid var(--border);
  border-top: none;
  background: var(--bg2);
}

/* ---- TREE VIEW ---- */
.tree-table { width: 100%; border-collapse: collapse; }
.tree-table th {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--dim);
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  background: var(--bg3);
  font-weight: 400;
}
.tree-table td {
  padding: 3px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.tree-table tr:last-child td { border-bottom: none; }
.tree-table tr:hover td { background: var(--bg3); }
.tree-indent { color: var(--dim2); user-select: none; }
.tree-name   { color: var(--text); }
.c-qty   { color: var(--hi); text-align: right; }
.c-runs  { color: var(--dim); text-align: right; }
.c-time  { color: var(--purple); text-align: right; font-size: 11px; }
.c-price { color: var(--hi); text-align: right; }
.c-type-raw   { color: var(--red); font-size: 10px; letter-spacing:.05em }
.c-type-craft { color: var(--hi2); font-size: 10px; letter-spacing:.05em }
.price-cell   { font-size: 12px; }
.price-total  { color: var(--hi); }
.price-each   { color: var(--dim); font-size: 10px; }

/* ---- SUMMARY GRIDS ---- */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 1px;
  background: var(--border);
  margin: 0;
}
.summary-grid > * { background: var(--bg2); }
.sum-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 5px 10px;
  gap: 8px;
}
.sum-row:hover { background: var(--bg3); }
.sum-name { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sum-qty  { color: var(--hi); white-space: nowrap; flex-shrink: 0; }
.sum-val  { color: var(--hi); font-size: 11px; white-space: nowrap; flex-shrink: 0; }
.sum-val.dim { color: var(--dim); }

/* ---- TIME PANEL ---- */
.time-header {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.time-header .label { color: var(--dim); font-size: 10px; text-transform:uppercase; letter-spacing:.08em; }
.time-header .val   { color: var(--purple); margin-left: 8px; }

/* ---- VALUE SUMMARY ---- */
.value-header {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  display: flex;
  gap: 28px;
  flex-wrap: wrap;
}
.vstat { display: flex; flex-direction: column; gap: 2px; }
.vstat .label { font-size: 10px; color: var(--dim); text-transform: uppercase; letter-spacing: .08em; }
.vstat .val   { font-size: 14px; color: var(--hi); }
.vstat .val.green { color: var(--hi2); }
.vstat .val.red   { color: var(--red); }

/* ---- VISUAL TREE ---- */
.tree-line {
  display: flex;
  align-items: baseline;
  gap: 0;
  line-height: 1.8;
  font-size: 12px;
  font-family: var(--mono);
  white-space: nowrap;
}
.tree-line:hover { background: var(--bg3); }
.tl-gutter { color: var(--dim2); user-select: none; flex-shrink: 0; }
.tl-name   { color: var(--text); }
.tl-qty    { color: var(--hi); margin-left: 10px; }
.tl-runs   { color: var(--dim); margin-left: 6px; font-size: 11px; }
.tl-time   { color: var(--purple); margin-left: 10px; font-size: 11px; }
.tl-price  { color: var(--dim); margin-left: 10px; font-size: 11px; }
.tl-raw    { color: var(--red); margin-left: 8px; font-size: 10px; text-transform:uppercase; letter-spacing:.05em; }
.tl-craft  { color: var(--hi2); margin-left: 8px; font-size: 10px; text-transform:uppercase; letter-spacing:.05em; }

/* ---- ERROR ---- */
.error {
  background: rgba(176,82,82,.08);
  border: 1px solid var(--red);
  color: var(--red);
  padding: 8px 12px;
  margin-bottom: 14px;
  font-size: 12px;
}
</style>
</head>
<body>

<div class="page-title">EVE Online — Industry Calculator</div>

<!-- SEARCH -->
<div class="search-row">
  <input id="search-input" type="text" placeholder="Search item name (e.g. Raven, Tritanium...)">
  <button onclick="searchItem()">Search</button>
  <span id="search-status"></span>
</div>

<!-- FORM -->
<form method="GET" action="/">
  <div class="field"><label>Item ID</label>
    <input id="item_id_field" name="item_id" type="number" value="{{ item_id or '' }}" placeholder="e.g. 17738" required></div>
  <div class="field"><label>Quantity</label>
    <input name="qty" type="number" value="{{ qty }}" min="1"></div>
  <div class="sep"></div>
  <div class="field"><label>ME (0–10)</label>
    <input name="me" type="number" value="{{ me }}" min="0" max="10"></div>
  <div class="field"><label>TE (0–20)</label>
    <input name="te" type="number" value="{{ te }}" min="0" max="20"></div>
  <div class="sep"></div>
  <div class="field"><label>Struct. Mat %</label>
    <input name="structure" type="number" step="0.01" value="{{ structure }}"></div>
  <div class="field"><label>Struct. Time %</label>
    <input name="structure_time" type="number" step="0.01" value="{{ structure_time }}"></div>
  <div class="field"><label>Skill Mat %</label>
    <input name="skill" type="number" step="0.01" value="{{ skill }}"></div>
  <div class="sep"></div>
  <div class="field"><label>Industry Lv</label>
    <input name="industry_level" type="number" value="{{ industry_level }}" min="0" max="5"></div>
  <div class="field"><label>Adv. Industry Lv</label>
    <input name="adv_industry_level" type="number" value="{{ adv_industry_level }}" min="0" max="5"></div>
  <div class="form-btns">
    <button type="submit" class="primary">Compute</button>
    {% if flat %}<a href="{{ csv_url }}"><button type="button">Export CSV</button></a>{% endif %}
  </div>
</form>

{% if error %}<div class="error">{{ error }}</div>{% endif %}

{% if flat %}

<div id="price-bar">Loading Jita prices...</div>

<!-- SKILLS -->
{% if all_skills %}
<div class="skills-block">
  <div class="block-head">Required skills</div>
  <div class="skills-inner">
    {% for sk in all_skills %}
    <div class="skill-pill">{{ sk.name }}<span>Lv {{ sk.level }}</span></div>
    {% endfor %}
  </div>
</div>
{% endif %}

<!-- TABS -->
<div class="tab-bar">
  <button class="tab active" onclick="switchTab('tree',this)">Tree</button>
  <button class="tab" onclick="switchTab('table',this)">Table</button>
  <button class="tab" onclick="switchTab('materials',this)">Raw Materials</button>
  <button class="tab" onclick="switchTab('time',this)">Time</button>
  <button class="tab" onclick="switchTab('value',this)">Value</button>
</div>

<!-- TREE -->
<div id="tab-tree" class="panel active"><div class="panel-wrap" style="padding:10px 14px;">
  <div id="tree-root"></div>
</div></div>

<!-- TABLE (flat, sortable) -->
<div id="tab-table" class="panel"><div class="panel-wrap">
<table class="tree-table" id="flat-table">
  <thead><tr>
    <th>Name</th>
    <th>Type ID</th>
    <th style="text-align:right">Qty</th>
    <th style="text-align:right">Runs</th>
    <th style="text-align:right">Time</th>
    <th style="text-align:right">Unit (Jita sell)</th>
    <th style="text-align:right">Total</th>
    <th>Kind</th>
  </tr></thead>
  <tbody>
  {% for row in flat %}
  <tr data-id="{{ row.id }}" data-qty="{{ row.qty }}">
    <td>{{ row.name }}</td>
    <td class="c-runs">{{ row.id }}</td>
    <td class="c-qty">{{ "{:,}".format(row.qty) }}</td>
    <td class="c-runs">{{ row.runs or '—' }}</td>
    <td class="c-time">{{ row.time_fmt or '—' }}</td>
    <td class="price-cell" data-unit=""><span class="price-each">—</span></td>
    <td class="price-cell" data-total=""><span class="price-total">—</span></td>
    <td class="{% if row.raw %}c-type-raw{% else %}c-type-craft{% endif %}">{{ 'raw' if row.raw else 'craft' }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
</div></div>

<!-- RAW MATERIALS -->
<div id="tab-materials" class="panel"><div class="panel-wrap">
  <div class="summary-grid">
  {% for name, qty, tid in raw_totals %}
  <div class="sum-row" data-id="{{ tid }}" data-qty="{{ qty }}">
    <span class="sum-name">{{ name }}</span>
    <span class="sum-qty">{{ "{:,}".format(qty) }}</span>
    <span class="sum-val dim" data-matval="">—</span>
  </div>
  {% endfor %}
  </div>
</div></div>

<!-- TIME -->
<div id="tab-time" class="panel"><div class="panel-wrap">
  <div class="time-header">
    <span class="label">Total (sequential)</span>
    <span class="val">{{ total_time_fmt }}</span>
  </div>
  <table class="tree-table">
    <thead><tr><th>Item</th><th style="text-align:right">Runs</th><th style="text-align:right">Time</th></tr></thead>
    <tbody>
    {% for row in flat if row.time_fmt %}
    <tr>
      <td>{{ row.name }}</td>
      <td class="c-runs">{{ row.runs }}</td>
      <td class="c-time">{{ row.time_fmt }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
</div></div>

<!-- VALUE -->
<div id="tab-value" class="panel"><div class="panel-wrap">
  <div class="value-header" id="value-header">
    <div class="vstat"><span class="label">Raw material cost (Jita sell)</span><span class="val" id="v-raw-cost">—</span></div>
    <div class="vstat"><span class="label">Output sell value (Jita sell)</span><span class="val" id="v-sell">—</span></div>
    <div class="vstat"><span class="label">Estimated margin</span><span class="val" id="v-margin">—</span></div>
  </div>
  <div class="summary-grid" id="value-detail"></div>
</div></div>

{% endif %}

<script>
// ---- ITEM IDS TO PRICE ----
const ALL_IDS = {{ all_ids | tojson }};
const ROOT_ID = {{ item_id or 'null' }};
const ROOT_QTY = {{ qty }};

const prices = {};   // typeID → { sell, buy }

function fmtISK(v) {
  if (v == null || isNaN(v)) return '—';
  if (v >= 1e12) return (v/1e12).toFixed(2) + ' T';
  if (v >= 1e9)  return (v/1e9).toFixed(2) + ' B';
  if (v >= 1e6)  return (v/1e6).toFixed(2) + ' M';
  if (v >= 1e3)  return (v/1e3).toFixed(1) + ' K';
  return v.toFixed(2);
}

async function loadPrices() {
  if (!ALL_IDS.length) return;
  const bar = document.getElementById('price-bar');
  bar.style.display = 'block';
  bar.textContent = 'Fetching Jita prices…';

  // chunk into 100s (ESI limit)
  const chunks = [];
  for (let i = 0; i < ALL_IDS.length; i += 100)
    chunks.push(ALL_IDS.slice(i, i + 100));

  try {
    for (const chunk of chunks) {
      const params = chunk.map(id => `type_id=${id}`).join('&');
      // ESI market region 10000002 = The Forge (Jita)
      // We fetch sell orders for Jita station 60003760
      // Use /markets/prices/ as a lightweight fallback — it's cached + no location filter
      // For proper Jita price use /markets/10000002/orders but that's paginated
      const r = await fetch(
        `https://esi.evetech.net/latest/markets/prices/?datasource=tranquility`,
        { headers: { 'Accept': 'application/json' } }
      );
      // We fetch all at once (it returns ALL types, ~10k items, ~400KB)
      if (r.ok) {
        const data = await r.json();
        for (const d of data)
          prices[d.type_id] = { sell: d.adjusted_price, buy: d.average_price };
      }
      break; // single call is enough, covers all types
    }
    applyPrices();
    bar.textContent = 'Prices loaded (ESI adjusted price — Jita-independent)';
    bar.className = 'loaded';
  } catch(e) {
    bar.textContent = 'Price fetch failed: ' + e.message;
    bar.className = 'err';
  }
}

function applyPrices() {
  // update tree + table rows
  document.querySelectorAll('tr[data-id]').forEach(row => {
    const id  = parseInt(row.dataset.id);
    const qty = parseInt(row.dataset.qty);
    const p   = prices[id];
    if (!p) return;
    const unit  = p.sell || p.average || 0;
    const total = unit * qty;
    const uCell = row.querySelector('[data-unit]');
    const tCell = row.querySelector('[data-total]');
    if (uCell) uCell.innerHTML = `<span class="price-each">${fmtISK(unit)}</span>`;
    if (tCell) tCell.innerHTML = `<span class="price-total">${fmtISK(total)}</span>`;
  });

  // raw materials tab
  document.querySelectorAll('[data-matval]').forEach(el => {
    const row = el.closest('[data-id]');
    if (!row) return;
    const id  = parseInt(row.dataset.id);
    const qty = parseInt(row.dataset.qty);
    const p   = prices[id];
    if (!p) return;
    const val = (p.sell || 0) * qty;
    el.textContent = fmtISK(val) + ' ISK';
    el.classList.remove('dim');
  });

  // value summary tab
  let rawCost = 0;
  document.querySelectorAll('#tab-materials [data-id]').forEach(row => {
    const id  = parseInt(row.dataset.id);
    const qty = parseInt(row.dataset.qty);
    const p   = prices[id];
    if (p) rawCost += (p.sell || 0) * qty;
  });

  const rootP   = prices[ROOT_ID];
  const sellVal = rootP ? (rootP.sell || 0) * ROOT_QTY : 0;
  const margin  = sellVal - rawCost;

  document.getElementById('v-raw-cost').textContent = fmtISK(rawCost) + ' ISK';
  if (sellVal) {
    document.getElementById('v-sell').textContent = fmtISK(sellVal) + ' ISK';
    const mEl = document.getElementById('v-margin');
    mEl.textContent = fmtISK(margin) + ' ISK';
    mEl.className = 'val ' + (margin >= 0 ? 'green' : 'red');
  }

  // detail breakdown
  const detail = document.getElementById('value-detail');
  detail.innerHTML = '';
  document.querySelectorAll('#tab-materials [data-id]').forEach(row => {
    const id  = parseInt(row.dataset.id);
    const qty = parseInt(row.dataset.qty);
    const name = row.querySelector('.sum-name').textContent;
    const p   = prices[id];
    const val = p ? (p.sell || 0) * qty : 0;
    const div = document.createElement('div');
    div.className = 'sum-row';
    div.innerHTML = `<span class="sum-name">${name}</span><span class="sum-qty">${fmtISK(qty)}</span><span class="sum-val">${fmtISK(val)} ISK</span>`;
    detail.appendChild(div);
  });
}

// ---- TABS ----
function switchTab(id, btn) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  btn.classList.add('active');
}

// ---- SEARCH ----
async function searchItem() {
  const q   = document.getElementById('search-input').value.trim();
  const st  = document.getElementById('search-status');
  if (!q) return;
  st.style.color = 'var(--dim)';
  st.textContent = 'searching…';
  try {
    const r = await fetch('/api/search?q=' + encodeURIComponent(q));
    const d = await r.json();
    if (d.id && d.id !== -1) {
      document.getElementById('item_id_field').value = d.id;
      st.style.color = 'var(--hi2)';
      st.textContent = d.name + ' [' + d.id + ']';
    } else {
      st.style.color = 'var(--red)';
      st.textContent = 'not found';
    }
  } catch(e) {
    st.style.color = 'var(--red)';
    st.textContent = 'error';
  }
}
document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') searchItem();
});

// ---- VISUAL TREE ----
const TREE_DATA = {{ tree | tojson }};

const treeNodes = []; // flat list of rendered nodes for price updates

function renderTreeLines(node, prefix, isLast, container) {
  const connector = isLast ? '└─ ' : '├─ ';
  const gutter    = prefix + connector;

  const line = document.createElement('div');
  line.className = 'tree-line';
  line.dataset.id  = node.id;
  line.dataset.qty = node.qty;

  line.innerHTML =
    `<span class="tl-gutter">${gutter}</span>` +
    `<span class="tl-name">${node.name || node.id}</span>` +
    `<span class="tl-qty">${Number(node.qty).toLocaleString()}</span>` +
    (node.runs  ? `<span class="tl-runs">×${node.runs} runs</span>` : '') +
    (node.time_fmt ? `<span class="tl-time">${node.time_fmt}</span>` : '') +
    `<span class="tl-price" data-unit="">—</span>` +
    `<span class="${node.raw ? 'tl-raw' : 'tl-craft'}">${node.raw ? 'raw' : 'craft'}</span>`;

  container.appendChild(line);
  treeNodes.push(line);

  if (node.children && node.children.length) {
    const childPrefix = prefix + (isLast ? '   ' : '│  ');
    node.children.forEach((child, i) => {
      renderTreeLines(child, childPrefix, i === node.children.length - 1, container);
    });
  }
}

function renderTree() {
  if (!TREE_DATA) return;
  const container = document.getElementById('tree-root');

  // root node (no connector)
  const rootLine = document.createElement('div');
  rootLine.className = 'tree-line';
  rootLine.dataset.id  = TREE_DATA.id;
  rootLine.dataset.qty = TREE_DATA.qty;
  rootLine.innerHTML =
    `<span class="tl-name">${TREE_DATA.name || TREE_DATA.id}</span>` +
    `<span class="tl-qty">${Number(TREE_DATA.qty).toLocaleString()}</span>` +
    (TREE_DATA.runs ? `<span class="tl-runs">×${TREE_DATA.runs} runs</span>` : '') +
    (TREE_DATA.time_fmt ? `<span class="tl-time">${TREE_DATA.time_fmt}</span>` : '') +
    `<span class="tl-price" data-unit="">—</span>` +
    `<span class="${TREE_DATA.raw ? 'tl-raw' : 'tl-craft'}">${TREE_DATA.raw ? 'raw' : 'craft'}</span>`;
  container.appendChild(rootLine);
  treeNodes.push(rootLine);

  if (TREE_DATA.children) {
    TREE_DATA.children.forEach((child, i) => {
      renderTreeLines(child, '', i === TREE_DATA.children.length - 1, container);
    });
  }
}

// patch applyPrices to update tree lines too
const _origApply = applyPrices;
applyPrices = function() {
  _origApply();
  treeNodes.forEach(line => {
    const id  = parseInt(line.dataset.id);
    const qty = parseInt(line.dataset.qty);
    const p   = prices[id];
    if (!p) return;
    const el = line.querySelector('[data-unit]');
    if (el) el.textContent = fmtISK((p.sell || 0) * qty) + ' ISK';
  });
};

// ---- INIT ----
{% if flat %}
renderTree();
loadPrices();
{% endif %}
</script>
</body>
</html>
"""

# -----------------------------------------------------------------------
# ROUTES
# -----------------------------------------------------------------------
@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"id": -1})
    item_id = search_bp(q)
    name = RESOLVER.get(item_id) if item_id != -1 else q
    return jsonify({"id": item_id, "name": name})

@app.route("/")
def index():
    item_id            = request.args.get("item_id",            type=int)
    qty                = request.args.get("qty",                1,    type=int)
    me                 = request.args.get("me",                 10,   type=int)
    te                 = request.args.get("te",                 0,    type=int)
    structure          = request.args.get("structure",          0.1,  type=float)
    structure_time     = request.args.get("structure_time",     0.0,  type=float)
    skill              = request.args.get("skill",              0.04, type=float)
    industry_level     = request.args.get("industry_level",     5,    type=int)
    adv_industry_level = request.args.get("adv_industry_level", 5,    type=int)

    flat = []
    raw_totals = []
    all_skills = []
    all_ids = []
    total_time_fmt = "—"
    error = None
    csv_url = "#"
    tree = None

    if item_id:
        try:
            tree = build_tree(
                item_id, qty, RECIPES,
                me=me, structure_bonus=structure, skill_bonus=skill,
                te=te, structure_time_bonus=structure_time,
                industry_level=industry_level, adv_industry_level=adv_industry_level,
            )
            flat = flatten_tree(tree)

            # raw totals with type IDs
            raw_map = {}
            for row in flat:
                if row["raw"]:
                    if row["id"] not in raw_map:
                        raw_map[row["id"]] = {"name": row["name"], "qty": 0}
                    raw_map[row["id"]]["qty"] += row["qty"]
            raw_totals = sorted(
                [(v["name"], v["qty"], k) for k, v in raw_map.items()],
                key=lambda x: -x[1]
            )

            skill_map  = collect_skills(tree)
            all_skills = sorted(skill_map.values(), key=lambda x: x["name"])

            total_secs     = sum(r["time_secs"] for r in flat if r["time_secs"])
            total_time_fmt = fmt_time(total_secs)

            all_ids = list(collect_all_ids(tree))

            p = (f"item_id={item_id}&qty={qty}&me={me}&te={te}"
                 f"&structure={structure}&structure_time={structure_time}"
                 f"&skill={skill}&industry_level={industry_level}"
                 f"&adv_industry_level={adv_industry_level}")
            csv_url = f"/export.csv?{p}"

        except Exception as e:
            error = str(e)

    return render_template_string(TEMPLATE,
        tree=tree, flat=flat, raw_totals=raw_totals,
        all_skills=all_skills, total_time_fmt=total_time_fmt,
        error=error, item_id=item_id, qty=qty,
        me=me, te=te, structure=structure, structure_time=structure_time,
        skill=skill, industry_level=industry_level, adv_industry_level=adv_industry_level,
        csv_url=csv_url, all_ids=all_ids,
    )

@app.route("/export.csv")
def export_csv():
    item_id            = request.args.get("item_id",            type=int)
    qty                = request.args.get("qty",                1,    type=int)
    me                 = request.args.get("me",                 10,   type=int)
    te                 = request.args.get("te",                 0,    type=int)
    structure          = request.args.get("structure",          0.1,  type=float)
    structure_time     = request.args.get("structure_time",     0.0,  type=float)
    skill              = request.args.get("skill",              0.04, type=float)
    industry_level     = request.args.get("industry_level",     5,    type=int)
    adv_industry_level = request.args.get("adv_industry_level", 5,    type=int)

    tree = build_tree(item_id, qty, RECIPES,
        me=me, structure_bonus=structure, skill_bonus=skill,
        te=te, structure_time_bonus=structure_time,
        industry_level=industry_level, adv_industry_level=adv_industry_level)
    flat = flatten_tree(tree)

    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=["name","id","qty","runs","time_fmt","time_secs","depth","raw"])
    w.writeheader()
    for row in flat:
        w.writerow(row)
    return Response(out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=industry_{item_id}_{qty}.csv"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)