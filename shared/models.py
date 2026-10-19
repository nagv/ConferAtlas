"""Request and response contracts between the Streamlit frontend and the FastAPI backend.

Every model forbids unknown fields, so a typo in either app fails loudly instead of being
ignored. Tables (DataFrames and Series) travel as ``Table``: column order, dtypes and rows,
which the frontend turns back into an identical DataFrame for charts, tables and CSV files.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.filters import DATE_PRESETS, FilterState

MAX_LIST = 500          # values in one filter list
MAX_QUESTION_CHARS = 2_000
MAX_HISTORY_MESSAGES = 40


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- building blocks --------------------------------------------------------------------------

class Table(Contract):
    """A DataFrame (``kind="frame"``) or Series (``kind="series"``) in JSON form."""

    kind: Literal["frame", "series"] = "frame"
    columns: list[Any]
    dtypes: list[str]
    data: list[list[Any]]
    index: list[Any] | None = None       # only when the index carries meaning
    index_name: str | None = None
    index_dtype: str | None = None
    name: str | None = None              # Series name


Option = tuple[str, str]  # (id, display name)


class Filters(Contract):
    """The sidebar filters (``shared.filters.FilterState``) as sent over the API."""

    date_preset: str = "All time"
    custom_start: date | None = None
    custom_end: date | None = None
    event_types: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    formats: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    cities: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    event_ids: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    sponsor_id: str | None = None

    @field_validator("date_preset")
    @classmethod
    def _known_preset(cls, value: str) -> str:
        if value not in DATE_PRESETS:
            raise ValueError(f"date_preset must be one of {', '.join(DATE_PRESETS)}")
        return value

    def to_state(self) -> FilterState:
        return FilterState.from_json(self.model_dump())

    @classmethod
    def of(cls, state: FilterState) -> Filters:
        return cls(**state.to_json())


class ScopeRequest(Contract):
    """Applied filters plus the viewer's date, which relative presets ("Last 6 months") need."""

    filters: Filters = Field(default_factory=Filters)
    today: date


def _strings() -> Any:
    return Field(default_factory=list, max_length=MAX_LIST)


class CallRecordModel(Contract):
    started: str           # ISO datetime
    purpose: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_s: float
    status: str
    error: str = ""


class AIErrorDetail(Contract):
    """``detail`` of an AI endpoint error, so the page can show its own wording and keep usage."""

    kind: str
    message: str
    exception_type: str = ""
    usage: list[CallRecordModel] = Field(default_factory=list)


class PortfolioKPIsModel(Contract):
    events: int
    first_date: date | None
    last_date: date | None
    registrations: int
    confirmed: int
    attendees: int
    show_up_rate: float | None
    no_show_rate: float | None
    sessions: int
    feedback_count: int
    avg_session_rating: float | None
    nps: float | None
    avg_event_nps: float | None
    sponsorships: int
    unique_sponsors: int
    sponsor_revenue: float
    leads: int
    leads_target: int
    lead_attainment: float | None
    share_met_target: float | None
    cost_per_lead: float | None
    budget: float
    actual_cost: float
    budget_variance: float | None


class MetricChangeModel(Contract):
    metric: str
    label: str
    current: float | None
    previous: float | None
    change: float | None
    pct_change: float | None
    improved: bool | None


class FeedbackSummaryModel(Contract):
    responses: int
    with_comment: int
    avg_rating: float | None
    nps: float | None
    positive: float | None
    neutral: float | None
    negative: float | None


# --- service status and datasets ------------------------------------------------------------------

class Health(Contract):
    status: str
    llm_configured: bool
    storage: str


class Limits(Contract):
    max_tool_calls: int
    history_messages: int
    feedback_batch_tokens: int
    feedback_max_total_tokens: int
    feedback_request_timeout_s: float


class DocumentStatus(Contract):
    """Whether document search (RAG) is ready, and with which embedding model and index."""

    available: bool
    note: str                       # what to do when it is off
    embedding_model: str = ""
    dimension: int = 0
    chunks: int = 0                 # in the structure-aware index the assistant searches
    chunk_settings: list[str] = Field(default_factory=list)   # every built chunking setting


