"""Sponsor performance: leads vs target, cost per lead, renewals, alerts and the sponsor report scope."""
from __future__ import annotations

from datetime import date

from backend.serialize import table
from backend.services.selection import scoped
from backend.store import StoredDataset
from core.analysis import (
    LEAD_SOURCES,
    LEAD_TARGET_ALERT,
    Tables,
    attainment_band,
    filter_sponsorships,
    lead_source_mix,
    renewal_rate,
    revenue_by_tier,
    sponsor_renewals,
    sponsor_scorecards,
    sponsorship_results,
)
from core.filters import describe, prune
from core.schema import TIERS
from shared.filters import FilterState, active_parts
from shared.models import SponsorFilters, SponsorKPIs, SponsorPerformanceRequest, SponsorPerformanceResponse

_FILTER_LABELS = {"tiers": "Tier", "industries": "Industry", "packages": "Package", "sources": "Lead source"}


def options(t: Tables) -> dict[str, list[str]]:
    present = set(t["sponsors.csv"]["tier"])
    return {"tiers": [x for x in TIERS if x in present],
            "industries": sorted(t["sponsors.csv"]["industry"].unique()),
            "packages": sorted(t["sponsorships.csv"]["package"].unique()),
            "sources": list(LEAD_SOURCES)}


def chosen(t: Tables, filters: SponsorFilters) -> dict[str, list[str]]:
    """The page filters with choices the sidebar filters made impossible dropped."""
    allowed = options(t)
    return {k: list(prune(getattr(filters, k), allowed[k])) for k in _FILTER_LABELS}


def report_scope(state: FilterState, picked: dict, history: Tables, today: date) -> str:
    parts = active_parts(describe(state, history, today))
    parts += [f"{_FILTER_LABELS[k]}: {', '.join(v)}" for k, v in picked.items() if v]
    return "; ".join(parts) or "all events and sponsors"


def build(ds: StoredDataset, req: SponsorPerformanceRequest) -> SponsorPerformanceResponse:
    state, t = scoped(ds, req)
    if t["sponsorships.csv"].empty:
        return SponsorPerformanceResponse(empty=True)
    opts = options(t)
    picked = chosen(t, req.sponsor_filters)
    out = SponsorPerformanceResponse(empty=False, tier_options=opts["tiers"], industry_options=opts["industries"],
                                     package_options=opts["packages"], source_options=opts["sources"])
    f = filter_sponsorships(t, **picked)
    if f["sponsorships.csv"].empty:
        out.no_match = True
        return out

    results = sponsorship_results(f).assign(band=lambda d: attainment_band(d["attainment"]))
    cards = sponsor_scorecards(f).assign(band=lambda d: attainment_band(d["attainment"]))
    history = ds.tables
    # Renewal status needs the whole history: a sponsor's earlier years may sit outside the filters.
    renewals = sponsor_renewals(history["sponsorships.csv"], history["events.csv"])
    renewals = renewals[renewals["sponsor_id"].isin(cards["sponsor_id"])]

    spend, leads, target = results["spend"].sum(), int(results["leads"].sum()), int(results["leads_target"].sum())
    counts = renewals["renewal_status"].value_counts()
    out.sponsors_count, out.sponsorships_count = len(cards), len(results)
    out.events_count = int(results["event_id"].nunique())
    out.kpis = SponsorKPIs(
        spend=float(spend), leads=leads, target=target,
        full_contracts=float(f["sponsors.csv"]["contract_value"].sum()),
        booth_visits=int(f["engagement.csv"]["booth_visits"].sum()),
        booth_leads=float(results["leads_booth"].sum()),
        below_count=int((results["attainment"] < LEAD_TARGET_ALERT).sum()), results_count=len(results),
        renewal_rate=renewal_rate(renewals), renewed=int(counts.get("Renewed", 0)),
        not_renewed=int(counts.get("Not renewed", 0)), new=int(counts.get("New", 0)),
    )
    low_sponsors = cards[cards["band"] == "Below 80%"]
    low_events = results[results["band"] == "Below 80%"]
    out.low_sponsors = [(r.sponsor, float(r.attainment)) for r in low_sponsors.itertuples()]
    out.low_events_count, out.low_events_sponsors = len(low_events), int(low_events["sponsor"].nunique())
    out.cards = table(cards)
    out.lead_mix = table(lead_source_mix(f["leads.csv"]))
    out.revenue_by_tier = table(revenue_by_tier(results))

    scorecard = cards.merge(renewals[["sponsor_id", "renewal_status"]], on="sponsor_id", how="left")
    scorecard = scorecard.assign(Status=scorecard["band"], tier=scorecard["tier"].str.title())
    out.scorecard = table(scorecard[[
        "sponsor", "tier", "industry", "events", "leads_target", "leads", "attainment", "Status",
        "events_below_alert", "leads_booth", "leads_demo", "leads_virtual", "spend", "cost_per_lead",
        "renewal_status"]].sort_values("attainment", na_position="last").reset_index(drop=True))
    if not low_events.empty:
        low = low_events[["sponsor", "tier", "event", "start_date", "package", "leads_target", "leads",
                          "attainment", "band"]].rename(columns={"band": "Status"}).sort_values("attainment")
        low["tier"] = low["tier"].str.title()
        out.low_sponsorships = table(low)

    by_name = cards.sort_values("sponsor")
    out.report_options = list(zip(by_name["sponsor_id"], by_name["sponsor"]))
    out.scope = report_scope(state, picked, history, req.today)
    return out
