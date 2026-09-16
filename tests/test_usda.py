"""USDA candidate ranking. Pure scoring -- no network.

These are real descriptions the API returned for these queries. Taking its first
result gave fish oil for "salmon" and soy yogurt for "strawberry", which is why
the ranking exists.
"""

from __future__ import annotations

import pytest

from usda import score


def best(query: str, *descriptions: str) -> str:
    """The description this query would actually resolve to."""
    ranked = sorted(descriptions, key=lambda d: score(query, d), reverse=True)
    assert score(query, ranked[0]) >= 1, f"{query!r} rejected everything"
    return ranked[0]


@pytest.mark.parametrize(
    "query,description",
    [
        ("salmon", "Fish oil, salmon"),
        ("avocado", "Oil, avocado"),
        ("oats", "Oil, oat"),
    ],
)
def test_a_derived_form_is_never_the_food(query, description) -> None:
    assert score(query, description) == 0


def test_an_unrelated_head_is_rejected() -> None:
    assert score("strawberry", "Cheesecake, prepared from mix") == 0


def test_the_plain_food_beats_a_branded_product() -> None:
    assert best(
        "strawberry", "SILK Strawberry soy yogurt", "Strawberries, raw"
    ) == "Strawberries, raw"


def test_the_plain_food_beats_a_different_species() -> None:
    assert best("cucumber", "Sea cucumber, yane", "Cucumber, peeled, raw") \
        == "Cucumber, peeled, raw"


def test_the_raw_food_beats_a_prepared_dish() -> None:
    assert best("spinach", "Spinach souffle", "Spinach, raw") == "Spinach, raw"


def test_plurals_still_match() -> None:
    assert score("blueberries", "Blueberries, raw") >= 1
    assert score("strawberry", "Strawberries, raw") >= 1


def test_fewer_qualifiers_wins_between_equals() -> None:
    assert best("broccoli", "Broccoli, cooked, boiled, drained, with salt",
                "Broccoli, raw") == "Broccoli, raw"
