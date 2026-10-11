"""Every table the API sends must rebuild in the frontend exactly: same dtypes, values and CSV bytes."""
import json

import pandas as pd
import pytest

from backend.serialize import first_row, json_safe, table
from core import analysis as an
from frontend.components.tables import to_frame


def _round_trip(obj):
    payload = json.loads(json.dumps(table(obj)))  # what actually crosses the wire
    return to_frame(payload)


def _frames(t):
    events = an.event_kpis(t)
    results = an.sponsorship_results(t)
    history = an.attendee_history(t["registrations.csv"], t["events.csv"]) if an.has_person_ids(
        t["registrations.csv"]) else None
    frames = {
        **{name: df for name, df in t.items()},
        "event_kpis": events, "ranked": an.rank_events(events, "nps", top_n=5),
        "mix": an.event_type_mix(events), "trend": an.registrations_over_time(t["registrations.csv"]),
        "results": results.assign(band=an.attainment_band(results["attainment"])),
        "cards": an.sponsor_scorecards(t), "renewals": an.sponsor_renewals(t["sponsorships.csv"], t["events.csv"]),
        "revenue": an.revenue_by_tier(results), "sessions": an.session_performance(t),
        "engagement": an.engagement_by_mode(t), "positive": an.top_comments(t["feedback.csv"], "positive"),
        "ratings": an.rating_distribution(t["feedback.csv"]),
        "sentiment": an.sentiment_by_event(t["feedback.csv"], t["events.csv"]),
        "themes": an.keyword_themes(t["feedback.csv"]), "notes": an.lead_notes(t["leads.csv"]),
        "country": an.audience_breakdown(t["registrations.csv"], "country"),
    }
    if len(events):
        frames["timeline"] = an.registration_timeline(t["registrations.csv"], events["start_date"].iloc[0])
    if history is not None:
        frames["returning"] = an.new_vs_returning(history)
        frames["returning_by_event"] = an.returning_by_event(history, t["events.csv"])
    return frames


@pytest.mark.parametrize("which", ["dataset", "tiny", "empty"])
def test_frames_round_trip_exactly(which, dataset, tiny_tables):
    t = {"dataset": dataset, "tiny": tiny_tables}.get(which) or an.filter_dataset(dataset, event_ids=["none"])
    for name, df in _frames(t).items():
        rebuilt = _round_trip(df)
        pd.testing.assert_frame_equal(rebuilt, df.reset_index(drop=True) if isinstance(df.index, pd.RangeIndex)
                                      else df, check_index_type=False, obj=name)
        assert rebuilt.to_csv(index=False) == df.to_csv(index=False), name


def test_series_round_trip(dataset):
    mix = an.lead_source_mix(dataset["leads.csv"])
    pd.testing.assert_series_equal(_round_trip(mix), mix)
    empty = an.lead_source_mix(dataset["leads.csv"].iloc[0:0])
    pd.testing.assert_series_equal(_round_trip(empty), empty, check_index_type=False)


def test_first_row_keeps_types(dataset):
    events = an.event_kpis(dataset)
    row = to_frame(json.loads(json.dumps(first_row(events)))).iloc[0]
    original = events.iloc[0]
    assert row["start_date"] == original["start_date"] and isinstance(row["start_date"], pd.Timestamp)
    assert (row["end_date"] - row["start_date"]).days == (original["end_date"] - original["start_date"]).days
    assert row.equals(original) or all(
        (pd.isna(a) and pd.isna(b)) or a == b for a, b in zip(row.tolist(), original.tolist()))


def test_json_safe():
    import numpy as np
    assert json_safe({"a": np.int64(3), "b": float("nan"), "c": pd.Timestamp("2025-01-02"), "d": [np.bool_(True)]}) == \
        {"a": 3, "b": None, "c": "2025-01-02T00:00:00", "d": [True]}
