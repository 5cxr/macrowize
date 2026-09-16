"""Where the numbers come from. Every macro traces to a CSV row, never the model."""

from __future__ import annotations

import pytest

from db import Base, engine
from nutrition import find_food, lookup, resolve, resolve_all, to_grams
from usda import UsdaFood


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Tables for the USDA cache, and no real network calls."""
    Base.metadata.create_all(engine)
    monkeypatch.setattr("usda.search", lambda query: None)
    yield
    Base.metadata.drop_all(engine)


def test_macros_are_scaled_from_the_csv_row() -> None:
    # roti: 264 kcal/100g, 40g per piece -> 2 pieces = 80g = 211.2 kcal
    item = resolve("roti", 2, "piece")
    assert item["grams"] == 80.0
    assert item["kcal"] == pytest.approx(211.2, abs=0.1)
    assert item["protein_g"] == pytest.approx(7.7, abs=0.1)


def test_an_unknown_food_returns_nothing_rather_than_a_guess() -> None:
    assert resolve("zzzznotafood", 1, "bowl") is None


def test_resolve_all_separates_hits_from_misses() -> None:
    resolved, unresolved = resolve_all([
        {"name": "roti", "quantity": 2, "unit": "piece"},
        {"name": "zzzznotafood", "quantity": 1, "unit": "bowl"},
    ])
    assert [r["food_name"] for r in resolved] == ["roti"]
    assert unresolved == ["zzzznotafood"]


def test_household_units_use_the_food_s_own_gram_weight() -> None:
    assert to_grams(2, "piece", find_food("roti")) == 80.0       # 40g each
    assert to_grams(1, "bowl", find_food("dal tadka")) == 150.0
    assert to_grams(1, "katori", find_food("dal tadka")) == 150.0, "katori is a bowl"


def test_real_mass_units_skip_the_guessing_entirely() -> None:
    assert to_grams(200, "g", find_food("roti")) == 200.0
    assert to_grams(250, "ml", find_food("curd")) == 250.0


def test_a_food_named_inside_a_phrase_is_still_found() -> None:
    assert find_food("a bowl of dal tadka").name == "dal tadka"
    assert find_food("chicken biryani").name == "chicken biryani", "not plain biryani"


def test_aliases_resolve_to_the_same_food() -> None:
    assert find_food("chapati") is find_food("roti")
    assert find_food("dahi") is find_food("curd")


def test_the_csv_is_used_before_the_api(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("usda.search", lambda q: calls.append(q))

    assert lookup("roti").name == "roti"
    assert calls == [], "a food in the CSV must not cost an API call"


def test_a_csv_miss_falls_through_to_usda(monkeypatch) -> None:
    monkeypatch.setattr(
        "usda.search",
        lambda q: UsdaFood("Strawberries, raw", 32.0, 0.7, 7.7, 0.3),
    )

    item = resolve("strawberry", 200, "g")
    assert item["food_name"] == "Strawberries, raw"
    assert item["kcal"] == pytest.approx(64.0), "32 kcal/100g scaled to 200g"


def test_a_usda_result_is_cached_so_the_second_lookup_is_free(monkeypatch) -> None:
    calls = []

    def once(query):
        calls.append(query)
        return UsdaFood("Strawberries, raw", 32.0, 0.7, 7.7, 0.3)

    monkeypatch.setattr("usda.search", once)

    lookup("strawberry")
    lookup("strawberry")
    assert calls == ["strawberry"], "the second lookup should come from the cache"
