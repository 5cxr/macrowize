"""Log-meal subgraph: extract -> lookup -> confirm -> save.

The confirm step is its own node and it *interrupts*. Nothing reaches `save_meal`
without the user explicitly resuming the graph, so a misparsed meal can never be
written silently.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from graph.state import AppState
from llm import get_llm
from models.db import save_meal, session_scope
from models.schemas import ExtractedMeal
from nutrition.lookup import resolve_item

EXTRACTION_SYSTEM_PROMPT = """Extract every food the user says they ate.

For each food return: name, quantity, unit.

Rules:
- Use the unit the user actually said: piece, bowl, katori, plate, glass, g, ml.
- "a"/"an"/"some" means quantity 1.
- Split compound meals: "2 rotis and dal" is two items.
- Keep food names simple and singular: "rotis" -> "roti".
- Never guess calories or macros. You are only reporting what was eaten."""


def extract_items_node(state: AppState) -> dict:
    """LLM structured-output call: raw text -> list of {name, quantity, unit}."""
    last_message = state["messages"][-1]
    text = last_message.content if hasattr(last_message, "content") else str(last_message)

    extracted = get_llm().with_structured_output(ExtractedMeal).invoke(
        [SystemMessage(content=EXTRACTION_SYSTEM_PROMPT), HumanMessage(content=text)]
    )

    return {
        "pending_meal": {
            "raw_text": text,
            "extracted": [item.model_dump() for item in extracted.items],
        }
    }


def lookup_macros_node(state: AppState) -> dict:
    """Resolve each extracted item against the cache, CSV, then USDA."""
    pending = dict(state["pending_meal"])
    resolved: list[dict] = []
    unresolved: list[str] = []

    with session_scope() as session:
        for item in pending["extracted"]:
            match = resolve_item(session, item["name"], item["quantity"], item["unit"])
            if match is None:
                unresolved.append(item["name"])
                continue
            resolved.append(
                {
                    **match.to_meal_item(),
                    "grams": round(match.grams, 1),
                    "source": match.source,
                    "estimate_basis": match.estimate_basis,
                    "estimate_note": match.estimate_note,
                }
            )

    pending["items"] = resolved
    pending["unresolved"] = unresolved
    pending["total_kcal"] = round(sum(item["kcal"] for item in resolved), 1)
    pending["total_protein_g"] = round(sum(item["protein_g"] for item in resolved), 1)
    return {"pending_meal": pending}


def format_meal_summary(pending: dict) -> str:
    """Render the breakdown the user is asked to approve."""
    if not pending["items"]:
        names = ", ".join(pending.get("unresolved", [])) or "anything"
        return f"I couldn't find nutrition data for {names}. Try naming the dish differently?"

    lines = ["Here's what I got — look right?", ""]
    for item in pending["items"]:
        lines.append(
            f"• **{item['food_name']}** ×{item['quantity']:g} {item['unit']}"
            f" (~{item['grams']:g}g) — {item['kcal']:.0f} kcal,"
            f" {item['protein_g']:.1f}g protein"
        )
    lines.append("")
    lines.append(
        f"**Total: {pending['total_kcal']:.0f} kcal, "
        f"{pending['total_protein_g']:.1f}g protein**"
    )
    if pending.get("unresolved"):
        lines.append("")
        lines.append(
            f"_Not found, so not counted: {', '.join(pending['unresolved'])}_"
        )
    return "\n".join(lines)


def confirm_node(state: AppState) -> dict:
    """Pause the graph and wait for the user to approve, edit, or cancel.

    `interrupt` suspends execution here; the value it returns is whatever the UI
    later passes via `Command(resume=...)`.
    """
    pending = state["pending_meal"]

    decision = interrupt(
        {"kind": "confirm_meal", "pending_meal": pending, "summary": format_meal_summary(pending)}
    )

    action = decision.get("action") if isinstance(decision, dict) else str(decision)

    if action == "confirm":
        edited = decision.get("items") if isinstance(decision, dict) else None
        if edited:
            pending = {
                **pending,
                "items": edited,
                "total_kcal": round(sum(item["kcal"] for item in edited), 1),
                "total_protein_g": round(sum(item["protein_g"] for item in edited), 1),
            }
        return {"pending_meal": pending, "route": "save"}

    return {
        "pending_meal": None,
        "route": "cancelled",
        "reply": "Dropped it — nothing was saved.",
        "messages": [AIMessage(content="Dropped it — nothing was saved.")],
    }


def after_confirm(state: AppState) -> str:
    """Conditional edge: only a confirmed meal continues to the save node."""
    return "save_meal" if state.get("route") == "save" else "__end__"


def save_node(state: AppState) -> dict:
    """Write the approved meal and report the updated daily tally."""
    from models.db import get_daily_tally, get_profile

    pending = state["pending_meal"]
    items = [
        {
            "food_name": item["food_name"],
            "quantity": item["quantity"],
            "unit": item["unit"],
            "kcal": item["kcal"],
            "protein_g": item["protein_g"],
            "carbs_g": item["carbs_g"],
            "fat_g": item["fat_g"],
        }
        for item in pending["items"]
    ]

    with session_scope() as session:
        save_meal(session, raw_text=pending["raw_text"], items=items)
        session.flush()
        kcal, protein = get_daily_tally(session)
        profile = get_profile(session)
        target_kcal = profile.target_kcal if profile else None
        protein_min = profile.target_protein_min_g if profile else None
        protein_max = profile.target_protein_max_g if profile else None

    lines = [f"Logged — {pending['total_kcal']:.0f} kcal, {pending['total_protein_g']:.1f}g protein."]
    if target_kcal:
        lines.append("")
        lines.append(f"**Today:** {kcal:.0f} / {target_kcal} kcal ({target_kcal - kcal:+.0f})")
        lines.append(f"**Protein:** {protein:.1f} / {protein_min}–{protein_max} g")
    reply = "\n".join(lines)

    return {
        "pending_meal": None,
        "route": "saved",
        "reply": reply,
        "messages": [AIMessage(content=reply)],
    }
