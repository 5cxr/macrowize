"""Streamlit UI tests via AppTest -- runs app.py for real, without a browser."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from config import PROJECT_ROOT
from models.db import Base, MealLog, SessionLocal, engine, get_profile, save_meal

APP_PATH = str(PROJECT_ROOT / "app.py")


@pytest.fixture
def fresh_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def run_app() -> AppTest:
    app = AppTest.from_file(APP_PATH, default_timeout=30)
    app.run()
    return app


def test_app_starts_without_a_profile(fresh_db) -> None:
    app = run_app()
    assert not app.exception
    assert "Fill in your profile" in app.sidebar.info[0].value


def test_profile_form_computes_and_persists_targets(fresh_db) -> None:
    app = run_app()

    app.number_input(key="height").set_value(178.0)
    app.number_input(key="weight").set_value(75.0)
    app.number_input(key="age").set_value(28)
    app.selectbox(key="sex").set_value("male")
    app.selectbox(key="activity").set_value("moderate")
    app.selectbox(key="goal").set_value("cut")
    app.button[0].click().run()

    assert not app.exception
    with SessionLocal() as session:
        profile = get_profile(session)
        assert profile is not None
        assert profile.target_kcal == 2178
        assert (profile.target_protein_min_g, profile.target_protein_max_g) == (135, 180)


def test_dashboard_shows_progress_against_targets(fresh_db) -> None:
    app = run_app()
    app.number_input(key="height").set_value(178.0)
    app.number_input(key="weight").set_value(75.0)
    app.number_input(key="age").set_value(28)
    app.selectbox(key="sex").set_value("male")
    app.selectbox(key="activity").set_value("moderate")
    app.selectbox(key="goal").set_value("cut")
    app.button[0].click().run()

    markdown = " ".join(block.value for block in app.sidebar.markdown)
    assert "2178 kcal" in markdown
    assert "135–180 g protein" in markdown
    assert "`+2178` left" in markdown, "nothing logged yet, so the full budget is left"


def test_logged_meals_move_the_tally(fresh_db) -> None:
    with SessionLocal() as session:
        save_meal(
            session,
            raw_text="2 rotis",
            items=[{
                "food_name": "roti", "quantity": 2, "unit": "piece",
                "kcal": 211.2, "protein_g": 7.7, "carbs_g": 41.6, "fat_g": 2.0,
            }],
        )
        session.commit()

    app = run_app()
    app.number_input(key="height").set_value(178.0)
    app.number_input(key="weight").set_value(75.0)
    app.number_input(key="age").set_value(28)
    app.selectbox(key="sex").set_value("male")
    app.selectbox(key="activity").set_value("moderate")
    app.selectbox(key="goal").set_value("cut")
    app.button[0].click().run()

    # The meal list renders inside an expander; regression guard for reading
    # detached ORM rows after their session closed.
    assert not app.exception
    markdown = " ".join(block.value for block in app.sidebar.markdown)
    assert "**211**" in markdown
    assert "`+1967`" in markdown
    assert any("2 rotis" in block.value for block in app.sidebar.caption)


def test_chat_without_an_api_key_reports_it_instead_of_crashing(fresh_db, monkeypatch) -> None:
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    app = run_app()
    app.number_input(key="height").set_value(178.0)
    app.number_input(key="weight").set_value(75.0)
    app.number_input(key="age").set_value(28)
    app.selectbox(key="sex").set_value("male")
    app.selectbox(key="activity").set_value("moderate")
    app.selectbox(key="goal").set_value("cut")
    app.button[0].click().run()

    app.chat_input[0].set_value("2 rotis and a bowl of dal").run()

    assert not app.exception, "a missing key must not crash the app"
    replies = [block.value for block in app.markdown]
    assert any("GOOGLE_API_KEY" in reply for reply in replies)


def _fill_profile(app: AppTest) -> AppTest:
    app.number_input(key="height").set_value(178.0)
    app.number_input(key="weight").set_value(75.0)
    app.number_input(key="age").set_value(28)
    app.selectbox(key="sex").set_value("male")
    app.selectbox(key="activity").set_value("moderate")
    app.selectbox(key="goal").set_value("cut")
    return app.button[0].click().run()


def test_meal_reaches_the_db_only_after_the_save_button(fresh_db, monkeypatch) -> None:
    """The whole point of the confirm gate, driven through the real UI."""
    from tests.test_graph import StubLLM, meal_extraction, route_to

    stub = StubLLM({
        "RouteDecision": route_to("log_meal"),
        "ExtractedMeal": meal_extraction(("roti", 2, "piece")),
    })
    for module in ("graph.router", "graph.log_meal", "graph.onboarding"):
        monkeypatch.setattr(f"{module}.get_llm", lambda: stub)

    app = _fill_profile(run_app())
    app.chat_input[0].set_value("2 rotis").run()

    # Confirmation is on screen; the DB is still untouched.
    assert not app.exception
    assert any("look right" in block.value for block in app.markdown)
    with SessionLocal() as session:
        assert session.query(MealLog).count() == 0

    save_button = next(b for b in app.button if b.label == "Save meal")
    save_button.click().run()

    with SessionLocal() as session:
        meal = session.query(MealLog).one()
        assert meal.items[0]["food_name"] == "roti"
        assert meal.total_kcal == pytest.approx(211.2, abs=0.1)


def test_discard_button_leaves_the_db_empty(fresh_db, monkeypatch) -> None:
    from tests.test_graph import StubLLM, meal_extraction, route_to

    stub = StubLLM({
        "RouteDecision": route_to("log_meal"),
        "ExtractedMeal": meal_extraction(("roti", 2, "piece")),
    })
    for module in ("graph.router", "graph.log_meal", "graph.onboarding"):
        monkeypatch.setattr(f"{module}.get_llm", lambda: stub)

    app = _fill_profile(run_app())
    app.chat_input[0].set_value("2 rotis").run()
    next(b for b in app.button if b.label == "Discard").click().run()

    with SessionLocal() as session:
        assert session.query(MealLog).count() == 0


def test_nothing_is_written_to_the_db_by_merely_chatting(fresh_db, monkeypatch) -> None:
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    app = run_app()
    app.chat_input[0].set_value("2 rotis").run()

    with SessionLocal() as session:
        assert session.query(MealLog).count() == 0
