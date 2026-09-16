"""USDA FoodData Central lookup, for foods the local CSV doesn't cover.

The API's own ranking is not safe to trust: searching "salmon" returns *Fish oil,
salmon* (902 kcal/100g) and "avocado" returns *Oil, avocado* (884). Taking the
first hit would produce confident, badly wrong numbers -- the exact failure this
project exists to avoid.

So candidates are re-ranked here on one rule: **the query has to match the head of
the description**, the part before the first comma. "Strawberries, raw" has the head
"Strawberries" and matches; "Oil, avocado" has the head "Oil" and does not.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# Curated datasets only -- branded entries are inconsistent and brand-named.
DATA_TYPES = "Foundation,SR Legacy"

NUTRIENT_IDS = {1008: "kcal", 1003: "protein_g", 1005: "carbs_g", 1004: "fat_g"}

# Forms that are a different food from the thing asked for.
DERIVED_FORMS = {"oil", "juice", "powder", "extract", "concentrate", "syrup"}


@dataclass(frozen=True)
class UsdaFood:
    """Per-100g macros for one USDA result."""

    description: str
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


def _tokens(text: str) -> set[str]:
    """Lowercase alphabetic tokens, crudely singularised so plurals match."""
    words = re.findall(r"[a-z]+", text.lower())
    singular = set()
    for word in words:
        if word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("es") and len(word) > 4:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        singular.add(word)
    return singular


def score(query: str, description: str) -> int:
    """Rank one candidate. Higher is better; below 1 means reject.

    "Strawberries, raw" beats "SILK Strawberry soy yogurt" because its head is
    *only* the food asked for, while the other pads it with three extra words.
    """
    head = description.split(",")[0]
    query_tokens, head_tokens = _tokens(query), _tokens(head)

    overlap = query_tokens & head_tokens
    if not overlap:
        return 0  # the head isn't the food we asked for

    # A different form of the food is a different food: "Oil, avocado".
    if (head_tokens & DERIVED_FORMS) - query_tokens:
        return 0

    points = 10 * len(overlap)

    # Words in the head we didn't ask for: "Sea cucumber", "Spinach souffle".
    points -= 3 * len(head_tokens - query_tokens)

    # Shouty words are brand names: SILK, SOUTH BEACH, CHOBANI.
    if re.search(r"\b[A-Z]{3,}\b", description):
        points -= 10

    # Prefer the plainest entry: fewer trailing qualifiers.
    points -= description.count(",")
    return points


def _macros(food: dict) -> dict[str, float]:
    """Pull the four macros out of a search hit."""
    found = {}
    for nutrient in food.get("foodNutrients", []):
        field = NUTRIENT_IDS.get(nutrient.get("nutrientId"))
        if field and nutrient.get("value") is not None:
            found[field] = float(nutrient["value"])
    return found


def search(query: str) -> UsdaFood | None:
    """Best sensible match for `query`, or None. Never raises."""
    try:
        response = requests.get(
            SEARCH_URL,
            params={
                "api_key": os.getenv("USDA_API_KEY", "DEMO_KEY"),
                "query": query,
                "dataType": DATA_TYPES,  # comma-joined; repeating the param 400s
                "pageSize": 25,
                "requireAllWords": "true",
            },
            timeout=float(os.getenv("USDA_TIMEOUT", "10")),
        )
        response.raise_for_status()
        hits = response.json().get("foods", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("USDA lookup failed for %r: %s", query, exc)
        return None

    ranked = sorted(
        ((score(query, hit.get("description", "")), hit) for hit in hits),
        key=lambda pair: pair[0],
        reverse=True,
    )

    for points, hit in ranked:
        if points < 1:
            break
        macros = _macros(hit)
        if "kcal" not in macros:
            continue
        return UsdaFood(
            description=hit.get("description", query),
            kcal=macros["kcal"],
            protein_g=macros.get("protein_g", 0.0),
            carbs_g=macros.get("carbs_g", 0.0),
            fat_g=macros.get("fat_g", 0.0),
        )
    return None
