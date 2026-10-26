"""Sponsor renewals (Week 3): a team of AI agents drafts renewal packs; a person approves each one.

The backend runs the Sponsor Renewal Desk in the background. This page starts a run, follows its
plan and activity while it works, and shows every pack that waits for a decision: approve it,
approve your edited version, or reject it with feedback for the writer. Nothing is ever sent.
"""
from __future__ import annotations

import re

import streamlit as st

from frontend import client
from frontend.components.ai_usage import ai_unavailable_notice, record_usage
from frontend.components.data_source import call, page_data, scope_request, show_empty_state, sponsor_candidates
from frontend.components.layout import page_header

RUN_KEY = "rn_run"            # the run this browser session is following
USAGE_KEY = "rn_usage_seen"   # usage records already added to the sidebar panel, per run
POLL_SECONDS = 3
TIERS = ["platinum", "gold", "silver", "bronze"]
_STATUS = {
    "queued": ("Queued", "gray"), "researching": ("Researching", "blue"), "drafting": ("Drafting", "blue"),
    "in_review": ("In review", "blue"), "awaiting_approval": ("Waiting for you", "orange"),
    "approved": ("Approved", "green"), "rejected": ("Rejected, redrafting", "orange"),
    "needs_edit": ("Needs your edit", "red"), "failed": ("Failed", "red"),
}
_AGENTS = {"orchestrator": "Orchestrator", "analyst": "Analyst", "package-advisor": "Package advisor",
           "writer": "Writer", "reviewer": "Reviewer", "specialist": "Specialist"}
_STEP = {"list_sponsors": "picked the sponsors", "write_todos": "updated the plan", "task": "delegated to",
         "sponsor_facts": "pulled the fact sheet", "search_sponsor_decks": "searched sponsor decks",
         "search_event_reports": "searched event reports", "search_pricing": "searched pricing",
         "save_draft": "saved a draft", "check_pack": "checked the draft", "finalize_pack": "asked for approval",
         "read_file": "read a file", "write_file": "wrote a file", "edit_file": "edited a file",
         "ls": "listed files", "glob": "looked for files", "grep": "searched files"}


def _badge(status: str) -> str:
    label, color = _STATUS.get(status, (status, "gray"))
    return f":{color}-badge[{label}]"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "sponsor"


def _record_new_usage(run: dict) -> None:
    """Adds this run's model calls to the sidebar usage panel once each, however often the page polls."""
    seen = st.session_state.setdefault(USAGE_KEY, {})
    already = seen.get(run["run_id"], 0)
    record_usage(run["usage"][already:])
    seen[run["run_id"]] = len(run["usage"])


# --- starting a run -------------------------------------------------------------------------------

def _start_form(data: dict) -> None:
    names = [name for _, name in data["options"]]
    with st.form("rn_start"):
        how = st.radio("Prepare packs for", ["A sponsor tier", "Named sponsors"], horizontal=True, key="rn_how")
        col1, col2 = st.columns(2)
        tier = col1.selectbox("Tier", TIERS, index=1, key="rn_tier")
        limit = col2.slider("How many sponsors (lowest lead attainment first)", 1, 5, 2, key="rn_limit")
        chosen = st.multiselect("Sponsors", names, max_selections=5, key="rn_sponsors",
                                help="Used when 'Named sponsors' is selected.")
        st.caption("Each pack takes about a minute. You approve every pack before it is saved; nothing is sent.")
        submitted = st.form_submit_button("Generate renewal packs", type="primary")
    if not submitted:
        return
    if how == "Named sponsors" and not chosen:
        st.warning("Pick at least one sponsor, or prepare packs for a tier.")
        return
    payload = {**scope_request(), "limit": limit,
               **({"sponsors": chosen} if how == "Named sponsors" else {"tier": tier})}
    try:
        run = call(client.renewal_start, payload, reraise=True)
    except client.ApiError as exc:
        record_usage(exc.usage)
        st.error(exc.message)
        return
    if run is not None:
        st.session_state[RUN_KEY] = run["run_id"]
        st.rerun()


