"""Thin wrapper around the USDA FoodData Central search API.

Only the four macros are pulled out. Everything is per 100 g, matching FoodCache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from config import USDA_API_KEY, USDA_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# FoodData Central nutrient ids for the four macros we care about.
NUTRIENT_IDS = {
    1008: "kcal_per_100g",      # Energy (kcal)
    1003: "protein_g_per_100g",
    1005: "carbs_g_per_100g",   # Carbohydrate, by difference
    1004: "fat_g_per_100g",     # Total lipid (fat)
}

# Curated data beats branded user-submitted entries for generic foods.
PREFERRED_DATA_TYPES = ["Foundation", "SR Legacy", "Survey (FNDDS)"]


@dataclass(frozen=True)
class UsdaFood:
    """Per-100g macros for one USDA food, plus what it was actually matched to."""

    description: str
    fdc_id: int
    kcal_per_100g: float
    protein_g_per_100g: float
    carbs_g_per_100g: float
    fat_g_per_100g: float


class UsdaClient:
    """Searches FoodData Central. Returns None rather than raising on failure."""

    def __init__(self, api_key: str = USDA_API_KEY, timeout: float = USDA_TIMEOUT_SECONDS):
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()

    def search(self, query: str) -> UsdaFood | None:
        """Return per-100g macros for the best match, or None if nothing usable."""
        try:
            response = self._session.get(
                SEARCH_URL,
                params={
                    "api_key": self.api_key,
                    "query": query,
                    "dataType": PREFERRED_DATA_TYPES,
                    "pageSize": 5,
                    "requireAllWords": "true",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("USDA lookup failed for %r: %s", query, exc)
            return None

        for food in response.json().get("foods", []):
            parsed = self._parse_food(food)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_food(food: dict) -> UsdaFood | None:
        """Pull the four macros out of one search hit; None if energy is missing."""
        macros: dict[str, float] = {}
        for nutrient in food.get("foodNutrients", []):
            field = NUTRIENT_IDS.get(nutrient.get("nutrientId"))
            if field is not None and nutrient.get("value") is not None:
                macros[field] = float(nutrient["value"])

        if "kcal_per_100g" not in macros:
            return None

        return UsdaFood(
            description=food.get("description", "unknown"),
            fdc_id=int(food.get("fdcId", 0)),
            kcal_per_100g=macros["kcal_per_100g"],
            protein_g_per_100g=macros.get("protein_g_per_100g", 0.0),
            carbs_g_per_100g=macros.get("carbs_g_per_100g", 0.0),
            fat_g_per_100g=macros.get("fat_g_per_100g", 0.0),
        )
