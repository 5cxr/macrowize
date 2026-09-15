"""Turn messy spoken quantities ("a bowl", "2 pieces") into grams.

The LLM is only trusted to report *what* the user said -- never to know that a roti
weighs 40g. Gram weights come from the CSV row for that food where it has one, and
otherwise from a per-category fallback table here.
"""

from __future__ import annotations

from dataclasses import dataclass

# Units that are already a mass -- no guessing involved.
MASS_UNITS_TO_GRAMS: dict[str, float] = {
    "g": 1.0,
    "gm": 1.0,
    "gms": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "kilo": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
}

# Volume units, treated as 1 g/ml. Fine for milk, dal, curd; rough for oil.
VOLUME_UNITS_TO_GRAMS: dict[str, float] = {
    "ml": 1.0,
    "millilitre": 1.0,
    "milliliter": 1.0,
    "l": 1000.0,
    "litre": 1000.0,
    "liter": 1000.0,
}

# Spoons are consistent enough across foods to live outside the category table.
FIXED_HOUSEHOLD_UNITS: dict[str, float] = {
    "tbsp": 15.0,
    "tablespoon": 15.0,
    "tablespoons": 15.0,
    "tsp": 5.0,
    "teaspoon": 5.0,
    "teaspoons": 5.0,
    "scoop": 30.0,
    "scoops": 30.0,
}

# Synonyms collapsed onto the three unit slots the CSV carries.
PIECE_ALIASES = {
    "piece", "pieces", "pc", "pcs", "no", "nos", "number", "unit", "units",
    "slice", "slices", "roti", "rotis", "chapati", "chapatis", "egg", "eggs",
    "whole", "each",
}
BOWL_ALIASES = {
    "bowl", "bowls", "katori", "katoris", "cup", "cups", "glass", "glasses",
    "mug", "mugs", "small bowl", "big bowl",
}
SERVING_ALIASES = {
    "serving", "servings", "plate", "plates", "portion", "portions", "helping",
    "helpings", "thali", "packet", "packets", "box", "boxes", "handful", "bunch",
}

# Fallback grams when the CSV row leaves a column blank, keyed by food category.
CATEGORY_DEFAULTS: dict[str, dict[str, float]] = {
    "flatbread": {"piece": 45.0, "bowl": 100.0, "serving": 90.0},
    "grain": {"piece": 80.0, "bowl": 150.0, "serving": 200.0},
    "dal": {"piece": 100.0, "bowl": 150.0, "serving": 200.0},
    "sabzi": {"piece": 100.0, "bowl": 150.0, "serving": 200.0},
    "dairy": {"piece": 50.0, "bowl": 150.0, "serving": 150.0},
    "nonveg": {"piece": 60.0, "bowl": 150.0, "serving": 200.0},
    "snack": {"piece": 40.0, "bowl": 80.0, "serving": 100.0},
    "sweet": {"piece": 35.0, "bowl": 120.0, "serving": 100.0},
    "fruit": {"piece": 130.0, "bowl": 150.0, "serving": 150.0},
    "beverage": {"piece": 200.0, "bowl": 200.0, "serving": 200.0},
    "fat": {"piece": 10.0, "bowl": 50.0, "serving": 14.0},
    "condiment": {"piece": 15.0, "bowl": 50.0, "serving": 15.0},
    "supplement": {"piece": 30.0, "bowl": 30.0, "serving": 30.0},
}

# Used when a food has no category at all -- a USDA hit, typically.
GENERIC_DEFAULTS: dict[str, float] = {"piece": 100.0, "bowl": 150.0, "serving": 150.0}


@dataclass(frozen=True)
class GramEstimate:
    """Result of a unit conversion, carrying how much guessing was involved."""

    grams: float
    basis: str        # "exact" | "per_food" | "per_category" | "generic"
    explanation: str

    @property
    def is_exact(self) -> bool:
        """True when the user gave a real mass or volume, so nothing was estimated."""
        return self.basis == "exact"


def normalize_unit(unit: str) -> str:
    """Lowercase and strip a unit string, collapsing trivial plurals."""
    return unit.strip().lower().rstrip(".")


def _slot_for_unit(unit: str) -> str | None:
    """Map a free-text unit onto the piece/bowl/serving slot it belongs to."""
    if unit in PIECE_ALIASES:
        return "piece"
    if unit in BOWL_ALIASES:
        return "bowl"
    if unit in SERVING_ALIASES:
        return "serving"
    return None


def to_grams(
    quantity: float,
    unit: str,
    *,
    category: str | None = None,
    grams_per_piece: float | None = None,
    grams_per_bowl: float | None = None,
    grams_per_serving: float | None = None,
) -> GramEstimate:
    """Convert `quantity` of `unit` into grams.

    Resolution order: real mass/volume units win outright; otherwise the food's own
    gram weight from the CSV; then the category fallback; then a generic guess.
    """
    normalized = normalize_unit(unit)

    if normalized in MASS_UNITS_TO_GRAMS:
        grams = quantity * MASS_UNITS_TO_GRAMS[normalized]
        return GramEstimate(grams, "exact", f"{quantity:g} {normalized} = {grams:g} g")

    if normalized in VOLUME_UNITS_TO_GRAMS:
        grams = quantity * VOLUME_UNITS_TO_GRAMS[normalized]
        return GramEstimate(
            grams, "exact", f"{quantity:g} {normalized} at 1 g/ml = {grams:g} g"
        )

    if normalized in FIXED_HOUSEHOLD_UNITS:
        per = FIXED_HOUSEHOLD_UNITS[normalized]
        return GramEstimate(
            quantity * per, "per_category", f"1 {normalized} taken as {per:g} g"
        )

    slot = _slot_for_unit(normalized)
    if slot is None:
        # Unrecognized unit -- treat it as a serving rather than dropping the food.
        slot = "serving"

    per_food = {
        "piece": grams_per_piece,
        "bowl": grams_per_bowl,
        "serving": grams_per_serving,
    }[slot]

    if per_food:
        return GramEstimate(
            quantity * per_food, "per_food", f"1 {slot} of this food ~ {per_food:g} g"
        )

    if category and category in CATEGORY_DEFAULTS:
        per = CATEGORY_DEFAULTS[category][slot]
        return GramEstimate(
            quantity * per, "per_category", f"1 {slot} of {category} ~ {per:g} g"
        )

    per = GENERIC_DEFAULTS[slot]
    return GramEstimate(quantity * per, "generic", f"1 {slot} assumed ~ {per:g} g")
