"""AI insights: LLM-written event pitches and sponsor briefs grounded in computed metrics."""
from __future__ import annotations

import hashlib
import json

import streamlit as st

from frontend import client
from frontend.components.ai_usage import ai_unavailable_notice, record_usage
from frontend.components.data_source import (
    applied_filters,
    call,
    data_source_key,
    event_candidates,
    event_picker,
    page_data,
    scope_request,
    show_empty_state,
    sponsor_candidates,
    sponsor_picker,
)
from frontend.components.layout import page_header

_EVENT_PITCH, _SPONSOR_BRIEF = "Event pitch for sponsors", "Renewal brief for one sponsor"


def render() -> None:
    page_header("AI insights", "AI-written sponsor pitches and renewal briefs, grounded in the computed figures.",
                icon=":material/auto_awesome:")
    if call(lambda ds: ds) is None:  # the data could not be loaded; the problem is shown
        return

    status = client.status()
    kind = st.radio("What to write", [_EVENT_PITCH, _SPONSOR_BRIEF], horizontal=True)
    if kind == _EVENT_PITCH:
        request = {"kind": "event", "candidates": event_candidates("ai_event")}
        data = page_data("ai-insights", **request)
        if data is None:
            return
        if data["empty"]:
            show_empty_state()
            return
        subject_id = event_picker(data["options"], key="ai_event")
        cache_key = f"ai::event::{subject_id}"
    else:
        request = {"kind": "sponsor", "candidates": sponsor_candidates("ai_sponsor")}
        data = page_data("ai-insights", **request)
        if data is None:
            return
        if data["empty"]:
            show_empty_state("sponsors")
            return
        subject_id = sponsor_picker(data["options"], key="ai_sponsor")
        cache_key = f"ai::sponsor::{subject_id}"
    subject, task, context = data["subject"], data["task"], data["context"]

    if not status["llm_configured"]:
        ai_unavailable_notice("Writing the pitch or brief with AI",
                              "the rule-based summary below, built from the same figures, and every dashboard.")
        st.markdown(data["fallback"])
        return

    col1, col2 = st.columns(2)
    audience = col1.selectbox("Audience", data["audiences"])
    tone = col2.selectbox("Tone", ["Confident and data-driven", "Concise executive brief",
                                   "Warm and relationship-focused"])
    fingerprint = hashlib.sha256(json.dumps({
        "source": data_source_key(), "filters": str(applied_filters()), "context": context,
        "subject": subject, "task": task, "audience": audience, "tone": tone,
        "provider": status["provider"], "model": status["model"],
        "endpoint": status["base_url"], "temperature": status["temperature"],
        "output_limit": status["max_output_tokens"],
    }, sort_keys=True).encode("utf-8")).hexdigest()

    with st.expander("Metrics sent to the model"):
        st.code(context, language="text")

    if st.button("Generate", type="primary"):
        with st.spinner(f"Asking {status['model']}..."):
            try:
                response = call(client.ai, "pitch", {**scope_request(), **request, "audience": audience,
                                                     "tone": tone}, reraise=True)
                if response is not None:
                    record_usage(response["usage"])
                    st.session_state[cache_key] = (fingerprint, response["text"])
            except client.ApiError as exc:
                record_usage(exc.usage)
                if exc.kind == "unexpected":  # anything unexpected: explain instead of crashing the page
                    st.error(f"The AI request failed unexpectedly ({exc.exception_type}): {exc.message}")
                else:  # messages are already written for users
                    st.error(exc.message)

    stored = st.session_state.get(cache_key)
    if isinstance(stored, tuple) and stored[0] == fingerprint:
        st.markdown(stored[1])
