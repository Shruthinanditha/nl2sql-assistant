"""
sql_generator.py - turns a natural-language question into SQL using the LLM.

WHY THIS FILE EXISTS
    It is the "translator" step of the pipeline:
      1. build the prompts (rules + schema + examples + question),
      2. call the LLM,
      3. CLEAN the answer: models often add ```sql fences or a sentence like
         "Here is the query:", even when told not to,
      4. detect the CANNOT_ANSWER marker (the model says it cannot answer).

    IMPORTANT: this module does NOT decide whether the SQL is safe. Cleaning
    only removes decoration around the SQL; it never cuts the SQL itself
    (e.g. it does not drop text after a ';'). Otherwise an attack such as
    "SELECT 1; DROP TABLE orders" could be hidden from the validator, which
    must see exactly what would be executed.

HOW IT FITS IN THE PIPELINE
    question -> [sql_generator] -> SQL text -> sql_validator -> executor
"""

import re
import time
from dataclasses import dataclass
from datetime import date

from src.config import settings
from src.llm_client import LLMClient, get_llm_client
from src.prompts import CANNOT_ANSWER_MARKER, build_repair_prompt, build_system_prompt, build_user_prompt
from src.schema import Schema, get_schema

# Questions longer than this are rejected: real questions are short, and very
# long inputs are a typical sign of prompt-injection attempts (and cost tokens).
MAX_QUESTION_LENGTH = 500

# Matches a markdown code block: ```sql ... ``` (the language tag is optional).
# re.DOTALL lets "." match newlines, so multi-line SQL is captured.
CODE_FENCE_PATTERN = re.compile(r"```[a-zA-Z]*\s*\n?(.*?)```", re.DOTALL)

# Finds where the SQL starts if the model wrote a sentence before it.
SQL_START_PATTERN = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE | re.MULTILINE)


@dataclass
class GenerationResult:
    """
    What the generator returns.

    Exactly one of `sql` or `cannot_answer_reason` is set (unless error is set).
      sql                  - the cleaned SQL text (NOT yet validated!)
      cannot_answer_reason - the model's explanation when it cannot answer
      error                - a problem with the input (e.g. empty question)
      raw_response         - the model's untouched answer (for logging/debugging)
      llm_seconds          - how long the LLM call took
      token_usage          - tokens used, if the provider reports them
    """

    sql: str | None = None
    cannot_answer_reason: str | None = None
    error: str | None = None
    raw_response: str = ""
    llm_seconds: float = 0.0
    token_usage: dict[str, int] | None = None


def clean_llm_output(raw_text: str) -> str:
    """
    Remove decoration around the SQL in an LLM answer.

    Handles, in order:
      1. Markdown fences: "```sql\\nSELECT ...\\n```" -> "SELECT ..."
      2. Leading chatter: "Here is the query:\\nSELECT ..." -> "SELECT ..."
    It does NOT shorten the SQL itself (see module docstring).

    Input:  the raw model output
    Output: the SQL text, stripped of surrounding whitespace
    """
    text = raw_text.strip()

    # 1. If there is a fenced code block, keep only what is inside the first one.
    fence_match = CODE_FENCE_PATTERN.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # 2. If the text does not start with SQL, skip to the first line that does.
    #    (If no SELECT/WITH exists at all, keep the text unchanged; the
    #    validator will reject it with a clear reason.)
    start_match = SQL_START_PATTERN.search(text)
    if start_match and start_match.start() > 0:
        text = text[start_match.start():].strip()

    return text


def extract_cannot_answer(text: str) -> str | None:
    """
    If the model used the CANNOT_ANSWER marker, return its reason, else None.

    Example: "CANNOT_ANSWER: No salary data." -> "No salary data."
    """
    stripped = text.strip().strip("`").strip()
    if stripped.upper().startswith(CANNOT_ANSWER_MARKER):
        reason = stripped[len(CANNOT_ANSWER_MARKER):].lstrip(" :").strip()
        return reason or "The question cannot be answered with the available data."
    return None


def generate_sql(
    question: str,
    llm: LLMClient | None = None,
    schema: Schema | None = None,
    today: date | None = None,
) -> GenerationResult:
    """
    Translate a question into SQL.

    Inputs:  question - the user's question in plain English
             llm      - the LLM client (defaults to the one configured in .env;
                        tests pass a FakeLLM)
             schema   - the allowed schema (defaults to get_schema())
             today    - the reference date (defaults to today; tests fix it)
    Output:  a GenerationResult (see the class above)
    Raises:  LLMError if the LLM call fails (the pipeline turns it into a message)
    """
    question = (question or "").strip()
    if not question:
        return GenerationResult(error="Please type a question.")
    if len(question) > MAX_QUESTION_LENGTH:
        return GenerationResult(
            error=f"The question is too long ({len(question)} characters; the maximum is {MAX_QUESTION_LENGTH})."
        )

    return _ask_llm(build_user_prompt(question), llm, schema, today)


def repair_sql(
    question: str,
    failed_sql: str,
    error_message: str,
    llm: LLMClient | None = None,
    schema: Schema | None = None,
    today: date | None = None,
) -> GenerationResult:
    """
    Ask the LLM to fix a query that failed, showing it the error message.

    Same inputs as generate_sql, plus the failed SQL and its error.
    Output: a new GenerationResult (its SQL must be validated again!).
    """
    return _ask_llm(build_repair_prompt(question, failed_sql, error_message), llm, schema, today)


def _ask_llm(
    user_prompt: str,
    llm: LLMClient | None,
    schema: Schema | None,
    today: date | None,
) -> GenerationResult:
    """
    Shared by generate_sql and repair_sql: build the system prompt, call the
    LLM, time it, and turn the answer into a GenerationResult.
    """
    llm = llm or get_llm_client()
    schema = schema or get_schema()
    today = today or date.today()

    system_prompt = build_system_prompt(schema.to_prompt_text(), today, settings.max_rows)

    start = time.perf_counter()
    raw_response = llm.complete(system_prompt, user_prompt)
    llm_seconds = time.perf_counter() - start

    result = GenerationResult(
        raw_response=raw_response,
        llm_seconds=llm_seconds,
        token_usage=llm.last_token_usage,
    )

    reason = extract_cannot_answer(raw_response)
    if reason is not None:
        result.cannot_answer_reason = reason
    else:
        result.sql = clean_llm_output(raw_response)
    return result
