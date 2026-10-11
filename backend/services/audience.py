"""Audience insights: who registers and attends, where from, and who comes back."""
from __future__ import annotations

from backend.serialize import table
from backend.services.selection import scoped
from backend.store import StoredDataset
from core.analysis import (
    ATTENDANCE_STATUSES,
    SENIORITY_ORDER,
    attendee_history,
    audience_breakdown,
    filter_audience,
    has_person_ids,
    new_vs_returning,
    returning_by_event,
)
from core.filters import prune
from shared.models import AudienceRequest, AudienceResponse


def build(ds: StoredDataset, req: AudienceRequest) -> AudienceResponse:
    _, t = scoped(ds, req)
    regs = t["registrations.csv"]
    if regs.empty:
        return AudienceResponse(empty=True)
    seniorities = [s for s in SENIORITY_ORDER if s in set(regs["seniority"])]
    seniorities += sorted(set(regs["seniority"]) - set(SENIORITY_ORDER))  # any unexpected values last
    out = AudienceResponse(empty=False, role_options=sorted(regs["attendee_role"].unique()),
                           seniority_options=seniorities, industry_options=sorted(regs["industry"].unique()),
                           status_options=list(ATTENDANCE_STATUSES), registrations_total=len(regs))
    audience = filter_audience(
        regs,
        roles=list(prune(req.roles, out.role_options)),
        seniorities=list(prune(req.seniorities, out.seniority_options)),
        industries=list(prune(req.industries, out.industry_options)),
        statuses=list(prune(req.statuses, out.status_options)),
    )
    out.audience_count = len(audience)
    if audience.empty:
        return out

    full = ds.tables
    history = None
    if has_person_ids(full["registrations.csv"]):
        # Use the full history so visits before the selected period still count as "returning".
        history = attendee_history(full["registrations.csv"], full["events.csv"])
        history = history[history["reg_id"].isin(audience["reg_id"])]

    attendees = audience[audience["attended"]]
    out.attendees, out.confirmed = len(attendees), int(audience["status"].eq("confirmed").sum())
    out.countries = int(attendees["country"].nunique())
    out.roles = table(audience_breakdown(audience, "attendee_role"))
    out.seniority = table(audience_breakdown(audience, "seniority"))
    out.industry = table(audience_breakdown(audience, "industry"))
    out.country = table(audience_breakdown(audience, "country"))
    if history is not None:
        out.has_history = True
        returning = new_vs_returning(history)
        out.returning = table(returning)
        if returning["attendees"].sum() != 0:
            out.returning_by_event = table(returning_by_event(history, t["events.csv"]))
    return out
