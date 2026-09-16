"""Streamlit entrypoint: sidebar dashboard on the left, chat on the right."""

from __future__ import annotations

from typing import Any

import streamlit as st

import agent
import nutrition
from agent import LLMUnavailableError
from db import (
    clear_chat_history,
    delete_meal,
    get_chat_history,
    get_daily_history,
    get_daily_tally,
    get_meals_for_day,
    get_profile,
    init_db,
    save_chat_message,
    save_meal,
    save_profile,
    session_scope,
)
from tdee import ActivityLevel, GoalType, Sex, compute_targets

st.set_page_config(page_title="macrowize", page_icon="🍽", layout="wide")

ACTIVITY_LABELS = {
    ActivityLevel.SEDENTARY: "Sedentary — desk job, little exercise",
    ActivityLevel.LIGHT: "Light — 1-3 sessions/week",
    ActivityLevel.MODERATE: "Moderate — 3-5 sessions/week",
    ActivityLevel.ACTIVE: "Active — 6-7 sessions/week",
    ActivityLevel.VERY_ACTIVE: "Very active — physical job or twice daily",
}

FALLBACK_REPLY = (
    "I track meals and macros. Tell me what you ate (\"2 rotis and a bowl of dal\"), "
    "ask how today's going, or update your stats in the sidebar."
)


def load_profile() -> dict[str, Any] | None:
    """Read the stored profile, or None before onboarding."""
    with session_scope() as session:
        return get_profile(session)


def init_session() -> None:
    """Create tables and load the profile and transcript, once per browser session."""
    if "started" not in st.session_state:
        init_db()
        st.session_state.started = True
        st.session_state.pending = None
        st.session_state.profile = load_profile()
        with session_scope() as session:
            st.session_state.history = get_chat_history(session)


def remember(role: str, content: str) -> None:
    """Show a message and persist it, so a reload doesn't lose the transcript."""
    st.session_state.history.append((role, content))
    with session_scope() as session:
        save_chat_message(session, role, content)


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
                st.session_state.profile = load_profile()
                if targets.kcal_clamped_to_bmr:
                    st.warning("Deficit floored at your BMR — a full 500 would go under it.")
                st.rerun()


def render_dashboard() -> None:
    """Today's totals against target, as two progress bars."""
    profile = st.session_state.profile
    with session_scope() as session:
        kcal, protein = get_daily_tally(session)
        meals = get_meals_for_day(session)

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
        # Read-only here. Deleting lives in the History view, where each meal gets
        # a full-width row instead of a cramped icon button.
        with st.sidebar.expander(f"{len(meals)} meal(s) logged"):
            for meal in meals:
                st.caption(
                    f"{meal['time']} — {meal['raw_text']} "
                    f"({meal['total_kcal']:.0f} kcal)"
                )

    if st.session_state.history:
        if st.sidebar.button("Clear chat", help="Clears the transcript only — logged meals stay"):
            with session_scope() as session:
                clear_chat_history(session)
            st.session_state.history = []
            st.session_state.pending = None
            st.rerun()


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
            f"~{item['grams']:g}g · {item['kcal']:.0f} kcal · {item['protein_g']:.1f}g P"
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
        commit_meal(pending["raw_text"], edited)
    if right.button("Discard"):
        st.session_state.pending = None
        remember("assistant", "Dropped it — nothing was saved.")
        st.rerun()


def commit_meal(raw_text: str, edited: list[dict]) -> None:
    """Write the approved meal, re-looking-up anything whose quantity changed.

    The re-lookup is deliberate: corrected macros come from the table, never from
    arithmetic done in the browser.
    """
    items = [
        nutrition.resolve(
            item.get("query", item["food_name"]), item["quantity"], item["unit"]
        )
        or item
        for item in edited
    ]
    stored = [{k: v for k, v in item.items() if k != "query"} for item in items]
    with session_scope() as session:
        save_meal(session, raw_text, stored)
        session.flush()
        kcal, protein = get_daily_tally(session)
        profile = get_profile(session)

    total_kcal = sum(item["kcal"] for item in stored)
    total_protein = sum(item["protein_g"] for item in stored)
    lines = [f"Logged — {total_kcal:.0f} kcal, {total_protein:.1f}g protein."]
    if profile:
        target = profile["target_kcal"]
        lines += [
            "",
            f"**Today:** {kcal:.0f} / {target} kcal ({target - kcal:+.0f})",
            f"**Protein:** {protein:.1f} / "
            f"{profile['target_protein_min_g']}–{profile['target_protein_max_g']} g",
        ]

    st.session_state.pending = None
    remember("assistant", "\n".join(lines))
    st.rerun()


def describe_progress() -> str:
    """Today's tally against target. Pure SQL -- no model call on this path."""
    with session_scope() as session:
        kcal, protein = get_daily_tally(session)
        count = len(get_meals_for_day(session))
        profile = get_profile(session)

    if profile is None:
        return (
            f"{count} meal(s) today: {kcal:.0f} kcal, {protein:.1f}g protein. "
            "Fill in your profile for targets."
        )

    target_kcal = profile["target_kcal"]
    low, high = profile["target_protein_min_g"], profile["target_protein_max_g"]
    if protein < low:
        note = f"{low - protein:.1f}g to reach the band"
    elif protein <= high:
        note = "in the band"
    else:
        note = f"{protein - high:.1f}g over"

    return "\n".join([
        f"**Today so far** ({count} meal{'s' if count != 1 else ''})",
        "",
        f"**Calories:** {kcal:.0f} / {target_kcal}  ({target_kcal - kcal:+.0f} left)",
        f"**Protein:** {protein:.1f} / {low}–{high} g  ({note})",
    ])


