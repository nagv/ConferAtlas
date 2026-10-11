"""Event drill-down: one event's KPIs, registrations, sessions, engagement, sponsors and feedback."""
from __future__ import annotations

from types import SimpleNamespace

import streamlit as st

from frontend.components.charts import (
    engagement_comparison,
    lead_sources,
    plot,
    registration_curve,
    session_fill_rates,
    sponsor_attainment,
)
from frontend.components.data_source import event_candidates, event_picker, page_data, show_empty_state
from frontend.components.kpi_cards import Tile, kpi_row, money, pct, score, vs_target
from frontend.components.layout import page_header
from frontend.components.tables import to_frame
from shared.filters import prune


def _multiselect(container, label: str, options: list[str], key: str) -> list[str]:
    # A new event has different tracks/speakers: drop choices that no longer exist.
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All")


def _kpis(ev, fill_mean) -> None:
    days = (ev["end_date"] - ev["start_date"]).days + 1
    st.caption(f"{ev['event_type'].title()} · {ev['format']} · {ev['city']} · "
               f"{ev['start_date']:%d %b %Y} · {days} day{'s' if days > 1 else ''}")
    kpi_row([
        Tile("Registered", f"{ev['registrations']:,}"),
        Tile("Attended", f"{ev['attended']:,}", delta=f"{pct(ev['no_show_rate'])} no-shows", delta_color="off",
             help=f"Show-up rate {pct(ev['show_up_rate'])} of confirmed registrants"),
        Tile("Session rating", f"{score(ev['avg_rating'])} / 5",
             help=f"Average of {ev['feedback_count']:,} ratings across {ev['sessions']} sessions; "
                  f"NPS {score(ev['nps'], '+.0f')} (approximated from 1-5 ratings)"),
        Tile("Avg session fill", pct(fill_mean),
             help="Average attendance as a share of room or stream capacity"),
        Tile("Sponsor leads", f"{ev['leads']:,}", delta=vs_target(ev["lead_attainment"]),
             help=f"Combined target: {ev['leads_target']:,} leads"),
        Tile("Actual cost", money(ev["actual_cost"]),
             delta=f"{pct(ev['budget_variance'], signed=True)} vs budget", delta_color="inverse"),
    ], per_row=3)


def _session_section(data) -> None:
    st.subheader("Sessions")
    track_col, mode_col, speaker_col, rating_col = st.columns([2, 2, 2, 2])
    _multiselect(track_col, "Track", data["track_options"], "dd_tracks")
    _multiselect(mode_col, "Mode", data["mode_options"], "dd_modes")
    _multiselect(speaker_col, "Speaker", data["speaker_options"], "dd_speakers")
    rating_col.slider("Rating range", 1.0, 5.0, (1.0, 5.0), step=0.1, key="dd_rating")
    shown = to_frame(data["sessions_shown"])
    st.caption(f"{len(shown)} of {data['sessions_total']} sessions match. These filters apply to both charts and "
               "the table.")
    if shown.empty:
        st.info("No sessions match these session filters.")
        return

    plot(st, session_fill_rates(shown))
    engagement = to_frame(data["engagement"])
    plot(st, engagement_comparison(engagement))
    if engagement["mode"].nunique() == 1:
        st.caption(f"All matching sessions are {engagement['mode'].iloc[0]}, so there is nothing to compare "
                   "against. Hybrid events mix modes.")
    else:
        st.caption("Hybrid sessions mix people in the room and online; the data does not say which, "
                   "so hybrid is shown as its own mode.")

    st.dataframe(
        shown[["track", "speaker", "mode", "rating", "feedback_count", "attendance", "capacity", "fill_rate",
               "avg_watch_minutes", "polls_per_attendee", "questions_per_attendee", "booth_visits"]],
        hide_index=True, width="stretch",
        column_config={
            "track": "Track", "speaker": "Speaker", "mode": "Mode",
            "rating": st.column_config.NumberColumn("Rating", format="%.2f"),
            "feedback_count": "Ratings", "attendance": "Attendance", "capacity": "Capacity",
            "fill_rate": st.column_config.ProgressColumn("Full", format="percent", min_value=0, max_value=1),
            "avg_watch_minutes": st.column_config.NumberColumn("Avg minutes", format="%.0f"),
            "polls_per_attendee": st.column_config.NumberColumn("Polls / person", format="%.2f"),
            "questions_per_attendee": st.column_config.NumberColumn("Questions / person", format="%.2f"),
            "booth_visits": "Booth visits",
        },
    )


