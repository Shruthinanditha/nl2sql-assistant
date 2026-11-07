"""
hello_llm.py - sends one tiny message to the configured LLM to prove the setup works.

WHY THIS FILE EXISTS
    Before building SQL generation on top of the LLM, we check the basics:
    is the API key valid, is the model name right, does the network work?
    This costs only a handful of tokens.

HOW TO RUN (from the project root, venv activated)
    python scripts/hello_llm.py
"""

import sys
import time
from pathlib import Path

# Make `import src...` work when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm_client import LLMError, get_llm_client  # noqa: E402  (import after sys.path change)


def main() -> None:
    """Create the configured client, send a greeting, and print the reply + stats."""
    try:
        client = get_llm_client()
        print(f"Provider: {client.provider_name}   Model: {client.model}")

        start = time.perf_counter()
        reply = client.complete(
            system_prompt="You are a friendly assistant. Answer in one short sentence.",
            user_prompt="Say hello in one sentence.",
        )
        seconds = time.perf_counter() - start
    except LLMError as error:
        # Friendly message, no stack trace, non-zero exit code (signals failure).
        print(f"FAILED: {error}")
        sys.exit(1)

    print(f"Reply   : {reply.strip()}")
    print(f"Time    : {seconds:.2f} s")
    print(f"Tokens  : {client.last_token_usage}")
    print("RESULT: the LLM connection works.")


if __name__ == "__main__":
    main()
