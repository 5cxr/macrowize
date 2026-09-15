"""Onboarding subgraph: collect body stats, then compute and store targets.

Re-enterable -- it merges whatever the latest message contained into the profile
already on state, and asks only for what's still missing.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from calc.tdee import ActivityLevel, GoalType, Sex, compute_targets
from graph.router import PROFILE_REQUIRED_FIELDS, profile_is_complete
from graph.state import AppState
from llm import get_llm
from models.db import save_profile, session_scope
from models.schemas import ExtractedProfile

ONBOARDING_SYSTEM_PROMPT = """Extract body stats from the user's message for a macro \
tracking app.

Fields: height_cm, weight_kg, age, sex, activity_level, goal_type.

Rules:
- Only fill a field if the user actually stated it. Leave the rest null.
- Convert units: 5'10" -> 177.8 cm, 165 lbs -> 74.8 kg.
- activity_level is one of: sedentary, light, moderate, active, very_active.
  Map descriptions: "desk job, no exercise" -> sedentary; "gym 2x a week" -> light;
  "gym 4x a week" -> moderate; "gym 6x a week" -> active; "athlete" -> very_active.
- goal_type is one of: cut, maintain, bulk. "lose weight"/"fat loss" -> cut,
  "gain muscle"/"gain weight" -> bulk."""

FIELD_PROMPTS = {
    "height_cm": "your height",
    "weight_kg": "your weight",
    "age": "your age",
    "sex": "your sex (male/female — needed for the BMR formula)",
    "activity_level": "how active you are (sedentary / light / moderate / active / very active)",
}


def extract_profile_node(state: AppState) -> dict:
    """Merge any stats in this message into the profile held on state."""
    last_message = state["messages"][-1]
    text = last_message.content if hasattr(last_message, "content") else str(last_message)

    extracted = get_llm().with_structured_output(ExtractedProfile).invoke(
        [SystemMessage(content=ONBOARDING_SYSTEM_PROMPT), HumanMessage(content=text)]
    )

    profile = dict(state.get("user_profile") or {})
    for field, value in extracted.model_dump().items():
        if value is not None:
            profile[field] = value.value if hasattr(value, "value") else value

    return {"user_profile": profile}


def check_complete(state: AppState) -> str:
    """Conditional edge: compute targets, or go back and ask for what's missing."""
    return "compute" if profile_is_complete(state.get("user_profile")) else "ask"


def ask_missing_node(state: AppState) -> dict:
    """Ask for the first still-missing field."""
    profile = state.get("user_profile") or {}
    missing = [f for f in PROFILE_REQUIRED_FIELDS if profile.get(f) is None]

    if len(missing) == len(PROFILE_REQUIRED_FIELDS):
        reply = (
            "Hey — I track your meals and macros. First I need a few stats to work "
            "out your targets: height, weight, age, sex, and roughly how active you "
            "are. You can give them all in one go."
        )
    else:
        asks = [FIELD_PROMPTS[f] for f in missing]
        joined = asks[0] if len(asks) == 1 else ", ".join(asks[:-1]) + f" and {asks[-1]}"
        reply = f"Got it. I still need {joined}."

    return {"reply": reply, "messages": [AIMessage(content=reply)]}


def compute_targets_node(state: AppState) -> dict:
    """Run the deterministic TDEE math and persist the profile."""
    profile = dict(state["user_profile"])
    goal_type = GoalType(profile.get("goal_type") or "maintain")
    profile["goal_type"] = goal_type.value

    targets = compute_targets(
        weight_kg=float(profile["weight_kg"]),
        height_cm=float(profile["height_cm"]),
        age=int(profile["age"]),
        sex=Sex(profile["sex"]),
        activity_level=ActivityLevel(profile["activity_level"]),
        goal_type=goal_type,
    )

    profile.update(
        target_kcal=targets.target_kcal,
        target_protein_min_g=targets.target_protein_min_g,
        target_protein_max_g=targets.target_protein_max_g,
    )

    with session_scope() as session:
        save_profile(
            session,
            height_cm=float(profile["height_cm"]),
            weight_kg=float(profile["weight_kg"]),
            age=int(profile["age"]),
            sex=profile["sex"],
            activity_level=profile["activity_level"],
            goal_type=goal_type.value,
            target_kcal=targets.target_kcal,
            target_protein_min_g=targets.target_protein_min_g,
            target_protein_max_g=targets.target_protein_max_g,
        )

    lines = [
        f"All set. On a **{goal_type.value}**:",
        "",
        f"**Calories:** {targets.target_kcal}/day  (TDEE {targets.tdee:.0f})",
        f"**Protein:** {targets.target_protein_min_g}–{targets.target_protein_max_g} g/day",
    ]
    if targets.kcal_clamped_to_bmr:
        lines.append("")
        lines.append(
            "_Note: a full 500 kcal deficit would drop you below your resting "
            "burn, so I floored the target at your BMR._"
        )
    lines.append("")
    lines.append("Tell me what you eat and I'll track it — e.g. \"2 rotis and a bowl of dal\".")
    reply = "\n".join(lines)

    return {"user_profile": profile, "reply": reply, "messages": [AIMessage(content=reply)]}
