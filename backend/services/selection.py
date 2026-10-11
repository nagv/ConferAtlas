"""Applied-filter scope shared by every page, the sidebar's cascading options and its summary."""
from __future__ import annotations

from datetime import date

from backend.store import StoredDataset
from core.analysis import Tables
from core.filters import column_options, describe, event_options, narrow_events, prune, sponsor_options
from shared.filters import FilterState, active_parts
from shared.models import Filters, FilterOptions, ScopeRequest, ScopeSummary


def scoped(ds: StoredDataset, req: ScopeRequest) -> tuple[FilterState, Tables]:
    """The applied FilterState and the dataset narrowed by it (cached per filter set)."""
    state = req.filters.to_state()
    return state, ds.filtered(state.to_kwargs(req.today))


def scope_text(state: FilterState, full: Tables, today: date, default: str = "all data") -> str:
    """The applied filters in words, as given to the AI assistant and its calculations."""
    return "; ".join(active_parts(describe(state, full, today))) or default


def pick(candidates: list[str], options: list[str]) -> str:
    """The first still-valid candidate (widget value, then shared selection), else the first option."""
    return next((c for c in candidates if c in options), options[0])


def filter_options(ds: StoredDataset, draft: Filters, today: date) -> FilterOptions:
    """Each sidebar list given the earlier draft choices, pruning each level before narrowing."""
    t = ds.tables
    events = t["events.csv"]
    state = draft.to_state()
    start, end = state.date_range(today)
    level = narrow_events(events, start=start, end=end)
    types_options = column_options(level, "event_type")
    level = narrow_events(level, event_types=prune(state.event_types, types_options))
    format_options = column_options(level, "format")
    level = narrow_events(level, formats=prune(state.formats, format_options))
    city_options = column_options(level, "city")
    level = narrow_events(level, cities=prune(state.cities, city_options))
    names = event_options(level)
    level = narrow_events(level, event_ids=prune(state.event_ids, list(names)))
    sponsors = sponsor_options(t, level["event_id"])
    return FilterOptions(event_types=types_options, formats=format_options, cities=city_options,
                         events=list(names.items()), sponsors=list(sponsors.items()),
                         matching_events=len(level), total_events=len(events))


def scope_summary(ds: StoredDataset, req: ScopeRequest) -> ScopeSummary:
    """The summary card and page chips: applied filters in words and what they leave."""
    state, scoped_t = scoped(ds, req)
    return ScopeSummary(description=describe(state, ds.tables, req.today),
                        events=len(scoped_t["events.csv"]),
                        sponsors=int(scoped_t["sponsorships.csv"]["sponsor_id"].nunique()),
                        registrations=len(scoped_t["registrations.csv"]))
