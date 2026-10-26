"""Ask the portfolio: chat with a tool-calling agent that only uses computed results.

When document search is on (Week 2), the agent can also search the event reports, pricing, sponsor
decks, SOPs and session abstracts. Each answer then lists the passages it cited ([S1], [S2], ...) and
shows how it was answered: its plan, every tool it called, and what each call returned."""
from __future__ import annotations

import streamlit as st

from frontend import client
from frontend.components.ai_usage import ai_unavailable_notice, record_usage
from frontend.components.charts import chart_from_spec, plot
from frontend.components.data_source import call, scope_request, scope_summary, show_empty_state
from frontend.components.layout import page_header
from frontend.components.tables import to_frame
from shared.filters import active_parts
from shared.models import AgentAnswerModel

_HISTORY_KEY = "ask_history"   # [{"role", "content", "answer": AgentAnswerModel | None, "scope": str}]
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
DOCUMENT_STARTERS = (
    "What went wrong at the Chicago DevSecOps Forum 2024, and how was it handled?",
    "What was the show-up rate at the Chicago DevSecOps Forum 2024, and what did the report recommend?",
    "Which package did Brightpath Security buy at the Austin Clinical Innovation Congress 2026, and what does it "
    "cost?",
    "By what time must badge scanners be charged and tested?",
)
_TRACE_ICONS = {"plan": ":material/checklist:", "tool": ":material/build:", "check": ":material/rule:",
                "answer": ":material/chat:"}


def _scope_text() -> str:
    summary = scope_summary()
    if summary is None:
        return "all data"
    return "; ".join(active_parts(summary["description"])) or "all data"


def _show_extras(answer: AgentAnswerModel, scope: str, index: int) -> None:
    tables = {r.id: to_frame(r.table) for r in answer.calculations}
    for spec in answer.charts:
        table = tables.get(spec.source_id)
        if table is not None and not table.empty:
            plot(st, chart_from_spec(spec, table), key=f"ask_chart_{index}_{spec.chart_id}")
    if answer.withheld:  # the final check removed unsupported claims instead of showing them
        st.info("**Withheld by the final check.** " + " ".join(answer.withheld), icon=":material/shield:")
    elif answer.unverified_numbers:
        st.warning("These figures could not be traced to a calculation, so treat them with caution: "
                   + ", ".join(answer.unverified_numbers), icon=":material/report:")
    for warning in answer.warnings:
        st.caption(f":orange[{warning}]")
    if answer.sources:
        _show_sources(answer)
    if answer.trace and (answer.sources or any(s.step in ("plan", "check") for s in answer.trace)):
        _show_trace(answer, index)
    label = (f"View calculations ({len(answer.calculations)} tool call{'s' if len(answer.calculations) != 1 else ''})"
             if answer.calculations else "View calculations (none: no figures were computed)")
    with st.expander(label, icon=":material/calculate:"):
        st.caption(f"Scope: {scope}. These are the exact results the assistant saw; numbers in the answer "
                   "must come from them.")
        for record in answer.calculations:
            args = ", ".join(f"{k}={v!r}" for k, v in record.args.items() if v not in (None, "", []))
            st.markdown(f"**{record.id} · `{record.tool}`**" + (f" ({args})" if args else ""))
            table = tables.get(record.id)
            if table is not None and not table.empty:
                st.dataframe(table, hide_index=True, width="stretch")
            else:
                st.json({k: v for k, v in record.result.items() if k != "result_id"}, expanded=False)


def _show_sources(answer: AgentAnswerModel) -> None:
    """The passages the answer could cite, cited ones first."""
    cited = [s for s in answer.sources if f"[{s.id}]" in answer.text]
    rest = [s for s in answer.sources if s not in cited]
    with st.expander(f"Sources ({len(cited)} cited, {len(answer.sources)} retrieved)", icon=":material/menu_book:"):
        st.caption("Passages from the documents the assistant searched. Answers cite them as [S1], [S2], ...")
        for source in cited + rest:
            mark = "cited" if source in cited else "not cited"
            st.markdown(f"**{source.id}** {source.citation} · {source.corpus.replace('_', ' ')} · "
                        f"score {source.score:.2f} · {mark}")
            st.caption(source.text[:600] + ("…" if len(source.text) > 600 else ""))


