"""Audience insights: who registers and attends, where from, and who comes back."""
from __future__ import annotations

import streamlit as st

from app.components.charts import (
    breakdown_bars,
    country_map,
    industry_treemap,
    new_vs_returning_donut,
    plot,
    returning_by_event_bars,
)
from app.components.data_source import get_data, get_filtered_data, show_empty_state
from app.components.kpi_cards import Tile, count, kpi_row, pct
from app.components.layout import page_header
from core.analysis import (
    ATTENDANCE_STATUSES,
    SENIORITY_ORDER,
    attendee_history,
    audience_breakdown,
    filter_audience,
    has_person_ids,
    new_vs_returning,
    returning_by_event,
)
from core.filters import prune


def _multiselect(container, label: str, options: list[str], key: str) -> list[str]:
    # Sidebar filters can remove roles or industries: drop choices that no longer exist.
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All")


def _filters(regs):
    role_col, seniority_col, industry_col, status_col = st.columns(4)
    seniorities = [s for s in SENIORITY_ORDER if s in set(regs["seniority"])]
    seniorities += sorted(set(regs["seniority"]) - set(SENIORITY_ORDER))  # any unexpected values last
    return filter_audience(
        regs,
        roles=_multiselect(role_col, "Role", sorted(regs["attendee_role"].unique()), "aud_roles"),
        seniorities=_multiselect(seniority_col, "Seniority", seniorities, "aud_seniority"),
        industries=_multiselect(industry_col, "Industry", sorted(regs["industry"].unique()), "aud_industries"),
        statuses=_multiselect(status_col, "Attendance status", list(ATTENDANCE_STATUSES), "aud_status"),
    )


def render() -> None:
    page_header("Audience insights", "Who registered and attended: roles, seniority, industries, countries and "
                "returning attendees.", icon=":material/groups:")
    t = get_filtered_data()
    if t is None:
        return
    if t["registrations.csv"].empty:
        show_empty_state("registrations")
        return

    regs = t["registrations.csv"]
    audience = _filters(regs)
    st.caption(f"{len(audience):,} of {len(regs):,} registrations match. These filters apply to everything "
               "on this page, on top of the sidebar filters.")
    if audience.empty:
        st.info("No registrations match these audience filters.")
        return

    full = get_data()
    history = None
    if full is not None and has_person_ids(full["registrations.csv"]):
        # Use the full history so visits before the selected period still count as "returning".
        history = attendee_history(full["registrations.csv"], full["events.csv"])
        history = history[history["reg_id"].isin(audience["reg_id"])]

    attendees = audience[audience["attended"]]
    confirmed = int(audience["status"].eq("confirmed").sum())
    returning = new_vs_returning(history) if history is not None else None
    kpi_row([
        Tile("Registrations", count(len(audience)), help=f"Exactly {len(audience):,}"),
        Tile("Attendees", count(len(attendees)), help=f"Exactly {len(attendees):,}"),
        Tile("Show-up rate", pct(len(attendees) / confirmed if confirmed else None),
             help="Attendees as a share of confirmed (not cancelled) registrations"),
        Tile("Returning attendees", pct(returning["share"].iloc[1]) if returning is not None else "n/a",
             help="Attendees who attended an earlier event" if returning is not None
             else "Needs a person_id column in registrations.csv"),
        Tile("Countries", str(attendees["country"].nunique())),
    ], per_row=3)

    st.subheader("Who they are")
    left, right = st.columns(2)
    plot(left, breakdown_bars(audience_breakdown(audience, "attendee_role"), "attendee_role", "Roles"))
    plot(right, breakdown_bars(audience_breakdown(audience, "seniority"), "seniority", "Seniority",
                               order=list(SENIORITY_ORDER)))
    plot(st, industry_treemap(audience_breakdown(audience, "industry")))

    st.subheader("Where they come from")
    countries = audience_breakdown(audience, "country")
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
    if history is None:
        st.info("Repeat attendance needs a `person_id` column in registrations.csv, the same ID for the same "
                "person at every event. The built-in data has one, but the uploaded data does not.")
        return
    if returning["attendees"].sum() == 0:
        st.info("None of the matching registrations attended, so there are no attendees to split.")
        return
    left, right = st.columns([2, 3])
    plot(left, new_vs_returning_donut(returning))
    plot(right, returning_by_event_bars(returning_by_event(history, t["events.csv"])))
    st.caption("Returning means the person attended an earlier event (by start date), counting events outside "
               "the current filters. An event's first edition has no returning attendees by definition.")
