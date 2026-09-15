"""Assemble the router and the three subgraphs into one compiled app."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.log_meal import (
    after_confirm,
    confirm_node,
    extract_items_node,
    lookup_macros_node,
    save_node,
)
from graph.onboarding import (
    ask_missing_node,
    check_complete,
    compute_targets_node,
    extract_profile_node,
)
from graph.query_progress import query_progress_node
from graph.router import pick_branch, route_node
from graph.state import AppState

FALLBACK_REPLY = (
    "I track meals and macros. Tell me what you ate (\"2 rotis and a bowl of dal\"), "
    "ask how today's going, or update your stats in the sidebar."
)


def other_node(state: AppState) -> dict:
    """Canned reply for off-topic turns -- deliberately not an LLM call, so the
    model never gets a free-text opening to volunteer nutrition numbers."""
    return {"reply": FALLBACK_REPLY, "messages": [AIMessage(content=FALLBACK_REPLY)]}


def build_graph(checkpointer: MemorySaver | None = None):
    """Wire up the full state machine and compile it.

    A checkpointer is required for the confirm step -- `interrupt` needs somewhere
    to park state between the pause and the resume.
    """
    builder = StateGraph(AppState)

    builder.add_node("router", route_node)
    builder.add_node("other", other_node)

    builder.add_node("extract_profile", extract_profile_node)
    builder.add_node("ask_missing", ask_missing_node)
    builder.add_node("compute_targets", compute_targets_node)

    builder.add_node("extract_items", extract_items_node)
    builder.add_node("lookup_macros", lookup_macros_node)
    builder.add_node("confirm", confirm_node)
    builder.add_node("save_meal", save_node)

    builder.add_node("query_progress", query_progress_node)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        pick_branch,
        {
            "onboarding": "extract_profile",
            "log_meal": "extract_items",
            "query_progress": "query_progress",
            "other": "other",
        },
    )

    builder.add_conditional_edges(
        "extract_profile",
        check_complete,
        {"ask": "ask_missing", "compute": "compute_targets"},
    )
    builder.add_edge("ask_missing", END)
    builder.add_edge("compute_targets", END)

    builder.add_edge("extract_items", "lookup_macros")
    builder.add_edge("lookup_macros", "confirm")
    builder.add_conditional_edges(
        "confirm", after_confirm, {"save_meal": "save_meal", "__end__": END}
    )
    builder.add_edge("save_meal", END)

    builder.add_edge("query_progress", END)
    builder.add_edge("other", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
