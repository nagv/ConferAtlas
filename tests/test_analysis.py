"""Tests for core/analysis.py.

Most expected numbers come from the hand-built ``tiny_tables`` fixture (see
conftest.py), so every figure can be checked by hand:
  E1 (2025-03-10, in-person): 4 registered, 3 confirmed, 2 attended;
     ratings 5, 4, 2, 2; S1 got 5 leads / target 4, S2 got 1 / target 2.
  E2 (2025-06-01, virtual): 2 registered, 2 confirmed, 1 attended;
     rating 3; S1 got 1 lead / target 2.
  S1's $1,000 contract covers two events ($500 each); S2's $600 covers one.
"""
import math
from dataclasses import replace

import pandas as pd
import pytest

from core import analysis as a


@pytest.fixture
def empty(tiny_tables):
    return a.filter_dataset(tiny_tables, event_ids=[])


def _nan(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


# --- filter_dataset / with_sponsor_spend --------------------------------------

def test_with_sponsor_spend_splits_contract_across_sponsorships(tiny_tables):
    out = a.with_sponsor_spend(tiny_tables["sponsorships.csv"], tiny_tables["sponsors.csv"])
    spend = out.set_index(["sponsor_id", "event_id"])["spend"]
    assert spend[("S1", "E1")] == spend[("S1", "E2")] == pytest.approx(500)
    assert spend[("S2", "E1")] == pytest.approx(600)


def test_filter_by_event_cascades_to_every_table(tiny_tables):
    f = a.filter_dataset(tiny_tables, event_ids=["E2"])
    assert list(f["events.csv"]["event_id"]) == ["E2"]
    assert set(f["registrations.csv"]["reg_id"]) == {"R5", "R6"}
    assert list(f["sessions.csv"]["session_id"]) == ["X3"]
    assert list(f["leads.csv"]["lead_id"]) == ["L6"]
    assert list(f["sponsors.csv"]["sponsor_id"]) == ["S1"]  # S2 did not sponsor E2


def test_filter_keeps_full_dataset_spend(tiny_tables):
    # S1's spend at E2 stays $500 even though E1 is filtered out.
    f = a.filter_dataset(tiny_tables, event_ids=["E2"])
    assert f["sponsorships.csv"]["spend"].tolist() == [pytest.approx(500)]


def test_filter_by_date_range_type_format_city(tiny_tables):
    ids = lambda **kw: list(a.filter_dataset(tiny_tables, **kw)["events.csv"]["event_id"])  # noqa: E731
    assert ids(start="2025-04-01") == ["E2"]
    assert ids(end="2025-03-10") == ["E1"]  # end is inclusive
    assert ids(start="2025-03-10", end="2025-06-01") == ["E1", "E2"]
    assert ids(event_types=["business"]) == ["E2"]
    assert ids(formats=["in-person"]) == ["E1"]
    assert ids(cities=["Austin", "Boston"]) == ["E1", "E2"]
    assert ids() == ["E1", "E2"]


def test_filter_by_sponsor(tiny_tables):
    f = a.filter_dataset(tiny_tables, sponsor_ids=["S2"])
    assert list(f["events.csv"]["event_id"]) == ["E1"]
    assert list(f["leads.csv"]["lead_id"]) == ["L7"]
    assert list(f["sponsorships.csv"]["sponsor_id"]) == ["S2"]


def test_filter_to_nothing_keeps_columns(tiny_tables, empty):
    for name, df in empty.items():
        assert df.empty and list(df.columns)[:3] == list(tiny_tables[name].columns)[:3], name


# --- feedback -----------------------------------------------------------------

@pytest.mark.parametrize("ratings, expected", [
    ([5, 5, 4, 3, 1], 0.0),
    ([5, 5], 100.0),
    ([1, 2, 3], -100.0),
    ([4, 4], 0.0),  # passives only
    ([], None),
])
def test_nps(ratings, expected):
    assert a.nps(pd.Series(ratings, dtype="int64")) == expected


def test_feedback_summary(tiny_tables):
    fb = a.feedback_summary(a.filter_dataset(tiny_tables, event_ids=["E1"])["feedback.csv"])
    assert isinstance(fb, a.FeedbackSummary)
    assert (fb.responses, fb.with_comment) == (4, 3)  # one blank comment
    assert fb.avg_rating == pytest.approx(3.25)
    assert fb.nps == pytest.approx(-25)  # 1 promoter, 2 detractors, 4 responses
    assert (fb.positive, fb.neutral, fb.negative) == pytest.approx((0.5, 0.0, 0.5))


def test_feedback_summary_empty(empty):
    fb = a.feedback_summary(empty["feedback.csv"])
    assert (fb.responses, fb.with_comment) == (0, 0)
    assert all(v is None for v in (fb.avg_rating, fb.nps, fb.positive, fb.neutral, fb.negative))


def test_top_comments(tiny_tables, empty):
    fb = tiny_tables["feedback.csv"]
    assert a.top_comments(fb, "positive").to_dict("records") == [{"comment": "Great", "mentions": 2}]
    assert a.top_comments(fb, "negative")["comment"].tolist() == ["Too long"]  # blank skipped
    assert a.top_comments(fb, "positive", n=0).empty
    assert list(a.top_comments(empty["feedback.csv"], "neutral").columns) == ["comment", "mentions"]
    with pytest.raises(ValueError, match="sentiment"):
        a.top_comments(fb, "angry")


# --- sponsors -----------------------------------------------------------------

def test_sponsorship_results(tiny_tables):
    res = a.sponsorship_results(tiny_tables)
    assert list(res.columns) == list(a.SPONSORSHIP_COLUMNS)
    s1e1 = res.set_index(["sponsor_id", "event_id"]).loc[("S1", "E1")]
    assert (s1e1["leads"], s1e1["leads_booth"], s1e1["leads_demo"], s1e1["leads_virtual"]) == (5, 3, 2, 0)
    assert s1e1["attainment"] == pytest.approx(1.25) and s1e1["met_target"]
    assert s1e1["spend"] == pytest.approx(500) and s1e1["cost_per_lead"] == pytest.approx(100)
    assert res["met_target"].tolist().count(False) == 2


def test_sponsorship_with_zero_leads_and_zero_target(tiny_tables):
    t = dict(tiny_tables)
    t["leads.csv"] = t["leads.csv"][t["leads.csv"]["sponsor_id"] != "S2"]  # S2 gets no leads
    sp = t["sponsorships.csv"].copy()
    sp.loc[sp["event_id"] == "E2", "leads_target"] = 0  # S1 has no target at E2
    t["sponsorships.csv"] = sp
    res = a.sponsorship_results(t).set_index(["sponsor_id", "event_id"])
    s2 = res.loc[("S2", "E1")]
    assert s2["leads"] == 0 and s2["attainment"] == 0 and not s2["met_target"]
    assert math.isnan(s2["cost_per_lead"])  # spend but no leads: undefined, not inf
    assert math.isnan(res.loc[("S1", "E2"), "attainment"])  # zero target
    assert res.loc[("S1", "E2"), "met_target"]  # 1 lead >= target of 0


def test_sponsorship_results_empty(empty):
    res = a.sponsorship_results(empty)
    assert res.empty and list(res.columns) == list(a.SPONSORSHIP_COLUMNS)


def test_sponsor_scorecards(tiny_tables):
    cards = a.sponsor_scorecards(tiny_tables)
    assert list(cards.columns) == list(a.SCORECARD_COLUMNS)
    assert cards["sponsor"].tolist() == ["Acme", "Brio"]
    s1 = cards.set_index("sponsor_id").loc["S1"]
    assert (s1["events"], s1["leads"], s1["leads_target"], s1["targets_met"], s1["targets_missed"]) == (2, 6, 6, 1, 1)
    assert s1["attainment"] == pytest.approx(1.0)
    assert s1["spend"] == pytest.approx(1000) and s1["cost_per_lead"] == pytest.approx(1000 / 6)
    assert (s1["best_event"], s1["best_event_leads"]) == ("Alpha Con", 5)
    assert (s1["leads_booth"], s1["leads_demo"], s1["leads_virtual"]) == (3, 2, 1)


def test_sponsor_scorecards_respect_filters(tiny_tables):
    cards = a.sponsor_scorecards(a.filter_dataset(tiny_tables, event_ids=["E2"]))
    s1 = cards.set_index("sponsor_id").loc["S1"]
    assert (s1["events"], s1["leads"], s1["spend"]) == (1, 1, pytest.approx(500))


def test_sponsor_scorecards_empty(empty):
    cards = a.sponsor_scorecards(empty)
    assert cards.empty and list(cards.columns) == list(a.SCORECARD_COLUMNS)


def test_lead_source_mix_and_notes(tiny_tables, empty):
    s1 = a.filter_dataset(tiny_tables, sponsor_ids=["S1"])["leads.csv"]
    assert a.lead_source_mix(s1).to_dict() == {"booth": 3, "demo": 2, "virtual": 1}
    assert a.lead_notes(s1).iloc[0].to_dict() == {"note": "Hot lead", "leads": 3}
    assert a.lead_source_mix(empty["leads.csv"]).to_dict() == {"booth": 0, "demo": 0, "virtual": 0}
    assert list(a.lead_notes(empty["leads.csv"]).columns) == ["note", "leads"]


# --- events -------------------------------------------------------------------

def test_event_kpis(tiny_tables):
    ev = a.event_kpis(tiny_tables)
    assert list(ev.columns) == list(a.EVENT_COLUMNS)
    e1, e2 = ev.set_index("event_id").loc["E1"], ev.set_index("event_id").loc["E2"]
    assert (e1["registrations"], e1["confirmed"], e1["attended"], e1["no_shows"]) == (4, 3, 2, 1)
    assert e1["show_up_rate"] == pytest.approx(2 / 3) and e1["no_show_rate"] == pytest.approx(1 / 3)
    assert e1["avg_rating"] == pytest.approx(3.25) and e1["nps"] == pytest.approx(-25)
    assert (e1["sponsors"], e1["leads"], e1["leads_target"], e1["sponsors_met_target"]) == (2, 6, 6, 1)
    assert e1["sponsor_revenue"] == pytest.approx(1100) and e1["cost_per_lead"] == pytest.approx(1100 / 6)
    assert e1["budget_variance"] == pytest.approx(0.1) and e1["cost_per_attendee"] == pytest.approx(550)
    assert e2["nps"] == pytest.approx(-100) and e2["budget_variance"] == pytest.approx(-0.1)


def test_event_with_no_activity(tiny_tables):
    t = dict(tiny_tables)
    extra = t["events.csv"].iloc[[0]].assign(event_id="E3", name="Ghost Event", budget=0.0)
    t["events.csv"] = pd.concat([t["events.csv"], extra], ignore_index=True)
    e3 = a.event_kpis(t).set_index("event_id").loc["E3"]
    assert (e3["registrations"], e3["sessions"], e3["sponsors"], e3["leads"]) == (0, 0, 0, 0)
    for col in ("show_up_rate", "avg_rating", "nps", "lead_attainment", "cost_per_lead", "budget_variance"):
        assert math.isnan(e3[col]), col


def test_event_kpis_empty(empty):
    ev = a.event_kpis(empty)
    assert ev.empty and list(ev.columns) == list(a.EVENT_COLUMNS)


def test_registration_timeline(tiny_tables):
    timeline = a.registration_timeline(tiny_tables["registrations.csv"].query("event_id == 'E1'"), "2025-03-10")
    assert list(timeline.columns) == list(a.TIMELINE_COLUMNS)
    assert timeline["days_before"].tolist() == list(range(9, -1, -1))
    assert timeline.iloc[0]["registrations"] == 2 and timeline.iloc[-1]["cumulative"] == 4


def test_registration_timeline_includes_walk_ins_and_handles_empty(tiny_tables, empty):
    regs = tiny_tables["registrations.csv"].query("event_id == 'E2'")  # 1 and 2 days before
    walk_in = regs.iloc[[0]].assign(reg_id="R7", reg_date=pd.Timestamp("2025-06-02"))
    timeline = a.registration_timeline(pd.concat([regs, walk_in]), "2025-06-01")
    assert timeline["days_before"].tolist() == [2, 1, 0, -1]
    assert timeline.iloc[-1]["cumulative"] == 3
    assert a.registration_timeline(empty["registrations.csv"], "2025-06-01").empty


# --- sessions -----------------------------------------------------------------

def test_session_performance(tiny_tables):
    s = a.session_performance(tiny_tables)
    assert list(s.columns) == list(a.SESSION_COLUMNS)
    assert s["session_id"].tolist() == ["X1", "X3", "X2"]  # best rated first
    x1 = s.set_index("session_id").loc["X1"]
    assert x1["fill_rate"] == pytest.approx(0.2) and x1["avg_watch_minutes"] == pytest.approx(35)
    assert x1["nps"] == pytest.approx(50) and x1["feedback_count"] == 2 and x1["booth_visits"] == 2
    assert x1["polls_per_attendee"] == pytest.approx(0.5)


def test_session_without_feedback_or_engagement(tiny_tables):
    t = dict(tiny_tables)
    t["feedback.csv"] = t["feedback.csv"][t["feedback.csv"]["session_id"] != "X3"]
    t["engagement.csv"] = t["engagement.csv"][t["engagement.csv"]["session_id"] != "X3"]
    sessions = t["sessions.csv"].copy()
    sessions.loc[sessions["session_id"] == "X2", "capacity"] = 0
    t["sessions.csv"] = sessions
    s = a.session_performance(t).set_index("session_id")
    assert s.loc["X3", "feedback_count"] == 0 and s.loc["X3", "booth_visits"] == 0
    assert math.isnan(s.loc["X3", "nps"]) and math.isnan(s.loc["X3", "avg_watch_minutes"])
    assert math.isnan(s.loc["X2", "fill_rate"])  # zero capacity


def test_session_performance_empty(empty):
    s = a.session_performance(empty)
    assert s.empty and list(s.columns) == list(a.SESSION_COLUMNS)


def test_track_performance(tiny_tables, empty):
    tracks = a.track_performance(tiny_tables)
    assert list(tracks.columns) == list(a.TRACK_COLUMNS)
    assert tracks["track"].tolist() == ["AI", "Sales", "Cloud"]
    ai = tracks.set_index("track").loc["AI"]
    assert (ai["sessions"], ai["attendance"], ai["feedback_count"]) == (1, 2, 2)
    assert ai["avg_rating"] == pytest.approx(4.5)
    assert a.track_performance(empty).empty


# --- audience -----------------------------------------------------------------

def test_audience_breakdown(tiny_tables):
    out = a.audience_breakdown(tiny_tables["registrations.csv"], "country")
    assert list(out.columns) == ["country", "registrations", "share", "confirmed", "attended", "show_up_rate"]
    canada = out.iloc[0]
    assert canada["country"] == "Canada" and canada["registrations"] == 4
    assert canada["share"] == pytest.approx(4 / 6) and canada["show_up_rate"] == pytest.approx(2 / 3)
    assert out["share"].sum() == pytest.approx(1.0)


def test_audience_breakdown_zero_confirmed_and_errors(tiny_tables, empty):
    regs = tiny_tables["registrations.csv"].query("reg_id == 'R4'")  # cancelled only
    out = a.audience_breakdown(regs, "seniority")
    assert out.iloc[0]["confirmed"] == 0 and math.isnan(out.iloc[0]["show_up_rate"])
    assert a.audience_breakdown(empty["registrations.csv"], "industry").empty
    with pytest.raises(ValueError, match="by must be one of"):
        a.audience_breakdown(regs, "email")


# --- portfolio ----------------------------------------------------------------

def test_portfolio_kpis(tiny_tables):
    p = a.portfolio_kpis(tiny_tables)
    assert isinstance(p, a.PortfolioKPIs)
    assert (p.events, p.registrations, p.confirmed, p.attendees) == (2, 6, 5, 3)
    assert p.first_date == pd.Timestamp("2025-03-10") and p.last_date == pd.Timestamp("2025-06-01")
    assert p.show_up_rate == pytest.approx(0.6) and p.no_show_rate == pytest.approx(0.4)
    assert p.nps == pytest.approx(-40)  # ratings 5,4,2,2,3: 1 promoter, 3 detractors
    assert p.avg_session_rating == pytest.approx(3.2)
    assert (p.sponsorships, p.unique_sponsors, p.leads, p.leads_target) == (3, 2, 7, 8)
    assert p.sponsor_revenue == pytest.approx(1600) and p.cost_per_lead == pytest.approx(1600 / 7)
    assert p.lead_attainment == pytest.approx(7 / 8) and p.share_met_target == pytest.approx(1 / 3)
    assert p.budget_variance == pytest.approx(-100 / 3000)


def test_portfolio_kpis_empty(empty):
    p = a.portfolio_kpis(empty)
    assert (p.events, p.registrations, p.attendees, p.leads, p.sponsorships) == (0, 0, 0, 0, 0)
    assert p.sponsor_revenue == 0 and p.budget == 0
    for field in ("first_date", "show_up_rate", "nps", "avg_session_rating", "lead_attainment",
                  "share_met_target", "cost_per_lead", "budget_variance"):
        assert getattr(p, field) is None, field


# --- period over period -------------------------------------------------------

@pytest.fixture
def base(tiny_tables):
    return a.portfolio_kpis(tiny_tables)


def _by_metric(changes):
    return {c.metric: c for c in changes}


def test_compare_kpis_changes_and_direction(base):
    previous = replace(base, leads=5, cost_per_lead=300.0, show_up_rate=0.5)
    c = _by_metric(a.compare_kpis(base, previous))
    assert c["leads"].change == 2 and c["leads"].pct_change == pytest.approx(0.4) and c["leads"].improved
    assert c["cost_per_lead"].change < 0 and c["cost_per_lead"].improved  # lower cost is better
    assert c["show_up_rate"].change == pytest.approx(0.1)
    assert c["show_up_rate"].pct_change is None  # rates report points, not %
    assert c["events"].change == 0 and c["events"].improved is None  # unchanged


def test_compare_kpis_zero_baseline(base):
    previous = replace(base, leads=0, registrations=0, sponsor_revenue=0.0)
    c = _by_metric(a.compare_kpis(base, previous))
    for metric in ("leads", "registrations", "sponsor_revenue"):
        assert c[metric].change > 0 and c[metric].pct_change is None and c[metric].improved, metric


def test_compare_kpis_missing_values(base, empty):
    c = _by_metric(a.compare_kpis(base, a.portfolio_kpis(empty)))
    assert c["nps"].previous is None and c["nps"].change is None and c["nps"].improved is None
    assert c["events"].change == 2 and c["events"].pct_change is None
    assert [m.metric for m in a.compare_kpis(base, base, metrics=["nps", "leads"])] == ["nps", "leads"]


def test_previous_period():
    assert a.previous_period("2025-04-01", "2025-04-30") == (pd.Timestamp("2025-03-02"), pd.Timestamp("2025-03-31"))
    assert a.previous_period("2025-06-01", "2025-06-01") == (pd.Timestamp("2025-05-31"), pd.Timestamp("2025-05-31"))
    with pytest.raises(ValueError):
        a.previous_period("2025-06-30", "2025-06-01")


def test_period_comparison(tiny_tables):
    # Current 2025-04-15..2025-06-15 holds E2; the previous 62 days hold E1.
    c = _by_metric(a.period_comparison(tiny_tables, "2025-04-15", "2025-06-15"))
    assert (c["events"].current, c["events"].previous, c["events"].improved) == (1, 1, None)
    assert c["registrations"].pct_change == pytest.approx(-0.5) and c["registrations"].improved is False
    assert c["nps"].current == pytest.approx(-100) and c["nps"].previous == pytest.approx(-25)


def test_period_comparison_with_empty_previous_period(tiny_tables):
    c = _by_metric(a.period_comparison(tiny_tables, "2025-06-01", "2025-06-30"))
    assert c["events"].previous == 0 and c["events"].pct_change is None
    assert c["show_up_rate"].previous is None and c["show_up_rate"].change is None


def test_period_comparison_applies_filters(tiny_tables):
    c = _by_metric(a.period_comparison(tiny_tables, "2025-04-15", "2025-06-15", formats=["in-person"]))
    assert c["events"].current == 0 and c["events"].previous == 1


# --- full generated dataset ---------------------------------------------------

def test_every_function_runs_on_generated_dataset(dataset):
    p = a.portfolio_kpis(dataset)
    assert p.events == 12 and 0.70 <= p.show_up_rate <= 0.85 and -100 <= p.nps <= 100
    assert len(a.event_kpis(dataset)) == 12
    assert len(a.sponsor_scorecards(dataset)) == dataset["sponsorships.csv"]["sponsor_id"].nunique()
    assert len(a.session_performance(dataset)) == len(dataset["sessions.csv"])
    for dim in a.AUDIENCE_DIMENSIONS:
        assert a.audience_breakdown(dataset["registrations.csv"], dim)["share"].sum() == pytest.approx(1.0)
    assert len(a.period_comparison(dataset, "2025-01-01", "2025-12-31")) == len(a.COMPARABLE_METRICS)
    assert not _nan(a.feedback_summary(dataset["feedback.csv"]).nps)


# --- overview helpers ---------------------------------------------------------

def test_avg_event_nps_weights_events_equally(tiny_tables, empty):
    # E1 NPS -25 (4 ratings), E2 NPS -100 (1 rating): mean -62.5, while pooled NPS is -40.
    p = a.portfolio_kpis(tiny_tables)
    assert p.avg_event_nps == pytest.approx(-62.5) and p.nps == pytest.approx(-40)
    assert a.portfolio_kpis(empty).avg_event_nps is None
    assert "avg_event_nps" in a.COMPARABLE_METRICS


def test_registrations_over_time_fills_quiet_months(tiny_tables, empty):
    regs = tiny_tables["registrations.csv"]  # March (4) and May (2): April must appear as 0
    trend = a.registrations_over_time(regs)
    assert trend["period"].dt.strftime("%Y-%m").tolist() == ["2025-03", "2025-04", "2025-05"]
    assert trend["registrations"].tolist() == [4, 0, 2]
    assert list(a.registrations_over_time(empty["registrations.csv"]).columns) == ["period", "registrations"]


def test_event_type_mix(tiny_tables, empty):
    mix = a.event_type_mix(a.event_kpis(tiny_tables))
    assert mix.to_dict("records") == [
        {"event_type": "business", "events": 1, "share": 0.5, "registrations": 2, "attended": 1},
        {"event_type": "tech", "events": 1, "share": 0.5, "registrations": 4, "attended": 2}]
    assert a.event_type_mix(a.event_kpis(empty)).empty


def test_rank_events(tiny_tables):
    events = a.event_kpis(tiny_tables)
    assert a.rank_events(events, "registrations")["event_id"].tolist() == ["E1", "E2"]
    assert a.rank_events(events, "registrations", ascending=True)["event_id"].tolist() == ["E2", "E1"]
    assert a.rank_events(events, "registrations", top_n=1)["event_id"].tolist() == ["E1"]
    assert a.rank_events(events, "registrations", top_n=0).empty
    assert len(a.rank_events(events, "registrations", top_n=10)) == 2  # fewer events than N


def test_rank_events_puts_missing_values_last_and_validates(tiny_tables, empty):
    events = a.event_kpis(tiny_tables)
    events.loc[events["event_id"] == "E1", "nps"] = float("nan")
    for ascending in (True, False):
        assert a.rank_events(events, "nps", ascending=ascending)["event_id"].tolist() == ["E2", "E1"]
    assert a.rank_events(a.event_kpis(empty), "leads", top_n=5).empty
    with pytest.raises(ValueError, match="Cannot sort by"):
        a.rank_events(events, "popularity")
    with pytest.raises(ValueError, match="top_n"):
        a.rank_events(events, "leads", top_n=-1)


# --- event drill-down ---------------------------------------------------------

def test_filter_sessions(tiny_tables):
    s = a.session_performance(tiny_tables)
    ids = lambda **kw: sorted(a.filter_sessions(s, **kw)["session_id"])  # noqa: E731
    assert ids() == ["X1", "X2", "X3"]
    assert ids(tracks=["AI"]) == ["X1"]
    assert ids(modes=["virtual"]) == ["X3"]
    assert ids(speakers=["Bo Chen", "Cy Diaz"]) == ["X2", "X3"]
    assert ids(min_rating=2.5, max_rating=5) == ["X1", "X3"]
    assert ids(min_rating=4.6) == []
    assert ids(tracks=["AI"], modes=["virtual"]) == []


def test_engagement_by_mode(tiny_tables, empty):
    eng = a.engagement_by_mode(tiny_tables)
    assert list(eng.columns) == list(a.ENGAGEMENT_COLUMNS)
    assert eng["mode"].tolist() == ["in-person", "virtual"]
    inp = eng.iloc[0]
    assert (inp["sessions"], inp["attendees"]) == (2, 4)
    assert inp["avg_watch_minutes"] == pytest.approx(25)
    assert (inp["polls_per_attendee"], inp["questions_per_attendee"], inp["booth_visits_per_attendee"]) == \
        pytest.approx((0.25, 0.25, 0.5))
    assert eng.iloc[1]["avg_watch_minutes"] == pytest.approx(45)
    assert a.engagement_by_mode(empty).empty


# --- audience -------------------------------------------------------------------

@pytest.fixture
def with_people(tiny_tables):
    """P1 attends E1 then returns to E2; P3 is a no-show at E1, so is not 'returning' at E2."""
    t = dict(tiny_tables)
    t["registrations.csv"] = t["registrations.csv"].assign(
        person_id=t["registrations.csv"]["reg_id"].map(
            {"R1": "P1", "R2": "P2", "R3": "P3", "R4": "P4", "R5": "P1", "R6": "P3"}))
    return t


def test_attendance_status_and_filter_audience(tiny_tables):
    regs = tiny_tables["registrations.csv"]
    status = dict(zip(regs["reg_id"], a.attendance_status(regs)))
    assert (status["R1"], status["R3"], status["R4"]) == ("Attended", "No-show", "Cancelled")
    ids = lambda **kw: sorted(a.filter_audience(regs, **kw)["reg_id"])  # noqa: E731
    assert ids(statuses=["No-show"]) == ["R3", "R6"]
    assert ids(roles=["Founder"]) == ["R5", "R6"]
    assert ids(roles=["Founder"], statuses=["Attended"]) == ["R5"]
    assert ids(seniorities=["Mid"], industries=["Software"]) == ["R1", "R2", "R3", "R4"]
    assert len(ids()) == 6


def test_attendee_history(with_people, tiny_tables):
    assert not a.has_person_ids(tiny_tables["registrations.csv"])
    assert a.has_person_ids(with_people["registrations.csv"])
    h = a.attendee_history(with_people["registrations.csv"], with_people["events.csv"]).set_index("reg_id")
    assert h.loc["R5", "prior_events_attended"] == 1 and h.loc["R5", "is_returning"]
    assert not h.loc["R6", "is_returning"]  # P3 registered for E1 but did not attend
    assert not h.loc[["R1", "R2", "R3", "R4"], "is_returning"].any()  # nothing before E1
    with pytest.raises(ValueError, match="person_id"):
        a.attendee_history(tiny_tables["registrations.csv"], tiny_tables["events.csv"])


def test_new_vs_returning_and_by_event(with_people):
    h = a.attendee_history(with_people["registrations.csv"], with_people["events.csv"])
    split = a.new_vs_returning(h)
    assert split["attendees"].tolist() == [2, 1]  # attendees R1, R2 new; R5 returning
    assert split["share"].tolist() == pytest.approx([2 / 3, 1 / 3])
    by_event = a.returning_by_event(h, with_people["events.csv"]).set_index("event_id")
    assert (by_event.loc["E1", "new"], by_event.loc["E1", "returning"]) == (2, 0)
    assert by_event.loc["E2", "returning_share"] == pytest.approx(1.0)


def test_new_vs_returning_with_no_attendees(with_people):
    h = a.attendee_history(with_people["registrations.csv"], with_people["events.csv"])
    no_shows = h[~h["attended"]]
    assert a.new_vs_returning(no_shows)["attendees"].tolist() == [0, 0]
    assert a.new_vs_returning(no_shows)["share"].isna().all()
    assert a.returning_by_event(no_shows, with_people["events.csv"]).empty


# --- sponsor performance --------------------------------------------------------

def test_filter_sponsorships(tiny_tables):
    t = a.filter_dataset(tiny_tables)  # attaches spend first
    gold = a.filter_sponsorships(t, tiers=["gold"])
    assert gold["sponsors.csv"]["sponsor_id"].tolist() == ["S1"]
    assert set(gold["leads.csv"]["sponsor_id"]) == {"S1"}
    assert len(gold["registrations.csv"]) == len(t["registrations.csv"])  # attendee tables untouched
    booth = a.sponsor_scorecards(a.filter_sponsorships(t, sources=["booth"])).set_index("sponsor_id")
    assert booth.loc["S1", "leads"] == 3 and booth.loc["S1", "leads_target"] == 6  # targets still cover all sources
    pkg = a.filter_sponsorships(t, packages=["Virtual Booth"])
    assert pkg["sponsorships.csv"][["sponsor_id", "event_id"]].values.tolist() == [["S1", "E2"]]
    assert pkg["sponsorships.csv"]["spend"].tolist() == [pytest.approx(500)]  # spend kept from full history
    assert a.filter_sponsorships(t, industries=["Nothing"])["sponsorships.csv"].empty


def test_attainment_band_and_events_below_alert(tiny_tables):
    bands = a.attainment_band(pd.Series([0.5, 0.79, 0.8, 0.99, 1.0, 1.4, float("nan")]))
    assert bands.tolist() == ["Below 80%", "Below 80%", "80-99%", "80-99%", "Met target", "Met target", "No target"]
    cards = a.sponsor_scorecards(tiny_tables).set_index("sponsor_id")
    assert cards.loc["S1", "events_below_alert"] == 1  # 1/2 leads at E2 = 50%
    assert cards.loc["S2", "events_below_alert"] == 1


def test_sponsor_renewals(tiny_tables):
    sp = tiny_tables["sponsorships.csv"]
    events = tiny_tables["events.csv"].copy()
    r = a.sponsor_renewals(sp, events).set_index("sponsor_id")
    assert (r.loc["S1", "years_active"], r.loc["S1", "renewal_status"]) == (1, "New")  # both events in 2025
    events.loc[events["event_id"] == "E2", "start_date"] = pd.Timestamp("2026-06-01")
    r = a.sponsor_renewals(sp, events).set_index("sponsor_id")
    assert r.loc["S1", "renewal_status"] == "Renewed"   # 2025 and 2026
    assert r.loc["S2", "renewal_status"] == "Not renewed"  # only March 2025, over 12 months before June 2026
    assert a.renewal_rate(r.reset_index()) == pytest.approx(0.5)
    assert a.sponsor_renewals(sp, events, as_of="2025-12-31").set_index("sponsor_id").loc["S2", "renewal_status"] == "New"
    assert a.sponsor_renewals(sp.iloc[0:0], events).empty


def test_renewal_rate_ignores_new_sponsors():
    renewals = pd.DataFrame({"renewal_status": ["New", "New"]})
    assert a.renewal_rate(renewals) is None
    assert a.renewal_rate(pd.DataFrame({"renewal_status": ["Renewed", "Not renewed", "New"]})) == 0.5


def test_revenue_by_tier(tiny_tables, empty):
    tiers = a.revenue_by_tier(a.sponsorship_results(tiny_tables))
    assert tiers["tier"].tolist() == ["gold", "silver"]  # tier order, not alphabetical
    gold = tiers.iloc[0]
    assert (gold["sponsors"], gold["sponsorships"], gold["leads"]) == (1, 2, 6)
    assert gold["spend"] == pytest.approx(1000) and gold["cost_per_lead"] == pytest.approx(1000 / 6)
    assert a.revenue_by_tier(a.sponsorship_results(empty)).empty


# --- feedback analyzer (rule-based) -----------------------------------------------

def test_rating_distribution(tiny_tables, empty):
    dist = a.rating_distribution(tiny_tables["feedback.csv"])  # ratings 5, 4, 2, 2, 3
    assert dist["rating"].tolist() == [1, 2, 3, 4, 5]
    assert dist["responses"].tolist() == [0, 2, 1, 1, 1]
    assert dist["share"].sum() == pytest.approx(1.0)
    empty_dist = a.rating_distribution(empty["feedback.csv"])
    assert empty_dist["responses"].sum() == 0 and empty_dist["share"].isna().all()


def test_sentiment_by_event(tiny_tables, empty):
    s = a.sentiment_by_event(tiny_tables["feedback.csv"], tiny_tables["events.csv"]).set_index("event_id")
    assert (s.loc["E1", "positive"], s.loc["E1", "negative"], s.loc["E1", "responses"]) == (2, 2, 4)
    assert s.loc["E1", "positive_share"] == pytest.approx(0.5)
    assert s.loc["E2", "neutral_share"] == pytest.approx(1.0)
    assert a.sentiment_by_event(empty["feedback.csv"], empty["events.csv"]).empty


def test_keyword_themes(tiny_tables):
    themes = a.keyword_themes(tiny_tables["feedback.csv"])  # "Great" x2, "Too long", "Fine", one blank
    assert themes.to_dict("records") == [
        {"theme": "Timing", "mentions": 1, "positive_share": 0.0, "example": "Too long"}]
    assert a.keyword_themes(tiny_tables["feedback.csv"].iloc[0:0]).empty
