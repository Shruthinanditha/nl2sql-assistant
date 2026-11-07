"""
llm_client.py - one simple way to talk to ANY large language model (LLM).

WHY THIS FILE EXISTS
    Different AI providers (Google Gemini, Anthropic Claude, a local Ollama
    model) all have different SDKs, request formats and error types. If that
    detail leaked into the rest of the app, switching provider would mean
    rewriting code everywhere.

    So the rest of the app only ever sees ONE function:

        complete(system_prompt, user_prompt) -> str

    Each provider gets a small "adapter" class that translates this simple call
    into that provider's API. The provider is chosen in `.env`
    (LLM_PROVIDER / LLM_MODEL), so switching is a config change, not a code change.

    There is also a FakeLLM: it returns pre-written answers without any network
    call. Tests use it, so they are free, fast, offline and predictable.

HOW IT FITS IN THE PIPELINE
    question -> [sql_generator] -> llm_client.complete(...) -> SQL text -> validator ...

ERRORS
    Every problem (missing key, wrong model name, quota exceeded, timeout,
    provider overloaded) is converted into an LLMError with a plain-English
    message, so the UI can show something helpful instead of a stack trace.
"""

import json
import urllib.error
import urllib.request

from src.config import Settings, settings as default_settings

# Low temperature = the model picks its most likely tokens, so the same question
# gives (almost always) the same SQL. Creativity is a bug when writing SQL.
SQL_TEMPERATURE = 0.0

# Upper limit for the length of the answer. SQL queries are short; the limit is
# generous because "thinking" models count their hidden reasoning tokens too.
MAX_OUTPUT_TOKENS = 8192

# Model used when LLM_MODEL is empty in .env.
# Gemini model chosen by live testing on 2026-11-07 (see docs/PROGRESS.md, Phase 3).
DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "anthropic": "claude-haiku-5-5",
    "ollama": "qwen2.5-coder:7b",
    "fake": "fake-model",
}


class LLMError(Exception):
    """
    A friendly, user-facing error from any LLM provider.

    The message is written for a human (what went wrong + what to do), so the
    UI can display str(error) directly.
    """


# ---------------------------------------------------------------------------
# Base class: the "contract" every provider must follow
# ---------------------------------------------------------------------------
class LLMClient:
    """
    Base class for all providers.

    Subclasses must implement `complete`. After each call, `last_token_usage`
    holds the token counts if the provider reports them (useful for cost
    estimates in the evaluation phase), e.g. {"input": 812, "output": 45}.
    """

    provider_name: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model
        self.last_token_usage: dict[str, int] | None = None

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a system prompt + user prompt to the model and return its text answer.

        Inputs:  system_prompt - standing rules and context (e.g. schema, "only SELECT")
                 user_prompt   - the specific request (e.g. the user's question)
        Output:  the model's reply as plain text
        Raises:  LLMError with a friendly message on any failure
        """
        raise NotImplementedError("Each provider class must implement complete().")


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------
class GeminiClient(LLMClient):
    """Adapter for Google's Gemini API, using the official `google-genai` SDK."""

    provider_name = "gemini"

    def __init__(self, model: str, api_key: str, timeout_seconds: int) -> None:
        super().__init__(model)
        if not api_key:
            raise LLMError(
                "GEMINI_API_KEY is missing. Create a key at https://aistudio.google.com/apikey "
                "and put it in your .env file (GEMINI_API_KEY=...)."
            )

        # Imported here (not at the top of the file) so the app still works for
        # people who use another provider and never installed google-genai.
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                # The SDK expects the timeout in MILLISECONDS.
                timeout=timeout_seconds * 1000,
                # Automatic retries for temporary problems:
                #   429 = too many requests per minute, 500/502/503 = Google overloaded.
                # 504 (timeout) is NOT retried: each attempt already waited the
                # full timeout, so retrying would make the user wait minutes.
                retry_options=types.HttpRetryOptions(
                    attempts=3,            # 1 original try + up to 2 retries
                    initial_delay=2.0,     # wait 2 s, then ~4 s (exponential backoff)
                    max_delay=10.0,
                    http_status_codes=[429, 500, 502, 503],
                ),
            ),
        )

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Call Gemini's generate_content. See LLMClient.complete for details."""
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=SQL_TEMPERATURE,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=user_prompt, config=config
            )
        except errors.APIError as error:
            # The provider answered with an error code -> translate it.
            raise LLMError(_explain_http_error("Gemini", error.code, str(error.message), self.model))
        except Exception as error:
            # No answer at all: network down, or our own timeout fired.
            raise LLMError(_explain_network_error("Gemini", error))

        usage = response.usage_metadata
        if usage is not None:
            self.last_token_usage = {
                "input": usage.prompt_token_count or 0,
                "output": usage.candidates_token_count or 0,
                # "Thinking" models reason privately before answering; those tokens are billed too.
                "thinking": usage.thoughts_token_count or 0,
            }

        # response.text is None when the model returned no text, for example when
        # a safety filter blocked the answer or it ran out of output tokens.
        if not response.text:
            reason = "unknown reason"
            if response.candidates and response.candidates[0].finish_reason:
                reason = str(response.candidates[0].finish_reason)
            raise LLMError(f"Gemini returned an empty answer ({reason}). Try rephrasing the question.")
        return response.text


