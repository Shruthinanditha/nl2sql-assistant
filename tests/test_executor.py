"""
test_executor.py - tests for src/executor.py (the database-level protections).

The most important tests here deliberately SKIP the validator and send
dangerous SQL straight to the executor. They prove that the database layer
blocks harm on its own ("even if the validator had a bug").
Those tests run on a temporary COPY of shop.db, never on the real file.
"""

import shutil
import sqlite3

import pytest

from src.config import settings
from src.executor import execute_query
from src.sql_validator import validate_sql
from tests.test_validator import VALID_QUERIES

pytestmark = pytest.mark.skipif(
    not settings.db_path.exists(), reason="Run `python data/seed_database.py` first."
)


@pytest.fixture
def db_copy(tmp_path):
    """A throwaway copy of shop.db for tests that attempt writes."""
    copy_path = tmp_path / "shop_copy.db"
    shutil.copy(settings.db_path, copy_path)
    return copy_path


def count_orders(db_path) -> int:
    """Count orders with a separate, normal connection (to detect any change)."""
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    finally:
        connection.close()


# ----- normal behaviour -----------------------------------------------------
def test_returns_dataframe_with_column_names():
    result = execute_query("SELECT name, price FROM products ORDER BY price DESC LIMIT 3")
    assert result.error is None
    assert list(result.dataframe.columns) == ["name", "price"]
    assert result.row_count == 3
    assert result.dataframe.iloc[0]["name"] == "Noise Cancelling Headphones"


def test_row_cap_truncates_large_results():
    result = execute_query("SELECT id FROM orders", max_rows=10)
    assert result.row_count == 10
    assert result.truncated is True


def test_small_result_is_not_marked_truncated():
    result = execute_query("SELECT id FROM products", max_rows=100)
    assert result.row_count == 40
    assert result.truncated is False


@pytest.mark.parametrize("sql", VALID_QUERIES.values(), ids=VALID_QUERIES.keys())
def test_authorizer_allows_every_valid_validated_query(sql):
    """The database-level allowlist must not break any query the validator accepts."""
    validation = validate_sql(sql)
    result = execute_query(validation.clean_sql)
    assert result.error is None, result.error


# ----- timeout ----------------------------------------------------------------
def test_slow_query_is_stopped_by_timeout():
    """A huge cross join (about 177 billion row combinations) is stopped after the timeout."""
    result = execute_query(
        "SELECT COUNT(*) FROM order_items a, order_items b, order_items c", timeout_seconds=1
    )
    assert result.timed_out is True
    assert "longer than 1 seconds" in result.error
    assert result.seconds < 3


# ----- writes blocked WITHOUT the validator (layer 4) -------------------------
@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM orders", "UPDATE orders SET total = 0", "DROP TABLE orders",
     "INSERT INTO orders (customer_id, order_date, status, total) VALUES (1, '2026-01-01', 'completed', 1)"],
)
@pytest.mark.parametrize("use_authorizer", [True, False], ids=["with_authorizer", "read_only_mode_alone"])
def test_writes_fail_at_database_level(db_copy, sql, use_authorizer):
    """Both protections block writes independently: read-only mode alone, and the authorizer."""
    before = count_orders(db_copy)
    result = execute_query(sql, db_path=db_copy, use_authorizer=use_authorizer)
    assert result.error is not None
    assert count_orders(db_copy) == before


def test_attach_is_blocked_by_authorizer(db_copy, tmp_path):
    """
    Read-only mode does NOT stop ATTACH (we tested: it creates a new file).
    The authorizer does.
    """
    evil_file = tmp_path / "evil.db"
    result = execute_query(f"ATTACH DATABASE '{evil_file.as_posix()}' AS evil", db_path=db_copy)
    assert result.error is not None
    assert not evil_file.exists()


def test_pragma_is_blocked_by_authorizer(db_copy):
    result = execute_query("PRAGMA journal_mode = DELETE", db_path=db_copy)
    assert result.error is not None


# ----- allowlist enforced by the database too (independent of sqlglot) --------
@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM users_private", "SELECT email FROM customers", "SELECT sql FROM sqlite_master",
     "SELECT sqlite_version()", "SELECT randomblob(10)",
     "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r LIMIT 5"],
)
def test_authorizer_blocks_non_allowlisted_access(sql):
    result = execute_query(sql)
    assert result.error == "The database refused this query (it uses something that is not allowed)."


def test_stacked_statements_are_refused_by_driver(db_copy):
    """Python's sqlite3 refuses to run two statements in one execute() call - another barrier."""
    before = count_orders(db_copy)
    result = execute_query("SELECT 1; DELETE FROM orders", db_path=db_copy, use_authorizer=False)
    assert result.error is not None
    assert count_orders(db_copy) == before