class Status(Contract):
    """What the frontend needs to know about the AI setup (never the key itself)."""

    llm_configured: bool
    missing_key_message: str
    provider: str
    model: str
    base_url: str | None
    temperature: float
    timeout_s: float
    max_retries: int
    max_output_tokens: int
    input_price_per_mtok: float | None
    output_price_per_mtok: float | None
    custom_data_dir: bool
    limits: Limits
    documents: DocumentStatus | None = None


class DatasetInfo(Contract):
    dataset_id: str
    kind: Literal["builtin", "upload"]
    source: str                   # changes whenever different data is loaded
    files: list[str]
    ignored_files: list[str] = Field(default_factory=list)
    rows: dict[str, int]
    first_event_date: date | None
    last_event_date: date | None


class FilterOptionsRequest(Contract):
    draft: Filters
    today: date


class FilterOptions(Contract):
    """Each sidebar list given the earlier draft choices (dates -> type -> format -> city -> event -> sponsor)."""

    event_types: list[str]
    formats: list[str]
    cities: list[str]
    events: list[Option]
    sponsors: list[Option]
    matching_events: int
    total_events: int


class ScopeSummary(Contract):
    description: list[tuple[str, str]]     # ``core.filters.describe`` of the applied filters
    events: int
    sponsors: int
    registrations: int


# --- pages ---------------------------------------------------------------------------------------

class OverviewRequest(ScopeRequest):
    sort_by: str = "registrations"
    ascending: bool = False
    top_n: int | None = 10


class Period(Contract):
    start: date
    end: date
    prev_start: date
    prev_end: date


class OverviewResponse(Contract):
    empty: bool
    kpis: PortfolioKPIsModel | None = None
    period: Period | None = None
    changes: list[MetricChangeModel] = Field(default_factory=list)
    events_total: int = 0
    ranked: Table | None = None
    mix: Table | None = None
    trend: Table | None = None


class DrilldownRequest(ScopeRequest):
    event_candidates: list[str] = _strings()   # widget value, then the shared selection
    tracks: list[str] = _strings()
    modes: list[str] = _strings()
    speakers: list[str] = _strings()
    rating: tuple[float, float] = (1.0, 5.0)


class DrilldownResponse(Contract):
    empty: bool
    event_options: list[Option] = Field(default_factory=list)
    event_id: str | None = None
    event: Table | None = None
    has_sessions: bool = False
    sessions_fill_mean: float | None = None
    track_options: list[str] = Field(default_factory=list)
    mode_options: list[str] = Field(default_factory=list)
    speaker_options: list[str] = Field(default_factory=list)
    sessions_total: int = 0
    sessions_shown: Table | None = None
    engagement: Table | None = None
    timeline: Table | None = None
    lead_mix: Table | None = None
    early_bird_days: int | None = None
    sponsor_results: Table | None = None
    feedback: FeedbackSummaryModel | None = None
    top_positive: Table | None = None
    top_negative: Table | None = None


class SponsorFilters(Contract):
    tiers: list[str] = _strings()
    industries: list[str] = _strings()
    packages: list[str] = _strings()
    sources: list[str] = _strings()


class SponsorPerformanceRequest(ScopeRequest):
    sponsor_filters: SponsorFilters = Field(default_factory=SponsorFilters)


class SponsorKPIs(Contract):
    spend: float
    leads: int
    target: int
    full_contracts: float
    booth_visits: int
    booth_leads: float
    below_count: int
    results_count: int
    renewal_rate: float | None
    renewed: int
    not_renewed: int
    new: int


class SponsorPerformanceResponse(Contract):
    empty: bool
    tier_options: list[str] = Field(default_factory=list)
    industry_options: list[str] = Field(default_factory=list)
    package_options: list[str] = Field(default_factory=list)
    source_options: list[str] = Field(default_factory=list)
    no_match: bool = False
    sponsors_count: int = 0
    sponsorships_count: int = 0
    events_count: int = 0
    kpis: SponsorKPIs | None = None
    low_sponsors: list[tuple[str, float]] = Field(default_factory=list)
    low_events_count: int = 0
    low_events_sponsors: int = 0
    cards: Table | None = None
    lead_mix: Table | None = None
    revenue_by_tier: Table | None = None
    scorecard: Table | None = None
    low_sponsorships: Table | None = None
    report_options: list[Option] = Field(default_factory=list)
    scope: str = ""


class SponsorReportRequest(ScopeRequest):
    sponsor_candidates: list[str] = _strings()


