# Natural-Language SQL Assistant

> Ask questions about a database in plain English: an LLM writes the SQL, layered safety checks verify it, and the app shows the SQL, the results table and a chart.

🚧 **Status: under construction.** Phase 8 of 10 is complete: the app works end to end and its accuracy is measured. Docs polish and deployment come next.

<!-- SCREENSHOT: main UI with a chart (added in Phase 10) -->

## Features

- Ask questions in plain English; the AI writes SQLite SQL using only the allowed schema
- Results as a table, an **automatic chart** (line for trends, bar for comparisons, big number for single values) and CSV download
- "Show generated SQL" panel: see exactly what ran
- Clear explanations when a query is **blocked for safety** or **cannot be answered**
- **Self-repair:** a failing query is sent back to the AI once with the error message
- **Safety lab:** type SQL yourself and watch the validator block attacks
- Question history, audit log, 190 automated tests (offline, using a fake LLM)
- **Measured accuracy:** 96.7% average on a 40-question evaluation set (baseline 92.5%), 100% on held-out questions, 100% of unsafe questions refused

## Evaluation results

Execution accuracy (result rows compared with hand-checked reference SQL), `gemini-3.5-flash-lite`:

| Setup | 40-question set | 8 held-out questions | Unsafe refused |
|---|---|---|---|
| Baseline | 92.5% | 87.5% | 100% |
| Final (clearer business rule + 2 general prompt rules) | **96.7%** (mean of 95.0 / 97.5 / 97.5) | **100%** | 100% |

Method, every change and its effect, remaining failures and limitations: [docs/EVALUATION.md](docs/EVALUATION.md).

```powershell
python eval/run_eval.py --dry-run --label dryrun          # checks the scorer, no API calls
python eval/run_eval.py --label myrun                     # ~40 requests, ~3 minutes, free tier
``` This README grows with each phase.

## How it works (preview)

```
question -> LLM (+ schema) -> SQL -> safety checks -> read-only DB -> rows -> table / chart
```

## Tech stack

Python 3.12 · Streamlit · pandas · Plotly · sqlglot · SQLite · Google Gemini API (provider-agnostic: Anthropic / Ollama also supported) · pytest

## Prerequisites

- Python 3.10+ (developed with 3.12)
- git
- VS Code (recommended)
- A Gemini API key (free tier): https://aistudio.google.com/apikey
- DBeaver Community (to explore the database; used from Phase 2)

## Setup (Windows, PowerShell)

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install packages
pip install -r requirements.txt

# 3. Create your private settings file and put your API key in it
Copy-Item .env.example .env
#    then open .env and fill in GEMINI_API_KEY (never commit .env)

# 4. Check the setup
python scripts/check_setup.py

# 5. Create the sample database (data/shop.db)
python data/seed_database.py

# 6. Check that the LLM connection works (uses a few tokens)
python scripts/hello_llm.py

# 7. Run the automated tests (offline, free)
pytest -v

# 8. See the SQL the AI writes (without running it)
python scripts/try_questions.py "Which city has the most customers?"

# 9. Ask a question end to end (LLM -> validator -> read-only DB -> table)
python scripts/ask.py "Show me last month's top 5 customers"

