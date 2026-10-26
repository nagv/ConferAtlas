"""Evaluate document search and the Ask agent on the 20 golden questions (Week 2, Step 10).

Usage, from the project root:
    python scripts/run_eval.py                       # retrieval and agent experiments
    python scripts/run_eval.py --retrieval-only      # no LLM calls: hit rate, MRR, refusal, latency
    python scripts/run_eval.py --questions 3         # a quick smoke run on the first 3 questions
    python scripts/run_eval.py --agent production    # one agent setting (production, dense, fixed, semantic)
    python scripts/run_eval.py --no-langsmith        # run locally even when LangSmith is configured
    python scripts/run_eval.py --agent fixed --merge  # rerun one setting, keep the other saved results

Answers use the planned production model (Nebius, --answer-model) and a stronger judge (EVAL_JUDGE_MODEL).
With LangSmith configured (LANGSMITH_API_KEY, LANGSMITH_TRACING=true), the golden set is uploaded as a dataset
and every setting becomes an experiment you can compare side by side. Results are saved to
docs/week2/eval_results.json (shown on the RAG Lab Experiments tab) and docs/week2/eval_results.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_settings, tracing_enabled  # noqa: E402
from core.rag import evaluate as ev  # noqa: E402

OUT_JSON = PROJECT_ROOT / "docs" / "week2" / "eval_results.json"
OUT_MD = PROJECT_ROOT / "docs" / "week2" / "eval_results.md"
AGENT_KEYS = {"production": 0, "dense": 1, "fixed": 2, "semantic": 3}
DEFAULT_ANSWER_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
DEFAULT_JUDGE_MODEL = "zai-org/GLM-5.3"   # a different model family from the answers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--questions", type=int, help="only the first N golden questions")
    parser.add_argument("--agent", nargs="*", choices=list(AGENT_KEYS), help="agent settings to run (default all)")
    parser.add_argument("--no-langsmith", action="store_true")
    parser.add_argument("--answer-provider", default="nebius")
    parser.add_argument("--answer-model", default=DEFAULT_ANSWER_MODEL)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--merge", action="store_true",
                        help="replace only the settings run now in the saved results (skips retrieval unless "
                             "--retrieval-only)")
    args = parser.parse_args(argv)

    settings = get_settings()
    from core.rag.embeddings import EmbeddingsNotConfiguredError, build_embeddings
    from core.rag.index import VectorIndex
    from core.rag.loaders import load_corpus
    from core.rag.retrieve import Retriever
    try:
        retriever = Retriever(VectorIndex(settings, build_embeddings(settings)))
    except EmbeddingsNotConfiguredError as exc:
        print(exc, file=sys.stderr)
        return 1
    golden = ev.load_golden(settings.data_dir)[: args.questions or None]
    use_langsmith = tracing_enabled() and not args.no_langsmith
    print(f"{len(golden)} golden questions · embeddings {retriever.index.model} · "
          f"LangSmith {'on (project ' + os.getenv('LANGSMITH_PROJECT', 'default') + ')' if use_langsmith else 'off'}")

    units = None
    for chunking in ("fixed", "structure", "semantic"):  # make sure every compared index exists
        from core.rag.chunking import PRESETS
        if not retriever.index.is_built(PRESETS[chunking]):
            units = units or load_corpus(settings.data_dir)
            print(f"Building the {chunking} index first...")
            retriever.index.build(units, PRESETS[chunking])

    runner = _LangSmithRunner(golden) if use_langsmith else None
    summaries, per_question = [], {}

    retrieval_settings = [] if args.merge and not args.retrieval_only else ev.RETRIEVAL_SETTINGS
    for setting in retrieval_settings:
        target = ev.RetrievalTarget(retriever, setting.config)
        rows = _run(runner, setting, target, golden, ev.RETRIEVAL_EVALUATORS, concurrency=1, kind="retrieval")
        summaries.append(ev.summarize(setting.name, "retrieval", rows))
        per_question[setting.name] = rows
        print(_line(summaries[-1]))

    meta = {"generated_at": datetime.now().isoformat(timespec="seconds"), "golden_questions": len(golden),
            "embedding_model": retriever.index.model}
    if not args.retrieval_only:
        from core.ai.llm import build_chat_model, structured_output_method
        from core.schema import load_dataset
        answer_settings = replace(settings, llm_provider=args.answer_provider, llm_model=args.answer_model)
        judge_model_id = os.getenv("EVAL_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
        judge_settings = replace(answer_settings, llm_model=judge_model_id, llm_max_output_tokens=2000)
        # (2,000: a reasoning judge thinks before answering; a small cap can cut it off before the grade)
        if not answer_settings.llm_configured:
            print(f"The answer model needs {answer_settings.llm_provider} configured (key and base URL).",
                  file=sys.stderr)
            return 1
        judge = ev.judge_evaluator(build_chat_model(judge_settings, "Eval judge"),
                                   structured_output_method(judge_settings))
        tables = load_dataset(settings.data_dir)
        chosen = [ev.AGENT_SETTINGS[AGENT_KEYS[k]] for k in (args.agent or AGENT_KEYS)]
        meta.update(answer_model=f"{args.answer_provider}:{args.answer_model}", judge_model=judge_model_id)
        print(f"Answer model {args.answer_model} · judge {judge_model_id}")
        for setting in chosen:
            target = ev.AgentTarget(retriever, setting.config, tables, answer_settings)
            rows = _run(runner, setting, target, golden, ev.AGENT_EVALUATORS + [judge],
                        concurrency=args.concurrency, kind="agent")
            summaries.append(ev.summarize(setting.name, "agent", rows))
            per_question[setting.name] = rows
            print(_line(summaries[-1]))

    if runner is not None:
        meta["langsmith_dataset"] = runner.dataset_name
    if args.merge and OUT_JSON.exists():  # keep every saved setting that wasn't rerun
        saved = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        rerun = {s["setting"] for s in summaries}
        kept = [s for s in saved["results"] if s["setting"] not in rerun]
        order = [s["setting"] for s in saved["results"]]
        summaries = sorted(kept + summaries, key=lambda s: order.index(s["setting"]) if s["setting"] in order
                           else len(order))
        old_rows = saved.get("per_question", {})
        per_question = {**{k: [{"id": r["id"], "category": r["category"], "scores": r["scores"],
                                "outputs": {"answer": r.get("answer", ""), "doc_ids": r.get("doc_ids", []),
                                            "seconds": r.get("seconds", 0.0)}} for r in v]
                           for k, v in old_rows.items() if k not in rerun}, **per_question}
        meta = {**{k: v for k, v in saved.items() if k not in ("results", "per_question")}, **meta,
                "merged_at": meta["generated_at"], "generated_at": saved.get("generated_at", meta["generated_at"])}
    ev.write_results(OUT_JSON, summaries, per_question, meta)
    OUT_MD.write_text(_markdown(summaries, meta), encoding="utf-8")
    print(f"\n{ev.markdown_table(summaries)}\n\nSaved {_shown(OUT_JSON)} and {_shown(OUT_MD)}")
    return 0


def _run(runner, setting, target, golden, evaluators, concurrency: int, kind: str) -> list[dict]:
    started = time.monotonic()
    if runner is None:
        rows = ev.run_local(target, golden, evaluators, concurrency)
    else:
        rows = runner.run(setting, target, evaluators, concurrency, kind)
    print(f"  ({setting.name}: {time.monotonic() - started:.0f} s)")
    return rows


def _shown(path: Path) -> str:
    """A path relative to the project when it is inside it."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _line(s: dict) -> str:
    keys = ("hit_rate", "mrr", "routing_accuracy", "refusal_accuracy", "faithfulness", "relevance", "correctness",
            "within_8s", "latency_median_s")
    return f"{s['setting']:<40} " + "  ".join(f"{k}={s[k]}" for k in keys if s.get(k) is not None)