def _earlier_runs() -> None:
    try:
        runs = client.renewal_runs()
    except client.ApiError:
        return
    if not runs:
        return
    with st.expander(f"Earlier runs ({len(runs)})"):
        labels = {r["run_id"]: f"{r['request']} · {r['packs']} pack(s)"
                  f"{' · ' + str(r['pending']) + ' waiting' if r['pending'] else ''}"
                  f"{'' if r['done'] or r['pending'] else ' · stopped'}" for r in runs}
        picked = st.selectbox("Open a run", list(labels), format_func=labels.get, key="rn_pick")
        if st.button("Open", key="rn_open"):
            st.session_state[RUN_KEY] = picked
            st.rerun()


# --- following a run ------------------------------------------------------------------------------

def _plan_and_activity(run: dict) -> None:
    left, right = st.columns([2, 3])
    with left:
        st.markdown("**Plan** (the orchestrator's todo list)")
        if not run["todos"]:
            st.caption("Planning...")
        icons = {"completed": ":material/check_circle:", "in_progress": ":material/progress_activity:",
                 "pending": ":material/radio_button_unchecked:"}
        for todo in run["todos"]:
            st.markdown(f"{icons.get(todo['status'], ':material/radio_button_unchecked:')} {todo['content']}")
        for pack in run["packs"]:
            st.markdown(f"{_badge(pack['status'])} {pack['sponsor']}")
    with right:
        steps = f"{len(run['events'])} step{'' if len(run['events']) == 1 else 's'}"
        timing = "" if run["running"] else f", {run['seconds']:.0f} s"
        st.markdown(f"**Agent activity** ({steps}{timing})")
        lines = []
        for event in run["events"][-12:]:
            who = _AGENTS.get(event["agent"], event["agent"])
            step = _STEP.get(event["tool"], event["tool"])
            target = event["detail"] if event["tool"] == "task" else event["sponsor"]
            lines.append(f"- **{who}** {step}{' ' + target if target else ''}")
        st.markdown("\n".join(lines) or "Starting...")


def _checks(pack: dict) -> None:
    if not pack["checks"]:
        return
    failed = [c for c in pack["checks"] if not c["ok"]]
    title = ("All checks passed" if not failed else f"{len(failed)} check(s) failed") + \
        f" · attempt {pack['attempts']}"
    with st.expander(title, expanded=bool(failed), icon=":material/fact_check:"):
        for check in pack["checks"]:
            mark = ":green[:material/check:]" if check["ok"] else ":red[:material/close:]"
            st.markdown(f"{mark} {check['label']}" + (f" — {check['detail']}" if check["detail"] else ""))


def _review_panel(run: dict) -> None:
    pending = [p for p in run["packs"] if p["sponsor"] in run["pending"]]
    st.subheader("Waiting for your decision", anchor=False)
    st.caption("Approve a pack, approve your edited version, or reject it with feedback for the writer. "
               "Approval saves the pack; nothing is sent to the sponsor.")
    decisions, problems = {}, []
    for pack in pending:
        key = _slug(pack["sponsor"])
        with st.container(border=True):
            st.markdown(f"### {pack['sponsor']} {_badge(pack['status'])}")
            _checks(pack)
            choice = st.radio("Decision", ["Approve", "Approve with my edits", "Reject with feedback"],
                              horizontal=True, key=f"rn_choice_{key}")
            if choice == "Approve with my edits":
                edited = st.text_area("Edit the pack (Markdown)", pack["draft"], height=420, key=f"rn_edit_{key}")
                decisions[pack["sponsor"]] = {"type": "edit", "markdown": edited}
            else:
                st.markdown(pack["draft"])
                if choice == "Reject with feedback":
                    feedback = st.text_area("What should change?", key=f"rn_feedback_{key}",
                                            placeholder="e.g. Keep the Premier Booth and add the keynote slot.")
                    if not feedback.strip():
                        problems.append(pack["sponsor"])
                    decisions[pack["sponsor"]] = {"type": "reject", "feedback": feedback}
                else:
                    decisions[pack["sponsor"]] = {"type": "approve"}
            if pack["memory"]:
                with st.expander("What we remember about this sponsor", icon=":material/history:"):
                    st.markdown(pack["memory"])
    if st.button(f"Submit {len(decisions)} decision(s)", type="primary", key="rn_submit"):
        if problems:
            st.warning(f"Say what should change for {', '.join(problems)}, so the writer can fix it.")
            return
        try:
            client.renewal_decide(run["run_id"], decisions)
        except client.ApiError as exc:
            st.error(exc.message)
            return
        st.rerun()


