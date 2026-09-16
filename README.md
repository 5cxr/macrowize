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
  agent.py  ->  [{roti, 2, piece}, {dal, 1, bowl}]     <- LLM, no macros in the schema
        |
        v
nutrition.py  ->  local CSV -> USDA cache -> USDA API   <- every number comes from here
        |
        v
  confirm card  ->  db.py                               <- nothing saves until you confirm
```

## Why

Ask a language model "how many calories in 2 rotis" and it answers confidently,
often wrongly, and you can't tell which times. For a tracking app that's fatal —
you'd log plausible fiction all week.

So the model gets a smaller job: extraction. The Pydantic schema it fills in has
`name`, `quantity` and `unit`, and no field for a calorie, so there is nowhere to
put one even if it tried. Numbers come from a 130-row CSV of Indian food first
(USDA is thin on home cooking), then USDA for the long tail, cached in SQLite.
A food that misses everywhere comes back unresolved rather than invented.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env     # then add GEMINI_API_KEY
streamlit run app.py
```

Needs Python 3.13. First run asks for height, weight, age, sex, activity level
and goal, and computes your targets from those.

## Configuration

All optional except a key for the active provider. `.env` is gitignored.

| var | default | what |
|---|---|---|
| `MACROWIZE_LLM_PROVIDER` | `google` | `google`, `groq` or `ollama` |
| `MACROWIZE_LLM_MODEL` | per provider | overrides the default model |
| `GEMINI_API_KEY` | — | required when provider is `google` ([key](https://aistudio.google.com/apikey)) |
| `GROQ_API_KEY` | — | required when provider is `groq` |
| `USDA_API_KEY` | demo key | [sign up](https://fdc.nal.usda.gov/api-key-signup.html); the shared demo key rate-limits fast |
| `MACROWIZE_DB` | `./macrowize.db` | SQLite file path |

Provider is read once at import, so **restart Streamlit after changing it**.
Ollama runs fully offline — `ollama serve` plus `ollama pull llama3.1:8b`, no key,
no quota.

## What it does

- **Log by chat** — free text in, parsed items out, shown as a confirm card with
  editable quantities. Nothing reaches the database until you press save.
- **Daily dashboard** — calories and protein eaten vs. target, in the sidebar.
- **Targets** — Mifflin-St Jeor BMR, activity multiplier, then a goal delta
  (cut −500, bulk +300) and a protein band in g/kg per goal.
- **Progress questions** — "how much protein left?" answered from the day's rows.
- **History** — multi-day view, and any logged meal can be deleted.
- **Persistent chat** — the transcript survives a reload; the pending confirm
  card does not.

## Layout

Six modules, flat, ~1,260 lines.

| file | lines | what |
|---|---:|---|
| `app.py` | 443 | Streamlit UI — sidebar dashboard, chat, confirm card, history |
| `db.py` | 258 | SQLAlchemy models and every query |
| `nutrition.py` | 180 | the lookup chain, and "a bowl" → grams |
| `usda.py` | 141 | USDA client and result ranking |
| `tdee.py` | 126 | Mifflin-St Jeor and goal targets |
| `agent.py` | 113 | the two LLM calls, and nothing else |

Two boundaries worth naming: `agent.py` is the only file that knows an LLM
exists, and `nutrition.py` is the only file that decides where a number comes
from. That's why swapping Gemini for Ollama is one environment variable.

`indian_foods.csv` holds per-100g macros plus household weights — grams per
piece, bowl and serving — so "a bowl of dal" converts without guessing.

## Tests

```bash
pytest          # 35 tests, no network, no API key
```

The UI tests drive Streamlit headlessly via `AppTest` with the LLM stubbed;
three of them assert the database is still empty while the confirmation card is
on screen.

## Known limits

- Single user — the profile is one row pinned to `id=1`.
- Timestamps are naive local time, so a daily tally lines up with your own
  calendar day rather than UTC.
- A pending confirmation lives in session state and is lost on reload.
- Gemini's free tier is 20 requests/day per model; Groq or Ollama if you hit it.

More detail: `macro-chatbot-design.md` for the architecture, `NOTES.md` for the
reasoning behind each decision.
