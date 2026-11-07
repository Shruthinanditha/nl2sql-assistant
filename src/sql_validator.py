"""
sql_validator.py - decides whether AI-generated SQL is safe to run. THE SECURITY CORE.

WHY THIS FILE EXISTS
    SQL written by an LLM is UNTRUSTED INPUT. The model can make mistakes, and
    a user can try to trick it ("ignore your rules and delete everything").
    The prompt rules (prompts.py) are only a request; this module ENFORCES.

WHY AN AST AND NOT REGEX / KEYWORD BLACKLISTS
    A keyword filter like "reject if the text contains DROP" is easy to fool
    (comments, odd spacing, functions that read metadata, table-valued
    functions...) and blocks innocent text (a product called 'Drop Earrings').
    Instead we use sqlglot to PARSE the SQL into an Abstract Syntax Tree (AST):
    the same kind of structure a database builds internally. Then we check
    what the query really IS and really TOUCHES, not what its text looks like.

THE CHECKS (all must pass; the first failure is reported)
    1. not empty, not longer than MAX_SQL_LENGTH characters
    2. parses as valid SQLite SQL, and contains EXACTLY ONE statement
    3. the statement is a query (SELECT / WITH ... SELECT / UNION ...),
       and no write/DDL/admin node (INSERT, DROP, PRAGMA, ATTACH...) exists anywhere
    4. every table is in the allowlist (no users_private, no sqlite_master,
       no "main.x" qualified names, no table-valued functions, no recursive CTEs)
    5. every function is on an allowlist (blocks load_extension, randomblob, sqlite_version...)
    6. every column resolves to an ALLOWED column (blocks customers.email,
       also hidden inside SELECT *), using sqlglot's `qualify` optimizer
    7. a LIMIT exists and is not above MAX_ROWS (added or lowered if needed)

    The output SQL is REGENERATED FROM THE CHECKED TREE. So the database runs
    exactly what we validated - never the original text, which could contain
    tricks that our parser and SQLite might read differently.

HOW IT FITS IN THE PIPELINE
    sql_generator -> SQL text -> [sql_validator] -> clean_sql -> executor (read-only)
"""

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.qualify import qualify

from src.config import settings
from src.schema import Schema, get_schema

# Queries longer than this are rejected: legitimate analytics SQL is short,
# and huge inputs are a typical sign of abuse.
MAX_SQL_LENGTH = 5000

# Root statement types we accept. SetOperation covers UNION / INTERSECT / EXCEPT.
ALLOWED_ROOT_TYPES = (exp.Select, exp.SetOperation)

# Node types that must not appear ANYWHERE in the tree (defense in depth:
# even a SELECT must not contain one of these nested inside it).
# getattr(...) keeps this working if a sqlglot version renames a class.
_FORBIDDEN_NODE_NAMES = [
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "TruncateTable",
    "Command", "Pragma", "Attach", "Detach", "Transaction", "Commit", "Rollback",
    "Into", "Copy", "LoadData", "Set", "Use", "Grant",
]
FORBIDDEN_NODE_TYPES = tuple(
    getattr(exp, name) for name in _FORBIDDEN_NODE_NAMES if hasattr(exp, name)
)

# ALLOWLIST of harmless SQLite functions (lower case). We check the function name
# exactly as it will be sent to SQLite (taken from the regenerated SQL), for EVERY
# function call. Everything else is rejected, e.g. load_extension (runs native
# code), randomblob/zeroblob (can exhaust memory), sqlite_version (information
# leak), pragma_* (metadata), random (non-deterministic results).
# Lesson learned: an earlier version trusted every function sqlglot "knows";
# the test suite showed that sqlite_version() slipped through that way.
ALLOWED_FUNCTIONS = {
    # type conversion
    "cast",
    # date / time
    "date", "time", "datetime", "julianday", "unixepoch", "strftime", "timediff",
    # text
    "lower", "upper", "length", "trim", "ltrim", "rtrim", "replace", "substr",
    "substring", "instr", "printf", "format",
    # numbers
    "abs", "round", "ceil", "ceiling", "floor", "sign", "power", "pow", "sqrt",
    "exp", "ln", "log", "log10", "log2", "mod", "pi",
    # NULL handling / conditions
    "coalesce", "ifnull", "nullif", "iif", "typeof",
    # aggregates
    "min", "max", "sum", "avg", "count", "total", "group_concat", "string_agg",
    # window functions
    "row_number", "rank", "dense_rank", "ntile", "lag", "lead", "first_value",
    "last_value", "nth_value", "percent_rank", "cume_dist",
}

