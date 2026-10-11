"""Backend API: auth, datasets, page endpoints (checked against core directly) and AI error mapping."""
import hashlib
import io
from datetime import date

import pandas as pd
import pytest

from backend.main import store
from backend.store import DatasetStore
from core import analysis as an
from core.filters import describe
from core.generator import generate_dataset, write_dataset
from core.schema import SCHEMAS
from shared.filters import CUSTOM, FilterState
from shared.models import (
    AudienceResponse,
    DrilldownResponse,
    FeedbackPageResponse,
    InsightsResponse,
    OverviewResponse,
    SponsorPerformanceResponse,
    SponsorReportPageResponse,
)

TODAY = date(2026, 10, 11)


@pytest.fixture
def api(install_api):
    return install_api


@pytest.fixture(scope="module")
def csv_files(tmp_path_factory) -> dict[str, bytes]:
    out = tmp_path_factory.mktemp("api_upload")
    write_dataset(generate_dataset(seed=3), out)
    return {name: (out / name).read_bytes() for name in SCHEMAS}


def _multipart(files: dict[str, bytes]):
    return [("files", (name, data, "text/csv")) for name, data in files.items()]


def _scope(**filters) -> dict:
    return {"filters": FilterState(**filters).to_json(), "today": TODAY.isoformat()}


def _tiny(api, tiny_tables) -> str:
    return store.put_tables(tiny_tables).id


# --- service and auth ------------------------------------------------------------------------

def test_health_is_open_and_data_routes_need_the_token(api, monkeypatch):
    monkeypatch.setenv("BACKEND_API_TOKEN", "s3cret")
    assert api.get("/health").json()["status"] == "ok"
    assert api.get("/status").status_code == 401
    assert api.get("/status", headers={"X-API-Token": "wrong"}).json()["detail"] == "Missing or invalid API token."
    assert api.get("/status", headers={"X-API-Token": "s3cret"}).status_code == 200


def test_status_reports_ai_setup_without_the_key(api, monkeypatch):
    status = api.get("/status").json()
    assert status["llm_configured"] is False and "OPENROUTER_API_KEY" in status["missing_key_message"]
    assert status["custom_data_dir"] is False and status["limits"]["max_tool_calls"] > 0
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value")
    status = api.get("/status").json()
    assert status["llm_configured"] is True and "sk-or-secret-value" not in str(status)


# --- datasets --------------------------------------------------------------------------------

def test_builtin_dataset_info(api, dataset):
    info = api.get("/datasets/builtin").json()
    assert info["kind"] == "builtin" and info["source"].startswith("folder:")
    assert info["rows"]["events.csv"] == 12 and info["first_event_date"] < info["last_event_date"]


def test_upload_validates_ignores_unknown_files_and_dedupes(api, csv_files):
    response = api.post("/datasets", files=_multipart({**csv_files, "notes.csv": b"x"}))
    assert response.status_code == 201
    info = response.json()
    assert info["kind"] == "upload" and info["ignored_files"] == ["notes.csv"]
    assert info["source"] == "upload:" + hashlib.sha256(b"".join(v for _, v in sorted(csv_files.items()))).hexdigest()
    again = api.post("/datasets", files=_multipart(csv_files)).json()
    assert again["dataset_id"] == info["dataset_id"]  # same bytes, same dataset
    assert api.delete(f"/datasets/{info['dataset_id']}").status_code == 204
    assert api.get(f"/datasets/{info['dataset_id']}").status_code == 404


def test_invalid_upload_lists_every_problem(api, csv_files):
    events = pd.read_csv(io.BytesIO(csv_files["events.csv"]), dtype=str).drop(columns="budget")
    files = dict(csv_files, **{"events.csv": events.to_csv(index=False).encode()})
    del files["leads.csv"]
    response = api.post("/datasets", files=_multipart(files))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "Could not load data. Please fix these problems:"
    assert "leads.csv: file is missing" in detail["issues"]
    assert "events.csv: missing required column(s): budget" in detail["issues"]


