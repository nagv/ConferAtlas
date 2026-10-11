from datetime import date

import pytest

from core.analysis import filter_dataset
from core.filters import (
    ALL_TIME,
    CUSTOM,
    FilterState,
    column_options,
    describe,
    event_options,
    narrow_events,
    preset_range,
    prune,
    sponsor_options,
)

TODAY = date(2026, 10, 11)


@pytest.mark.parametrize("preset, expected", [
    (ALL_TIME, (None, None)),
    ("Last 6 months", (date(2026, 4, 12), TODAY)),
    ("Last 12 months", (date(2025, 10, 12), TODAY)),
    ("Year to date", (date(2026, 1, 1), TODAY)),
    ("Last calendar year", (date(2025, 1, 1), date(2025, 12, 31))),
])
def test_preset_range(preset, expected):
    assert preset_range(preset, TODAY) == expected


def test_preset_range_month_end_and_errors():
    assert preset_range("Last 6 months", date(2026, 8, 31)) == (date(2026, 3, 1), date(2026, 8, 31))
    with pytest.raises(ValueError):
        preset_range(CUSTOM, TODAY)
    with pytest.raises(ValueError):
        preset_range("Last fortnight", TODAY)


def test_default_state_has_no_filters():
    state = FilterState()
    assert state.is_default and state.to_kwargs(TODAY) == {}
    assert hash(state) == hash(FilterState())  # hashable, so it can key caches


def test_to_kwargs_only_includes_set_filters():
    state = FilterState(date_preset="Last calendar year", formats=("virtual",), sponsor_id="S1")
    assert state.to_kwargs(TODAY) == {
        "start": date(2025, 1, 1), "end": date(2025, 12, 31), "formats": ("virtual",), "sponsor_ids": ("S1",)}
    assert not state.is_default


def test_custom_dates_including_half_picked_range():
    full = FilterState(date_preset=CUSTOM, custom_start=date(2025, 1, 1), custom_end=date(2025, 3, 31))
    assert full.to_kwargs(TODAY) == {"start": date(2025, 1, 1), "end": date(2025, 3, 31)}
    half = FilterState(date_preset=CUSTOM, custom_start=date(2025, 1, 1))  # end not picked yet
    assert half.to_kwargs(TODAY) == {"start": date(2025, 1, 1)}


def test_narrow_events_cascades(tiny_tables):
    events = tiny_tables["events.csv"]
    ids = lambda df: df["event_id"].tolist()  # noqa: E731
    assert ids(narrow_events(events)) == ["E1", "E2"]  # empty selections = all
    assert ids(narrow_events(events, start=date(2025, 4, 1))) == ["E2"]
    assert ids(narrow_events(events, end=date(2025, 3, 10))) == ["E1"]
    assert ids(narrow_events(events, event_types=["tech"], formats=["virtual"])) == []
    assert ids(narrow_events(events, cities=["Boston"], event_ids=["E1", "E2"])) == ["E2"]


def test_options_follow_earlier_choices(tiny_tables):
    events = tiny_tables["events.csv"]
    assert column_options(events, "format") == ["in-person", "virtual"]
    business = narrow_events(events, event_types=["business"])
    assert column_options(business, "format") == ["virtual"]
    assert event_options(events) == {"E2": "Beta Live", "E1": "Alpha Con"}  # newest first
    assert sponsor_options(tiny_tables, ["E2"]) == {"S1": "Acme"}
    assert sponsor_options(tiny_tables, ["E1", "E2"]) == {"S1": "Acme", "S2": "Brio"}
    assert sponsor_options(tiny_tables, []) == {}


def test_prune_keeps_order_and_drops_unavailable():
    assert prune(["c", "a", "x"], ["a", "b", "c"]) == ("c", "a")
    assert prune([], ["a"]) == ()


def test_describe_default(tiny_tables):
    assert describe(FilterState(), tiny_tables, TODAY) == [
        ("Dates", "All time"), ("Event type", "All"), ("Format", "All"),
        ("City", "All"), ("Event", "All"), ("Sponsor", "All")]


def test_describe_selection_uses_names(tiny_tables):
    state = FilterState(date_preset="Last calendar year", event_types=("tech",), formats=("in-person", "virtual"),
                        event_ids=("E1",), sponsor_id="S2")
    d = dict(describe(state, tiny_tables, TODAY))
    assert d["Dates"] == "Last calendar year (1 Jan 2025 – 31 Dec 2025)"
    assert d["Event type"] == "Tech" and d["Format"] == "In-Person, Virtual"
    assert d["Event"] == "Alpha Con" and d["Sponsor"] == "Brio"


def test_describe_custom_and_long_lists(tiny_tables):
    state = FilterState(date_preset=CUSTOM, custom_start=date(2025, 2, 1), cities=("A", "B", "C", "D"))
    d = dict(describe(state, tiny_tables, TODAY))
    assert d["Dates"] == "1 Feb 2025 – today" and d["City"] == "4 selected"


def test_state_drives_filter_dataset(tiny_tables):
    state = FilterState(date_preset=CUSTOM, custom_start=date(2025, 5, 1), custom_end=date(2025, 6, 30),
                        sponsor_id="S1")
    scoped = filter_dataset(tiny_tables, **state.to_kwargs(TODAY))
    assert scoped["events.csv"]["event_id"].tolist() == ["E2"]
    assert scoped["leads.csv"]["lead_id"].tolist() == ["L6"]


def test_non_date_kwargs_drops_dates_only():
    state = FilterState(date_preset="Year to date", formats=("virtual",), sponsor_id="S1")
    assert state.non_date_kwargs(TODAY) == {"formats": ("virtual",), "sponsor_ids": ("S1",)}

