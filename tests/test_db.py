"""Round-trip tests for meal persistence and the daily tally aggregation."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from models.db import (
    get_daily_tally,
    get_meals_for_day,
    get_profile,
    save_meal,
    save_profile,
)

ROTI = dict(
    food_name="roti", quantity=2, unit="piece",
    kcal=240.0, protein_g=6.4, carbs_g=48.0, fat_g=1.6,
)
DAL = dict(
    food_name="dal", quantity=1, unit="bowl",
    kcal=180.0, protein_g=12.0, carbs_g=24.0, fat_g=3.0,
)

PROFILE = dict(
    height_cm=178.0, weight_kg=75.0, age=28, sex="male",
    activity_level="moderate", goal_type="cut",
    target_kcal=2178, target_protein_min_g=135, target_protein_max_g=180,
)


def test_save_meal_derives_totals_from_items(session) -> None:
    meal = save_meal(session, raw_text="2 rotis and dal", items=[ROTI, DAL])
    assert meal.total_kcal == 420.0
    assert meal.total_protein_g == 18.4


def test_items_survive_the_json_round_trip(session) -> None:
    save_meal(session, raw_text="2 rotis", items=[ROTI])
    session.commit()
    session.expunge_all()

    stored = get_meals_for_day(session)[0]
    assert stored.items[0]["food_name"] == "roti"
    assert stored.items[0]["unit"] == "piece"


def test_daily_tally_sums_every_meal_that_day(session) -> None:
    save_meal(session, raw_text="breakfast", items=[ROTI])
    save_meal(session, raw_text="lunch", items=[DAL])

    kcal, protein = get_daily_tally(session)
    assert kcal == 420.0
    assert protein == 18.4


def test_daily_tally_excludes_other_days(session) -> None:
    yesterday = datetime.now() - timedelta(days=1)
    save_meal(session, raw_text="yesterday's dinner", items=[ROTI], timestamp=yesterday)
    save_meal(session, raw_text="today's lunch", items=[DAL])

    kcal, protein = get_daily_tally(session)
    assert kcal == 180.0
    assert protein == 12.0

    past_kcal, _ = get_daily_tally(session, day=yesterday.date())
    assert past_kcal == 240.0


def test_daily_tally_is_zero_when_nothing_logged(session) -> None:
    assert get_daily_tally(session) == (0.0, 0.0)


def test_late_night_meal_counts_against_the_local_day(session) -> None:
    # 00:30 local -- would fall on the previous day if timestamps were stored in UTC.
    after_midnight = datetime.combine(date.today(), datetime.min.time()) + timedelta(minutes=30)
    save_meal(session, raw_text="midnight snack", items=[ROTI], timestamp=after_midnight)

    kcal, _ = get_daily_tally(session)
    assert kcal == 240.0


def test_profile_is_a_single_upserted_row(session) -> None:
    save_profile(session, **PROFILE)
    save_profile(session, **{**PROFILE, "weight_kg": 73.0, "target_kcal": 2150})
    session.commit()

    profile = get_profile(session)
    assert profile is not None
    assert profile.id == 1
    assert profile.weight_kg == 73.0
    assert profile.target_kcal == 2150


def test_get_profile_returns_none_before_onboarding(session) -> None:
    assert get_profile(session) is None
