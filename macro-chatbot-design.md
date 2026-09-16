# macrowize — Design Document

A macro-tracking chatbot. You tell it what you ate in plain English; it works out
the calories and protein and tracks them against a daily target.

## 1. The core idea

**The LLM parses language. It does not do arithmetic.**

That single rule shapes everything else. A language model asked "how many calories
in 2 rotis?" will answer confidently and often wrongly, and you have no way to tell
which. So the model is given a much smaller job — turn *"3 rotis and a bowl of
rajma"* into `[{roti, 3, piece}, {rajma, 1, bowl}]` — and every number after that
comes from a lookup table.

Two supporting rules fall out of it:

- **The maths is plain Python.** BMR, TDEE and goal targets are a pure function.
  No model involved, so it is testable with exact expected values.
- **Nothing is saved without confirmation.** Extraction *will* misread quantities
  sometimes. Every parsed meal is shown for approval before it reaches the database.

## 2. Stack

| Layer | Choice | Why |
|---|---|---|
| UI | Streamlit | Chat and a sidebar dashboard in one file, no separate frontend |
| LLM | Gemini 3.6 Flash, via LangChain | Free tier; Groq and Ollama are drop-in alternatives |
| Structured output | `.with_structured_output()` + Pydantic | Forces parseable objects instead of free text |
| Nutrition data | Local CSV (~130 Indian dishes) + USDA FoodData Central | The CSV covers Indian home cooking, which USDA is thin on; USDA covers the long tail |
| Storage | SQLite via SQLAlchemy | Single user, single file, zero setup |

## 3. Files

```
macrowize/
├── app.py            Streamlit UI — sidebar dashboard, chat, history view
├── agent.py          the two LLM calls, and nothing else
├── nutrition.py      the lookup chain and unit → grams conversion
├── usda.py           USDA FoodData Central client and result ranking
├── tdee.py           Mifflin-St Jeor and goal targets
├── db.py             SQLAlchemy models and queries
├── indian_foods.csv  ~130 foods, 278 lookup keys including aliases
└── tests/            35 tests
```

Six modules, no subpackages. `agent.py` is the only file that knows an LLM exists;
`nutrition.py` is the only file that decides where a macro value comes from.

## 4. How one message flows

    "3 rotis and a bowl of rajma"
              │
              ▼
    agent.classify()          LLM call 1 → "log_meal"
              │
              ▼
    agent.parse_meal()        LLM call 2 → [{roti,3,piece}, {rajma,1,bowl}]
              │                            ...and nothing else. No kcal field
              ▼                               exists on the schema to fill in.
    nutrition.resolve_all()   CSV → roti 40g/piece × 3 = 120g
              │                     120g × 264 kcal/100g = 317 kcal
              │               (a CSV miss falls through to the USDA cache,
              ▼                then the USDA API — see §7)
    st.session_state.pending  shown to the user. NOT saved.
              │
              ▼ (only on a click of "Save meal")
    db.save_meal()

The other two routes are shorter: `query_progress` is a SQL aggregation with no
model call at all, and `other` returns a fixed string.

Earlier versions ran this through a LangGraph state machine with a checkpointer.
It was removed: the routing was an `if/elif` in disguise, and the checkpointer held
a second copy of the pending meal that `st.session_state` was already holding. Two
state stores kept in sync by hand is a bug waiting to happen, not a safety feature.

## 5. Data model

```
UserProfile  (exactly one row, id=1 — single-user app)
  height_cm, weight_kg, age, sex, activity_level, goal_type
  target_kcal
  target_protein_min_g, target_protein_max_g

MealLog
  timestamp, raw_text (what was typed)
  items: [{food_name, quantity, unit, kcal, protein_g, carbs_g, fat_g}]  (JSON)
  total_kcal, total_protein_g   (denormalized so a daily sum is one query)

ChatMessage
  timestamp, role, content   (so a browser reload doesn't wipe the conversation)

FoodCache
  query (pk), description, kcal, protein_g, carbs_g, fat_g
  USDA results, so the same food costs one API call ever rather than one per meal
```

Two decisions worth writing down:

**Timestamps are naive local time, not UTC.** A daily tally has to match the user's
own calendar day. Under UTC a 00:30 snack would count against the day before.

**Protein is a band, not a number.** The evidence is a range, so storing a single
figure would invent precision that isn't there.

**Every query helper returns plain dicts, never ORM rows.** Callers read them after
the session has closed, and a detached SQLAlchemy row raises on attribute access —
a bug that got shipped three separate times before the rule was made absolute.

## 6. The maths

Mifflin-St Jeor:

```
BMR (male)   = 10*weight_kg + 6.25*height_cm - 5*age + 5
BMR (female) = 10*weight_kg + 6.25*height_cm - 5*age - 161

TDEE = BMR × activity multiplier
  sedentary 1.2 · light 1.375 · moderate 1.55 · active 1.725 · very active 1.9

cut       TDEE - 500
maintain  TDEE
bulk      TDEE + 300

floor: never below BMR. A 500 kcal deficit on a light, sedentary body lands under
resting expenditure, so the target is clamped up and the clamp is shown to the
user rather than applied silently. Only a cut can hit this — TDEE is ≥ 1.2 × BMR.

protein, g per kg of bodyweight:
  cut       1.8 – 2.4   highest: in a deficit the body is readier to break down
                        muscle for energy, and protein is what protects it
  bulk      1.6 – 2.2   calories already cover growth; more protein only displaces
                        the carbs and fats the surplus and training need
  maintain  1.6 – 2.0   enough to hold lean mass and recover
```

