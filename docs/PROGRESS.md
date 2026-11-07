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
