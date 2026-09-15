# Macro-Tracking Chatbot — Design Document

## 1. Overview

A conversational app where the user:
1. Onboards once with body stats + activity level → app computes maintenance / cut / bulk calorie & protein targets.
2. Logs meals throughout the day in natural language ("2 rotis, 1 bowl chole, a bowl of curd").
3. Gets back estimated macros for that meal, and a running tally against the day's goal.

Core design principle: **the LLM parses language, it does not compute nutrition or math.** All calorie/macro values come from a nutrition database lookup; all TDEE/goal math is deterministic Python. This avoids hallucinated numbers, which is the single biggest risk in this kind of app.

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph | Multiple distinct conversational flows (onboarding, logging, query) that need to share/branch state — a plain chain isn't enough |
| LLM | Gemini 3.6 Flash (default) | Free tier, good structured-output support, no local GPU needed |
| LLM hosting | Google AI Studio (default) / Groq / Ollama | All three sit behind `llm.py`; switching is one env var, nothing downstream imports a provider package |
| Nutrition data | USDA FoodData Central API (free, no key limits worth worrying about) + a local CSV for common Indian dishes (IFCT 2017 dataset, or hand-seeded) | USDA is thin on Indian home-cooked food; a local override table fixes the biggest gap for your use case |
| Structured extraction | LangChain's `.with_structured_output()` (Pydantic schema) | Forces the model to return parseable food+quantity objects instead of free text |
| Storage | SQLite via SQLAlchemy | Single user, single file, zero setup — Postgres is overkill here |
| UI | Streamlit | Chat UI (`st.chat_message`, `st.chat_input`) + sidebar dashboard (goals, today's tally) in one framework, no separate frontend |

## 3. Data Model

```
User Profile (1 row, single-user app, pinned to id=1)
- height_cm, weight_kg, age, sex, activity_level
- goal_type: maintain | cut | bulk
- target_kcal                              # computed, cached
- target_protein_min_g, target_protein_max_g  # a band, not a single number
- updated_at

Meal Log
- id, timestamp, raw_text (what the user typed)
- items: [{food_name, quantity, unit, kcal, protein_g, carbs_g, fat_g}]  # JSON column
- total_kcal, total_protein_g  (denormalized for fast daily sums)

Food Cache
- food_name (normalized) -> {kcal_per_100g, protein, carbs, fat, source}
- populated from USDA lookups, cached so repeat foods don't re-hit the API

Chat Message
- id, timestamp, role (user | assistant), content
- the transcript, so a browser reload doesn't wipe the conversation
```

**Timestamps are naive local time, not UTC.** A daily tally has to match the user's
own calendar day — under UTC a 00:30 IST snack would count against the day before.

**Protein is a band because the evidence is a range, not a point.** Storing one
number would have meant inventing a precision the source material doesn't have.

## 4. LangGraph State Machine

```
                    ┌─────────────────┐
                    │   Router node    │  (classifies: onboarding / log_meal / query_progress / other)
                    └────────┬─────────┘
             ┌───────────────┼──────────────────┐
             ▼                ▼                  ▼
     ┌───────────────┐  ┌──────────────┐  ┌────────────────┐
     │ Onboarding     │  │ Log Meal     │  │ Query Progress  │
     │ subgraph       │  │ subgraph     │  │ subgraph        │
     └───────────────┘  └──────────────┘  └────────────────┘
```

**State schema** (shared `TypedDict`):
```python
class AppState(TypedDict):
    messages: list             # conversation history
    user_profile: dict | None  # None until onboarding complete
    pending_meal: dict | None  # structured items awaiting user confirmation
    route: str                 # set by router node
```

**Onboarding subgraph:** a simple sequence of "ask for missing field → parse reply → store" loops until height/weight/age/activity_level are all filled, then computes and stores targets. Re-enterable — if `user_profile` is already complete, router skips straight past this.

**Log Meal subgraph:**
1. `extract_items` node — LLM structured-output call: raw text → `list[FoodItem(name, qty, unit)]`
2. `lookup_macros` node — for each item, check Food Cache → else query USDA/local CSV → compute macros for the given quantity → cache result
3. `confirm` node — show the user the estimated breakdown, let them correct a quantity/food name before it's saved (important: LLM extraction *will* misparse "a bowl" sometimes — always show before saving, don't silently commit)
4. `save` node — write to Meal Log, update daily tally

