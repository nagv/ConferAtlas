"""Feedback analyzer: rating distribution, sentiment by event, themes, and AI analysis of comments."""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from app.components.charts import keyword_themes_chart, plot, rating_distribution_chart, sentiment_by_event_chart
from app.components.data_source import get_filtered_data, show_empty_state, using_uploaded_data
from app.components.kpi_cards import Tile, count, kpi_row, pct, score
from app.components.layout import page_header
from core.ai.feedback import (
    DEFAULT_BATCH_TOKENS,
    DEFAULT_MAX_TOTAL_TOKENS,
    DEFAULT_REQUEST_TIMEOUT_S,
    FeedbackAnalysis,
    FeedbackAnalysisError,
    analyze_feedback,
    batch_comments,
    estimate_tokens,
    prepare_comments,
)
from app.components.ai_usage import ai_unavailable_notice, get_usage_recorder
from core.ai.llm import LLMNotConfiguredError
from core.analysis import feedback_summary, keyword_themes, rating_distribution, sentiment_by_event
from core.config import DEFAULT_DATA_DIR, get_settings
from core.filters import prune

_RESULT_KEY = "feedback_ai_result"  # (selection fingerprint, FeedbackAnalysis)
_SENTIMENT_STYLE = {"positive": "color: #047857", "neutral": "color: #475569",
                    "negative": "color: #B91C1C; font-weight: 600", "unknown": "color: #94A3B8"}
_OVERALL_ICON = {"positive": ":material/sentiment_satisfied:", "mixed": ":material/sentiment_neutral:",
                 "neutral": ":material/sentiment_neutral:", "negative": ":material/sentiment_dissatisfied:"}
_ERROR_HINTS = {
    "timeout": "Try again, or narrow the selection with the sidebar or page filters so there is less to send.",
    "invalid_output": "This is usually temporary. Try again; if it keeps happening, try another model in `.env`.",
    "provider": "Check your internet connection, API key and model name in `.env`.",
}


def _multiselect(container, label, options, key, fmt=str):
    current = list(st.session_state.get(key, []))
    if (kept := list(prune(current, options))) != current:
        st.session_state[key] = kept
    return container.multiselect(label, options, key=key, placeholder="All", format_func=fmt)


def _filtered_feedback(t):
    fb = t["feedback.csv"].merge(t["sessions.csv"][["session_id", "track"]], on="session_id", how="left")
    rating_col, track_col = st.columns(2)
    ratings = _multiselect(rating_col, "Rating", [1, 2, 3, 4, 5], "fb_ratings", lambda r: "★" * r)
    tracks = _multiselect(track_col, "Session track", sorted(fb["track"].dropna().unique()), "fb_tracks")
    if ratings:
        fb = fb[fb["rating"].isin(ratings)]
    if tracks:
        fb = fb[fb["track"].isin(tracks)]
    return fb.reset_index(drop=True)


def _fingerprint(prepared) -> str:
    text = prepared[["comment", "mentions", "avg_rating"]].to_json(orient="records")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_analysis(fb, fingerprint) -> None:
    settings = get_settings()
    with st.spinner(f"Asking {settings.llm_model} to read the comments..."):
        try:
            result = analyze_feedback(fb, settings=settings, recorder=get_usage_recorder())
        except LLMNotConfiguredError as exc:
            st.error(str(exc))
            return
        except ValueError as exc:
            st.info(str(exc))
            return
        except FeedbackAnalysisError as exc:
            st.error(f"**AI analysis failed:** {exc}\n\n{_ERROR_HINTS.get(exc.kind, '')}")
            return
        except Exception as exc:  # anything unexpected: explain instead of crashing the page
            st.error(f"**AI analysis failed unexpectedly:** {type(exc).__name__}: {exc}")
            return
    st.session_state[_RESULT_KEY] = (fingerprint, result)


def _show_results(result: FeedbackAnalysis) -> None:
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


def _ai_section(fb) -> None:
    st.subheader("AI analysis")
    settings = get_settings()
    prepared = prepare_comments(fb)
    if prepared.empty:
        st.info("There are no written comments in the current selection to analyse.")
        return
    batches, skipped = batch_comments(prepared, DEFAULT_BATCH_TOKENS, DEFAULT_MAX_TOTAL_TOKENS)
    tokens = sum(estimate_tokens(c) for b in batches for c in b["comment"])
    st.caption(f"Sends **{len(prepared) - skipped} distinct comments** (covering {int(prepared['mentions'].sum()):,} "
               f"responses; repeats are sent once with a count), about {tokens:,} tokens in {len(batches)} "
               f"batch(es) of up to {DEFAULT_BATCH_TOKENS:,} tokens. Each request times out after "
               f"{DEFAULT_REQUEST_TIMEOUT_S:.0f}s." + (f" {skipped} rarely used comments are over the "
                                                      f"{DEFAULT_MAX_TOTAL_TOKENS:,}-token limit and will be "
                                                      "left out." if skipped else ""))
    if not settings.llm_configured:
        ai_unavailable_notice("AI feedback analysis",
                              "the rating distribution, sentiment by event and keyword themes above.")
    fingerprint = _fingerprint(prepared)
    reviewed = True
    if using_uploaded_data() or settings.data_dir.resolve() != DEFAULT_DATA_DIR.resolve():
        st.info("AI analysis sends comment text to your configured AI provider. Common contact details are "
                "redacted automatically, but names and other identifying details may remain. Review and "
                "de-identify your comments before sending them.")
        reviewed = st.checkbox("I have reviewed these comments and may send them to the AI provider",
                               key=f"fb_reviewed_{fingerprint}")
    if st.button("Analyse feedback", type="primary", icon=":material/auto_awesome:",
                 disabled=not settings.llm_configured or not reviewed, key="fb_analyse"):
        _run_analysis(fb, fingerprint)

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
    t = get_filtered_data()
    if t is None:
        return
    if t["feedback.csv"].empty:
        show_empty_state("feedback")
        return

    fb = _filtered_feedback(t)
    if fb.empty:
        st.info("No feedback matches these page filters.")
        return
    summary = feedback_summary(fb)
    kpi_row([
        Tile("Responses", count(summary.responses), help=f"Exactly {summary.responses:,} ratings"),
        Tile("Avg rating (of 5)", score(summary.avg_rating)),
        Tile("NPS", score(summary.nps, "+.0f"), help="Approximated from 1-5 ratings: 5 = promoter, 1-3 = detractor"),
        Tile("Commented", pct(summary.with_comment / summary.responses),
             help=f"{summary.with_comment:,} responses include written feedback"),
    ])

    left, right = st.columns([2, 3])
    plot(left, rating_distribution_chart(rating_distribution(fb)))
    plot(right, sentiment_by_event_chart(sentiment_by_event(fb, t["events.csv"])))
    themes = keyword_themes(fb)
    if themes.empty:
        st.caption("No comments match the keyword themes.")
    else:
        plot(st, keyword_themes_chart(themes))
        st.caption("Keyword themes are a quick, free first look: they only match listed words. "
                   "Use **Analyse feedback** below for themes an AI model groups by meaning.")
    _ai_section(fb)