# 10. Start the web app (opens http://localhost:8501; stop with Ctrl+C)
streamlit run app.py
```

## Example questions to try

| Question | What you should see |
|---|---|
| Show me last month's top 5 customers by spending | bar chart + 5 rows |
| What is the revenue by month this year? | line chart |
| How many orders are there in each status? | bar chart (completed / cancelled / pending) |
| How many products do we sell? | a single big number |
| What is the average delivery time per city? | "I can't answer that" (no such data) |
| Delete all customers | refused (read-only) |
| Safety lab: `SELECT 1; DROP TABLE orders` | 🛡️ blocked: `multiple_statements` |

## How the AI sees the database

`src/schema.py` reads the real table structure from SQLite and merges it with
`data/schema_descriptions.yaml` (meanings, business rules, real example values).
That YAML file is also the **allowlist**: only listed tables/columns are shown to the LLM.
`users_private` (sensitive) and `customers.email` (personal data) are deliberately not listed.
`src/prompts.py` holds the rules, today's date and 7 few-shot examples.

## Security design

SQL written by the LLM is treated as **untrusted input** and passes six layers of defence:
(1) prompt rules, (2) AST validation with sqlglot (one statement, SELECT only, function allowlist, no recursion),
(3) a table/column allowlist, (4) a read-only database connection **plus a SQLite authorizer** that re-checks the
allowlist inside the database engine (read-only mode alone does not block `ATTACH`; we tested it),
(5) a row limit + query timeout, (6) the SQL is shown to the user. The SQL that runs is regenerated from the
validated syntax tree. If a query fails because of an honest mistake, the error is sent back to the LLM **once**
for a self-repair; security rejections are never repaired.
42 attack cases (stacked statements, UNION injection, `sqlite_master`, `ATTACH`, `PRAGMA`, hidden columns,
`load_extension`, recursive CTEs, ...) are tested in `tests/test_validator.py`.
Details: [docs/SECURITY.md](docs/SECURITY.md).

## LLM providers

All LLM calls go through `src/llm_client.py` (`complete(system_prompt, user_prompt) -> str`).
You switch provider by changing `.env`; no code changes are needed.

| `LLM_PROVIDER` | Cost | Notes |
|---|---|---|
| `gemini` (default) | Free tier | Model `gemini-3.5-flash-lite`, chosen by live testing (fast, ~1-2 s) |
| `anthropic` | Pay as you go | `pip install anthropic`; set a spending limit in the Anthropic Console |
| `ollama` | Free, local | Needs Ollama installed; slow without a dedicated GPU |
| `fake` | Free | Canned answers; used by the tests |

## Sample database

An imaginary Indian online shop (amounts in INR), generated by `data/seed_database.py` with a fixed seed.
Dates are relative to the day you run it, so "last month" always has data.

| Table | Rows | Notes |
|---|---|---|
| `customers` | 200 | some have NULL city/email; 23 never ordered |
| `products` | 40 | 6 categories |
| `orders` | 3200 | ~18 months; status `completed` / `cancelled` / `pending` |
| `order_items` | 5619 | `unit_price` = price actually paid |
| `users_private` | 10 | **sensitive**: the assistant must never access it |

To explore it, see [docs/DBEAVER_GUIDE.md](docs/DBEAVER_GUIDE.md) (includes 10 SQL exercises).

If activation fails with "running scripts is disabled on this system", run this once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Configuration (`.env`)

| Variable | Meaning | Default |
|---|---|---|
| `LLM_PROVIDER` | `gemini`, `anthropic`, `ollama` or `fake` | `gemini` |
| `LLM_MODEL` | Model name for the provider (empty = provider default) | `gemini-3.5-flash-lite` |
| `GEMINI_API_KEY` | Your Gemini key (secret) | – |
| `ANTHROPIC_API_KEY` | Claude API key, if using `anthropic` | – |
| `OLLAMA_HOST` | Local Ollama server URL | `http://localhost:11434` |
| `LLM_TIMEOUT_SECONDS` | Max wait for the LLM | `60` |
| `DB_PATH` | SQLite database file | `data/shop.db` |
| `MAX_ROWS` | Max rows a query may return | `500` |
| `QUERY_TIMEOUT_SECONDS` | Max seconds a query may run | `5` |
| `LOG_PATH` | Query log file | `logs/queries.log` |

## Project structure (so far)

