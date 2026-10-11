"""AI insights: LLM-written event pitches and sponsor briefs grounded in computed metrics."""
from __future__ import annotations

import hashlib
import json

import streamlit as st

from app.components.data_source import applied_filters, data_source_key, event_picker, get_filtered_data, show_empty_state, sponsor_picker
from core.ai import (
    EVENT_TASK,
    SPONSOR_TASK,
    LLMNotConfiguredError,
    build_event_context,
    build_sponsor_context,
    fallback_event_summary,
    fallback_sponsor_summary,
    generate_pitch,
)
from core.analysis import event_kpis, feedback_summary, filter_dataset, sponsor_scorecards, sponsorship_results
from app.components.ai_usage import ai_unavailable_notice, get_usage_recorder
from core.ai.llm import LLMError
from core.config import get_settings
from app.components.layout import page_header

_EVENT_PITCH, _SPONSOR_BRIEF = "Event pitch for sponsors", "Renewal brief for one sponsor"


def render() -> None:
    page_header("AI insights", "AI-written sponsor pitches and renewal briefs, grounded in the computed figures.",
                icon=":material/auto_awesome:")
    t = get_filtered_data()
    if t is None:
        return

    settings = get_settings()
    kind = st.radio("What to write", [_EVENT_PITCH, _SPONSOR_BRIEF], horizontal=True)
    if kind == _EVENT_PITCH:
        if t["events.csv"].empty:
            show_empty_state()
            return
        event_id = event_picker(t, key="ai_event")
        scope = filter_dataset(t, event_ids=[event_id])
        ev = event_kpis(scope).iloc[0]
        subject, task, cache_key = ev["name"], EVENT_TASK, f"ai::event::{event_id}"
        context = build_event_context(ev, sponsorship_results(scope), feedback_summary(scope["feedback.csv"]))
        fallback = fallback_event_summary(ev)
        audiences = ["Prospective sponsors", "Renewing sponsors", "Executive sponsor committee"]
    else:
        if t["sponsorships.csv"].empty:
            show_empty_state("sponsors")
            return
        sponsor_id = sponsor_picker(t, key="ai_sponsor")
        scope = filter_dataset(t, sponsor_ids=[sponsor_id])
        card = sponsor_scorecards(scope).iloc[0]
        subject, task, cache_key = card["sponsor"], SPONSOR_TASK, f"ai::sponsor::{sponsor_id}"
        context = build_sponsor_context(card, sponsorship_results(scope))
        fallback = fallback_sponsor_summary(card)
        audiences = ["Sponsor's marketing lead", "Sponsor's executive team"]

    if not settings.llm_configured:
        ai_unavailable_notice("Writing the pitch or brief with AI",
                              "the rule-based summary below, built from the same figures, and every dashboard.")
        st.markdown(fallback)
        return

    col1, col2 = st.columns(2)
    audience = col1.selectbox("Audience", audiences)
    tone = col2.selectbox("Tone", ["Confident and data-driven", "Concise executive brief",
                                   "Warm and relationship-focused"])
    fingerprint = hashlib.sha256(json.dumps({
        "source": data_source_key(), "filters": str(applied_filters()), "context": context,
        "subject": subject, "task": task, "audience": audience, "tone": tone,
        "provider": settings.llm_provider, "model": settings.llm_model,
        "endpoint": settings.base_url, "temperature": settings.llm_temperature,
        "output_limit": settings.llm_max_output_tokens,
    }, sort_keys=True).encode("utf-8")).hexdigest()

    with st.expander("Metrics sent to the model"):
        st.code(context, language="text")

    if st.button("Generate", type="primary"):
        with st.spinner(f"Asking {settings.llm_model}..."):
            try:
                text = generate_pitch(
                    subject, context, task=task, audience=audience, tone=tone, settings=settings,
                    recorder=get_usage_recorder(),
                )
                st.session_state[cache_key] = (fingerprint, text)
            except (LLMNotConfiguredError, LLMError) as exc:  # messages are already written for users
                st.error(str(exc))
            except Exception as exc:  # anything unexpected: explain instead of crashing the page
                st.error(f"The AI request failed unexpectedly ({type(exc).__name__}): {exc}")

    stored = st.session_state.get(cache_key)
    if isinstance(stored, tuple) and stored[0] == fingerprint:
        st.markdown(stored[1])
