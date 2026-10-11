"""Pure pandas analysis functions for every ConferAtlas metric.

Conventions
- Inputs are already filtered. Functions that need one table take that
  DataFrame directly; functions that join tables take the filtered dataset
  dict (``Tables``), which ``filter_dataset`` builds consistently.
- Single results are frozen dataclasses; tabular results are DataFrames with
  a fixed column order (the ``*_COLUMNS`` constants), even when empty.
- A rate with a zero denominator is undefined: ``None`` in dataclasses and
  NaN in DataFrames. Empty input never raises.

Notes on the data
- NPS: feedback holds 1-5 ratings, not the 0-10 "would you recommend" scale,
  so NPS is approximated on that scale: 5 = promoter, 4 = passive,
  1-3 = detractor. Range -100..100, like standard NPS.
- Sponsor revenue: the data has one contract_value per sponsor, so it is split
  evenly across every event the sponsor sponsored. This is computed on the
  full dataset before filtering, so a filter never changes a sponsor's
  per-event value.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from core.schema import LEAD_SOURCES, TIERS

Tables = dict[str, pd.DataFrame]

AUDIENCE_DIMENSIONS = ("attendee_role", "seniority", "industry", "country")
SENTIMENTS = ("positive", "neutral", "negative")
LEAD_SOURCE_COLUMNS = tuple(f"leads_{s}" for s in LEAD_SOURCES)
LEAD_TARGET_ALERT = 0.80  # sponsors below this share of their lead target are flagged
_CHILD_TABLES = ("registrations.csv", "sessions.csv", "engagement.csv", "feedback.csv")

EVENT_COLUMNS = (
    "event_id", "name", "event_type", "format", "city", "start_date", "end_date",
    "registrations", "confirmed", "attended", "no_shows", "show_up_rate", "no_show_rate",
    "sessions", "feedback_count", "avg_rating", "nps",
    "sponsors", "sponsors_met_target", "leads", "leads_target", "lead_attainment",
    "leads_per_sponsor", "sponsor_revenue", "cost_per_lead",
    "budget", "actual_cost", "budget_variance", "cost_per_attendee",
)
SESSION_COLUMNS = (
    "session_id", "event_id", "track", "speaker", "mode", "capacity", "attendance", "fill_rate",
    "rating", "feedback_count", "nps", "avg_watch_minutes", "polls_per_attendee",
    "questions_per_attendee", "booth_visits",
)
TRACK_COLUMNS = ("track", "sessions", "attendance", "avg_fill_rate", "avg_rating", "feedback_count")
SPONSORSHIP_COLUMNS = (
    "sponsor_id", "sponsor", "tier", "industry", "event_id", "event", "event_type", "format",
    "start_date", "package", "leads_target", "leads", *LEAD_SOURCE_COLUMNS, "attainment",
    "met_target", "spend", "cost_per_lead",
)
SCORECARD_COLUMNS = (
    "sponsor_id", "sponsor", "tier", "industry", "goals", "contract_value", "events",
    "leads_target", "leads", "attainment", "targets_met", "targets_missed", "events_below_alert",
    *LEAD_SOURCE_COLUMNS,
    "spend", "cost_per_lead", "best_event", "best_event_leads",
)
TIMELINE_COLUMNS = ("days_before", "registrations", "cumulative")


# --- helpers -----------------------------------------------------------------

def _div(num: float, den: float) -> float | None:
    """Scalar division; ``None`` when the denominator is zero (undefined rate)."""
    return float(num) / float(den) if den else None


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """Element-wise division; NaN instead of inf where the denominator is zero."""
    return num.astype(float) / den.astype(float).replace(0, np.nan)


def _empty(columns: Iterable[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _sentiment(ratings: pd.Series) -> pd.Series:
    labels = np.select([ratings >= 4, ratings == 3], ["positive", "neutral"], "negative")
    return pd.Series(labels, index=ratings.index, dtype=object)


# --- filtering ---------------------------------------------------------------

def with_sponsor_spend(sponsorships: pd.DataFrame, sponsors: pd.DataFrame) -> pd.DataFrame:
    """Add ``spend``: each sponsor's contract_value split evenly across its sponsorships."""
    out = sponsorships.copy()
    value = out["sponsor_id"].map(sponsors.set_index("sponsor_id")["contract_value"])
    count = out.groupby("sponsor_id")["event_id"].transform("count")
    out["spend"] = (value / count).astype(float)
    return out


