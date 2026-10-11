"""Global filter state and the pure logic behind the sidebar filters.

The UI keeps a ``FilterState`` in session state; this module turns it into
``filter_dataset`` arguments, works out the cascading options (each filter
only offers values that still exist given the earlier choices) and describes
the selection in words. No Streamlit here, so it is fully unit tested.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from core.analysis import Tables
from shared.filters import ALL_TIME, CUSTOM, DATE_PRESETS, FilterState, preset_range, prune  # noqa: F401


def narrow_events(
    events: pd.DataFrame,
    *,
    start: date | None = None,
    end: date | None = None,
    event_types: Iterable[str] = (),
    formats: Iterable[str] = (),
    cities: Iterable[str] = (),
    event_ids: Iterable[str] = (),
) -> pd.DataFrame:
    """Events matching the given filters; an empty selection means no restriction."""
    keep = pd.Series(True, index=events.index)
    if start is not None:
        keep &= events["start_date"] >= pd.Timestamp(start)
    if end is not None:
        keep &= events["start_date"] <= pd.Timestamp(end)
    for column, chosen in (("event_type", event_types), ("format", formats),
                           ("city", cities), ("event_id", event_ids)):
        chosen = list(chosen)
        if chosen:
            keep &= events[column].isin(chosen)
    return events[keep]


def column_options(events: pd.DataFrame, column: str) -> list[str]:
    """Sorted distinct values of ``column`` among ``events``."""
    return sorted(events[column].dropna().unique().tolist())


def event_options(events: pd.DataFrame) -> dict[str, str]:
    """event_id -> name, newest first."""
    ordered = events.sort_values("start_date", ascending=False)
    return dict(zip(ordered["event_id"], ordered["name"]))


def sponsor_options(t: Tables, event_ids: Iterable[str]) -> dict[str, str]:
    """sponsor_id -> name for sponsors with a sponsorship at any of ``event_ids``, by name."""
    active = t["sponsorships.csv"].loc[t["sponsorships.csv"]["event_id"].isin(list(event_ids)), "sponsor_id"]
    sponsors = t["sponsors.csv"][t["sponsors.csv"]["sponsor_id"].isin(active)].sort_values("name")
    return dict(zip(sponsors["sponsor_id"], sponsors["name"]))


def _fmt_date(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _list(values: tuple[str, ...], names: dict[str, str] | None = None, title: bool = False) -> str:
    if not values:
        return "All"
    labels = [names.get(v, v) if names else v for v in values]
    labels = [label.title() if title else label for label in labels]
    return ", ".join(labels) if len(labels) <= 3 else f"{len(labels)} selected"


def describe(state: FilterState, t: Tables, today: date) -> list[tuple[str, str]]:
    """(label, value) pairs describing the filters in plain words, for the summary card."""
    start, end = state.date_range(today)
    if start is None and end is None:
        dates = state.date_preset if state.date_preset != CUSTOM else ALL_TIME
    else:
        span = f"{_fmt_date(start) if start else 'start'} – {_fmt_date(end) if end else 'today'}"
        dates = span if state.date_preset == CUSTOM else f"{state.date_preset} ({span})"
    sponsor_names = dict(zip(t["sponsors.csv"]["sponsor_id"], t["sponsors.csv"]["name"]))
    event_names = dict(zip(t["events.csv"]["event_id"], t["events.csv"]["name"]))
    return [
        ("Dates", dates),
        ("Event type", _list(state.event_types, title=True)),
        ("Format", _list(state.formats, title=True)),
        ("City", _list(state.cities)),
        ("Event", _list(state.event_ids, event_names)),
        ("Sponsor", sponsor_names.get(state.sponsor_id, state.sponsor_id) if state.sponsor_id else "All"),
    ]
