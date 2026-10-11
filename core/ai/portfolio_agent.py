"""'Ask the Portfolio': a LangChain tool-calling agent over the analysis functions.

Guardrails
- Fixed tools only. Each wraps a ``core.analysis`` function and runs on the
  tables it is given: the app passes the globally filtered dataset, so every
  answer respects the sidebar filters.
- Tools return aggregates only (counts, rates, sums per event/sponsor/segment),
  capped at ``MAX_ROWS`` rows. No registration, person or lead records ever
  reach the model; ``assert_no_raw_records`` checks every payload.
- Every tool result is recorded in a ledger with an id (T1, T2, ...). The UI
  shows it under the answer ("View calculations"), and ``make_chart`` can only
  chart a ledger table, so charts use computed results only.
- After the model answers, every number in the text is checked against the
  ledger (allowing rounding, %, K/M/B). Untraceable numbers trigger one
  rewrite request; if any remain, they are returned in ``unverified_numbers``
  so the UI can flag them.
- Limits: tool calls per question, model turns, a per-request timeout, an
  overall deadline, and a cap on how much chat history is sent.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from core import analysis as an
from core.ai.llm import LLMError, UsageRecorder, build_chat_model, resolve_settings, retry_call
from core.config import Settings

MAX_TOOL_CALLS = 6          # per question
MAX_MODEL_TURNS = 5         # model responses per question (tool rounds + final answer)
MAX_ROWS = 15               # rows per tool result
HISTORY_MESSAGES = 6        # past chat messages (3 exchanges) sent with a new question
HISTORY_CHARS = 4_000       # and at most this many characters of them
REQUEST_TIMEOUT_S = 45.0    # shown on the page; the actual value comes from settings (LLM_TIMEOUT_SECONDS)
DEADLINE_S = 120.0

RAW_RECORD_KEYS = {"reg_id", "person_id", "attendee_id", "lead_id", "notes", "comment"}
_RAW_ID_PATTERN = re.compile(r"\b(REG\d{6}|P\d{6}|L\d{6})\b")

SYSTEM_PROMPT = """You answer questions about a conference organizer's event portfolio for sponsors and staff.

Rules:
- Use the tools for every figure. Every number you write must appear in a tool result (you may round it or show it as a percentage). Never estimate, invent or do new arithmetic beyond simple rounding.
- If the tools cannot answer, say so plainly and suggest what could.
- The data is already narrowed by the user's filters; mention that when it matters ("in the current selection").
- To show a chart, first get the data with a tool, then call make_chart with that result's id (e.g. "T1").
- Be concise: a direct answer first, then 2-4 supporting bullet points. Use event and sponsor names, not ids.
- NPS is approximated from 1-5 session ratings (5 = promoter, 1-3 = detractor). Sponsor revenue and cost per lead use each sponsor's contract value split evenly across its events."""


# --- records ------------------------------------------------------------------

@dataclass
class ToolRecord:
    id: str
    tool: str
    args: dict
    result: dict                      # what the model saw (JSON-safe)
    table: pd.DataFrame | None = None  # chartable rows, if any


@dataclass
class ChartSpec:
    chart_id: str
    source_id: str
    chart_type: str
    x: str
    y: str
    title: str


@dataclass
class AgentAnswer:
    text: str
    calculations: list[ToolRecord] = field(default_factory=list)
    charts: list[ChartSpec] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tool_calls: int = 0