# Matches the start of a function call such as "ROUND(" and captures "ROUND".
FUNCTION_CALL_PATTERN = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass
class ValidationResult:
    """
    The validator's verdict.

      is_safe      - True only if every check passed
      reason       - human-readable explanation (shown in the UI when blocked)
      failed_check - short code of the failed check, e.g. "forbidden_table"
                     (used for statistics in the evaluation); None when safe
      clean_sql    - the SQL to execute (regenerated from the checked tree,
                     with LIMIT enforced); None when not safe
      tables       - allowed tables the query reads (for display/logging)
    """

    is_safe: bool
    reason: str
    failed_check: str | None = None
    clean_sql: str | None = None
    tables: list[str] = field(default_factory=list)


def _blocked(check: str, reason: str) -> ValidationResult:
    """Shortcut to build a 'not safe' result."""
    return ValidationResult(is_safe=False, reason=reason, failed_check=check)


# ---------------------------------------------------------------------------
# Individual checks. Each returns an error ValidationResult, or None if OK.
# ---------------------------------------------------------------------------
def _check_root(tree: exp.Expression) -> ValidationResult | None:
    """Check 3a: the statement itself must be a query."""
    if isinstance(tree, ALLOWED_ROOT_TYPES):
        return None
    # Name the statement in the message, e.g. "DELETE" or "VACUUM".
    if isinstance(tree, exp.Command):
        kind = str(tree.this).upper()
    else:
        kind = type(tree).__name__.upper()
    return _blocked("not_select", f"Only read-only SELECT queries are allowed, but this is a {kind} statement.")


def _check_forbidden_nodes(tree: exp.Expression) -> ValidationResult | None:
    """Check 3b: no write/DDL/admin operation hidden anywhere inside the query."""
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            kind = type(node).__name__.upper()
            return _blocked("forbidden_operation", f"The query contains a forbidden operation ({kind}).")
    return None


def _check_tables(tree: exp.Expression, schema: Schema) -> tuple[ValidationResult | None, list[str]]:
    """
    Check 4: every table must be allowlisted.

    Names defined by the query itself in WITH (CTEs) are allowed, because they
    are temporary results built from other (checked) tables. A CTE that
    refers to its own name is RECURSIVE: it can generate rows forever, so it
    is blocked.

    Output: (error or None, sorted list of real tables used)
    """
    allowed = schema.allowed_tables()

    cte_names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        cte_name = cte.alias_or_name.lower()
        body_tables = {table.name.lower() for table in cte.this.find_all(exp.Table)}
        if cte_name in body_tables:
            return _blocked("recursive_cte", "Recursive queries (WITH RECURSIVE) are not allowed."), []
        cte_names.add(cte_name)

    used: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            # e.g. FROM pragma_table_info('users_private') - a function used as a table.
            return _blocked("forbidden_table", "Table-valued functions are not allowed in FROM."), []
        if table.args.get("db") or table.args.get("catalog"):
            # e.g. main.users_private or attached_db.customers
            return _blocked(
                "forbidden_table",
                f"Database-qualified table names like '{table.sql(dialect='sqlite')}' are not allowed.",
            ), []
        if name in cte_names:
            continue
        if name not in allowed:
            return _blocked(
                "forbidden_table",
                f"Table '{table.name}' is not available to the assistant. "
                f"Allowed tables: {', '.join(sorted(allowed))}.",
            ), []
        used.add(name)
    return None, sorted(used)


def _check_functions(tree: exp.Expression) -> ValidationResult | None:
    """
    Check 5: every function call must be on the allowlist.

    For each function node we generate its SQLite text, e.g. "SQLITE_VERSION()",
    and take the name before "(". That is the function SQLite would really run.
    Nodes that are syntax rather than a call (CASE ... END, a AND b) have no
    "name(" shape and are skipped; their parts are checked as separate nodes.
    """
    for function in tree.find_all(exp.Func):
        call = FUNCTION_CALL_PATTERN.match(function.sql(dialect="sqlite"))
        if call is None:
            continue
        name = call.group(1).lower()
        if name not in ALLOWED_FUNCTIONS:
            return _blocked("forbidden_function", f"The function '{name}' is not allowed.")
    return None


