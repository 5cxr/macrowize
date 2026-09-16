# macrowize — explanation notes

Read-from-this notes for demoing and defending the project. The architecture lives
in `macro-chatbot-design.md`; this file is about *explaining* it out loud.

---

## 1. The pitch

**30 seconds**

> It's a macro tracker you talk to. You say *"3 rotis and a bowl of rajma"* and it
> logs the calories and protein against your daily target.
>
> The interesting part isn't the chat — it's that **the language model never
> produces a number**. It only converts your sentence into structured items. Every
> calorie comes from a lookup table or an API.

**2 minutes — the problem it solves**

> If you ask a language model "how many calories in 2 rotis", it answers
> confidently, and it's often wrong, and you can't tell which times. For a tracking
> app that's fatal — you'd be logging plausible fiction all week.
>
> So I gave the model a much smaller job: turn *"3 rotis and a bowl of rajma"* into
> `[{roti, 3, piece}, {rajma, 1, bowl}]`. That's a language task, which is what it's
> actually good at. Then real numbers come from three places in order — a local CSV
> of Indian food, a cache, and the USDA database.
>
> The Pydantic schema the model fills in has `name`, `quantity` and `unit` — and no
> calorie field. There's literally nowhere for it to put a made-up number.
>
> Then two more rules fall out of that: the BMR/TDEE maths is plain Python so it's
> exactly testable, and every parsed meal is shown to you for confirmation before
> anything is saved, because the extraction *will* misread a quantity sometimes.

---

## 2. Demo script

Run it: `./.venv/bin/streamlit run app.py` → http://localhost:8501

| step | do this | say this |
|---|---|---|
| 1 | Sidebar → fill in profile → Save | "Mifflin-St Jeor computes the targets. Plain Python, no model." |
| 2 | Type `3 rotis and a bowl of rajma` | "One call classifies the message, a second extracts the foods." |
| 3 | **Point at the confirm card** | "Nothing is saved yet. This is the guardrail — extraction gets quantities wrong, so I never auto-commit." |
| 4 | Change a quantity → Save | "Changing that re-runs the lookup. It doesn't multiply the number on screen — macros only ever come from the data layer." |
| 5 | Type `how much protein do I have left?` | "This one makes *no* model call at all. It's a SQL aggregation." |
| 6 | Type `200g of strawberries` | "Not in my CSV, so it falls through to the USDA API — then gets cached." |
| 7 | Sidebar → History | "Per-day totals. Days with nothing logged show as gaps rather than disappearing." |
| 8 | Delete a meal | "Two clicks, because it can't be undone." |

**Have a fallback ready.** If the network or quota dies mid-demo, switch to the
local model: `MACROWIZE_LLM_PROVIDER=ollama MACROWIZE_LLM_MODEL=llama3.2:3b`.
Requires `ollama serve` running. Being able to say *"and it runs fully offline"*
is a bonus, not an excuse.

---

## 3. The six files

| file | lines | one-line summary |
|---|---:|---|
| `app.py` | 443 | Streamlit UI — sidebar dashboard, chat, confirm card, history |
| `db.py` | 258 | SQLAlchemy models and every query |
| `nutrition.py` | 180 | the lookup chain, and "a bowl" → grams |
| `usda.py` | 141 | USDA client + result ranking |
| `tdee.py` | 126 | Mifflin-St Jeor and goal targets |
| `agent.py` | 113 | the two LLM calls, and nothing else |

~1,260 lines, 35 tests. **Two boundaries worth naming out loud:**

- `agent.py` is the only file that knows an LLM exists.
- `nutrition.py` is the only file that decides where a number comes from.

That's why swapping Gemini for Ollama is one environment variable, and why "could
the model have invented this calorie count?" has a one-word answer: no.

---

## 4. Tech stack

**Python 3.13**, one Streamlit process, one SQLite file, one LLM provider at a time.
No containers, no API server, no frontend build.

