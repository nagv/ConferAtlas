"""Evaluate document search and the Ask agent on the golden questions.

Two kinds of experiment, each a "target" run on every golden question and scored by "evaluators":

- Retrieval (no LLM): the retriever alone, for each chunking setting and search mode.
  hit_rate (an expected document in the top k), mrr (1 / rank of the first expected document),
  refusal_accuracy (refused exactly the unanswerable questions) and latency.
- Agent (end to end): the Ask agent with document search, for a few retrieval settings. Adds
  routing_accuracy (searched the expected corpora), and an LLM judge (a stronger model than the one
  answering) scores faithfulness (share of claims supported by the evidence the agent saw),
  relevance (does it address the question) and correctness (does it match the reference answer).

Evaluators take (inputs, outputs, reference_outputs), the signature LangSmith's ``evaluate`` uses, so
the same code runs locally (``run_local``) or as LangSmith experiments (scripts/run_eval.py).
"""
from __future__ import annotations

import json
import re
import statistics
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from core.docgen import EVAL_DIR, GOLDEN_SET
from core.rag.retrieve import RetrievalConfig, Retriever

LATENCY_TARGET_S = 8.0
QUESTION_LIMIT_S = 180.0   # one answer may not take longer than this in the evaluation (a stalled request)
# A refusal, however it is worded: "I couldn't find the hotel rate in our documents", "not in the documents".
REFUSAL = re.compile(r"(couldn'?t|could not|can'?t|cannot|did not|didn'?t) find|not (?:in|covered (?:in|by)|"
                     r"available in|mentioned in|included in) (?:our|the) (?:documents|data)|no (?:information|"
                     r"mention|record)s? (?:of|about|on)", re.IGNORECASE)


def is_refusal(text: str) -> bool:
    return not text.strip() or bool(REFUSAL.search(text))
EVIDENCE_CHARS = 6_000     # evidence shown to the judge per answer


@dataclass(frozen=True)
class Setting:
    name: str
    config: RetrievalConfig


RETRIEVAL_SETTINGS = [Setting(f"{chunking} · {label}", RetrievalConfig(chunking=chunking, **kwargs))
                      for chunking in ("fixed", "structure", "semantic")
                      for label, kwargs in (("dense", {"mode": "dense", "rerank": False}),
                                            ("hybrid", {"mode": "hybrid", "rerank": False}),
                                            ("hybrid + rerank", {"mode": "hybrid", "rerank": True}))]
AGENT_SETTINGS = [
    Setting("agent · structure · hybrid + rerank", RetrievalConfig(chunking="structure")),
    Setting("agent · structure · dense", RetrievalConfig(chunking="structure", mode="dense", rerank=False)),
    Setting("agent · fixed · hybrid + rerank", RetrievalConfig(chunking="fixed")),
    Setting("agent · semantic · hybrid + rerank", RetrievalConfig(chunking="semantic")),
]


def load_golden(data_dir: str | Path) -> list[dict]:
    return json.loads((Path(data_dir) / EVAL_DIR / GOLDEN_SET).read_text(encoding="utf-8"))


def example(q: dict) -> tuple[dict, dict]:
    """A golden question as (inputs, reference_outputs)."""
    return ({"question": q["question"], "id": q["id"], "category": q["category"]},
            {"answer": q["reference_answer"], "answerable": q["answerable"],
             "expected_doc_ids": q["expected_doc_ids"], "expected_corpora": q["expected_corpora"]})


# --- targets --------------------------------------------------------------------------------------

class RetrievalTarget:
    def __init__(self, retriever: Retriever, config: RetrievalConfig):
        self.retriever, self.config = retriever, config

    def __call__(self, inputs: dict) -> dict:
        result = self.retriever.search(inputs["question"], self.config)
        return {"doc_ids": [h.doc_id for h in result.hits], "refused": result.refused,
                "seconds": result.seconds.get("total", 0.0), "best_score": result.best_score}


