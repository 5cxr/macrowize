"""End-to-end graph tests with a stubbed LLM.

The model is replaced by a stub that returns fixed structured output, so these
tests exercise the real routing, lookup, interrupt and save logic without a key.
The point of most of them is the confirm gate: a meal must never reach the DB
without an explicit resume.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from graph import build_graph
from models.db import Base, MealLog, engine, get_daily_tally, save_profile
from models.schemas import ExtractedMeal, ExtractedProfile, FoodItem

CONFIG = {"configurable": {"thread_id": "test"}}

COMPLETE_PROFILE = {
    "height_cm": 178.0, "weight_kg": 75.0, "age": 28, "sex": "male",
    "activity_level": "moderate", "goal_type": "cut",
    "target_kcal": 2178, "target_protein_min_g": 135, "target_protein_max_g": 180,
}


class StubStructuredModel:
    """Stands in for `llm.with_structured_output(Schema)`."""

    def __init__(self, result):
        self._result = result

    def invoke(self, _messages):
        return self._result


class StubLLM:
    """Stands in for the chat model, handing back canned structured output."""

    def __init__(self, by_schema: dict):
        self._by_schema = by_schema
        self.calls: list[str] = []

    def with_structured_output(self, schema):
        self.calls.append(schema.__name__)
        return StubStructuredModel(self._by_schema[schema.__name__])


@pytest.fixture
def fresh_db():
    """Empty tables for the duration of one test."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def stub_llm(monkeypatch):
    """Install a stub LLM across every module that builds one."""

    def install(**by_schema):
        stub = StubLLM(by_schema)
        for module in ("graph.router", "graph.log_meal", "graph.onboarding"):
            monkeypatch.setattr(f"{module}.get_llm", lambda: stub)
        return stub

    return install


def make_graph():
    return build_graph(MemorySaver())


def meal_extraction(*items: tuple[str, float, str]) -> ExtractedMeal:
    return ExtractedMeal(
        items=[FoodItem(name=n, quantity=q, unit=u) for n, q, u in items]
    )


def route_to(route: str):
    from graph.router import RouteDecision

    return RouteDecision(route=route)


def test_log_meal_interrupts_before_saving(fresh_db, stub_llm) -> None:
    stub_llm(
        RouteDecision=route_to("log_meal"),
        ExtractedMeal=meal_extraction(("roti", 2, "piece"), ("dal", 1, "bowl")),
    )
    graph = make_graph()

    result = graph.invoke(
        {"messages": [("user", "2 rotis and a bowl of dal")],
         "user_profile": COMPLETE_PROFILE},
        CONFIG,
    )

    assert "__interrupt__" in result, "graph should pause at the confirm node"
    payload = result["__interrupt__"][0].value
    assert payload["kind"] == "confirm_meal"
    assert len(payload["pending_meal"]["items"]) == 2

    from models.db import SessionLocal

    with SessionLocal() as session:
        assert session.query(MealLog).count() == 0, "nothing may be saved before confirm"


def test_macros_come_from_the_csv_not_the_model(fresh_db, stub_llm) -> None:
    stub_llm(
        RouteDecision=route_to("log_meal"),
        ExtractedMeal=meal_extraction(("roti", 2, "piece")),
    )
    graph = make_graph()

    result = graph.invoke(
        {"messages": [("user", "2 rotis")], "user_profile": COMPLETE_PROFILE}, CONFIG
    )
    item = result["__interrupt__"][0].value["pending_meal"]["items"][0]

    # roti: 264 kcal/100g, 40g per piece, 2 pieces -> 80g -> 211.2 kcal
    assert item["grams"] == 80.0
    assert item["kcal"] == pytest.approx(211.2, abs=0.1)
    assert item["source"] == "indian_csv"


def test_confirming_saves_the_meal(fresh_db, stub_llm) -> None:
    stub_llm(
        RouteDecision=route_to("log_meal"),
        ExtractedMeal=meal_extraction(("roti", 2, "piece"), ("dal", 1, "bowl")),
    )
    graph = make_graph()
    graph.invoke(
        {"messages": [("user", "2 rotis and dal")], "user_profile": COMPLETE_PROFILE},
        CONFIG,
    )

    result = graph.invoke(Command(resume={"action": "confirm"}), CONFIG)

    assert "__interrupt__" not in result
    from models.db import SessionLocal

    with SessionLocal() as session:
        assert session.query(MealLog).count() == 1
        kcal, protein = get_daily_tally(session)
        # roti 80g -> 211.2 kcal, dal tadka 150g -> 180 kcal
        assert kcal == pytest.approx(391.2, abs=0.5)
        assert protein == pytest.approx(16.7, abs=0.5)


