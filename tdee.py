"""Deterministic TDEE and macro-target math (Mifflin-St Jeor).

No LLM involvement anywhere in this module by design -- every function here is a
pure function of its arguments so the numbers are reproducible and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"


class ActivityLevel(str, Enum):
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"


class GoalType(str, Enum):
    MAINTAIN = "maintain"
    CUT = "cut"
    BULK = "bulk"


ACTIVITY_MULTIPLIERS: dict[ActivityLevel, float] = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.ACTIVE: 1.725,
    ActivityLevel.VERY_ACTIVE: 1.9,
}

KCAL_DELTA: dict[GoalType, int] = {
    GoalType.MAINTAIN: 0,
    GoalType.CUT: -500,
    GoalType.BULK: +300,
}

# Protein is a band per goal, not a single number.
#   cut      1.8-2.4 -- highest, since a deficit makes the body readier to break
#                       down muscle for energy
#   bulk     1.6-2.2 -- calories already cover growth; more protein just displaces
#                       the carbs and fats the surplus needs
#   maintain 1.6-2.0 -- enough to hold lean mass and recover, no deficit pressure
PROTEIN_G_PER_KG: dict[GoalType, tuple[float, float]] = {
    GoalType.CUT: (1.8, 2.4),
    GoalType.MAINTAIN: (1.6, 2.0),
    GoalType.BULK: (1.6, 2.2),
}


@dataclass(frozen=True)
class Targets:
    """Computed daily targets for one profile + goal combination."""

    bmr: float
    tdee: float
    target_kcal: int
    target_protein_min_g: int
    target_protein_max_g: int
    kcal_clamped_to_bmr: bool

    @property
    def target_protein_mid_g(self) -> int:
        """Midpoint of the protein band, for anywhere a single number is needed."""
        return round((self.target_protein_min_g + self.target_protein_max_g) / 2)


def calculate_bmr(weight_kg: float, height_cm: float, age: int, sex: Sex) -> float:
    """Return basal metabolic rate in kcal/day via Mifflin-St Jeor."""
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    return base + 5.0 if sex is Sex.MALE else base - 161.0


def calculate_tdee(bmr: float, activity_level: ActivityLevel) -> float:
    """Scale BMR by the activity multiplier to get total daily energy expenditure."""
    return bmr * ACTIVITY_MULTIPLIERS[activity_level]


def calculate_target_kcal(tdee: float, goal_type: GoalType, bmr: float) -> tuple[int, bool]:
    """Apply the goal's calorie delta to TDEE, floored at BMR.

    Returns (target_kcal, was_clamped). A deficit is never allowed to push intake
    below resting expenditure; in practice only a cut on a light, sedentary body
    ever hits the floor.
    """
    raw = tdee + KCAL_DELTA[goal_type]
    if raw < bmr:
        return round(bmr), True
    return round(raw), False


def calculate_target_protein_g(weight_kg: float, goal_type: GoalType) -> tuple[int, int]:
    """Return the (min, max) daily protein band in grams for a bodyweight and goal."""
    low, high = PROTEIN_G_PER_KG[goal_type]
    return round(weight_kg * low), round(weight_kg * high)


def compute_targets(
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: Sex,
    activity_level: ActivityLevel,
    goal_type: GoalType,
) -> Targets:
    """Run the full chain: BMR -> TDEE -> calorie and protein targets."""
    bmr = calculate_bmr(weight_kg, height_cm, age, sex)
    tdee = calculate_tdee(bmr, activity_level)
    target_kcal, clamped = calculate_target_kcal(tdee, goal_type, bmr)
    protein_min, protein_max = calculate_target_protein_g(weight_kg, goal_type)
    return Targets(
        bmr=bmr,
        tdee=tdee,
        target_kcal=target_kcal,
        target_protein_min_g=protein_min,
        target_protein_max_g=protein_max,
        kcal_clamped_to_bmr=clamped,
    )
