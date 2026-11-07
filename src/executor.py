"""
executor.py - runs validated SQL on the database, safely.

WHY THIS FILE EXISTS
    Even after the validator, we do not trust the query. The database itself
    is configured so that a mistake in our Python checks still cannot cause harm.
    This module adds THREE database-level protections:

    1. READ-ONLY CONNECTION  ("file:...?mode=ro")
       SQLite itself refuses INSERT/UPDATE/DELETE/CREATE/DROP.

    2. AUTHORIZER  (connection.set_authorizer)
       SQLite calls our function for every table, column and function a query
       uses, WHILE PREPARING it. We allow only: SELECT, reading allowlisted
       tables/columns, and allowlisted functions. Everything else is denied.
       Why needed: we tested that read-only mode does NOT stop
       `ATTACH DATABASE 'x.db'` (it created a new file on disk!) or some
       PRAGMA commands. The authorizer blocks those. It also re-checks the
       allowlist independently of sqlglot (two different implementations).

    3. TIMEOUT + ROW CAP
       A "progress handler" runs every few thousand SQLite steps and aborts the
       query when it runs too long (stops runaway joins). At most `max_rows`
       rows are fetched, so a huge result cannot exhaust memory.

HOW IT FITS IN THE PIPELINE
    sql_validator -> clean_sql -> [executor] -> pandas DataFrame -> UI / charts
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from src.config import settings
from src.schema import Schema, get_schema
from src.sql_validator import ALLOWED_FUNCTIONS

# How often (in SQLite virtual-machine steps) the timeout check runs.
# Small enough to react quickly, large enough to cost almost nothing.
PROGRESS_CHECK_INTERVAL = 1000


@dataclass
class ExecutionResult:
    """
    Outcome of running one query.

      dataframe - the rows as a pandas DataFrame (None if an error happened)
      seconds   - how long the execution took
      row_count - number of rows returned (after the cap)
      truncated - True if there were more rows than max_rows (the rest were cut)
      error     - a readable error message, or None on success
      timed_out - True if the query was stopped by the timeout
    """

    dataframe: pd.DataFrame | None = None
    seconds: float = 0.0
    row_count: int = 0
    truncated: bool = False
    error: str | None = None
    timed_out: bool = False


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    """
    Open the database in READ-ONLY mode.

    The path is URL-encoded (spaces become %20 etc.) because the "file:" URI
    format needs it; our folder name contains spaces.
    """
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. Create it with: python data/seed_database.py"
        )
    uri = f"file:{quote(db_path.as_posix(), safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def make_authorizer(schema: Schema):
    """
    Build the authorizer callback for this schema's allowlist.

    SQLite calls it as callback(action, arg1, arg2, database_name, trigger_name)
    and expects SQLITE_OK (allow) or SQLITE_DENY (refuse; the query fails).
      - SQLITE_SELECT:   the statement is a SELECT                       -> allow
      - SQLITE_READ:     arg1 = table, arg2 = column being read          -> allow only allowlisted
      - SQLITE_FUNCTION: arg2 = function name                            -> allow only allowlisted
      - anything else (INSERT, ATTACH, PRAGMA, RECURSIVE, ...)           -> deny
    """
    allowed_columns = schema.allowed_columns()

    def authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            column = (arg2 or "").lower()
            # An empty column name is a whole-row read on an allowed table (e.g. for COUNT(*)).
            if table in allowed_columns and (column == "" or column in allowed_columns[table]):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").lower() in ALLOWED_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    return authorizer


def _explain_sqlite_error(error: Exception) -> str:
    """Turn a SQLite error into a short, readable message (also sent to the LLM for repair)."""
    text = str(error)
    # The authorizer's refusals: "not authorized" (statements, functions) or
    # "access to <table>.<column> is prohibited" (column reads).
    if "not authorized" in text or "is prohibited" in text:
        return "The database refused this query (it uses something that is not allowed)."
    if "readonly database" in text:
        return "The database is read-only; the query tried to change data."
    return f"SQL error: {text}"


def execute_query(
    sql: str,
    db_path: Path | None = None,
    schema: Schema | None = None,
    max_rows: int | None = None,
    timeout_seconds: float | None = None,
    use_authorizer: bool = True,
) -> ExecutionResult:
    """
    Run one (already validated) SQL query with all database-level protections.

    Inputs:  sql             - the query (normally ValidationResult.clean_sql)
             db_path         - database file (default: DB_PATH from .env)
             schema          - allowlist for the authorizer (default: get_schema())
             max_rows        - maximum rows to fetch (default: MAX_ROWS)
             timeout_seconds - abort after this long (default: QUERY_TIMEOUT_SECONDS)
             use_authorizer  - only tests switch this off, to prove that the
                               read-only mode works on its own
    Output:  ExecutionResult (never raises for SQL problems; check .error)
    """
    db_path = db_path or settings.db_path
    max_rows = max_rows if max_rows is not None else settings.max_rows
    timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.query_timeout_seconds

    start = time.perf_counter()
    deadline = start + timeout_seconds
    timed_out = False

    def stop_if_too_slow() -> int:
        """Progress handler: a non-zero return value makes SQLite abort the query."""
        nonlocal timed_out
        if time.perf_counter() > deadline:
            timed_out = True
            return 1
        return 0

    connection = connect_read_only(db_path)
    try:
        if use_authorizer:
            connection.set_authorizer(make_authorizer(schema or get_schema()))
        connection.set_progress_handler(stop_if_too_slow, PROGRESS_CHECK_INTERVAL)

        cursor = connection.execute(sql)
        # Fetch one row more than allowed: if it exists, we know the result was cut.
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        column_names = [description[0] for description in cursor.description or []]
        dataframe = pd.DataFrame(rows, columns=column_names)
    except (sqlite3.Error, sqlite3.Warning) as error:
        seconds = time.perf_counter() - start
        if timed_out:
            return ExecutionResult(
                seconds=seconds,
                timed_out=True,
                error=f"The query took longer than {timeout_seconds:g} seconds and was stopped.",
            )
        return ExecutionResult(seconds=seconds, error=_explain_sqlite_error(error))
    finally:
        connection.close()

    return ExecutionResult(
        dataframe=dataframe,
        seconds=time.perf_counter() - start,
        row_count=len(dataframe),
        truncated=truncated,
    )