def test_upload_size_limit(api, csv_files, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.0001")
    assert api.post("/datasets", files=_multipart(csv_files)).status_code == 413


def test_broken_custom_data_folder_is_reported(api, tmp_path, monkeypatch):
    (tmp_path / "events.csv").write_text("event_id\nE1\n", encoding="utf-8")
    monkeypatch.setenv("CONFERATLAS_DATA_DIR", str(tmp_path))
    response = api.get("/datasets/builtin")
    assert response.status_code == 422 and "file is missing" in " ".join(response.json()["detail"]["issues"])
    assert api.get("/status").json()["custom_data_dir"] is True


def test_store_limits_and_expiry(tiny_tables):
    now = [0.0]
    small = DatasetStore(max_uploads=2, ttl_s=10, clock=lambda: now[0])
    first, second, third = (small.put_tables(tiny_tables) for _ in range(3))
    with pytest.raises(KeyError):
        small.get(first.id)  # least recently used is evicted
    now[0] = 5
    small.get(second.id)
    now[0] = 12
    assert small.get(second.id) is second
    with pytest.raises(KeyError):
        small.get(third.id)  # unused for longer than the TTL


def test_unknown_dataset_is_404(api):
    response = api.post("/datasets/nope/pages/overview", json=_scope())
    assert response.status_code == 404 and "expired" in response.json()["detail"]


# --- sidebar ---------------------------------------------------------------------------------

def test_filter_options_cascade_and_scope_summary(api, dataset):
    draft = FilterState(formats=("virtual",), cities=("Nowhere",)).to_json()
    options = api.post("/datasets/builtin/filter-options", json={"draft": draft, "today": "2026-10-11"}).json()
    assert len(options["events"]) == 4 and options["matching_events"] == 4 and options["total_events"] == 12
    assert options["formats"] == ["hybrid", "in-person", "virtual"]
    summary = api.post("/datasets/builtin/scope", json=_scope(formats=("virtual",))).json()
    assert summary["events"] == 4 and ["Format", "Virtual"] in summary["description"]


def test_relative_presets_use_the_viewers_date(api, dataset):
    body = {"filters": FilterState(date_preset="Last 12 months").to_json(), "today": "2026-10-11"}
    summary = api.post("/datasets/builtin/scope", json=body).json()
    expected = describe(FilterState(date_preset="Last 12 months"), dataset, TODAY)
    assert summary["description"] == [list(pair) for pair in expected]


def test_unknown_preset_is_rejected(api):
    body = {"filters": dict(FilterState().to_json(), date_preset="Last fortnight"), "today": "2026-10-11"}
    assert api.post("/datasets/builtin/scope", json=body).status_code == 422


# --- pages, checked against core -------------------------------------------------------------

def test_overview_matches_core(api, dataset):
    body = dict(_scope(date_preset=CUSTOM, custom_start=date(2025, 1, 1), custom_end=date(2025, 12, 31)),
                sort_by="nps", ascending=False, top_n=5)
    data = OverviewResponse(**api.post("/datasets/builtin/pages/overview", json=body).json())
    t = an.filter_dataset(dataset, start=date(2025, 1, 1), end=date(2025, 12, 31))
    assert data.kpis.events == an.portfolio_kpis(t).events == 6
    assert data.period.prev_start == date(2024, 1, 2) and len(data.ranked.data) == 5
    events = {c.metric: c for c in data.changes}["events"]
    assert (events.current, events.previous) == (6, 3)


def test_reversed_custom_dates_are_empty_not_an_error(api):
    body = _scope(date_preset=CUSTOM, custom_start=date(2025, 12, 31), custom_end=date(2025, 1, 1))
    for page in ("overview", "event-drilldown", "audience", "feedback", "sponsor-performance", "sponsor-report"):
        response = api.post(f"/datasets/builtin/pages/{page}", json=body)
        assert response.status_code == 200 and response.json()["empty"] is True, page


def test_drilldown_picks_event_and_filters_sessions(api, tiny_tables):
    ds = _tiny(api, tiny_tables)
    body = dict(_scope(), event_candidates=["E9", "E1"], rating=[4.0, 5.0], tracks=["AI", "Gone"])
    data = DrilldownResponse(**api.post(f"/datasets/{ds}/pages/event-drilldown", json=body).json())
    assert data.event_id == "E1" and [o[0] for o in data.event_options] == ["E2", "E1"]
    assert data.track_options == ["AI", "Cloud"] and len(data.sessions_shown.data) == 1
    assert data.early_bird_days is None  # uploads have no early-bird line
    builtin = api.post("/datasets/builtin/pages/event-drilldown", json=_scope()).json()
    assert builtin["early_bird_days"] is not None


def test_sponsor_pages_match_core(api, tiny_tables):
    ds = _tiny(api, tiny_tables)
    data = SponsorPerformanceResponse(**api.post(f"/datasets/{ds}/pages/sponsor-performance", json=_scope()).json())
    assert data.kpis.leads == 7 and data.kpis.target == 8 and data.kpis.below_count == 2
    assert data.low_sponsors == [("Brio", 0.5)] and data.scope == "all events and sponsors"
    assert [o[1] for o in data.report_options] == ["Acme", "Brio"]
    filtered = api.post(f"/datasets/{ds}/pages/sponsor-performance",
                        json=dict(_scope(), sponsor_filters={"tiers": ["gold"], "sources": ["nope"]})).json()
    assert filtered["sponsors_count"] == 1 and filtered["scope"] == "Tier: gold"
    report = SponsorReportPageResponse(**api.post(f"/datasets/{ds}/pages/sponsor-report",
                                                  json=dict(_scope(), sponsor_candidates=["S2"])).json())
    assert report.sponsor_id == "S2" and len(report.history.data) == 1


def test_audience_and_feedback_pages(api, tiny_tables):
    ds = _tiny(api, tiny_tables)
    data = AudienceResponse(**api.post(f"/datasets/{ds}/pages/audience",
                                       json=dict(_scope(), statuses=["Attended"])).json())
    assert data.audience_count == 3 and data.attendees == 3 and data.has_history is False
    fb = FeedbackPageResponse(**api.post(f"/datasets/{ds}/pages/feedback",
                                         json=dict(_scope(), ratings=[2], tracks=["Cloud"])).json())
    assert fb.summary.responses == 2 and fb.track_options == ["AI", "Cloud", "Sales"]
    assert fb.preview.prepared == 1 and len(fb.fingerprint) == 64


def test_insights_page(api, tiny_tables):
    ds = _tiny(api, tiny_tables)
    data = InsightsResponse(**api.post(f"/datasets/{ds}/pages/ai-insights",
                                       json=dict(_scope(), kind="sponsor", candidates=["S1"])).json())
    assert data.subject == "Acme" and "Acme" in data.context and data.fallback


def test_pages_never_change_the_stored_tables(api, dataset):
    stored = store.builtin()
    before = {name: pd.util.hash_pandas_object(df, index=True).sum() for name, df in stored.tables.items()}
    for page in ("overview", "event-drilldown", "sponsor-performance", "sponsor-report", "audience", "feedback",
                 "ai-insights"):
        assert api.post(f"/datasets/builtin/pages/{page}", json=_scope()).status_code == 200
    api.get("/datasets/builtin/pages/about")
    after = {name: pd.util.hash_pandas_object(df, index=True).sum() for name, df in stored.tables.items()}
    assert before == after


# --- AI endpoints ----------------------------------------------------------------------------

def test_sponsor_report_falls_back_to_the_template(api, tiny_tables):
    ds = _tiny(api, tiny_tables)
    data = api.post(f"/datasets/{ds}/ai/sponsor-report", json=dict(_scope(), sponsor_id="S1")).json()
    assert data["report"]["generated_by"] == "Template (no AI)" and data["facts"]["sponsor"]["name"] == "Acme"
    assert data["usage"] == []


def test_sponsor_report_ai_failure_is_explained(api, tiny_tables, monkeypatch):
    import backend.services.sponsor_summary as service
    from core.ai.sponsor_report import ReportError

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def fail(*a, **k):
        raise ReportError("timeout", "Too slow.")
    monkeypatch.setattr(service, "generate_sponsor_report", fail)
    ds = _tiny(api, tiny_tables)
    report = api.post(f"/datasets/{ds}/ai/sponsor-report", json=dict(_scope(), sponsor_id="S1")).json()["report"]
    assert report["warnings"] == ["The AI report failed (Too slow.), so this is the template draft instead."]


@pytest.mark.parametrize("error, status, kind", [
    ("not_configured", 503, "not_configured"),
    ("timeout", 504, "timeout"),
    ("provider", 502, "provider"),
    ("value", 422, "invalid_request"),
    ("boom", 500, "unexpected"),
])
def test_ai_errors_keep_kind_message_and_usage(api, tiny_tables, monkeypatch, error, status, kind):
    from datetime import datetime

    import backend.services.feedback as service
    from core.ai.feedback import FeedbackAnalysisError
    from core.ai.llm import CallRecord, LLMNotConfiguredError

    def fake(fb, settings, recorder=None, **kw):
        recorder.add(CallRecord(datetime(2026, 10, 11, 12), "Feedback analysis", "m", 10, None, None, 1.0,
                                "error", "timeout"))
        raise {"not_configured": LLMNotConfiguredError("No API key."), "value": ValueError("No comments."),
               "boom": RuntimeError("Kaboom")}.get(error) or FeedbackAnalysisError(error, "It failed.")
    monkeypatch.setattr(service, "analyze_feedback", fake)
    ds = _tiny(api, tiny_tables)
    response = api.post(f"/datasets/{ds}/ai/feedback-analysis", json=_scope())
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["kind"] == kind and detail["usage"][0]["purpose"] == "Feedback analysis"
    if error == "boom":
        assert detail["exception_type"] == "RuntimeError" and detail["message"] == "Kaboom"


def test_chat_returns_tables_and_charts(api, monkeypatch):
    import backend.services.chat as service
    from core.ai.portfolio_agent import AgentAnswer

    def fake(question, toolkit, history, **kw):
        toolkit.event_kpis(sort_by="show_up_rate", top_n=3)
        toolkit.make_chart("T1", "bar", "name", "show_up_rate", "Show-up")
        assert history == [{"role": "user", "content": "Earlier"}]
        return AgentAnswer(text="Done", calculations=list(toolkit.ledger), charts=list(toolkit.charts), tool_calls=2)
    monkeypatch.setattr(service, "ask_portfolio", fake)
    body = dict(_scope(), question="Best show-up?", history=[{"role": "user", "content": "Earlier"}])
    answer = api.post("/datasets/builtin/chat", json=body).json()["answer"]
    assert answer["text"] == "Done" and answer["charts"][0]["source_id"] == "T1"
    assert len(answer["calculations"][0]["table"]["data"]) == 3


def test_chat_rejects_oversized_requests(api):
    body = dict(_scope(), question="x" * 2001)
    assert api.post("/datasets/builtin/chat", json=body).status_code == 422
