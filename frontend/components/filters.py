"""Global sidebar filters shared by every page.

The widgets hold a *draft* selection. Each list only offers values that still
exist given the earlier choices (dates -> type -> format -> city -> event ->
sponsor); the backend works those lists out from the draft. Nothing changes on
the pages until **Apply filters** copies the draft into
``st.session_state["filters"]``, which every page request sends.
They sit outside a form on purpose: a form would not rerun as you pick, so
the lists could not narrow.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from frontend import client
from frontend.components.data_source import (
    APPLIED_FILTERS_KEY,
    applied_filters,
    call,
    data_source_key,
    dataset_info,
    scope_summary,
)
from shared.filters import CUSTOM, DATE_PRESETS, FilterState, prune

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


def _init_widgets(info: dict) -> None:
    """Seed widget state from the applied filters (first run, or after Reset)."""
    applied = applied_filters()
    first = date.fromisoformat(info["first_event_date"]) if info["first_event_date"] else date.today()
    last = date.fromisoformat(info["last_event_date"]) if info["last_event_date"] else date.today()
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


def _summary_card() -> None:
    applied = applied_filters()
    summary = scope_summary()
    with st.container(border=True):
        st.markdown("**Current selection**")
        if summary is not None:
            st.markdown("  \n".join(f"{label}: **{value}**" for label, value in summary["description"]))
            st.caption(f"{summary['events']} events · {summary['sponsors']} sponsors · "
                       f"{summary['registrations']:,} registrations")
        if _draft() != applied:
            st.caption(":orange[You have unapplied changes. Press **Apply filters**.]")


def render_filter_sidebar() -> None:
    info = dataset_info(show_errors=False)  # pages report load errors themselves
    if info is None:
        return
    if st.session_state.get(_SOURCE_KEY) != data_source_key():  # new dataset: start clean
        _reset()
        st.session_state[_SOURCE_KEY] = data_source_key()

    with st.sidebar:
        st.divider()
        st.subheader("Filters")
        _init_widgets(info)

        preset = st.selectbox("Date range", DATE_PRESETS, key=K_PRESET)
        if preset == CUSTOM:
            st.date_input("From / to", key=K_DATES, help="Events are included by their start date")
        options = call(client.filter_options, _draft().to_json(), date.today(), show_errors=False)
        if options is None:
            return

        _multiselect("Event type", options["event_types"], K_TYPES, title=True)
        _multiselect("Format", options["formats"], K_FORMATS, title=True)
        _multiselect("City", options["cities"], K_CITIES)
        names = {event_id: name for event_id, name in options["events"]}
        _multiselect("Event", list(names), K_EVENTS, names=names)

        sponsors = {sponsor_id: name for sponsor_id, name in options["sponsors"]}
        if st.session_state.get(K_SPONSOR) not in (_ALL_SPONSORS, *sponsors):
            st.session_state[K_SPONSOR] = _ALL_SPONSORS  # earlier filters excluded this sponsor
        st.selectbox("Sponsor (optional)", [_ALL_SPONSORS, *sponsors], key=K_SPONSOR,
                     format_func=lambda s: sponsors.get(s, "All sponsors"))
        st.caption(f"{options['matching_events']} of {options['total_events']} events match this selection")

        apply_col, reset_col = st.columns(2)
        apply_col.button("Apply filters", type="primary", key="apply_filters", on_click=_apply, width="stretch")
        reset_col.button("Reset filters", key="reset_filters", on_click=_reset, width="stretch")
        _summary_card()
