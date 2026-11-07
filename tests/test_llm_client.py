"""
test_llm_client.py - tests for src/llm_client.py.

These tests NEVER call a real AI provider: they use FakeLLM, or they check
behaviour that happens before any network call (like a missing API key).
So they are free, fast and work offline.

Run with:  pytest -v
"""

from dataclasses import replace

import pytest

from src.config import settings
from src.llm_client import (
    DEFAULT_MODELS,
    FakeLLM,
    GeminiClient,
    LLMError,
    _explain_http_error,
    get_llm_client,
)


def test_fake_llm_returns_default_response():
    """With no rules configured, FakeLLM returns its default answer."""
    fake = FakeLLM(default_response="SELECT 42;")
    assert fake.complete("system", "any question") == "SELECT 42;"


def test_fake_llm_queued_responses_come_in_order_then_default():
    """Queued answers are used one by one, then it falls back to the default."""
    fake = FakeLLM(queued_responses=["first", "second"], default_response="default")
    assert fake.complete("s", "q") == "first"
    assert fake.complete("s", "q") == "second"
    assert fake.complete("s", "q") == "default"


def test_fake_llm_keyword_match_is_case_insensitive():
    """A keyword rule matches regardless of upper/lower case in the question."""
    fake = FakeLLM(responses_by_keyword={"top customers": "SELECT name FROM customers;"})
    assert fake.complete("s", "Show me the TOP CUSTOMERS please") == "SELECT name FROM customers;"


def test_fake_llm_records_calls():
    """Tests can inspect exactly what was sent to the LLM."""
    fake = FakeLLM()
    fake.complete("the rules", "the question")
    assert fake.calls == [{"system_prompt": "the rules", "user_prompt": "the question"}]


def test_factory_returns_fake_llm_for_fake_provider():
    """LLM_PROVIDER=fake gives a FakeLLM (no key needed)."""
    fake_settings = replace(settings, llm_provider="fake")
    assert isinstance(get_llm_client(fake_settings), FakeLLM)


def test_factory_uses_default_model_when_model_empty():
    """An empty LLM_MODEL falls back to the provider's default model."""
    gemini_settings = replace(settings, llm_provider="gemini", llm_model="", gemini_api_key="dummy-key")
    client = get_llm_client(gemini_settings)
    assert client.model == DEFAULT_MODELS["gemini"]


def test_missing_gemini_key_gives_friendly_error():
    """A missing key fails immediately with advice, before any network call."""
    with pytest.raises(LLMError, match="GEMINI_API_KEY is missing"):
        GeminiClient(model="any-model", api_key="", timeout_seconds=5)


@pytest.mark.parametrize(
    "status_code, expected_phrase",
    [
        (401, "API key was rejected"),
        (404, "not found or is retired"),
        (429, "Rate limit or quota exceeded"),
        (503, "overloaded"),
        (504, "took too long"),
    ],
)
def test_http_errors_are_explained_in_plain_english(status_code, expected_phrase):
    """Each common HTTP error code maps to a helpful explanation."""
    message = _explain_http_error("Gemini", status_code, "raw provider text", "some-model")
    assert expected_phrase in message