def test_discarding_saves_nothing(fresh_db, stub_llm) -> None:
    stub_llm(
        RouteDecision=route_to("log_meal"),
        ExtractedMeal=meal_extraction(("roti", 2, "piece")),
    )
    graph = make_graph()
    graph.invoke(
        {"messages": [("user", "2 rotis")], "user_profile": COMPLETE_PROFILE}, CONFIG
    )

    result = graph.invoke(Command(resume={"action": "cancel"}), CONFIG)

    from models.db import SessionLocal

    with SessionLocal() as session:
        assert session.query(MealLog).count() == 0
    assert "nothing was saved" in result["reply"]


def test_edited_quantities_are_what_get_saved(fresh_db, stub_llm) -> None:
    stub_llm(
        RouteDecision=route_to("log_meal"),
        ExtractedMeal=meal_extraction(("roti", 2, "piece")),
    )
    graph = make_graph()
    result = graph.invoke(
        {"messages": [("user", "2 rotis")], "user_profile": COMPLETE_PROFILE}, CONFIG
    )
    item = result["__interrupt__"][0].value["pending_meal"]["items"][0]

    corrected = {**item, "quantity": 4, "kcal": 422.4, "protein_g": 15.4,
                 "carbs_g": 83.2, "fat_g": 4.0}
    graph.invoke(Command(resume={"action": "confirm", "items": [corrected]}), CONFIG)

    from models.db import SessionLocal

    with SessionLocal() as session:
        meal = session.query(MealLog).one()
        assert meal.items[0]["quantity"] == 4
        assert meal.total_kcal == pytest.approx(422.4, abs=0.1)


def test_unknown_food_is_reported_not_invented(fresh_db, stub_llm, monkeypatch) -> None:
    monkeypatch.setattr("nutrition.lookup.UsdaClient.search", lambda self, q: None)
    stub_llm(
        RouteDecision=route_to("log_meal"),
        ExtractedMeal=meal_extraction(("zzzznotafood", 1, "bowl")),
    )
    graph = make_graph()

    result = graph.invoke(
        {"messages": [("user", "had some zzzznotafood")],
         "user_profile": COMPLETE_PROFILE},
        CONFIG,
    )
    pending = result["__interrupt__"][0].value["pending_meal"]

    assert pending["items"] == []
    assert pending["unresolved"] == ["zzzznotafood"]
    assert pending["total_kcal"] == 0


def test_query_progress_uses_no_llm_call(fresh_db, stub_llm) -> None:
    stub = stub_llm(RouteDecision=route_to("query_progress"))
    from models.db import SessionLocal

    with SessionLocal() as session:
        save_profile(session, **COMPLETE_PROFILE)
        session.commit()

    graph = make_graph()
    result = graph.invoke(
        {"messages": [("user", "how am I doing?")], "user_profile": COMPLETE_PROFILE},
        CONFIG,
    )

    # Only the router consulted the model; the progress node did not.
    assert stub.calls == ["RouteDecision"]
    assert "2178" in result["reply"]


def test_incomplete_profile_routes_to_onboarding_without_calling_the_model(
    fresh_db, stub_llm
) -> None:
    stub = stub_llm(ExtractedProfile=ExtractedProfile(weight_kg=75.0))
    graph = make_graph()

    result = graph.invoke({"messages": [("user", "hi")], "user_profile": None}, CONFIG)

    assert "RouteDecision" not in stub.calls, "router must not ask the model"
    assert result["route"] == "onboarding"
    assert "still need" in result["reply"] or "stats" in result["reply"]


def test_onboarding_computes_and_stores_targets(fresh_db, stub_llm) -> None:
    stub_llm(
        ExtractedProfile=ExtractedProfile(
            height_cm=178, weight_kg=75, age=28, sex="male",
            activity_level="moderate", goal_type="cut",
        )
    )
    graph = make_graph()

    result = graph.invoke(
        {"messages": [("user", "28m, 178cm, 75kg, gym 4x a week, want to cut")],
         "user_profile": None},
        CONFIG,
    )

    assert result["user_profile"]["target_kcal"] == 2178
    from models.db import SessionLocal, get_profile

    with SessionLocal() as session:
        stored = get_profile(session)
        assert stored is not None
        assert stored.target_kcal == 2178
        assert (stored.target_protein_min_g, stored.target_protein_max_g) == (135, 180)
