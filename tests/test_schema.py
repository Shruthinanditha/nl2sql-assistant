"""
test_schema.py - tests for src/schema.py (reading the schema + the allowlist).

These use the real data/shop.db (read-only), so run `python data/seed_database.py`
once before running the tests. No LLM is involved.
"""

import pytest

from src.config import settings
from src.schema import build_schema, get_schema

# Skip the whole file with a clear message if the database was never created.
pytestmark = pytest.mark.skipif(
    not settings.db_path.exists(), reason="Run `python data/seed_database.py` first."
)


def test_only_allowlisted_tables_are_visible():
    """The four business tables are visible; the sensitive table is not."""
    assert get_schema().allowed_tables() == {"customers", "products", "orders", "order_items"}


def test_sensitive_table_never_appears_in_prompt():
    """users_private and its columns must not be mentioned anywhere in the LLM prompt."""
    prompt_text = get_schema().to_prompt_text().lower()
    for secret in ("users_private", "password_hash", "salary"):
        assert secret not in prompt_text


def test_column_allowlist_hides_email():
    """customers.email exists in the database but is hidden (personal data)."""
    schema = get_schema()
    assert "email" not in schema.allowed_columns()["customers"]
    assert "email" not in schema.to_prompt_text().lower()


def test_sample_values_are_read_from_database():
    """Columns marked show_values list their real values, so the LLM uses exact spellings."""
    status_column = next(c for c in get_schema().tables["orders"].columns if c.name == "status")
    assert status_column.sample_values == ["cancelled", "completed", "pending"]


def test_foreign_keys_are_included():
    """Relationships appear in the prompt so the LLM knows how to join."""
    assert "FOREIGN KEY (customer_id) REFERENCES customers(id)" in get_schema().to_prompt_text()


def test_yaml_typo_is_caught(tmp_path):
    """A column name in the YAML that does not exist in the database raises a clear error."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        "tables:\n  customers:\n    columns:\n      custmer_name: {description: typo}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown column"):
        build_schema(descriptions_path=bad_yaml)
