"""ConferAtlas API: datasets, page calculations and AI features for the Streamlit frontend.

Run from the repository root:  python -m uvicorn backend.main:app --port 8000
State is in memory, so run a single worker.
"""
from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile

from backend import errors
from backend.services import (
    about,
    audience,
    chat,
    documents,
    drilldown,
    feedback,
    insights,
    overview,
    renewals,
    selection,
    sponsor_report_page,
    sponsor_summary,
    sponsors,
)
from backend.store import BUILTIN_ID, DatasetLoadError, DatasetStore, StoredDataset
from core.ai.feedback import DEFAULT_BATCH_TOKENS, DEFAULT_MAX_TOTAL_TOKENS, DEFAULT_REQUEST_TIMEOUT_S
from core.ai.llm import UsageRecorder, missing_key_message
from core.ai.portfolio_agent import HISTORY_MESSAGES, MAX_TOOL_CALLS
from core.config import DEFAULT_DATA_DIR, get_settings
from core.rag.embeddings import EmbeddingsNotConfiguredError
from shared.models import (
    AboutResponse,
    BuildRequest,
    BuildResponse,
    ChunkRequest,
    ChunkResponse,
    DocumentInfo,
    ExperimentsResponse,
    MapRequest,
    MapResponse,
    RagSearchRequest,
    RagSearchResponse,
    AudienceRequest,
    AudienceResponse,
    ChatRequest,
    ChatResponse,
    DatasetInfo,
    DrilldownRequest,
    DrilldownResponse,
    FeedbackAnalysisRequest,
    FeedbackAnalysisResponse,
    FeedbackPageResponse,
    FeedbackRequest,
    FilterOptions,
    FilterOptionsRequest,
    Health,
    InsightsRequest,
    InsightsResponse,
    Limits,
    OverviewRequest,
    OverviewResponse,
    PitchRequest,
    PitchResponse,
    RenewalDecisionRequest,
    RenewalRunResponse,
    RenewalRunSummary,
    RenewalStartRequest,
    ScopeRequest,
    ScopeSummary,
    SponsorPerformanceRequest,
    SponsorPerformanceResponse,
    SponsorReportPageResponse,
    SponsorReportRequest,
    SponsorSummaryRequest,
    SponsorSummaryResponse,
    Status,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
store = DatasetStore()
app = FastAPI(title="ConferAtlas API", version="1.0.0")


def max_upload_bytes() -> int:
    try:
        return int(float(os.getenv("MAX_UPLOAD_MB", "200")) * 1024 * 1024)
    except ValueError:
        return 200 * 1024 * 1024


def require_token(x_api_token: Annotated[str | None, Header()] = None):
    """When BACKEND_API_TOKEN is set (any shared deployment), every data route requires it."""
    expected = os.getenv("BACKEND_API_TOKEN", "")
    if expected and not hmac.compare_digest((x_api_token or "").encode(), expected.encode()):
        raise HTTPException(401, "Missing or invalid API token.")


protected = [Depends(require_token)]


def get_dataset(dataset_id: str) -> StoredDataset:
    try:
        return store.get(dataset_id)
    except KeyError:
        raise HTTPException(404, "Dataset expired or is unavailable. Load or upload it again.") from None
    except DatasetLoadError as exc:
        raise HTTPException(422, {"message": exc.message, "issues": exc.issues}) from None


def info(ds: StoredDataset) -> DatasetInfo:
    events = ds.tables["events.csv"]["start_date"]
    return DatasetInfo(dataset_id=ds.id, kind=ds.kind, source=ds.source, files=ds.files,
                       ignored_files=ds.ignored_files, rows={name: len(df) for name, df in ds.tables.items()},
                       first_event_date=events.min().date() if len(events) else None,
                       last_event_date=events.max().date() if len(events) else None)


# --- service --------------------------------------------------------------------------------------

@app.get("/health", response_model=Health)
def health():
    return Health(status="ok", llm_configured=get_settings().llm_configured, storage="in-memory")


@app.get("/status", response_model=Status, dependencies=protected)
def status():
    s = get_settings()
    return Status(
        llm_configured=s.llm_configured, missing_key_message=missing_key_message(s), provider=s.llm_provider,
        model=s.llm_model, base_url=s.base_url, temperature=s.llm_temperature, timeout_s=s.llm_timeout_s,
        max_retries=s.llm_max_retries, max_output_tokens=s.llm_max_output_tokens,
        input_price_per_mtok=s.input_price_per_mtok, output_price_per_mtok=s.output_price_per_mtok,
        custom_data_dir=s.data_dir.resolve() != DEFAULT_DATA_DIR.resolve(),
        limits=Limits(max_tool_calls=MAX_TOOL_CALLS, history_messages=HISTORY_MESSAGES,
                      feedback_batch_tokens=DEFAULT_BATCH_TOKENS, feedback_max_total_tokens=DEFAULT_MAX_TOTAL_TOKENS,
                      feedback_request_timeout_s=DEFAULT_REQUEST_TIMEOUT_S),
        documents=documents.status(s),
    )


# --- datasets -------------------------------------------------------------------------------------

@app.post("/datasets", response_model=DatasetInfo, status_code=201, dependencies=protected)
async def upload(files: Annotated[list[UploadFile], File()]):
    limit, payload = max_upload_bytes(), {}
    try:
        for upload_file in files:
            data = await upload_file.read(limit + 1)
            name = Path(upload_file.filename or "").name.lower()
            if len(data) > limit:
                raise HTTPException(413, f"{name} is larger than the {limit // (1024 * 1024)} MB upload limit.")
            payload[name] = data
    finally:
        for upload_file in files:
            await upload_file.close()
    try:
        return info(store.add_upload(payload))
    except DatasetLoadError as exc:
        raise HTTPException(422, {"message": exc.message, "issues": exc.issues}) from None


@app.get("/datasets/{dataset_id}", response_model=DatasetInfo, dependencies=protected)
def dataset_info(dataset_id: str):
    return info(get_dataset(dataset_id))


@app.delete("/datasets/{dataset_id}", status_code=204, dependencies=protected)
def delete_dataset(dataset_id: str):
    if dataset_id != BUILTIN_ID:
        store.delete(dataset_id)
    return Response(status_code=204)


@app.post("/datasets/{dataset_id}/filter-options", response_model=FilterOptions, dependencies=protected)
def filter_options(dataset_id: str, request: FilterOptionsRequest):
    return selection.filter_options(get_dataset(dataset_id), request.draft, request.today)


@app.post("/datasets/{dataset_id}/scope", response_model=ScopeSummary, dependencies=protected)
def scope(dataset_id: str, request: ScopeRequest):
    return selection.scope_summary(get_dataset(dataset_id), request)


# --- pages ----------------------------------------------------------------------------------------

@app.post("/datasets/{dataset_id}/pages/overview", response_model=OverviewResponse, dependencies=protected)
def overview_page(dataset_id: str, request: OverviewRequest):
    return overview.build(get_dataset(dataset_id), request)


@app.post("/datasets/{dataset_id}/pages/event-drilldown", response_model=DrilldownResponse, dependencies=protected)
def drilldown_page(dataset_id: str, request: DrilldownRequest):
    return drilldown.build(get_dataset(dataset_id), request)


@app.post("/datasets/{dataset_id}/pages/sponsor-performance", response_model=SponsorPerformanceResponse,
          dependencies=protected)
def sponsor_performance_page(dataset_id: str, request: SponsorPerformanceRequest):
    return sponsors.build(get_dataset(dataset_id), request)


@app.post("/datasets/{dataset_id}/pages/sponsor-report", response_model=SponsorReportPageResponse,
          dependencies=protected)
def sponsor_report_page_(dataset_id: str, request: SponsorReportRequest):
    return sponsor_report_page.build(get_dataset(dataset_id), request)


@app.post("/datasets/{dataset_id}/pages/audience", response_model=AudienceResponse, dependencies=protected)
def audience_page(dataset_id: str, request: AudienceRequest):
    return audience.build(get_dataset(dataset_id), request)


@app.post("/datasets/{dataset_id}/pages/feedback", response_model=FeedbackPageResponse, dependencies=protected)
def feedback_page(dataset_id: str, request: FeedbackRequest):
    return feedback.build(get_dataset(dataset_id), request)


@app.post("/datasets/{dataset_id}/pages/ai-insights", response_model=InsightsResponse, dependencies=protected)
def insights_page(dataset_id: str, request: InsightsRequest):
    return insights.build(get_dataset(dataset_id), request)


@app.get("/datasets/{dataset_id}/pages/about", response_model=AboutResponse, dependencies=protected)
def about_page(dataset_id: str):
    return about.build(get_dataset(dataset_id))


# --- AI -------------------------------------------------------------------------------------------

@app.post("/datasets/{dataset_id}/ai/feedback-analysis", response_model=FeedbackAnalysisResponse,
          dependencies=protected)
def feedback_analysis(dataset_id: str, request: FeedbackAnalysisRequest):
    ds, recorder = get_dataset(dataset_id), UsageRecorder()
    try:
        result = feedback.analyse(ds, request, recorder)
    except Exception as exc:
        raise errors.ai_error(exc, recorder) from None
    return {"result": result.model_dump(), "usage": errors.usage_records(recorder)}


@app.post("/datasets/{dataset_id}/ai/sponsor-report", response_model=SponsorSummaryResponse,
          dependencies=protected)
def sponsor_report(dataset_id: str, request: SponsorSummaryRequest):
    ds, recorder = get_dataset(dataset_id), UsageRecorder()
    try:
        report, facts = sponsor_summary.generate(ds, request, recorder)
    except KeyError:
        raise HTTPException(404, "That sponsor has no sponsorships in the current selection.") from None
    return {"report": report, "facts": facts, "usage": errors.usage_records(recorder)}


@app.post("/datasets/{dataset_id}/ai/pitch", response_model=PitchResponse, dependencies=protected)
def pitch(dataset_id: str, request: PitchRequest):
    ds, recorder = get_dataset(dataset_id), UsageRecorder()
    try:
        text = insights.pitch(ds, request, recorder)
    except Exception as exc:
        raise errors.ai_error(exc, recorder) from None
    return {"text": text, "usage": errors.usage_records(recorder)}


@app.post("/datasets/{dataset_id}/chat", response_model=ChatResponse, dependencies=protected)
def ask(dataset_id: str, request: ChatRequest):
    ds, recorder = get_dataset(dataset_id), UsageRecorder()
    try:
        answer = chat.ask(ds, request, recorder)
    except Exception as exc:
        raise errors.ai_error(exc, recorder) from None
    return {"answer": answer, "usage": errors.usage_records(recorder)}


# --- documents and RAG Lab (Week 2) -------------------------------------------------------------

def _rag(run):
    """Run a RAG Lab call, turning setup problems into clear HTTP errors."""
    try:
        return run()
    except EmbeddingsNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from None
    except KeyError as exc:
        raise HTTPException(404, f"Unknown document {exc}.") from None
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/rag/documents", response_model=list[DocumentInfo], dependencies=protected)
def rag_documents():
    return documents.documents()


@app.post("/rag/chunks", response_model=ChunkResponse, dependencies=protected)
def rag_chunks(request: ChunkRequest):
    return _rag(lambda: documents.chunks(request))


@app.post("/rag/map", response_model=MapResponse, dependencies=protected)
def rag_map(request: MapRequest):
    return _rag(lambda: documents.embedding_map(request))


@app.post("/rag/build", response_model=BuildResponse, dependencies=protected)
def rag_build(request: BuildRequest):
    return _rag(lambda: documents.build(request))


@app.get("/rag/experiments", response_model=ExperimentsResponse, dependencies=protected)
def rag_experiments():
    return documents.experiments()


@app.post("/datasets/{dataset_id}/rag/search", response_model=RagSearchResponse, dependencies=protected)
def rag_search(dataset_id: str, request: RagSearchRequest):
    ds = get_dataset(dataset_id)
    _, tables = selection.scoped(ds, request)
    return _rag(lambda: documents.search(ds, tables, request))


# --- Sponsor Renewal Desk (Week 3) ----------------------------------------------------------------

def _renewal(call):
    """Run a Renewal Desk call, turning its problems into clear HTTP errors."""
    try:
        return call()
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(404, "That renewal run was not found.") from None
    except renewals.RunBusyError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:  # e.g. no API key configured
        raise errors.ai_error(exc, UsageRecorder()) from None


@app.post("/datasets/{dataset_id}/renewals", response_model=RenewalRunResponse, status_code=202,
          dependencies=protected)
def renewal_start(dataset_id: str, request: RenewalStartRequest):
    ds = get_dataset(dataset_id)
    return _renewal(lambda: renewals.start(ds, request))


@app.get("/renewals", response_model=list[RenewalRunSummary], dependencies=protected)
def renewal_recent():
    return renewals.recent()


@app.get("/renewals/{run_id}", response_model=RenewalRunResponse, dependencies=protected)
def renewal_status(run_id: str):
    return _renewal(lambda: renewals.status(run_id, get_dataset))


@app.post("/renewals/{run_id}/decisions", response_model=RenewalRunResponse, status_code=202,
          dependencies=protected)
def renewal_decide(run_id: str, request: RenewalDecisionRequest):
    return _renewal(lambda: renewals.decide(run_id, request, get_dataset))


@app.post("/renewals/{run_id}/retry", response_model=RenewalRunResponse, status_code=202, dependencies=protected)
def renewal_retry(run_id: str):
    return _renewal(lambda: renewals.retry(run_id, get_dataset))