**Query Progress subgraph:** pure SQL aggregation (sum of today's `kcal`/`protein_g` vs `target_kcal` and the `target_protein_min_g`–`target_protein_max_g` band) — no LLM call needed at all, just format the numbers into a reply. A test asserts the model is never consulted on this path.

## 5. TDEE / Goal Calculation

Mifflin-St Jeor (more accurate than Harris-Benedict for most people):

```
BMR (male)   = 10*weight_kg + 6.25*height_cm - 5*age + 5
BMR (female) = 10*weight_kg + 6.25*height_cm - 5*age - 161

TDEE = BMR * activity_multiplier
  sedentary        = 1.2
  light (1-3x/wk)  = 1.375
  moderate (3-5x/wk) = 1.55
  active (6-7x/wk)  = 1.725
  very active       = 1.9

cut  target_kcal = TDEE - 500   (≈0.5kg/week loss)
bulk target_kcal = TDEE + 300
maintain         = TDEE

floor: target_kcal is never below BMR. A 500 kcal deficit on a light, sedentary
body lands under resting expenditure; the target is clamped up to BMR and the
clamp is surfaced to the user rather than applied silently. Only a cut can ever
hit this, since TDEE is at least 1.2 × BMR.

protein target = a band in g/kg of bodyweight, by goal:
  cut       1.8 – 2.4   highest: a deficit makes the body readier to break down
                        muscle for energy, so protein is what protects lean mass
  bulk      1.6 – 2.2   calories already cover growth; more protein just displaces
                        the carbs and fats the surplus and training need
  maintain  1.6 – 2.0   enough to hold lean mass and recover, no deficit pressure
```
This is a plain function, not an LLM call — deterministic and instantly testable.
Covered by tests across every activity level, every goal type, and the BMR floor.

## 6. Macro Estimation Strategy (the part most likely to go wrong)

- LLM extracts `{food, quantity, unit}` — units will be messy ("a bowl", "2 pieces", "a plate"). Keep a small local **unit-to-grams heuristic table** per food category (e.g., "roti" ≈ 40g/piece, "bowl of dal" ≈ 150g) rather than trusting the LLM to know gram weights — it won't, reliably.
- Always surface the estimate to the user before logging it ("Roti ×3 (~120g, 300kcal), Chole (~250g bowl, 350kcal) — look right?"). This is your main hallucination/error guardrail — cheap to add, saves you from silently wrong logs.
- Indian dish coverage: seed a ~100-row CSV of common items (roti, dal, sabzi varieties, rice, curd, paneer dishes) up front — this covers most of your actual daily logging and sidesteps USDA's poor Indian food coverage entirely.
- Gram weights are resolved per food from the CSV first, then per category, then a
  generic fallback — and real mass/volume units ("200g", "250ml") bypass the guessing
  entirely. Each estimate records which of those four it used.

## 7. UI Layout (Streamlit)

```
┌─────────────────────────────┬───────────────────────┐
│ Sidebar                     │ Main: Chat             │
│ ─────────────               │ ──────────             │
│ Profile (height/weight/etc) │ [chat history]          │
│ Goal: Cut / Maintain / Bulk │                         │
│ Today: 848 / 2178 kcal      │ [confirm card, when    │
│ Protein: 34 / 135–180 g     │  a meal is pending]     │
│ [progress bars]             │ [chat_input box]        │
│ [n meal(s) logged]          │                         │
└─────────────────────────────┴───────────────────────┘
```
`st.progress()` for the two bars, recomputed on every meal save. The protein bar
fills toward the band minimum. The sidebar profile form collapses to a one-line
summary once filled, and reopens on demand.

## 8. Project Structure

```
macrowize/
├── app.py                  # Streamlit entrypoint
├── llm.py                  # the only place a chat model is constructed
├── config.py               # DB path, model provider, API keys (loads .env)
├── manual_entry.py         # CLI harness from Phase 1; no LLM, still useful for debugging
├── graph/
│   ├── __init__.py         # build_graph() — wires the router to the subgraphs
│   ├── state.py            # AppState schema
│   ├── router.py
│   ├── onboarding.py
│   ├── log_meal.py
│   └── query_progress.py
├── nutrition/
│   ├── usda_client.py
│   ├── lookup.py           # cache -> CSV -> USDA resolution, and quantity scaling
│   ├── indian_foods.csv    # ~130 dishes, 278 lookup keys including aliases
│   └── unit_conversion.py
├── models/
│   ├── db.py               # SQLAlchemy models + session + queries
│   └── schemas.py          # Pydantic (FoodItem, ExtractedMeal, ProfileInput, ...)
├── calc/
│   └── tdee.py             # Mifflin-St Jeor + goal math
├── tests/                  # 53 tests; LLM stubbed, UI driven via Streamlit AppTest
└── .env                    # GEMINI_API_KEY, USDA_API_KEY (gitignored)
```
Deviations from the original plan: the project root *is* the package (no nested
`macro_chatbot/`); `lookup.py` and `llm.py` were added as the seams that keep
provider and data-source choices out of the graph nodes.

## 9. Build Phases — all complete, one commit each

1. **Phase 1 (core loop, no LLM):** DB models, TDEE calculator, manual meal input →
   proved the tally and calculation logic before any model was involved.
2. **Phase 2:** USDA client, Indian foods CSV, unit conversion; replaced the
   hand-entered macros with real lookups.
3. **Phase 3:** LangGraph router + all three subgraphs, structured-output extraction,
   confirm-before-save as its own interrupting node.
4. **Phase 4:** Streamlit sidebar dashboard and chat, wired to the compiled graph.

Not yet built, from the original Phase 4 list: deleting a logged meal, and a
multi-day history view. Both only reachable through the sidebar today.

## 10. Decisions Made

- **Single-user, no login.** No `user_id` FK anywhere; `UserProfile` is one row pinned
  to id=1. A retrofit later means touching every table, and that trade was taken
  knowingly.
- **Gemini 3.6 Flash as the default**, via Google AI Studio's free tier. Groq and
  Ollama both still work — `MACROWIZE_LLM_PROVIDER` picks one and `llm.py` is the
  only file that knows the difference. (Note: `gemini-2.5-flash` is no longer served
  to new API keys.)
- **Correction UI: inline.** Each item in the confirmation gets an editable quantity
  and a keep/discard checkbox. Changing a quantity re-runs the deterministic lookup
  rather than scaling numbers client-side, so corrected macros still come from the
  data layer.

## 11. Known Gaps

- **No migrations.** Schema changes rely on `create_all` picking up *new* tables;
  altering an existing one means dropping the DB. Alembic before there's real data
  worth keeping.
- **The CSV is hand-seeded**, not from IFCT or any audited source. Values are
  plausible, not verified — worth spot-checking the foods eaten most often.
- **A pending confirmation does not survive a reload**, because the checkpointer is
  in-memory. Nothing is lost except the parse; the meal was never saved.
- **USDA match quality is mediocre** for generic terms ("almonds" can match almond
  butter). The CSV covers the common cases, so this mostly shows up on the long tail.
