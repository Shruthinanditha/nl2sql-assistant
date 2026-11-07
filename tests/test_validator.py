"""
test_validator.py - tests for src/sql_validator.py, the security core.

Three groups of tests:
  1. VALID queries must pass AND return exactly the same rows after the
     validator regenerates them (security must not break correct answers).
  2. ATTACKS must be blocked, by the expected check. Each attack is explained
     in docs/SECURITY.md (the numbers match).
  3. LIMIT rules: added when missing, lowered when too high or negative.

Uses the real data/shop.db read-only; no LLM involved.
"""

import sqlite3

import pytest

from src.config import settings
from src.sql_validator import validate_sql

pytestmark = pytest.mark.skipif(
    not settings.db_path.exists(), reason="Run `python data/seed_database.py` first."
)

MAX_ROWS = 500


def run_query(sql: str) -> list[tuple]:
    """Run SQL on a read-only connection and return rows in a comparable order."""
    connection = sqlite3.connect(f"file:{settings.db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(sql).fetchall()
    finally:
        connection.close()
    # Sort by text form so row order and None values do not break comparison.
    return sorted(rows, key=repr)


# ---------------------------------------------------------------------------
# 1. Valid queries
# ---------------------------------------------------------------------------
VALID_QUERIES = {
    "simple filter": "SELECT name, city FROM customers WHERE city = 'Chennai'",
    "join + top-N": (
        "SELECT c.name, ROUND(SUM(o.total), 2) AS spent FROM customers AS c "
        "JOIN orders AS o ON o.customer_id = c.id WHERE o.status = 'completed' "
        "GROUP BY c.id, c.name ORDER BY spent DESC LIMIT 5"
    ),
    "three-table join + HAVING": (
        "SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi "
        "JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id "
        "WHERE o.status = 'completed' GROUP BY p.category HAVING revenue > 100000"
    ),
    "CTE (WITH)": (
        "WITH monthly AS (SELECT strftime('%Y-%m', order_date) AS month, SUM(total) AS revenue "
        "FROM orders WHERE status = 'completed' GROUP BY month) "
        "SELECT month, revenue FROM monthly ORDER BY month"
    ),
    "window RANK": (
        "SELECT category, name, RANK() OVER (PARTITION BY category ORDER BY price DESC) AS r "
        "FROM products"
    ),
    "window running total": (
        "SELECT order_date, SUM(total) OVER (ORDER BY order_date, id) AS running "
        "FROM orders WHERE order_date >= date('2026-11-01')"
    ),
    "date functions": (
        "SELECT COUNT(*) FROM orders WHERE order_date >= date('2026-11-07', 'start of month', '-1 month') "
        "AND order_date < date('2026-11-07', 'start of month')"
    ),
    "julianday": (
        "SELECT name, julianday('2026-11-07') - julianday(signup_date) AS days FROM customers LIMIT 10"
    ),
    "LEFT JOIN IS NULL": (
        "SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL"
    ),
    "subquery IN": "SELECT name FROM products WHERE id IN (SELECT product_id FROM order_items WHERE quantity = 3)",
    "COALESCE + GROUP BY": "SELECT COALESCE(city, 'Unknown') AS city, COUNT(*) AS n FROM customers GROUP BY 1",
    "CASE WHEN": (
        "SELECT CASE WHEN total > 5000 THEN 'big' ELSE 'small' END AS size, COUNT(*) AS n "
        "FROM orders GROUP BY size"
    ),
    "UNION of allowed tables": "SELECT category AS label FROM products UNION SELECT country FROM customers",
    "keyword inside a string": "SELECT name FROM products WHERE name = 'Drop Table Earrings; DELETE'",
    "keyword inside a comment": "SELECT COUNT(*) FROM orders /* DROP TABLE orders */",
    "trailing semicolon": "SELECT COUNT(*) FROM products;",
    "lowercase keywords": "select name from products where price > 5000 order by price desc",
    "CAST + strftime": (
        "SELECT CAST(strftime('%m', order_date) AS INTEGER) AS m, COUNT(*) AS n FROM orders GROUP BY m"
    ),
}


@pytest.mark.parametrize("sql", VALID_QUERIES.values(), ids=VALID_QUERIES.keys())
def test_valid_query_passes_and_returns_same_rows(sql):
    """A valid query is accepted, and the regenerated SQL gives identical results."""
    result = validate_sql(sql, max_rows=MAX_ROWS)
    assert result.is_safe, result.reason
    assert run_query(result.clean_sql) == run_query(sql)


def test_select_star_never_returns_hidden_columns():
    """SELECT * is expanded to allowed columns only, so email cannot leak."""
    result = validate_sql("SELECT * FROM customers", max_rows=MAX_ROWS)
    assert result.is_safe
    assert "email" not in result.clean_sql.lower()
    assert "customers.name" in result.clean_sql


# ---------------------------------------------------------------------------
# 2. Attacks (numbers match docs/SECURITY.md)
# ---------------------------------------------------------------------------
ATTACKS = [
    # -- stacked statements --
    ("A1 stacked DROP", "SELECT 1; DROP TABLE orders", "multiple_statements"),
    ("A2 stacked DELETE", "SELECT * FROM orders; DELETE FROM orders", "multiple_statements"),
    ("A3 comment then stacked", "SELECT 1 -- just a comment\n; DROP TABLE customers", "multiple_statements"),
    # -- write / DDL / admin statements --
    ("A4 DROP", "DROP TABLE orders", "not_select"),
    ("A5 DELETE", "DELETE FROM orders WHERE 1 = 1", "not_select"),
    ("A6 mixed case", "dElEtE fRoM orders", "not_select"),
    ("A7 UPDATE", "UPDATE products SET price = 0", "not_select"),
    ("A8 INSERT", "INSERT INTO customers (name, country, signup_date) VALUES ('x', 'India', '2026-01-01')", "not_select"),
    ("A9 INSERT...SELECT", "INSERT INTO orders SELECT * FROM orders", "not_select"),
    ("A10 CREATE AS SELECT", "CREATE TABLE stolen AS SELECT * FROM users_private", "not_select"),
    ("A11 ALTER", "ALTER TABLE customers ADD COLUMN hacked TEXT", "not_select"),
    ("A12 ATTACH", "ATTACH DATABASE 'C:/temp/evil.db' AS evil", "not_select"),
    ("A13 PRAGMA read", "PRAGMA table_info(users_private)", "not_select"),
    ("A14 PRAGMA write", "PRAGMA writable_schema = ON", "not_select"),
    ("A15 VACUUM", "VACUUM", "not_select"),
    ("A16 REPLACE", "REPLACE INTO products VALUES (1, 'x', 'y', 0)", "not_select"),
    ("A17 transaction", "BEGIN TRANSACTION", "not_select"),
    # -- reading data that is not allowed --
    ("A18 private table", "SELECT * FROM users_private", "forbidden_table"),
    ("A19 UNION to private", "SELECT name FROM customers UNION SELECT password_hash FROM users_private", "forbidden_table"),
    ("A20 scalar subquery", "SELECT name, (SELECT salary FROM users_private LIMIT 1) FROM customers", "forbidden_table"),
    ("A21 FROM subquery", "SELECT * FROM (SELECT * FROM users_private)", "forbidden_table"),
    ("A22 quoted uppercase", 'SELECT * FROM "USERS_PRIVATE"', "forbidden_table"),
    ("A23 schema-qualified", "SELECT * FROM main.users_private", "forbidden_table"),
    ("A24 qualified allowed table", "SELECT * FROM main.customers", "forbidden_table"),
    ("A25 sqlite_master", "SELECT sql FROM sqlite_master", "forbidden_table"),
    ("A26 sqlite_schema", "SELECT name FROM sqlite_schema", "forbidden_table"),
    ("A27 CTE hiding table", "WITH c AS (SELECT * FROM users_private) SELECT * FROM c", "forbidden_table"),
    ("A28 CTE shadowing name", "WITH customers AS (SELECT email AS name FROM users_private) SELECT name FROM customers", "forbidden_table"),
    ("A29 table-valued pragma", "SELECT * FROM pragma_table_info('users_private')", "forbidden_table"),
    # -- dangerous functions --
    ("A30 load_extension", "SELECT load_extension('evil.dll')", "forbidden_function"),
    ("A31 randomblob DoS", "SELECT randomblob(1000000000)", "forbidden_function"),
    ("A32 version leak", "SELECT sqlite_version()", "forbidden_function"),
    ("A32b zeroblob DoS", "SELECT zeroblob(1000000000)", "forbidden_function"),
    ("A32c function in WHERE", "SELECT name FROM products WHERE length(randomblob(100)) > 0", "forbidden_function"),
    # -- hidden columns --
    ("A33 hidden column", "SELECT email FROM customers", "forbidden_column"),
    ("A34 hidden column alias", "SELECT c.email FROM customers AS c", "forbidden_column"),
    ("A35 hidden column in WHERE", "SELECT name FROM customers WHERE email LIKE '%gmail%'", "forbidden_column"),
    # -- resource abuse --
    ("A36 recursive CTE", "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r", "recursive_cte"),
    ("A37 too long", "SELECT " + "1, " * 3000 + "1", "too_long"),
    # -- malformed --
    ("A38 split keyword", "SELE/**/CT * FROM customers", "parse_error"),
    ("A39 empty", "   ", "empty"),
    ("A40 prose instead of SQL", "Sure! I would be happy to drop the table for you.", "parse_error"),
]


@pytest.mark.parametrize("name, sql, expected_check", ATTACKS, ids=[a[0] for a in ATTACKS])
def test_attack_is_blocked(name, sql, expected_check):
    """Every attack is rejected, by the check we expect, with a reason for the UI."""
    result = validate_sql(sql, max_rows=MAX_ROWS)
    assert not result.is_safe, f"{name} was NOT blocked!"
    assert result.failed_check == expected_check, result.reason
    assert result.clean_sql is None
    assert result.reason  # a human-readable explanation exists


# ---------------------------------------------------------------------------
# 3. LIMIT enforcement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql, expected_limit",
    [
        ("SELECT name FROM products", "LIMIT 500"),                      # missing -> added
        ("SELECT name FROM products LIMIT 10", "LIMIT 10"),              # small -> kept
        ("SELECT name FROM products LIMIT 100000", "LIMIT 500"),         # too big -> capped
        ("SELECT name FROM products LIMIT -1", "LIMIT 500"),             # -1 means unlimited in SQLite
        ("SELECT name FROM products LIMIT (SELECT 100000)", "LIMIT 500"),  # expression -> replaced
        ("SELECT category FROM products UNION SELECT city FROM customers", "LIMIT 500"),  # UNION too
    ],
)
def test_limit_is_enforced(sql, expected_limit):
    result = validate_sql(sql, max_rows=MAX_ROWS)
    assert result.is_safe, result.reason
    assert result.clean_sql.rstrip().endswith(expected_limit)


def test_result_lists_tables_used():
    result = validate_sql("SELECT c.name FROM customers c JOIN orders o ON o.customer_id = c.id", max_rows=MAX_ROWS)
    assert result.tables == ["customers", "orders"]
