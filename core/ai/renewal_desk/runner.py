"""Runs the Renewal Desk step by step: start, pause for approval, resume with a person's decisions.

State lives in one SQLite file (``open_storage``):
- the LangGraph checkpoints, so a paused run survives a restart and resumes on the same thread;
- the store, which holds ``/memories/`` (one note per sponsor, read by the analyst next time)
  and a snapshot of each run's packs, so the tools can be rebuilt after a restart.

Decisions are keyed by sponsor: ``{"Acme": {"type": "approve"}}``, ``{"type": "edit", "markdown": ...}``
or ``{"type": "reject", "feedback": ...}``. Rejected packs go back to the writer with the feedback
(the orchestrator's prompt says so); every outcome is added to the sponsor's memory note by code,
so memory never depends on the model remembering to write it.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from datetime import date
from pathlib import Path

from core.ai.renewal_desk.agent import APPROVAL_TOOL, MODEL_RETRIES, RECURSION_LIMIT, build_renewal_agent
from core.ai.renewal_desk.tools import BudgetExceeded, RenewalWorkspace
from core.config import PROJECT_ROOT

DEFAULT_DB = PROJECT_ROOT / "data" / "renewals" / "renewal_desk.sqlite"   # git-ignored
MEMORY_NAMESPACE = ("renewal_desk",)     # the /memories/ route (see agent._backend)
RUNS_NAMESPACE = ("renewal_runs",)       # one snapshot per thread id
_BOLD = re.compile(r"\*\*(.+?)\*\*")
RETRYABLE = {"timeout", "rate_limit", "overloaded", "network"}   # may work if tried again later
_FIX = {"auth": "Check the API key in the backend .env, then start a new run.",
        "credits": "Add credit with the provider, then start a new run.",
        "not_found": "Check LLM_MODEL in the backend .env, then start a new run.",
        "bad_request": "The provider refused the request; try another model."}


def open_storage(db_path: Path | str):
    """(checkpointer, store) on one SQLite file, created if missing. Safe to share across threads."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.store.sqlite import SqliteStore

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    checkpointer = SqliteSaver(sqlite3.connect(str(db_path), check_same_thread=False))
    store = SqliteStore(sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None))
    checkpointer.setup()
    store.setup()
    return checkpointer, store


def memory_path(sponsor: str) -> str:
    return f"/sponsors/{sponsor}.md"   # inside the /memories/ route, so the agent sees /memories/sponsors/<name>.md


def read_memory(store, sponsor: str) -> str:
    item = store.get(MEMORY_NAMESPACE, memory_path(sponsor))
    if item is None:
        return ""
    content = item.value.get("content", "")
    return "\n".join(content) if isinstance(content, list) else str(content)


def add_memory(store, sponsor: str, line: str) -> None:
    """Appends one dated line to the sponsor's note, in the format the deepagents StoreBackend reads."""
    old = read_memory(store, sponsor) or f"# {sponsor}: renewal history\n"
    content = old.rstrip("\n") + f"\n- {date.today():%Y-%m-%d}: {line}\n"
    store.put(MEMORY_NAMESPACE, memory_path(sponsor), {"content": content, "encoding": "utf-8"})


def recommended_package(markdown: str) -> str:
    section = re.search(r"^## Recommended package\s*$(.*?)(?=^## |\Z)", markdown, flags=re.M | re.S)
    names = _BOLD.findall(section.group(1)) if section else []
    return names[0] if names else "no package named"