class AgentError(RuntimeError):
    """A failure the page can explain. ``kind`` is 'timeout' or 'provider'."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


# --- JSON-safe payloads ---------------------------------------------------------

def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return None if math.isnan(value) or math.isinf(value) else round(value, 4)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _rows(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    return [_clean(r) for r in df[columns].head(MAX_ROWS).to_dict("records")]


def assert_no_raw_records(payload: Any) -> None:
    """Raise if a tool payload contains person-level fields or record ids."""
    if isinstance(payload, dict):
        bad = RAW_RECORD_KEYS & set(payload)
        if bad:
            raise ValueError(f"tool payload contains raw record fields: {sorted(bad)}")
        for v in payload.values():
            assert_no_raw_records(v)
    elif isinstance(payload, list):
        for v in payload:
            assert_no_raw_records(v)
    elif isinstance(payload, str) and _RAW_ID_PATTERN.search(payload):
        raise ValueError("tool payload contains a raw record id")


# --- tool argument schemas ------------------------------------------------------

EventSort = Literal["start_date", "registrations", "attended", "show_up_rate", "nps", "avg_rating", "leads",
                    "lead_attainment", "sponsor_revenue", "cost_per_lead", "budget_variance"]
SponsorSort = Literal["attainment", "leads", "cost_per_lead", "spend", "events"]
Dimension = Literal["attendee_role", "seniority", "industry", "country", "attendance_status", "new_vs_returning"]


class NoArgs(BaseModel):
    pass


class EventKpisArgs(BaseModel):
    event: str | None = Field(None, description="Event name (or part of it) or id; omit for all events")
    sort_by: EventSort = "start_date"
    descending: bool = Field(True, description="True for highest first")
    top_n: int = Field(10, ge=1, le=MAX_ROWS)


class AudienceMixArgs(BaseModel):
    dimension: Dimension = Field(description="How to break the audience down")
    top_n: int = Field(10, ge=1, le=MAX_ROWS)


class CompareEventsArgs(BaseModel):
    events: list[str] = Field(min_length=2, max_length=4, description="2-4 event names (or parts) or ids")


class SponsorOutcomesArgs(BaseModel):
    sponsor: str | None = Field(None, description="Sponsor name (or part of it); omit for all sponsors")
    tier: Literal["platinum", "gold", "silver", "bronze"] | None = None
    sort_by: SponsorSort = "attainment"
    descending: bool = Field(False, description="False puts the lowest first (e.g. furthest below target)")
    top_n: int = Field(10, ge=1, le=MAX_ROWS)


class MakeChartArgs(BaseModel):
    source_id: str = Field(description="Id of an earlier tool result with rows, e.g. 'T1'")
    chart_type: Literal["bar", "line", "pie"]
    x: str = Field(description="Column for categories / x axis, from that result's rows")
    y: str = Field(description="Numeric column for values, from that result's rows")
    title: str


EVENT_COLUMNS = ["name", "event_type", "format", "city", "start_date", "registrations", "attended", "show_up_rate",
                 "avg_rating", "nps", "sponsors", "leads", "leads_target", "lead_attainment", "sponsor_revenue",
                 "cost_per_lead", "budget", "actual_cost", "budget_variance"]
SPONSOR_COLUMNS = ["sponsor", "tier", "industry", "events", "leads_target", "leads", "attainment", "targets_met",
                   "events_below_alert", "leads_booth", "leads_demo", "leads_virtual", "spend", "cost_per_lead"]


# --- the toolkit ----------------------------------------------------------------

class PortfolioToolkit:
    """The agent's tools, bound to one (filtered) dataset, with a ledger of every result.

    ``history`` is the unfiltered dataset, used only where earlier activity
    matters (returning attendees, sponsor renewals), exactly as on the pages.
    """

    def __init__(self, tables: an.Tables, history: an.Tables | None = None, scope: str = "all data"):
        self.t = tables
        self.history = history if history is not None else tables
        self.scope = scope
        self.ledger: list[ToolRecord] = []
        self.charts: list[ChartSpec] = []
        self._events = an.event_kpis(tables)

    # -- helpers --
    def _record(self, tool: str, args: dict, result: dict, table: pd.DataFrame | None = None) -> dict:
        result = _clean(result)
        assert_no_raw_records(result)
        record_id = f"T{len(self.ledger) + 1}"
        result = {"result_id": record_id, **result}
        self.ledger.append(ToolRecord(record_id, tool, args, result, table))
        return result

    def _match(self, query: str, names: pd.Series, ids: pd.Series | None = None) -> list[int]:
        q = query.strip().lower()
        if ids is not None and (exact := ids.str.lower() == q).any():
            return list(np.flatnonzero(exact))
        if (exact := names.str.lower() == q).any():
            return list(np.flatnonzero(exact))
        return list(np.flatnonzero(names.str.lower().str.contains(re.escape(q), regex=True)))

    # -- tools --
    def portfolio_summary(self) -> dict:
        p = an.portfolio_kpis(self.t)
        return self._record("portfolio_summary", {}, {"scope": self.scope, **p.to_dict()})

    def event_kpis(self, event: str | None = None, sort_by: str = "start_date", descending: bool = True,
                   top_n: int = 10) -> dict:
        args = {"event": event, "sort_by": sort_by, "descending": descending, "top_n": top_n}
        events = self._events
        if event:
            hits = self._match(event, events["name"], events["event_id"])
            if not hits:
                return self._record("event_kpis", args, {"error": f"No event matches '{event}' in the current "
                                                                  "selection.", "events_available": len(events)})
            events = events.iloc[hits]
        ranked = an.rank_events(events, sort_by, ascending=not descending, top_n=top_n)
        rows = ranked[EVENT_COLUMNS]
        return self._record("event_kpis", args, {"scope": self.scope, "events_in_selection": len(self._events),
                                                 "events_returned": len(rows), "rows": _rows(rows, EVENT_COLUMNS)},
                            rows.reset_index(drop=True))

    def audience_mix(self, dimension: str, top_n: int = 10) -> dict:
        args = {"dimension": dimension, "top_n": top_n}
        regs = self.t["registrations.csv"]
        if dimension == "attendance_status":
            status = an.attendance_status(regs).value_counts()
            table = pd.DataFrame({"attendance_status": status.index, "registrations": status.to_numpy()})
            table["share"] = table["registrations"] / table["registrations"].sum() if len(regs) else np.nan
        elif dimension == "new_vs_returning":
            if not an.has_person_ids(self.history["registrations.csv"]):
                return self._record("audience_mix", args, {"error": "Repeat attendance needs a person_id column, "
                                                                    "which this dataset does not have."})
            hist = an.attendee_history(self.history["registrations.csv"], self.history["events.csv"])
            table = an.new_vs_returning(hist[hist["reg_id"].isin(regs["reg_id"])])
        else:
            table = an.audience_breakdown(regs, dimension)
        table = table.head(top_n).reset_index(drop=True)
        return self._record("audience_mix", args, {"scope": self.scope, "total_registrations": len(regs),
                                                   "rows": _rows(table, list(table.columns))}, table)

    def compare_events(self, events: list[str]) -> dict:
        args = {"events": events}
        picked, problems = [], []
        for query in events:
            hits = self._match(query, self._events["name"], self._events["event_id"])
            if len(hits) == 1:
                picked.append(hits[0])
            elif not hits:
                problems.append(f"no event matches '{query}'")
            else:
                names = ", ".join(self._events["name"].iloc[hits[:5]])
                problems.append(f"'{query}' matches several events ({names}); be more specific")
        if problems:
            return self._record("compare_events", args, {"error": "; ".join(problems)})
        rows = self._events.iloc[sorted(set(picked))].sort_values("start_date")[EVENT_COLUMNS]
        return self._record("compare_events", args, {"scope": self.scope, "rows": _rows(rows, EVENT_COLUMNS)},
                            rows.reset_index(drop=True))

    def sponsor_outcomes(self, sponsor: str | None = None, tier: str | None = None, sort_by: str = "attainment",
                         descending: bool = False, top_n: int = 10) -> dict:
        args = {"sponsor": sponsor, "tier": tier, "sort_by": sort_by, "descending": descending, "top_n": top_n}
        cards = an.sponsor_scorecards(self.t)
        renewals = an.sponsor_renewals(self.history["sponsorships.csv"], self.history["events.csv"])
        cards = cards.merge(renewals[["sponsor_id", "renewal_status"]], on="sponsor_id", how="left")
        if tier:
            cards = cards[cards["tier"] == tier]
        if sponsor:
            hits = self._match(sponsor, cards["sponsor"].reset_index(drop=True))
            if not hits:
                return self._record("sponsor_outcomes", args, {"error": f"No sponsor matches '{sponsor}' in the "
                                                                        "current selection."})
            cards = cards.iloc[hits]
        total_target, total_leads = int(cards["leads_target"].sum()), int(cards["leads"].sum())
        cards = cards.sort_values(sort_by, ascending=not descending, na_position="last")
        columns = SPONSOR_COLUMNS + ["renewal_status"]
        rows = cards[columns].head(top_n).reset_index(drop=True)
        return self._record("sponsor_outcomes", args, {
            "scope": self.scope, "sponsors_matching": len(cards), "sponsors_returned": len(rows),
            "alert_threshold": an.LEAD_TARGET_ALERT,
            "sponsors_below_threshold": int((cards["attainment"] < an.LEAD_TARGET_ALERT).sum()),
            "total_leads": total_leads, "total_target": total_target,
            "rows": _rows(rows, columns)}, rows)

    def make_chart(self, source_id: str, chart_type: str, x: str, y: str, title: str) -> dict:
        args = {"source_id": source_id, "chart_type": chart_type, "x": x, "y": y, "title": title}
        source = next((r for r in self.ledger if r.id == source_id), None)
        error = None
        if source is None or source.table is None or source.table.empty:
            error = f"'{source_id}' is not an earlier tool result with rows. Chartable ids: " + \
                    (", ".join(r.id for r in self.ledger if r.table is not None and not r.table.empty) or "none")
        elif x not in source.table.columns or y not in source.table.columns:
            error = f"Columns must come from {source_id}: {', '.join(source.table.columns)}"
        elif not pd.api.types.is_numeric_dtype(source.table[y]):
            error = f"'{y}' is not numeric, so it cannot be plotted"
        if error:
            return self._record("make_chart", args, {"error": error})
        chart = ChartSpec(f"C{len(self.charts) + 1}", source_id, chart_type, x, y, title)
        self.charts.append(chart)
        return self._record("make_chart", args, {"chart_id": chart.chart_id, "status": "chart will be shown "
                                                 "under your answer"})

    # -- LangChain wiring --
    def tools(self):
        from langchain_core.tools import StructuredTool

        spec = [
            (self.portfolio_summary, NoArgs, "Headline KPIs for the current selection: events, registrations, "
             "attendees, show-up rate, NPS, sponsor revenue, leads vs target, cost per lead, budget."),
            (self.event_kpis, EventKpisArgs, "KPIs per event (one event by name, or a ranked list)."),
            (self.audience_mix, AudienceMixArgs, "Aggregated audience breakdown by role, seniority, industry, "
             "country, attendance status, or new vs returning attendees. Counts and shares only."),
            (self.compare_events, CompareEventsArgs, "Side-by-side KPIs for 2-4 named events."),
            (self.sponsor_outcomes, SponsorOutcomesArgs, "Sponsor scorecards: leads vs target, attainment, "
             "events below 80% of target, cost per lead, renewal status. Filter by sponsor or tier."),
            (self.make_chart, MakeChartArgs, "Chart rows from an earlier tool result (by its result_id). "
             "Use only columns present in that result."),
        ]
        return [StructuredTool.from_function(func=f, name=f.__name__, description=d, args_schema=s)
                for f, s, d in spec]

    def run(self, name: str, args: dict) -> dict:
        func = {"portfolio_summary": self.portfolio_summary, "event_kpis": self.event_kpis,
                "audience_mix": self.audience_mix, "compare_events": self.compare_events,
                "sponsor_outcomes": self.sponsor_outcomes, "make_chart": self.make_chart}.get(name)
        if func is None:
            return {"error": f"Unknown tool '{name}'"}
        schema = {"portfolio_summary": NoArgs, "event_kpis": EventKpisArgs, "audience_mix": AudienceMixArgs,
                  "compare_events": CompareEventsArgs, "sponsor_outcomes": SponsorOutcomesArgs,
                  "make_chart": MakeChartArgs}[name]
        try:
            parsed = schema.model_validate(args or {})
        except Exception as exc:  # bad arguments go back to the model as an error it can fix
            return {"error": f"Invalid arguments for {name}: {exc}"}
        return func(**parsed.model_dump())


# --- number verification ----------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])([+\-−]?\$?)([+\-−]?\d[\d,]*(?:\.\d+)?)\s?(%|[kKmMbB]\b|pts?\b|points\b)?")
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}


def _number_value(match: re.Match) -> float:
    value = float(match.group(2).replace(",", "").replace("−", "-"))
    if match.group(1).startswith(("-", "−")):
        value = -value
    suffix = (match.group(3) or "").lower()
    return value / 100 if suffix == "%" else value * _SCALE.get(suffix, 1)


def _numbers_in(value: Any) -> list[float]:
    found: list[float] = []
    if isinstance(value, dict):
        for v in value.values():
            found += _numbers_in(v)
    elif isinstance(value, list):
        for v in value:
            found += _numbers_in(v)
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        found.append(float(value))
    elif isinstance(value, str):
        found += [_number_value(m) for m in _NUMBER.finditer(value)]
    return found


def unverified_numbers(answer: str, allowed_sources: list[Any]) -> list[str]:
    """Numbers in ``answer`` that cannot be traced to any of ``allowed_sources``.

    A number matches if it equals an allowed value after rounding to the
    precision shown, as a percentage (0.78 -> 78%), or with K/M/B scaling.
    Markdown list numbering ("1.") is ignored.
    """
    allowed = [a for src in allowed_sources for a in _numbers_in(src)]
    misses = []
    text = re.sub(r"(?m)^\s*\d+[.)]\s", " ", answer)  # list numbering
    for m in _NUMBER.finditer(text):
        token, digits, suffix = m.group(0).strip(), m.group(2), (m.group(3) or "").lower()
        value = _number_value(m)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        tol = 0.5 * 10 ** -decimals + 1e-9
        if suffix == "%":
            tol /= 100
        if suffix in _SCALE:
            tol *= _SCALE[suffix]
        if not any(math.isfinite(a) and abs(a - value) <= tol for a in allowed):
            misses.append(token)
    return list(dict.fromkeys(misses))


# --- the agent loop ---------------------------------------------------------------

def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks (e.g. Anthropic)
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def trim_history(history: list[dict]) -> list[dict]:
    """The last HISTORY_MESSAGES text messages, dropping the oldest until under HISTORY_CHARS."""
    recent = [m for m in history if m.get("role") in ("user", "assistant") and m.get("content")][-HISTORY_MESSAGES:]
    while recent and sum(len(m["content"]) for m in recent) > HISTORY_CHARS:
        recent = recent[1:]
    return recent


def ask_portfolio(
    question: str,
    toolkit: PortfolioToolkit,
    history: list[dict] | None = None,
    settings: Settings | None = None,
    model=None,
    clock: Callable[[], float] = time.monotonic,
    deadline_s: float = DEADLINE_S,
    recorder: UsageRecorder | None = None,
) -> AgentAnswer:
    """Answer ``question`` with the toolkit's tools. ``history`` is prior chat as
    [{'role': 'user'|'assistant', 'content': str}]; only a trimmed tail is sent."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    settings = resolve_settings(settings, model)
    model = model or build_chat_model(settings, "Portfolio chat", recorder)
    bound = model.bind_tools(toolkit.tools())
    messages: list = [SystemMessage(SYSTEM_PROMPT)]
    for m in trim_history(history or []):
        messages.append(HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"]))
    messages.append(HumanMessage(question))

    started, calls, warnings, final = clock(), 0, [], None

    def call(runnable, msgs):
        if clock() - started > deadline_s:
            raise AgentError("timeout", f"Stopped after {deadline_s:.0f} seconds without a final answer.")
        try:
            return retry_call(lambda: runnable.invoke(msgs), settings)
        except LLMError as exc:
            raise AgentError("timeout" if exc.kind == "timeout" else "provider", str(exc)) from exc

    for _ in range(MAX_MODEL_TURNS):
        reply = call(bound, messages)
        messages.append(reply)
        tool_calls = getattr(reply, "tool_calls", None) or []
        if not tool_calls:
            final = _text(reply.content)
            break
        for tc in tool_calls:
            if calls >= MAX_TOOL_CALLS:
                payload = {"error": f"Tool-call limit ({MAX_TOOL_CALLS}) reached. Answer with the results you have."}
                if not any("limit" in w for w in warnings):
                    warnings.append(f"Reached the limit of {MAX_TOOL_CALLS} tool calls for one question.")
            else:
                calls += 1
                payload = toolkit.run(tc["name"], tc.get("args", {}))
            messages.append(ToolMessage(json.dumps(payload, default=str), tool_call_id=tc["id"], name=tc["name"]))
    if final is None:  # ran out of turns while still calling tools: ask for an answer without tools
        warnings.append("The assistant used all its steps; this answer is based on the results gathered so far.")
        messages.append(HumanMessage("Stop calling tools and answer now using only the tool results above."))
        # The tool-bound model is used even here: Anthropic rejects tool history without tool definitions.
        final = _text(call(bound, messages).content) or "I ran out of steps before I could answer."

    # Questions, arguments and error messages can repeat untrusted claims.
    sources = [r.result for r in toolkit.ledger if "error" not in r.result]
    misses = unverified_numbers(final, sources)
    if misses:  # one chance to fix untraceable figures
        messages.append(HumanMessage(f"These numbers in your answer do not appear in any tool result: "
                                     f"{', '.join(misses)}. Rewrite the answer using only numbers from the tool "
                                     f"results (rounding is fine), or remove them. Do not call tools."))
        try:
            revised = _text(call(bound, messages).content)
            if revised.strip():
                final, misses = revised, unverified_numbers(revised, sources)
        except AgentError as exc:
            warnings.append(f"Could not double-check the figures ({exc}).")

    return AgentAnswer(text=final.strip() or "I could not produce an answer.", calculations=list(toolkit.ledger),
                       charts=list(toolkit.charts), unverified_numbers=misses, warnings=warnings, tool_calls=calls)
