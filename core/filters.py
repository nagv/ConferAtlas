"""Global filter state and the pure logic behind the sidebar filters.

The UI keeps a ``FilterState`` in session state; this module turns it into
``filter_dataset`` arguments, works out the cascading options (each filter
only offers values that still exist given the earlier choices) and describes
the selection in words. No Streamlit here, so it is fully unit tested.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from core.analysis import Tables

ALL_TIME, CUSTOM = "All time", "Custom dates"
DATE_PRESETS = (ALL_TIME, "Last 6 months", "Last 12 months", "Year to date", "Last calendar year", CUSTOM)


def preset_range(preset: str, today: date) -> tuple[date | None, date | None]:
    """Inclusive (start, end) for a quick-pick preset; (None, None) means no date limit."""
    if preset == ALL_TIME:
        return None, None
    if preset in ("Last 6 months", "Last 12 months"):
        months = 6 if preset == "Last 6 months" else 12
        return (pd.Timestamp(today) - pd.DateOffset(months=months)).date() + timedelta(days=1), today
    if preset == "Year to date":
        return date(today.year, 1, 1), today
    if preset == "Last calendar year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    raise ValueError(f"Unknown date preset {preset!r}; custom dates are stored on the FilterState")


@dataclass(frozen=True)
class FilterState:
    """The filters applied across every page. Empty tuples mean "all"."""

    date_preset: str = ALL_TIME
    custom_start: date | None = None
    custom_end: date | None = None
    event_types: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    sponsor_id: str | None = None

    def date_range(self, today: date) -> tuple[date | None, date | None]:
        if self.date_preset == CUSTOM:
            return self.custom_start, self.custom_end
        return preset_range(self.date_preset, today)

    def to_kwargs(self, today: date) -> dict:
        """Arguments for ``core.analysis.filter_dataset``; only filters that are set."""
        start, end = self.date_range(today)
        kwargs = {"start": start, "end": end, "event_types": self.event_types, "formats": self.formats,
                  "cities": self.cities, "event_ids": self.event_ids,
                  "sponsor_ids": (self.sponsor_id,) if self.sponsor_id else ()}
        return {k: v for k, v in kwargs.items() if v not in (None, ())}

    def non_date_kwargs(self, today: date) -> dict:
        """``to_kwargs`` without the date range, for comparing other periods under the same filters."""
        return {k: v for k, v in self.to_kwargs(today).items() if k not in ("start", "end")}

    @property
    def is_default(self) -> bool:
        return self == FilterState()



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


def prune(selected: Iterable[str], allowed: Iterable[str]) -> tuple[str, ...]:
    """Keep only the selected values that are still allowed, in their original order."""
    allowed = set(allowed)
    return tuple(v for v in selected if v in allowed)


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
