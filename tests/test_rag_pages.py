"""The RAG Lab page and the Ask page's document features, rendered headlessly against the real API."""
import pytest

from backend.services import documents
from core.rag.retrieve import Retriever
from shared.models import AgentAnswerModel
from tests.test_app_pages import _ok, _page, _text
from tests.test_rag_retrieve import OverlapRanker


@pytest.fixture
def rag_on(monkeypatch, tmp_path, install_api):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CONFERATLAS_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setattr(Retriever, "ranker", lambda self: OverlapRanker())
    monkeypatch.setattr(documents, "EXPERIMENTS_FILE", tmp_path / "no_results.json")
    documents._retriever.cache_clear()
    assert install_api.post("/rag/build", json={"chunking": "structure"}).status_code == 200
    yield install_api
    documents._retriever.cache_clear()


def test_rag_lab_without_document_search_still_shows_chunks():
    at = _page("rag_lab").run()
    _ok(at)
    assert "Document search is off" in " ".join(i.value for i in at.info)
    text = _text(at)
    assert "chunks** from" in text and "Chunk 1" in text
    assert "Search needs the document index" in text


def test_rag_lab_with_document_search(rag_on):
    at = _page("rag_lab").run()
    _ok(at)
    text = _text(at)
    assert "structure-aware chunks, fake-hash-256" in text
    assert any("chunks kept as evidence" in s.value for s in at.success)   # the retrieval inspector
    assert at.dataframe                                                      # candidates table
    assert "250 chunks embedded with fake-hash-256" in text                 # the embedding map
    assert "No evaluation results yet" in " ".join(i.value for i in at.info)


def test_rag_lab_offers_to_build_an_unindexed_setting(rag_on):
    at = _page("rag_lab").run()
    at.selectbox(key="lab_s_chunking").set_value("fixed").run()
    _ok(at)
    assert any("isn't indexed yet" in w.value for w in at.warning)
    at.button(key="lab_build").click().run()
    _ok(at)
    assert any("chunks kept as evidence" in s.value for s in at.success)


def test_ask_page_shows_document_starters_sources_and_trace(rag_on):
    at = _page("ask").run()
    _ok(at)
    assert "Document search is on: 250 passages" in _text(at)
    assert any("badge scanners" in b.label for b in at.button)
    answer = AgentAnswerModel(
        text="Scanners are charged by 7:00 AM [S1].",
        sources=[{"id": "S1", "corpus": "operations", "doc_id": "sop_booth_logistics", "chunk_id": "c1",
                  "citation": "[SOP-03 Sponsor booth logistics, Lead retrieval]", "text": "Badge scanners ...",
                  "score": 0.98},
                 {"id": "S2", "corpus": "operations", "doc_id": "sop_registration_desk", "chunk_id": "c2",
                  "citation": "[SOP-02 Registration desk, Opening]", "text": "The desk opens ...", "score": 0.4}],
        trace=[{"step": "tool", "tool": "search_operations", "args": {"query": "badge scanners"},
                "summary": "Found S1, S2"}, {"step": "answer", "text": "Scanners are charged by 7:00 AM [S1]."}])
    at.session_state["ask_history"] = [
        {"role": "user", "content": "When?", "answer": None, "scope": "all data"},
        {"role": "assistant", "content": answer.text, "answer": answer, "scope": "all data"}]
    at.run()
    _ok(at)
    labels = [e.label for e in at.expander]
    assert "Sources (1 cited, 2 retrieved)" in labels and "How this was answered" in labels
    assert "**S1** [SOP-03 Sponsor booth logistics, Lead retrieval]" in _text(at)


def test_experiments_tab_charts_saved_results(rag_on, monkeypatch, tmp_path):
    import json
    results = {"generated_at": "2026-10-18T10:00:00", "results": [
        {"setting": "structure · hybrid + rerank", "kind": "retrieval", "questions": 20, "hit_rate": 1.0, "mrr": 1.0},
        {"setting": "agent · structure · hybrid + rerank", "kind": "agent", "questions": 20, "hit_rate": 1.0,
         "mrr": 1.0, "faithfulness": 0.98, "relevance": 1.0, "routing_accuracy": 1.0}]}
    path = tmp_path / "eval_results.json"
    path.write_text(json.dumps(results), encoding="utf-8")
    monkeypatch.setattr(documents, "EXPERIMENTS_FILE", path)
    at = _page("rag_lab").run()
    _ok(at)
    assert "Generated 2026-10-18T10:00:00" in _text(at)
    assert len(at.dataframe) >= 2  # the search inspector's table and the results table
