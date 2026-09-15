"""Query-progress subgraph: pure SQL aggregation, no LLM call anywhere."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from graph.state import AppState
from models.db import get_daily_tally, get_meals_for_day, get_profile, session_scope


def format_progress(
    kcal: float,
    protein: float,
    target_kcal: int | None,
    protein_min: int | None,
    protein_max: int | None,
    meal_count: int,
) -> str:
    """Turn the day's numbers into the reply text."""
    if target_kcal is None:
        return (
            f"You've logged {meal_count} meal(s) today: "
            f"{kcal:.0f} kcal and {protein:.1f}g protein. "
            "Fill in your profile in the sidebar to get targets."
        )

    kcal_left = target_kcal - kcal
    lines = [
        f"**Today so far** ({meal_count} meal{'s' if meal_count != 1 else ''})",
        "",
        f"**Calories:** {kcal:.0f} / {target_kcal}  ({kcal_left:+.0f} left)",
    ]

    if protein < protein_min:
        note = f"{protein_min - protein:.1f}g to reach the band"
    elif protein <= protein_max:
        note = "in the band"
    else:
        note = f"{protein - protein_max:.1f}g over"
    lines.append(f"**Protein:** {protein:.1f} / {protein_min}–{protein_max} g  ({note})")

    return "\n".join(lines)


def query_progress_node(state: AppState) -> dict:
    """Sum today's meals and compare against the stored targets."""
    with session_scope() as session:
        kcal, protein = get_daily_tally(session)
        profile = get_profile(session)
        meal_count = len(get_meals_for_day(session))
        reply = format_progress(
            kcal,
            protein,
            profile.target_kcal if profile else None,
            profile.target_protein_min_g if profile else None,
            profile.target_protein_max_g if profile else None,
            meal_count,
        )

    return {"reply": reply, "messages": [AIMessage(content=reply)]}