class AgentTarget:
    """The Ask agent with document search, on the full built-in dataset."""

    def __init__(self, retriever: Retriever, config: RetrievalConfig, tables, settings, model=None):
        self.retriever, self.config, self.tables, self.settings, self.model = retriever, config, tables, settings, model

    def __call__(self, inputs: dict) -> dict:
        """The answer, or an error once QUESTION_LIMIT_S passes (the stalled call is left behind)."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
        pool = ThreadPoolExecutor(1)
        future = pool.submit(self._answer, inputs)
        try:
            return future.result(timeout=QUESTION_LIMIT_S)
        except FutureTimeout:
            return {"answer": "", "error": f"no answer within {QUESTION_LIMIT_S:.0f} s", "seconds": QUESTION_LIMIT_S,
                    "doc_ids": [], "corpora": [], "tools": [], "refused": True, "evidence": "",
                    "withheld": False, "input_tokens": 0, "output_tokens": 0}
        finally:
            pool.shutdown(wait=False)

    def _answer(self, inputs: dict) -> dict:
        from core.ai.llm import UsageRecorder
        from core.ai.portfolio_agent import AgentError, PortfolioToolkit, ask_portfolio
        from core.analysis import filter_dataset
        from core.rag.agent_tools import DocumentSearch

        toolkit = PortfolioToolkit(filter_dataset(self.tables), history=self.tables,
                                   documents=DocumentSearch(self.retriever, self.config))
        recorder, started = UsageRecorder(), time.monotonic()
        try:
            answer = ask_portfolio(inputs["question"], toolkit, settings=self.settings, model=self.model,
                                   recorder=recorder)
            text, error = answer.text, ""
        except AgentError as exc:
            answer, text, error = None, "", str(exc)
        seconds = time.monotonic() - started
        sources = answer.sources if answer else []
        tools = [s["tool"] for s in answer.trace if s["step"] == "tool"] if answer else []
        numeric = [r.result for r in toolkit.ledger if not r.tool.startswith("search_")]
        return {"answer": text, "error": error, "seconds": round(seconds, 2),
                "doc_ids": list(dict.fromkeys(s.doc_id for s in sources)),
                "corpora": sorted({t.removeprefix("search_") for t in tools if t.startswith("search_")}),
                "tools": tools, "refused": is_refusal(text),
                "evidence": _evidence(sources, numeric),
                "withheld": bool(answer and answer.withheld),
                "input_tokens": recorder.total_input_tokens, "output_tokens": recorder.total_output_tokens}


def _evidence(sources, numeric: list[dict]) -> str:
    parts = [f"[{s.id}] {s.citation}\n{s.text}" for s in sources]
    parts += [f"[{r.get('result_id', 'T')}] {json.dumps(r, default=str)[:1500]}" for r in numeric]
    return "\n\n".join(parts)[:EVIDENCE_CHARS]


# --- evaluators -----------------------------------------------------------------------------------

def hit_rate(inputs, outputs, reference_outputs) -> dict:
    expected = set(reference_outputs["expected_doc_ids"])
    if not expected:
        return {"key": "hit_rate", "score": None}
    return {"key": "hit_rate", "score": float(bool(expected & set(outputs["doc_ids"])))}


def mrr(inputs, outputs, reference_outputs) -> dict:
    expected = set(reference_outputs["expected_doc_ids"])
    if not expected:
        return {"key": "mrr", "score": None}
    rank = next((i for i, d in enumerate(outputs["doc_ids"], start=1) if d in expected), None)
    return {"key": "mrr", "score": 1.0 / rank if rank else 0.0}


def context_recall(inputs, outputs, reference_outputs) -> dict:
    """Share of the expected documents the agent actually retrieved (retrieval quality inside the agent)."""
    expected = set(reference_outputs["expected_doc_ids"])
    if not expected:
        return {"key": "context_recall", "score": None}
    return {"key": "context_recall", "score": len(expected & set(outputs["doc_ids"])) / len(expected)}


def withheld(inputs, outputs, reference_outputs) -> dict:
    """1 when the final answer gate withheld claims from this answer."""
    return {"key": "withheld_rate", "score": float(bool(outputs.get("withheld")))}


def refusal_accuracy(inputs, outputs, reference_outputs) -> dict:
    """Correct when the answer refuses exactly when the question can't be answered from the documents."""
    return {"key": "refusal_accuracy", "score": float(outputs["refused"] != reference_outputs["answerable"])}


def routing_accuracy(inputs, outputs, reference_outputs) -> dict:
    expected = set(reference_outputs["expected_corpora"])
    if not expected:
        return {"key": "routing_accuracy", "score": None}
    return {"key": "routing_accuracy", "score": float(expected <= set(outputs.get("corpora", [])))}


def within_latency(inputs, outputs, reference_outputs) -> dict:
    return {"key": "within_8s", "score": float(outputs["seconds"] <= LATENCY_TARGET_S)}


