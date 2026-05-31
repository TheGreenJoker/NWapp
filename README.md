Calculateur de fabrication EVE Online. Interface web locale pour explorer l'arbre de matériaux d'un blueprint, estimer les temps de production et les coûts en ISK.

## 📦 Téléchargement

👉 Dernières versions ici : https://github.com/TheGreenJoker/NWapp/releases

---

## Fonctionnalités

- **Recherche par nom** — tape le nom de l'item, l'ID est résolu via l'ESI
- **Arbre de matériaux** — vue hiérarchique `├─ / └─` avec quantités, runs et temps
- **Tableau** — vue à plat de tous les composants
- **Matières premières** — agrégat des matériaux de base avec coût total
- **Temps** — temps par job et total séquentiel de la chaîne
- **Valeur** — prix ESI (adjusted price), coût matières, valeur de revente, marge estimée
- **Export CSV** — dump complet de l'arbre

---

## Installation

**Prérequis :** Python 3.8+

```bash
pip install flask requests
```

---

## Utilisation

1. Place `blueprints.jsonl` dans le même dossier que `app.py`
2. Lance le serveur :

```bash
python app.py
```

3. Ouvre http://localhost:5000

---

## Paramètres

| Champ | Description | Défaut |
|---|---|---|
| Item ID | Type ID EVE du produit | — |
| Quantity | Nombre d'unités à produire | 1 |
| ME | Material Efficiency du blueprint (0–10) | 10 |
| TE | Time Efficiency du blueprint (0–20) | 0 |
| Struct. Mat % | Bonus matériaux de la structure (ex: 0.1 = −10%) | 0.1 |
| Struct. Time % | Bonus temps de la structure | 0.0 |
| Skill Mat % | Bonus matériaux via skills | 0.04 |
| Industry Lv | Niveau du skill Industry (0–5, −4%/lv temps) | 5 |
| Adv. Industry Lv | Niveau du skill Advanced Industry (0–5, −3%/lv temps) | 5 |

---

## Format blueprints.jsonl

Un blueprint par ligne, format SDE EVE Online standard :

```json
{"activities":{"manufacturing":{"materials":[...],"products":[{"quantity":1,"typeID":17738}],"time":3600,"skillRequirements":[...]}}}
```

Les fichiers SDE sont disponibles sur [Fuzzwork](https://www.fuzzwork.co.uk/dump/) ou [jeveassets](https://eve.nikr.net/jeveasset).

---

## Prix

Les prix sont chargés côté client depuis l'ESI (`/markets/prices/`) au chargement de la page. Il s'agit de l'**adjusted price** CCP — pas strictement le prix Jita sell, mais une bonne approximation pour les calculs de marge.