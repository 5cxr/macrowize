"""The confirm gate, driven through the real app with the model stubbed out.

These are the tests worth keeping about the UI: they assert the one design promise
that would be expensive to get wrong -- a parsed meal never reaches the database
until the user clicks Save.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import FoodItem
from db import Base, MealLog, SessionLocal, engine

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def fresh_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def run_app() -> AppTest:
    app = AppTest.from_file(APP_PATH, default_timeout=30)
    app.run()
    return app


def stub_agent(monkeypatch, route: str, *items: tuple[str, float, str]) -> None:
    """Replace both LLM calls. Patched on `agent`, not `app`: AppTest re-executes
    app.py on every run, which would rebind a name imported directly into it."""
    monkeypatch.setattr("agent.classify", lambda text: route)
    monkeypatch.setattr(
        "agent.parse_meal",
        lambda text: [FoodItem(name=n, quantity=q, unit=u) for n, q, u in items],
    )


def with_profile(app: AppTest) -> AppTest:
    """75kg moderate cut -> 2178 kcal, 135-180g protein."""
    app.number_input(key="height").set_value(178.0)
    app.number_input(key="weight").set_value(75.0)
    app.number_input(key="age").set_value(28)
    app.selectbox(key="sex").set_value("male")
    app.selectbox(key="activity").set_value("moderate")
    app.selectbox(key="goal").set_value("cut")
    return app.button[0].click().run()


def meal_count() -> int:
    with SessionLocal() as session:
        return session.query(MealLog).count()


def test_a_parsed_meal_is_not_saved_until_save_is_clicked(fresh_db, monkeypatch) -> None:
    stub_agent(monkeypatch, "log_meal", ("roti", 2, "piece"))

    app = with_profile(run_app())
    app.chat_input[0].set_value("2 rotis").run()

    assert not app.exception
    assert any("look right" in block.value for block in app.markdown)
    assert meal_count() == 0, "the confirmation is on screen; nothing is saved"

    next(b for b in app.button if b.label == "Save meal").click().run()

    with SessionLocal() as session:
        meal = session.query(MealLog).one()
        assert meal.items[0]["food_name"] == "roti"
        assert meal.total_kcal == pytest.approx(211.2, abs=0.1)


def test_discarding_saves_nothing(fresh_db, monkeypatch) -> None:
    stub_agent(monkeypatch, "log_meal", ("roti", 2, "piece"))

    app = with_profile(run_app())
    app.chat_input[0].set_value("2 rotis").run()
    next(b for b in app.button if b.label == "Discard").click().run()

    assert meal_count() == 0


def test_a_corrected_quantity_is_looked_up_again_not_multiplied(
    fresh_db, monkeypatch
) -> None:
    stub_agent(monkeypatch, "log_meal", ("roti", 2, "piece"))

    app = with_profile(run_app())
    app.chat_input[0].set_value("2 rotis").run()
    app.number_input(key="qty_0").set_value(4.0).run()
    next(b for b in app.button if b.label == "Save meal").click().run()

    with SessionLocal() as session:
        meal = session.query(MealLog).one()
        assert meal.items[0]["quantity"] == 4
        assert meal.total_kcal == pytest.approx(422.4, abs=0.1), "4 pieces = 160g"


def test_progress_is_answered_from_sql_without_the_model(fresh_db, monkeypatch) -> None:
    extractions: list[str] = []
    monkeypatch.setattr("agent.classify", lambda text: "query_progress")
    monkeypatch.setattr("agent.parse_meal", lambda text: extractions.append(text) or [])

    app = with_profile(run_app())
    app.chat_input[0].set_value("how am I doing?").run()

    assert not app.exception
    assert extractions == [], "the progress path must not extract anything"
    # Assert on the reply itself: the sidebar also renders the target, so a looser
    # check passed once while the reply was actually a database error.
    reply = next(r.value for r in app.markdown if "Today so far" in r.value)
    assert "**Calories:** 0 / 2178" in reply
    assert "**Protein:** 0.0 / 135–180 g" in reply
    assert "⚠️" not in reply


def test_a_failing_model_call_is_reported_not_raised(fresh_db, monkeypatch) -> None:
    """Free tiers rate-limit constantly; that must not blank the page."""
    def boom(text):
        raise RuntimeError("429 quota exceeded")

    monkeypatch.setattr("agent.classify", boom)

    app = with_profile(run_app())
    app.chat_input[0].set_value("2 rotis").run()

    assert not app.exception, "the error belongs in the chat, not a traceback page"
    assert any("model call failed" in block.value for block in app.markdown)
