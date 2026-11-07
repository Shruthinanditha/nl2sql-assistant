"""
test_app.py - automated UI tests for app.py using Streamlit's AppTest.

AppTest runs the Streamlit script in memory (no browser) and lets us type,
click and read what is on the page. The real LLM is replaced by FakeLLM,
and the audit log goes to a temporary file, so the tests are offline and clean.
"""

from dataclasses import replace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import src.llm_client
import src.pipeline
from src.config import PROJECT_ROOT, settings
from src.llm_client import FakeLLM

pytestmark = pytest.mark.skipif(
    not settings.db_path.exists(), reason="Run `python data/seed_database.py` first."
)

FAKE_ANSWERS = {
    "status": "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status ORDER BY order_count DESC",
    "how many products": "SELECT COUNT(*) AS product_count FROM products",
    "delivery": "CANNOT_ANSWER: There is no delivery data.",
    "evil": "SELECT name FROM customers UNION SELECT password_hash FROM users_private",
}


@pytest.fixture
def app(monkeypatch, tmp_path):
    """A fresh app whose 'AI' is a FakeLLM, logging to a temp file."""
    st.cache_resource.clear()  # forget clients cached by earlier tests
    monkeypatch.setattr(src.llm_client, "get_llm_client", lambda *args, **kwargs: FakeLLM(responses_by_keyword=FAKE_ANSWERS))
    monkeypatch.setattr(src.pipeline, "settings", replace(settings, log_path=tmp_path / "queries.log"))
    test_app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
    test_app.run()
    return test_app


def ask_question(app: AppTest, question: str) -> AppTest:
    app.text_input(key="question_input").input(question)
    app.button(key="ask").click()
    return app.run()


def test_page_loads_with_examples(app):
    assert not app.exception
    assert app.title[0].value.startswith("🔎")
    labels = [b.label for b in app.button]
    assert "Delete all customers" in labels


def test_question_shows_table_chart_and_history(app):
    ask_question(app, "orders per status")
    assert not app.exception
    assert len(app.dataframe) >= 1
    assert list(app.dataframe[0].value["status"]) == ["completed", "cancelled", "pending"]
    assert any("Chart chosen automatically" in c.value for c in app.caption)
    assert any(b.label.startswith("✅ orders per status") for b in app.sidebar.button)


def test_single_number_is_shown_as_metric(app):
    ask_question(app, "how many products do we sell?")
    assert app.metric[0].value == "40"


def test_blocked_query_is_explained(app):
    ask_question(app, "evil question")
    assert "Blocked for safety" in app.error[0].value
    assert "users_private" in app.error[0].value


def test_cannot_answer_is_explained(app):
    ask_question(app, "average delivery time?")
    assert "can't answer" in app.info[0].value


def test_example_button_asks_question(app):
    example = next(b for b in app.button if b.label == "How many orders are there in each status?")
    example.click()
    app.run()
    assert app.text_input(key="question_input").value == "How many orders are there in each status?"
    assert len(app.dataframe) >= 1


def test_safety_lab_blocks_attack_without_llm(app):
    app.text_area(key="lab_sql").input("SELECT 1; DROP TABLE orders")
    app.button(key="lab_run").click()
    app.run()
    assert any("multiple_statements" in e.value for e in app.error)
