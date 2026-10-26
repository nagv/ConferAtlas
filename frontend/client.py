"""The frontend's only door to the backend: HTTP calls, error mapping and response caching.

No analysis code or model keys live in the frontend. Page calls are cached per dataset,
filters, date and page options (``st.cache_data``), so reruns that change nothing do not
hit the API. AI calls and the status call are never cached.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import httpx
import streamlit as st
from dotenv import load_dotenv

HTTP_TIMEOUT_S = 65
HEALTH_TTL_S = 30
BUILTIN = "builtin"


def load_env() -> None:
    """Read frontend/.env (never overriding real environment variables)."""
    load_dotenv(Path(__file__).with_name(".env"), override=False)


def setting(name: str, default: str = "") -> str:
    """A frontend setting: Streamlit secrets (e.g. Streamlit Community Cloud) first, then the environment.

    Streamlit copies top-level secrets into os.environ only once st.secrets is first read, so they
    are read here directly rather than relying on that side effect."""
    try:
        value = st.secrets.get(name)
    except Exception:  # no secrets file (local runs, tests)
        value = None
    if value is None or isinstance(value, dict):
        value = os.getenv(name, default)
    return str(value)


def ai_timeout_s() -> float:
    """AI requests can run for minutes (feedback batches, report revisions, agent tool rounds)."""
    try:
        return float(setting("AI_HTTP_TIMEOUT_S", "600"))
    except ValueError:
        return 600.0


class ApiError(Exception):
    """A failed backend call. ``kind``, ``issues`` and ``usage`` come from a structured error detail."""

    def __init__(self, message: str, status_code: int = 502, kind: str | None = None,
                 issues: list[str] | None = None, usage: list[dict] | None = None, exception_type: str = ""):
        super().__init__(message)
        self.message, self.status_code, self.kind = message, status_code, kind
        self.issues, self.usage, self.exception_type = list(issues or []), list(usage or []), exception_type


def error_from(status_code: int, detail) -> ApiError:
    """Turn an error response's ``detail`` into an ApiError."""
    if isinstance(detail, dict):
        return ApiError(detail.get("message", "The request could not be completed."), status_code,
                        kind=detail.get("kind"), issues=detail.get("issues"), usage=detail.get("usage"),
                        exception_type=detail.get("exception_type", ""))
    if isinstance(detail, list):
        return ApiError("Please check the selected filters and request fields.", status_code)
    return ApiError(str(detail), status_code)