def filter_dataset(
    t: Tables,
    *,
    start: date | str | pd.Timestamp | None = None,
    end: date | str | pd.Timestamp | None = None,
    event_types: Iterable[str] | None = None,
    formats: Iterable[str] | None = None,
    cities: Iterable[str] | None = None,
    event_ids: Iterable[str] | None = None,
    sponsor_ids: Iterable[str] | None = None,
) -> Tables:
    """Return every table restricted to the matching events (and sponsors).

    Events match on start_date within [start, end] (inclusive) and on the
    given types, formats, cities and IDs; every other table is cut to those
    events. ``sponsor_ids`` keeps only those sponsors' sponsorships and leads,
    and only the events they sponsored. Sponsor spend is attached before
    filtering (see module notes).
    """
    sponsorships = t["sponsorships.csv"]
    if "spend" not in sponsorships.columns:
        sponsorships = with_sponsor_spend(sponsorships, t["sponsors.csv"])

    events = t["events.csv"]
    keep = pd.Series(True, index=events.index)
    if start is not None:
        keep &= events["start_date"] >= pd.Timestamp(start)
    if end is not None:
        keep &= events["start_date"] <= pd.Timestamp(end)
    for column, allowed in (("event_type", event_types), ("format", formats),
                            ("city", cities), ("event_id", event_ids)):
        if allowed is not None:
            keep &= events[column].isin(list(allowed))
    leads = t["leads.csv"]
    if sponsor_ids is not None:
        sponsor_ids = list(sponsor_ids)
        sponsorships = sponsorships[sponsorships["sponsor_id"].isin(sponsor_ids)]
        leads = leads[leads["sponsor_id"].isin(sponsor_ids)]
        keep &= events["event_id"].isin(sponsorships["event_id"])
    events = events[keep]

    in_scope = lambda df: df[df["event_id"].isin(events["event_id"])]  # noqa: E731
    out = {"events.csv": events}
    out.update({name: in_scope(t[name]) for name in _CHILD_TABLES})
    out["sponsorships.csv"] = in_scope(sponsorships)
    out["leads.csv"] = in_scope(leads)
    out["sponsors.csv"] = t["sponsors.csv"][
        t["sponsors.csv"]["sponsor_id"].isin(out["sponsorships.csv"]["sponsor_id"])]
    return {name: df.reset_index(drop=True) for name, df in out.items()}


# --- feedback ----------------------------------------------------------------

def nps(ratings: pd.Series) -> float | None:
    """Net Promoter Score from 1-5 ratings (5 promoter, 1-3 detractor); None if empty."""
    if len(ratings) == 0:
        return None
    return float(((ratings >= 5).sum() - (ratings <= 3).sum()) / len(ratings) * 100)


@dataclass(frozen=True)
class FeedbackSummary:
    responses: int
    with_comment: int
    avg_rating: float | None
    nps: float | None
    positive: float | None  # share of ratings 4-5
    neutral: float | None   # share of rating 3
    negative: float | None  # share of ratings 1-2

    def to_dict(self) -> dict:
        return asdict(self)


def feedback_summary(feedback: pd.DataFrame) -> FeedbackSummary:
    """Volume, average, NPS and sentiment split for a slice of feedback."""
    ratings = feedback["rating"]
    counts = _sentiment(ratings).value_counts()
    n = len(ratings)
    return FeedbackSummary(
        responses=n,
        with_comment=int(feedback["comment"].notna().sum()),
        avg_rating=float(ratings.mean()) if n else None,
        nps=nps(ratings),
        positive=_div(counts.get("positive", 0), n),
        neutral=_div(counts.get("neutral", 0), n),
        negative=_div(counts.get("negative", 0), n),
    )


def top_comments(feedback: pd.DataFrame, sentiment: str, n: int = 5) -> pd.DataFrame:
    """Most frequent non-blank comments of one sentiment, with counts."""
    if sentiment not in SENTIMENTS:
        raise ValueError(f"sentiment must be one of {SENTIMENTS}, not {sentiment!r}")
    commented = feedback[feedback["comment"].notna()]
    counts = commented.loc[_sentiment(commented["rating"]) == sentiment, "comment"].value_counts().head(n)
    return pd.DataFrame({"comment": counts.index.astype(object), "mentions": counts.to_numpy(dtype=int)})


# --- sponsors ----------------------------------------------------------------

def _lead_counts(leads: pd.DataFrame) -> pd.DataFrame:
    """Leads per sponsor and event, one column per source."""
    if leads.empty:
        return _empty(["sponsor_id", "event_id", *LEAD_SOURCE_COLUMNS])
    counts = (pd.crosstab([leads["sponsor_id"], leads["event_id"]], leads["source"])
              .reindex(columns=list(LEAD_SOURCES), fill_value=0)
              .add_prefix("leads_").reset_index())
    counts.columns.name = None
    return counts