class SponsorReportPageResponse(Contract):
    empty: bool
    sponsor_options: list[Option] = Field(default_factory=list)
    sponsor_id: str | None = None
    card: Table | None = None
    history: Table | None = None
    lead_mix: Table | None = None
    lead_notes: Table | None = None


class AudienceRequest(ScopeRequest):
    roles: list[str] = _strings()
    seniorities: list[str] = _strings()
    industries: list[str] = _strings()
    statuses: list[str] = _strings()


class AudienceResponse(Contract):
    empty: bool
    role_options: list[str] = Field(default_factory=list)
    seniority_options: list[str] = Field(default_factory=list)
    industry_options: list[str] = Field(default_factory=list)
    status_options: list[str] = Field(default_factory=list)
    registrations_total: int = 0
    audience_count: int = 0
    attendees: int = 0
    confirmed: int = 0
    countries: int = 0
    has_history: bool = False
    returning: Table | None = None
    roles: Table | None = None
    seniority: Table | None = None
    industry: Table | None = None
    country: Table | None = None
    returning_by_event: Table | None = None


class FeedbackRequest(ScopeRequest):
    ratings: list[int] = Field(default_factory=list, max_length=5)
    tracks: list[str] = _strings()


class FeedbackPreview(Contract):
    comments: int          # distinct comments that would be sent
    prepared: int          # distinct comments in the selection
    mentions: int
    tokens: int
    batches: int
    skipped: int


class FeedbackPageResponse(Contract):
    empty: bool
    track_options: list[str] = Field(default_factory=list)
    no_match: bool = False
    summary: FeedbackSummaryModel | None = None
    rating_distribution: Table | None = None
    sentiment_by_event: Table | None = None
    keyword_themes: Table | None = None
    preview: FeedbackPreview | None = None
    fingerprint: str = ""


class InsightsRequest(ScopeRequest):
    kind: Literal["event", "sponsor"] = "event"
    candidates: list[str] = _strings()


class InsightsResponse(Contract):
    empty: bool
    options: list[Option] = Field(default_factory=list)
    subject_id: str | None = None
    subject: str = ""
    task: str = ""
    context: str = ""
    fallback: str = ""
    audiences: list[str] = Field(default_factory=list)


class AboutResponse(Contract):
    portfolio: PortfolioKPIsModel


# --- AI ------------------------------------------------------------------------------------------

class ThemeModel(Contract):
    name: str
    count: int
    example_quote: str


class TaggedCommentModel(Contract):
    comment: str
    mentions: int
    avg_rating: float
    theme: str
    sentiment: str


class FeedbackAnalysisModel(Contract):
    themes: list[ThemeModel]
    overall_sentiment: str
    action_items: list[str]
    tagged: list[TaggedCommentModel]
    unique_comments: int
    mentions: int
    skipped_comments: int
    batches: int
    warnings: list[str] = Field(default_factory=list)


class FeedbackAnalysisRequest(FeedbackRequest):
    pass


class FeedbackAnalysisResponse(Contract):
    result: FeedbackAnalysisModel
    usage: list[CallRecordModel]


class CheckResultModel(Contract):
    item: str
    passed: bool
    detail: str = ""


class SponsorReportModel(Contract):
    markdown: str
    checks: list[CheckResultModel]
    generated_by: str
    warnings: list[str] = Field(default_factory=list)


class SponsorSummaryRequest(SponsorPerformanceRequest):
    sponsor_id: str


class SponsorSummaryResponse(Contract):
    report: SponsorReportModel
    facts: dict[str, Any]
    usage: list[CallRecordModel]


class PitchRequest(InsightsRequest):
    audience: str = Field(max_length=200)
    tone: str = Field(max_length=200)


class PitchResponse(Contract):
    text: str
    usage: list[CallRecordModel]


class ChatMessage(Contract):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=50_000)


class ChatRequest(ScopeRequest):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[ChatMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)


class ToolRecordModel(Contract):
    id: str
    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    table: Table | None = None


class ChartSpecModel(Contract):
    chart_id: str
    source_id: str
    chart_type: str
    x: str
    y: str
    title: str


class SourceModel(Contract):
    """A document passage the answer can cite as [S1], [S2], ..."""

    id: str
    corpus: str
    doc_id: str
    chunk_id: str
    citation: str
    text: str
    score: float


