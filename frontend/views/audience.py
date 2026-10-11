"""Audience insights: who registers and attends, where from, and who comes back."""
from __future__ import annotations

import streamlit as st

from frontend.components.charts import (
    breakdown_bars,
    country_map,
    industry_treemap,
    new_vs_returning_donut,
    plot,
    returning_by_event_bars,
)
from frontend.components.data_source import page_data, show_empty_state
from frontend.components.kpi_cards import Tile, count, kpi_row, pct
from frontend.components.layout import page_header
from frontend.components.tables import to_frame
from shared.constants import SENIORITY_ORDER
from shared.filters import prune

_FILTERS = {"roles": "aud_roles", "seniorities": "aud_seniority", "industries": "aud_industries",
            "statuses": "aud_status"}


def _multiselect(container, label: str, options: list[str], key: str) -> list[str]:
    # Sidebar filters can remove roles or industries: drop choices that no longer exist.
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All")


def _filters(data) -> None:
    role_col, seniority_col, industry_col, status_col = st.columns(4)
    _multiselect(role_col, "Role", data["role_options"], "aud_roles")
    _multiselect(seniority_col, "Seniority", data["seniority_options"], "aud_seniority")
    _multiselect(industry_col, "Industry", data["industry_options"], "aud_industries")
    _multiselect(status_col, "Attendance status", data["status_options"], "aud_status")


def render() -> None:
    page_header("Audience insights", "Who registered and attended: roles, seniority, industries, countries and "
                "returning attendees.", icon=":material/groups:")
    data = page_data("audience", **{name: st.session_state.get(key, []) for name, key in _FILTERS.items()})
    if data is None:
        return
    if data["empty"]:
        show_empty_state("registrations")
        return

    _filters(data)
    total, matching = data["registrations_total"], data["audience_count"]
    st.caption(f"{matching:,} of {total:,} registrations match. These filters apply to everything "
               "on this page, on top of the sidebar filters.")
    if matching == 0:
        st.info("No registrations match these audience filters.")
        return

    attendees, confirmed = data["attendees"], data["confirmed"]
    returning = to_frame(data["returning"]) if data["has_history"] else None
    kpi_row([
        Tile("Registrations", count(matching), help=f"Exactly {matching:,}"),
        Tile("Attendees", count(attendees), help=f"Exactly {attendees:,}"),
        Tile("Show-up rate", pct(attendees / confirmed if confirmed else None),
             help="Attendees as a share of confirmed (not cancelled) registrations"),
        Tile("Returning attendees", pct(returning["share"].iloc[1]) if returning is not None else "n/a",
             help="Attendees who attended an earlier event" if returning is not None
             else "Needs a person_id column in registrations.csv"),
        Tile("Countries", str(data["countries"])),
    ], per_row=3)

    st.subheader("Who they are")
    left, right = st.columns(2)
    plot(left, breakdown_bars(to_frame(data["roles"]), "attendee_role", "Roles"))
    plot(right, breakdown_bars(to_frame(data["seniority"]), "seniority", "Seniority",
                               order=list(SENIORITY_ORDER)))
    plot(st, industry_treemap(to_frame(data["industry"])))

    st.subheader("Where they come from")
    countries = to_frame(data["country"])
    fig, unmapped = country_map(countries)
    plot(st, fig)
    if unmapped:
        st.caption(f"Not shown on the map (no country code known): {', '.join(unmapped)}. They are in the table.")
    with st.expander("Country table"):
        st.dataframe(countries, hide_index=True, width="stretch", column_config={
            "country": "Country", "registrations": "Registrations",
            "share": st.column_config.NumberColumn("Share", format="percent"),
            "confirmed": "Confirmed", "attended": "Attended",
            "show_up_rate": st.column_config.NumberColumn("Show-up rate", format="percent"),
        })

    st.subheader("New vs returning")
    if returning is None:
        st.info("Repeat attendance needs a `person_id` column in registrations.csv, the same ID for the same "
                "person at every event. The built-in data has one, but the uploaded data does not.")
        return
    if returning["attendees"].sum() == 0:
        st.info("None of the matching registrations attended, so there are no attendees to split.")
        return
    left, right = st.columns([2, 3])
    plot(left, new_vs_returning_donut(returning))
    plot(right, returning_by_event_bars(to_frame(data["returning_by_event"])))
    st.caption("Returning means the person attended an earlier event (by start date), counting events outside "
               "the current filters. An event's first edition has no returning attendees by definition.")
