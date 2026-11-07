"""
config.py - loads all settings for the app from the `.env` file.

WHY THIS FILE EXISTS
    Settings (which LLM to use, API keys, database path, safety limits) should
    never be hard-coded inside the code. Hard-coded keys end up on GitHub, and
    hard-coded limits are hard to change. Instead, every setting lives in `.env`
    (private, never committed), and this one module reads them.
    Every other module simply does:  `from src.config import settings`.

HOW IT FITS IN THE PIPELINE
    It is the foundation: the LLM client reads the provider/model/key from here,
    the executor reads the database path, row limit and timeout from here.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# The project root is the folder that contains this `src` folder.
# We compute it from this file's location so paths work no matter which
# folder you start Python from.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Read the .env file and put its values into environment variables.
# override=False means a real environment variable (for example one set by
# Streamlit Cloud in Phase 10) wins over the value in the .env file.
load_dotenv(PROJECT_ROOT / ".env", override=False)

# The providers our llm_client.py will support (built in Phase 3).
VALID_PROVIDERS = ("gemini", "anthropic", "ollama", "fake")


def _get_int(name: str, default: int) -> int:
    """
    Read an environment variable and convert it to an integer.

    Inputs:  name    - the variable name, e.g. "MAX_ROWS"
             default - the value used if the variable is missing or empty
    Output:  the integer value
    Raises:  ValueError with a friendly message if the value is not a number
             (better to fail loudly at startup than misbehave later).
    """
    raw_value = os.getenv(name, "").strip()
    if raw_value == "":
        return default
    try:
        return int(raw_value)
    except ValueError:
        raise ValueError(
            f"Setting {name} in .env must be a whole number, but it is '{raw_value}'."
        )


def _resolve_path(raw_path: str) -> Path:
    """
    Turn a path from .env (like "data/shop.db") into an absolute path.

    Relative paths are treated as relative to the project root, so the app
    finds the database even if you run it from another folder.
    """
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


@dataclass(frozen=True)
class Settings:
    """
    All app settings in one object.

    `frozen=True` makes it read-only after creation: no code can accidentally
    change a safety limit (like max_rows) while the app is running.
    """

    llm_provider: str
    llm_model: str
    gemini_api_key: str
    anthropic_api_key: str
    ollama_host: str
    llm_timeout_seconds: int
    db_path: Path
    max_rows: int
    query_timeout_seconds: int
    log_path: Path


def load_settings() -> Settings:
    """
    Build a Settings object from the environment variables.

    Output: a Settings instance.
    Raises: ValueError if LLM_PROVIDER is not one of VALID_PROVIDERS, so a typo
            like "gemni" is caught immediately with a clear message.
    """
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(
            f"LLM_PROVIDER in .env is '{provider}', but it must be one of: "
            f"{', '.join(VALID_PROVIDERS)}."
        )

    return Settings(
        llm_provider=provider,
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        # Keys default to "" (empty). We check for a missing key later, only when
        # we actually call that provider, so tests with the fake LLM need no key.
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434").strip(),
        llm_timeout_seconds=_get_int("LLM_TIMEOUT_SECONDS", 60),
        db_path=_resolve_path(os.getenv("DB_PATH", "data/shop.db")),
        max_rows=_get_int("MAX_ROWS", 500),
        query_timeout_seconds=_get_int("QUERY_TIMEOUT_SECONDS", 5),
        log_path=_resolve_path(os.getenv("LOG_PATH", "logs/queries.log")),
    )


# A single shared settings object, created once when this module is first imported.
settings: Settings = load_settings()


def describe_settings() -> str:
    """
    Return a human-readable summary of the settings, with secrets MASKED.

    Useful for debugging ("is my .env being read?") without ever printing a key.
    """

    def mask(secret: str) -> str:
        # Show only whether a key is set, never its characters.
        return "set (hidden)" if secret else "NOT SET"

    return "\n".join(
        [
            f"LLM provider        : {settings.llm_provider}",
            f"LLM model           : {settings.llm_model or 'NOT SET (chosen in Phase 3)'}",
            f"Gemini API key      : {mask(settings.gemini_api_key)}",
            f"Anthropic API key   : {mask(settings.anthropic_api_key)}",
            f"Database path       : {settings.db_path}",
            f"Max rows per query  : {settings.max_rows}",
            f"Query timeout (sec) : {settings.query_timeout_seconds}",
            f"Log file            : {settings.log_path}",
        ]
    )
