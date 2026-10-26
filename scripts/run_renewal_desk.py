"""Run the Sponsor Renewal Desk from the terminal on the built-in data.

    python scripts/run_renewal_desk.py --sponsor "Orchidia Cloud"            # stops at the approval
    python scripts/run_renewal_desk.py --resume <run id> --approve           # later, even after a restart
    python scripts/run_renewal_desk.py --resume <run id> --reject "Offer a smaller package"
    python scripts/run_renewal_desk.py --tier platinum --limit 2 --approve   # approve every pack

Runs, approvals and per-sponsor memory are kept in data/renewals/renewal_desk.sqlite, so a paused
run can be resumed by its id from a new process. It prints who did what, then each pack.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_settings  # noqa: E402

MAX_OUTPUT_TOKENS = 4_000   # a pack is about 600 tokens; specialists also write notes files
TIMEOUT_S = 120.0


def _print(status: dict, shown: int) -> int:
    for event in status["events"][shown:]:
        extra = f" → {event['detail']}" if event["detail"] else ""
        sponsor = f" ({event['sponsor']})" if event["sponsor"] else ""
        print(f"  [{event['agent']}] {event['tool']}{sponsor}{extra}")
    return len(status["events"])


def _summary(status: dict) -> None:
    print(f"\nRun {status['run_id']} · {status['seconds']:.0f} s · tool calls: {status['tool_calls']}")
    if status["error"]:
        print(f"Error: {status['error']}")
    for todo in status["todos"]:
        print(f"  todo [{todo['status']}] {todo['content']}")
    for pack in status["packs"]:
        print(f"\n=== {pack['sponsor']}: {pack['status']} (attempts {pack['attempts']})")
        for check in pack["checks"]:
            print(f"   {'✓' if check['ok'] else '✗'} {check['label']}{' — ' + check['detail'] if check['detail'] else ''}")
        if pack["memory"]:
            print("   memory: " + pack["memory"].strip().replace("\n", "\n           "))
        print(pack["final"] or pack["draft"] or "(no draft)")
    if status["pending"]:
        print(f"\n⏸  Waiting for approval: {', '.join(status['pending'])}")
        print(f"   Resume with: python scripts/run_renewal_desk.py --resume {status['run_id']} --approve "
              f"(or --reject \"feedback\")")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--sponsor", help="One sponsor by name")
    pick.add_argument("--tier", choices=["platinum", "gold", "silver", "bronze"])
    pick.add_argument("--resume", metavar="RUN_ID", help="Continue a paused run")
    parser.add_argument("--limit", type=int, default=2, help="Sponsors per run when --tier is used (max 5)")
    decide = parser.add_mutually_exclusive_group()
    decide.add_argument("--approve", action="store_true", help="Approve every pack that is waiting")
    decide.add_argument("--reject", metavar="FEEDBACK", help="Reject the waiting packs with this feedback")
    decide.add_argument("--retry", action="store_true", help="Continue a run that stopped on a provider error")
    parser.add_argument("--db", default=None, help="SQLite file (default data/renewals/renewal_desk.sqlite)")
    args = parser.parse_args()

    from dataclasses import replace

    from backend.services.documents import APP_CHUNKING, retriever
    from core.ai.llm import build_chat_model
    from core.ai.renewal_desk.runner import DEFAULT_DB, RenewalRun, open_storage
    from core.ai.renewal_desk.tools import RenewalWorkspace
    from core.analysis import filter_dataset
    from core.rag.agent_tools import DocumentSearch
    from core.rag.retrieve import RetrievalConfig
    from core.schema import load_dataset

    settings = get_settings()
    if not settings.llm_configured:
        print(f"The chat model needs {settings.llm_provider} configured (key and base URL).", file=sys.stderr)
        return 1
    tables = load_dataset(settings.data_dir)
    try:
        docs = DocumentSearch(retriever(settings), RetrievalConfig(chunking=APP_CHUNKING))
    except Exception as exc:  # noqa: BLE001 - the desk still runs, it just has no documents
        print(f"Document search is off: {exc}", file=sys.stderr)
        docs = None
    limit = 1 if args.sponsor else min(args.limit, 5)   # hold the run to what was asked, as the app does
    workspace = RenewalWorkspace(filter_dataset(tables), history=tables, documents=docs, max_sponsors=limit,
                                 allowed=[args.sponsor] if args.sponsor else None)
    model = build_chat_model(replace(settings, llm_max_output_tokens=MAX_OUTPUT_TOKENS), "Renewal desk",
                             timeout_s=TIMEOUT_S)
    checkpointer, store = open_storage(args.db or DEFAULT_DB)
    print(f"Model: {settings.llm_provider}:{settings.llm_model}")

    if args.resume:
        run = RenewalRun.load(args.resume, workspace, model, checkpointer, store)
        status = run.status()
        shown = len(status["events"])
        print(f"Resumed run {run.thread_id}: {run.request}")
        if args.retry:
            status = run.retry()
            shown = _print(status, shown)
    else:
        request = (f"Prepare a renewal pack for {args.sponsor}." if args.sponsor else
                   f"Prepare renewal packs for the {min(args.limit, 5)} {args.tier} sponsors with the lowest lead "
                   "attainment.")
        print(f"Request: {request}\n")
        run = RenewalRun(workspace, model, checkpointer, store)
        status, shown = run.start(request), 0
        shown = _print(status, shown)

    while status["pending"] and (args.approve or args.reject):
        if args.reject:
            print(f"\n✗ Rejecting {', '.join(status['pending'])}: {args.reject}\n")
            decisions = {s: {"type": "reject", "feedback": args.reject} for s in status["pending"]}
            args.reject = None  # reject once; the redraft stops for a new decision
        else:
            print(f"\n✓ Approving {', '.join(status['pending'])}\n")
            decisions = {s: {"type": "approve"} for s in status["pending"]}
        status = run.decide(decisions)
        shown = _print(status, shown)
    _summary(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
