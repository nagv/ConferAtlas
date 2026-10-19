"""The document search and RAG Lab endpoints, offline (fake embeddings, word-overlap re-ranker)."""
from datetime import date

import pytest

from backend.services import documents
from core.rag.retrieve import Retriever
from tests.test_rag_retrieve import OverlapRanker

TODAY = date(2026, 10, 17).isoformat()


@pytest.fixture
def rag_on(monkeypatch, tmp_path, install_api):
    """Document search configured with the fake model, the structure-aware index built."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CONFERATLAS_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setattr(Retriever, "ranker", lambda self: OverlapRanker())
    documents._retriever.cache_clear()
    assert install_api.post("/rag/build", json={"chunking": "structure"}).status_code == 200
    yield install_api
    documents._retriever.cache_clear()


def test_status_explains_when_document_search_is_off(install_api):
    docs = install_api.get("/status").json()["documents"]
    assert docs["available"] is False and "EMBEDDING_PROVIDER" in docs["note"]


def test_status_when_the_index_is_built(rag_on):
    docs = rag_on.get("/status").json()["documents"]
    assert docs["available"] is True and docs["chunks"] == 250 and docs["dimension"] == 256
    assert docs["embedding_model"] == "fake-hash-256" and docs["chunk_settings"] == ["structure_400_ctx"]


def test_document_list_and_chunk_viewer(rag_on):
    docs = rag_on.get("/rag/documents").json()
    assert len(docs) == 36 and {d["corpus"] for d in docs} == {
        "event_reports", "pricing", "sponsor_decks", "operations", "session_abstracts"}
    structure = rag_on.post("/rag/chunks", json={"doc_id": "report_EV008"}).json()
    assert [c["section"] for c in structure["chunks"]][:2] == ["1. Executive summary", "2. Audience"]
    assert structure["built"] is True and len(structure["corpus_tokens"]) == 250
    fixed = rag_on.post("/rag/chunks", json={"doc_id": "report_EV008", "strategy": "fixed", "chunk_size": 200,
                                             "overlap": 20}).json()
    assert fixed["key"] == "fixed_200_o20_ctx" and fixed["built"] is False and len(fixed["chunks"]) > 2
    assert rag_on.post("/rag/chunks", json={"doc_id": "nope"}).status_code == 404


def test_search_returns_scored_candidates_and_respects_filters(rag_on):
    payload = {"filters": {}, "today": TODAY, "question": "badge scanners charged and tested by 7:00 AM"}
    result = rag_on.post("/datasets/builtin/rag/search", json=payload).json()
    assert not result["refused"] and result["hit_ids"] and result["score_name"] == "rerank"
    top = next(c for c in result["candidates"] if c["chunk_id"] == result["hit_ids"][0])
    assert top["doc_id"] == "sop_booth_logistics" and top["rank_after"] == 1
    boston = {**payload, "question": "main operational issue at the venue", "filters": {"cities": ["Boston"]}}
    for c in rag_on.post("/datasets/builtin/rag/search", json=boston).json()["candidates"]:
        assert c["corpus"] != "event_reports" or c["title"].startswith("Boston")


def test_search_on_an_unbuilt_setting_asks_for_a_build(rag_on):
    payload = {"filters": {}, "today": TODAY, "question": "Premier Booth price",
               "settings": {"chunking": "fixed"}}
    result = rag_on.post("/datasets/builtin/rag/search", json=payload).json()
    assert result["needs_build"] is True and "Build it first" in result["note"]
    built = rag_on.post("/rag/build", json={"chunking": "fixed"}).json()
    assert built["key"] == "fixed_500_o50_ctx" and len(built["collections"]) == 5
    assert rag_on.post("/datasets/builtin/rag/search", json=payload).json()["needs_build"] is False


def test_embedding_map_places_the_question(rag_on):
    result = rag_on.post("/rag/map", json={"question": "Premier Booth price 2026"}).json()
    assert len(result["points"]) == 250 and len(result["nearest"]) == 5
    assert result["question_point"] is not None and 0 < result["explained"] <= 1
    assert rag_on.post("/rag/map", json={"chunking": "semantic"}).status_code == 409  # not indexed yet


def test_experiments_before_any_evaluation(install_api, monkeypatch, tmp_path):
    monkeypatch.setattr(documents, "EXPERIMENTS_FILE", tmp_path / "missing.json")
    assert install_api.get("/rag/experiments").json() == {"available": False, "results": [], "generated_at": ""}


def test_document_tools_follow_the_dataset_and_filters(rag_on, dataset):
    from backend.store import StoredDataset
    from core.analysis import filter_dataset

    builtin = StoredDataset("builtin", "builtin", "folder:x", dataset, [])
    search, note = documents.document_search(builtin, dataset)
    assert search is not None and search.event_ids is None and note == ""
    narrowed, _ = documents.document_search(builtin, filter_dataset(dataset, cities=["Boston"]))
    assert narrowed.event_ids and all(e.startswith("EV") for e in narrowed.event_ids)
    upload = StoredDataset("u1", "upload", "upload:abc", dataset, [])
    none, why = documents.document_search(upload, dataset)
    assert none is None and "uploaded data" in why
