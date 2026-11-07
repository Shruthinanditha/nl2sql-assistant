# Natural-Language SQL Assistant

> Ask questions about a database in plain English: an LLM writes the SQL, layered safety checks verify it, and the app shows the SQL, the results table and a chart.

🚧 **Status: under construction.** Phase 1 of 10 is complete (project setup). This README grows with each phase.

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
```

If activation fails with "running scripts is disabled on this system", run this once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Configuration (`.env`)

| Variable | Meaning | Default |
|---|---|---|
| `LLM_PROVIDER` | `gemini`, `anthropic`, `ollama` or `fake` | `gemini` |
| `LLM_MODEL` | Model name for the provider | set in Phase 3 |
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
├── src/
│   ├── __init__.py
│   └── config.py         # loads settings from .env
├── scripts/
│   └── check_setup.py    # verifies the installation
└── docs/
    └── PROGRESS.md       # build log, phase by phase
```

## WHAT YOU SHOULD DO (checklist)

- [x] Install Python, git, VS Code
- [x] Create the virtual environment and install packages (Phase 1)
- [ ] Create a Gemini API key and put it in `.env` (Phase 1/3)
- [ ] Install DBeaver and connect to `data/shop.db` (Phase 2)
- _More items are added as the project grows._

## License

MIT (to be added in Phase 10).