def sponsorship_results(t: Tables) -> pd.DataFrame:
    """One row per sponsor per event: leads vs target, source mix, spend and cost per lead."""
    sponsorships = t["sponsorships.csv"]
    if "spend" not in sponsorships.columns:
        sponsorships = with_sponsor_spend(sponsorships, t["sponsors.csv"])
    out = sponsorships.merge(_lead_counts(t["leads.csv"]), on=["sponsor_id", "event_id"], how="left")
    cols = list(LEAD_SOURCE_COLUMNS)
    out[cols] = out[cols].fillna(0).astype(int)
    out["leads"] = out[cols].sum(axis=1).astype(int)

    sponsors = t["sponsors.csv"][["sponsor_id", "name", "tier", "industry"]].rename(columns={"name": "sponsor"})
    events = t["events.csv"][["event_id", "name", "event_type", "format", "start_date"]]
    out = out.merge(sponsors, on="sponsor_id").merge(events.rename(columns={"name": "event"}), on="event_id")
    out["attainment"] = _ratio(out["leads"], out["leads_target"])
    out["met_target"] = (out["leads"] >= out["leads_target"]).astype(bool)
    out["cost_per_lead"] = _ratio(out["spend"], out["leads"])
    return out[list(SPONSORSHIP_COLUMNS)].sort_values(["start_date", "sponsor"]).reset_index(drop=True)


def sponsor_scorecards(t: Tables) -> pd.DataFrame:
    """One row per sponsor across the filtered events: leads vs target, cost per lead, best event."""
    results = sponsorship_results(t)
    if results.empty:
        return _empty(SCORECARD_COLUMNS)
    sums = {c: (c, "sum") for c in ("leads_target", "leads", "spend", *LEAD_SOURCE_COLUMNS)}
    results = results.assign(below_alert=results["attainment"] < LEAD_TARGET_ALERT)
    cards = results.groupby("sponsor_id").agg(
        events=("event_id", "size"), targets_met=("met_target", "sum"),
        events_below_alert=("below_alert", "sum"), **sums)
    best = results.loc[results.groupby("sponsor_id")["leads"].idxmax(), ["sponsor_id", "event", "leads"]]
    best = best.set_index("sponsor_id").rename(columns={"event": "best_event", "leads": "best_event_leads"})
    profile = t["sponsors.csv"].set_index("sponsor_id")[["name", "tier", "industry", "goals", "contract_value"]]
    cards = cards.join(best).join(profile.rename(columns={"name": "sponsor"})).reset_index()
    cards["targets_missed"] = cards["events"] - cards["targets_met"]
    cards["attainment"] = _ratio(cards["leads"], cards["leads_target"])
    cards["cost_per_lead"] = _ratio(cards["spend"], cards["leads"])
    return cards[list(SCORECARD_COLUMNS)].sort_values("sponsor").reset_index(drop=True)


def lead_source_mix(leads: pd.DataFrame) -> pd.Series:
    """Lead count per source (booth/demo/virtual), zero-filled."""
    return leads["source"].value_counts().reindex(list(LEAD_SOURCES), fill_value=0).astype(int)


