"""Central configuration: database location, nutrition API, and LLM provider."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

# Keys live in .env (gitignored). Real environment variables win over the file.
load_dotenv(PROJECT_ROOT / ".env")

DB_PATH = Path(os.getenv("MACROWIZE_DB", PROJECT_ROOT / "macrowize.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

INDIAN_FOODS_CSV = PROJECT_ROOT / "nutrition" / "indian_foods.csv"

# DEMO_KEY works but is rate-limited to ~30 requests/hour per IP.
# Get a real one free at https://fdc.nal.usda.gov/api-key-signup.html
USDA_API_KEY = os.getenv("USDA_API_KEY", "DEMO_KEY")
USDA_TIMEOUT_SECONDS = float(os.getenv("USDA_TIMEOUT_SECONDS", "10"))

# Swap providers with one env var: MACROWIZE_LLM_PROVIDER=groq|ollama
LLM_PROVIDER = os.getenv("MACROWIZE_LLM_PROVIDER", "google")
DEFAULT_MODELS = {
    "google": "gemini-3.6-flash",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.1:8b",
}
LLM_MODEL = os.getenv("MACROWIZE_LLM_MODEL") or DEFAULT_MODELS.get(LLM_PROVIDER, "")
LLM_TEMPERATURE = float(os.getenv("MACROWIZE_LLM_TEMPERATURE", "0"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
