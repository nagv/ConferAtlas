"""The evaluation: evaluators, targets, summaries and the script, offline (fake models, temporary output)."""
import json

import pytest
from langchain_core.messages import AIMessage

from core.rag import evaluate as ev
from tests.test_portfolio_agent import FakeToolModel, tool_call

REF = {"answer": "By 7:00 AM.", "answerable": True, "expected_doc_ids": ["sop_booth_logistics"],
       "expected_corpora": ["operations"]}
NO_ANSWER = {"answer": "Not in the documents.", "answerable": False, "expected_doc_ids": [], "expected_corpora": []}


def test_retrieval_evaluators():
    out = {"doc_ids": ["sop_registration_desk", "sop_booth_logistics"], "refused": False, "seconds": 0.4}
    assert ev.hit_rate({}, out, REF)["score"] == 1.0
    assert ev.mrr({}, out, REF)["score"] == 0.5
    assert ev.mrr({}, {**out, "doc_ids": ["x"]}, REF)["score"] == 0.0
    assert ev.hit_rate({}, out, NO_ANSWER)["score"] is None  # not applicable to unanswerable questions
    assert ev.refusal_accuracy({}, out, REF)["score"] == 1.0
    assert ev.refusal_accuracy({}, out, NO_ANSWER)["score"] == 0.0
    assert ev.refusal_accuracy({}, {**out, "refused": True}, NO_ANSWER)["score"] == 1.0
    assert ev.within_latency({}, {**out, "seconds": 9.0}, REF)["score"] == 0.0


def test_routing_needs_every_expected_corpus():
    assert ev.routing_accuracy({}, {"corpora": ["operations", "pricing"]}, REF)["score"] == 1.0
    assert ev.routing_accuracy({}, {"corpora": ["pricing"]}, REF)["score"] == 0.0
    assert ev.routing_accuracy({}, {"corpora": []}, NO_ANSWER)["score"] is None


class FakeJudge:
    """with_structured_output returns itself; invoke returns a fixed grade and keeps the prompt."""

    def __init__(self, **grade):
        self.grade, self.prompts = ev.Grade(reason="ok", **grade), []

    def with_structured_output(self, schema, method, include_raw=False):
        assert schema is ev.Grade and method == "function_calling"
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return {"parsed": self.grade, "raw": AIMessage(content="")}


def test_judge_scores_three_dimensions_and_sees_the_evidence():
    judge = FakeJudge(faithfulness=0.5, relevance=1.0, correctness=0.25)
    out = ev.judge_evaluator(judge)({"question": "When?"}, {"answer": "By 7 AM [S1].", "evidence": "[S1] SOP text"},
                                    REF)
    assert {r["key"]: r["score"] for r in out["results"]} == {"faithfulness": 0.5, "relevance": 1.0,
                                                              "correctness": 0.25}
    assert "[S1] SOP text" in judge.prompts[0] and "By 7:00 AM." in judge.prompts[0]
    failed = ev.judge_evaluator(judge)({"question": "q"}, {"answer": "", "error": "timeout"}, REF)
    assert all(r["score"] == 0.0 for r in failed["results"])


def test_run_local_and_summarize():
    golden = [{"id": "Q1", "category": "single_doc", "question": "a", "answerable": True, "reference_answer": "x",
               "expected_doc_ids": ["d1"], "expected_corpora": ["operations"]},
              {"id": "Q2", "category": "unanswerable", "question": "b", "answerable": False, "reference_answer": "-",
               "expected_doc_ids": [], "expected_corpora": []}]

    def target(inputs):
        return {"doc_ids": ["d1"], "refused": inputs["question"] == "b", "seconds": 1.0 if inputs["id"] == "Q1" else 3.0}

    rows = ev.run_local(target, golden, ev.RETRIEVAL_EVALUATORS)
    summary = ev.summarize("test", "retrieval", rows)
    assert summary["hit_rate"] == 1.0 and summary["refusal_accuracy"] == 1.0 and summary["questions"] == 2
    assert summary["latency_median_s"] == 2.0
    table = ev.markdown_table([summary])
    assert table.startswith("| Setting | Hit rate | MRR | Refusal |") and "| test | 100% | 1.00 | 100% |" in table


def test_agent_target_reports_sources_corpora_and_evidence(dataset, tmp_path, monkeypatch):
    from core.config import DEFAULT_DATA_DIR, get_settings
    from core.rag.chunking import PRESETS
    from core.rag.embeddings import build_embeddings
    from core.rag.index import VectorIndex
    from core.rag.loaders import load_corpus
    from core.rag.retrieve import Retriever
    from tests.test_rag_retrieve import OverlapRanker

    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CONFERATLAS_INDEX_DIR", str(tmp_path))
    settings = get_settings()
    index = VectorIndex(settings, build_embeddings(settings))
    index.build(load_corpus(DEFAULT_DATA_DIR), PRESETS["structure"])
    retriever = Retriever(index)
    retriever._ranker = OverlapRanker()
    model = FakeToolModel(tool_call("search_operations", {"query": "badge scanners charged tested 7:00 AM"}),
                          AIMessage(content="By 7:00 AM [S1]."))
    target = ev.AgentTarget(retriever, ev.AGENT_SETTINGS[0].config, dataset, settings, model=model)
    out = target({"question": "When must badge scanners be charged?", "id": "Q09"})
    assert out["answer"] == "By 7:00 AM [S1]." and out["corpora"] == ["operations"] and not out["refused"]
    assert "sop_booth_logistics" in out["doc_ids"] and out["evidence"].startswith("[S1]")
    assert ev.routing_accuracy({}, out, REF)["score"] == 1.0


