"""One place for every LLM call: model setup, retries, friendly errors and usage tracking.

- ``build_chat_model`` creates the LangChain chat model with the request
  timeout and output-token cap from settings, the provider's own retries
  switched off (``retry_call`` does them, so attempts are never doubled), and
  a callback that records each call in a ``UsageRecorder``.
- ``retry_call`` runs a call, retrying timeouts, rate limits, overloads and
  network errors with exponential backoff and jitter (or the provider's
  Retry-After). Anything else, or the last failed attempt, becomes an
  ``LLMError`` whose message tells the user what to do.
- ``UsageRecorder`` keeps, per app session, one ``CallRecord`` per attempt:
  purpose, model, input/output tokens, estimated cost, latency and status.
  The app stores one in ``st.session_state`` and shows it in the sidebar.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from core.config import Settings, get_settings
from shared.usage import MAX_STORED_CALLS, CallRecord, UsageRecorder  # noqa: F401

RETRYABLE = {"timeout", "rate_limit", "overloaded", "network"}
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 8.0


class LLMNotConfiguredError(RuntimeError):
    """Raised when no API key (or provider package) is available for the configured provider."""


def resolve_settings(settings: Settings | None, injected_model) -> Settings:
    """Settings for a call. When a test injects a fake model without settings, retries are off so
    scripted replies are used exactly once."""
    if settings is not None:
        return settings
    from dataclasses import replace
    base = get_settings()
    return replace(base, llm_max_retries=0) if injected_model is not None else base


# --- usage tracking ---------------------------------------------------------------

def estimate_cost(input_tokens: int | None, output_tokens: int | None, settings: Settings) -> float | None:
    """USD from the per-million-token prices in settings; None when prices or counts are unknown."""
    if settings.input_price_per_mtok is None or settings.output_price_per_mtok is None:
        return None
    if input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * settings.input_price_per_mtok + output_tokens * settings.output_price_per_mtok) / 1e6


def _usage_from(response: Any) -> tuple[int | None, int | None]:
    """Token counts from a LangChain LLMResult (usage_metadata first, then provider llm_output)."""
    try:
        message = response.generations[0][0].message
        usage = getattr(message, "usage_metadata", None)
        if usage:
            return usage.get("input_tokens"), usage.get("output_tokens")
    except (AttributeError, IndexError, TypeError):
        pass
    raw = (getattr(response, "llm_output", None) or {}).get("usage") or {}
    return raw.get("input_tokens"), raw.get("output_tokens")


def make_usage_callback(recorder: UsageRecorder, settings: Settings, purpose: str,
                        clock: Callable[[], float] = time.monotonic):
    """A LangChain callback handler that records latency, tokens and cost for each model call."""
    from langchain_core.callbacks import BaseCallbackHandler

    class UsageCallback(BaseCallbackHandler):
        def __init__(self):
            self._started: dict = {}

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            self._started[run_id] = (clock(), datetime.now())

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            self._started[run_id] = (clock(), datetime.now())

        def _start_of(self, run_id):
            started = self._started.pop(run_id, None)  # not pop(key, default): the default would be evaluated
            return started if started is not None else (clock(), datetime.now())

        def on_llm_end(self, response, *, run_id, **kwargs):
            start, when = self._start_of(run_id)
            tokens_in, tokens_out = _usage_from(response)
            recorder.add(CallRecord(when, purpose, settings.llm_model, tokens_in, tokens_out,
                                    estimate_cost(tokens_in, tokens_out, settings), clock() - start, "ok"))

        def on_llm_error(self, error, *, run_id, **kwargs):
            start, when = self._start_of(run_id)
            recorder.add(CallRecord(when, purpose, settings.llm_model, None, None, None, clock() - start,
                                    "error", classify_error(error)))

    return UsageCallback()


# --- model --------------------------------------------------------------------------

def build_chat_model(settings: Settings, purpose: str, recorder: UsageRecorder | None = None,
                     max_tokens: int | None = None, timeout_s: float | None = None):
    """The configured chat model with timeout, output cap and usage tracking applied."""
    if not settings.llm_configured:
        raise LLMNotConfiguredError(missing_key_message(settings))
    try:
        from langchain.chat_models import init_chat_model

        callbacks = [make_usage_callback(recorder, settings, purpose)] if recorder is not None else None
        common = dict(
            timeout=timeout_s or settings.llm_timeout_s,
            max_retries=0,  # retry_call does retries with backoff, so attempts are never doubled
            # Always cap output: without it OpenRouter reserves the model's full output budget
            # and can return 402 on low-credit accounts.
            max_tokens=max_tokens or settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
            callbacks=callbacks,
        )
        if settings.llm_provider == "openrouter":
            # OpenRouter speaks the OpenAI API, so it is the "openai" provider with another base URL.
            return init_chat_model(model=settings.llm_model, model_provider="openai", base_url=settings.base_url,
                                   api_key=settings.api_key, default_headers={"X-Title": "ConferAtlas"}, **common)
        if settings.llm_provider in ("nebius", "fireworks"):
            # Also OpenAI-compatible, at the base URL from settings.
            return init_chat_model(model=settings.llm_model, model_provider="openai", base_url=settings.base_url,
                                   api_key=settings.api_key, **common)
        if settings.llm_provider == "google_genai":
            # Google's API uses bare ids ("gemini-2.5-flash"), so a "google/" (OpenRouter) or "gemini/" (LiteLLM)
            # prefix is dropped. Gemini 2.5 Flash thinks by default and thinking tokens count against max_tokens,
            # which can truncate structured output; thinking_budget=0 turns it off (as with the OpenRouter default).
            return init_chat_model(google_model_id(settings.llm_model), model_provider="google_genai",
                                   api_key=settings.api_key, thinking_budget=0, **common)
        return init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **common)
    except ImportError as exc:
        package = {"anthropic": "langchain-anthropic", "openai": "langchain-openai",
                   "openrouter": "langchain-openai", "nebius": "langchain-openai", "fireworks": "langchain-openai",
                   "google_genai": "langchain-google-genai"}.get(settings.llm_provider, "the provider package")
        raise LLMNotConfiguredError(f"The '{settings.llm_provider}' provider needs {package}. Install it with "
                                    f"`pip install {package}` and restart the app.") from exc


def google_model_id(model: str) -> str:
    """'gemini-2.5-flash' from 'google/gemini-2.5-flash', 'gemini/gemini-2.5-flash' or 'gemini-2.5-flash'."""
    for prefix in ("google/", "gemini/"):
        model = model.removeprefix(prefix)
    return model


def structured_output_method(settings: Settings) -> str:
    """'json_schema' for Anthropic (forced tool calling is unsupported on some Claude models);
    'function_calling' for OpenAI-compatible providers such as OpenRouter, where tool calling is
    supported by the widest range of models (including Gemini)."""
    return "json_schema" if settings.llm_provider == "anthropic" else "function_calling"


def missing_key_message(settings: Settings) -> str:
    var = settings.api_key_env_var
    if var is None:
        return (f"LLM_PROVIDER is set to '{settings.llm_provider}', which ConferAtlas does not support. "
                "Use 'openrouter', 'google_genai', 'nebius', 'fireworks', 'anthropic' or 'openai' in .env.")
    if settings.llm_provider in ("nebius", "fireworks") and not settings.base_url:
        return (f"No base URL: set {settings.llm_provider.upper()}_BASE_URL in backend/.env to the OpenAI-compatible "
                "endpoint your provider's console shows, then restart the app.")
    return f"No API key: set {var} in the .env file in the project folder (see .env.example), then restart the app."


# --- errors and retries ----------------------------------------------------------------

class LLMError(RuntimeError):
    """A failed LLM call with a message written for the user. ``kind`` says what went wrong."""

    def __init__(self, kind: str, message: str, attempts: int = 1):
        self.kind, self.attempts = kind, attempts
        super().__init__(message)


def classify_error(exc: BaseException) -> str:
    """'auth', 'not_found', 'bad_request', 'rate_limit', 'overloaded', 'timeout', 'network' or 'unknown'."""
    name = type(exc).__name__.lower()
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:  # Google's client errors carry the HTTP status in .code instead
        code = getattr(exc, "code", None)
        status = int(code) if isinstance(code, int) else None
    if isinstance(exc, TimeoutError) or "timeout" in name or "deadlineexceeded" in name:
        return "timeout"
    if status in (401, 403) or "authentication" in name or "permission" in name or "unauthenticated" in name:
        return "auth"
    if status == 402 or "paymentrequired" in name or "insufficient" in str(exc).lower():
        return "credits"
    if status == 404 or "notfound" in name:
        return "not_found"
    if status == 429 or "ratelimit" in name or "resourceexhausted" in name:
        return "rate_limit"
    if (status in (500, 502, 503, 504, 529) or "overloaded" in name or "internalserver" in name
            or "serviceunavailable" in name):
        return "overloaded"
    if status in (400, 413, 422) or "badrequest" in name or "invalidargument" in name:
        return "bad_request"
    if isinstance(exc, ConnectionError) or "connection" in name or "network" in name:
        return "network"
    return "unknown"


def friendly_message(kind: str, exc: BaseException, settings: Settings, attempts: int) -> str:
    tries = f" (tried {attempts} times)" if attempts > 1 else ""
    detail = str(exc).strip()[:200]
    return {
        "auth": f"The AI provider rejected the API key. Check {settings.api_key_env_var or 'the key'} in .env "
                "(no quotes or spaces) and restart the app.",
        "not_found": f"The model '{settings.llm_model}' was not found. Check LLM_MODEL in .env."
                     + (f" LLM_PROVIDER is '{settings.llm_provider}': OpenRouter needs a vendor/model id such as "
                        "google/gemini-2.5-flash; for Google AI Studio set LLM_PROVIDER=google_genai."
                        if settings.llm_provider == "openrouter" else ""),
        "credits": "The AI provider says the account does not have enough credit for this request. Add credit "
                   "(for OpenRouter: openrouter.ai/settings/credits) or lower LLM_MAX_OUTPUT_TOKENS in .env.",
        "rate_limit": f"The AI provider is limiting requests right now{tries}. Wait a minute and try again."
                      + (" The Google AI Studio free tier allows only a few requests per minute and per day; "
                         "see aistudio.google.com for your limits." if settings.llm_provider == "google_genai"
                         else ""),
        "overloaded": f"The AI provider is busy or having problems{tries}. Try again in a moment.",
        "timeout": f"The AI request took longer than {settings.llm_timeout_s:.0f} seconds{tries}. Try again, or "
                   "narrow the selection so there is less to process.",
        "network": f"Couldn't reach the AI provider{tries}. Check your internet connection.",
        "bad_request": f"The AI provider refused the request: {detail}",
    }.get(kind, f"The AI request failed ({type(exc).__name__}): {detail}")


def backoff_delay(attempt: int, exc: BaseException | None = None, rng: Callable[[], float] = random.random) -> float:
    """Seconds to wait before retry number ``attempt`` (1-based): exponential with jitter, or Retry-After."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        retry_after = float(headers.get("retry-after", ""))
        return min(BACKOFF_CAP_S, max(0.0, retry_after))
    except (TypeError, ValueError):
        pass
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1)) * (0.5 + rng() / 2)


def retry_call(fn: Callable[[], Any], settings: Settings, max_retries: int | None = None,
               sleep: Callable[[float], None] = time.sleep) -> Any:
    """Run ``fn()``; retry retryable failures with backoff; raise ``LLMError`` with a friendly message."""
    retries = settings.llm_max_retries if max_retries is None else max_retries
    for attempt in range(1, retries + 2):
        try:
            return fn()
        except LLMError:
            raise
        except Exception as exc:
            kind = classify_error(exc)
            if kind in RETRYABLE and attempt <= retries:
                sleep(backoff_delay(attempt, exc))
                continue
            raise LLMError(kind, friendly_message(kind, exc, settings, attempt), attempt) from exc
