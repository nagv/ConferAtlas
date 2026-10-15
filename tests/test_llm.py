"""Tests for core/ai/llm.py: retries, backoff, friendly errors, usage tracking and model setup (offline)."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from core.ai import llm
from core.config import Settings, get_settings

SETTINGS = Settings("anthropic", "claude-opus-5-5", Path("."), llm_timeout_s=30, llm_max_retries=2,
                    llm_max_output_tokens=1024, input_price_per_mtok=5.0, output_price_per_mtok=25.0)


class APITimeoutError(Exception):
    pass


class RateLimitError(Exception):
    status_code = 429


class AuthenticationError(Exception):
    status_code = 401


class StatusError(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


class _Response:
    def __init__(self, headers):
        self.headers, self.status_code = headers, 429


class RetryAfterError(Exception):
    def __init__(self, seconds):
        super().__init__("slow down")
        self.response = _Response({"retry-after": str(seconds)})


def _raise(exc):
    raise exc


# --- classification and messages --------------------------------------------------------

@pytest.mark.parametrize("exc, kind", [
    (APITimeoutError(), "timeout"), (TimeoutError(), "timeout"), (RateLimitError(), "rate_limit"),
    (AuthenticationError(), "auth"), (StatusError(403), "auth"), (StatusError(404), "not_found"),
    (StatusError(529), "overloaded"), (StatusError(500), "overloaded"), (StatusError(400), "bad_request"),
    (ConnectionError("down"), "network"), (ValueError("odd"), "unknown"),
])
def test_classify_error(exc, kind):
    assert llm.classify_error(exc) == kind


def test_friendly_messages_say_what_to_do():
    def msg(kind, attempts=1):
        return llm.friendly_message(kind, Exception("raw detail"), SETTINGS, attempts)
    assert "ANTHROPIC_API_KEY" in msg("auth") and ".env" in msg("auth")
    assert "claude-opus-5-5" in msg("not_found") and "LLM_MODEL" in msg("not_found")
    assert "30 seconds (tried 3 times)" in msg("timeout", 3)
    assert "Wait a minute" in msg("rate_limit") and "internet connection" in msg("network")
    assert "raw detail" in msg("bad_request") and "raw detail" in msg("unknown")


# --- backoff and retries -----------------------------------------------------------------

def test_backoff_is_exponential_with_jitter_and_capped():
    assert [llm.backoff_delay(n, rng=lambda: 1.0) for n in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert llm.backoff_delay(3, rng=lambda: 0.0) == 2.0  # jitter: between half and the full delay


def test_backoff_honours_retry_after():
    assert llm.backoff_delay(1, RetryAfterError(3)) == 3.0
    assert llm.backoff_delay(1, RetryAfterError(120)) == llm.BACKOFF_CAP_S


def test_retry_call_retries_then_succeeds():
    outcomes, sleeps = [APITimeoutError(), RateLimitError(), "ok"], []

    def fn():
        result = outcomes.pop(0)
        return _raise(result) if isinstance(result, Exception) else result
    assert llm.retry_call(fn, SETTINGS, sleep=sleeps.append) == "ok"
    assert len(sleeps) == 2 and all(0 < s <= llm.BACKOFF_CAP_S for s in sleeps)


def test_retry_call_gives_up_with_friendly_error():
    sleeps = []
    with pytest.raises(llm.LLMError) as exc:
        llm.retry_call(lambda: _raise(APITimeoutError("read timed out")), SETTINGS, sleep=sleeps.append)
    assert exc.value.kind == "timeout" and exc.value.attempts == 3 and len(sleeps) == 2
    assert "tried 3 times" in str(exc.value)


def test_non_retryable_errors_fail_immediately():
    calls = []

    def bad_key():
        calls.append(1)
        raise AuthenticationError("invalid x-api-key")
    with pytest.raises(llm.LLMError) as exc:
        llm.retry_call(bad_key, SETTINGS, sleep=lambda s: None)
    assert exc.value.kind == "auth" and len(calls) == 1


def test_zero_retries_setting():
    with pytest.raises(llm.LLMError) as exc:
        llm.retry_call(lambda: _raise(APITimeoutError()), replace(SETTINGS, llm_max_retries=0),
                       sleep=lambda s: pytest.fail("should not sleep"))
    assert exc.value.attempts == 1


# --- usage tracking ------------------------------------------------------------------------

def test_estimate_cost():
    assert llm.estimate_cost(1_000_000, 100_000, SETTINGS) == pytest.approx(7.5)
    assert llm.estimate_cost(1000, None, SETTINGS) is None
    assert llm.estimate_cost(1000, 100, replace(SETTINGS, input_price_per_mtok=None)) is None


def test_recorder_totals_and_cap():
    rec = llm.UsageRecorder()
    for _ in range(llm.MAX_STORED_CALLS + 5):
        rec.add(llm.CallRecord(datetime.now(), "x", "m", 100, 10, 0.001, 0.5, "ok"))
    rec.add(llm.CallRecord(datetime.now(), "x", "m", None, None, None, 2.0, "error", "timeout"))
    assert len(rec.calls) == llm.MAX_STORED_CALLS  # old rows roll off...
    assert rec.total_calls == llm.MAX_STORED_CALLS + 6 and rec.total_errors == 1  # ...totals do not
    assert rec.total_input_tokens == 100 * (llm.MAX_STORED_CALLS + 5)
    assert rec.cost_known and rec.total_cost_usd == pytest.approx(0.001 * (llm.MAX_STORED_CALLS + 5))
    rec.add(llm.CallRecord(datetime.now(), "x", "m", 5, 5, None, 0.1, "ok"))
    assert not rec.cost_known  # a successful call without a cost makes the total unknown


def test_callback_records_successful_and_failed_calls():
    rec = llm.UsageRecorder()
    ticks = iter([10.0, 11.5])
    reply = AIMessage(content="hi", usage_metadata={"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500})
    model = GenericFakeChatModel(messages=iter([reply]),
                                 callbacks=[llm.make_usage_callback(rec, SETTINGS, "Test", clock=lambda: next(ticks))])
    model.invoke("hello")
    call = rec.calls[0]
    assert (call.purpose, call.input_tokens, call.output_tokens, call.status) == ("Test", 1200, 300, "ok")
    assert call.cost_usd == pytest.approx(0.0135) and call.latency_s == pytest.approx(1.5)

    failing = GenericFakeChatModel(messages=iter([]), callbacks=[llm.make_usage_callback(rec, SETTINGS, "Test")])
    with pytest.raises(Exception):
        failing.invoke("hello")
    assert rec.calls[-1].status == "error" and rec.total_errors == 1


# --- model setup ------------------------------------------------------------------------------

def test_build_chat_model_needs_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.LLMNotConfiguredError, match="ANTHROPIC_API_KEY"):
        llm.build_chat_model(SETTINGS, "Test")


def test_build_chat_model_applies_limits(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    model = llm.build_chat_model(SETTINGS, "Test", llm.UsageRecorder())
    assert model.default_request_timeout == 30 and model.max_retries == 0 and model.max_tokens == 1024
    assert len(model.callbacks) == 1
    assert llm.build_chat_model(SETTINGS, "Test", max_tokens=4096, timeout_s=90).max_tokens == 4096


def test_unsupported_provider_message():
    assert "not support" in llm.missing_key_message(replace(SETTINGS, llm_provider="acme"))


def test_settings_from_env(monkeypatch):
    for name, value in {"LLM_TIMEOUT_SECONDS": "15", "LLM_MAX_RETRIES": "4", "LLM_MAX_OUTPUT_TOKENS": "oops",
                        "LLM_INPUT_PRICE_PER_MTOK": "3", "LLM_OUTPUT_PRICE_PER_MTOK": ""}.items():
        monkeypatch.setenv(name, value)
    s = get_settings()
    assert (s.llm_timeout_s, s.llm_max_retries, s.llm_max_output_tokens) == (15.0, 4, 1000)  # typo -> default
    assert s.input_price_per_mtok == 3.0 and s.output_price_per_mtok is None


@pytest.mark.parametrize("value, expected", [
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
    (" https://openrouter.ai/api/v1/ ", "https://openrouter.ai/api/v1"),
    ("https://router.example.test/api/v1", "https://router.example.test/api/v1"),
    ("", "https://openrouter.ai/api/v1"),
])
def test_openrouter_base_url_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("OPENROUTER_BASE_URL", value)
    assert get_settings().base_url == expected


def test_openrouter_model_uses_configured_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://router.example.test/api/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    settings = get_settings()
    model = llm.build_chat_model(settings, "OpenRouter test", llm.UsageRecorder())
    assert settings.llm_provider == "openrouter"
    assert model.model_name == "google/gemini-2.5-flash"
    assert model.openai_api_base == "https://router.example.test/api/v1"
    assert model.openai_api_key.get_secret_value() == "sk-or-test"
    assert model.max_tokens == 1000 and model.max_retries == 0
    assert model.request_timeout == 60 and model.temperature == 0
    assert len(model.callbacks) == 1


def test_google_genai_model_uses_bare_id_key_and_no_thinking(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google_genai")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "unrelated-key")
    settings = get_settings()  # LLM_MODEL unset, so the OpenRouter-style default id is used
    assert settings.api_key_env_var == "GOOGLE_API_KEY" and settings.llm_configured and settings.base_url is None
    model = llm.build_chat_model(settings, "Google test", llm.UsageRecorder())
    assert model.model.removeprefix("models/") == "gemini-2.5-flash"
    assert model.google_api_key.get_secret_value() == "google-test-key"
    assert model.thinking_budget == 0 and model.max_output_tokens == 1000 and model.max_retries == 0
    assert model.timeout == 60 and model.temperature == 0
    assert len(model.callbacks) == 1


@pytest.mark.parametrize("model", ["gemini-2.5-flash", "google/gemini-2.5-flash", "gemini/gemini-2.5-flash"])
def test_google_model_id_accepts_prefixed_ids(model):
    assert llm.google_model_id(model) == "gemini-2.5-flash"


def test_not_found_on_openrouter_points_at_the_provider():
    settings = replace(SETTINGS, llm_provider="openrouter", llm_model="gemini/gemini-2.5-flash")
    message = llm.friendly_message("not_found", Exception("404"), settings, 1)
    assert "gemini/gemini-2.5-flash" in message and "LLM_PROVIDER=google_genai" in message


def test_google_genai_missing_key_is_explained(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google_genai")
    with pytest.raises(llm.LLMNotConfiguredError, match="GOOGLE_API_KEY"):
        llm.build_chat_model(get_settings(), "Google test")


class ResourceExhausted(Exception):  # shaped like google.api_core.exceptions.ResourceExhausted
    code = 429


class GoogleClientError(Exception):  # shaped like google.genai.errors.ClientError
    def __init__(self, code):
        super().__init__(f"{code} error")
        self.code = code


class PermissionDenied(Exception):
    pass


class DeadlineExceeded(Exception):
    pass


@pytest.mark.parametrize("exc, kind", [
    (ResourceExhausted(), "rate_limit"), (GoogleClientError(429), "rate_limit"), (GoogleClientError(403), "auth"),
    (GoogleClientError(400), "bad_request"), (GoogleClientError(503), "overloaded"), (PermissionDenied(), "auth"),
    (DeadlineExceeded(), "timeout"),
])
def test_classify_google_errors(exc, kind):
    assert llm.classify_error(exc) == kind


def test_google_rate_limit_message_mentions_free_tier():
    settings = replace(SETTINGS, llm_provider="google_genai")
    assert "free tier" in llm.friendly_message("rate_limit", ResourceExhausted(), settings, 3)
    assert "free tier" not in llm.friendly_message("rate_limit", ResourceExhausted(), SETTINGS, 3)


def test_openrouter_missing_key_does_not_use_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    with pytest.raises(llm.LLMNotConfiguredError, match="OPENROUTER_API_KEY"):
        llm.build_chat_model(get_settings(), "OpenRouter test")


def test_openrouter_insufficient_credit_is_not_retried():
    settings = replace(SETTINGS, llm_provider="openrouter")
    attempts = []
    def request():
        attempts.append(1)
        raise StatusError(402)
    with pytest.raises(llm.LLMError) as exc:
        llm.retry_call(request, settings, sleep=lambda _: None)
    assert exc.value.kind == "credits"
    assert len(attempts) == 1


def test_openrouter_insufficient_credit_is_explained_and_not_retried():
    sleeps = []
    with pytest.raises(llm.LLMError) as exc:
        llm.retry_call(lambda: _raise(StatusError(402)), SETTINGS, sleep=sleeps.append)
    assert exc.value.kind == "credits" and sleeps == []  # retrying would not help
    assert "enough credit" in str(exc.value) and "LLM_MAX_OUTPUT_TOKENS" in str(exc.value)
