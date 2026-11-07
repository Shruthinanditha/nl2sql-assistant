"""
try_questions.py - prints the SQL the LLM generates for some questions.

WHY THIS FILE EXISTS
    Phase 4 check: before we have a validator, executor or UI, we want to SEE
    what SQL the model writes, and compare it in DBeaver with our own SQL.
    Nothing is executed here; it only prints.

HOW TO RUN (venv activated)
    python scripts/try_questions.py                      # the 5 sample questions
    python scripts/try_questions.py "your own question"  # any question(s)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm_client import LLMError, get_llm_client  # noqa: E402
from src.sql_generator import generate_sql  # noqa: E402

SAMPLE_QUESTIONS = [
    "Show me last month's top 5 customers by spending",
    "What is the revenue per month this year?",
    "How many orders are there in each status?",
    "Which product category sold the most units in the last 90 days?",
    "What are the email addresses of customers in Chennai?",  # email is hidden -> should refuse
]


def main() -> None:
    """Generate and print SQL for each question (from the command line or the samples)."""
    questions = sys.argv[1:] or SAMPLE_QUESTIONS
    llm = get_llm_client()  # create once, reuse for every question
    print(f"Provider: {llm.provider_name}   Model: {llm.model}\n")

    for number, question in enumerate(questions, start=1):
        print("=" * 70)
        print(f"Q{number}: {question}")
        print("-" * 70)
        try:
            result = generate_sql(question, llm=llm)
        except LLMError as error:
            print(f"LLM ERROR: {error}\n")
            continue

        if result.error:
            print(f"INPUT ERROR: {result.error}")
        elif result.cannot_answer_reason:
            print(f"CANNOT ANSWER: {result.cannot_answer_reason}")
        else:
            print(result.sql)
        print(f"\n({result.llm_seconds:.1f} s, tokens: {result.token_usage})\n")


if __name__ == "__main__":
    main()
