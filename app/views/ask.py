"""Ask the portfolio: chat with a tool-calling agent that only uses computed results."""
from __future__ import annotations

from datetime import date

import streamlit as st

from app.components.charts import chart_from_spec, plot
from app.components.data_source import applied_filters, get_data, get_filtered_data, show_empty_state
from app.components.ai_usage import ai_unavailable_notice, get_usage_recorder
from core.ai.llm import LLMNotConfiguredError
from core.ai.portfolio_agent import (
    HISTORY_MESSAGES,
    MAX_TOOL_CALLS,
    AgentAnswer,
    AgentError,
    PortfolioToolkit,
    ask_portfolio,
)
from core.config import get_settings
from app.components.layout import page_header
from core.filters import describe

_HISTORY_KEY = "ask_history"   # [{"role", "content", "answer": AgentAnswer | None, "scope": str}]
_PENDING_KEY = "ask_pending"
MAX_STORED_MESSAGES = 40        # what the page keeps on screen; the model only sees the last few
STARTERS = (
    "Give me a one-paragraph summary of portfolio performance.",
    "Which sponsors are furthest below their lead target?",
    "Which events had the best and worst show-up rates? Chart show-up rate by event.",
    "What is the seniority mix of our audience?",
    "Compare our two largest events.",
    "How many attendees are returning versus new?",
)


def _scope_text() -> str:
    full = get_data(show_errors=False)
    if full is None:
        return "all data"
    parts = [f"{label}: {value}" for label, value in describe(applied_filters(), full, date.today())
             if value not in ("All", "All time")]
    return "; ".join(parts) or "all data"


def _show_extras(answer: AgentAnswer, scope: str, index: int) -> None:
    tables = {r.id: r.table for r in answer.calculations}
    for spec in answer.charts:
        table = tables.get(spec.source_id)
        if table is not None and not table.empty:
            plot(st, chart_from_spec(spec, table), key=f"ask_chart_{index}_{spec.chart_id}")
    if answer.unverified_numbers:
        st.warning("These figures could not be traced to a calculation, so treat them with caution: "
                   + ", ".join(answer.unverified_numbers), icon=":material/report:")
    for warning in answer.warnings:
        st.caption(f":orange[{warning}]")
    label = (f"View calculations ({len(answer.calculations)} tool call{'s' if len(answer.calculations) != 1 else ''})"
             if answer.calculations else "View calculations (none: no figures were computed)")
    with st.expander(label, icon=":material/calculate:"):
        st.caption(f"Scope: {scope}. These are the exact results the assistant saw; numbers in the answer "
                   "must come from them.")
        for record in answer.calculations:
            args = ", ".join(f"{k}={v!r}" for k, v in record.args.items() if v not in (None, "", []))
            st.markdown(f"**{record.id} · `{record.tool}`**" + (f" ({args})" if args else ""))
            if record.table is not None and not record.table.empty:
                st.dataframe(record.table, hide_index=True, width="stretch")
            else:
                st.json({k: v for k, v in record.result.items() if k != "result_id"}, expanded=False)


def _answer(question: str, t, scope: str) -> None:
    history = st.session_state.setdefault(_HISTORY_KEY, [])
    prior = [{"role": m["role"], "content": m["content"]} for m in history]
    history.append({"role": "user", "content": question, "answer": None, "scope": scope})
    toolkit = PortfolioToolkit(t, history=get_data(show_errors=False), scope=scope)
    try:
        with st.spinner("Looking it up..."):
            answer = ask_portfolio(question, toolkit, history=prior, recorder=get_usage_recorder())
        history.append({"role": "assistant", "content": answer.text, "answer": answer, "scope": scope})
    except LLMNotConfiguredError as exc:
        history.append({"role": "assistant", "content": f"⚠️ {exc}", "answer": None, "scope": scope})
    except AgentError as exc:
        hint = ("Try a simpler or more specific question." if exc.kind == "timeout"
                else "Check your connection, API key and model name in `.env`.")
        history.append({"role": "assistant", "content": f"⚠️ {exc} {hint}", "answer": None, "scope": scope})
    except Exception as exc:  # never crash the page on an unexpected failure
        history.append({"role": "assistant", "content": f"⚠️ Something went wrong: {type(exc).__name__}: {exc}",
                        "answer": None, "scope": scope})
    del history[:-MAX_STORED_MESSAGES]


def render() -> None:
    page_header("Ask the portfolio", "Ask questions in plain English. Every figure comes from a calculation you "
                "can inspect under the answer.", icon=":material/chat:")
    t = get_filtered_data()
    if t is None:
        return
    if t["events.csv"].empty:
        show_empty_state()
        return

    scope = _scope_text()
    settings = get_settings()
    st.caption(f"Answers use only the filters shown above. Up to {MAX_TOOL_CALLS} calculations per question; "
               f"the last {HISTORY_MESSAGES} messages are remembered.")
    if not settings.llm_configured:
        ai_unavailable_notice("Chat", "every dashboard page; they show the same figures this chat would look up.")

    history = st.session_state.setdefault(_HISTORY_KEY, [])
    for index, message in enumerate(history):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("answer") is not None:
                _show_extras(message["answer"], message["scope"], index)

    if not history:
        st.markdown("**Try asking:**")
        cols = st.columns(2)
        for i, starter in enumerate(STARTERS):
            if cols[i % 2].button(starter, key=f"ask_starter_{i}", width="stretch", disabled=not settings.llm_configured):
                st.session_state[_PENDING_KEY] = starter
                st.rerun()
    elif st.button("Clear conversation", icon=":material/delete_sweep:", key="ask_clear"):
        st.session_state[_HISTORY_KEY] = []
        st.rerun()

    typed = st.chat_input("Ask about events, audiences or sponsors", disabled=not settings.llm_configured)
    question = typed or st.session_state.pop(_PENDING_KEY, None)
    if question:
        _answer(question.strip(), t, scope)
        st.rerun()
    st.caption(f"Each AI request times out after {settings.llm_timeout_s:.0f}s and is retried up to "
               f"{settings.llm_max_retries} times. The assistant never sees individual "
               "attendee, lead or comment records, only aggregated results.")