```
├── README.md
├── requirements.txt
├── .env.example          # settings template (no secrets)
├── .gitignore
├── pytest.ini            # test runner settings
├── app.py                # Streamlit web UI
├── .streamlit/config.toml  # UI settings (no secrets)
├── src/
│   ├── __init__.py
│   ├── config.py         # loads settings from .env
│   ├── llm_client.py     # provider-agnostic LLM wrapper (+ FakeLLM for tests)
│   ├── schema.py         # reads DB structure + descriptions, applies allowlist
│   ├── prompts.py        # system prompt, rules, few-shot examples
│   ├── sql_generator.py  # question -> SQL (cleans fences, detects CANNOT_ANSWER, repair)
│   ├── sql_validator.py  # SECURITY CORE: AST-based safety checks (sqlglot)
│   ├── executor.py       # read-only DB + authorizer + timeout + row cap
│   ├── pipeline.py       # ask(question): generate -> validate -> execute (+1 repair), audit log
│   └── charts.py         # picks metric / line / bar / table automatically (Plotly)
├── scripts/
│   ├── check_setup.py    # verifies the installation
│   ├── hello_llm.py      # verifies the LLM key/model work
│   ├── try_questions.py  # prints generated SQL for sample questions
│   ├── ask.py            # full pipeline from the terminal
│   └── check_secrets.py  # run before every push: no API keys in the repo
├── tests/
│   ├── test_llm_client.py
│   ├── test_schema.py
│   ├── test_sql_generator.py
│   ├── test_validator.py # 18 valid queries + 42 attacks + LIMIT rules
│   ├── test_executor.py  # DB-level protections, tested WITHOUT the validator
│   ├── test_pipeline.py  # end-to-end with FakeLLM (malicious LLM, self-repair, ...)
│   ├── test_charts.py    # automatic chart choice
│   ├── test_app.py       # UI tests with Streamlit AppTest
│   └── test_eval_scoring.py  # the evaluation's result comparison
├── eval/
│   ├── questions.yaml    # 40 questions + reference SQL
│   ├── holdout.yaml      # 8 held-out questions
│   ├── run_eval.py       # runs the evaluation, saves results
│   └── results/          # every run as JSON
├── data/
│   ├── seed_database.py           # builds data/shop.db (the .db itself is git-ignored)
│   └── schema_descriptions.yaml   # descriptions + business rules + ALLOWLIST
└── docs/
    ├── PROGRESS.md       # build log, phase by phase
    ├── DBEAVER_GUIDE.md  # DBeaver walkthrough + practice exercises
    ├── SECURITY.md       # threat model, defence layers, attack catalogue
    ├── EVALUATION.md     # how accuracy was measured and improved
    └── CODEBASE_GUIDE.docx  # beginner guide: every file and function explained
```

## WHAT YOU SHOULD DO (checklist)

- [x] Install Python, git, VS Code
- [x] Create the virtual environment and install packages (Phase 1)
- [x] Create a Gemini API key and put it in `.env` (Phase 1/3)
- [x] Run `python data/seed_database.py` to create the database (Phase 2)
- [ ] Connect DBeaver to `data/shop.db` and check the row counts (Phase 2)
- [ ] Do the 10 SQL exercises in `docs/DBEAVER_GUIDE.md` (Phase 2)
- [x] Run `python scripts/hello_llm.py` and `pytest -v` (Phase 3)
- [ ] Run `python scripts/try_questions.py` and compare the SQL in DBeaver (Phase 4)
- [ ] Read `docs/SECURITY.md` and be able to explain 5 attacks and the 6 layers (Phase 5)
- [ ] Ask your own questions with `python scripts/ask.py "..."` and look at `logs/queries.log` (Phase 6)
- [ ] Run `streamlit run app.py` and try every example question and the Safety lab (Phase 7)
- [ ] Read `docs/EVALUATION.md` and be able to explain the baseline, each change and the held-out set (Phase 8)
- [ ] Read `docs/CODEBASE_GUIDE.docx` in the recommended order
- _More items are added as the project grows._

## License

MIT (to be added in Phase 10).
