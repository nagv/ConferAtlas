"""Sponsor report: one sponsor's results across every event it sponsored."""
from __future__ import annotations

import streamlit as st

from frontend.components.charts import plot, lead_sources, leads_vs_target
from frontend.components.data_source import page_data, show_empty_state, sponsor_candidates, sponsor_picker
from frontend.components.kpi_cards import Tile, kpi_row, money, vs_target
from frontend.components.layout import page_header
from frontend.components.tables import to_frame


def render() -> None:
    page_header("Sponsor report", "One sponsor's results at every event they sponsored.", icon=":material/badge:")
    data = page_data("sponsor-report", sponsor_candidates=sponsor_candidates("sponsor_report_sponsor"))
    if data is None:
        return
    if data["empty"]:
        show_empty_state("sponsors")
        return

    sponsor_id = sponsor_picker(data["sponsor_options"], key="sponsor_report_sponsor")
    card = to_frame(data["card"]).iloc[0]
    history = to_frame(data["history"])
    st.caption(f"{card['tier'].title()} tier · {card['industry']} · Goals: {card['goals']}")

    kpi_row([
        Tile("Events sponsored", str(card["events"])),
        Tile("Leads delivered", f"{card['leads']:,}", delta=vs_target(card["attainment"])),
        Tile("Targets met", f"{card['targets_met']} of {card['events']}"),
        Tile("Est. cost per lead", money(card["cost_per_lead"]),
             help="Contract value split evenly across the sponsor's events, divided by leads"),
        Tile("Contract value", money(card["contract_value"])),
    ], per_row=3)

    left, right = st.columns([3, 2])
    plot(left, leads_vs_target(history, "event", "Leads by event: target vs delivered"))
    plot(right, lead_sources(to_frame(data["lead_mix"])))

    st.subheader("Event-by-event results")
    table = history[["event", "start_date", "format", "package", "leads_target", "leads", "attainment",
                     "leads_booth", "leads_demo", "leads_virtual", "spend", "cost_per_lead"]]
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "event": "Event",
        "start_date": st.column_config.DateColumn("Date", format="D MMM YYYY"),
        "format": "Format", "package": "Package", "leads_target": "Target", "leads": "Leads",
        "attainment": st.column_config.ProgressColumn("Of target", format="percent",
                                                      min_value=0, max_value=1.5),
        "leads_booth": "Booth", "leads_demo": "Demo", "leads_virtual": "Virtual",
        "spend": st.column_config.NumberColumn("Est. spend", format="dollar"),
        "cost_per_lead": st.column_config.NumberColumn("Cost per lead", format="dollar"),
    })
    st.download_button("Download as CSV", table.to_csv(index=False).encode("utf-8"),
                       file_name=f"{sponsor_id}_results.csv", mime="text/csv")

    st.subheader("What leads said")
    st.dataframe(to_frame(data["lead_notes"]), hide_index=True, width="stretch",
                 column_config={"note": "Note from the sponsor's team", "leads": "Leads"})