def _results(run: dict) -> None:
    st.subheader("Renewal packs", anchor=False)
    for pack in run["packs"]:
        with st.container(border=True):
            st.markdown(f"### {pack['sponsor']} {_badge(pack['status'])}")
            text = pack["final"] or pack["draft"]
            if pack["status"] == "approved":
                st.download_button("Download pack (.md)", pack["final"], file_name=f"{_slug(pack['sponsor'])}"
                                   "-renewal-pack.md", mime="text/markdown", key=f"rn_dl_{_slug(pack['sponsor'])}",
                                   icon=":material/download:")
            elif text:
                st.caption("Not approved: this is the last draft.")
            _checks(pack)
            if text:
                with st.expander("Pack", expanded=pack["status"] == "approved"):
                    st.markdown(text)
            for note in pack["feedback"]:
                st.caption(f"Feedback: {note}")
            if pack["memory"]:
                with st.expander("What we remember about this sponsor", icon=":material/history:"):
                    st.markdown(pack["memory"])


def _show_run(run: dict) -> None:
    st.markdown(f"**Request:** {run['request']}  \n**Model:** `{run['model']}` · run `{run['run_id'][:8]}`")
    if run["error"]:
        st.error(run["error"])
        if run["can_retry"] and st.button("Retry from the last step", key="rn_retry"):
            try:
                client.renewal_retry(run["run_id"])
            except client.ApiError as exc:
                st.error(exc.message)
                return
            st.rerun()
    _plan_and_activity(run)
    if run["pending"]:
        _review_panel(run)
    elif run["done"]:
        _results(run)


@st.fragment(run_every=POLL_SECONDS)
def _poll(run_id: str) -> None:
    """Refreshes while the agents work; once the run pauses or ends, the whole page reruns to show it."""
    try:
        run = client.renewal_status(run_id)
    except client.ApiError as exc:
        st.error(exc.message)
        return
    _record_new_usage(run)
    if not run["running"]:
        st.rerun()
    st.info("The agents are working. This updates every few seconds; you can leave and come back.",
            icon=":material/progress_activity:")
    _plan_and_activity(run)


def render() -> None:
    page_header("Sponsor renewals", "A team of AI agents drafts a renewal pack per sponsor from its results, "
                "deck and next year's pricing; you approve each one.", icon=":material/assignment_turned_in:")
    if call(lambda ds: ds) is None:  # the data could not be loaded; the problem is shown
        return
    run_id = st.session_state.get(RUN_KEY)
    if run_id is None:  # the sponsors to choose from, in the current selection
        data = page_data("ai-insights", kind="sponsor", candidates=sponsor_candidates("rn_sponsor"))
        if data is None:
            return
        if data["empty"]:
            show_empty_state("sponsors")
            return
    if not client.status()["llm_configured"]:
        ai_unavailable_notice("Preparing renewal packs", "the Sponsor report and AI insights pages, which build "
                              "sponsor summaries from the same figures, and every dashboard.")
        return
    if run_id is None:
        _start_form(data)
        _earlier_runs()
        return
    if st.button("Start a new run", icon=":material/add:", key="rn_new"):
        st.session_state.pop(RUN_KEY, None)
        st.rerun()
    try:
        run = client.renewal_status(run_id)
    except client.ApiError as exc:
        st.error(exc.message)
        st.session_state.pop(RUN_KEY, None)
        return
    _record_new_usage(run)
    if run["running"]:
        st.markdown(f"**Request:** {run['request']}")
        _poll(run_id)
    else:
        _show_run(run)
