"""
check_secrets.py - makes sure no API key is about to be committed or pushed.

WHY THIS FILE EXISTS
    Leaked API keys are one of the most common (and expensive) mistakes.
    This script checks:
      1. the files staged for commit, for patterns that look like API keys;
      2. all tracked files AND the whole git history for the EXACT keys in
         your .env file (catches new key formats that patterns would miss).
    It never prints a key.

HOW TO RUN (before every commit/push)
    python scripts/check_secrets.py
    Exit code 0 = clean, 1 = possible secret found (do NOT commit/push).
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402

# Patterns that look like real keys. "{20,}" requires a long value, so help
# text such as "GEMINI_API_KEY=..." does not trigger a false alarm.
KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                                # classic Google keys
    re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}"),                             # Anthropic keys
    re.compile(r"(GEMINI|ANTHROPIC)_API_KEY\s*=\s*[A-Za-z0-9_.\-]{20,}"),  # key=value in files
]


def git_output(*args: str) -> str:
    """Run a git command and return its text output."""
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout


def main() -> None:
    """Run both checks and exit with 0 (clean) or 1 (problem)."""
    problems: list[str] = []

    # 1. Pattern scan of tracked + staged files.
    for file_name in git_output("ls-files").split("\n"):
        path = Path(file_name)
        if not file_name or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in KEY_PATTERNS:
            if pattern.search(text):
                problems.append(f"{file_name}: text that looks like an API key ({pattern.pattern[:20]}...)")

    # 2. Exact-value scan for the keys in .env (files + full history).
    history = git_output("log", "-p", "--all")
    for label, key in [("Gemini key", settings.gemini_api_key), ("Anthropic key", settings.anthropic_api_key)]:
        if not key:
            continue
        for file_name in git_output("ls-files").split("\n"):
            path = Path(file_name)
            if file_name and path.is_file() and key in path.read_text(encoding="utf-8", errors="ignore"):
                problems.append(f"{file_name}: contains your real {label}!")
        if key in history:
            problems.append(f"git history contains your real {label}! (rotate the key and rewrite history)")

    if problems:
        print("POSSIBLE SECRETS FOUND - do not commit/push:")
        for problem in problems:
            print("  -", problem)
        sys.exit(1)
    print("Secret check: clean (no key patterns, real keys not in files or history).")


if __name__ == "__main__":
    main()
