"""Global sidebar filters shared by every page.

The widgets hold a *draft* selection. Each list only offers values that still
exist given the earlier choices (dates -> type -> format -> city -> event ->
sponsor). Nothing changes on the pages until **Apply filters** copies the
draft into ``st.session_state["filters"]``, which ``get_filtered_data`` reads.
They sit outside a form on purpose: a form would not rerun as you pick, so
the lists could not narrow.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from app.components.data_source import (
    APPLIED_FILTERS_KEY,
    applied_filters,
    data_source_key,
    get_data,
    get_filtered_data,
)
from core.analysis import Tables
from core.filters import (
    CUSTOM,
    DATE_PRESETS,
    FilterState,
    column_options,
    describe,
    event_options,
    narrow_events,
    prune,
    sponsor_options,
)

K_PRESET, K_DATES, K_TYPES, K_FORMATS = "f_preset", "f_dates", "f_types", "f_formats"
K_CITIES, K_EVENTS, K_SPONSOR = "f_cities", "f_events", "f_sponsor"
_WIDGET_KEYS = (K_PRESET, K_DATES, K_TYPES, K_FORMATS, K_CITIES, K_EVENTS, K_SPONSOR)
_SOURCE_KEY = "filters_source"
_ALL_SPONSORS = ""  # selectbox treats None as "nothing chosen", so "all" needs a real value


def _draft() -> FilterState:
    """The selection currently shown in the widgets (not yet applied)."""
    ss = st.session_state
    preset = ss.get(K_PRESET, DATE_PRESETS[0])
    dates = tuple(ss.get(K_DATES) or ()) if preset == CUSTOM else ()
    return FilterState(
        date_preset=preset,
        custom_start=dates[0] if len(dates) > 0 else None,
        custom_end=dates[1] if len(dates) > 1 else None,  # None while the range is half picked
        event_types=tuple(ss.get(K_TYPES, ())),
        formats=tuple(ss.get(K_FORMATS, ())),
        cities=tuple(ss.get(K_CITIES, ())),
        event_ids=tuple(ss.get(K_EVENTS, ())),
        sponsor_id=ss.get(K_SPONSOR) or None,
    )


def _apply() -> None:
    st.session_state[APPLIED_FILTERS_KEY] = _draft()


def _reset() -> None:
    for key in _WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state[APPLIED_FILTERS_KEY] = FilterState()


def _init_widgets(t: Tables) -> None:
    """Seed widget state from the applied filters (first run, or after Reset)."""
    applied = applied_filters()
    events = t["events.csv"]
    first = events["start_date"].min().date() if len(events) else date.today()
    last = events["start_date"].max().date() if len(events) else date.today()
    defaults = {
        K_PRESET: applied.date_preset,
        K_DATES: (applied.custom_start or first, applied.custom_end or last),
        K_TYPES: list(applied.event_types), K_FORMATS: list(applied.formats),
        K_CITIES: list(applied.cities), K_EVENTS: list(applied.event_ids), K_SPONSOR: applied.sponsor_id or _ALL_SPONSORS,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _multiselect(label: str, options: list[str], key: str, names: dict[str, str] | None = None,
                 title: bool = False) -> tuple[str, ...]:
    # Drop choices that earlier filters made impossible before the widget is drawn. Only
    # write when something changed: writing widget state every run would override the user.
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept

    def fmt(value: str) -> str:
        label_ = names.get(value, value) if names else value
        return label_.title() if title else label_

    return tuple(st.multiselect(label, options, key=key, placeholder="All", format_func=fmt))


def _summary_card(t: Tables) -> None:
    applied = applied_filters()
    scoped = get_filtered_data()
    with st.container(border=True):
        st.markdown("**Current selection**")
        st.markdown("  \n".join(f"{label}: **{value}**" for label, value in describe(applied, t, date.today())))
        if scoped is not None:
            n_sponsors = scoped["sponsorships.csv"]["sponsor_id"].nunique()
            st.caption(f"{len(scoped['events.csv'])} events · {n_sponsors} sponsors · "
                       f"{len(scoped['registrations.csv']):,} registrations")
        if _draft() != applied:
            st.caption(":orange[You have unapplied changes. Press **Apply filters**.]")


def render_filter_sidebar() -> None:
    t = get_data(show_errors=False)  # pages report load errors themselves
    if t is None:
        return
    if st.session_state.get(_SOURCE_KEY) != data_source_key():  # new dataset: start clean
        _reset()
        st.session_state[_SOURCE_KEY] = data_source_key()

    events = t["events.csv"]
    with st.sidebar:
        st.divider()
        st.subheader("Filters")
        _init_widgets(t)

        preset = st.selectbox("Date range", DATE_PRESETS, key=K_PRESET)
        if preset == CUSTOM:
            st.date_input("From / to", key=K_DATES, help="Events are included by their start date")
        start, end = _draft().date_range(date.today())

        level = narrow_events(events, start=start, end=end)
        types = _multiselect("Event type", column_options(level, "event_type"), K_TYPES, title=True)
        level = narrow_events(level, event_types=types)
        formats = _multiselect("Format", column_options(level, "format"), K_FORMATS, title=True)
        level = narrow_events(level, formats=formats)
        cities = _multiselect("City", column_options(level, "city"), K_CITIES)
        level = narrow_events(level, cities=cities)
        names = event_options(level)
        chosen = _multiselect("Event", list(names), K_EVENTS, names=names)
        level = narrow_events(level, event_ids=chosen)

        sponsors = sponsor_options(t, level["event_id"])
        if st.session_state.get(K_SPONSOR) not in (_ALL_SPONSORS, *sponsors):
            st.session_state[K_SPONSOR] = _ALL_SPONSORS  # earlier filters excluded this sponsor
        st.selectbox("Sponsor (optional)", [_ALL_SPONSORS, *sponsors], key=K_SPONSOR,
                     format_func=lambda s: sponsors.get(s, "All sponsors"))
        st.caption(f"{len(level)} of {len(events)} events match this selection")

        apply_col, reset_col = st.columns(2)
        apply_col.button("Apply filters", type="primary", key="apply_filters", on_click=_apply, width="stretch")
        reset_col.button("Reset filters", key="reset_filters", on_click=_reset, width="stretch")
        _summary_card(t)
