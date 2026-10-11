"""Portfolio overview: headline KPIs vs the previous period, charts and an event scorecard."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import streamlit as st

from frontend.components.charts import event_type_mix, plot, registration_trend, registrations_vs_attendance
from frontend.components.data_source import page_data, show_empty_state
from frontend.components.kpi_cards import Tile, change_delta, delta_color, count, kpi_row, money, money_compact, pct, score
from frontend.components.layout import page_header
from frontend.components.tables import to_frame
from shared.kpis import MetricChange

SORT_OPTIONS = {  # label -> event_kpis column
    "Start date": "start_date", "Registrations": "registrations", "Attendance": "attended",
    "Show-up rate": "show_up_rate", "NPS": "nps", "Session rating": "avg_rating", "Leads": "leads",
    "Leads vs target": "lead_attainment", "Sponsor revenue": "sponsor_revenue",
    "Cost per lead": "cost_per_lead", "Budget variance": "budget_variance",
}
TOP_N_OPTIONS = {"Top 5": 5, "Top 10": 10, "All events": None}
MIX_MEASURES = {"Events": "events", "Registrations": "registrations", "Attendees": "attended"}


def _fmt(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _kpi_cards(data: dict) -> None:
    p = SimpleNamespace(**dict(data["kpis"], first_date=_day(data["kpis"]["first_date"]),
                               last_date=_day(data["kpis"]["last_date"])))
    changes = {}
    if period := data["period"]:  # a closed date range has an equal-length period before it
        start, end, prev_start, prev_end = (_day(period[k]) for k in ("start", "end", "prev_start", "prev_end"))
        changes = {c["metric"]: MetricChange(**c) for c in data["changes"]}
        st.caption(f"**{_fmt(start)} – {_fmt(end)}**, compared vs "
                   f"{_fmt(prev_start)} – {_fmt(prev_end)}")
    else:
        st.caption(f"**{p.first_date:%b %Y} – {p.last_date:%b %Y}** (all events). Pick a date range in the "
                   "sidebar to compare each KPI with the previous equal period.")

    def tile(label, value, metric, kind="count", help_fmt=str, help_note=""):
        c = changes.get(metric)
        if c is None:
            return Tile(label, value, help=help_note or None)
        prev = "no data" if c.previous is None else help_fmt(c.previous)
        return Tile(label, value, delta=change_delta(c, kind), delta_color=delta_color(c),
                    help=f"Previous period: {prev}" + (f". {help_note}" if help_note else ""))

    kpi_row([
        tile("Events", f"{p.events:,}", "events", help_fmt=lambda v: f"{v:,.0f}"),
        tile("Registrations", count(p.registrations), "registrations", help_fmt=lambda v: f"{v:,.0f}",
             help_note=f"Exactly {p.registrations:,}"),
        tile("Show-up rate", pct(p.show_up_rate), "show_up_rate", "rate", help_fmt=pct),
        tile("Avg NPS", score(p.avg_event_nps, "+.0f"), "avg_event_nps", "score", help_fmt=lambda v: f"{v:+.0f}",
             help_note="NPS is approximated from 1-5 session ratings (5 = promoter, 1-3 = detractor) "
                       "and averaged across events"),
        tile("Sponsor revenue", money_compact(p.sponsor_revenue), "sponsor_revenue", help_fmt=money,
             help_note=f"Exactly {money(p.sponsor_revenue)}: contracts split evenly across each sponsor's events"),
        tile("Leads", count(p.leads), "leads", help_fmt=lambda v: f"{v:,.0f}", help_note=f"Exactly {p.leads:,}"),
    ], per_row=3)


def _requested_view() -> tuple[str, bool, int | None]:
    """The view options as the widgets will return them, read before the page data is fetched."""
    ss = st.session_state
    sort_label = ss.get("ov_sort") if ss.get("ov_sort") in SORT_OPTIONS else list(SORT_OPTIONS)[1]
    order = ss.get("ov_order") or "Highest first"
    top = ss.get("ov_top") if ss.get("ov_top") in TOP_N_OPTIONS else list(TOP_N_OPTIONS)[1]
    return SORT_OPTIONS[sort_label], order == "Lowest first", TOP_N_OPTIONS[top]


def _view_options() -> tuple[str, bool, int | None]:
    sort_col, order_col, top_col = st.columns([2, 2, 1])
    sort_label = sort_col.selectbox("Sort events by", list(SORT_OPTIONS), index=1, key="ov_sort")
    order = order_col.segmented_control("Order", ["Highest first", "Lowest first"], default="Highest first",
                                        key="ov_order") or "Highest first"
    top = top_col.selectbox("Show", list(TOP_N_OPTIONS), index=1, key="ov_top")
    return SORT_OPTIONS[sort_label], order == "Lowest first", TOP_N_OPTIONS[top]


def _scorecard(ranked) -> None:
    pct_col = lambda label: st.column_config.NumberColumn(label, format="percent")  # noqa: E731
    money_col = lambda label: st.column_config.NumberColumn(label, format="dollar")  # noqa: E731
    table = ranked[["name", "event_type", "format", "city", "start_date", "registrations", "attended",
                    "show_up_rate", "nps", "avg_rating", "sponsors", "leads", "lead_attainment",
                    "sponsor_revenue", "cost_per_lead", "budget_variance"]].copy()
    table["event_type"] = table["event_type"].str.title()
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "name": st.column_config.TextColumn("Event", width="large"), "event_type": "Type",
        "format": "Format", "city": "City",
        "start_date": st.column_config.DateColumn("Start", format="D MMM YYYY"),
        "registrations": "Registered", "attended": "Attended",
        "show_up_rate": pct_col("Show-up"),
        "nps": st.column_config.NumberColumn("NPS", format="%+.0f"),
        "avg_rating": st.column_config.NumberColumn("Rating", format="%.2f"),
        "sponsors": "Sponsors", "leads": "Leads",
        "lead_attainment": st.column_config.ProgressColumn("Leads vs target", format="percent",
                                                           min_value=0, max_value=1.5),
        "sponsor_revenue": money_col("Sponsor revenue"), "cost_per_lead": money_col("Cost per lead"),
        "budget_variance": pct_col("Over/under budget"),
    })
    st.download_button("Download scorecard as CSV", table.to_csv(index=False).encode("utf-8"),
                       file_name="event_scorecard.csv", mime="text/csv")


def render() -> None:
    page_header("Portfolio overview", "How every event performed: attendance, satisfaction, sponsor revenue and "
                "leads, compared with the previous period.", icon=":material/dashboard:")
    sort_by, ascending, top_n = _requested_view()
    data = page_data("overview", sort_by=sort_by, ascending=ascending, top_n=top_n)
    if data is None:
        return
    if data["empty"]:
        show_empty_state()
        return

    _kpi_cards(data)

    st.subheader("Events")
    sort_by, ascending, top_n = _view_options()
    ranked, total = to_frame(data["ranked"]), data["events_total"]
    shown = f"{len(ranked)} of {total}" if len(ranked) < total else f"All {total}"
    st.caption(f"{shown} events, sorted by {[k for k, v in SORT_OPTIONS.items() if v == sort_by][0].lower()}. "
               "These options apply to the bar chart and the scorecard. Click a column header to re-sort the table.")

    left, right = st.columns([3, 2])
    plot(left, registrations_vs_attendance(ranked))
    with right:
        measure = st.segmented_control("Mix by", list(MIX_MEASURES), default="Events", key="ov_mix") or "Events"
        plot(st, event_type_mix(to_frame(data["mix"]), MIX_MEASURES[measure], measure))
    plot(st, registration_trend(to_frame(data["trend"])))

    st.subheader("Event scorecard")
    _scorecard(ranked)
