"""Unit tests for the deterministic TDEE/goal math. No DB, no LLM."""

from __future__ import annotations

import pytest

from calc.tdee import (
    PROTEIN_G_PER_KG,
    ActivityLevel,
    GoalType,
    Sex,
    calculate_bmr,
    calculate_target_kcal,
    calculate_target_protein_g,
    calculate_tdee,
    compute_targets,
)

# Reference male: 75kg, 178cm, 28y -> BMR 1727.5
MALE = dict(weight_kg=75.0, height_cm=178.0, age=28, sex=Sex.MALE)
MALE_BMR = 1727.5

# Reference female: 65kg, 165cm, 30y -> BMR 1370.25
FEMALE = dict(weight_kg=65.0, height_cm=165.0, age=30, sex=Sex.FEMALE)
FEMALE_BMR = 1370.25


def test_bmr_male_matches_mifflin_st_jeor() -> None:
    # 10*75 + 6.25*178 - 5*28 + 5
    assert calculate_bmr(**MALE) == pytest.approx(MALE_BMR)


def test_bmr_female_matches_mifflin_st_jeor() -> None:
    # 10*65 + 6.25*165 - 5*30 - 161
    assert calculate_bmr(**FEMALE) == pytest.approx(FEMALE_BMR)


def test_sex_difference_is_the_constant_offset() -> None:
    same_body = dict(weight_kg=70.0, height_cm=170.0, age=30)
    male = calculate_bmr(**same_body, sex=Sex.MALE)
    female = calculate_bmr(**same_body, sex=Sex.FEMALE)
    assert male - female == pytest.approx(166.0)


@pytest.mark.parametrize(
    "activity_level,expected_tdee",
    [
        (ActivityLevel.SEDENTARY, 2073.0),
        (ActivityLevel.LIGHT, 2375.3125),
        (ActivityLevel.MODERATE, 2677.625),
        (ActivityLevel.ACTIVE, 2979.9375),
        (ActivityLevel.VERY_ACTIVE, 3282.25),
    ],
)
def test_tdee_per_activity_level(
    activity_level: ActivityLevel, expected_tdee: float
) -> None:
    assert calculate_tdee(MALE_BMR, activity_level) == pytest.approx(expected_tdee)


def test_activity_multipliers_increase_monotonically() -> None:
    order = [
        ActivityLevel.SEDENTARY,
        ActivityLevel.LIGHT,
        ActivityLevel.MODERATE,
        ActivityLevel.ACTIVE,
        ActivityLevel.VERY_ACTIVE,
    ]
    tdees = [calculate_tdee(MALE_BMR, level) for level in order]
    assert tdees == sorted(tdees)


@pytest.mark.parametrize(
    "goal_type,expected_kcal",
    [
        (GoalType.MAINTAIN, 2678),
        (GoalType.CUT, 2178),
        (GoalType.BULK, 2978),
    ],
)
def test_target_kcal_per_goal_type(goal_type: GoalType, expected_kcal: int) -> None:
    kcal, clamped = calculate_target_kcal(2677.625, goal_type, MALE_BMR)
    assert kcal == expected_kcal
    assert clamped is False


@pytest.mark.parametrize(
    "goal_type,expected_band",
    [
        (GoalType.CUT, (144, 192)),      # 1.8-2.4 g/kg
        (GoalType.MAINTAIN, (128, 160)), # 1.6-2.0 g/kg
        (GoalType.BULK, (128, 176)),     # 1.6-2.2 g/kg
    ],
)
def test_target_protein_band_per_goal_type(
    goal_type: GoalType, expected_band: tuple[int, int]
) -> None:
    assert calculate_target_protein_g(80.0, goal_type) == expected_band


def test_protein_bands_match_the_agreed_ranges() -> None:
    weight = 80.0
    for goal_type, (low, high) in PROTEIN_G_PER_KG.items():
        band_min, band_max = calculate_target_protein_g(weight, goal_type)
        assert band_min == round(weight * low)
        assert band_max == round(weight * high)
        assert band_min < band_max


def test_cut_band_reaches_highest_of_the_three() -> None:
    weight = 80.0
    cut_max = calculate_target_protein_g(weight, GoalType.CUT)[1]
    maintain_max = calculate_target_protein_g(weight, GoalType.MAINTAIN)[1]
    bulk_max = calculate_target_protein_g(weight, GoalType.BULK)[1]
    assert cut_max > bulk_max > maintain_max


def test_protein_rounding_is_bankers_rounding() -> None:
    # 1.8 * 75 == 135.0, 2.4 * 75 == 180.0; but 1.6 * 65 == 104.0 and the .5 cases
    # round to even -- 2.1 * 75 == 157.5 -> 158.
    targets = compute_targets(
        **MALE, activity_level=ActivityLevel.MODERATE, goal_type=GoalType.CUT
    )
    assert targets.target_protein_mid_g == 158


def test_compute_targets_chains_the_whole_calculation() -> None:
    targets = compute_targets(
        **MALE,
        activity_level=ActivityLevel.MODERATE,
        goal_type=GoalType.CUT,
    )
    assert targets.bmr == pytest.approx(MALE_BMR)
    assert targets.tdee == pytest.approx(2677.625)
    assert targets.target_kcal == 2178
    assert (targets.target_protein_min_g, targets.target_protein_max_g) == (135, 180)
    assert targets.kcal_clamped_to_bmr is False


def test_cut_is_clamped_up_to_bmr_when_the_deficit_digs_too_deep() -> None:
    # 50kg, 155cm, 30y sedentary female: BMR 1157.75, TDEE 1389.3, raw cut 889.3
    targets = compute_targets(
        weight_kg=50.0,
        height_cm=155.0,
        age=30,
        sex=Sex.FEMALE,
        activity_level=ActivityLevel.SEDENTARY,
        goal_type=GoalType.CUT,
    )
    assert targets.bmr == pytest.approx(1157.75)
    assert targets.tdee == pytest.approx(1389.3)
    assert targets.target_kcal == 1158
    assert targets.kcal_clamped_to_bmr is True


def test_target_kcal_never_drops_below_bmr() -> None:
    for weight in (45.0, 50.0, 60.0, 75.0, 95.0):
        for activity_level in ActivityLevel:
            for goal_type in GoalType:
                targets = compute_targets(
                    weight_kg=weight,
                    height_cm=160.0,
                    age=35,
                    sex=Sex.FEMALE,
                    activity_level=activity_level,
                    goal_type=goal_type,
                )
                assert targets.target_kcal >= round(targets.bmr)


def test_maintain_and_bulk_never_hit_the_floor() -> None:
    # TDEE is at least 1.2x BMR, so only a cut can ever dip under it.
    for goal_type in (GoalType.MAINTAIN, GoalType.BULK):
        targets = compute_targets(
            weight_kg=45.0,
            height_cm=150.0,
            age=60,
            sex=Sex.FEMALE,
            activity_level=ActivityLevel.SEDENTARY,
            goal_type=goal_type,
        )
        assert targets.kcal_clamped_to_bmr is False


def test_cut_is_500_below_and_bulk_300_above_maintain() -> None:
    # Well clear of the BMR floor, so the raw deltas apply untouched.
    kwargs = dict(**MALE, activity_level=ActivityLevel.MODERATE)
    maintain = compute_targets(**kwargs, goal_type=GoalType.MAINTAIN).target_kcal
    cut = compute_targets(**kwargs, goal_type=GoalType.CUT).target_kcal
    bulk = compute_targets(**kwargs, goal_type=GoalType.BULK).target_kcal
    assert maintain - cut == 500
    assert bulk - maintain == 300