def _markdown(summaries: list[dict], meta: dict) -> str:
    retrieval = [s for s in summaries if s["kind"] == "retrieval"]
    agent = [s for s in summaries if s["kind"] == "agent"]
    parts = ["# Week 2 evaluation results", "",
             f"Generated {meta['generated_at']} on {meta['golden_questions']} golden questions. Embeddings: "
             f"{meta['embedding_model']}."
             + (f" Answers: {meta['answer_model']}; judge: {meta['judge_model']}." if agent else "")
             + (f" LangSmith dataset: `{meta['langsmith_dataset']}`." if meta.get("langsmith_dataset") else ""),
             "", "## Retrieval (no LLM)", "", ev.markdown_table(retrieval)]
    if agent:
        parts += ["", "## Agent, end to end", "", ev.markdown_table(agent)]
        if stages := ev.stage_table(agent):
            parts += ["", "## Where the misses come from", "",
                      "Retrieval and final-answer quality measured separately on the answerable questions the judge "
                      f"graded: retrieval is right when an expected document was retrieved, the answer is right when "
                      f"the judge's correctness is at least {ev.ANSWER_OK:.0%}. Recall is the share of expected "
                      "documents the agent retrieved; Withheld is how often the final check withheld claims.", "",
                      stages]
    parts += ["", "Hit rate and MRR use the 17 answerable questions; refusal accuracy uses all of them "
              "(refuse exactly the 3 unanswerable ones). Faithfulness, relevance and correctness are LLM-judge "
              "scores from 0 to 1. Generated by `scripts/run_eval.py`.", ""]
    return "\n".join(parts)


class _LangSmithRunner:
    """Uploads the golden set once and runs each setting as a LangSmith experiment."""

    def __init__(self, golden: list[dict]):
        from langsmith import Client
        self.client = Client()
        digest = hashlib.sha1(json.dumps(golden, sort_keys=True).encode()).hexdigest()[:8]
        self.dataset_name = f"conferatlas-golden-{len(golden)}q-{digest}"
        if not self.client.has_dataset(dataset_name=self.dataset_name):
            dataset = self.client.create_dataset(self.dataset_name, description="ConferAtlas Week 2 golden questions "
                                                 "(synthetic demo data), from data/synthetic/eval/golden_set.json")
            for q in golden:
                inputs, reference = ev.example(q)
                self.client.create_example(inputs=inputs, outputs=reference, dataset_id=dataset.id)
        self.golden = {q["id"]: q for q in golden}

    def run(self, setting, target, evaluators, concurrency: int, kind: str) -> list[dict]:
        results = self.client.evaluate(
            target, data=self.dataset_name, evaluators=evaluators, max_concurrency=concurrency,
            experiment_prefix=setting.name.replace(" · ", "-").replace(" + ", "+").replace(" ", "-"),
            metadata={"kind": kind, **ev.settings_dict(setting)}, blocking=True)
        rows = []
        for item in results:
            inputs = item["example"].inputs
            outputs = item["run"].outputs or {}
            scores = {r.key: r.score for r in item["evaluation_results"]["results"]}
            q = self.golden[inputs["id"]]
            rows.append({"id": q["id"], "category": q["category"], "question": q["question"],
                         "outputs": {"doc_ids": [], "seconds": 0.0, **outputs}, "scores": scores})
        return sorted(rows, key=lambda r: r["id"])


if __name__ == "__main__":
    raise SystemExit(main())
