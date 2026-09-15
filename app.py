"""Streamlit entrypoint: sidebar dashboard on the left, chat on the right."""

from __future__ import annotations

from typing import Any

import streamlit as st
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from calc.tdee import ActivityLevel, GoalType, Sex, compute_targets
from graph import build_graph
from llm import LLMUnavailableError
from models.db import (
    get_daily_tally,
    get_meals_for_day,
    get_profile,
    init_db,
    save_profile,
    session_scope,
)
from nutrition.lookup import resolve_item

st.set_page_config(page_title="macrowize", page_icon="🍽", layout="wide")

ACTIVITY_LABELS = {
    ActivityLevel.SEDENTARY: "Sedentary — desk job, little exercise",
    ActivityLevel.LIGHT: "Light — 1-3 sessions/week",
    ActivityLevel.MODERATE: "Moderate — 3-5 sessions/week",
    ActivityLevel.ACTIVE: "Active — 6-7 sessions/week",
    ActivityLevel.VERY_ACTIVE: "Very active — physical job or twice daily",
}

THREAD_CONFIG = {"configurable": {"thread_id": "macrowize-single-user"}}


def load_profile_dict() -> dict[str, Any] | None:
    """Read the stored profile into a plain dict for the graph state."""
    with session_scope() as session:
        profile = get_profile(session)
        if profile is None:
            return None
        return {
            "height_cm": profile.height_cm,
            "weight_kg": profile.weight_kg,
            "age": profile.age,
            "sex": profile.sex,
            "activity_level": profile.activity_level,
            "goal_type": profile.goal_type,
            "target_kcal": profile.target_kcal,
            "target_protein_min_g": profile.target_protein_min_g,
            "target_protein_max_g": profile.target_protein_max_g,
        }


def init_session() -> None:
    """Set up the graph, checkpointer, and chat history once per browser session."""
    if "graph" not in st.session_state:
        init_db()
        st.session_state.checkpointer = MemorySaver()
        st.session_state.graph = build_graph(st.session_state.checkpointer)
        st.session_state.history = []
        st.session_state.pending = None
        st.session_state.profile = load_profile_dict()


def render_profile_form() -> None:
    """Sidebar profile editor. Collapsed to a summary once it's filled in."""
    profile = st.session_state.profile
    is_new = profile is None

    if not is_new:
        st.sidebar.caption(
            f"{profile['height_cm']:.0f} cm · {profile['weight_kg']:.0f} kg · "
            f"{profile['age']}y · {profile['sex']} · {profile['activity_level']}"
        )

    with st.sidebar.expander("Profile", expanded=is_new):
        with st.form("profile_form"):
            height = st.number_input(
                "Height (cm)", 100.0, 250.0, key="height",
                value=float(profile["height_cm"]) if profile else 170.0, step=1.0,
            )
            weight = st.number_input(
                "Weight (kg)", 30.0, 250.0, key="weight",
                value=float(profile["weight_kg"]) if profile else 70.0, step=0.5,
            )
            age = st.number_input(
                "Age", 10, 100, key="age",
                value=int(profile["age"]) if profile else 30, step=1,
            )
            sex = st.selectbox(
                "Sex", list(Sex), key="sex",
                index=list(Sex).index(Sex(profile["sex"])) if profile else 0,
                format_func=lambda s: s.value,
            )
            activity = st.selectbox(
                "Activity level", list(ActivityLevel), key="activity",
                index=list(ActivityLevel).index(ActivityLevel(profile["activity_level"]))
                if profile else 2,
                format_func=lambda a: ACTIVITY_LABELS[a],
            )
            goal = st.selectbox(
                "Goal", list(GoalType), key="goal",
                index=list(GoalType).index(GoalType(profile["goal_type"]))
                if profile and profile.get("goal_type") else 0,
                format_func=lambda g: g.value,
            )

            if st.form_submit_button("Save profile", use_container_width=True):
                targets = compute_targets(
                    weight_kg=weight, height_cm=height, age=int(age),
                    sex=sex, activity_level=activity, goal_type=goal,
                )
                with session_scope() as session:
                    save_profile(
                        session,
                        height_cm=height, weight_kg=weight, age=int(age),
                        sex=sex.value, activity_level=activity.value,
                        goal_type=goal.value,
                        target_kcal=targets.target_kcal,
                        target_protein_min_g=targets.target_protein_min_g,
                        target_protein_max_g=targets.target_protein_max_g,
                    )
                st.session_state.profile = load_profile_dict()
                if targets.kcal_clamped_to_bmr:
                    st.warning("Deficit floored at your BMR — a full 500 would go under it.")
                st.rerun()


