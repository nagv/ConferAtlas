"""AI usage panel (sidebar) and the shared 'AI is not set up' notice for AI pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.ai.llm import UsageRecorder, missing_key_message
from core.config import get_settings

_USAGE_KEY = "ai_usage"


def get_usage_recorder() -> UsageRecorder:
    """This browser session's record of LLM calls (pass it to every AI function)."""
    return st.session_state.setdefault(_USAGE_KEY, UsageRecorder())


def ai_unavailable_notice(feature: str, still_works: str) -> None:
    """Explain exactly what is missing for an AI feature, how to fix it, and what works without it."""
    settings = get_settings()
    st.info(
        f"**{feature} needs an AI model, and none is set up.**\n\n"
        f"- **Missing:** {missing_key_message(settings)}\n"
        f"- **Configured:** provider `{settings.llm_provider}`, model `{settings.llm_model}`\n"
        f"- **Still works:** {still_works}",
        icon=":material/key_off:",
    )


def _fmt_cost(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def render_ai_usage_panel(container) -> None:
    """Fill ``container`` (a sidebar slot) with session totals and one row per LLM call."""
    settings = get_settings()
    recorder = get_usage_recorder()
    with container:
        label = f"AI usage · {recorder.total_calls} call{'s' if recorder.total_calls != 1 else ''}"
        with st.expander(label, icon=":material/data_usage:", expanded=False):
            if not settings.llm_configured:
                st.caption(":orange[AI features are off: no API key.] Every dashboard works without one; only "
                           "the AI buttons and chat need it.")
            else:
                st.caption(f"Model `{settings.llm_model}` · {settings.llm_timeout_s:.0f}s timeout · "
                           f"{settings.llm_max_retries} retries with backoff · max "
                           f"{settings.llm_max_output_tokens:,} output tokens")
            if recorder.total_calls == 0:
                st.caption("No AI calls yet this session.")
                return

            cost_known = recorder.cost_known and settings.input_price_per_mtok is not None
            totals = pd.DataFrame([
                ("Calls", f"{recorder.total_calls:,}" + (f" ({recorder.total_errors} failed)" if recorder.total_errors else "")),
                ("Input tokens", f"{recorder.total_input_tokens:,}"),
                ("Output tokens", f"{recorder.total_output_tokens:,}"),
                ("Est. cost", _fmt_cost(recorder.total_cost_usd) if cost_known else "n/a"),
                ("Avg latency", f"{recorder.avg_latency_s:.1f}s"),
            ], columns=["Session total", "Value"])
            st.dataframe(totals, hide_index=True, width="stretch")
            if not cost_known:
                st.caption("Set `LLM_INPUT_PRICE_PER_MTOK` and `LLM_OUTPUT_PRICE_PER_MTOK` in `.env` "
                           "(from your provider's pricing page) to see estimated cost.")

            calls = pd.DataFrame([{
                "Time": c.started.strftime("%H:%M:%S"), "Feature": c.purpose,
                "In": c.input_tokens, "Out": c.output_tokens, "Cost": _fmt_cost(c.cost_usd),
                "Latency": round(c.latency_s, 2), "Status": "ok" if c.status == "ok" else f"error: {c.error}",
            } for c in reversed(recorder.calls)])
            st.dataframe(calls, hide_index=True, width="stretch", column_config={
                "In": st.column_config.NumberColumn("In", format="%d", help="Input tokens"),
                "Out": st.column_config.NumberColumn("Out", format="%d", help="Output tokens"),
                "Latency": st.column_config.NumberColumn("Latency (s)", format="%.2f"),
            })
            st.caption("One row per request, including retries. Newest first.")
            if st.button("Reset usage", key="ai_usage_reset", icon=":material/restart_alt:"):
                st.session_state[_USAGE_KEY] = UsageRecorder()
                st.rerun()
