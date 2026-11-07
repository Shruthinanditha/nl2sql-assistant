"""
schema.py - builds the description of the database that the LLM is allowed to see.

WHY THIS FILE EXISTS
    The LLM writes SQL only from what we tell it about the database. If we
    tell it too little, it guesses column names (wrong SQL). If we tell it
    too much, it may query sensitive data. This module gets that balance right:

    1. It READS the real structure from SQLite (tables, columns, types,
       primary/foreign keys) using PRAGMA commands. The database is the source
       of truth, so the prompt never describes a column that does not exist.
    2. It MERGES human-written knowledge from data/schema_descriptions.yaml
       (meaning, units, business rules, example values).
    3. It applies the ALLOWLIST: only tables/columns listed in the YAML file
       are kept. Everything else (users_private, customers.email) is dropped
       and will never appear in the prompt.

    The same allowlist is used again by the SQL validator (Phase 5) to block
    any query that mentions a non-allowed table, even if the LLM invents it.

HOW IT FITS IN THE PIPELINE
    shop.db + YAML -> [schema.py] -> schema text -> prompts.py -> LLM
                                  -> allowlist   -> sql_validator.py
"""

import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from src.config import PROJECT_ROOT, settings

DESCRIPTIONS_PATH: Path = PROJECT_ROOT / "data" / "schema_descriptions.yaml"

# Never show more than this many distinct example values for one column
# (keeps the prompt short; a city list is useful, 3,000 names are not).
MAX_SAMPLE_VALUES = 15


@dataclass
class ColumnInfo:
    """One allowed column: real structure from SQLite + description from YAML."""

    name: str
    data_type: str
    description: str
    is_primary_key: bool = False
    not_null: bool = False
    sample_values: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    """A relationship: this table's `column` points to `ref_table.ref_column`."""

    column: str
    ref_table: str
    ref_column: str


@dataclass
class TableInfo:
    """One allowed table with its allowed columns and relationships."""

    name: str
    description: str
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKey] = field(default_factory=list)


@dataclass
class Schema:
    """
    The complete allowed view of the database.

    `tables` maps table name -> TableInfo. Only allowlisted tables/columns are in it.
    """

    database_description: str
    notes: list[str]
    tables: dict[str, TableInfo]

    def allowed_tables(self) -> set[str]:
        """Return the allowed table names (lower case, for case-insensitive checks)."""
        return {name.lower() for name in self.tables}

    def allowed_columns(self) -> dict[str, set[str]]:
        """Return {table_name: {allowed column names}}, all lower case."""
        return {
            name.lower(): {column.name.lower() for column in table.columns}
            for name, table in self.tables.items()
        }

    def to_prompt_text(self) -> str:
        """
        Format the schema as text for the LLM.

        We use CREATE TABLE syntax with SQL comments. LLMs have seen millions of
        CREATE TABLE statements, so this format is compact and easy for them to
        read, and the comments carry the human meaning of each column.
        """
        lines: list[str] = [f"-- {self.database_description.strip()}", ""]
        for table in self.tables.values():
            lines.append(f"-- {table.description}")
            lines.append(f"CREATE TABLE {table.name} (")

            column_lines = []
            for column in table.columns:
                definition = f"    {column.name} {column.data_type}"
                if column.is_primary_key:
                    definition += " PRIMARY KEY"
                elif column.not_null:
                    definition += " NOT NULL"
                comment = column.description
                if column.sample_values:
                    quoted = ", ".join(f"'{value}'" for value in column.sample_values)
                    comment += f" Values: {quoted}."
                column_lines.append((definition, comment))

            # Foreign keys go at the end of the table definition, like real DDL.
            fk_lines = [
                f"    FOREIGN KEY ({fk.column}) REFERENCES {fk.ref_table}({fk.ref_column})"
                for fk in table.foreign_keys
            ]

            # Join with commas; the comment goes after the comma on each line.
            all_items = [d for d, _ in column_lines] + fk_lines
            for index, (definition, comment) in enumerate(column_lines):
                comma = "," if index < len(all_items) - 1 else ""
                lines.append(f"{definition}{comma}  -- {comment}")
            for index, fk_line in enumerate(fk_lines):
                comma = "," if len(column_lines) + index < len(all_items) - 1 else ""
                lines.append(f"{fk_line}{comma}")
            lines.append(");")
            lines.append("")

        if self.notes:
            lines.append("-- Business rules:")
            lines.extend(f"-- * {note}" for note in self.notes)
        return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Reading the pieces