class RenewalRun:
    """One request, on one thread. ``status()`` is what the API and the page show."""

    def __init__(self, workspace: RenewalWorkspace, model, checkpointer, store, thread_id: str | None = None,
                 request: str = ""):
        self.workspace, self.store = workspace, store
        self.thread_id = thread_id or uuid.uuid4().hex
        self.request = request
        self.agent = build_renewal_agent(workspace, model, checkpointer=checkpointer, store=store)
        self.config = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": RECURSION_LIMIT}
        self.pending: list[dict] = []   # approval requests: {"sponsor", "args"}, in the graph's order
        self.done = False
        self.error = ""
        self.can_retry = False          # a provider failure: retry() continues from the last checkpoint
        self.seconds = 0.0
        self.events: list[dict] = []    # who called which tool, in order (for the page's activity log)

    # -- lifecycle --
    def start(self, request: str) -> dict:
        self.request = request
        return self._advance({"messages": [{"role": "user", "content": request}]})

    def decide(self, decisions: dict[str, dict]) -> dict:
        """Resumes with one decision per pending sponsor (a missing one counts as approve)."""
        from langgraph.types import Command

        if not self.pending:
            raise ValueError("This run is not waiting for approval.")
        answers = []
        for request in self.pending:
            sponsor = request["sponsor"]
            decision = decisions.get(sponsor, {"type": "approve"})
            kind = decision.get("type", "approve")
            if kind == "edit":
                answers.append({"type": "edit", "edited_action": {
                    "name": APPROVAL_TOOL, "args": {"sponsor": sponsor, "markdown": decision["markdown"]}}})
            elif kind == "reject":
                feedback = (decision.get("feedback") or "").strip() or "Rejected without feedback."
                answers.append({"type": "reject", "message": f"The sponsorship manager rejected the pack for "
                                f"{sponsor}. Feedback: {feedback} Redraft it with the writer, review it, and "
                                "send it for approval again."})
                self.workspace.set_status(sponsor, "rejected")
                if (pack := self.workspace._pack(sponsor)) is not None:
                    pack.feedback.append(f"Manager: {feedback}")
                add_memory(self.store, sponsor, f"pack rejected ({recommended_package(self._draft(sponsor))}). "
                                                f"Feedback: {feedback}")
            else:
                answers.append({"type": "approve"})
        decided = {r["sponsor"]: d for r, d in zip(self.pending, answers)}
        self.pending = []
        self._advance(Command(resume={"decisions": answers}))
        for sponsor, answer in decided.items():
            pack = self.workspace._pack(sponsor)
            if answer["type"] in ("approve", "edit") and pack is not None and pack.status == "approved":
                how = "approved with edits" if answer["type"] == "edit" else "approved"
                add_memory(self.store, sponsor, f"pack {how}, recommending {recommended_package(pack.final)}.")
        return self.status()

    def retry(self) -> dict:
        """Continues a run that stopped on a provider failure, from its last checkpoint."""
        if not self.can_retry:
            raise ValueError("This run did not stop on a provider failure, so there is nothing to retry.")
        self.error, self.can_retry = "", False
        return self._advance(None)

    @classmethod
    def load(cls, thread_id: str, workspace: RenewalWorkspace, model, checkpointer, store) -> "RenewalRun":
        """A run rebuilt from its checkpoint and snapshot, for example after the API restarted."""
        saved = store.get(RUNS_NAMESPACE, thread_id)
        if saved is None:
            raise KeyError(f"No renewal run {thread_id}")
        workspace.restore(saved.value["workspace"])
        run = cls(workspace, model, checkpointer, store, thread_id=thread_id, request=saved.value["request"])
        run.done, run.error, run.seconds = saved.value["done"], saved.value["error"], saved.value["seconds"]
        run.can_retry = saved.value.get("can_retry", False)
        run.events = saved.value.get("events", [])
        run.pending = run._interrupts_from_state()
        return run

    # -- internals --
    def _draft(self, sponsor: str) -> str:
        pack = self.workspace._pack(sponsor)
        return pack.draft if pack else ""

    def _advance(self, payload) -> dict:
        from langgraph.errors import GraphRecursionError

        started = time.perf_counter()
        interrupts = []
        try:
            for namespace, update in self.agent.stream(payload, self.config, stream_mode="updates", subgraphs=True):
                interrupts += self._record(namespace, update)
        except BudgetExceeded as exc:
            self.error = str(exc)
        except GraphRecursionError:
            self.error = f"The run stopped after {RECURSION_LIMIT} steps; the packs so far are kept."
        except Exception as exc:  # noqa: BLE001 - a provider failure is reported on the run, not raised
            from core.ai.llm import classify_error
            kind = classify_error(exc)
            self.can_retry = kind in RETRYABLE
            self.error = (f"The run stopped ({kind}) after {MODEL_RETRIES} retries: {exc}" if self.can_retry else
                          f"The run stopped ({kind}): {exc}. {_FIX.get(kind, 'Check the backend log.')}")
        self.seconds += time.perf_counter() - started
        self.pending = [] if self.error else self._requests(interrupts)
        for request in self.pending:
            self.workspace.set_status(request["sponsor"], "awaiting_approval")
        self.done = not self.pending and not self.can_retry
        if self.done:
            for pack in self.workspace.packs.values():
                if pack.status not in ("approved", "needs_edit", "failed") and (self.error or not pack.draft):
                    pack.status = "failed"  # always with the reason, so the page can say why
                    pack.feedback.append(f"Not prepared: {self.error}" if self.error else
                                         "Not prepared: the run ended before a draft was written for this sponsor.")
        self._save()
        return self.status()

    def _record(self, namespace, update) -> list:
        """Logs each tool call with the agent that made it. A specialist's calls are named after the last
        delegation (``task``), which is exact while the orchestrator delegates one task at a time."""
        who = "orchestrator" if not namespace else getattr(self, "_delegate", "specialist")
        found = []
        for node, value in (update or {}).items():
            if node == "__interrupt__":
                found.extend(value)
                continue
            messages = value.get("messages", []) if isinstance(value, dict) else []
            for message in messages or []:
                for call in getattr(message, "tool_calls", None) or []:
                    args = call.get("args", {})
                    if call["name"] == "task" and not namespace:
                        self._delegate = args.get("subagent_type") or "specialist"
                    if call.get("id") and any(e.get("call_id") == call["id"] for e in self.events[-20:]):
                        continue  # a resume replays the paused call with the same id
                    self.events.append({"agent": who, "tool": call["name"], "sponsor": args.get("sponsor", ""),
                                        "detail": str(args.get("subagent_type") or args.get("query") or "")[:120],
                                        "call_id": call.get("id") or ""})
        return found

    @staticmethod
    def _requests(interrupts) -> list[dict]:
        return [{"sponsor": r["args"].get("sponsor", ""), "args": r["args"]}
                for i in interrupts for r in (i.value or {}).get("action_requests", [])
                if r.get("name") == APPROVAL_TOOL]

    def _interrupts_from_state(self) -> list[dict]:
        state = self.agent.get_state(self.config)
        return self._requests(list(getattr(state, "interrupts", ()) or ()))

    def todos(self) -> list[dict]:
        values = self.agent.get_state(self.config).values or {}
        return [{"content": t.get("content", ""), "status": t.get("status", "")} for t in values.get("todos", [])]

    def _save(self) -> None:
        self.store.put(RUNS_NAMESPACE, self.thread_id, {
            "request": self.request, "workspace": self.workspace.snapshot(), "done": self.done,
            "error": self.error, "can_retry": self.can_retry, "seconds": round(self.seconds, 1),
            "events": self.events})

    def status(self) -> dict:
        packs = []
        for p in list(self.workspace.packs.values()):  # a copy: a background step may be adding packs
            packs.append({"sponsor": p.name, "status": p.status, "attempts": p.attempts, "draft": p.draft,
                          "final": p.final, "feedback": list(p.feedback),
                          "checks": [c.model_dump() for c in p.review.checks] if p.review else [],
                          "passed": bool(p.review and p.review.passed),
                          "memory": read_memory(self.store, p.name)})
        return {"run_id": self.thread_id, "request": self.request, "done": self.done, "error": self.error,
                "can_retry": self.can_retry,
                "seconds": round(self.seconds, 1), "todos": self.todos(), "packs": packs,
                "pending": [r["sponsor"] for r in self.pending], "tool_calls": dict(self.workspace.calls),
                "events": [{k: v for k, v in e.items() if k != "call_id"} for e in self.events]}
