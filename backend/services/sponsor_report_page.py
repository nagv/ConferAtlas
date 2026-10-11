"""Sponsor report page: one sponsor's results at every event they sponsored."""
from __future__ import annotations

from backend.serialize import first_row, table
from backend.services.selection import pick, scoped
from backend.store import StoredDataset
from core.analysis import Tables, filter_dataset, lead_notes, lead_source_mix, sponsor_scorecards, sponsorship_results
from shared.models import SponsorReportPageResponse, SponsorReportRequest


def sponsor_options(t: Tables) -> dict[str, str]:
    """Sponsors with at least one sponsorship, by name."""
    active = t["sponsors.csv"][t["sponsors.csv"]["sponsor_id"].isin(t["sponsorships.csv"]["sponsor_id"])]
    active = active.sort_values("name")
    return dict(zip(active["sponsor_id"], active["name"]))


def build(ds: StoredDataset, req: SponsorReportRequest) -> SponsorReportPageResponse:
    _, t = scoped(ds, req)
    if t["sponsorships.csv"].empty:
        return SponsorReportPageResponse(empty=True)
    options = sponsor_options(t)
    sponsor_id = pick(req.sponsor_candidates, list(options))
    scope = filter_dataset(t, sponsor_ids=[sponsor_id])
    return SponsorReportPageResponse(
        empty=False, sponsor_options=list(options.items()), sponsor_id=sponsor_id,
        card=first_row(sponsor_scorecards(scope)), history=table(sponsorship_results(scope)),
        lead_mix=table(lead_source_mix(scope["leads.csv"])), lead_notes=table(lead_notes(scope["leads.csv"])),
    )
