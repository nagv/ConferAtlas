"""Sponsor performance: sales analytics across every sponsor in scope."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from frontend import client
from frontend.components.ai_usage import ai_unavailable_notice, record_usage
from frontend.components.charts import lead_sources, plot, revenue_by_tier_chart, sponsor_leads_vs_target
from frontend.components.data_source import call, page_data, scope_request, show_empty_state
from frontend.components.kpi_cards import Tile, count, kpi_row, money, money_compact, pct, vs_target
from frontend.components.layout import page_header
from frontend.components.tables import to_frame
from shared.filters import prune
from shared.models import SponsorReportModel
from shared.report import report_file

_ALERT_BG = "background-color: #FEE2E2"  # light red row for sponsors below 80% of target
_BAND_TEXT = {"Below 80%": "color: #B91C1C; font-weight: 600", "80-99%": "color: #B45309",
              "Met target": "color: #047857"}
_FILTERS = {"tiers": "sp_tiers", "industries": "sp_industries", "packages": "sp_packages", "sources": "sp_sources"}


def _multiselect(container, label: str, options: list[str], key: str, fmt=None) -> list[str]:
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All", format_func=fmt or str)


def _filters(data) -> dict[str, list[str]]:
    tier_col, industry_col, package_col, source_col = st.columns(4)
    return dict(
        tiers=_multiselect(tier_col, "Sponsor tier", data["tier_options"], "sp_tiers", str.title),
        industries=_multiselect(industry_col, "Industry", data["industry_options"], "sp_industries"),
        packages=_multiselect(package_col, "Package", data["package_options"], "sp_packages"),
        sources=_multiselect(source_col, "Lead source", data["source_options"], "sp_sources", str.title),
    )


def _style_rows(table: pd.DataFrame, band_col: str = "Status"):
    def row(r):
        return [_ALERT_BG if r[band_col] == "Below 80%" else ""] * len(r)
    return (table.style.apply(row, axis=1)
            .map(lambda v: _BAND_TEXT.get(v, ""), subset=[band_col]))


def _kpis(k) -> None:
    spend, leads, target = k.spend, k.leads, k.target
    kpi_row([
        Tile("Contract value", money_compact(spend),
             help=f"{money(spend)} of contract value allocated to the events in scope: each contract is split "
                  f"evenly across the sponsor's events. Full contracts of these sponsors: {money(k.full_contracts)}."),
        Tile("Leads vs target", count(leads), delta=vs_target(leads / target if target else None),
             help=f"{leads:,} leads delivered against a combined target of {target:,}"),
        Tile("Cost per lead", money(spend / leads if leads else None),
             help="Allocated contract value divided by leads delivered"),
        Tile("Booth visits", count(k.booth_visits), delta=f"{count(k.booth_leads)} booth leads",
             delta_color="off",
             help="All booth visits recorded at these sponsors' events. The data does not say whose booth "
                  "was visited, so this is event-wide traffic, not visits to one sponsor."),
        Tile("Renewal rate", pct(k.renewal_rate),
             delta=f"{k.renewed} of {k.renewed + k.not_renewed} renewed", delta_color="off",
             help="Derived from sponsorship history: renewed means they sponsored in more than one year. "
                  f"{k.new} new sponsor(s) (first event in the last 12 months of data) are too "
                  "early to call and are left out."),
        Tile("Below 80% of target", f"{k.below_count}", delta=f"of {k.results_count} sponsorships", delta_color="off",
             help="Sponsor-event pairs where leads came in under 80% of that event's target"),
    ], per_row=3)


def _alerts(data) -> None:
    if data["low_sponsors"]:
        st.error(f"**{len(data['low_sponsors'])} sponsor(s) are below 80% of their lead target overall:** "
                 + ", ".join(f"{sponsor} ({attainment:.0%})" for sponsor, attainment in data["low_sponsors"]))
    elif data["low_events_count"]:
        st.warning(f"No sponsor is below 80% overall, but **{data['low_events_count']} sponsorship(s) at "
                   f"{data['low_events_sponsors']} sponsor(s)** came in under 80% at a single event. "
                   "See the table at the bottom of the page.")
    else:
        st.success("Every sponsor and sponsorship in scope reached at least 80% of its lead target.")


def _scorecard(table: pd.DataFrame) -> None:
    st.dataframe(_style_rows(table), hide_index=True, width="stretch", column_config={
        "sponsor": st.column_config.TextColumn("Sponsor", width="medium"), "tier": "Tier", "industry": "Industry",
        "events": "Events", "leads_target": "Target", "leads": "Leads",
        "attainment": st.column_config.ProgressColumn("Of target", format="percent", min_value=0, max_value=1.5),
        "events_below_alert": st.column_config.NumberColumn("Events < 80%", help="Events where this sponsor got "
                                                            "under 80% of that event's target"),
        "leads_booth": "Booth", "leads_demo": "Demo", "leads_virtual": "Virtual",
        "spend": st.column_config.NumberColumn("Contract value", format="dollar",
                                               help="Allocated to the events in scope"),
        "cost_per_lead": st.column_config.NumberColumn("Cost per lead", format="dollar"),
        "renewal_status": "Renewal",
    })
    st.download_button("Download scorecard as CSV", table.to_csv(index=False).encode("utf-8"),
                       file_name="sponsor_scorecard.csv", mime="text/csv")


def _low_sponsorships(table: pd.DataFrame | None) -> None:
    if table is None:
        return
    st.subheader(f"Sponsorships below 80% of target ({len(table)})")
    st.dataframe(_style_rows(table), hide_index=True, width="stretch", column_config={
        "sponsor": "Sponsor", "tier": "Tier", "event": "Event",
        "start_date": st.column_config.DateColumn("Date", format="D MMM YYYY"), "package": "Package",
        "leads_target": "Target", "leads": "Leads",
        "attainment": st.column_config.NumberColumn("Of target", format="percent"),
    })


def render() -> None:
    page_header("Sponsor performance", "How sponsors fared across the portfolio: leads against target, cost per "
                "lead, renewals, and a downloadable report per sponsor.", icon=":material/handshake:")
    requested = {name: st.session_state.get(key, []) for name, key in _FILTERS.items()}
    data = page_data("sponsor-performance", sponsor_filters=requested)
    if data is None:
        return
    if data["empty"]:
        show_empty_state("sponsors")
        return

    chosen = _filters(data)
    if data["no_match"]:
        st.info("No sponsorships match these sponsor filters.")
        return
    if chosen["sources"]:
        st.caption(":orange[Lead-source filter on: only " + ", ".join(chosen["sources"]) + " leads are counted, "
                   "but targets cover every source, so attainment will look lower than it is.]")

    st.caption(f"{data['sponsors_count']} sponsors across {data['sponsorships_count']} sponsorships at "
               f"{data['events_count']} events")
    _kpis(SimpleNamespace(**data["kpis"]))
    _alerts(data)

    plot(st, sponsor_leads_vs_target(to_frame(data["cards"])))
    left, right = st.columns(2)
    plot(left, lead_sources(to_frame(data["lead_mix"]), "Lead source mix"))
    plot(right, revenue_by_tier_chart(to_frame(data["revenue_by_tier"])))

    st.subheader("Sponsor scorecard")
    st.caption("Sorted by lowest attainment first. Rows in red are below 80% of their lead target overall. "
               "Click a column header to re-sort.")
    _scorecard(to_frame(data["scorecard"]))
    _low_sponsorships(to_frame(data["low_sponsorships"]))
    _report_section(data, chosen)


_REPORT_KEY = "sponsor_report"  # (fingerprint, SponsorReportModel, facts)


def _generate(sponsor_id: str, chosen: dict):
    payload = {**scope_request(), "sponsor_filters": chosen, "sponsor_id": sponsor_id}
    response = call(client.ai, "sponsor-report", payload)
    if response is None:
        return None
    record_usage(response["usage"])
    return SponsorReportModel(**response["report"]), response["facts"]


def _report_section(data, chosen: dict) -> None:
    st.subheader("Sponsor summary report")
    status = client.status()
    names = dict(map(tuple, data["report_options"]))
    sponsor_id = st.selectbox("Sponsor", list(names), format_func=names.get, key="sp_report_sponsor")
    st.caption("Written with the **Sponsor Report Writer** skill (`skills/sponsor-report-writer`) from figures "
               "computed for this sponsor under the current sidebar and page filters.")
    if not status["llm_configured"]:
        ai_unavailable_notice("An AI-written sponsor report",
                              "the button still builds a template draft in the same structure from the same "
                              "figures, with the same checklist, and you can download it.")
    fingerprint = hashlib.sha256(json.dumps([sponsor_id, data["scope"]], default=str).encode()).hexdigest()

    if st.button("Generate sponsor summary", type="primary", icon=":material/description:", key="sp_generate"):
        if status["llm_configured"]:
            with st.spinner(f"Writing the report with {status['model']}..."):
                result = _generate(sponsor_id, chosen)
        else:
            result = _generate(sponsor_id, chosen)
        if result is not None:
            st.session_state[_REPORT_KEY] = (fingerprint, *result)

    stored = st.session_state.get(_REPORT_KEY)
    if not stored:
        return
    stored_fingerprint, report, facts = stored
    if stored_fingerprint != fingerprint:
        st.info("This report is for a different sponsor or filter selection. Press **Generate sponsor summary** "
                "to write one for the current selection.", icon=":material/history:")
    for warning in report.warnings:
        st.warning(warning)
    with st.container(border=True):
        st.markdown(f"**Checklist** · {report.generated_by}")
        for check in report.checks:
            icon = ":green[:material/check_circle:]" if check.passed else ":red[:material/cancel:]"
            st.markdown(f"{icon} {check.item}" + (f"  \n:red[{check.detail}]" if check.detail else ""))
    with st.container(border=True):
        # Shown two heading levels down so it sits inside the page; the download keeps # and ##.
        st.markdown(re.sub(r"(?m)^(#{1,4}) ", lambda m: "#" * min(len(m.group(1)) + 2, 6) + " ", report.markdown))
    name = facts["sponsor"]["name"].lower().replace(" ", "_")
    st.download_button("Download report (.md)", report_file(report, facts, date.today().isoformat()).encode("utf-8"),
                       file_name=f"{name}_sponsor_report.md", mime="text/markdown", icon=":material/download:",
                       key="sp_report_download")
    with st.expander("Fact sheet used (every number in the report must come from here)"):
        st.json(facts, expanded=False)
