"""
pipeline.py - the complete journey from a question to a result.

WHY THIS FILE EXISTS
    The UI (and the evaluation) should call ONE function: ask(question).
    This module ties the steps together and decides what happens when
    something goes wrong:

        question
          -> generate SQL (LLM)          -> "cannot answer"?  stop, explain
          -> validate (security checks)  -> unsafe?            stop, explain why
          -> execute (read-only DB)      -> SQL error?         repair ONCE
          -> result (DataFrame + SQL + timings)

SELF-REPAIR (at most once)
    LLMs sometimes write SQL with a small mistake (a misspelled column, a
    function used wrongly). If the query fails to RUN, or fails validation for
    a "fixable" reason (cannot parse / unknown column), we send the error back
    to the LLM once. The new SQL goes through ALL checks again.
    We never repair SECURITY rejections (DELETE, private tables, ...): helping
    a malicious request "get past" the validator would be exactly wrong.
    We never loop: one repair attempt, then we give up with a clear message.

AUDIT LOG
    Every question is logged as one JSON line in LOG_PATH (question, SQL,
    verdict, status, timings). Useful for debugging and evaluation.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.config import settings
from src.executor import ExecutionResult, execute_query
from src.llm_client import LLMClient, LLMError, get_llm_client
from src.schema import Schema, get_schema
from src.sql_generator import GenerationResult, generate_sql, repair_sql
from src.sql_validator import ValidationResult, validate_sql

# Validation failures that a second LLM attempt might fix (honest mistakes).
# All other failures are security rejections and are never repaired.
REPAIRABLE_CHECKS = {"parse_error", "forbidden_column", "unverifiable"}

# Possible final statuses of a question.
STATUS_SUCCESS = "success"              # rows returned
STATUS_CANNOT_ANSWER = "cannot_answer"  # the LLM said the data does not exist
STATUS_BLOCKED = "blocked"              # the validator refused the SQL
STATUS_ERROR = "error"                  # LLM failure, SQL error, timeout, bad input


@dataclass
class PipelineResult:
    """
    Everything the UI needs to show for one question.

      status         - one of the STATUS_* values above
      message        - a human-readable explanation (for errors/blocks/cannot-answer)
      generated_sql  - the SQL the LLM wrote (last attempt)
      executed_sql   - the validated SQL that was actually run
      dataframe      - result rows (None unless status == success)
      validation     - the validator's verdict on the last attempt
      repaired       - True if the self-repair attempt was used
      truncated      - True if the result was cut at MAX_ROWS
      timings        - seconds per step: llm, validation, execution, total
      token_usage    - tokens used by all LLM calls together
    """

    question: str
    status: str = STATUS_ERROR
    message: str = ""
    generated_sql: str | None = None
    executed_sql: str | None = None
    dataframe: pd.DataFrame | None = None
    validation: ValidationResult | None = None
    repaired: bool = False
    truncated: bool = False
    timings: dict[str, float] = field(default_factory=lambda: {"llm": 0.0, "validation": 0.0, "execution": 0.0, "total": 0.0})
    token_usage: dict[str, int] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        """Number of rows in the result (0 if there is no result)."""
        return 0 if self.dataframe is None else len(self.dataframe)


def _add_generation(result: PipelineResult, generation: GenerationResult) -> None:
    """Add one LLM call's time and tokens to the running totals."""
    result.timings["llm"] += generation.llm_seconds
    for key, value in (generation.token_usage or {}).items():
        result.token_usage[key] = result.token_usage.get(key, 0) + value


def _validate(result: PipelineResult, sql: str, schema: Schema) -> ValidationResult:
    """Run the validator, record its time and verdict."""
    start = time.perf_counter()
    validation = validate_sql(sql, schema=schema)
    result.timings["validation"] += time.perf_counter() - start
    result.validation = validation
    return validation


