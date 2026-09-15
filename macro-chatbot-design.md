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
| LLM | Llama 3.1 8B-Instruct or Qwen2.5 7B-Instruct | Open-source, decent function-calling/structured-output support at this size |
| LLM hosting | Groq (dev) / Ollama (local, private, offline) | LangChain's `ChatGroq` / `ChatOllama` are drop-in swaps — build against one, keep the other as a fallback |
| Nutrition data | USDA FoodData Central API (free, no key limits worth worrying about) + a local CSV for common Indian dishes (IFCT 2017 dataset, or hand-seeded) | USDA is thin on Indian home-cooked food; a local override table fixes the biggest gap for your use case |
| Structured extraction | LangChain's `.with_structured_output()` (Pydantic schema) | Forces the model to return parseable food+quantity objects instead of free text |
| Storage | SQLite via SQLAlchemy | Single user, single file, zero setup — Postgres is overkill here |
| UI | Streamlit | Chat UI (`st.chat_message`, `st.chat_input`) + sidebar dashboard (goals, today's tally) in one framework, no separate frontend |

## 3. Data Model

```
User Profile (1 row, single-user app)
- height_cm, weight_kg, age, sex, activity_level
- goal_type: maintain | cut | bulk
- target_kcal, target_protein_g   # computed, cached
- updated_at

Meal Log
- id, timestamp, raw_text (what the user typed)
- items: [{food_name, quantity, unit, kcal, protein_g, carbs_g, fat_g}]
- total_kcal, total_protein_g  (denormalized for fast daily sums)

Food Cache
- food_name (normalized) -> {kcal_per_100g, protein, carbs, fat}
- populated from USDA/local CSV lookups, cached so repeat foods don't re-hit the API
```

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

**Query Progress subgraph:** pure SQL aggregation (sum of today's `kcal`/`protein_g` vs `target_kcal`/`target_protein_g`) — no LLM call needed at all, just format the numbers into a reply.

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

protein target (g) = 1.8–2.2 * weight_kg   (cut skews higher in this range to preserve muscle)
```
This is a plain function/tool, not an LLM call — deterministic and instantly testable.

## 6. Macro Estimation Strategy (the part most likely to go wrong)

- LLM extracts `{food, quantity, unit}` — units will be messy ("a bowl", "2 pieces", "a plate"). Keep a small local **unit-to-grams heuristic table** per food category (e.g., "roti" ≈ 40g/piece, "bowl of dal" ≈ 150g) rather than trusting the LLM to know gram weights — it won't, reliably.
- Always surface the estimate to the user before logging it ("Roti ×3 (~120g, 300kcal), Chole (~250g bowl, 350kcal) — look right?"). This is your main hallucination/error guardrail — cheap to add, saves you from silently wrong logs.
- Indian dish coverage: seed a ~100-row CSV of common items (roti, dal, sabzi varieties, rice, curd, paneer dishes) up front — this covers most of your actual daily logging and sidesteps USDA's poor Indian food coverage entirely.

## 7. UI Layout (Streamlit)

```
┌─────────────────────────────┬───────────────────────┐
│ Sidebar                     │ Main: Chat             │
│ ─────────────               │ ──────────             │
│ Profile (height/weight/etc) │ [chat history]          │
│ Goal: Cut / Maintain / Bulk │                         │
│ Today: 1450 / 2200 kcal     │ [chat_input box]        │
│ Protein: 90 / 145 g         │                         │
│ [progress bars]             │                         │
└─────────────────────────────┴───────────────────────┘
```
`st.progress()` for the two bars, recomputed on every meal save. Sidebar profile form only shown/editable once, then collapses to a summary.

## 8. Project Structure

```
macro_chatbot/
├── app.py                 # Streamlit entrypoint
├── graph/
│   ├── state.py           # AppState schema
│   ├── router.py
│   ├── onboarding.py
│   ├── log_meal.py
│   └── query_progress.py
├── nutrition/
│   ├── usda_client.py
│   ├── indian_foods.csv
│   └── unit_conversion.py
├── models/
│   ├── db.py               # SQLAlchemy models + session
│   └── schemas.py           # Pydantic (FoodItem, MealLog, etc.)
├── calc/
│   └── tdee.py              # Mifflin-St Jeor + goal math
└── config.py                # model provider, API keys
```

## 9. Build Phases

1. **Phase 1 (core loop, no LLM yet):** DB models, TDEE calculator, hardcoded meal input → prove the macro lookup + tally logic works.
2. **Phase 2:** Add LLM extraction node (structured output) in place of hardcoded input; add confirm-before-save step.
3. **Phase 3:** Full LangGraph router + onboarding subgraph; wire into Streamlit chat.
4. **Phase 4:** Polish — sidebar dashboard, progress bars, edge cases (corrections, deleting a logged meal, multi-day history view).

## 10. Open Questions to Decide Before Building

- Single-user (just you) vs. multi-user with login? Changes the DB schema (adds a `user_id` FK everywhere) — worth deciding now since it's a bigger retrofit later than up-front.
- Local Ollama vs. Groq as the default — affects whether this needs a GPU-capable machine or just internet access.
- How much manual correction UI do you want for misparsed quantities (inline edit vs. re-type the whole message)?
