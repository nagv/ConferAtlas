"""Feedback analyzer: rating distribution, sentiment by event, themes, and AI analysis of comments."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import streamlit as st

from frontend import client
from frontend.components.ai_usage import ai_unavailable_notice, record_usage
from frontend.components.charts import keyword_themes_chart, plot, rating_distribution_chart, sentiment_by_event_chart
from frontend.components.data_source import call, page_data, scope_request, show_empty_state, using_uploaded_data
from frontend.components.kpi_cards import Tile, count, kpi_row, pct, score
from frontend.components.layout import page_header
from frontend.components.tables import to_frame
from shared.filters import prune
from shared.models import FeedbackAnalysisModel

_RESULT_KEY = "feedback_ai_result"  # (selection fingerprint, FeedbackAnalysisModel)
_SENTIMENT_STYLE = {"positive": "color: #047857", "neutral": "color: #475569",
                    "negative": "color: #B91C1C; font-weight: 600", "unknown": "color: #94A3B8"}
_OVERALL_ICON = {"positive": ":material/sentiment_satisfied:", "mixed": ":material/sentiment_neutral:",
                 "neutral": ":material/sentiment_neutral:", "negative": ":material/sentiment_dissatisfied:"}
_ERROR_HINTS = {
    "timeout": "Try again, or narrow the selection with the sidebar or page filters so there is less to send.",
    "invalid_output": "This is usually temporary. Try again; if it keeps happening, try another model in `.env`.",
    "provider": "Check your internet connection, API key and model name in `.env`.",
}
_RATINGS = [1, 2, 3, 4, 5]


def _multiselect(container, label, options, key, fmt=str):
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All", format_func=fmt)


def _page_filters(data) -> None:
    rating_col, track_col = st.columns(2)
    _multiselect(rating_col, "Rating", _RATINGS, "fb_ratings", lambda r: "★" * r)
    _multiselect(track_col, "Session track", data["track_options"], "fb_tracks")


def _requested_filters() -> dict:
    return {"ratings": list(st.session_state.get("fb_ratings", [])),
            "tracks": list(st.session_state.get("fb_tracks", []))}


def _run_analysis(fingerprint) -> None:
    status = client.status()
    with st.spinner(f"Asking {status['model']} to read the comments..."):
        try:
            response = call(client.ai, "feedback-analysis", {**scope_request(), **_requested_filters()},
                            reraise=True)
        except client.ApiError as exc:
            record_usage(exc.usage)
            if exc.kind == "not_configured":
                st.error(exc.message)
            elif exc.kind == "invalid_request":  # no comments to analyse
                st.info(exc.message)
            elif exc.kind in _ERROR_HINTS:
                st.error(f"**AI analysis failed:** {exc.message}\n\n{_ERROR_HINTS.get(exc.kind, '')}")
            elif exc.kind == "unexpected":  # anything unexpected: explain instead of crashing the page
                st.error(f"**AI analysis failed unexpectedly:** {exc.exception_type}: {exc.message}")
            else:
                st.error(f"**AI analysis failed:** {exc.message}")
            return
    if response is None:
        return
    record_usage(response["usage"])
    st.session_state[_RESULT_KEY] = (fingerprint, FeedbackAnalysisModel(**response["result"]))


def _show_results(result: FeedbackAnalysisModel) -> None:
    for warning in result.warnings:
        st.warning(warning, icon=":material/info:")
    total = sum(t.count for t in result.themes) or 1
    kpi_row([
        Tile("Overall sentiment", result.overall_sentiment.title()),
        Tile("Themes found", str(len(result.themes))),
        Tile("Comments analysed", f"{result.mentions:,}",
             help=f"{result.unique_comments} distinct comments covering {result.mentions:,} responses"
                  + (f"; {result.skipped_comments} distinct comments left out" if result.skipped_comments else "")),
        Tile("Batches", str(result.batches), help="Requests sent to the model"),
    ])
    st.markdown(f"#### {_OVERALL_ICON.get(result.overall_sentiment, '')} Top actions")
    for col, (i, action) in zip(st.columns(max(len(result.action_items), 1)), enumerate(result.action_items, 1)):
        with col.container(border=True):
            st.markdown(f"**{i}.** {action}")

    st.markdown("#### Themes")
    per_row = 3
    for start in range(0, len(result.themes), per_row):
        for col, theme in zip(st.columns(per_row), result.themes[start:start + per_row]):
            with col.container(border=True):
                st.markdown(f"**{theme.name}**")
                st.caption(f"{theme.count:,} mentions · {theme.count / total:.0%} of tagged comments")
                st.markdown(f"> {theme.example_quote}")

    st.markdown("#### Comments tagged by theme and sentiment")
    table = (pd.DataFrame([c.model_dump() for c in result.tagged])
             .sort_values(["mentions", "comment"], ascending=[False, True]))
    styled = table.style.map(lambda v: _SENTIMENT_STYLE.get(v, ""), subset=["sentiment"])
    st.dataframe(styled, hide_index=True, width="stretch", column_config={
        "comment": st.column_config.TextColumn("Comment", width="large"), "mentions": "Mentions",
        "avg_rating": st.column_config.NumberColumn("Avg rating", format="%.1f"),
        "theme": "Theme", "sentiment": "Sentiment",
    })
    st.download_button("Download tagged comments as CSV", table.to_csv(index=False).encode("utf-8"),
                       file_name="feedback_themes.csv", mime="text/csv")


def _ai_section(data) -> None:
    st.subheader("AI analysis")
    status = client.status()
    preview = data["preview"]
    if preview is None:
        st.info("There are no written comments in the current selection to analyse.")
        return
    limits = status["limits"]
    batch_tokens, max_tokens = limits["feedback_batch_tokens"], limits["feedback_max_total_tokens"]
    skipped = preview["skipped"]
    st.caption(f"Sends **{preview['comments']} distinct comments** (covering {preview['mentions']:,} "
               f"responses; repeats are sent once with a count), about {preview['tokens']:,} tokens in "
               f"{preview['batches']} batch(es) of up to {batch_tokens:,} tokens. Each request times out after "
               f"{limits['feedback_request_timeout_s']:.0f}s." + (f" {skipped} rarely used comments are over the "
                                                                  f"{max_tokens:,}-token limit and will be "
                                                                  "left out." if skipped else ""))
    if not status["llm_configured"]:
        ai_unavailable_notice("AI feedback analysis",
                              "the rating distribution, sentiment by event and keyword themes above.")
    fingerprint = data["fingerprint"]
    reviewed = True
    if using_uploaded_data() or status["custom_data_dir"]:
        st.info("AI analysis sends comment text to your configured AI provider. Common contact details are "
                "redacted automatically, but names and other identifying details may remain. Review and "
                "de-identify your comments before sending them.")
        reviewed = st.checkbox("I have reviewed these comments and may send them to the AI provider",
                               key=f"fb_reviewed_{fingerprint}")
    if st.button("Analyse feedback", type="primary", icon=":material/auto_awesome:",
                 disabled=not status["llm_configured"] or not reviewed, key="fb_analyse"):
        _run_analysis(fingerprint)

    stored = st.session_state.get(_RESULT_KEY)
    if stored:
        stored_fingerprint, result = stored
        if stored_fingerprint != fingerprint:
            st.info("These results are for a different selection of comments. Press **Analyse feedback** to "
                    "analyse the current one.", icon=":material/history:")
        _show_results(result)


def render() -> None:
    page_header("Feedback analyzer", "What attendees said: ratings, sentiment by event, recurring themes and an "
                "optional AI analysis of the comments.", icon=":material/reviews:")
    data = page_data("feedback", **_requested_filters())
    if data is None:
        return
    if data["empty"]:
        show_empty_state("feedback")
        return

    _page_filters(data)
    if data["no_match"]:
        st.info("No feedback matches these page filters.")
        return
    summary = SimpleNamespace(**data["summary"])
    kpi_row([
        Tile("Responses", count(summary.responses), help=f"Exactly {summary.responses:,} ratings"),
        Tile("Avg rating (of 5)", score(summary.avg_rating)),
        Tile("NPS", score(summary.nps, "+.0f"), help="Approximated from 1-5 ratings: 5 = promoter, 1-3 = detractor"),
        Tile("Commented", pct(summary.with_comment / summary.responses),
             help=f"{summary.with_comment:,} responses include written feedback"),
    ])

    left, right = st.columns([2, 3])
    plot(left, rating_distribution_chart(to_frame(data["rating_distribution"])))
    plot(right, sentiment_by_event_chart(to_frame(data["sentiment_by_event"])))
    themes = to_frame(data["keyword_themes"])
    if themes.empty:
        st.caption("No comments match the keyword themes.")
    else:
        plot(st, keyword_themes_chart(themes))
        st.caption("Keyword themes are a quick, free first look: they only match listed words. "
                   "Use **Analyse feedback** below for themes an AI model groups by meaning.")
    _ai_section(data)
