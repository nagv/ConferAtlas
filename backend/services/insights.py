"""AI insights: the metrics context for an event pitch or sponsor brief, and the AI draft itself."""
from __future__ import annotations

from backend.services.selection import pick, scoped
from backend.services.sponsor_report_page import sponsor_options
from backend.store import StoredDataset
from core.ai import (
    EVENT_TASK,
    SPONSOR_TASK,
    build_event_context,
    build_sponsor_context,
    fallback_event_summary,
    fallback_sponsor_summary,
    generate_pitch,
)
from core.ai.llm import UsageRecorder
from core.analysis import event_kpis, feedback_summary, filter_dataset, sponsor_scorecards, sponsorship_results
from core.config import get_settings
from core.filters import event_options
from shared.models import InsightsRequest, InsightsResponse, PitchRequest

EVENT_AUDIENCES = ["Prospective sponsors", "Renewing sponsors", "Executive sponsor committee"]
SPONSOR_AUDIENCES = ["Sponsor's marketing lead", "Sponsor's executive team"]


def build(ds: StoredDataset, req: InsightsRequest) -> InsightsResponse:
    _, t = scoped(ds, req)
    if req.kind == "event":
        if t["events.csv"].empty:
            return InsightsResponse(empty=True)
        options = event_options(t["events.csv"])  # newest first, like the shared event picker
        event_id = pick(req.candidates, list(options))
        scope = filter_dataset(t, event_ids=[event_id])
        ev = event_kpis(scope).iloc[0]
        return InsightsResponse(
            empty=False, options=list(options.items()), subject_id=event_id, subject=ev["name"], task=EVENT_TASK,
            context=build_event_context(ev, sponsorship_results(scope), feedback_summary(scope["feedback.csv"])),
            fallback=fallback_event_summary(ev), audiences=EVENT_AUDIENCES)
    if t["sponsorships.csv"].empty:
        return InsightsResponse(empty=True)
    options = sponsor_options(t)
    sponsor_id = pick(req.candidates, list(options))
    scope = filter_dataset(t, sponsor_ids=[sponsor_id])
    card = sponsor_scorecards(scope).iloc[0]
    return InsightsResponse(
        empty=False, options=list(options.items()), subject_id=sponsor_id, subject=card["sponsor"],
        task=SPONSOR_TASK, context=build_sponsor_context(card, sponsorship_results(scope)),
        fallback=fallback_sponsor_summary(card), audiences=SPONSOR_AUDIENCES)


def pitch(ds: StoredDataset, req: PitchRequest, recorder: UsageRecorder) -> str:
    """The AI-written pitch or brief for the chosen subject (raises the core errors unchanged)."""
    page = build(ds, InsightsRequest(filters=req.filters, today=req.today, kind=req.kind,
                                     candidates=req.candidates))
    if page.empty:
        raise ValueError("Nothing matches the current filters.")
    return generate_pitch(page.subject, page.context, task=page.task, audience=req.audience, tone=req.tone,
                          settings=get_settings(), recorder=recorder)
