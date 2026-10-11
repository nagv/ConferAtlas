"""LangChain-powered AI features (no Streamlit imports here)."""
from core.ai.insights import (
    EVENT_TASK,
    SPONSOR_TASK,
    LLMNotConfiguredError,
    build_event_context,
    build_sponsor_context,
    fallback_event_summary,
    fallback_sponsor_summary,
    generate_pitch,
)

__all__ = [
    "EVENT_TASK",
    "SPONSOR_TASK",
    "LLMNotConfiguredError",
    "build_event_context",
    "build_sponsor_context",
    "fallback_event_summary",
    "fallback_sponsor_summary",
    "generate_pitch",
]