class TraceStepModel(Contract):
    """One step of how the assistant answered: its plan, a tool call, a check, or the answer."""

    step: Literal["plan", "tool", "check", "answer"]
    text: str = ""
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class AgentAnswerModel(Contract):
    text: str
    calculations: list[ToolRecordModel] = Field(default_factory=list)
    charts: list[ChartSpecModel] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tool_calls: int = 0
    sources: list[SourceModel] = Field(default_factory=list)
    trace: list[TraceStepModel] = Field(default_factory=list)
    documents_note: str = ""     # why document search was off for this answer, if it was


class ChatResponse(Contract):
    answer: AgentAnswerModel
    usage: list[CallRecordModel]


# --- documents and RAG Lab (Week 2) -------------------------------------------------------------

CORPUS_NAMES = ("event_reports", "pricing", "sponsor_decks", "operations", "session_abstracts")
Corpus = Literal["event_reports", "pricing", "sponsor_decks", "operations", "session_abstracts"]
ChunkPreset = Literal["fixed", "structure", "semantic"]


class RetrievalSettings(Contract):
    """The RAG Lab tuning panel: which index to search and how (``core.rag.retrieve.RetrievalConfig``)."""

    chunking: ChunkPreset = "structure"
    chunk_size: int | None = Field(None, ge=50, le=1_000)
    overlap: int | None = Field(None, ge=0, le=400)
    mode: Literal["dense", "bm25", "hybrid"] = "hybrid"
    alpha: float = Field(0.5, ge=0, le=1)
    candidates: int = Field(20, ge=1, le=50)
    top_k: int = Field(5, ge=1, le=20)
    rerank: bool = True
    threshold: float = Field(0.05, ge=0, le=1)
    dense_threshold: float = Field(0.45, ge=0, le=1)


class RagSearchRequest(ScopeRequest):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    settings: RetrievalSettings = Field(default_factory=RetrievalSettings)
    corpora: list[Corpus] = Field(default_factory=list, max_length=5)


class CandidateModel(Contract):
    chunk_id: str
    doc_id: str
    corpus: str
    title: str
    citation: str
    text: str
    dense: float
    bm25: float
    hybrid: float
    rerank: float | None
    rank_before: int
    rank_after: int


class RagSearchResponse(Contract):
    question: str
    candidates: list[CandidateModel]
    hit_ids: list[str]
    refused: bool
    best_score: float
    score_name: str
    threshold: float
    seconds: dict[str, float]
    needs_build: bool = False
    note: str = ""


class DocumentInfo(Contract):
    doc_id: str
    corpus: str
    source_type: str
    title: str
    event_id: str = ""
    year: int | None = None


class ChunkRequest(Contract):
    doc_id: str = Field(min_length=1, max_length=100)
    strategy: ChunkPreset = "structure"
    chunk_size: int = Field(400, ge=50, le=1_000)
    overlap: int = Field(0, ge=0, le=400)
    contextual: bool = True


class ChunkModel(Contract):
    chunk_id: str
    index: int
    tokens: int
    page: int
    slide: int
    section: str
    header: str
    body: str


class ChunkResponse(Contract):
    doc_id: str
    title: str
    source_type: str
    units: int
    chunks: list[ChunkModel]
    corpus_tokens: list[int]        # token count of every chunk in the corpus, for the histogram
    key: str                        # the chunking setting's name, e.g. "structure_400_ctx"
    built: bool                     # whether this setting is in the index (searchable)


class MapRequest(Contract):
    chunking: ChunkPreset = "structure"
    question: str | None = Field(None, max_length=MAX_QUESTION_CHARS)


class MapPoint(Contract):
    x: float
    y: float
    corpus: str
    chunk_id: str
    title: str
    section: str
    preview: str


class MapResponse(Contract):
    points: list[MapPoint]
    explained: float
    model: str
    question_point: tuple[float, float] | None = None
    nearest: list[str] = Field(default_factory=list)


class BuildRequest(Contract):
    chunking: ChunkPreset = "structure"
    chunk_size: int | None = Field(None, ge=50, le=1_000)
    overlap: int | None = Field(None, ge=0, le=400)


class BuildResponse(Contract):
    key: str
    collections: list[dict[str, Any]]
    seconds: float
    texts_embedded: int


class ExperimentsResponse(Contract):
    """Evaluation results from Step 10 (scripts/run_eval.py), if any have been run."""

    available: bool
    results: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str = ""
