"""Sponsor Renewal Desk (Week 3): start a run, poll it, and send a person's decisions back to it.

A run takes minutes, so every step (start, resume after a decision, retry) runs in a background
thread and the page polls ``status``. Runs, checkpoints and per-sponsor memory are in one SQLite
file, so a run paused for approval survives an API restart: the first request for it after a
restart rebuilds its tools from the dataset and filters it was started with.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

from backend.services import documents
from backend.services.selection import scope_text, scoped
from backend.store import StoredDataset
from core.ai.llm import LLMNotConfiguredError, UsageRecorder, build_chat_model, missing_key_message
from core.ai.renewal_desk.runner import DEFAULT_DB, RUNS_NAMESPACE, RenewalRun, open_storage
from core.ai.renewal_desk.tools import RenewalWorkspace
from core.config import get_settings
from shared.models import Filters, RenewalDecisionRequest, RenewalStartRequest

MAX_OUTPUT_TOKENS = 4_000   # a pack is about 600 tokens; specialists also write notes files
TIMEOUT_S = 120.0           # per model request
META_NAMESPACE = ("renewal_meta",)   # dataset, filters and date each run was started with
RECENT_RUNS = 10


class RunBusyError(RuntimeError):
    """A step of this run is still in progress."""


@dataclass
class _Live:
    run: RenewalRun
    recorder: UsageRecorder
    model: str
    thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


_runs: dict[str, _Live] = {}
_registry_lock = threading.Lock()
_storage = None


def storage():
    """(checkpointer, store), opened once per process."""
    global _storage
    with _registry_lock:
        if _storage is None:
            _storage = open_storage(DEFAULT_DB)
        return _storage


def request_text(req: RenewalStartRequest) -> str:
    if req.sponsors:
        names = ", ".join(req.sponsors)
        return f"Prepare renewal packs for these sponsors: {names}."
    who = "sponsor" if req.limit == 1 else f"{req.limit} sponsors"
    group = f"{req.tier} {who}" if req.tier else who
    packs = "a renewal pack" if req.limit == 1 else "renewal packs"
    return f"Prepare {packs} for the {group} with the lowest lead attainment."


def _build(ds: StoredDataset, req, recorder: UsageRecorder, max_sponsors: int | None = None,
           allowed: list[str] | None = None) -> tuple[RenewalWorkspace, object, str]:
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMNotConfiguredError(missing_key_message(settings))
    state, tables = scoped(ds, req)
    docs, _ = documents.document_search(ds, tables)
    workspace = RenewalWorkspace(tables, history=ds.tables, scope=scope_text(state, ds.tables, req.today, default="all events on record"),
                                 documents=docs, max_sponsors=max_sponsors, allowed=allowed)
    model = build_chat_model(replace(settings, llm_max_output_tokens=MAX_OUTPUT_TOKENS), "Renewal desk",
                             recorder=recorder, timeout_s=TIMEOUT_S)
    return workspace, model, f"{settings.llm_provider}:{settings.llm_model}"


def _launch(live: _Live, step, *args) -> None:
    """Runs one step of the run in a background thread (the run reports its own errors)."""
    if live.running:
        raise RunBusyError("This run is still working on its last step.")

    def work():
        with live.lock:
            step(*args)

    live.thread = threading.Thread(target=work, name=f"renewal-{live.run.thread_id[:8]}", daemon=True)
    live.thread.start()


def start(ds: StoredDataset, req: RenewalStartRequest) -> dict:
    recorder = UsageRecorder()
    max_sponsors = len(req.sponsors) or req.limit   # the person's choice, enforced by the tools
    workspace, model, model_id = _build(ds, req, recorder, max_sponsors, req.sponsors or None)
    checkpointer, store = storage()
    run = RenewalRun(workspace, model, checkpointer, store)
    run.request = request_text(req)
    store.put(META_NAMESPACE, run.thread_id, {"dataset_id": ds.id, "filters": req.filters.model_dump(mode="json"),
                                              "today": req.today.isoformat(), "model": model_id,
                                              "max_sponsors": max_sponsors, "allowed": req.sponsors or None})
    live = _Live(run, recorder, model_id)
    with _registry_lock:
        _runs[run.thread_id] = live
    _launch(live, run.start, run.request)
    return status(run.thread_id)


def _live(run_id: str, get_dataset) -> _Live:
    """The run in memory, or rebuilt from the database (after an API restart)."""
    with _registry_lock:
        if run_id in _runs:
            return _runs[run_id]
    checkpointer, store = storage()
    meta = store.get(META_NAMESPACE, run_id)
    if meta is None or store.get(RUNS_NAMESPACE, run_id) is None:
        raise KeyError(run_id)
    from datetime import date

    from shared.models import ScopeRequest

    req = ScopeRequest(filters=Filters(**meta.value["filters"]), today=date.fromisoformat(meta.value["today"]))
    recorder = UsageRecorder()
    workspace, model, model_id = _build(get_dataset(meta.value["dataset_id"]), req, recorder,
                                        meta.value.get("max_sponsors"), meta.value.get("allowed"))
    live = _Live(RenewalRun.load(run_id, workspace, model, checkpointer, store), recorder, model_id)
    with _registry_lock:
        return _runs.setdefault(run_id, live)


def status(run_id: str, get_dataset=None) -> dict:
    live = _live(run_id, get_dataset)
    running = live.running
    snapshot = live.run.status()
    usage = [c.to_json() for c in live.recorder.calls]
    return {**snapshot, "running": running, "done": snapshot["done"] and not running,
            "pending": [] if running else snapshot["pending"], "model": live.model, "usage": usage}


def decide(run_id: str, req: RenewalDecisionRequest, get_dataset) -> dict:
    live = _live(run_id, get_dataset)
    if live.running:
        raise RunBusyError("This run is still working on its last step.")
    if not live.run.pending:
        raise ValueError("This run is not waiting for approval.")
    waiting = {r["sponsor"] for r in live.run.pending}
    if unknown := set(req.decisions) - waiting:
        raise ValueError(f"Not waiting for approval: {', '.join(sorted(unknown))}.")
    if missing := waiting - set(req.decisions):  # nothing is approved by default
        raise ValueError(f"Decide on every pack that is waiting; missing: {', '.join(sorted(missing))}.")
    for sponsor, decision in req.decisions.items():
        if decision.type == "edit" and not (decision.markdown or "").strip():
            raise ValueError(f"The edit for {sponsor} is empty.")
        if decision.type == "reject" and not (decision.feedback or "").strip():
            raise ValueError(f"Say why the pack for {sponsor} is rejected, so the writer can fix it.")
    decisions = {s: d.model_dump(exclude_none=True) for s, d in req.decisions.items()}
    _launch(live, live.run.decide, decisions)
    return status(run_id)


def retry(run_id: str, get_dataset) -> dict:
    live = _live(run_id, get_dataset)
    if not live.run.can_retry:
        raise ValueError("This run did not stop on a provider failure, so there is nothing to retry.")
    _launch(live, live.run.retry)
    return status(run_id)


def recent() -> list[dict]:
    _, store = storage()
    items = sorted(store.search(RUNS_NAMESPACE, limit=100), key=lambda i: i.updated_at, reverse=True)
    out = []
    for item in items[:RECENT_RUNS]:
        packs = item.value.get("workspace", {}).get("packs", [])
        out.append({"run_id": item.key, "request": item.value.get("request", ""), "done": item.value.get("done", False),
                    "pending": sum(p.get("status") == "awaiting_approval" for p in packs), "packs": len(packs)})
    return out