def api(method: str, path: str, timeout: float = HTTP_TIMEOUT_S, **kwargs):
    base = setting("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    token = setting("BACKEND_API_TOKEN")
    headers = {"X-API-Token": token} if token else None
    try:
        response = httpx.request(method, base + path, headers=headers, timeout=timeout, **kwargs)
    except httpx.TimeoutException:
        raise ApiError("The backend took too long to respond. Please try again.", 504) from None
    except httpx.RequestError:
        raise ApiError("Cannot reach the backend. Start the API service and try again.", 503) from None
    if response.is_error:
        try:
            detail = response.json().get("detail", "The request could not be completed.")
        except ValueError:
            detail = "The request could not be completed."
        raise error_from(response.status_code, detail)
    return response.json() if response.content else None


# The helpers below call `api` through the module global so tests can monkeypatch it.

def _key(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def health() -> dict:
    """Backend health, re-checked at most every HEALTH_TTL_S seconds per browser session."""
    cached = st.session_state.get("_health")
    if cached and time.monotonic() - cached[0] < HEALTH_TTL_S:
        return cached[1]
    result = api("GET", "/health")
    st.session_state["_health"] = (time.monotonic(), result)
    return result


def status() -> dict:
    """AI setup and limits. Not cached: a changed model or key must show on the next run."""
    return api("GET", "/status")


@st.cache_data(ttl=300, max_entries=32, show_spinner="Loading data...")
def dataset_info(dataset_id: str) -> dict:
    return api("GET", f"/datasets/{dataset_id}")


def upload(files: dict[str, bytes]) -> dict:
    return api("POST", "/datasets", files=[("files", (name, data, "text/csv")) for name, data in files.items()])


def delete_dataset(dataset_id: str) -> None:
    try:
        api("DELETE", f"/datasets/{dataset_id}")
    except ApiError:
        pass  # best effort: an upload expires on its own


@st.cache_data(ttl=300, max_entries=256, show_spinner=False)
def _post(path: str, payload_json: str) -> dict:
    return api("POST", path, json=json.loads(payload_json))


@st.cache_data(ttl=300, max_entries=256, show_spinner="Applying filters...")
def _page(path: str, payload_json: str) -> dict:
    return api("POST", path, json=json.loads(payload_json))


def filter_options(dataset_id: str, draft: dict, today: date) -> dict:
    return _post(f"/datasets/{dataset_id}/filter-options", _key({"draft": draft, "today": today.isoformat()}))


def scope(dataset_id: str, filters: dict, today: date) -> dict:
    return _page(f"/datasets/{dataset_id}/scope", _key({"filters": filters, "today": today.isoformat()}))


def page(dataset_id: str, name: str, payload: dict) -> dict:
    """One page's data (``name`` is the endpoint, e.g. 'overview'). Cached per request."""
    return _page(f"/datasets/{dataset_id}/pages/{name}", _key(payload))


@st.cache_data(ttl=300, max_entries=32, show_spinner=False)
def about(dataset_id: str) -> dict:
    return api("GET", f"/datasets/{dataset_id}/pages/about")


def ai(dataset_id: str, name: str, payload: dict) -> dict:
    """An AI action ('feedback-analysis', 'sponsor-report', 'pitch'); never cached."""
    return api("POST", f"/datasets/{dataset_id}/ai/{name}", json=json.loads(_key(payload)), timeout=ai_timeout_s())


def chat(dataset_id: str, payload: dict) -> dict:
    return api("POST", f"/datasets/{dataset_id}/chat", json=json.loads(_key(payload)), timeout=ai_timeout_s())


# --- documents and RAG Lab (Week 2) ---

@st.cache_data(ttl=300, max_entries=4, show_spinner=False)
def rag_documents() -> list[dict]:
    return api("GET", "/rag/documents")


def rag_chunks(payload: dict) -> dict:
    """Chunks of one document for a chunking setting (cached; semantic chunking may embed text)."""
    return _post("/rag/chunks", _key(payload))


def rag_search(dataset_id: str, payload: dict) -> dict:
    return api("POST", f"/datasets/{dataset_id}/rag/search", json=json.loads(_key(payload)), timeout=ai_timeout_s())


def rag_map(payload: dict) -> dict:
    return _post("/rag/map", _key(payload))


def rag_build(payload: dict) -> dict:
    """Index a chunking setting; never cached, and it can take a minute the first time."""
    return api("POST", "/rag/build", json=json.loads(_key(payload)), timeout=ai_timeout_s())


def rag_experiments() -> dict:
    return api("GET", "/rag/experiments")


# --- Sponsor Renewal Desk (Week 3): each step runs in the background, so these return at once ---

def renewal_start(dataset_id: str, payload: dict) -> dict:
    return api("POST", f"/datasets/{dataset_id}/renewals", json=json.loads(_key(payload)))


def renewal_status(run_id: str) -> dict:
    return api("GET", f"/renewals/{run_id}")


def renewal_decide(run_id: str, decisions: dict) -> dict:
    return api("POST", f"/renewals/{run_id}/decisions", json={"decisions": decisions})


def renewal_retry(run_id: str) -> dict:
    return api("POST", f"/renewals/{run_id}/retry")


def renewal_runs() -> list[dict]:
    return api("GET", "/renewals")


def clear_caches() -> None:
    for fn in (dataset_info, _post, _page, about, rag_documents):
        fn.clear()
    st.session_state.pop("_health", None)
