"""Sponsor performance: sales analytics across every sponsor in scope."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date

import pandas as pd
import streamlit as st

from app.components.charts import lead_sources, plot, revenue_by_tier_chart, sponsor_leads_vs_target
from app.components.data_source import applied_filters, get_data, get_filtered_data, show_empty_state
from app.components.kpi_cards import Tile, count, kpi_row, money, money_compact, pct, vs_target
from app.components.layout import page_header
from app.components.ai_usage import ai_unavailable_notice, get_usage_recorder
from core.ai.llm import LLMNotConfiguredError
from core.ai.sponsor_report import (
    ReportError,
    build_sponsor_facts,
    generate_sponsor_report,
    report_file,
    template_report,
)
from core.analysis import (
    LEAD_SOURCES,
    LEAD_TARGET_ALERT,
    attainment_band,
    filter_sponsorships,
    lead_source_mix,
    renewal_rate,
    revenue_by_tier,
    sponsor_renewals,
    sponsor_scorecards,
    sponsorship_results,
)
from core.config import get_settings
from core.filters import describe, prune
from core.schema import TIERS

_ALERT_BG = "background-color: #FEE2E2"  # light red row for sponsors below 80% of target
_BAND_TEXT = {"Below 80%": "color: #B91C1C; font-weight: 600", "80-99%": "color: #B45309",
              "Met target": "color: #047857"}


def _multiselect(container, label: str, options: list[str], key: str, fmt=None) -> list[str]:
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All", format_func=fmt or str)


def _filters(t):
    tier_col, industry_col, package_col, source_col = st.columns(4)
    present = set(t["sponsors.csv"]["tier"])
    return dict(
        tiers=_multiselect(tier_col, "Sponsor tier", [x for x in TIERS if x in present], "sp_tiers", str.title),
        industries=_multiselect(industry_col, "Industry", sorted(t["sponsors.csv"]["industry"].unique()),
                                "sp_industries"),
        packages=_multiselect(package_col, "Package", sorted(t["sponsorships.csv"]["package"].unique()),
                              "sp_packages"),
        sources=_multiselect(source_col, "Lead source", list(LEAD_SOURCES), "sp_sources", str.title),
    )


def _style_rows(table: pd.DataFrame, band_col: str = "Status"):
    def row(r):
        return [_ALERT_BG if r[band_col] == "Below 80%" else ""] * len(r)
    return (table.style.apply(row, axis=1)
            .map(lambda v: _BAND_TEXT.get(v, ""), subset=[band_col]))


def _kpis(f, results, cards, renewals) -> None:
    spend, leads, target = results["spend"].sum(), int(results["leads"].sum()), int(results["leads_target"].sum())
    full_contracts = f["sponsors.csv"]["contract_value"].sum()
    booth_visits = int(f["engagement.csv"]["booth_visits"].sum())
    below = results[results["attainment"] < LEAD_TARGET_ALERT]
    counts = renewals["renewal_status"].value_counts()
    rate = renewal_rate(renewals)
    kpi_row([
        Tile("Contract value", money_compact(spend),
             help=f"{money(spend)} of contract value allocated to the events in scope: each contract is split "
                  f"evenly across the sponsor's events. Full contracts of these sponsors: {money(full_contracts)}."),
        Tile("Leads vs target", count(leads), delta=vs_target(leads / target if target else None),
             help=f"{leads:,} leads delivered against a combined target of {target:,}"),
        Tile("Cost per lead", money(spend / leads if leads else None),
             help="Allocated contract value divided by leads delivered"),
        Tile("Booth visits", count(booth_visits), delta=f"{count(results['leads_booth'].sum())} booth leads",
             delta_color="off",
             help="All booth visits recorded at these sponsors' events. The data does not say whose booth "
                  "was visited, so this is event-wide traffic, not visits to one sponsor."),
        Tile("Renewal rate", pct(rate),
             delta=f"{counts.get('Renewed', 0)} of {counts.get('Renewed', 0) + counts.get('Not renewed', 0)} "
                   "renewed", delta_color="off",
             help="Derived from sponsorship history: renewed means they sponsored in more than one year. "
                  f"{counts.get('New', 0)} new sponsor(s) (first event in the last 12 months of data) are too "
                  "early to call and are left out."),
        Tile("Below 80% of target", f"{len(below)}", delta=f"of {len(results)} sponsorships", delta_color="off",
             help="Sponsor-event pairs where leads came in under 80% of that event's target"),
    ], per_row=3)


def _alerts(cards, results) -> None:
    low_sponsors = cards[cards["band"] == "Below 80%"]
    low_events = results[results["band"] == "Below 80%"]
    if not low_sponsors.empty:
        st.error(f"**{len(low_sponsors)} sponsor(s) are below 80% of their lead target overall:** "
                 + ", ".join(f"{r.sponsor} ({r.attainment:.0%})" for r in low_sponsors.itertuples()))
    elif not low_events.empty:
        st.warning(f"No sponsor is below 80% overall, but **{len(low_events)} sponsorship(s) at "
                   f"{low_events['sponsor'].nunique()} sponsor(s)** came in under 80% at a single event. "
                   "See the table at the bottom of the page.")
    else:
        st.success("Every sponsor and sponsorship in scope reached at least 80% of its lead target.")


def _scorecard(cards, renewals) -> None:
    table = cards.merge(renewals[["sponsor_id", "renewal_status"]], on="sponsor_id", how="left")
    table = table.assign(Status=table["band"], tier=table["tier"].str.title())
    table = table[["sponsor", "tier", "industry", "events", "leads_target", "leads", "attainment", "Status",
                   "events_below_alert", "leads_booth", "leads_demo", "leads_virtual", "spend", "cost_per_lead",
                   "renewal_status"]].sort_values("attainment", na_position="last").reset_index(drop=True)
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


def _low_sponsorships(results) -> None:
    low = results[results["band"] == "Below 80%"]
    if low.empty:
        return
    st.subheader(f"Sponsorships below 80% of target ({len(low)})")
    table = low[["sponsor", "tier", "event", "start_date", "package", "leads_target", "leads", "attainment",
                 "band"]].rename(columns={"band": "Status"}).sort_values("attainment")
    table["tier"] = table["tier"].str.title()
    st.dataframe(_style_rows(table), hide_index=True, width="stretch", column_config={
        "sponsor": "Sponsor", "tier": "Tier", "event": "Event",
        "start_date": st.column_config.DateColumn("Date", format="D MMM YYYY"), "package": "Package",
        "leads_target": "Target", "leads": "Leads",
        "attainment": st.column_config.NumberColumn("Of target", format="percent"),
    })


def render() -> None:
    page_header("Sponsor performance", "How sponsors fared across the portfolio: leads against target, cost per "
                "lead, renewals, and a downloadable report per sponsor.", icon=":material/handshake:")
    t = get_filtered_data()
    if t is None:
        return
    if t["sponsorships.csv"].empty:
        show_empty_state("sponsors")
        return

    chosen = _filters(t)
    f = filter_sponsorships(t, **chosen)
    if f["sponsorships.csv"].empty:
        st.info("No sponsorships match these sponsor filters.")
        return
    if chosen["sources"]:
        st.caption(":orange[Lead-source filter on: only " + ", ".join(chosen["sources"]) + " leads are counted, "
                   "but targets cover every source, so attainment will look lower than it is.]")

    results = sponsorship_results(f).assign(band=lambda d: attainment_band(d["attainment"]))
    cards = sponsor_scorecards(f).assign(band=lambda d: attainment_band(d["attainment"]))
    full = get_data()
    history = full if full is not None else t
    # Renewal status needs the whole history: a sponsor's earlier years may sit outside the filters.
    renewals = sponsor_renewals(history["sponsorships.csv"], history["events.csv"])
    renewals = renewals[renewals["sponsor_id"].isin(cards["sponsor_id"])]

    st.caption(f"{len(cards)} sponsors across {len(results)} sponsorships at {results['event_id'].nunique()} events")
    _kpis(f, results, cards, renewals)
    _alerts(cards, results)

    plot(st, sponsor_leads_vs_target(cards))
    left, right = st.columns(2)
    plot(left, lead_sources(lead_source_mix(f["leads.csv"]), "Lead source mix"))
    plot(right, revenue_by_tier_chart(revenue_by_tier(results)))

    st.subheader("Sponsor scorecard")
    st.caption("Sorted by lowest attainment first. Rows in red are below 80% of their lead target overall. "
               "Click a column header to re-sort.")
    _scorecard(cards, renewals)
    _low_sponsorships(results)
    _report_section(f, cards, chosen, history)


_REPORT_KEY = "sponsor_report"  # (fingerprint, SponsorReport, facts)


def _scope(chosen: dict, history) -> str:
    parts = [f"{label}: {value}" for label, value in describe(applied_filters(), history, date.today())
             if value not in ("All", "All time")]
    labels = {"tiers": "Tier", "industries": "Industry", "packages": "Package", "sources": "Lead source"}
    parts += [f"{labels[k]}: {', '.join(v)}" for k, v in chosen.items() if v]
    return "; ".join(parts) or "all events and sponsors"


def _report_section(f, cards, chosen: dict, history) -> None:
    st.subheader("Sponsor summary report")
    settings = get_settings()
    names = dict(zip(cards.sort_values("sponsor")["sponsor_id"], cards.sort_values("sponsor")["sponsor"]))
    sponsor_id = st.selectbox("Sponsor", list(names), format_func=names.get, key="sp_report_sponsor")
    st.caption("Written with the **Sponsor Report Writer** skill (`skills/sponsor-report-writer`) from figures "
               "computed for this sponsor under the current sidebar and page filters.")
    if not settings.llm_configured:
        ai_unavailable_notice("An AI-written sponsor report",
                              "the button still builds a template draft in the same structure from the same "
                              "figures, with the same checklist, and you can download it.")
    scope = _scope(chosen, history)
    fingerprint = hashlib.sha256(json.dumps([sponsor_id, scope], default=str).encode()).hexdigest()

    if st.button("Generate sponsor summary", type="primary", icon=":material/description:", key="sp_generate"):
        facts = build_sponsor_facts(f, sponsor_id, history=history, scope=scope)
        report, problem = None, None
        if settings.llm_configured:
            try:
                with st.spinner(f"Writing the report with {settings.llm_model}..."):
                    report = generate_sponsor_report(facts, settings=settings, recorder=get_usage_recorder())
            except (ReportError, LLMNotConfiguredError) as exc:
                problem = str(exc)
            except Exception as exc:  # keep the page usable whatever happens
                problem = f"{type(exc).__name__}: {exc}"
        if report is None:
            report = template_report(facts)
            if problem:
                report.warnings.append(f"The AI report failed ({problem}), so this is the template draft instead.")
        st.session_state[_REPORT_KEY] = (fingerprint, report, facts)

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
