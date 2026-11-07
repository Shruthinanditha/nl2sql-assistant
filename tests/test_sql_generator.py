"""
test_sql_generator.py - tests for src/sql_generator.py and src/prompts.py.

All tests use FakeLLM, so no network or API key is needed.
"""

from datetime import date

import pytest

from src.config import settings
from src.llm_client import FakeLLM
from src.sql_generator import clean_llm_output, extract_cannot_answer, generate_sql

pytestmark = pytest.mark.skipif(
    not settings.db_path.exists(), reason="Run `python data/seed_database.py` first."
)

FIXED_TODAY = date(2026, 11, 7)


# ----- clean_llm_output -----------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("SELECT 1;", "SELECT 1;"),
        ("```sql\nSELECT 1;\n```", "SELECT 1;"),                       # markdown fence
        ("```\nSELECT 1;\n```", "SELECT 1;"),                          # fence without language
        ("Here is the query:\nSELECT 1;", "SELECT 1;"),                # chatter before SQL
        ("Sure!\n```sql\nWITH x AS (SELECT 1) SELECT * FROM x;\n```\nHope it helps",
         "WITH x AS (SELECT 1) SELECT * FROM x;"),                      # fence + chatter
        ("  select 1  ", "select 1"),                                   # whitespace, lowercase
    ],
)
def test_clean_llm_output(raw, expected):
    """Decoration around the SQL is removed."""
    assert clean_llm_output(raw) == expected


def test_cleaning_never_hides_stacked_statements():
    """SECURITY: the text after ';' must be kept so the validator can see and block it."""
    raw = "```sql\nSELECT 1; DROP TABLE orders;\n```"
    assert "DROP TABLE orders" in clean_llm_output(raw)


# ----- CANNOT_ANSWER marker -------------------------------------------------
def test_extract_cannot_answer_reason():
    assert extract_cannot_answer("CANNOT_ANSWER: No salary data.") == "No salary data."


def test_extract_cannot_answer_returns_none_for_sql():
    assert extract_cannot_answer("SELECT 1;") is None


# ----- generate_sql (with FakeLLM) ------------------------------------------
def test_generate_sql_returns_clean_sql():
    fake = FakeLLM(default_response="```sql\nSELECT COUNT(*) FROM customers;\n```")
    result = generate_sql("How many customers?", llm=fake, today=FIXED_TODAY)
    assert result.sql == "SELECT COUNT(*) FROM customers;"
    assert result.cannot_answer_reason is None


def test_generate_sql_handles_cannot_answer():
    fake = FakeLLM(default_response="CANNOT_ANSWER: There is no weather data.")
    result = generate_sql("What is the weather?", llm=fake, today=FIXED_TODAY)
    assert result.sql is None
    assert result.cannot_answer_reason == "There is no weather data."


def test_prompt_contains_date_schema_and_question():
    """The system prompt gets today's date and the schema; the user prompt gets the question."""
    fake = FakeLLM()
    generate_sql("Top customers last month", llm=fake, today=FIXED_TODAY)
    sent = fake.calls[0]
    assert "2026-11-07" in sent["system_prompt"]
    assert "CREATE TABLE orders" in sent["system_prompt"]
    assert "users_private" not in sent["system_prompt"]
    assert "<question>\nTop customers last month\n</question>" in sent["user_prompt"]


def test_empty_question_is_rejected_without_calling_llm():
    fake = FakeLLM()
    result = generate_sql("   ", llm=fake)
    assert result.error == "Please type a question."
    assert fake.calls == []  # no tokens wasted


def test_too_long_question_is_rejected():
    fake = FakeLLM()
    result = generate_sql("x" * 501, llm=fake)
    assert "too long" in result.error
    assert fake.calls == []
