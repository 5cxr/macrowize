# macrowize

A macro tracker you talk to. Say *"3 rotis and a bowl of rajma"* and it logs the
calories and protein against your daily target.

The interesting part isn't the chat — it's that **the language model never
produces a number**. It only turns your sentence into structured items. Every
calorie comes from a lookup table or the USDA database.

```
"2 rotis and dal"
        |
        v
 graph/router   ->  onboarding | log_meal | query_progress | other
        |
        v
 graph/log_meal ->  extract -> lookup -> confirm (interrupt) -> save
     |        |                  |                                |
   LLM, no macros in         nutrition/: CSV -> cache -> USDA   models/db.py
   the schema
```

## Why

Ask a language model "how many calories in 2 rotis" and it answers confidently,
often wrongly, and you can't tell which times. For a tracking app that's fatal —
you'd log plausible fiction all week.

So the model gets a smaller job: extraction. The Pydantic schema it fills in
(`models/schemas.py:FoodItem`) has `name`, `quantity` and `unit`, and no field
for a calorie, so there is nowhere to put one even if it tried. Numbers come
from a 130-row CSV of Indian food first (USDA is thin on home cooking), then
USDA for the long tail, cached in SQLite. A food that misses everywhere comes
back unresolved rather than invented.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # then add GEMINI_API_KEY
streamlit run app.py
```

Needs Python 3.13. First run asks for height, weight, age, sex, activity level
and goal — in the sidebar form, or just by telling the chat.

## Configuration

All optional except a key for the active provider. `.env` is gitignored.

| var | default | what |
|---|---|---|
| `MACROWIZE_LLM_PROVIDER` | `google` | `google`, `groq` or `ollama` |
| `MACROWIZE_LLM_MODEL` | per provider | overrides the default model |
| `MACROWIZE_LLM_TEMPERATURE` | `0` | |
| `GEMINI_API_KEY` | — | required when provider is `google` ([key](https://aistudio.google.com/apikey)) |
| `GROQ_API_KEY` | — | required when provider is `groq` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `USDA_API_KEY` | `DEMO_KEY` | [sign up](https://fdc.nal.usda.gov/api-key-signup.html); the demo key is capped at ~30 requests/hour per IP |
| `USDA_TIMEOUT_SECONDS` | `10` | |
| `MACROWIZE_DB` | `./macrowize.db` | SQLite file path |

All of it is read once in `config.py`, so **restart Streamlit after changing
it**. Ollama runs fully offline — `ollama serve` plus `ollama pull llama3.1:8b`,
no key, no quota.

## The graph

One LangGraph app, compiled in `graph/__init__.py`, with a router in front of
three subgraphs:

- **onboarding** — pulls body stats out of plain sentences, asks for whatever is
  still missing, then computes targets.
- **log_meal** — extract → lookup → **confirm** → save. The confirm node calls
  `interrupt()`, so execution suspends there and nothing reaches `save_meal`
  until the UI resumes with `Command(resume=...)`. A misparsed meal can never be
  written silently.
- **query_progress** — answers "how much protein left?" from the day's rows.

State (`graph/state.py:AppState`) threads `messages`, `user_profile`,
`pending_meal`, `route` and `reply` through every node. `reply` is kept separate
from `messages` so the UI has one unambiguous string to render. A `MemorySaver`
checkpointer per browser session is what makes the interrupt resumable.

## What else it does

- **Dashboard** — calories and protein eaten vs. target, in the sidebar.
- **Targets** — Mifflin-St Jeor BMR, activity multiplier, then a goal delta
  (cut −500, bulk +300) and a protein band in g/kg per goal.
- **History** — multi-day view, and any logged meal can be deleted.
- **Persistent chat** — the transcript is stored in SQLite and survives a
  reload; a pending confirmation does not.

## Layout

| path | lines | what |
|---|---:|---|
| `app.py` | 406 | Streamlit UI — sidebar dashboard, chat, confirm card, history |
| `graph/` | 547 | router + the three subgraphs + shared state |
| `models/` | 365 | SQLAlchemy models, queries, Pydantic schemas |
| `nutrition/` | 472 | lookup chain, "a bowl" → grams, USDA client |
| `calc/tdee.py` | 126 | Mifflin-St Jeor and goal targets |
| `llm.py` | 71 | provider selection, one chat model, cached |
| `config.py` | 34 | every env var, read once |
| `manual_entry.py` | 198 | CLI scaffolding from phase 1 — DB + TDEE without an LLM |

Two boundaries worth naming: `llm.py` is the only file that knows which provider
is in use, and `nutrition/` is the only place that decides where a number comes
from. That's why swapping Gemini for Ollama is one environment variable.

`nutrition/indian_foods.csv` holds per-100g macros plus household weights —
grams per piece, bowl and serving — so "a bowl of dal" converts without guessing.

## Tests

```bash
pytest          # 63 tests, no network, no API key
```

The UI tests drive Streamlit headlessly via `AppTest` with the LLM stubbed; the
graph tests assert the database is still empty while the confirm interrupt is
pending.

## Known limits

- Single user — the profile is one row pinned to `id=1`.
- Timestamps are naive local time, so a daily tally lines up with your own
  calendar day rather than UTC.
- The checkpointer is in-memory, so a pending confirmation is lost on reload.
- Gemini's free tier is 20 requests/day per model; Groq or Ollama if you hit it.

Architecture and the reasoning behind each decision: `macro-chatbot-design.md`.

> A flattened rewrite of this app — same behaviour, six flat modules, no
> LangGraph — lives on the `flat-agent-rewrite` branch. Neither is merged into
> the other.