class Grade(BaseModel):
    faithfulness: float = Field(ge=0, le=1, description="Share of the answer's factual claims supported by the "
                                                         "evidence; 1 if the answer makes no claims (a refusal)")
    relevance: float = Field(ge=0, le=1, description="How directly the answer addresses the question")
    correctness: float = Field(ge=0, le=1, description="How well the answer matches the reference answer")
    reason: str = Field(description="One sentence explaining the lowest score")


JUDGE_PROMPT = """You grade answers from a conference analytics assistant that answers from tool results and quoted documents.

Score each from 0 to 1:
- faithfulness: the share of the answer's factual claims that are supported by the EVIDENCE below. Claims that are not in the evidence lower it, even if they might be true. A refusal ("I couldn't find this in our documents") makes no claims: score 1.
- relevance: how directly the answer addresses the QUESTION. A refusal scores 1 if the REFERENCE ANSWER says the information is not in the documents, otherwise 0.
- correctness: how well the answer matches the REFERENCE ANSWER (same facts and figures; wording may differ).

QUESTION:
{question}

REFERENCE ANSWER:
{reference}

EVIDENCE THE ASSISTANT SAW:
{evidence}

ANSWER TO GRADE:
{answer}"""


def judge_evaluator(model, method: str = "function_calling") -> Callable:
    """An evaluator scoring faithfulness, relevance and correctness with ``model`` (a LangChain chat model);
    ``method`` is the structured-output method for its provider (``core.ai.llm.structured_output_method``)."""
    grader = model.with_structured_output(Grade, method=method, include_raw=True)
    keys = ("faithfulness", "relevance", "correctness")

    def judge(inputs, outputs, reference_outputs) -> dict:
        if outputs.get("error"):
            return {"results": [{"key": k, "score": 0.0, "comment": outputs["error"]} for k in keys]}
        prompt = JUDGE_PROMPT.format(question=inputs["question"], reference=reference_outputs["answer"],
                                     evidence=outputs.get("evidence") or "(none)", answer=outputs["answer"])
        grade = None
        for _ in range(2):  # a reasoning judge sometimes ends without filling in the form: try once more
            out = grader.invoke(prompt)
            grade = out.get("parsed") or _grade_from_text(out.get("raw"))
            if grade is not None:
                break
        if grade is None:  # counted as not graded (None), never as a score
            return {"results": [{"key": k, "score": None, "comment": "the judge returned no grade"} for k in keys]}
        return {"results": [{"key": "faithfulness", "score": grade.faithfulness, "comment": grade.reason},
                            {"key": "relevance", "score": grade.relevance},
                            {"key": "correctness", "score": grade.correctness}]}

    return judge


def _grade_from_text(message) -> Grade | None:
    """A grade written as JSON in the reply text instead of through the form."""
    text = getattr(message, "content", "") or ""
    if isinstance(text, list):
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return Grade.model_validate_json(match.group(0))
    except ValueError:
        return None


RETRIEVAL_EVALUATORS = [hit_rate, mrr, refusal_accuracy, within_latency]
AGENT_EVALUATORS = [hit_rate, mrr, context_recall, refusal_accuracy, routing_accuracy, within_latency, withheld]
ANSWER_OK = 0.75   # judge correctness at or above this counts as a good final answer


# --- running and summarising ----------------------------------------------------------------------

def _scores(result: dict) -> list[dict]:
    return result["results"] if "results" in result else [result]


def run_local(target: Callable, golden: list[dict], evaluators: list[Callable], concurrency: int = 2) -> list[dict]:
    """Run ``target`` on every golden question and score it; one row per question."""
    def one(q):
        inputs, reference = example(q)
        outputs = target(inputs)
        scores = {}
        for ev in evaluators:
            for s in _scores(ev(inputs, outputs, reference)):
                scores[s["key"]] = s["score"]
        return {"id": q["id"], "category": q["category"], "question": q["question"], "outputs": outputs,
                "scores": scores}

    with ThreadPoolExecutor(max(1, concurrency)) as pool:
        return list(pool.map(one, golden))


def summarize(setting: str, kind: str, rows: list[dict]) -> dict:
    """Mean of each score over the questions it applies to, plus latency."""
    keys = sorted({k for r in rows for k in r["scores"]})
    out = {"setting": setting, "kind": kind, "questions": len(rows)}
    if any("faithfulness" in r["scores"] for r in rows):
        out["judged"] = sum(r["scores"].get("faithfulness") is not None for r in rows)
    for key in keys:
        values = [r["scores"][key] for r in rows if r["scores"].get(key) is not None]
        out[key] = round(statistics.mean(values), 4) if values else None
    seconds = sorted(r["outputs"]["seconds"] for r in rows)
    out["latency_median_s"] = round(statistics.median(seconds), 2) if seconds else None
    out["latency_p90_s"] = round(seconds[min(len(seconds) - 1, int(0.9 * len(seconds)))], 2) if seconds else None
    tokens = [r["outputs"].get("input_tokens", 0) + r["outputs"].get("output_tokens", 0) for r in rows]
    if any(tokens):
        out["tokens_per_question"] = round(statistics.mean(tokens))
    out.update(stage_breakdown(rows))
    return out


