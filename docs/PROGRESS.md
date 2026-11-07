# Progress Log

A phase-by-phase record of what was built, how to verify it, and what was learned.

---

## Phase 0: Plan and environment check (2026-11-07)

**Done**
- Checked the environment: Windows 11 Pro, Python 3.12.10, pip 25.0.1, git 2.51.0.
  Hardware: 24 GB RAM, Intel i5-1135G7, integrated GPU (local models would be slow).
- Explained the pipeline and the difference between a Claude subscription and API access.
- **Decision:** LLM provider = **Google Gemini** (free tier). The code stays provider-agnostic.
- **Decision:** the project root is this folder (`D:\Projects\Natural Language SQL Assistant`).

**Learned**
- A chat subscription is for a person. An API key is for a program, and it is billed separately.

---

## Phase 1: Project setup (2026-11-07)

**Done**
- Created a virtual environment in `.venv/`.
- Pinned package versions in `requirements.txt` (checked on PyPI on 2026-11-07).
  Chose `google-genai` (Google's current SDK), not the deprecated `google-generativeai`.
- Created `.gitignore` (excludes `.env`, `.venv`, caches, `*.db`, logs), `.env.example`,
  `src/__init__.py`, `src/config.py` and `scripts/check_setup.py`.
- Initialised git and made the first commit.

**Files created**
`requirements.txt`, `.gitignore`, `.env.example`, `src/__init__.py`, `src/config.py`,
`scripts/check_setup.py`, `README.md`, `docs/PROGRESS.md`

**Commands used**
| Command | What it does |
|---|---|
| `python -m venv .venv` | Creates a private Python environment in `.venv` |
| `.\.venv\Scripts\Activate.ps1` | Activates it, so `python`/`pip` now mean the venv's copies |
| `pip install -r requirements.txt` | Installs every package listed in the file |
| `git init` | Turns the folder into a git repository (creates a hidden `.git` folder) |
| `git add .` | Stages all files (except ignored ones) for the next commit |
| `git commit -m "..."` | Saves a snapshot of the staged files with a message |

**How to verify**
`python scripts/check_setup.py` should list every package as `OK` and end with `RESULT: setup looks good.`

**Learned**
- A venv isolates project packages. `requirements.txt` lets anyone recreate the environment.
- Secrets go in `.env` (ignored by git). `.env.example` documents the settings without values.
- Settings are loaded once, in `config.py`, and they are read-only (`frozen=True`).

**Afterwards**
- Connected the GitHub repo https://github.com/Shruthinanditha/nl2sql-assistant
  (`git remote add origin ...`, `git push -u origin main`). Before pushing, scanned the commit for key patterns: none found.
- Created a Gemini API key in Google AI Studio and saved it in `.env`. Verified it by listing models (26 visible).

---

## Phase 2: Sample database + DBeaver (2026-11-07)

**Done**
- `data/seed_database.py` builds `data/shop.db` (SQLite) with a fixed random seed (42):
  customers 200, products 40, orders 3200, order_items 5619, users_private 10.
- Orders span ~18 months ending today, with growth over time and a festive peak in Oct–Nov.
  Each `orders.total` equals the sum of its items, and no order is dated before its customer's signup.
- Messy realities: ~10% cancelled orders, pending orders only in the last 10 days, 23 customers with
  no orders, 5 NULL cities, 3 NULL emails, discounted `unit_price` on ~15% of lines.
- `users_private` (password hashes, salaries) is a sensitive table, used later to demonstrate the allowlist.
- `docs/DBEAVER_GUIDE.md`: connecting, browsing, the SQL editor, the ER diagram, CSV export, verifying AI SQL,
  and 10 exercises with answers (all answers tested against the database).

**Bug found and fixed during the phase**
- The first version picked the customer first and then a date after their signup. That pushed orders into recent
  months (15 orders in the first month vs 593 in Oct 2026). Fixed by picking the **date first**, following the
  growth/season curve, then a customer who had signed up by then.

**Files created**
`data/seed_database.py`, `docs/DBEAVER_GUIDE.md` (the `.db` file is git-ignored and rebuilt from the script)

**How to verify**
1. `python data/seed_database.py` prints the five row counts above.
2. In DBeaver, run the sanity query in section 10 of the guide. The counts must match.

**Learned**
- Commit the *script* that creates data, not the binary data file. It's reproducible and keeps the repo small.
- Relative dates keep "last month" questions meaningful whenever the demo runs.
- Always verify generated data (consistency checks, distribution by month) instead of trusting the generator.

---

## Phase 3: LLM client (2026-11-07)

**Done**
- `src/llm_client.py`: one interface, `complete(system_prompt, user_prompt) -> str`, with adapters for
  **Gemini** (`google-genai` SDK), **Anthropic** (`anthropic` SDK, not installed or tested yet), **Ollama** (plain HTTP)
  and **FakeLLM** (canned answers for tests). `get_llm_client()` picks the adapter from `.env`.
- Temperature 0 (deterministic SQL). Automatic retries (3 attempts, 2 s → 4 s backoff) for 429/500/502/503.
  504 timeouts are not retried, because each one already waited the full timeout.
- Every error becomes an `LLMError` with plain-English advice. Token usage is recorded per call.
- `scripts/hello_llm.py` (connection check), `tests/test_llm_client.py` (12 offline tests), `pytest.ini`.

**Model selection: tested live on the free tier, 2026-11-07**
| Model | Result | Latency |
|---|---|---|
| gemini-3.5-flash-lite | ✅ 3/3 | 1.0–1.6 s |
| gemini-3.5-flash | ✅ 2/3 (one 503) | 7.7–17.7 s (a "thinking" model: 800–1,200 hidden tokens) |
| gemini-3.1-flash-lite | ✅ 1/1 | 12.5 s |
| gemini-3.6 / 3.7 / 3.8-flash | ❌ 503 overloaded / 504 timeouts | – |
| gemini-2.5-flash(-lite) | ❌ 404, retired for new users | – |

→ **Default: `gemini-3.5-flash-lite`**. We'll compare it with 3.5-flash in Phase 8.
Observed: even when told "return only SQL", the model wrapped its answer in ```` ```sql ```` fences, so Phase 4 must strip them.

**Files created**
`src/llm_client.py`, `scripts/hello_llm.py`, `tests/test_llm_client.py`, `pytest.ini`

**How to verify**
- `pytest -v`: 12 passed (no network needed).
- `python scripts/hello_llm.py`: a one-sentence hello and `RESULT: the LLM connection works.`
- Error handling, tested live: a wrong model name gives a 404 with advice; a bad key gives 400 "API key was rejected".

**Learned**
- Tokens, temperature, and system vs user prompts.
- The adapter pattern: the app depends on one small interface, not on a vendor SDK.
- Model availability changes fast. Check live instead of trusting tutorials (2.5 was already retired).
- Free tiers have overload (503) and rate limits (429), so retries with backoff matter.

---

## Phase 4: Schema + prompt engineering (2026-11-07)

**Done**
- `data/schema_descriptions.yaml`: descriptions, business rules (revenue = completed orders only, INR,
  `unit_price` vs `price`), and the **allowlist**. `users_private` is not listed (sensitive table), and
  `customers.email` is not listed (personal data, a column-level allowlist).
- `src/schema.py`: reads the real structure with `PRAGMA table_info` / `PRAGMA foreign_key_list` (read-only
  connection), merges the YAML, keeps only allowlisted tables/columns, reads real distinct values for
  `show_values` columns, and formats everything as commented `CREATE TABLE` text. YAML typos raise an error.
- `src/prompts.py`: a system prompt with role, 9 rules, today's date injected (with exact meanings of "last month",
  "this year", ...), the schema, and 7 few-shot examples (5 SQL + 2 `CANNOT_ANSWER`). The question goes
  in `<question>` tags.
- `src/sql_generator.py`: calls the LLM, strips markdown fences and chatter (but never cuts the SQL itself),
  detects `CANNOT_ANSWER`, and rejects empty or too-long questions without spending tokens.
- `scripts/try_questions.py`, `scripts/check_secrets.py`, `tests/test_schema.py`, `tests/test_sql_generator.py`.

**Results (gemini-3.5-flash-lite, live)**
| Question | Result | Time |
|---|---|---|
| Last month's top 5 customers by spending | correct, **identical** to DBeaver exercise 7 | 1.9 s |
| Revenue per month this year | correct, **identical** to DBeaver exercise 6 | 1.2 s |
| Orders in each status | correct | 1.5 s |
| Category with most units, last 90 days | correct | 1.3 s |
| Emails of customers in Chennai | correctly refused (`CANNOT_ANSWER`, email is hidden) | 1.0 s |

Prompt size: about 1,900 input tokens per question, which is cheap and well within free-tier limits.

**How to verify**
- `pytest -v`: 32 passed.
- `python scripts/try_questions.py` prints the SQL for 5 questions. Paste them into DBeaver and compare.

**Learned**
- The LLM only knows what the prompt tells it, so the schema description quality drives the SQL quality.
- Allowlist > blocklist: list what is allowed, and everything else is invisible by default.
- Injecting today's date makes relative dates deterministic and testable.
- Few-shot examples must not overlap with evaluation questions (avoid testing on training data).
- Cleaning LLM output must never remove anything the validator needs to see.

---

## Phase 5: Safety validator (2026-11-07)

**Done**
- `src/sql_validator.py`: parses SQL into an AST with sqlglot and checks size, single statement, query-only root,
  no forbidden nodes anywhere, table allowlist (CTEs handled, no `db.table`, no table-valued functions,
  no recursive CTEs), function allowlist, and column allowlist (via sqlglot `qualify`, which also expands `SELECT *`
  to allowed columns). It enforces LIMIT ≤ `MAX_ROWS` and returns `ValidationResult(is_safe, reason, failed_check,
  clean_sql, tables)`.
- The executed SQL is **regenerated from the checked tree** ("execute what you validated").
- `tests/test_validator.py`: 18 valid queries (each must return **identical rows** after regeneration),
  42 attacks, 6 LIMIT cases, 1 table-list test.
- `docs/SECURITY.md`: threat model, the 6 defence layers, the attack catalogue, honest limitations.

**Bug found by the tests**
- The first version only allowlisted *unknown* functions and trusted the ones sqlglot recognises.
  Test A32 showed `sqlite_version()` slipping through, because sqlglot knows it. Fix: check the real function name
  sent to SQLite for **every** function call. Added tests A32b/A32c.

**How to verify**
- `pytest -v`: 100 passed (`pytest tests/test_validator.py -v` lists each attack by name).
- The 4 real Gemini queries from Phase 4 pass the validator and return identical rows.

**Learned**
- LLM output is untrusted input. Defence in depth: no single layer is enough.
- AST parsing beats regex/keyword filters. Allowlists beat blocklists. Fail closed when unsure.
- Execute exactly what you validated (regenerate the SQL from the tree).
- Tests find real security gaps. Assumptions about libraries must be verified.

---

## Phase 6: Read-only executor + pipeline (2026-11-07)

**Done**
- `src/executor.py`: read-only connection (URL-encoded `file:...?mode=ro` URI), a **SQLite authorizer** (allows only
  SELECT, allowlisted table/column reads and allowlisted functions), a timeout via the progress handler, and a row cap
  via `fetchmany(max_rows + 1)` (detects truncation). Returns `ExecutionResult` with a DataFrame and timing.
- `src/pipeline.py`: `ask(question)` → generate → validate → execute → `PipelineResult` (status `success` /
  `cannot_answer` / `blocked` / `error`, message, generated and executed SQL, DataFrame, timings, tokens).
  **One** self-repair for honest mistakes only (parse error, unknown column, runtime error). Security
  rejections and timeouts are never repaired. Every question is appended as JSON to `logs/queries.log`.
- `src/prompts.py`: `build_repair_prompt`. `src/sql_generator.py`: `repair_sql`.
- `scripts/ask.py`: the full pipeline from the terminal.
- `tests/test_executor.py` (37 tests) and `tests/test_pipeline.py` (16 tests).

**Security finding**
- On a test copy, **read-only mode did not block `ATTACH DATABASE`. It created a new file on disk.** It also
  allowed `PRAGMA journal_mode`. Hence the authorizer, which blocks both, plus private tables/columns, functions
  and recursion, independently of sqlglot. Documented in `docs/SECURITY.md`.

**Results (live, gemini-3.5-flash-lite)**
| Question | Status | Total time |
|---|---|---|
| Last month's top 5 customers | success (same 5 customers as DBeaver exercise 7) | 1.53 s |
| Revenue by month this year | success, 11 rows | 1.24 s |
| Orders per status | success, 3 rows | 1.03 s |
| Average delivery time per city | cannot_answer (no delivery data) | 0.96 s |
| Delete all customers | cannot_answer (the LLM refused at layer 1) | 0.96 s |
| Prompt injection asking for `users_private` | cannot_answer (the LLM refused at layer 1) | 0.98 s |

Validation takes 2–17 ms and execution 4–10 ms. Almost all the time is the LLM call.

**How to verify**
- `pytest`: 153 passed.
- `python scripts/ask.py "your question"`

**Learned**
- Test every layer **alone**. Read-only mode alone has gaps (ATTACH), so two independent allowlists are better.
- Self-repair improves robustness, but it must never help an attacker, and it must not loop.
- Measure where time goes: the LLM dominates, and the security checks are almost free.