def _execute(result: PipelineResult, sql: str, schema: Schema, db_path: Path | None) -> ExecutionResult:
    """Run the executor, record its time."""
    execution = execute_query(sql, db_path=db_path, schema=schema)
    result.timings["execution"] += execution.seconds
    return execution


def ask(
    question: str,
    llm: LLMClient | None = None,
    schema: Schema | None = None,
    today: date | None = None,
    db_path: Path | None = None,
    log_path: Path | None = None,
) -> PipelineResult:
    """
    Answer one natural-language question end to end.

    Inputs:  question - the user's question
             llm      - LLM client (default: from .env; tests pass a FakeLLM)
             schema   - allowed schema (default: get_schema())
             today    - reference date (default: today)
             db_path  - database file (default: DB_PATH)
             log_path - audit log file (default: LOG_PATH); tests use a temp file
    Output:  PipelineResult (this function does not raise for normal failures)
    """
    total_start = time.perf_counter()
    result = PipelineResult(question=question)
    try:
        _run_steps(result, question, llm, schema or get_schema(), today, db_path)
    except LLMError as error:
        result.status = STATUS_ERROR
        result.message = str(error)
    result.timings["total"] = time.perf_counter() - total_start
    _write_log(result, log_path or settings.log_path)
    return result


def _run_steps(
    result: PipelineResult,
    question: str,
    llm: LLMClient | None,
    schema: Schema,
    today: date | None,
    db_path: Path | None,
) -> None:
    """The actual step-by-step logic of ask(). Fills in `result`."""
    # --- Step 1: generate SQL --------------------------------------------
    generation = generate_sql(question, llm=llm, schema=schema, today=today)
    if generation.error:  # bad input: no LLM call was made
        result.status, result.message = STATUS_ERROR, generation.error
        return
    llm = llm or get_llm_client()  # reuse the same client for a repair
    _add_generation(result, generation)

    attempt = 1
    while True:
        # --- Cannot answer? ----------------------------------------------
        if generation.cannot_answer_reason:
            result.status = STATUS_CANNOT_ANSWER
            result.message = generation.cannot_answer_reason
            return

        result.generated_sql = generation.sql

        # --- Step 2: validate --------------------------------------------
        validation = _validate(result, generation.sql, schema)
        if not validation.is_safe:
            if attempt == 1 and validation.failed_check in REPAIRABLE_CHECKS:
                error_for_llm = validation.reason
            else:
                result.status = STATUS_BLOCKED
                result.message = validation.reason
                return
        else:
            # --- Step 3: execute -----------------------------------------
            result.executed_sql = validation.clean_sql
            execution = _execute(result, validation.clean_sql, schema, db_path)
            if execution.error is None:
                result.status = STATUS_SUCCESS
                result.dataframe = execution.dataframe
                result.truncated = execution.truncated
                result.message = f"{execution.row_count} row(s)."
                if execution.truncated:
                    result.message += f" Only the first {execution.row_count} rows are shown."
                return
            # Timeouts are not repaired: the same question would likely time out again.
            if attempt > 1 or execution.timed_out:
                result.status, result.message = STATUS_ERROR, execution.error
                return
            error_for_llm = execution.error

        # --- Step 4: ONE repair attempt ---------------------------------
        attempt += 1
        result.repaired = True
        generation = repair_sql(question, generation.sql, error_for_llm, llm=llm, schema=schema, today=today)
        _add_generation(result, generation)


def _write_log(result: PipelineResult, log_path: Path) -> None:
    """
    Append one JSON line describing this question to the audit log.

    Logging must never break the app, so file errors are ignored.
    """
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "question": result.question,
        "status": result.status,
        "message": result.message,
        "generated_sql": result.generated_sql,
        "executed_sql": result.executed_sql,
        "failed_check": result.validation.failed_check if result.validation else None,
        "repaired": result.repaired,
        "rows": result.row_count,
        "timings": {key: round(value, 3) for key, value in result.timings.items()},
        "tokens": result.token_usage,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
