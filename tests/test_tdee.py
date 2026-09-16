"""The goal maths. Pure functions -- no DB, no model, exact expected values."""

from __future__ import annotations

import pytest

from tdee import ActivityLevel, GoalType, Sex, compute_targets

# 75kg, 178cm, 28y male -> BMR 1727.5, TDEE at moderate 2677.625
MALE = dict(weight_kg=75.0, height_cm=178.0, age=28, sex=Sex.MALE)


def test_bmr_and_tdee_match_mifflin_st_jeor() -> None:
    targets = compute_targets(
        **MALE, activity_level=ActivityLevel.MODERATE, goal_type=GoalType.MAINTAIN
    )
    # 10*75 + 6.25*178 - 5*28 + 5 = 1727.5, then x1.55
    assert targets.bmr == pytest.approx(1727.5)
    assert targets.tdee == pytest.approx(2677.625)


@pytest.mark.parametrize(
    "activity_level,expected",
    [
        (ActivityLevel.SEDENTARY, 2073.0),
        (ActivityLevel.LIGHT, 2375.3125),
        (ActivityLevel.MODERATE, 2677.625),
        (ActivityLevel.ACTIVE, 2979.9375),
        (ActivityLevel.VERY_ACTIVE, 3282.25),
    ],
)
def test_every_activity_multiplier(activity_level, expected) -> None:
    targets = compute_targets(
        **MALE, activity_level=activity_level, goal_type=GoalType.MAINTAIN
    )
    assert targets.tdee == pytest.approx(expected)


@pytest.mark.parametrize(
    "goal_type,kcal,band",
    [
        (GoalType.CUT, 2178, (135, 180)),       # TDEE-500, 1.8-2.4 g/kg
        (GoalType.MAINTAIN, 2678, (120, 150)),  # TDEE,     1.6-2.0 g/kg
        (GoalType.BULK, 2978, (120, 165)),      # TDEE+300, 1.6-2.2 g/kg
    ],
)
def test_every_goal_type(goal_type, kcal, band) -> None:
    targets = compute_targets(
        **MALE, activity_level=ActivityLevel.MODERATE, goal_type=goal_type
    )
    assert targets.target_kcal == kcal
    assert (targets.target_protein_min_g, targets.target_protein_max_g) == band


def test_a_deficit_is_never_allowed_below_resting_burn() -> None:
    """50kg sedentary female: TDEE 1389, so TDEE-500 would be under her BMR."""
    targets = compute_targets(
        weight_kg=50.0, height_cm=155.0, age=30, sex=Sex.FEMALE,
        activity_level=ActivityLevel.SEDENTARY, goal_type=GoalType.CUT,
    )
    assert targets.bmr == pytest.approx(1157.75)
    assert targets.target_kcal == 1158, "clamped up to BMR, not 889"
    assert targets.kcal_clamped_to_bmr is True


def test_the_floor_never_fires_when_it_should_not() -> None:
    for weight in (45.0, 60.0, 95.0):
        for activity_level in ActivityLevel:
            for goal_type in GoalType:
                targets = compute_targets(
                    weight_kg=weight, height_cm=160.0, age=35, sex=Sex.FEMALE,
                    activity_level=activity_level, goal_type=goal_type,
                )
                assert targets.target_kcal >= round(targets.bmr)