def _sponsors_tab(ev, perf, event_id) -> None:
    st.markdown(f"**{ev['sponsors_met_target']} of {ev['sponsors']}** sponsors met their lead target, "
                f"averaging **{score(ev['leads_per_sponsor'], ',.0f')}** leads each at an estimated "
                f"**{money(ev['cost_per_lead'])}** per lead.")
    if perf.empty:
        return
    plot(st, sponsor_attainment(perf))
    table = perf[["sponsor", "tier", "package", "leads_target", "leads", "attainment",
                  "leads_booth", "leads_demo", "leads_virtual", "spend", "cost_per_lead"]]
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "sponsor": "Sponsor", "tier": "Tier", "package": "Package", "leads_target": "Target", "leads": "Leads",
        "attainment": st.column_config.ProgressColumn("Of target", format="percent", min_value=0, max_value=1.5),
        "leads_booth": "Booth", "leads_demo": "Demo", "leads_virtual": "Virtual",
        "spend": st.column_config.NumberColumn("Est. spend", format="dollar"),
        "cost_per_lead": st.column_config.NumberColumn("Cost per lead", format="dollar"),
    })
    st.download_button("Download sponsor results as CSV", table.to_csv(index=False).encode("utf-8"),
                       file_name=f"{event_id}_sponsor_results.csv", mime="text/csv")


def _feedback_tab(data) -> None:
    fb = SimpleNamespace(**data["feedback"])
    kpi_row([Tile("Positive (4-5)", pct(fb.positive)), Tile("Neutral (3)", pct(fb.neutral)),
             Tile("Negative (1-2)", pct(fb.negative)),
             Tile("NPS", score(fb.nps, "+.0f"),
                  help="Approximated from 1-5 ratings: 5 = promoter, 4 = passive, 1-3 = detractor")])
    good, bad = st.columns(2)
    labels = {"comment": "Comment", "mentions": "Mentions"}
    good.markdown("**Most common praise**")
    good.dataframe(to_frame(data["top_positive"]), hide_index=True, width="stretch", column_config=labels)
    bad.markdown("**Most common complaints**")
    bad.dataframe(to_frame(data["top_negative"]), hide_index=True, width="stretch", column_config=labels)


def render() -> None:
    page_header("Event drill-down", "Everything about one event: registrations, sessions, engagement, sponsor "
                "results and feedback.", icon=":material/event:")
    ss = st.session_state
    data = page_data("event-drilldown", event_candidates=event_candidates("drilldown_event"),
                     tracks=ss.get("dd_tracks", []), modes=ss.get("dd_modes", []),
                     speakers=ss.get("dd_speakers", []), rating=list(ss.get("dd_rating", (1.0, 5.0))))
    if data is None:
        return
    if data["empty"]:
        show_empty_state()
        return

    event_id = event_picker(data["event_options"], key="drilldown_event")
    ev = to_frame(data["event"]).iloc[0]
    _kpis(ev, data["sessions_fill_mean"])

    left, right = st.columns([3, 2])
    plot(left, registration_curve(to_frame(data["timeline"]), data["early_bird_days"]))
    plot(right, lead_sources(to_frame(data["lead_mix"])))

    if not data["has_sessions"]:
        st.info("This event has no sessions in the data.")
    else:
        _session_section(data)

    st.subheader("Sponsors and feedback")
    tab_sponsors, tab_feedback = st.tabs(["Sponsors", "Feedback"])
    with tab_sponsors:
        _sponsors_tab(ev, to_frame(data["sponsor_results"]), event_id)
    with tab_feedback:
        _feedback_tab(data)
