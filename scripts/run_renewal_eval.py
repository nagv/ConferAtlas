"""Evaluate the Sponsor Renewal Desk on the golden sponsors (data/synthetic/eval/renewal_golden.json).

    python scripts/run_renewal_eval.py                    # all six sponsors, judge on
    python scripts/run_renewal_eval.py --sponsors 2 --no-judge
    python scripts/run_renewal_eval.py --no-langsmith     # local only, even when LangSmith is configured

Each sponsor runs end to end on the configured model (the same desk the app uses, with every approval
given automatically), in a fresh database so no memory carries over. Results go to
docs/week3/eval_results.json and .md, and each approved pack to docs/week3/samples/. With LangSmith
configured, the run is also recorded as an experiment on a "conferatlas-renewal-golden" dataset.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_settings, tracing_enabled  # noqa: E402

OUT_DIR = PROJECT_ROOT / "docs" / "week3"
DEFAULT_JUDGE_MODEL = "zai-org/GLM-5.3"   # a different model family from the desk's model (as in Week 2)
MAX_OUTPUT_TOKENS = 4_000
TIMEOUT_S = 120.0
JUDGE_MAX_TOKENS = 8_000
JUDGE_TIMEOUT_S = 180.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sponsors", type=int, default=None, help="Only the first N golden sponsors")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Sponsors run at once (1 is safest: the keyword index is built on first use)")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--no-langsmith", action="store_true")
    args = parser.parse_args()

    from backend.services.documents import APP_CHUNKING, retriever
    from core.ai.llm import UsageRecorder, build_chat_model, structured_output_method
    from core.ai.renewal_desk import evaluate as ev
    from core.ai.renewal_desk.runner import open_storage
    from core.ai.skills import load_skill
    from core.analysis import filter_dataset
    from core.rag.agent_tools import DocumentSearch
    from core.rag.retrieve import RetrievalConfig
    from core.schema import load_dataset

    settings = get_settings()
    if not settings.llm_configured:
        print(f"The desk's model needs {settings.llm_provider} configured (key and base URL).", file=sys.stderr)
        return 1
    golden = ev.load_golden(settings.data_dir)[: args.sponsors or None]
    tables = load_dataset(settings.data_dir)
    shared_retriever = retriever(settings)
    db = Path(tempfile.mkdtemp(prefix="renewal-eval-")) / "desk.sqlite"   # fresh: no memory from earlier runs
    checkpointer, store = open_storage(db)
    desk_settings = replace(settings, llm_max_output_tokens=MAX_OUTPUT_TOKENS)
    judge_id = os.getenv("EVAL_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
    grader = None
    if not args.no_judge:
        # A reasoning judge thinks before it answers, and a long pack plus evidence needs room: 2,000 tokens
        # cut it off before the grade (seen on the first run), so it gets 8,000 and three minutes.
        judge_settings = replace(settings, llm_model=judge_id, llm_max_output_tokens=JUDGE_MAX_TOKENS)
        grader = build_chat_model(judge_settings, "Renewal eval judge", timeout_s=JUDGE_TIMEOUT_S).with_structured_output(
            ev.PackGrade, method=structured_output_method(judge_settings), include_raw=True)
    use_langsmith = tracing_enabled() and not args.no_langsmith
    print(f"Desk model {settings.llm_provider}:{settings.llm_model} · judge {judge_id if grader else 'off'} · "
          f"LangSmith {'on' if use_langsmith else 'off'} · {len(golden)} sponsors\n")

    results: dict[str, tuple[dict, dict]] = {}

    def target(inputs: dict) -> dict:
        """One golden sponsor, end to end: the LangSmith target (or called directly when it is off)."""
        g = next(x for x in golden if x["id"] == inputs["id"])
        recorder = UsageRecorder()
        model = build_chat_model(desk_settings, "Renewal desk", recorder=recorder, timeout_s=TIMEOUT_S)
        docs = DocumentSearch(shared_retriever, RetrievalConfig(chunking=APP_CHUNKING))
        result = ev.run_sponsor(g["sponsor"], filter_dataset(tables), tables, docs, model, checkpointer, store)
        results[g["id"]] = (g, result)
        metrics = ev.pack_metrics(g, result)
        judged = ev.judge_pack(grader, g, result) if grader else {}
        pack = result["pack"]
        row = {**metrics, **judged, "seconds": round(result["seconds"], 1),
               "tool_calls": sum(result["workspace"].calls.values()), "model_calls": len(recorder.calls),
               "status": pack.status if pack else "missing", "attempts": pack.attempts if pack else 0,
               "error": result["status"]["error"], "pack": (pack.final or pack.draft) if pack else ""}
        print(f"  {g['id']} {g['sponsor']:<22} {row['status']:<10} {row['seconds']:>5.0f} s  "
              f"figures {row['figure_accuracy']}  move {row['move']} ({row['recommended']})"
              + (f"  judge {row.get('usefulness')}/{row.get('grounding')}/{row.get('tone')}" if grader else ""),
              flush=True)
        return row

    started = time.perf_counter()
    rows = _run_langsmith(golden, target, args.concurrency) if use_langsmith else \
        _run_local(golden, target, args.concurrency)
    catch = ev.catch_rate(list(results.values()), load_skill("sponsor-renewal-pack"))
    summary = ev.summarize(rows)
    meta = {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "desk_model": f"{settings.llm_provider}:{settings.llm_model}", "judge_model": judge_id if grader else None,
            "sponsors": len(golden), "wall_seconds": round(time.perf_counter() - started),
            "langsmith_dataset": _DATASET if use_langsmith else None}
    _write(rows, summary, catch, meta)
    print(f"\n{json.dumps(summary)}\nReviewer catch rate: {catch['catch_rate']} on {catch['broken_packs']} broken "
          f"packs {catch['per_kind']}\nWrote docs/week3/eval_results.md and samples/")
    return 0


def _run_local(golden, target, concurrency: int) -> list[dict]:
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        outs = list(pool.map(lambda g: target({"id": g["id"], "sponsor": g["sponsor"]}), golden))
    return [{"id": g["id"], "sponsor": g["sponsor"], **o} for g, o in zip(golden, outs)]


_DATASET = "conferatlas-renewal-golden"


def _run_langsmith(golden, target, concurrency: int) -> list[dict]:
    global _DATASET
    from langsmith import Client

    client = Client()
    name = f"{_DATASET}-{len(golden)}"
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(name, description="ConferAtlas Week 3 Renewal Desk golden sponsors "
                                        "(synthetic demo data), from data/synthetic/eval/renewal_golden.json")
        for g in golden:
            client.create_example(inputs={"id": g["id"], "sponsor": g["sponsor"]},
                                  outputs={"allowed_moves": g["allowed_moves"], "has_deck": g["has_deck"],
                                           "why": g["why"]}, dataset_id=dataset.id)

    def scores(inputs, outputs, reference_outputs) -> dict:
        from core.ai.renewal_desk.evaluate import CODE_METRICS, JUDGE_METRICS
        return {"results": [{"key": k, "score": outputs.get(k)} for k in CODE_METRICS + JUDGE_METRICS
                            if outputs.get(k) is not None]}

    results = client.evaluate(target, data=name, evaluators=[scores], max_concurrency=concurrency,
                              experiment_prefix="renewal-desk", metadata={"kind": "renewal_desk"}, blocking=True)
    rows = [{"id": item["example"].inputs["id"], "sponsor": item["example"].inputs["sponsor"],
             **(item["run"].outputs or {})} for item in results]
    _DATASET = name
    return sorted(rows, key=lambda r: r["id"])


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _write(rows: list[dict], summary: dict, catch: dict, meta: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    samples = OUT_DIR / "samples"
    samples.mkdir(exist_ok=True)
    for row in rows:
        if row.get("status") == "approved" and row.get("pack"):
            slug = re.sub(r"[^a-z0-9]+", "-", row["sponsor"].lower()).strip("-")
            (samples / f"{slug}.md").write_text(row["pack"], encoding="utf-8")
    saved = {"meta": meta, "summary": summary, "reviewer": catch,
             "per_sponsor": [{k: v for k, v in r.items() if k != "pack"} for r in rows]}
    (OUT_DIR / "eval_results.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")

    lines = ["# Week 3 evaluation results: Sponsor Renewal Desk", "",
             f"Generated {meta['generated_at']} on {meta['sponsors']} golden sponsors "
             f"(`data/synthetic/eval/renewal_golden.json`). Desk model: `{meta['desk_model']}`; judge: "
             f"`{meta['judge_model'] or 'off'}`." + (f" LangSmith dataset: `{meta['langsmith_dataset']}`."
                                                     if meta["langsmith_dataset"] else ""), "",
             "## Summary", "", "| Metric | Result |", "|---|---|"]
    labels = [("completed", "Packs completed (approved)"), ("first_pass", "Checklist passed on the first draft"),
              ("final_pass", "Checklist passed on the final draft"), ("figure_accuracy", "Figure accuracy"),
              ("package_valid", "Recommended package exists"), ("price_correct", "2026 price quoted correctly"),
              ("low_events_named", "Events below 80% named"), ("deck_cited", "Deck cited (sponsors with a deck)"),
              ("sensible_move", "Recommendation in the sensible range"), ("usefulness", "Judge: usefulness"),
              ("grounding", "Judge: grounding"), ("tone", "Judge: tone")]
    lines += [f"| {label} | {_pct(summary.get(key))} |" for key, label in labels]
    lines += [f"| Reviewer catch rate (broken packs rejected) | {_pct(catch['catch_rate'])} of "
              f"{catch['broken_packs']} |",
              f"| Median time per pack | {summary['median_seconds']} s |",
              f"| Mean tool calls / model calls per pack | {summary['mean_tool_calls']} / "
              f"{summary['mean_model_calls']} |", "",
              "## Per sponsor", "",
              "| Sponsor | Status | Draft attempts | Figures | Recommended | Move | Checklist | Judge (use/ground/tone) "
              "| Time |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        judge = "/".join("–" if r.get(k) is None else f"{r[k]:.2f}" for k in ("usefulness", "grounding", "tone"))
        lines.append(f"| {r['sponsor']} | {r.get('status')} | {r.get('attempts')} | {_pct(r.get('figure_accuracy'))} | "
                     f"{r.get('recommended') or '–'} | {r.get('move')} | {'pass' if r.get('final_pass') else 'fail'} | "
                     f"{judge} | {r.get('seconds')} s |")
    lines += ["", "## Reviewer catch rate by fault", "", "| Fault | Caught |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {_pct(v)} |" for k, v in catch["per_kind"].items()]
    notes = [r for r in rows if r.get("unverified") or r.get("error") or r.get("judge_reason")]
    if notes:
        lines += ["", "## Notes", ""]
        for r in notes:
            if r.get("unverified"):
                lines.append(f"- {r['sponsor']}: numbers not traced: {', '.join(r['unverified'])}")
            if r.get("error"):
                lines.append(f"- {r['sponsor']}: run error: {r['error']}")
            if r.get("judge_reason"):
                lines.append(f"- {r['sponsor']} (judge): {r['judge_reason']}")
    lines += ["", "Every approval was given automatically, so the scores measure the agents and the reviewer, "
              "not a person's edits. Generated by `scripts/run_renewal_eval.py`.", ""]
    (OUT_DIR / "eval_results.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
