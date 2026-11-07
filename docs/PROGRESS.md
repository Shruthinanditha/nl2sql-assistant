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
