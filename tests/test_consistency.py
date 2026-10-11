"""Cross-checks: analysis functions must agree with each other and with the raw tables,
and filters must narrow the data consistently. Runs on the full generated dataset."""
from datetime import date

import pytest

from core import analysis as a
from core.filters import DATE_PRESETS, FilterState, column_options, narrow_events
from core.schema import DatasetValidationError, read_csv_bytes, read_tables_from_bytes


# --- CSV reading ------------------------------------------------------------------------

def test_read_csv_bytes_accepts_excel_encodings():
    assert read_csv_bytes("e.csv", "﻿event_id,name\nEV1,Café Summit\n".encode("utf-8")).columns[0] == "event_id"
    df = read_csv_bytes("e.csv", "event_id,name\nEV1,Café Summit\n".encode("cp1252"))
    assert df.loc[0, "name"] == "Café Summit"


@pytest.mark.parametrize("data, message", [
    (b"", "the file is empty"),
    (b"   \n\n", "the file is empty"),
    (b"a,b\n\x00\x01\x02,3\n", "binary file"),
    (b'a,b\n"unterminated,1\n', "could not be read as CSV"),
])
def test_read_csv_bytes_explains_bad_files(data, message):
    with pytest.raises(DatasetValidationError) as exc:
        read_csv_bytes("events.csv", data)
    assert exc.value.errors[0].startswith("events.csv: ") and message in exc.value.errors[0]


def test_read_errors_are_collected_across_files():
    with pytest.raises(DatasetValidationError) as exc:
        read_tables_from_bytes({"events.csv": b"", "leads.csv": b'x\n"bad\n', "sponsors.csv": b"a\n1\n"})
    assert len(exc.value.errors) == 2


# --- analysis functions agree -------------------------------------------------------------

@pytest.fixture(scope="module")
def t(dataset):
    return a.filter_dataset(dataset)


def test_event_rows_add_up_to_the_portfolio(t):
    events, p = a.event_kpis(t), a.portfolio_kpis(t)
    for column, total in (("registrations", p.registrations), ("attended", p.attendees),
                          ("confirmed", p.confirmed), ("leads", p.leads), ("leads_target", p.leads_target),
                          ("sessions", p.sessions)):
        assert events[column].sum() == total, column
    assert events["sponsor_revenue"].sum() == pytest.approx(p.sponsor_revenue)
    assert p.registrations == len(t["registrations.csv"]) and p.feedback_count == len(t["feedback.csv"])


def test_sponsor_views_agree_with_the_leads_table(t):
    cards, results = a.sponsor_scorecards(t), a.sponsorship_results(t)
    assert cards["leads"].sum() == results["leads"].sum() == len(t["leads.csv"])
    assert a.lead_source_mix(t["leads.csv"]).sum() == len(t["leads.csv"])
    assert cards["spend"].sum() == pytest.approx(t["sponsors.csv"]["contract_value"].sum())  # contracts fully allocated
    assert (cards["targets_met"] + cards["targets_missed"] == cards["events"]).all()


def test_breakdowns_cover_everyone(t):
    regs = t["registrations.csv"]
    for dim in a.AUDIENCE_DIMENSIONS:
        b = a.audience_breakdown(regs, dim)
        assert b["registrations"].sum() == len(regs) and b["attended"].sum() == regs["attended"].sum()
    assert a.attendance_status(regs).value_counts().sum() == len(regs)
    assert a.rating_distribution(t["feedback.csv"])["responses"].sum() == len(t["feedback.csv"])
    assert a.event_type_mix(a.event_kpis(t))["events"].sum() == len(t["events.csv"])
    assert a.registrations_over_time(regs)["registrations"].sum() == len(regs)


def test_session_and_engagement_tables_agree(t):
    perf = a.session_performance(t)
    assert len(perf) == len(t["sessions.csv"])
    assert perf["booth_visits"].sum() == t["engagement.csv"]["booth_visits"].sum()
    assert a.engagement_by_mode(t)["attendees"].sum() == len(t["engagement.csv"])


# --- filters narrow consistently -------------------------------------------------------------

def test_event_type_filters_partition_the_portfolio(dataset):
    total = a.portfolio_kpis(a.filter_dataset(dataset))
    parts = [a.portfolio_kpis(a.filter_dataset(dataset, event_types=[x])) for x in ("tech", "business",
                                                                                     "medical", "education")]
    assert sum(p.events for p in parts) == total.events
    assert sum(p.registrations for p in parts) == total.registrations
    assert sum(p.leads for p in parts) == total.leads


def test_filtering_twice_changes_nothing(dataset):
    once = a.filter_dataset(dataset, formats=["hybrid"], cities=["Austin", "San Francisco"])
    twice = a.filter_dataset(once, formats=["hybrid"], cities=["Austin", "San Francisco"])
    for name in once:
        assert len(once[name]) == len(twice[name]), name


def test_sponsor_filter_keeps_only_that_sponsor(dataset):
    sponsor_id = dataset["sponsorships.csv"]["sponsor_id"].iloc[0]
    scoped = a.filter_dataset(dataset, sponsor_ids=[sponsor_id], start="2025-01-01")
    assert set(scoped["sponsorships.csv"]["sponsor_id"]) <= {sponsor_id}
    assert set(scoped["leads.csv"]["sponsor_id"]) <= {sponsor_id}
    assert (scoped["events.csv"]["start_date"] >= "2025-01-01").all()
    assert set(scoped["registrations.csv"]["event_id"]) <= set(scoped["events.csv"]["event_id"])


@pytest.mark.parametrize("preset", [p for p in DATE_PRESETS if p != "Custom dates"])
def test_every_date_preset_produces_valid_filters(dataset, preset):
    state = FilterState(date_preset=preset)
    scoped = a.filter_dataset(dataset, **state.to_kwargs(date(2026, 10, 11)))
    start, end = state.date_range(date(2026, 10, 11))
    if start is not None:
        assert (scoped["events.csv"]["start_date"] >= str(start)).all()
        assert (scoped["events.csv"]["start_date"] <= str(end)).all()
    a.portfolio_kpis(scoped)  # never raises, even when the preset holds no events


def test_cascade_options_only_shrink(dataset):
    events = dataset["events.csv"]
    all_formats = set(column_options(events, "format"))
    for event_type in column_options(events, "event_type"):
        narrowed = narrow_events(events, event_types=[event_type])
        assert set(column_options(narrowed, "format")) <= all_formats
        assert set(column_options(narrowed, "city")) <= set(column_options(events, "city"))


def test_period_comparison_matches_direct_calculation(dataset):
    changes = {c.metric: c for c in a.period_comparison(dataset, "2025-01-01", "2025-12-31")}
    direct = a.portfolio_kpis(a.filter_dataset(dataset, start="2025-01-01", end="2025-12-31"))
    assert changes["registrations"].current == direct.registrations
    assert changes["leads"].current == direct.leads