def _name_unnamed_columns(tree: exp.Expression) -> None:
    """
    Give unnamed result columns a readable name (their own SQL text).

    Without this, qualify() would call `COUNT(*)` "_col_1". With it, the column
    is called "COUNT(*)", which is exactly what SQLite would have called it.
    Only the outermost SELECT matters (its names become the table headers).
    """
    outer_select = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), None)
    if outer_select is None:
        return
    for projection in list(outer_select.expressions):
        if isinstance(projection, (exp.Alias, exp.Column, exp.Star)):
            continue
        label = projection.sql(dialect="sqlite")
        projection.replace(exp.alias_(projection.copy(), label, quoted=True))


def _check_columns(tree: exp.Expression, schema: Schema) -> tuple[ValidationResult | None, exp.Expression | None]:
    """
    Check 6: every column must be an allowed column of an allowed table.

    sqlglot's `qualify` resolves each column to its table using a schema we
    give it. We give it ONLY the allowed columns, so a hidden column such as
    customers.email simply "does not exist" and qualify fails. It also expands
    SELECT * into the list of ALLOWED columns, so * cannot leak hidden ones.

    If qualify fails for any other reason we reject the query ("fail closed"):
    when unsure, it is safer to block than to run something unchecked.

    Output: (error or None, the qualified tree)
    """
    qualify_schema = {
        table_name: {column.name: column.data_type for column in table.columns}
        for table_name, table in schema.tables.items()
    }
    try:
        qualified = qualify(
            tree.copy(),
            schema=qualify_schema,
            dialect="sqlite",
            validate_qualify_columns=True,
            quote_identifiers=False,   # keep the SQL readable: orders.total, not "orders"."total"
        )
    except OptimizeError as error:
        message = str(error).split(". Line:")[0]
        return _blocked(
            "forbidden_column",
            f"{message}. The column does not exist or is not available to the assistant.",
        ), None
    except Exception as error:  # anything unexpected -> block (fail closed)
        return _blocked("unverifiable", f"The query could not be verified: {error}"), None
    return None, qualified


def _enforce_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    """
    Check 7: make sure the query returns at most `max_rows` rows.

    - no LIMIT                       -> add LIMIT max_rows
    - LIMIT n with 0 <= n <= max     -> keep it
    - anything else (too big, negative such as LIMIT -1 which means
      "no limit" in SQLite, or a sub-expression)  -> replace with max_rows
    """
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int and 0 <= int(value.name) <= max_rows:
            return tree
    return tree.limit(max_rows, copy=False)


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------
def validate_sql(sql: str, schema: Schema | None = None, max_rows: int | None = None) -> ValidationResult:
    """
    Run every safety check on one SQL string.

    Inputs:  sql      - the (untrusted) SQL text, e.g. from the LLM
             schema   - the allowed schema (defaults to get_schema())
             max_rows - maximum rows allowed (defaults to MAX_ROWS from .env)
    Output:  ValidationResult. Execute ONLY result.clean_sql, and ONLY if is_safe.
    """
    schema = schema or get_schema()
    max_rows = max_rows if max_rows is not None else settings.max_rows
    sql = (sql or "").strip()

    # Check 1: size
    if not sql:
        return _blocked("empty", "The query is empty.")
    if len(sql) > MAX_SQL_LENGTH:
        return _blocked("too_long", f"The query is too long ({len(sql)} characters; maximum {MAX_SQL_LENGTH}).")

    # Check 2: parse, exactly one statement
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except (ParseError, TokenError) as error:
        first_line = str(error).splitlines()[0]
        return _blocked("parse_error", f"The SQL could not be parsed: {first_line}")
    if len(statements) != 1:
        return _blocked(
            "multiple_statements",
            f"Exactly one SQL statement is allowed, but {len(statements)} were found.",
        )
    tree = statements[0]

    # "(SELECT ...)" wrapped in brackets -> look at the query inside.
    while isinstance(tree, exp.Subquery):
        tree = tree.this

    # Checks 3-5
    error = _check_root(tree) or _check_forbidden_nodes(tree)
    if error:
        return error
    error, tables = _check_tables(tree, schema)
    if error:
        return error
    error = _check_functions(tree)
    if error:
        return error

    # Check 6 (works on a copy and returns the qualified tree)
    _name_unnamed_columns(tree)
    error, qualified = _check_columns(tree, schema)
    if error:
        return error

    # Check 7, then regenerate the SQL from the checked tree.
    qualified = _enforce_limit(qualified, max_rows)
    clean_sql = qualified.sql(dialect="sqlite", pretty=True)

    return ValidationResult(is_safe=True, reason="All safety checks passed.", clean_sql=clean_sql, tables=tables)