def _show_trace(answer: AgentAnswerModel, index: int) -> None:
    with st.expander("How this was answered", icon=":material/route:"):
        for number, step in enumerate(answer.trace, start=1):
            if step.step == "tool":
                args = ", ".join(f"{k}={v!r}" for k, v in step.args.items() if v not in (None, "", []))
                st.markdown(f"{number}. **Tool** `{step.tool}`" + (f" ({args})" if args else ""))
                st.caption(step.summary)
            elif step.step == "plan":
                st.markdown(f"{number}. **Plan:** {step.text}")
            elif step.step == "check":
                st.markdown(f"{number}. **Check:** {step.text}")
            else:
                st.markdown(f"{number}. **Answer** written from the results above.")


def _answer(question: str, scope: str) -> None:
    history = st.session_state.setdefault(_HISTORY_KEY, [])
    prior = [{"role": m["role"], "content": m["content"]} for m in history]
    history.append({"role": "user", "content": question, "answer": None, "scope": scope})
    try:
        with st.spinner("Looking it up..."):
            response = call(client.chat, {**scope_request(), "question": question, "history": prior}, reraise=True)
        if response is not None:
            record_usage(response["usage"])
            answer = AgentAnswerModel(**response["answer"])
            history.append({"role": "assistant", "content": answer.text, "answer": answer, "scope": scope})
    except client.ApiError as exc:
        record_usage(exc.usage)
        if exc.kind == "not_configured":
            content = f"⚠️ {exc.message}"
        elif exc.kind in ("timeout", "provider"):
            hint = ("Try a simpler or more specific question." if exc.kind == "timeout"
                    else "Check your connection, API key and model name in `.env`.")
            content = f"⚠️ {exc.message} {hint}"
        elif exc.kind == "unexpected":  # never crash the page on an unexpected failure
            content = f"⚠️ Something went wrong: {exc.exception_type}: {exc.message}"
        else:
            content = f"⚠️ {exc.message}"
        history.append({"role": "assistant", "content": content, "answer": None, "scope": scope})
    del history[:-MAX_STORED_MESSAGES]


def render() -> None:
    page_header("Ask the portfolio", "Ask questions in plain English. Every figure comes from a calculation you "
                "can inspect under the answer.", icon=":material/chat:")
    summary = scope_summary(show_errors=True)
    if summary is None:
        return
    if summary["events"] == 0:
        show_empty_state()
        return

    scope = _scope_text()
    status = client.status()
    limits = status["limits"]
    st.caption(f"Answers use only the filters shown above. Up to {limits['max_tool_calls']} calculations per "
               f"question; the last {limits['history_messages']} messages are remembered.")
    if not status["llm_configured"]:
        ai_unavailable_notice("Chat", "every dashboard page; they show the same figures this chat would look up.")
    docs = status.get("documents") or {}
    if docs.get("available"):
        st.caption(f"Document search is on: {docs['chunks']} passages from event reports, pricing, sponsor decks, "
                   f"SOPs and session abstracts ({docs['embedding_model']}). Answers cite them as [S1], [S2].")

    history = st.session_state.setdefault(_HISTORY_KEY, [])
    for index, message in enumerate(history):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("answer") is not None:
                _show_extras(message["answer"], message["scope"], index)

    if not history:
        st.markdown("**Try asking:**")
        cols = st.columns(2)
        starters = STARTERS + (DOCUMENT_STARTERS if docs.get("available") else ())
        for i, starter in enumerate(starters):
            if cols[i % 2].button(starter, key=f"ask_starter_{i}", width="stretch",
                                  disabled=not status["llm_configured"]):
                st.session_state[_PENDING_KEY] = starter
                st.rerun()
    elif st.button("Clear conversation", icon=":material/delete_sweep:", key="ask_clear"):
        st.session_state[_HISTORY_KEY] = []
        st.rerun()

    prompt = ("Ask about events, sponsors, documents or procedures" if docs.get("available")
              else "Ask about events, audiences or sponsors")
    typed = st.chat_input(prompt, disabled=not status["llm_configured"])
    question = typed or st.session_state.pop(_PENDING_KEY, None)
    if question:
        _answer(question.strip(), scope)
        st.rerun()
    st.caption(f"Each AI request times out after {status['timeout_s']:.0f}s and is retried up to "
               f"{status['max_retries']} times. The assistant never sees individual "
               "attendee, lead or comment records, only aggregated results.")