def summarize(pending: dict) -> str:
    """The breakdown the user is asked to approve."""
    if not pending["items"]:
        names = ", ".join(pending["unresolved"]) or "anything"
        return f"I couldn't find nutrition data for {names}. Try naming it differently?"

    lines = ["Here's what I got — look right?", ""]
    for item in pending["items"]:
        lines.append(
            f"• **{item['food_name']}** ×{item['quantity']:g} {item['unit']}"
            f" (~{item['grams']:g}g) — {item['kcal']:.0f} kcal,"
            f" {item['protein_g']:.1f}g protein"
        )
    total_kcal = sum(item["kcal"] for item in pending["items"])
    total_protein = sum(item["protein_g"] for item in pending["items"])
    lines += ["", f"**Total: {total_kcal:.0f} kcal, {total_protein:.1f}g protein**"]
    if pending["unresolved"]:
        lines += ["", f"_Not found, so not counted: {', '.join(pending['unresolved'])}_"]
    return "\n".join(lines)


def handle_input(text: str) -> None:
    """One user message: classify it, then do the matching thing."""
    remember("user", text)
    try:
        route = agent.classify(text)
        if route == "log_meal":
            resolved, unresolved = nutrition.resolve_all(agent.parse_meal(text))
            pending = {"raw_text": text, "items": resolved, "unresolved": unresolved}
            # Shown but not persisted: the pending meal lives only in session
            # state, so a summary that survived a reload would have no working
            # Save button behind it.
            st.session_state.pending = pending
            st.session_state.history.append(("assistant", summarize(pending)))
        elif route == "query_progress":
            remember("assistant", describe_progress())
        else:
            remember("assistant", FALLBACK_REPLY)
    except LLMUnavailableError as exc:
        remember("assistant", f"⚠️ {exc}")
    except Exception as exc:
        # Rate limits, timeouts and provider outages are normal on a free tier.
        # Surface them in the chat instead of replacing the page with a traceback.
        remember("assistant", f"⚠️ The model call failed — {type(exc).__name__}: {exc}")
    st.rerun()


def render_meal_row(meal: dict, key_prefix: str) -> None:
    """One logged meal with a two-step delete. Deleting is not undoable."""
    pending_key = f"confirm_delete_{meal['id']}"
    label = (
        f"{meal['time']} — {meal['raw_text']} "
        f"({meal['total_kcal']:.0f} kcal, {meal['total_protein_g']:.0f}g protein)"
    )

    if st.session_state.get(pending_key):
        st.warning(f"Delete this permanently?  \n{label}")
        confirm, cancel, _ = st.columns([1, 1, 3])
        if confirm.button("Delete", key=f"{key_prefix}_yes_{meal['id']}", type="primary"):
            with session_scope() as session:
                delete_meal(session, meal["id"])
            st.session_state.pop(pending_key, None)
            st.rerun()
        if cancel.button("Keep", key=f"{key_prefix}_no_{meal['id']}"):
            st.session_state.pop(pending_key, None)
            st.rerun()
        return

    text, button = st.columns([4, 1])
    text.markdown(label)
    if button.button("Delete", key=f"{key_prefix}_del_{meal['id']}"):
        st.session_state[pending_key] = True
        st.rerun()


def render_history() -> None:
    """Multi-day view: per-day totals against target, expandable to the meals."""
    profile = st.session_state.profile
    st.title("History")

    days = st.selectbox("Range", [7, 14, 30], format_func=lambda d: f"Last {d} days")

    with session_scope() as session:
        history = get_daily_history(session, days=days)
        meals_by_day = {
            entry["date"]: get_meals_for_day(session, entry["date"])
            for entry in history
            if entry["meals"]
        }

    logged = [entry for entry in history if entry["meals"]]
    if not logged:
        st.info("Nothing logged in this range yet.")
        return

    target_kcal = profile["target_kcal"] if profile else None
    average = sum(entry["kcal"] for entry in logged) / len(logged)
    left, right = st.columns(2)
    left.metric("Days logged", f"{len(logged)} of {days}")
    right.metric(
        "Average kcal on those days",
        f"{average:.0f}",
        delta=f"{average - target_kcal:+.0f} vs target" if target_kcal else None,
        delta_color="off",
    )

    for entry in history:
        day_label = entry["date"].strftime("%a %d %b")
        if not entry["meals"]:
            st.caption(f"**{day_label}** — nothing logged")
            continue

        headline = f"**{day_label}** — {entry['kcal']:.0f} kcal, {entry['protein_g']:.0f}g protein"
        if target_kcal:
            headline += f"  ({entry['kcal'] - target_kcal:+.0f})"
        st.markdown(headline)
        if target_kcal:
            st.progress(min(entry["kcal"] / target_kcal, 1.0))

        with st.expander(f"{entry['meals']} meal(s)"):
            for meal in meals_by_day[entry["date"]]:
                render_meal_row(meal, key_prefix="hist")
        st.divider()


def render_chat() -> None:
    """Chat transcript, the pending confirmation, and the input box."""
    st.title("What did you eat?")

    for role, content in st.session_state.history:
        with st.chat_message(role):
            st.markdown(content)

    if st.session_state.pending:
        with st.chat_message("assistant"):
            render_confirmation(st.session_state.pending)

    if prompt := st.chat_input("e.g. 2 rotis and a bowl of dal"):
        handle_input(prompt)


def main() -> None:
    init_session()

    st.sidebar.title("macrowize")
    view = st.sidebar.radio("View", ["Chat", "History"], horizontal=True, key="view")
    render_profile_form()
    render_dashboard()

    if view == "History":
        render_history()
    else:
        render_chat()


if __name__ == "__main__":
    main()