# ---------------------------------------------------------------------------
# Anthropic Claude
# ---------------------------------------------------------------------------
class AnthropicClient(LLMClient):
    """
    Adapter for Anthropic's Claude API, using the official `anthropic` SDK.

    Not used by default in this project (we use Gemini). To use it:
    `pip install anthropic`, then set LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY in .env.
    """

    provider_name = "anthropic"

    def __init__(self, model: str, api_key: str, timeout_seconds: int) -> None:
        super().__init__(model)
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is missing. Create a key at https://console.anthropic.com "
                "(API usage is billed separately from a Claude subscription) and put it in .env."
            )
        try:
            import anthropic
        except ImportError:
            raise LLMError("The 'anthropic' package is not installed. Run: pip install anthropic")

        self._anthropic = anthropic
        # max_retries: the SDK retries rate-limit and overload errors itself.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=2)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Call Claude's Messages API. See LLMClient.complete for details."""
        anthropic = self._anthropic
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=SQL_TEMPERATURE,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIStatusError as error:
            raise LLMError(_explain_http_error("Claude", error.status_code, str(error.message), self.model))
        except Exception as error:
            raise LLMError(_explain_network_error("Claude", error))

        self.last_token_usage = {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
        }
        # The reply is a list of content blocks; we keep only the text ones.
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text:
            raise LLMError("Claude returned an empty answer. Try rephrasing the question.")
        return text


# ---------------------------------------------------------------------------
# Ollama (local open-source models)
# ---------------------------------------------------------------------------
class OllamaClient(LLMClient):
    """
    Adapter for a local Ollama server (https://ollama.com).

    No SDK is needed: Ollama has a simple HTTP API, so we use Python's built-in
    urllib. Install Ollama, run `ollama pull qwen2.5-coder:7b`, and set
    LLM_PROVIDER=ollama in .env. (Slow on laptops without a dedicated GPU.)
    """

    provider_name = "ollama"

    def __init__(self, model: str, host: str, timeout_seconds: int) -> None:
        super().__init__(model)
        self.host = host.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Call Ollama's /api/chat endpoint. See LLMClient.complete for details."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,  # one complete answer instead of word-by-word streaming
            "options": {"temperature": SQL_TEMPERATURE},
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise LLMError(_explain_http_error("Ollama", error.code, error.read().decode("utf-8", "ignore"), self.model))
        except urllib.error.URLError:
            raise LLMError(
                f"Cannot reach Ollama at {self.host}. Is it installed and running? "
                "(Start the Ollama app, or run 'ollama serve'.)"
            )
        except Exception as error:
            raise LLMError(_explain_network_error("Ollama", error))

        self.last_token_usage = {
            "input": body.get("prompt_eval_count", 0),
            "output": body.get("eval_count", 0),
        }
        text = body.get("message", {}).get("content", "")
        if not text:
            raise LLMError("Ollama returned an empty answer.")
        return text