def stage_breakdown(rows: list[dict]) -> dict:
    """Where the misses come from, for answerable questions the judge graded.

    Retrieval is right when an expected document was retrieved (hit rate 1); the answer is right when the
    judge's correctness is at least ANSWER_OK. A wrong answer after a hit is an answer-stage problem
    (prompt, model or the final gate); a wrong answer after a miss is a retrieval-stage problem.
    """
    graded = [r for r in rows if r["scores"].get("hit_rate") is not None and r["scores"].get("correctness") is not None]
    if not graded:
        return {}
    hit = [r for r in graded if r["scores"]["hit_rate"] == 1]
    good = {id(r) for r in graded if r["scores"]["correctness"] >= ANSWER_OK}
    return {
        "stage_graded": len(graded),
        "stage_both_ok": sum(id(r) in good for r in hit),
        "stage_answer_problem": sum(id(r) not in good for r in hit),
        "stage_retrieval_problem": sum(id(r) not in good for r in graded if r not in hit),
        "stage_rescued": sum(id(r) in good for r in graded if r not in hit),
        "correct_given_hit": round(statistics.mean(r["scores"]["correctness"] for r in hit), 4) if hit else None,
    }


def write_results(path: Path, summaries: list[dict], per_question: dict[str, list[dict]], meta: dict) -> None:
    payload = {"generated_at": meta.get("generated_at", ""), **meta, "results": summaries,
               "per_question": {k: [{"id": r["id"], "category": r["category"], "scores": r["scores"],
                                     "answer": r["outputs"].get("answer", ""), "doc_ids": r["outputs"]["doc_ids"],
                                     "seconds": r["outputs"]["seconds"]} for r in rows]
                                for k, rows in per_question.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def markdown_table(summaries: list[dict]) -> str:
    columns = [("setting", "Setting"), ("hit_rate", "Hit rate"), ("mrr", "MRR"), ("context_recall", "Recall"),
               ("routing_accuracy", "Routing"),
               ("refusal_accuracy", "Refusal"), ("faithfulness", "Faithfulness"), ("relevance", "Relevance"),
               ("correctness", "Correctness"), ("judged", "Judged"), ("withheld_rate", "Withheld"),
               ("within_8s", "≤ 8 s"), ("latency_median_s", "Median s"),
               ("latency_p90_s", "p90 s")]
    present = [(k, label) for k, label in columns if any(s.get(k) is not None for s in summaries)]

    def cell(k, v, s):
        if v is None:
            return "–"
        if k in ("setting",):
            return str(v)
        if k == "judged":
            return f"{v}/{s['questions']}"
        if k.startswith("latency"):
            return f"{v:.2f}"
        return f"{v:.0%}" if k != "mrr" else f"{v:.2f}"

    lines = ["| " + " | ".join(label for _, label in present) + " |", "|" + "---|" * len(present)]
    lines += ["| " + " | ".join(cell(k, s.get(k), s) for k, _ in present) + " |" for s in summaries]
    return "\n".join(lines)


def stage_table(summaries: list[dict]) -> str:
    """Retrieval quality and final-answer quality side by side, per agent setting."""
    rows = [s for s in summaries if s.get("stage_graded")]
    if not rows:
        return ""
    lines = ["| Setting | Retrieval right, answer right | Retrieval right, answer wrong (answer stage) | "
             "Retrieval missed, answer wrong (retrieval stage) | Retrieval missed, answer right | "
             "Correctness when retrieval was right |", "|---|---|---|---|---|---|"]
    for s in rows:
        given = "–" if s.get("correct_given_hit") is None else f"{s['correct_given_hit']:.0%}"
        lines.append(f"| {s['setting']} | {s['stage_both_ok']}/{s['stage_graded']} | {s['stage_answer_problem']} | "
                     f"{s['stage_retrieval_problem']} | {s['stage_rescued']} | {given} |")
    return "\n".join(lines)


def settings_dict(setting: Setting) -> dict:
    return {"name": setting.name, **asdict(setting.config)}
