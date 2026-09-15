"""Resolve a spoken food + quantity into real macros.

Lookup order is cheapest-and-most-trusted first:
    1. FoodCache  -- already resolved once, per-100g values in SQLite
    2. indian_foods.csv -- hand-seeded, covers the Indian home food USDA is thin on
    3. USDA FoodData Central -- everything else

No step in this module asks an LLM for a number. If all three miss, the food comes
back unresolved and the confirm step shows it as such rather than inventing a value.
"""

from __future__ import annotations

import csv
import functools
from dataclasses import dataclass

from sqlalchemy.orm import Session

from config import INDIAN_FOODS_CSV
from models.db import FoodCache
from nutrition.unit_conversion import GramEstimate, to_grams
from nutrition.usda_client import UsdaClient


@dataclass(frozen=True)
class FoodRecord:
    """Per-100g macros for a food, plus the unit weights needed to scale them."""

    food_name: str
    kcal_per_100g: float
    protein_g_per_100g: float
    carbs_g_per_100g: float
    fat_g_per_100g: float
    source: str
    category: str | None = None
    grams_per_piece: float | None = None
    grams_per_bowl: float | None = None
    grams_per_serving: float | None = None


@dataclass(frozen=True)
class ResolvedItem:
    """One food scaled to the quantity the user actually said."""

    food_name: str
    matched_name: str
    quantity: float
    unit: str
    grams: float
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    source: str
    estimate_basis: str
    estimate_note: str

    def to_meal_item(self) -> dict[str, float | str]:
        """Shape this for MealItem / the MealLog.items JSON column."""
        return {
            "food_name": self.matched_name,
            "quantity": self.quantity,
            "unit": self.unit,
            "kcal": round(self.kcal, 1),
            "protein_g": round(self.protein_g, 1),
            "carbs_g": round(self.carbs_g, 1),
            "fat_g": round(self.fat_g, 1),
        }


def normalize_name(name: str) -> str:
    """Lowercase, collapse whitespace -- the key format used everywhere."""
    return " ".join(name.strip().lower().split())


def _to_float(value: str) -> float | None:
    """Parse a CSV cell that may legitimately be blank."""
    return float(value) if value.strip() else None


@functools.lru_cache(maxsize=1)
def load_indian_foods() -> dict[str, FoodRecord]:
    """Load the CSV into a name+alias keyed index. Cached for the process lifetime."""
    index: dict[str, FoodRecord] = {}
    with open(INDIAN_FOODS_CSV, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            record = FoodRecord(
                food_name=row["food_name"],
                kcal_per_100g=float(row["kcal_per_100g"]),
                protein_g_per_100g=float(row["protein_g_per_100g"]),
                carbs_g_per_100g=float(row["carbs_g_per_100g"]),
                fat_g_per_100g=float(row["fat_g_per_100g"]),
                source="indian_csv",
                category=row["category"] or None,
                grams_per_piece=_to_float(row["grams_per_piece"]),
                grams_per_bowl=_to_float(row["grams_per_bowl"]),
                grams_per_serving=_to_float(row["grams_per_serving"]),
            )
            index[normalize_name(row["food_name"])] = record
            for alias in filter(None, row["aliases"].split("|")):
                index.setdefault(normalize_name(alias), record)
    return index


def _from_cache(session: Session, key: str) -> FoodRecord | None:
    """Return a previously resolved food from SQLite, if present."""
    cached = session.get(FoodCache, key)
    if cached is None:
        return None
    return FoodRecord(
        food_name=cached.food_name,
        kcal_per_100g=cached.kcal_per_100g,
        protein_g_per_100g=cached.protein_g_per_100g,
        carbs_g_per_100g=cached.carbs_g_per_100g,
        fat_g_per_100g=cached.fat_g_per_100g,
        source=cached.source,
    )


def _cache_record(session: Session, key: str, record: FoodRecord) -> None:
    """Persist a USDA hit so the same food doesn't re-hit the API."""
    session.merge(
        FoodCache(
            food_name=key,
            kcal_per_100g=record.kcal_per_100g,
            protein_g_per_100g=record.protein_g_per_100g,
            carbs_g_per_100g=record.carbs_g_per_100g,
            fat_g_per_100g=record.fat_g_per_100g,
            source=record.source,
        )
    )


def find_food(
    session: Session, food_name: str, *, usda_client: UsdaClient | None = None
) -> FoodRecord | None:
    """Resolve a food name to per-100g macros via cache, then CSV, then USDA."""
    key = normalize_name(food_name)

    csv_index = load_indian_foods()
    if key in csv_index:
        return csv_index[key]

    # Partial match: "bowl of dal tadka" -> "dal tadka". Longest name wins so
    # "chicken biryani" beats a bare "biryani".
    substring_hits = [name for name in csv_index if name in key]
    if substring_hits:
        return csv_index[max(substring_hits, key=len)]

    cached = _from_cache(session, key)
    if cached is not None:
        return cached

    client = usda_client or UsdaClient()
    hit = client.search(food_name)
    if hit is None:
        return None

    record = FoodRecord(
        food_name=hit.description,
        kcal_per_100g=hit.kcal_per_100g,
        protein_g_per_100g=hit.protein_g_per_100g,
        carbs_g_per_100g=hit.carbs_g_per_100g,
        fat_g_per_100g=hit.fat_g_per_100g,
        source="usda",
    )
    _cache_record(session, key, record)
    return record


def resolve_item(
    session: Session,
    food_name: str,
    quantity: float,
    unit: str,
    *,
    usda_client: UsdaClient | None = None,
) -> ResolvedItem | None:
    """Look the food up, convert its quantity to grams, and scale the macros."""
    record = find_food(session, food_name, usda_client=usda_client)
    if record is None:
        return None

    estimate: GramEstimate = to_grams(
        quantity,
        unit,
        category=record.category,
        grams_per_piece=record.grams_per_piece,
        grams_per_bowl=record.grams_per_bowl,
        grams_per_serving=record.grams_per_serving,
    )

    scale = estimate.grams / 100.0
    return ResolvedItem(
        food_name=food_name,
        matched_name=record.food_name,
        quantity=quantity,
        unit=unit,
        grams=estimate.grams,
        kcal=record.kcal_per_100g * scale,
        protein_g=record.protein_g_per_100g * scale,
        carbs_g=record.carbs_g_per_100g * scale,
        fat_g=record.fat_g_per_100g * scale,
        source=record.source,
        estimate_basis=estimate.basis,
        estimate_note=estimate.explanation,
    )
