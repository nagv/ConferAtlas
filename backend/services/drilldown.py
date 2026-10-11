"""Event drill-down: one event's KPIs, registrations, sessions, engagement, sponsors and feedback."""
from __future__ import annotations

import math

from backend.serialize import dataclass_dict, first_row, table
from backend.services.selection import pick, scoped
from backend.store import StoredDataset
from core.analysis import (
    engagement_by_mode,
    event_kpis,
    feedback_summary,
    filter_dataset,
    filter_sessions,
    lead_source_mix,
    registration_timeline,
    session_performance,
    sponsorship_results,
    top_comments,
)
from core.filters import event_options, prune
from core.generator import EARLY_BIRD_DAYS
from shared.models import DrilldownRequest, DrilldownResponse


def build(ds: StoredDataset, req: DrilldownRequest) -> DrilldownResponse:
    _, t = scoped(ds, req)
    if t["events.csv"].empty:
        return DrilldownResponse(empty=True)

    options = event_options(t["events.csv"])  # newest first
    event_id = pick(req.event_candidates, list(options))
    scope = filter_dataset(t, event_ids=[event_id])
    events = event_kpis(scope)
    ev = events.iloc[0]
    sessions = session_performance(scope)
    out = DrilldownResponse(
        empty=False, event_options=list(options.items()), event_id=event_id, event=first_row(events),
        has_sessions=not sessions.empty, sessions_total=len(sessions),
        timeline=table(registration_timeline(scope["registrations.csv"], ev["start_date"])),
        lead_mix=table(lead_source_mix(scope["leads.csv"])),
        early_bird_days=None if ds.kind == "upload" else EARLY_BIRD_DAYS,
        sponsor_results=table(sponsorship_results(scope)),
        feedback=dataclass_dict(feedback_summary(scope["feedback.csv"])),
        top_positive=table(top_comments(scope["feedback.csv"], "positive")),
        top_negative=table(top_comments(scope["feedback.csv"], "negative")),
    )
    fill_mean = sessions["fill_rate"].mean()
    out.sessions_fill_mean = None if math.isnan(fill_mean) else float(fill_mean)
    if sessions.empty:
        return out

    out.track_options = sorted(sessions["track"].unique())
    out.mode_options = sorted(sessions["mode"].unique())
    out.speaker_options = sorted(sessions["speaker"].unique())
    low, high = req.rating
    shown = filter_sessions(sessions, tracks=list(prune(req.tracks, out.track_options)),
                            modes=list(prune(req.modes, out.mode_options)),
                            speakers=list(prune(req.speakers, out.speaker_options)), min_rating=low, max_rating=high)
    out.sessions_shown = table(shown)
    if not shown.empty:
        in_scope = {"sessions.csv": shown,
                    "engagement.csv": scope["engagement.csv"][scope["engagement.csv"]["session_id"].isin(
                        shown["session_id"])]}
        out.engagement = table(engagement_by_mode(in_scope))
    return out
