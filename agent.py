"""The agent: two LLM calls, both of which only ever return structured text.

The model classifies a message and extracts what was eaten. It never produces a
calorie or macro number -- the schemas below have no field for one, so there is
nowhere for it to put one even if it tried.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent / ".env")

# Gemini's free tier is 20 requests/day *per model*, so a fallback matters.
PROVIDER = os.getenv("MACROWIZE_LLM_PROVIDER", "google")
DEFAULT_MODELS = {
    "google": "gemini-3.6-flash",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.1:8b",
}
MODEL = os.getenv("MACROWIZE_LLM_MODEL") or DEFAULT_MODELS[PROVIDER]

ROUTE_PROMPT = """Classify one message in a macro-tracking app.

- "log_meal": they're reporting food they ate ("2 rotis and dal", "had curd").
- "query_progress": they're asking how today is going ("how much protein left?").
- "other": anything else.

Classify only. Do not answer the message."""

EXTRACT_PROMPT = """Extract every food the user says they ate.

For each: name, quantity, unit.
- Use the unit they said: piece, bowl, katori, plate, glass, g, ml.
- "a"/"an"/"some" means quantity 1.
- Split compound meals: "2 rotis and dal" is two items.
- Singular names: "rotis" -> "roti".
- Never guess calories or macros. Report only what was eaten."""


class LLMUnavailableError(RuntimeError):
    """No API key, so no model."""


class RouteDecision(BaseModel):
    route: Literal["log_meal", "query_progress", "other"]


class FoodItem(BaseModel):
    """One food, as the user described it. Note the absence of any macro field."""

    name: str = Field(description="The food, plainly: 'roti', 'dal'")
    quantity: float = Field(gt=0, description="How many, e.g. 2 for '2 rotis'")
    unit: str = Field(description="piece, bowl, katori, plate, glass, g, ml")


class ExtractedMeal(BaseModel):
    items: list[FoodItem] = Field(description="Every distinct food mentioned")


@functools.lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Build the chat model once, for whichever provider is configured.

    Switching is one env var: MACROWIZE_LLM_PROVIDER=groq|ollama|google.
    """
    if PROVIDER == "google":
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise LLMUnavailableError("GEMINI_API_KEY is not set. Add it to .env.")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=MODEL, temperature=0, google_api_key=key)

    if PROVIDER == "groq":
        if not os.getenv("GROQ_API_KEY"):
            raise LLMUnavailableError("GROQ_API_KEY is not set. Add it to .env.")
        from langchain_groq import ChatGroq

        return ChatGroq(model=MODEL, temperature=0)

    if PROVIDER == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=MODEL, temperature=0)

    raise LLMUnavailableError(f"Unknown provider {PROVIDER!r}. Use google, groq or ollama.")


def _ask(schema: type[BaseModel], system: str, text: str):
    """One structured-output call."""
    return get_llm().with_structured_output(schema).invoke(
        [SystemMessage(content=system), HumanMessage(content=text)]
    )


def classify(text: str) -> str:
    """Return 'log_meal', 'query_progress' or 'other'."""
    return _ask(RouteDecision, ROUTE_PROMPT, text).route


def parse_meal(text: str) -> list[FoodItem]:
    """Pull the foods out of an 'I ate ...' message."""
    return _ask(ExtractedMeal, EXTRACT_PROMPT, text).items