def lead_notes(leads: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """Most common non-blank lead notes, with counts."""
    counts = leads["notes"].dropna().value_counts().head(n)
    return pd.DataFrame({"note": counts.index.astype(object), "leads": counts.to_numpy(dtype=int)})


# --- events ------------------------------------------------------------------

def event_kpis(t: Tables) -> pd.DataFrame:
    """One row per event: attendance, sessions, feedback, sponsor results and budget."""
    regs = t["registrations.csv"]
    regs = regs.assign(is_confirmed=regs["status"].eq("confirmed"), did_attend=regs["attended"].astype(bool))
    reg_stats = regs.groupby("event_id").agg(
        registrations=("reg_id", "size"), confirmed=("is_confirmed", "sum"), attended=("did_attend", "sum"))
    session_stats = t["sessions.csv"].groupby("event_id").size().rename("sessions")
    fb = t["feedback.csv"].groupby("event_id")["rating"]
    fb_stats = pd.DataFrame({"feedback_count": fb.size(), "avg_rating": fb.mean(),
                             "nps": fb.apply(nps).astype(float)})
    sponsor_stats = sponsorship_results(t).groupby("event_id").agg(
        sponsors=("sponsor_id", "size"), sponsors_met_target=("met_target", "sum"),
        leads=("leads", "sum"), leads_target=("leads_target", "sum"), sponsor_revenue=("spend", "sum"))

    out = t["events.csv"].join(reg_stats, on="event_id").join(session_stats, on="event_id")
    out = out.join(fb_stats, on="event_id").join(sponsor_stats, on="event_id")
    counts = ["registrations", "confirmed", "attended", "sessions", "feedback_count",
              "sponsors", "sponsors_met_target", "leads", "leads_target"]
    out[counts] = out[counts].fillna(0).astype(int)
    out["sponsor_revenue"] = out["sponsor_revenue"].fillna(0.0).astype(float)
    out[["avg_rating", "nps"]] = out[["avg_rating", "nps"]].astype(float)

    out["no_shows"] = out["confirmed"] - out["attended"]
    out["show_up_rate"] = _ratio(out["attended"], out["confirmed"])
    out["no_show_rate"] = _ratio(out["no_shows"], out["confirmed"])
    out["lead_attainment"] = _ratio(out["leads"], out["leads_target"])
    out["leads_per_sponsor"] = _ratio(out["leads"], out["sponsors"])
    out["cost_per_lead"] = _ratio(out["sponsor_revenue"], out["leads"])
    out["budget_variance"] = _ratio(out["actual_cost"] - out["budget"], out["budget"])
    out["cost_per_attendee"] = _ratio(out["actual_cost"], out["attended"])
    return out[list(EVENT_COLUMNS)].sort_values("start_date").reset_index(drop=True)


def registration_timeline(registrations: pd.DataFrame, event_start: date | str | pd.Timestamp) -> pd.DataFrame:
    """Daily and cumulative registrations by days before the event, oldest first."""
    if registrations.empty:
        return _empty(TIMELINE_COLUMNS)
    days = (pd.Timestamp(event_start) - registrations["reg_date"]).dt.days
    daily = days.value_counts().reindex(range(int(days.max()), min(int(days.min()), 0) - 1, -1), fill_value=0)
    return pd.DataFrame({"days_before": daily.index, "registrations": daily.to_numpy(),
                         "cumulative": daily.cumsum().to_numpy()})


# --- sessions ----------------------------------------------------------------

def session_performance(t: Tables) -> pd.DataFrame:
    """One row per session: fill rate, rating, NPS and engagement; best rated first."""
    engagement = t["engagement.csv"].groupby("session_id").agg(
        avg_watch_minutes=("watch_minutes", "mean"), polls_per_attendee=("polls", "mean"),
        questions_per_attendee=("questions", "mean"), booth_visits=("booth_visits", "sum"))
    fb = t["feedback.csv"].groupby("session_id")["rating"]
    fb_stats = pd.DataFrame({"feedback_count": fb.size(), "nps": fb.apply(nps).astype(float)})
    out = t["sessions.csv"].join(engagement, on="session_id").join(fb_stats, on="session_id")
    out["feedback_count"] = out["feedback_count"].fillna(0).astype(int)
    out["booth_visits"] = out["booth_visits"].fillna(0).astype(int)
    out["fill_rate"] = _ratio(out["attendance"], out["capacity"])
    out = out[list(SESSION_COLUMNS)]
    return out.sort_values(["rating", "attendance"], ascending=False).reset_index(drop=True)


def track_performance(t: Tables) -> pd.DataFrame:
    """Sessions grouped by track: volume, attendance, fill rate and feedback-weighted rating."""
    sessions = t["sessions.csv"]
    if sessions.empty:
        return _empty(TRACK_COLUMNS)
    perf = session_performance(t)
    by_track = perf.groupby("track").agg(sessions=("session_id", "size"), attendance=("attendance", "sum"),
                                         avg_fill_rate=("fill_rate", "mean"),
                                         feedback_count=("feedback_count", "sum"))
    ratings = t["feedback.csv"].merge(sessions[["session_id", "track"]], on="session_id")
    by_track["avg_rating"] = ratings.groupby("track")["rating"].mean()
    out = by_track.reset_index()[list(TRACK_COLUMNS)]
    return out.sort_values("avg_rating", ascending=False).reset_index(drop=True)


# --- audience ----------------------------------------------------------------

def audience_breakdown(registrations: pd.DataFrame, by: str) -> pd.DataFrame:
    """Registrations, share and show-up rate per value of one audience dimension."""
    if by not in AUDIENCE_DIMENSIONS:
        raise ValueError(f"by must be one of {AUDIENCE_DIMENSIONS}, not {by!r}")
    columns = [by, "registrations", "share", "confirmed", "attended", "show_up_rate"]
    if registrations.empty:
        return _empty(columns)
    regs = registrations.assign(is_confirmed=registrations["status"].eq("confirmed"),
                                did_attend=registrations["attended"].astype(bool))
    out = regs.groupby(by).agg(registrations=("reg_id", "size"), confirmed=("is_confirmed", "sum"),
                               attended=("did_attend", "sum")).reset_index()
    out["share"] = out["registrations"] / len(regs)
    out["show_up_rate"] = _ratio(out["attended"], out["confirmed"])
    return out[columns].sort_values(["registrations", by], ascending=[False, True]).reset_index(drop=True)


def registrations_over_time(registrations: pd.DataFrame, freq: str = "MS") -> pd.DataFrame:
    """Registrations per period (default: calendar month, labelled by its first day).

    Periods with no registrations between the first and last one are included
    as zeros, so a line chart does not skip over quiet months.
    """
    if registrations.empty:
        return _empty(("period", "registrations"))
    counts = registrations.set_index("reg_date").resample(freq)["reg_id"].count()
    return pd.DataFrame({"period": counts.index, "registrations": counts.to_numpy(dtype=int)})


def event_type_mix(events: pd.DataFrame) -> pd.DataFrame:
    """Events, registrations and attendees per event type, from ``event_kpis`` rows."""
    columns = ["event_type", "events", "share", "registrations", "attended"]
    if events.empty:
        return _empty(columns)
    out = events.groupby("event_type").agg(events=("event_id", "size"), registrations=("registrations", "sum"),
                                           attended=("attended", "sum")).reset_index()
    out["share"] = out["events"] / out["events"].sum()
    return out[columns].sort_values(["events", "event_type"], ascending=[False, True]).reset_index(drop=True)


def rank_events(events: pd.DataFrame, sort_by: str, ascending: bool = False,
                top_n: int | None = None) -> pd.DataFrame:
    """Sort ``event_kpis`` rows by one column (missing values last) and keep the first ``top_n``."""
    if sort_by not in events.columns:
        raise ValueError(f"Cannot sort by {sort_by!r}; choose one of: {', '.join(events.columns)}")
    if top_n is not None and top_n < 0:
        raise ValueError("top_n must be zero or more (or None for all events)")
    ranked = events.sort_values([sort_by, "start_date"], ascending=[ascending, True], na_position="last",
                                kind="stable")
    return (ranked if top_n is None else ranked.head(top_n)).reset_index(drop=True)


# --- portfolio ---------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioKPIs:
    events: int
    first_date: pd.Timestamp | None
    last_date: pd.Timestamp | None
    registrations: int
    confirmed: int
    attendees: int
    show_up_rate: float | None
    no_show_rate: float | None
    sessions: int
    feedback_count: int
    avg_session_rating: float | None
    nps: float | None             # pooled over every rating
    avg_event_nps: float | None   # mean of per-event NPS (each event counts equally)
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

    def to_dict(self) -> dict:
        return asdict(self)


def portfolio_kpis(t: Tables) -> PortfolioKPIs:
    """Headline KPIs across every event in the (filtered) dataset."""
    events = event_kpis(t)
    results = sponsorship_results(t)
    ratings = t["feedback.csv"]["rating"]
    confirmed, attendees = int(events["confirmed"].sum()), int(events["attended"].sum())
    revenue, leads = float(results["spend"].sum()), int(results["leads"].sum())
    budget, actual = float(events["budget"].sum()), float(events["actual_cost"].sum())
    return PortfolioKPIs(
        events=len(events),
        first_date=events["start_date"].min() if len(events) else None,
        last_date=events["start_date"].max() if len(events) else None,
        registrations=int(events["registrations"].sum()),
        confirmed=confirmed,
        attendees=attendees,
        show_up_rate=_div(attendees, confirmed),
        no_show_rate=_div(confirmed - attendees, confirmed),
        sessions=int(events["sessions"].sum()),
        feedback_count=len(ratings),
        avg_session_rating=float(ratings.mean()) if len(ratings) else None,
        nps=nps(ratings),
        avg_event_nps=float(events["nps"].mean()) if events["nps"].notna().any() else None,
        sponsorships=len(results),
        unique_sponsors=int(results["sponsor_id"].nunique()),
        sponsor_revenue=revenue,
        leads=leads,
        leads_target=int(results["leads_target"].sum()),
        lead_attainment=_div(leads, results["leads_target"].sum()),
        share_met_target=_div(results["met_target"].sum(), len(results)),
        cost_per_lead=_div(revenue, leads),
        budget=budget,
        actual_cost=actual,
        budget_variance=_div(actual - budget, budget),
    )


# --- period over period ------------------------------------------------------

@dataclass(frozen=True)
class MetricChange:
    metric: str
    label: str
    current: float | None
    previous: float | None
    change: float | None       # current - previous (in points for rates and scores)
    pct_change: float | None   # relative change; None for a zero/missing baseline and for rates/scores
    improved: bool | None      # None when unchanged or not comparable

    def to_dict(self) -> dict:
        return asdict(self)


# metric -> (label, higher is better, report a % change). Rates and scores get
# no % change: going from an NPS of -0.4 to -7.6 is not a meaningful "-1,900%".
COMPARABLE_METRICS: dict[str, tuple[str, bool, bool]] = {
    "events": ("Events", True, True),
    "registrations": ("Registrations", True, True),
    "attendees": ("Attendees", True, True),
    "show_up_rate": ("Show-up rate", True, False),
    "nps": ("NPS", True, False),
    "avg_event_nps": ("Avg NPS", True, False),
    "avg_session_rating": ("Avg session rating", True, False),
    "sponsor_revenue": ("Sponsor revenue", True, True),
    "leads": ("Leads", True, True),
    "lead_attainment": ("Leads vs target", True, False),
    "share_met_target": ("Sponsors meeting target", True, False),
    "cost_per_lead": ("Cost per lead", False, True),
    "budget_variance": ("Budget variance", False, False),
}


def compare_kpis(current: PortfolioKPIs, previous: PortfolioKPIs,
                 metrics: Iterable[str] | None = None) -> list[MetricChange]:
    """Change in each metric from ``previous`` to ``current``."""
    changes = []
    for metric in metrics or COMPARABLE_METRICS:
        label, higher_is_better, relative = COMPARABLE_METRICS[metric]
        cur, prev = getattr(current, metric), getattr(previous, metric)
        change = None if cur is None or prev is None else float(cur) - float(prev)
        pct = change / abs(float(prev)) if relative and change is not None and prev != 0 else None
        improved = None if not change else (change > 0) == higher_is_better
        changes.append(MetricChange(metric, label, None if cur is None else float(cur),
                                    None if prev is None else float(prev), change, pct, improved))
    return changes


def previous_period(start: date | str | pd.Timestamp,
                    end: date | str | pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The equal-length window that ends the day before ``start``."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if end < start:
        raise ValueError("end must not be before start")
    prev_end = start - timedelta(days=1)
    return prev_end - (end - start), prev_end


def period_comparison(t: Tables, start: date | str | pd.Timestamp, end: date | str | pd.Timestamp,
                      **filters) -> list[MetricChange]:
    """Compare [start, end] with the preceding equal-length period, under the same filters."""
    prev_start, prev_end = previous_period(start, end)
    current = portfolio_kpis(filter_dataset(t, start=start, end=end, **filters))
    previous = portfolio_kpis(filter_dataset(t, start=prev_start, end=prev_end, **filters))
    return compare_kpis(current, previous)


# --- event drill-down: sessions ------------------------------------------------

ENGAGEMENT_COLUMNS = ("mode", "sessions", "attendees", "avg_watch_minutes", "polls_per_attendee",
                      "questions_per_attendee", "booth_visits_per_attendee")


def filter_sessions(
    sessions: pd.DataFrame,
    *,
    tracks: Iterable[str] = (),
    modes: Iterable[str] = (),
    speakers: Iterable[str] = (),
    min_rating: float | None = None,
    max_rating: float | None = None,
) -> pd.DataFrame:
    """Sessions (e.g. ``session_performance`` rows) matching the filters; empty selections mean all."""
    keep = pd.Series(True, index=sessions.index)
    for column, chosen in (("track", tracks), ("mode", modes), ("speaker", speakers)):
        chosen = list(chosen)
        if chosen:
            keep &= sessions[column].isin(chosen)
    if min_rating is not None:
        keep &= sessions["rating"] >= min_rating
    if max_rating is not None:
        keep &= sessions["rating"] <= max_rating
    return sessions[keep].reset_index(drop=True)


def engagement_by_mode(t: Tables) -> pd.DataFrame:
    """Engagement per session mode (in-person / hybrid / virtual), per attendee of each session.

    Hybrid sessions mix people in the room and online; the data does not say
    which, so hybrid is reported as its own mode.
    """
    if t["engagement.csv"].empty:
        return _empty(ENGAGEMENT_COLUMNS)
    rows = t["engagement.csv"].merge(t["sessions.csv"][["session_id", "mode"]], on="session_id")
    out = rows.groupby("mode").agg(
        sessions=("session_id", "nunique"), attendees=("attendee_id", "size"),
        avg_watch_minutes=("watch_minutes", "mean"), polls_per_attendee=("polls", "mean"),
        questions_per_attendee=("questions", "mean"), booth_visits_per_attendee=("booth_visits", "mean"),
    ).reset_index()
    order = {m: i for i, m in enumerate(("in-person", "hybrid", "virtual"))}
    return out[list(ENGAGEMENT_COLUMNS)].sort_values("mode", key=lambda s: s.map(order)).reset_index(drop=True)


# --- audience -----------------------------------------------------------------

SENIORITY_ORDER = ("Entry", "Mid", "Senior", "Director", "VP", "C-level")
ATTENDANCE_STATUSES = ("Attended", "No-show", "Cancelled")


def attendance_status(registrations: pd.DataFrame) -> pd.Series:
    """'Attended', 'No-show' (confirmed but absent) or 'Cancelled' for each registration."""
    status = np.where(registrations["attended"].astype(bool), "Attended",
                      np.where(registrations["status"].eq("confirmed"), "No-show", "Cancelled"))
    return pd.Series(status, index=registrations.index, dtype=object)


def filter_audience(
    registrations: pd.DataFrame,
    *,
    roles: Iterable[str] = (),
    seniorities: Iterable[str] = (),
    industries: Iterable[str] = (),
    statuses: Iterable[str] = (),
) -> pd.DataFrame:
    """Registrations matching the audience filters; empty selections mean all."""
    keep = pd.Series(True, index=registrations.index)
    for column, chosen in (("attendee_role", roles), ("seniority", seniorities), ("industry", industries)):
        chosen = list(chosen)
        if chosen:
            keep &= registrations[column].isin(chosen)
    statuses = list(statuses)
    if statuses:
        keep &= attendance_status(registrations).isin(statuses)
    return registrations[keep].reset_index(drop=True)


def has_person_ids(registrations: pd.DataFrame) -> bool:
    """True when registrations carry a usable person_id (needed for new vs returning)."""
    return "person_id" in registrations.columns and registrations["person_id"].notna().any()


def attendee_history(registrations: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Registrations plus ``prior_events_attended`` and ``is_returning`` for each row.

    A person is returning at an event if they attended any earlier event (by
    start date). Pass the full, unfiltered history, then narrow the result:
    filtering first would hide earlier visits. Needs ``person_id``.
    """
    if not has_person_ids(registrations):
        raise ValueError("registrations.csv has no person_id column, so repeat attendance is unknown")
    out = registrations.merge(events[["event_id", "start_date"]], on="event_id", how="left")
    out = out.sort_values(["start_date", "reg_id"], kind="stable")
    attended = out["attended"].astype(int)
    out["prior_events_attended"] = (attended.groupby(out["person_id"]).cumsum() - attended).astype(int)
    out["is_returning"] = out["prior_events_attended"] > 0
    return out.drop(columns="start_date").sort_index().reset_index(drop=True)


def new_vs_returning(history: pd.DataFrame) -> pd.DataFrame:
    """Attendees split into first-timers and returning attendees (from ``attendee_history`` rows)."""
    attendees = history[history["attended"].astype(bool)]
    counts = attendees["is_returning"].map({False: "New", True: "Returning"}).value_counts()
    counts = counts.reindex(["New", "Returning"], fill_value=0)
    total = int(counts.sum())
    return pd.DataFrame({"segment": counts.index, "attendees": counts.to_numpy(dtype=int),
                         "share": counts.to_numpy() / total if total else np.nan})


def returning_by_event(history: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """New and returning attendees per event, oldest first."""
    columns = ["event_id", "name", "start_date", "new", "returning", "returning_share"]
    attendees = history[history["attended"].astype(bool)]
    if attendees.empty:
        return _empty(columns)
    counts = attendees.groupby(["event_id", "is_returning"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=[False, True], fill_value=0)
    counts.columns = ["new", "returning"]
    out = events[["event_id", "name", "start_date"]].merge(counts.reset_index(), on="event_id")
    out["returning_share"] = _ratio(out["returning"], out["new"] + out["returning"])
    return out[columns].sort_values("start_date").reset_index(drop=True)


# --- sponsor performance ------------------------------------------------------

ATTAINMENT_BANDS = ("Below 80%", "80-99%", "Met target")
RENEWAL_STATUSES = ("Renewed", "Not renewed", "New")


def filter_sponsorships(
    t: Tables,
    *,
    tiers: Iterable[str] = (),
    industries: Iterable[str] = (),
    packages: Iterable[str] = (),
    sources: Iterable[str] = (),
) -> Tables:
    """Narrow sponsors, sponsorships and leads; events and attendee tables are untouched.

    Tier and industry select sponsors, package selects sponsorships, and source
    selects which leads count. Targets cover leads from every source, so with
    a source filter, attainment compares only part of the leads to the target.
    """
    out = dict(t)
    sponsors = t["sponsors.csv"]
    for column, chosen in (("tier", tiers), ("industry", industries)):
        chosen = list(chosen)
        if chosen:
            sponsors = sponsors[sponsors[column].isin(chosen)]
    sponsorships = t["sponsorships.csv"]
    if "spend" not in sponsorships.columns:
        sponsorships = with_sponsor_spend(sponsorships, t["sponsors.csv"])
    sponsorships = sponsorships[sponsorships["sponsor_id"].isin(sponsors["sponsor_id"])]
    packages = list(packages)
    if packages:
        sponsorships = sponsorships[sponsorships["package"].isin(packages)]
    leads = t["leads.csv"].merge(sponsorships[["sponsor_id", "event_id"]], on=["sponsor_id", "event_id"])
    sources = list(sources)
    if sources:
        leads = leads[leads["source"].isin(sources)]
    out["sponsorships.csv"] = sponsorships.reset_index(drop=True)
    out["leads.csv"] = leads.reset_index(drop=True)
    out["sponsors.csv"] = sponsors[sponsors["sponsor_id"].isin(sponsorships["sponsor_id"])].reset_index(drop=True)
    return out


def attainment_band(attainment: pd.Series) -> pd.Series:
    """'Below 80%', '80-99%' or 'Met target' for each attainment ratio (NaN: no target)."""
    bands = np.select([attainment < LEAD_TARGET_ALERT, attainment < 1, attainment >= 1],
                      list(ATTAINMENT_BANDS), default="No target")
    return pd.Series(bands, index=attainment.index, dtype=object)


def sponsor_renewals(sponsorships: pd.DataFrame, events: pd.DataFrame,
                     as_of: date | str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Renewal status per sponsor, derived from when they sponsored (the data has no renewal field).

    - Renewed: sponsored events in more than one calendar year (they came back).
    - New: only one year so far, and their first event is within 12 months of
      ``as_of`` (default: the latest event date), so it is too early to tell.
    - Not renewed: only one year, and more than 12 months ago.
    Pass the full history; filtering first would hide earlier years.
    """
    columns = ["sponsor_id", "first_event", "last_event", "years_active", "renewal_status"]
    dated = sponsorships.merge(events[["event_id", "start_date"]], on="event_id")
    if dated.empty:
        return _empty(columns)
    as_of = pd.Timestamp(as_of) if as_of is not None else events["start_date"].max()
    out = dated.groupby("sponsor_id").agg(first_event=("start_date", "min"), last_event=("start_date", "max"),
                                          years_active=("start_date", lambda s: s.dt.year.nunique())).reset_index()
    recent = out["first_event"] > as_of - pd.DateOffset(months=12)
    out["renewal_status"] = np.where(out["years_active"] > 1, "Renewed", np.where(recent, "New", "Not renewed"))
    return out[columns]


def renewal_rate(renewals: pd.DataFrame) -> float | None:
    """Renewed / (renewed + not renewed); new sponsors are left out as too early to tell."""
    counts = renewals["renewal_status"].value_counts()
    decided = counts.get("Renewed", 0) + counts.get("Not renewed", 0)
    return _div(counts.get("Renewed", 0), decided)


def revenue_by_tier(results: pd.DataFrame) -> pd.DataFrame:
    """Sponsor revenue, sponsors and leads per tier (platinum first), from ``sponsorship_results`` rows."""
    columns = ["tier", "sponsors", "sponsorships", "spend", "leads", "cost_per_lead"]
    if results.empty:
        return _empty(columns)
    out = results.groupby("tier").agg(sponsors=("sponsor_id", "nunique"), sponsorships=("event_id", "size"),
                                      spend=("spend", "sum"), leads=("leads", "sum")).reset_index()
    out["cost_per_lead"] = _ratio(out["spend"], out["leads"])
    order = {tier: i for i, tier in enumerate(TIERS)}
    return out[columns].sort_values("tier", key=lambda s: s.map(order).fillna(len(order))).reset_index(drop=True)


# --- feedback analyzer (rule-based parts; the LLM part is core/ai/feedback.py) --

# Keyword rules for a quick, free first look at themes. The LLM analysis is the
# considered version; these only match words, so they miss phrasing they don't list.
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Speaker & delivery": ("speaker", "delivery", "rushed", "paced", "engaging", "prepared"),
    "Content depth": ("basic", "depth", "practical", "takeaway", "insightful", "framework", "substance",
                      "abstract", "examples", "overview"),
    "Audio & streaming": ("audio", "stream", "buffering", "sound"),
    "Venue & logistics": ("room", "overcrowded", "hot", "seating", "venue"),
    "Timing": ("long", "late", "early", "overran"),
    "Slides": ("slide",),
    "Demos & Q&A": ("demo", "q&a", "questions"),
    "Sales pitch": ("sales pitch", "pitch"),
}


def rating_distribution(feedback: pd.DataFrame) -> pd.DataFrame:
    """Count and share of each rating 1-5 (all five rows, even when zero)."""
    counts = feedback["rating"].value_counts().reindex(range(1, 6), fill_value=0)
    total = int(counts.sum())
    return pd.DataFrame({"rating": counts.index, "responses": counts.to_numpy(dtype=int),
                         "share": counts.to_numpy() / total if total else np.nan})


def sentiment_by_event(feedback: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Positive / neutral / negative counts and shares per event (from ratings), oldest first."""
    columns = ["event_id", "name", "start_date", "responses", *SENTIMENTS, *(f"{s}_share" for s in SENTIMENTS)]
    if feedback.empty:
        return _empty(columns)
    counts = pd.crosstab(feedback["event_id"], _sentiment(feedback["rating"])).reindex(
        columns=list(SENTIMENTS), fill_value=0)
    counts.columns.name = None
    counts["responses"] = counts.sum(axis=1)
    for s in SENTIMENTS:
        counts[f"{s}_share"] = counts[s] / counts["responses"]
    out = events[["event_id", "name", "start_date"]].merge(counts.reset_index(), on="event_id")
    return out[columns].sort_values("start_date").reset_index(drop=True)


def keyword_themes(feedback: pd.DataFrame) -> pd.DataFrame:
    """Comments matching each keyword theme (a comment can match several), most mentioned first."""
    columns = ["theme", "mentions", "positive_share", "example"]
    comments = feedback.dropna(subset=["comment"])
    rows = []
    lowered = comments["comment"].str.lower()
    for theme, words in THEME_KEYWORDS.items():
        hit = lowered.apply(lambda text, words=words: any(w in text for w in words))
        if hit.any():
            matched = comments[hit]
            rows.append({"theme": theme, "mentions": int(hit.sum()),
                         "positive_share": float((matched["rating"] >= 4).mean()),
                         "example": matched["comment"].value_counts().index[0]})
    if not rows:
        return _empty(columns)
    return pd.DataFrame(rows, columns=columns).sort_values(["mentions", "theme"], ascending=[False, True]) \
        .reset_index(drop=True)