## 7. Where a macro value comes from

Three sources, cheapest and most trusted first:

1. **`indian_foods.csv`** — roti, dal, every sabzi. Hand-seeded, and the reason the
   app works at all for Indian home cooking.
2. **`FoodCache`** — a USDA result already fetched once.
3. **USDA FoodData Central** — strawberries, broccoli, a protein bar.

If all three miss, the food is reported as unresolved and contributes nothing. It
is never given a guessed value.

**USDA's own ranking cannot be trusted.** Its first result for `salmon` is *Fish
oil, salmon* at 902 kcal/100g; for `avocado` it's *Oil, avocado* at 884. Logging
those would be far worse than logging nothing, so candidates are re-ranked in
`usda.py` on one rule: **the query must match the head of the description** — the
part before the first comma. *Strawberries, raw* has the head "Strawberries" and
matches. *Oil, avocado* has the head "Oil" and does not.

Three adjustments on top, each earned by a wrong answer seen in testing:

| problem | example | rule |
|---|---|---|
| a different form of the food | `Oil, avocado` | reject *oil, juice, powder, extract* heads |
| padded brand entries | `SILK Strawberry soy yogurt` | penalise extra head words and SHOUTY brand names |
| a different species or dish | `Sea cucumber`, `Spinach souffle` | penalise head words not in the query |

After ranking: strawberry 32, avocado 167, broccoli 31, cucumber 10, spinach 23,
blueberries 57 — all correct.

USDA gives per-100g values and no household weights, so a USDA food falls back to
100g for "a bowl". Foods you eat by the bowl are the ones in the CSV anyway.

## 8. Turning "a bowl" into grams

The messy part. The model reports the unit the user said — *piece*, *bowl*,
*katori*, *plate* — and never a gram weight, because it doesn't reliably know them.

Resolution order:

1. **A real mass or volume** (`200g`, `250ml`) is used directly. No guessing.
2. **The food's own weight** from the CSV — roti 40g/piece, dal 150g/bowl.
3. **Any weight that food does have**, if the exact slot is blank.
4. **100g**, as a last resort — which is where every USDA food lands.

Unit synonyms collapse first: *katori* and *glass* are bowls, *plate* and *portion*
are servings. A food named inside a phrase still matches — "a bowl of dal tadka"
finds `dal tadka`, and the longest match wins so "chicken biryani" doesn't degrade
to plain "biryani".

## 9. Providers

One environment variable: `MACROWIZE_LLM_PROVIDER=google|groq|ollama`. Nothing
outside `agent.py` knows which is in use.

This matters more than it looks: Gemini's free tier is **20 requests per day, per
model**, which one testing session exhausts. Groq is the hosted fallback; Ollama
runs locally with no key and no quota at all (`llama3.2:3b` is enough for this job).

API failures — rate limits, timeouts, outages — are caught and shown as a chat
message. On a free tier they are normal operation, not exceptional.

## 10. What's tested, and what isn't

35 tests, roughly a third the size of the source. Each maps to a claim worth
defending:

- **`test_tdee.py`** — every activity multiplier, every goal type, and the BMR
  floor, with exact expected values. Pure functions, so the assertions are exact.
- **`test_nutrition.py`** — macros are scaled from the CSV; the CSV is consulted
  before the API; a USDA result is cached so the second lookup is free; an unknown
  food returns nothing rather than a guess.
- **`test_usda.py`** — the ranking rules, against real descriptions the API actually
  returned. Pure scoring, so no network is needed.
- **`test_app.py`** — the confirm gate: a parsed meal is absent from the database
  while the confirmation is on screen, a corrected quantity is re-looked-up rather
  than multiplied, progress is answered without a model call, and a failing API
  call becomes a message instead of a traceback.

Deliberately not tested: Streamlit widget plumbing, SQLAlchemy CRUD, and the LLM
prompts themselves. The first two are the frameworks' job; the third can't be
asserted on meaningfully and is checked by hand instead.

## 11. Known gaps

- **No migrations.** New tables appear via `create_all`; altering an existing one
  means dropping the database. Fine pre-release, not fine once there's real data.
- **The CSV is hand-seeded**, not from IFCT or any audited source. Spot-checking
  against USDA where the two overlap gave six of ten within 3% (banana, guava, egg,
  curd, peanuts, milk), but the Indian dishes — the bulk of the file — are estimates
  and remain unverified. Tofu is known to be wrong: 76 kcal is silken, firm is ~145.
- **A pending confirmation does not survive a reload.** It lives in session state.
  Nothing is lost but the parse — the meal was never saved.
- **Onboarding is the sidebar form only.** Chat-based onboarding existed once and
  was removed as a second path to the same single row.
