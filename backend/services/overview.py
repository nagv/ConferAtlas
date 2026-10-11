"""Portfolio overview: headline KPIs vs the previous period, ranked events, mix and trend."""
from __future__ import annotations

from backend.serialize import dataclass_dict, table
from backend.services.selection import scoped
from backend.store import StoredDataset
from core.analysis import (
    event_kpis,
    event_type_mix,
    portfolio_kpis,
    previous_period,
    rank_events,
    registrations_over_time,
)
from shared.models import OverviewRequest, OverviewResponse, Period


def build(ds: StoredDataset, req: OverviewRequest) -> OverviewResponse:
    state, t = scoped(ds, req)
    if t["events.csv"].empty:
        return OverviewResponse(empty=True)
    events = event_kpis(t)
    period, changes = None, []
    start, end = state.date_range(req.today)
    if start and end:  # a closed date range has an equal-length period before it
        prev_start, prev_end = previous_period(start, end)
        period = Period(start=start, end=end, prev_start=prev_start.date(), prev_end=prev_end.date())
        changes = [c.to_dict() for c in ds.comparison(start, end, state.non_date_kwargs(req.today))]
    ranked = rank_events(events, req.sort_by, ascending=req.ascending, top_n=req.top_n)
    return OverviewResponse(
        empty=False, kpis=dataclass_dict(portfolio_kpis(t)), period=period, changes=changes,
        events_total=len(events), ranked=table(ranked), mix=table(event_type_mix(events)),
        trend=table(registrations_over_time(t["registrations.csv"])),
    )
