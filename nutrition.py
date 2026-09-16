"""Food lookup and quantity conversion -- the source of every number in the app.

Local CSV first (it covers Indian home cooking), then USDA for the long tail,
cached so repeats are free. If all three miss, the food comes back unresolved
rather than being given an invented value.
"""

from __future__ import annotations

import csv
import functools
from dataclasses import dataclass
from pathlib import Path

import usda

CSV_PATH = Path(__file__).resolve().parent / "indian_foods.csv"

# Real mass and volume units, where no guessing is involved. Volume is treated
# as 1 g/ml, which is fine for milk, dal and curd.
EXACT_UNITS = {
    "g": 1.0, "gm": 1.0, "gms": 1.0, "gram": 1.0, "grams": 1.0,
    "kg": 1000.0, "kilo": 1000.0, "kilogram": 1000.0,
    "ml": 1.0, "l": 1000.0, "litre": 1000.0, "liter": 1000.0,
    "tbsp": 15.0, "tablespoon": 15.0, "tsp": 5.0, "teaspoon": 5.0,
}

# Everything else collapses onto one of the three gram columns in the CSV.
UNIT_SLOTS = {
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece",
    "no": "piece", "nos": "piece", "slice": "piece", "slices": "piece",
    "egg": "piece", "eggs": "piece", "roti": "piece", "rotis": "piece",
    "each": "piece", "whole": "piece",
    "bowl": "bowl", "bowls": "bowl", "katori": "bowl", "cup": "bowl",
    "cups": "bowl", "glass": "bowl", "glasses": "bowl", "mug": "bowl",
    "serving": "serving", "servings": "serving", "plate": "serving",
    "plates": "serving", "portion": "serving", "helping": "serving",
    "packet": "serving", "handful": "serving", "scoop": "serving",
}

FALLBACK_GRAMS = 100.0


@dataclass(frozen=True)
class Food:
    """Per-100g macros plus the household gram weights for this food."""

    name: str
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    grams: dict[str, float]  # piece / bowl / serving, whichever the CSV gives


def normalize(name: str) -> str:
    """Lowercase and collapse whitespace -- the key format used throughout."""
    return " ".join(name.strip().lower().split())


@functools.lru_cache(maxsize=1)
def load_foods() -> dict[str, Food]:
    """Load the CSV into a name-and-alias keyed index, once per process."""
    index: dict[str, Food] = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            food = Food(
                name=row["food_name"],
                kcal=float(row["kcal_per_100g"]),
                protein_g=float(row["protein_g_per_100g"]),
                carbs_g=float(row["carbs_g_per_100g"]),
                fat_g=float(row["fat_g_per_100g"]),
                grams={
                    slot: float(row[f"grams_per_{slot}"])
                    for slot in ("piece", "bowl", "serving")
                    if row[f"grams_per_{slot}"].strip()
                },
            )
            index[normalize(row["food_name"])] = food
            for alias in filter(None, row["aliases"].split("|")):
                index.setdefault(normalize(alias), food)
    return index


def find_food(name: str) -> Food | None:
    """Local CSV only: exact match, then longest substring so 'bowl of dal' hits 'dal'."""
    foods = load_foods()
    key = normalize(name)
    if key in foods:
        return foods[key]
    hits = [known for known in foods if known in key]
    return foods[max(hits, key=len)] if hits else None


def lookup(name: str) -> Food | None:
    """Resolve a food: local CSV, then the USDA cache, then the USDA API.

    The CSV wins because it covers Indian home cooking, which USDA is thin on.
    USDA covers the long tail the CSV was never going to have -- strawberries,
    broccoli, a protein bar.
    """
    local = find_food(name)
    if local is not None:
        return local

    import db  # local import: db imports nothing from here, but keep it one-way

    key = normalize(name)
    with db.session_scope() as session:
        cached = db.get_cached_food(session, key)
        if cached is None:
            hit = usda.search(name)
            if hit is None:
                return None
            cached = {
                "description": hit.description, "kcal": hit.kcal,
                "protein_g": hit.protein_g, "carbs_g": hit.carbs_g, "fat_g": hit.fat_g,
            }
            db.cache_food(session, key, cached)

    # USDA gives per-100g values and no household weights, so household units
    # fall back to FALLBACK_GRAMS for these.
    return Food(
        name=cached["description"],
        kcal=cached["kcal"],
        protein_g=cached["protein_g"],
        carbs_g=cached["carbs_g"],
        fat_g=cached["fat_g"],
        grams={},
    )


def to_grams(quantity: float, unit: str, food: Food) -> float:
    """Convert a spoken quantity to grams using this food's own weights."""
    unit = unit.strip().lower().rstrip(".")
    if unit in EXACT_UNITS:
        return quantity * EXACT_UNITS[unit]

    slot = UNIT_SLOTS.get(unit, "serving")
    per_unit = food.grams.get(slot)
    if per_unit is None:
        # No weight for that slot; any weight this food does have beats a guess.
        per_unit = next(iter(food.grams.values()), FALLBACK_GRAMS)
    return quantity * per_unit


def resolve(name: str, quantity: float, unit: str) -> dict | None:
    """Look the food up and scale its macros to the quantity eaten."""
    food = lookup(name)
    if food is None:
        return None

    grams = to_grams(quantity, unit, food)
    scale = grams / 100.0
    return {
        # What the user said, kept so a re-lookup after an edit asks the same
        # question. Re-querying with `food_name` would search USDA for its own
        # description and could resolve somewhere else entirely.
        "query": name,
        "food_name": food.name,
        "quantity": quantity,
        "unit": unit,
        "grams": round(grams, 1),
        "kcal": round(food.kcal * scale, 1),
        "protein_g": round(food.protein_g * scale, 1),
        "carbs_g": round(food.carbs_g * scale, 1),
        "fat_g": round(food.fat_g * scale, 1),
    }


def resolve_all(items) -> tuple[list[dict], list[str]]:
    """Resolve every extracted item. Returns (resolved, names that missed)."""
    resolved, unresolved = [], []
    for item in items:
        name = item.name if hasattr(item, "name") else item["name"]
        quantity = item.quantity if hasattr(item, "quantity") else item["quantity"]
        unit = item.unit if hasattr(item, "unit") else item["unit"]
        match = resolve(name, quantity, unit)
        (resolved if match else unresolved).append(match or name)
    return resolved, unresolved