# ---------------------------------------------------------------------------
# FakeLLM (for tests)
# ---------------------------------------------------------------------------
class FakeLLM(LLMClient):
    """
    A pretend LLM for automated tests: no network, no cost, fully predictable.

    You decide its answers in advance, in one of three ways (checked in this order):
      1. queued_responses: answers returned one by one, in order.
         Useful to simulate "first a broken query, then a fixed one" (repair retry).
      2. responses_by_keyword: if a keyword appears in the user prompt,
         return the matching answer. Example: {"top 5 customers": "SELECT ..."}.
      3. default_response: returned when nothing else matches.

    Every call is recorded in `self.calls`, so a test can check what was sent.
    """

    provider_name = "fake"

    def __init__(
        self,
        queued_responses: list[str] | None = None,
        responses_by_keyword: dict[str, str] | None = None,
        default_response: str = "SELECT 1;",
    ) -> None:
        super().__init__(DEFAULT_MODELS["fake"])
        # list(...) copies the list, so we never modify the caller's list.
        self.queued_responses = list(queued_responses or [])
        self.responses_by_keyword = responses_by_keyword or {}
        self.default_response = default_response
        self.calls: list[dict[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return a pre-written answer. See the class docstring for the order of rules."""
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        self.last_token_usage = {"input": 0, "output": 0}

        if self.queued_responses:
            return self.queued_responses.pop(0)

        lowered_prompt = user_prompt.lower()
        for keyword, answer in self.responses_by_keyword.items():
            if keyword.lower() in lowered_prompt:
                return answer

        return self.default_response


# ---------------------------------------------------------------------------
# Error translation helpers
# ---------------------------------------------------------------------------
def _explain_http_error(provider: str, status_code: int | None, detail: str, model: str) -> str:
    """
    Turn an HTTP error code from a provider into a plain-English message.

    Inputs:  provider    - display name, e.g. "Gemini"
             status_code - the HTTP status code (e.g. 401, 404, 429)
             detail      - the provider's own error text (shown shortened)
             model       - the model name we tried (helps with 404s)
    Output:  a message explaining what went wrong and what to do
    """
    short_detail = " ".join(detail.split())[:200]
    lowered = short_detail.lower()

    if status_code in (401, 403) or "api key" in lowered:
        advice = "The API key was rejected. Check the key in your .env file (no spaces or quotes)."
    elif status_code == 404:
        advice = f"Model '{model}' was not found or is retired. Change LLM_MODEL in .env."
    elif status_code == 429:
        advice = ("Rate limit or quota exceeded (too many requests). Wait a minute and try again; "
                  "free tiers also have a daily limit.")
    elif status_code in (500, 502, 503):
        advice = "The provider is overloaded or having problems. Try again in a minute, or use another model."
    elif status_code in (408, 504):
        advice = "The provider took too long to answer. Try again, or use a faster model."
    else:
        advice = "The request was rejected."

    return f"{provider} error ({status_code}): {advice} [details: {short_detail}]"


def _explain_network_error(provider: str, error: Exception) -> str:
    """
    Explain failures where no HTTP answer arrived at all (timeout, no internet).

    We look at the exception's class name, because each SDK uses its own
    exception classes (e.g. httpx.ReadTimeout, anthropic.APITimeoutError).
    """
    error_type = type(error).__name__
    if "timeout" in error_type.lower():
        return (f"{provider} did not answer in time ({error_type}). Try again, or raise "
                "LLM_TIMEOUT_SECONDS in .env.")
    if "connect" in error_type.lower():
        return f"Could not connect to {provider} ({error_type}). Check your internet connection."
    return f"Unexpected error calling {provider}: {error_type}: {error}"


# ---------------------------------------------------------------------------
# Factory: build the right client from the settings
# ---------------------------------------------------------------------------
def get_llm_client(app_settings: Settings = default_settings) -> LLMClient:
    """
    Create the LLM client selected in .env.

    Input:  app_settings - the Settings object (tests can pass a modified copy)
    Output: an LLMClient subclass instance, ready to call .complete(...)
    Raises: LLMError if the provider is misconfigured (e.g. missing API key)
    """
    provider = app_settings.llm_provider
    # Fall back to a sensible default model if LLM_MODEL is empty.
    model = app_settings.llm_model or DEFAULT_MODELS[provider]
    timeout = app_settings.llm_timeout_seconds

    if provider == "gemini":
        return GeminiClient(model, app_settings.gemini_api_key, timeout)
    if provider == "anthropic":
        return AnthropicClient(model, app_settings.anthropic_api_key, timeout)
    if provider == "ollama":
        return OllamaClient(model, app_settings.ollama_host, timeout)
    if provider == "fake":
        return FakeLLM()
    # config.py already validates the provider, so this is a safety net only.
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'.")


def complete(system_prompt: str, user_prompt: str) -> str:
    """
    Convenience function: build the configured client and make one call.

    For many calls, create the client once with get_llm_client() and reuse it.
    """
    return get_llm_client().complete(system_prompt, user_prompt)
