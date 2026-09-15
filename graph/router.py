"""Router node: decide which subgraph handles this turn."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from graph.state import AppState
from llm import get_llm

Route = Literal["onboarding", "log_meal", "query_progress", "other"]

PROFILE_REQUIRED_FIELDS = ("height_cm", "weight_kg", "age", "sex", "activity_level")

ROUTER_SYSTEM_PROMPT = """You classify a single user message in a macro-tracking app.

Reply with exactly one route:
- "onboarding": they're giving body stats (height, weight, age, sex, activity level) \
or changing their goal (cut/bulk/maintain).
- "log_meal": they're reporting food they ate. e.g. "2 rotis and dal", "had a bowl \
of curd", "chicken biryani for lunch".
- "query_progress": they're asking how they're doing today. e.g. "how much protein \
left?", "what's my total?", "am I under my calories?".
- "other": anything else -- greetings, questions about the app, off-topic.

Classify only. Do not answer the message."""


class RouteDecision(BaseModel):
    """Structured output for the router classification."""

    route: Route = Field(description="Which subgraph should handle this message")


def profile_is_complete(profile: dict | None) -> bool:
    """True when every field needed for the TDEE calculation is filled in."""
    if not profile:
        return False
    return all(profile.get(field) is not None for field in PROFILE_REQUIRED_FIELDS)


def route_node(state: AppState) -> dict:
    """Classify the latest message, forcing onboarding while the profile is bare."""
    last_message = state["messages"][-1]
    text = last_message.content if hasattr(last_message, "content") else str(last_message)

    if not profile_is_complete(state.get("user_profile")):
        # Nothing works without targets, so collect stats first -- unless they're
        # clearly just saying hello, in which case onboarding greets them anyway.
        return {"route": "onboarding"}

    decision = get_llm().with_structured_output(RouteDecision).invoke(
        [SystemMessage(content=ROUTER_SYSTEM_PROMPT), HumanMessage(content=text)]
    )
    return {"route": decision.route}


def pick_branch(state: AppState) -> str:
    """Conditional-edge function: map the stored route onto a node name."""
    return state.get("route", "other")
