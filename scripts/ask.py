"""
ask.py - ask the assistant a question from the terminal (the full pipeline).

WHY THIS FILE EXISTS
    Lets you try the complete pipeline (LLM -> validator -> read-only DB)
    before the web UI exists, and later for quick debugging.

HOW TO RUN (venv activated)
    python scripts/ask.py "Show me last month's top 5 customers"
    python scripts/ask.py "question 1" "question 2"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.llm_client import get_llm_client  # noqa: E402
from src.pipeline import ask  # noqa: E402

DEFAULT_QUESTIONS = ["Show me last month's top 5 customers by spending"]


def main() -> None:
    """Run each question through the pipeline and print the outcome."""
    questions = sys.argv[1:] or DEFAULT_QUESTIONS
    llm = get_llm_client()
    pd.set_option("display.width", 120)

    for question in questions:
        result = ask(question, llm=llm)
        print("=" * 72)
        print(f"Q: {question}")
        print(f"STATUS: {result.status.upper()}   {result.message}")
        if result.repaired:
            print("(the first SQL failed; the self-repair attempt was used)")
        if result.generated_sql:
            print("\n-- SQL written by the LLM:\n" + result.generated_sql)
        if result.dataframe is not None:
            print("\n" + result.dataframe.head(15).to_string(index=False))
        t = result.timings
        print(f"\n(llm {t['llm']:.2f}s | validation {t['validation']:.3f}s | "
              f"execution {t['execution']:.3f}s | total {t['total']:.2f}s | tokens {result.token_usage})")
    print("=" * 72)


if __name__ == "__main__":
    main()
