import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _isolate_from_dotenv(monkeypatch):
    """Tests never read the real .env or use a real API key: they run offline with defaults."""
    monkeypatch.setattr("core.config.load_dotenv", lambda *a, **k: None)
    for var in ("OPENROUTER_BASE_URL", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL",
                "LLM_MAX_OUTPUT_TOKENS", "LLM_TEMPERATURE", "LLM_MAX_RETRIES", "LLM_TIMEOUT_SECONDS",
                "LLM_INPUT_PRICE_PER_MTOK", "LLM_OUTPUT_PRICE_PER_MTOK", "CONFERATLAS_REQUIRE_AUTH",
                "CONFERATLAS_STAFF_EMAILS", "CONFERATLAS_DATA_DIR", "BACKEND_API_TOKEN", "BACKEND_URL",
                "MAX_UPLOAD_MB", "AI_HTTP_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("frontend.client.load_dotenv", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def install_api(monkeypatch):
    """Route the frontend's HTTP calls to the FastAPI app in this process (a full contract test).

    Each test starts with empty frontend caches and no uploaded datasets on the backend."""
    import streamlit as st
    from fastapi.testclient import TestClient

    import frontend.client as api_client
    from backend.main import app, store

    test_client = TestClient(app)

    def local_api(method, path, timeout=None, **kwargs):
        response = test_client.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", "The request could not be completed.")
            except ValueError:
                detail = "The request could not be completed."
            raise api_client.error_from(response.status_code, detail)
        return response.json() if response.content else None

    monkeypatch.setattr(api_client, "api", local_api)
    st.cache_data.clear()
    for dataset_id in list(store._uploads):
        store.delete(dataset_id)
    yield test_client


def seed_upload(at, tables, digest: str = "seed") -> object:
    """Make ``tables`` the AppTest session's uploaded dataset (already validated, held by the backend)."""
    from backend.main import info, store

    stored = store.put_tables(tables, source=f"upload:{digest}")
    at.session_state["uploaded_dataset"] = {name: b"" for name in tables}
    at.session_state["uploaded_dataset_digest"] = digest
    at.session_state["uploaded_dataset_ref"] = {"digest": digest, "info": info(stored).model_dump(mode="json"),
                                                "error": None}
    return stored


def refresh(stored) -> None:
    """After editing a seeded dataset's tables in place, drop every cached result built from them."""
    import streamlit as st

    stored._filtered.clear()
    stored._comparisons.clear()
    st.cache_data.clear()

from core.generator import generate_dataset, write_dataset
from core.schema import load_dataset, validate_dataset


def _table(columns: str, *rows: str) -> pd.DataFrame:
    """Build a raw (all-text) table, as if read from CSV."""
    return pd.DataFrame([r.split("|") for r in rows], columns=columns.split("|"))


@pytest.fixture
def tiny_tables() -> dict[str, pd.DataFrame]:
    """Two events and two sponsors with easy-to-check numbers, validated like real CSVs.

    E1: 4 registered, 3 confirmed, 2 attended. S1 got 5/4 leads (met), S2 1/2 (missed).
    E2: 2 registered, 2 confirmed, 1 attended. S1 got 1/2 leads (missed).
    S1's $1,000 contract covers two events ($500 each); S2's $600 covers one.
    """
    raw = {
        "events.csv": _table(
            "event_id|name|event_type|format|city|start_date|end_date|budget|actual_cost",
            "E1|Alpha Con|tech|in-person|Austin|2025-03-10|2025-03-11|1000|1100",
            "E2|Beta Live|business|virtual|Boston|2025-06-01|2025-06-01|2000|1800",
        ),
        "sponsors.csv": _table(
            "sponsor_id|name|industry|tier|goals|contract_value",
            "S1|Acme|Software|gold|Lead generation|1000",
            "S2|Brio|Consulting|silver|Brand awareness|600",
        ),
        "sponsorships.csv": _table(
            "sponsor_id|event_id|package|leads_target",
            "S1|E1|Premier Booth|4", "S1|E2|Virtual Booth|2", "S2|E1|Standard Booth|2",
        ),
        "registrations.csv": _table(
            "reg_id|event_id|attendee_role|seniority|industry|country|reg_date|status|attended",
            "R1|E1|Engineer|Mid|Software|Canada|2025-03-01|confirmed|True",
            "R2|E1|Engineer|Mid|Software|Canada|2025-03-01|confirmed|True",
            "R3|E1|Engineer|Mid|Software|Canada|2025-03-09|confirmed|False",
            "R4|E1|Engineer|Mid|Software|Canada|2025-03-10|cancelled|False",
            "R5|E2|Founder|VP|Retail|Mexico|2025-05-31|confirmed|True",
            "R6|E2|Founder|VP|Retail|Mexico|2025-05-30|confirmed|False",
        ),
        "sessions.csv": _table(
            "session_id|event_id|track|speaker|mode|capacity|attendance|rating",
            "X1|E1|AI|Ann Lee|in-person|10|2|4.5",
            "X2|E1|Cloud|Bo Chen|in-person|4|2|2.0",
            "X3|E2|Sales|Cy Diaz|virtual|100|1|3.0",
        ),
        "engagement.csv": _table(
            "event_id|session_id|attendee_id|watch_minutes|polls|questions|booth_visits",
            "E1|X1|R1|30|1|0|2", "E1|X1|R2|40|0|1|0", "E1|X2|R1|20|0|0|0",
            "E1|X2|R2|10|0|0|0", "E2|X3|R5|45|2|1|0",
        ),
        "leads.csv": _table(
            "lead_id|sponsor_id|event_id|source|notes",
            "L1|S1|E1|booth|Hot lead", "L2|S1|E1|booth|Hot lead", "L3|S1|E1|booth|",
            "L4|S1|E1|demo|Asked about pricing", "L5|S1|E1|demo|", "L6|S1|E2|virtual|Hot lead",
            "L7|S2|E1|booth|Not a fit",
        ),
        "feedback.csv": _table(
            "event_id|session_id|rating|comment",
            "E1|X1|5|Great", "E1|X1|4|Great", "E1|X2|2|Too long", "E1|X2|2|", "E2|X3|3|Fine",
        ),
    }
    return validate_dataset(raw)


@pytest.fixture(scope="session")
def dataset(tmp_path_factory) -> dict[str, pd.DataFrame]:
    """The full generated dataset, written to disk and loaded back like the app does."""
    out = tmp_path_factory.mktemp("generated")
    write_dataset(generate_dataset(seed=42), out)
    return load_dataset(out)
