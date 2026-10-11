"""Event drill-down: one event's KPIs, registrations, sessions, engagement, sponsors and feedback."""
from __future__ import annotations

import streamlit as st

from app.components.charts import (
    engagement_comparison,
    lead_sources,
    plot,
    registration_curve,
    session_fill_rates,
    sponsor_attainment,
)
from app.components.data_source import event_picker, get_filtered_data, show_empty_state, using_uploaded_data
from app.components.kpi_cards import Tile, kpi_row, money, pct, score, vs_target
from app.components.layout import page_header
from core.analysis import (
    engagement_by_mode,
    event_kpis,
    feedback_summary,
    filter_dataset,
    filter_sessions,
    lead_source_mix,
    registration_timeline,
    session_performance,
    sponsorship_results,
    top_comments,
)
from core.filters import prune
from core.generator import EARLY_BIRD_DAYS


def _multiselect(container, label: str, options: list[str], key: str) -> list[str]:
    # A new event has different tracks/speakers: drop choices that no longer exist.
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All")


def _kpis(ev, sessions) -> None:
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
        Tile("Avg session fill", pct(sessions["fill_rate"].mean()),
             help="Average attendance as a share of room or stream capacity"),
        Tile("Sponsor leads", f"{ev['leads']:,}", delta=vs_target(ev["lead_attainment"]),
             help=f"Combined target: {ev['leads_target']:,} leads"),
        Tile("Actual cost", money(ev["actual_cost"]),
             delta=f"{pct(ev['budget_variance'], signed=True)} vs budget", delta_color="inverse"),
    ], per_row=3)


def _session_section(scope, sessions) -> None:
    st.subheader("Sessions")
    track_col, mode_col, speaker_col, rating_col = st.columns([2, 2, 2, 2])
    tracks = _multiselect(track_col, "Track", sorted(sessions["track"].unique()), "dd_tracks")
    modes = _multiselect(mode_col, "Mode", sorted(sessions["mode"].unique()), "dd_modes")
    speakers = _multiselect(speaker_col, "Speaker", sorted(sessions["speaker"].unique()), "dd_speakers")
    low, high = rating_col.slider("Rating range", 1.0, 5.0, (1.0, 5.0), step=0.1, key="dd_rating")
    shown = filter_sessions(sessions, tracks=tracks, modes=modes, speakers=speakers, min_rating=low, max_rating=high)
    st.caption(f"{len(shown)} of {len(sessions)} sessions match. These filters apply to both charts and the table.")
    if shown.empty:
        st.info("No sessions match these session filters.")
        return

    plot(st, session_fill_rates(shown))
    in_scope = {"sessions.csv": shown,
                "engagement.csv": scope["engagement.csv"][scope["engagement.csv"]["session_id"].isin(shown["session_id"])]}
    engagement = engagement_by_mode(in_scope)
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


def _feedback_tab(feedback) -> None:
    fb = feedback_summary(feedback)
    kpi_row([Tile("Positive (4-5)", pct(fb.positive)), Tile("Neutral (3)", pct(fb.neutral)),
             Tile("Negative (1-2)", pct(fb.negative)),
             Tile("NPS", score(fb.nps, "+.0f"),
                  help="Approximated from 1-5 ratings: 5 = promoter, 4 = passive, 1-3 = detractor")])
    good, bad = st.columns(2)
    labels = {"comment": "Comment", "mentions": "Mentions"}
    good.markdown("**Most common praise**")
    good.dataframe(top_comments(feedback, "positive"), hide_index=True, width="stretch", column_config=labels)
    bad.markdown("**Most common complaints**")
    bad.dataframe(top_comments(feedback, "negative"), hide_index=True, width="stretch", column_config=labels)


def render() -> None:
    page_header("Event drill-down", "Everything about one event: registrations, sessions, engagement, sponsor "
                "results and feedback.", icon=":material/event:")
    t = get_filtered_data()
    if t is None:
        return
    if t["events.csv"].empty:
        show_empty_state()
        return

    event_id = event_picker(t, key="drilldown_event")
    scope = filter_dataset(t, event_ids=[event_id])
    ev = event_kpis(scope).iloc[0]
    sessions = session_performance(scope)
    _kpis(ev, sessions)

    left, right = st.columns([3, 2])
    early_bird = None if using_uploaded_data() else EARLY_BIRD_DAYS
    plot(left, registration_curve(registration_timeline(scope["registrations.csv"], ev["start_date"]), early_bird))
    plot(right, lead_sources(lead_source_mix(scope["leads.csv"])))

    if sessions.empty:
        st.info("This event has no sessions in the data.")
    else:
        _session_section(scope, sessions)

    st.subheader("Sponsors and feedback")
    tab_sponsors, tab_feedback = st.tabs(["Sponsors", "Feedback"])
    with tab_sponsors:
        _sponsors_tab(ev, sponsorship_results(scope), event_id)
    with tab_feedback:
        _feedback_tab(scope["feedback.csv"])
