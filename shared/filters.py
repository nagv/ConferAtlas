"""The global filter state shared by every page, and how a date preset becomes a date range.

The frontend keeps a ``FilterState`` in session state and sends it, with the
browser session's date, to the backend, which narrows the data the same way.
"""
from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, timedelta

ALL_TIME, CUSTOM = "All time", "Custom dates"
DATE_PRESETS = (ALL_TIME, "Last 6 months", "Last 12 months", "Year to date", "Last calendar year", CUSTOM)


def _months_before(day: date, months: int) -> date:
    """Same day ``months`` earlier, clamped to the end of a shorter month (like pandas DateOffset)."""
    years, month_index = divmod(day.month - 1 - months, 12)
    year, month = day.year + years, month_index + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def preset_range(preset: str, today: date) -> tuple[date | None, date | None]:
    """Inclusive (start, end) for a quick-pick preset; (None, None) means no date limit."""
    if preset == ALL_TIME:
        return None, None
    if preset in ("Last 6 months", "Last 12 months"):
        months = 6 if preset == "Last 6 months" else 12
        return _months_before(today, months) + timedelta(days=1), today
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

    def to_json(self) -> dict:
        """JSON-ready form for API requests (dates as ISO strings, tuples as lists)."""
        data = asdict(self)
        for key in ("custom_start", "custom_end"):
            data[key] = data[key].isoformat() if data[key] else None
        return {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}

    @classmethod
    def from_json(cls, data: dict) -> FilterState:
        def day(value):
            return date.fromisoformat(value) if isinstance(value, str) else value
        return cls(
            date_preset=data.get("date_preset", ALL_TIME),
            custom_start=day(data.get("custom_start")),
            custom_end=day(data.get("custom_end")),
            event_types=tuple(data.get("event_types") or ()),
            formats=tuple(data.get("formats") or ()),
            cities=tuple(data.get("cities") or ()),
            event_ids=tuple(data.get("event_ids") or ()),
            sponsor_id=data.get("sponsor_id") or None,
        )


def prune(selected: Iterable, allowed: Iterable) -> tuple:
    """Keep only the selected values that are still allowed, in their original order."""
    allowed = set(allowed)
    return tuple(v for v in selected if v in allowed)


def active_parts(description) -> list[str]:
    """'Label: value' for each described filter that is switched on (``describe`` output)."""
    return [f"{label}: {value}" for label, value in description if value not in ("All", "All time")]