def test_script_retrieval_only_writes_results(monkeypatch, tmp_path, capsys):
    import scripts.run_eval as run_eval
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CONFERATLAS_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setattr(run_eval, "OUT_JSON", tmp_path / "eval_results.json")
    monkeypatch.setattr(run_eval, "OUT_MD", tmp_path / "eval_results.md")
    from core.rag.retrieve import Retriever
    from tests.test_rag_retrieve import OverlapRanker
    monkeypatch.setattr(Retriever, "ranker", lambda self: OverlapRanker())
    assert run_eval.main(["--retrieval-only", "--questions", "4", "--no-langsmith"]) == 0
    data = json.loads((tmp_path / "eval_results.json").read_text(encoding="utf-8"))
    assert len(data["results"]) == 9 and data["golden_questions"] == 4
    assert {r["kind"] for r in data["results"]} == {"retrieval"}
    assert "## Retrieval (no LLM)" in (tmp_path / "eval_results.md").read_text(encoding="utf-8")
    assert "LangSmith off" in capsys.readouterr().out


@pytest.mark.parametrize("text, refused", [
    ("I couldn't find this in our documents.", True),
    ("I couldn't find the hotel room rate for speakers in our documents.", True),
    ("That is not in the documents.", True),
    ("There is no information about catering vendors.", True),
    ("", True),
    ("Badge scanners must be charged by 7:00 AM [S1].", False),
    ("We found 3 sponsors below target.", False),
])
def test_refusal_detection_reads_the_wording(text, refused):
    assert ev.is_refusal(text) is refused


class FlakyJudge:
    """Fills in nothing first (as a reasoning model can), then writes the grade as JSON text."""

    def __init__(self):
        self.calls = 0

    def with_structured_output(self, schema, method, include_raw=False):
        assert include_raw
        return self

    def invoke(self, prompt):
        self.calls += 1
        if self.calls == 1:
            return {"parsed": None, "raw": AIMessage(content="")}
        return {"parsed": None, "raw": AIMessage(content='Here: {"faithfulness": 0.8, "relevance": 1, '
                                                         '"correctness": 0.5, "reason": "one claim unsupported"}')}


def test_judge_retries_and_reads_json_from_text():
    judge = FlakyJudge()
    out = ev.judge_evaluator(judge)({"question": "q"}, {"answer": "a", "evidence": "e"}, REF)
    assert judge.calls == 2 and [r["score"] for r in out["results"]] == [0.8, 1.0, 0.5]


def test_a_missing_grade_is_not_a_score():
    class SilentJudge(FlakyJudge):
        def invoke(self, prompt):
            return {"parsed": None, "raw": AIMessage(content="")}
    out = ev.judge_evaluator(SilentJudge())({"question": "q"}, {"answer": "a", "evidence": "e"}, REF)
    assert all(r["score"] is None for r in out["results"])
    rows = [{"id": "Q1", "category": "x", "scores": {"faithfulness": None, "hit_rate": 1.0}, "outputs": {"seconds": 1}},
            {"id": "Q2", "category": "x", "scores": {"faithfulness": 0.5, "hit_rate": 1.0}, "outputs": {"seconds": 1}}]
    summary = ev.summarize("s", "agent", rows)
    assert summary["judged"] == 1 and summary["faithfulness"] == 0.5
    assert "| 1/2 |" in ev.markdown_table([summary])


def test_context_recall_and_withheld_rate():
    ref = {"expected_doc_ids": ["a", "b"], "expected_corpora": [], "answerable": True}
    assert ev.context_recall({}, {"doc_ids": ["b", "c"]}, ref)["score"] == 0.5
    assert ev.context_recall({}, {"doc_ids": []}, {**ref, "expected_doc_ids": []})["score"] is None
    assert ev.withheld({}, {"withheld": True}, ref) == {"key": "withheld_rate", "score": 1.0}


def test_stage_breakdown_separates_retrieval_and_answer_problems():
    def row(hit, correct):
        return {"scores": {"hit_rate": hit, "correctness": correct}, "outputs": {"seconds": 1.0}}

    rows = [row(1.0, 1.0), row(1.0, 0.2), row(0.0, 0.1), row(0.0, 0.9),
            {"scores": {"hit_rate": None, "correctness": 1.0}, "outputs": {"seconds": 1.0}}]  # unanswerable: left out
    stages = ev.stage_breakdown(rows)
    assert stages == {"stage_graded": 4, "stage_both_ok": 1, "stage_answer_problem": 1,
                      "stage_retrieval_problem": 1, "stage_rescued": 1, "correct_given_hit": 0.6}
    table = ev.stage_table([{"setting": "s", **stages}])
    assert "| s | 1/4 | 1 | 1 | 1 | 60% |" in table
    assert ev.stage_table([{"setting": "retrieval only"}]) == ""
