"""Shared state passed between every node in the graph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AppState(TypedDict, total=False):
    """State threaded through the router and all three subgraphs.

    `reply` is this turn's assistant text. It's kept separate from `messages` so the
    UI has one unambiguous string to render, rather than having to dig the last
    AIMessage out of the history.
    """

    messages: Annotated[list, add_messages]
    user_profile: dict[str, Any] | None
    pending_meal: dict[str, Any] | None
    route: str
    reply: str