| tech | version | where | what it does |
|---|---|---|---|
| **Streamlit** | 1.63 | `app.py` | The entire UI. Re-runs the whole script on every interaction. Also `AppTest` for headless UI tests. |
| **LangChain Core** | 1.6 | `agent.py` | `.with_structured_output()` — the only feature really used, plus message types. |
| **langchain-google-genai** | 4.4 | `agent.py` | Gemini. Default provider. |
| **langchain-groq** | 1.1 | `agent.py` | Groq. Hosted fallback. |
| **langchain-ollama** | 1.1 | `agent.py` | Local models. No key, no quota. |
| **Pydantic** | 2.13 | `agent.py` | The schemas the model fills in. No calorie field — that's the guardrail. |
| **SQLAlchemy** | 2.0 | `db.py` | ORM over SQLite, four tables. |
| **requests** | 2.34 | `usda.py` | Plain HTTP to USDA. No SDK needed. |
| **python-dotenv** | 1.2 | `agent.py`, `db.py` | Loads `.env` so keys stay out of the shell. |
| **pytest** | 9.1 | `tests/` | 35 tests, ~1s, no network. |

Nine packages. That's the whole dependency list.

**How it runs:**

    browser <--WebSocket--> Streamlit (Python, local)
                              |-> Gemini / Groq (HTTPS)  or  Ollama (localhost:11434)
                              |-> USDA API      (HTTPS)
                              `-> macrowize.db  (local file)

Everything is server-side Python. The browser only receives a UI description — keys,
the database and the CSV never leave the machine.

**Deliberately not used** (be ready for "why didn't you use X"):

- **LangGraph** — had it, removed it. See the Q&A.
- **Postgres** — single user, single file. Infrastructure with no benefit here.
- **A separate frontend** — React would be a week of work for the same three screens.
- **Vector DB / RAG** — nothing to retrieve. Food lookup is exact-match, not semantic.
- **Alembic** — no migrations yet. A listed gap, not a claim.

---

## 5. The flow (know this cold)

    "3 rotis and a bowl of rajma"
        │
        ├─ agent.classify()      → "log_meal"          [LLM call 1]
        │
        ├─ agent.parse_meal()    → [{roti,3,piece},    [LLM call 2]
        │                           {rajma,1,bowl}]     ← no kcal field exists
        │
        ├─ nutrition.resolve_all()
        │     roti → CSV: 40g per piece → 3 × 40 = 120g
        │            120g × 264 kcal/100g = 317 kcal
        │
        ├─ st.session_state.pending = {...}   ← shown on screen, NOT in the DB
        │
        └─ [user clicks Save meal]
              └─ db.save_meal()

If you only memorise one thing, memorise this. Most questions are answered by
pointing at a step in it.

**Say "nothing is in the database yet" out loud** when you reach the pending step.
That's the sentence they're listening for.

**Model calls per message type:**

| you type | calls | why |
|---|:-:|---|
| a meal | **2** | classify, then extract |
| "how much protein left?" | **1** | classify only — the answer is a SQL aggregation |
| anything off-topic | **1** | classify only — the reply is a fixed string |
| profile form (no chat) | **0** | Mifflin-St Jeor is plain Python |

*Careful with the second row — "zero" is wrong. The router still runs.*

**The three routes after `classify()`:**

    log_meal        → parse_meal → resolve_all → pending → [Save] → DB
    query_progress  → describe_progress()  — SUM() over today, no model
    other           → a fixed string, deliberately not an LLM reply

**Where a number comes from, in order:**

    indian_foods.csv   roti, dal, sabzi — 130 foods, 278 keys with aliases
         ↓ miss
    food_cache table   a USDA result already fetched once
         ↓ miss
    USDA API           strawberries, broccoli — then cached
         ↓ miss
    unresolved         reported, contributes zero. Never a guess.

**Where state lives:**

| tier | holds | survives rerun? | survives reload? |
|---|---|:-:|:-:|
| local variables | everything transient | ✗ | ✗ |
| `st.session_state` | pending meal, profile + history copies | ✓ | ✗ |
| SQLite | profile, meals, transcript, food cache | ✓ | ✓ |

The pending meal is the only session-state value with no database backing. That's
deliberate — see Q14.

---

## 6. Decisions you should be able to defend

**Why protein is a range, not a number.** The evidence is a range (cut 1.8–2.4 g/kg,
bulk 1.6–2.2, maintain 1.6–2.0). Storing one figure would invent precision that
isn't in the source.

**Why calories are floored at BMR.** `TDEE − 500` on a light, sedentary person can
land below what their body burns at rest. The target is clamped up to BMR and the
app *tells* you it clamped, rather than silently handing you an unsafe number.

**Why the model is asked twice instead of once.** Classification and extraction are
different jobs. A combined prompt would have to decide *and* extract, and when it
guesses wrong it produces items for a message that wasn't about food. Two small
calls are more reliable and each is separately testable.

**Why USDA's first result isn't trusted.** Searching `salmon` returns *Fish oil,
salmon* at 902 kcal/100g. `avocado` returns *Oil, avocado* at 884. So candidates
are re-ranked on one rule: **the query must match the head of the description** —
the part before the first comma. *Strawberries, raw* → head "Strawberries" ✓.
*Oil, avocado* → head "Oil" ✗. Plus penalties for brand padding (*SILK Strawberry
soy yogurt*) and wrong species (*Sea cucumber*).

**Why timestamps are local, not UTC.** A daily tally has to match *your* calendar
day. Under UTC a 00:30 snack counts against yesterday.

**Why query helpers return dicts, not ORM objects.** A SQLAlchemy row raises if you
read it after its session closes. That bug shipped three times before the rule
became absolute. It's a good "what did you learn" answer.

---

## 7. Q&A

**Q: Is this really a GenAI project if the model doesn't produce the numbers?**
*(the best question you can get — the answer is the whole project)*
Yes, and the constraint is the point. The model does two jobs on every message:
intent classification, and structured entity extraction via constrained decoding
against a Pydantic schema — free-text sentence into typed objects. That's two LLM
calls per logged meal, plus prompt engineering, a multi-provider abstraction
including local open weights through Ollama, and handling of real failure modes:
rate limits, output-parsing failures, a small model returning `quantity: 0` for
nonsense input.

The framing I'd lead with: *this isn't an app with less AI in it — it's an app that
uses AI for the part AI is reliable at, understanding language, and refuses to use
it for the part it isn't, arithmetic.* Wrapping a chat model and printing whatever
it says is the easy version, and it gives you an app that confidently says a roti
is 300 calories. Deciding **where the boundary goes** is the actual engineering.

If compared to something like a weather-API agent: that classifies intent and calls
an API. This does that too — plus constrained structured extraction, a
hallucination guardrail that's structural rather than advisory, human-in-the-loop
verification of model output, and provider fallback. Strictly more GenAI surface,
not less.

**Q: Why not just ask the LLM for the calories? It'd be way less code.**
It would, and it'd be wrong in ways you can't detect. The model gives a confident
number every time, including when it's off by 200 kcal, and there's no signal
telling you which. The whole app is built to make that impossible — the extraction
schema has no calorie field.

**Q: So where do the numbers come from?**
Three sources in order: a local CSV of ~130 Indian foods (278 lookup keys including
aliases like *chapati* → *roti*), then a cache of previous USDA results, then the
USDA FoodData Central API. If all three miss, the food is reported as unresolved
and contributes zero — never a guess.

**Q: Where did the CSV come from?** *(answer honestly)*
I seeded it by hand. It's not IFCT or any audited dataset. Where it overlaps with
USDA I spot-checked ten foods and six were within 3% — banana, guava, egg, curd,
peanuts, milk. But the Indian dishes, which are most of the file, are estimates and
unverified. The honest framing is "hand-seeded, partially spot-checked". If this
were going further, keying in IFCT 2017 values is the fix.

**Q: Is this actually an "agent"?**
It's an LLM-driven router plus a tool-using flow: the model classifies intent, then
a structured-output call extracts entities, then deterministic tools (a lookup
table, an API, a calculator) produce the result. There's no autonomous multi-step
planning loop — that would be wrong for this problem, where every path is known and
you want the same answer every time.

**Q: Didn't you use LangGraph? Why is it gone?** *(good question to get)*
I built it on LangGraph first. When I looked at what it was actually doing, the
routing was an `if/elif` wrapped in graph ceremony, and the checkpointer was holding
a second copy of the pending meal that Streamlit's session state already held. Two
state stores kept in sync by hand is a bug waiting to happen, not a safety feature.
Removing it cut 547 lines across 6 files down to about 80 in one, and the
confirm-before-save guarantee got *easier* to verify, not harder.

**Q: Then how does confirm-before-save work without a state machine?**
The parsed meal goes into `st.session_state.pending` and is rendered. `db.save_meal`
is called from exactly one place — inside the Save button handler. So it's
unreachable without a click, and that's one line to verify instead of a graph to
trace. Three tests assert the database is empty while the confirmation is on screen.

**Q: What happens when the API fails?**
Free tiers rate-limit constantly — Gemini's is 20 requests/day *per model*. Failures
are caught and shown as a chat message rather than replacing the page with a
traceback. There's a test that raises a fake 429 and asserts the app survives. And
there are three providers behind one env var, so Ollama runs it locally with no key
and no quota at all.

**Q: Why SQLite?**
Single user, single file, zero setup. Postgres would be infrastructure with no
benefit here. The tradeoff is it's single-user by design — there's no `user_id`
anywhere, so multi-user would mean touching every table.

**Q: How do you handle "a bowl"? That's not a unit.**
A gram-weight table, not the model — it doesn't reliably know that a roti is 40g.
Order: a real mass (`200g`) is used directly; else that food's own weight from the
CSV (roti 40g/piece, dal 150g/bowl); else any weight that food has; else 100g.
Synonyms collapse first — *katori* is a bowl, *plate* is a serving.

**Q: What's tested, and why only 35 tests?**
Each test maps to a claim worth defending: the maths with exact expected values,
the confirm gate, macros coming from the CSV, and the USDA ranking rules. I
deliberately don't test Streamlit widgets or SQLAlchemy CRUD — that's testing the
frameworks. The UI tests run the real `app.py` through Streamlit's `AppTest` with
the model stubbed, so they're fast and need no API key.

**Q: What's the weakest part?**
The CSV's provenance. Everything else has a defensible source; the Indian food
values are my estimates. Second is that there are no migrations — new tables get
created automatically, but altering one means dropping the database.

**Q: What would you do next?**
IFCT values for the CSV; Alembic for migrations; a weight-over-time chart, since the
profile already stores weight but only uses today's.

---

### Harder ones

**Q: Trace "2 rotis and a bowl of dal" to the database. Name the functions.**
`handle_input()` runs. `remember()` saves my turn to `chat_message` first, so a
reload doesn't lose it. Then `agent.classify()` — one LLM call, returns
`"log_meal"`. Then `agent.parse_meal()` — second call, returns two `FoodItem`
objects. That's the model done; it never sees a number. `nutrition.resolve_all()`
checks the CSV, then the cache, then USDA. Roti is in the CSV at 40g per piece, so
2 pieces is 80g, and 80g at 264 kcal/100g is 211 kcal. The result goes into
`st.session_state.pending` and renders as a confirmation card. **Nothing is in the
database yet.** Only when I click Save does `commit_meal()` run — it redoes the
lookup in case I edited a quantity, then calls `db.save_meal()`.

**Q: How many model calls when I ask "how much protein do I have left today?"**
One — just the router, which has to decide what kind of message it is. After that,
zero. `describe_progress()` sums today's kcal and protein in SQL and compares them
to the stored targets. There's a test that stubs the extractor and asserts it was
never called.

**Q: Your CSV says a roti is 40g. Where did that come from, and why isn't the model
deciding it?**
It's a column in the CSV. The model reports the *unit* you said — "piece", "bowl" —
and never a gram weight, because it doesn't reliably know them. Converting "a bowl"
to grams is a lookup, not a judgement. There's a fallback chain: a real mass like
"200g" is used directly, else the food's own weight, else any weight that food has,
else 100g. As for the 40g itself — I seeded the CSV by hand. Not audited.

**Q: What would break if you added a `kcal` field to `FoodItem`?**
Everything the project rests on. LangChain turns the Pydantic class into a tool
definition and the model fills in every required field — so it would start
generating calorie numbers, and those would flow into the confirmation card and
then the database. The guarantee isn't a prompt asking it not to; it's that there's
no field to put one in. **The safety mechanism is the absence.**

**Q: Why does `handle_input()` end with `st.rerun()`?**
Streamlit only paints the screen after a run finishes, and `handle_input()` runs
near the bottom of the script — by then the chat history above has already
rendered. Without the rerun the state would be correct but the screen stale: you'd
type a message and see nothing until your next interaction. `st.rerun()` abandons
the current run and re-executes from the top immediately.

**Q: Why is `meal_log.items` a JSON column instead of a child table?**
Items are only ever read as a group — I never query across meals. A child table
would mean a join on every read for no query benefit, and the daily tally doesn't
even touch `items` because the totals are denormalized onto the row. The tradeoff
is real: I can't efficiently query by individual food. If that became a feature, a
child table would be the right change.

**Q: I reload the page mid-confirmation. What's lost?**
The pending meal and its summary, because they live in session state. Everything in
SQLite survives — profile, saved meals, transcript. It's the right tradeoff because
nothing was saved, so nothing is inconsistent; you just retype it. The alternative
is worse: persisting the summary would reload you into a breakdown with a Save
button that does nothing, because the meal behind it is gone.

**Q: Why does it matter that `tdee.py` imports nothing but the standard library?**
It means the maths is pure functions — no database, no network, no model — so the
tests assert exact values with no mocking and no flakiness. 75kg, 178cm, 28,
moderate, cut is exactly 2178 kcal every run. That matters because this module
decides the daily target: it's the part most worth being certain about, and
deliberately the easiest to be certain about.

**Q: What does Pydantic actually do here?**
Two jobs. It defines what the model is allowed to return — the class becomes JSON
Schema, which becomes the tool definition the model fills in, and the response is
validated before my code sees it. And it catches bad output: `quantity: float =
Field(gt=0)` is enforced, which is how a small Ollama model returning
`quantity: 0` for nonsense got rejected instead of reaching the UI. The router's
`Literal["log_meal", "query_progress", "other"]` means it can't invent a fourth
route either.

**Q: "This is just a chatbot with a database. Where's the actual AI engineering?"**
*(the one to have word-perfect)*
The AI engineering is in the constraints, not the call. Constrained decoding — the
model fills a JSON Schema generated from a Pydantic class, not free text. Schema
design as a safety mechanism — `FoodItem` has no calorie field, so hallucinated
macros are structurally impossible rather than discouraged. Handling real failure
modes — a 3B model returned `quantity: 0` and Pydantic rejected it before the UI.
Human-in-the-loop verification, because extraction misreads quantities and I never
auto-commit. And a provider abstraction across hosted and local open-weight models
behind one env var.

The hardest decision was knowing where **not** to use the model. Asking it for
calories would have been five lines and would have produced an app that confidently
tells you a roti is 300 calories. Anyone can call an API — deciding where the
boundary goes is the engineering.

---

## 8. If pushed, admit these

Don't get caught defending something indefensible — concede and move on:

- **The CSV values are estimates.** Spot-checked where possible, not audited.
- **Tofu is wrong** — 76 kcal is silken, firm is ~145. Known, not yet fixed.
- **No migrations.** Schema changes mean dropping the DB.
- **Single-user by design.** No `user_id`, and retrofitting it means every table.
- **A pending confirmation doesn't survive a page reload.** It's in session state.
  Nothing is lost but the parse — the meal was never saved.
- **The prompts aren't unit-tested.** Can't be meaningfully asserted on; checked by
  hand instead.

---

## 9. Quick reference

```bash
# run
./.venv/bin/streamlit run app.py

# tests
./.venv/bin/python -m pytest -q          # 35 tests, ~1s, no network

# providers
MACROWIZE_LLM_PROVIDER=google            # default, needs GEMINI_API_KEY
MACROWIZE_LLM_PROVIDER=groq              # needs GROQ_API_KEY
MACROWIZE_LLM_PROVIDER=ollama            # local, no key; needs `ollama serve`
MACROWIZE_LLM_MODEL=gemini-3.5-flash     # override the model
```

Keys live in `.env` (gitignored). `GEMINI_API_KEY` and optionally `USDA_API_KEY` —
without the latter it falls back to a shared demo key that rate-limits fast.

**Numbers to have ready:** ~1,260 source lines across 6 modules · 35 tests ·
130 foods and 278 lookup keys in the CSV · 2 LLM calls per logged meal, 0 for a
progress query.