# ---------------------------------------------------------------------------
def load_descriptions(path: Path = DESCRIPTIONS_PATH) -> dict:
    """
    Read the YAML file with descriptions + allowlist.

    yaml.safe_load is used (never yaml.load): safe_load only creates plain data
    (dicts, lists, strings), so a malicious YAML file cannot run code.
    """
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """
    Open the SQLite database in READ-ONLY mode.

    The "file:...?mode=ro" URI tells SQLite itself to refuse any write.
    Reading the schema never needs to write, so we never ask for write access.
    """
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. Create it with: python data/seed_database.py"
        )
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def read_table_structure(connection: sqlite3.Connection, table: str) -> tuple[list[dict], list[ForeignKey]]:
    """
    Read the real columns and foreign keys of one table with PRAGMA commands.

    PRAGMA table_info(t)        -> one row per column: (cid, name, type, notnull, default, pk)
    PRAGMA foreign_key_list(t)  -> one row per foreign key: (..., table, from, to, ...)

    Output: (list of column dicts, list of ForeignKey)
    """
    # The table name comes from our own YAML/database, never from user input.
    # PRAGMA does not support "?" parameters, so we quote the name instead.
    quoted = '"' + table.replace('"', '""') + '"'

    columns = [
        {"name": row[1], "type": row[2], "not_null": bool(row[3]), "pk": bool(row[5])}
        for row in connection.execute(f"PRAGMA table_info({quoted})")
    ]
    foreign_keys = [
        ForeignKey(column=row[3], ref_table=row[2], ref_column=row[4])
        for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")
    ]
    return columns, foreign_keys


def read_sample_values(connection: sqlite3.Connection, table: str, column: str) -> list[str]:
    """
    Return up to MAX_SAMPLE_VALUES distinct non-NULL values of a column, sorted.

    Showing real values (e.g. status = 'completed') stops the LLM from guessing
    'Completed' or 'COMPLETE', which would silently return zero rows.
    """
    quoted_table = '"' + table.replace('"', '""') + '"'
    quoted_column = '"' + column.replace('"', '""') + '"'
    rows = connection.execute(
        f"SELECT DISTINCT {quoted_column} FROM {quoted_table} "
        f"WHERE {quoted_column} IS NOT NULL ORDER BY 1 LIMIT {MAX_SAMPLE_VALUES}"
    ).fetchall()
    return [str(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------
def build_schema(db_path: Path | None = None, descriptions_path: Path = DESCRIPTIONS_PATH) -> Schema:
    """
    Combine the real database structure with the YAML descriptions + allowlist.

    Inputs:  db_path           - SQLite file (defaults to DB_PATH from .env)
             descriptions_path - the YAML file
    Output:  a Schema containing ONLY allowlisted tables and columns
    Raises:  ValueError if the YAML mentions a table/column that does not exist
             in the database (a typo there would silently mislead the LLM)
    """
    db_path = db_path or settings.db_path
    descriptions = load_descriptions(descriptions_path)
    yaml_tables: dict = descriptions.get("tables") or {}

    connection = open_read_only(db_path)
    try:
        real_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

        tables: dict[str, TableInfo] = {}
        for table_name, table_yaml in yaml_tables.items():
            if table_name not in real_tables:
                raise ValueError(f"schema_descriptions.yaml lists table '{table_name}', which is not in the database.")

            real_columns, foreign_keys = read_table_structure(connection, table_name)
            real_by_name = {column["name"]: column for column in real_columns}
            yaml_columns: dict = table_yaml.get("columns") or {}

            columns: list[ColumnInfo] = []
            # Keep the database's column order, but only the allowlisted columns.
            for real_column in real_columns:
                if real_column["name"] not in yaml_columns:
                    continue  # not allowlisted -> hidden from the LLM
                column_yaml = yaml_columns[real_column["name"]] or {}
                samples = []
                if column_yaml.get("show_values"):
                    samples = read_sample_values(connection, table_name, real_column["name"])
                columns.append(
                    ColumnInfo(
                        name=real_column["name"],
                        data_type=real_column["type"],
                        description=column_yaml.get("description", ""),
                        is_primary_key=real_column["pk"],
                        not_null=real_column["not_null"],
                        sample_values=samples,
                    )
                )

            # Catch typos in the YAML (e.g. "custmer_id").
            unknown = set(yaml_columns) - set(real_by_name)
            if unknown:
                raise ValueError(
                    f"schema_descriptions.yaml lists unknown column(s) {sorted(unknown)} for table '{table_name}'."
                )

            allowed_names = {column.name for column in columns}
            # Only keep relationships whose both ends are visible to the LLM.
            visible_fks = [
                fk for fk in foreign_keys
                if fk.column in allowed_names and fk.ref_table in yaml_tables
            ]
            tables[table_name] = TableInfo(
                name=table_name,
                description=table_yaml.get("description", ""),
                columns=columns,
                foreign_keys=visible_fks,
            )
    finally:
        connection.close()

    return Schema(
        database_description=descriptions.get("database_description", ""),
        notes=descriptions.get("notes") or [],
        tables=tables,
    )


@lru_cache(maxsize=1)
def get_schema() -> Schema:
    """
    Return the schema for the configured database, built only once.

    lru_cache remembers the result, so the database and YAML are read on the
    first call only (the schema does not change while the app runs).
    """
    return build_schema()