def render_dashboard() -> None:
    """Today's totals against target, as two progress bars."""
    profile = st.session_state.profile
    with session_scope() as session:
        kcal, protein = get_daily_tally(session)
        # Read the fields out while the session is open -- these rows are detached
        # the moment it closes, and touching an attribute afterwards raises.
        meals = [
            {
                "time": meal.timestamp.strftime("%H:%M"),
                "raw_text": meal.raw_text,
                "total_kcal": meal.total_kcal,
            }
            for meal in get_meals_for_day(session)
        ]

    st.sidebar.subheader("Today")

    if profile is None:
        st.sidebar.info("Fill in your profile to see targets.")
        return

    target_kcal = profile["target_kcal"]
    protein_min = profile["target_protein_min_g"]
    protein_max = profile["target_protein_max_g"]

    st.sidebar.progress(min(kcal / target_kcal, 1.0))
    st.sidebar.markdown(
        f"**{kcal:.0f}** / {target_kcal} kcal &nbsp;·&nbsp; "
        f"`{target_kcal - kcal:+.0f}` left"
    )

    st.sidebar.progress(min(protein / protein_min, 1.0) if protein_min else 0.0)
    band_note = (
        "in band" if protein_min <= protein <= protein_max
        else f"{protein_min - protein:+.0f}g to band" if protein < protein_min
        else f"{protein - protein_max:.0f}g over"
    )
    st.sidebar.markdown(
        f"**{protein:.0f}** / {protein_min}–{protein_max} g protein &nbsp;·&nbsp; {band_note}"
    )

    if meals:
        with st.sidebar.expander(f"{len(meals)} meal(s) logged"):
            for meal in meals:
                st.caption(
                    f"{meal['time']} — {meal['raw_text']} "
                    f"({meal['total_kcal']:.0f} kcal)"
                )


def render_confirmation(pending: dict) -> None:
    """The confirm-before-save gate. Nothing is written until Save is clicked."""
    st.info("Check this before I log it — quantities are the bit I get wrong.")

    edited: list[dict] = []
    for index, item in enumerate(pending["items"]):
        cols = st.columns([3, 1.4, 1.2, 2.4, 1])
        cols[0].markdown(f"**{item['food_name']}**")
        quantity = cols[1].number_input(
            "qty", min_value=0.0, value=float(item["quantity"]), step=0.5,
            key=f"qty_{index}", label_visibility="collapsed",
        )
        cols[2].markdown(f"`{item['unit']}`")
        cols[3].caption(
            f"~{item['grams']:g}g · {item['kcal']:.0f} kcal · "
            f"{item['protein_g']:.1f}g P · _{item['source']}_"
        )
        keep = cols[4].checkbox("keep", value=True, key=f"keep_{index}",
                                label_visibility="collapsed")
        if keep and quantity > 0:
            edited.append({**item, "quantity": quantity})

    if pending.get("unresolved"):
        st.warning(
            f"No nutrition data for: {', '.join(pending['unresolved'])}. "
            "These won't be counted — try another name for them."
        )

    left, right, _ = st.columns([1, 1, 4])
    if left.button("Save meal", type="primary", disabled=not edited):
        resolved = rescale_items(edited)
        result = st.session_state.graph.invoke(
            Command(resume={"action": "confirm", "items": resolved}), THREAD_CONFIG
        )
        finish_turn(result)
    if right.button("Discard"):
        result = st.session_state.graph.invoke(
            Command(resume={"action": "cancel"}), THREAD_CONFIG
        )
        finish_turn(result)


def rescale_items(edited: list[dict]) -> list[dict]:
    """Re-run the nutrition lookup for any quantity the user changed.

    Deliberately a fresh deterministic lookup rather than scaling the numbers in
    the browser -- macros only ever come from the data layer.
    """
    rescaled: list[dict] = []
    with session_scope() as session:
        for item in edited:
            match = resolve_item(session, item["food_name"], item["quantity"], item["unit"])
            if match is None:
                rescaled.append(item)
                continue
            rescaled.append({
                **match.to_meal_item(),
                "grams": round(match.grams, 1),
                "source": match.source,
            })
    return rescaled


def finish_turn(result: dict) -> None:
    """Store the graph's reply (or its next interrupt) and rerun the page."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        st.session_state.pending = interrupts[0].value["pending_meal"]
        st.session_state.history.append(
            ("assistant", interrupts[0].value["summary"])
        )
    else:
        st.session_state.pending = None
        reply = result.get("reply")
        if reply:
            st.session_state.history.append(("assistant", reply))
        st.session_state.profile = load_profile_dict()
    st.rerun()


def handle_input(text: str) -> None:
    """Send one user message through the graph."""
    st.session_state.history.append(("user", text))
    try:
        result = st.session_state.graph.invoke(
            {
                "messages": [HumanMessage(content=text)],
                "user_profile": st.session_state.profile,
                "pending_meal": None,
            },
            THREAD_CONFIG,
        )
    except LLMUnavailableError as exc:
        st.session_state.history.append(("assistant", f"⚠️ {exc}"))
        st.rerun()
        return
    finish_turn(result)


def main() -> None:
    init_session()

    st.sidebar.title("macrowize")
    render_profile_form()
    render_dashboard()

    st.title("What did you eat?")

    for role, content in st.session_state.history:
        with st.chat_message(role):
            st.markdown(content)

    if st.session_state.pending:
        with st.chat_message("assistant"):
            render_confirmation(st.session_state.pending)

    if prompt := st.chat_input("e.g. 2 rotis and a bowl of dal"):
        handle_input(prompt)


if __name__ == "__main__":
    main()
