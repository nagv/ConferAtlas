"""Retrieval: dense, BM25 and hybrid search, filters, re-ranking and refusal (offline: fake model and re-ranker)."""
import re

import pytest

from core.config import DEFAULT_DATA_DIR, get_settings
from core.rag.chunking import PRESETS
from core.rag.embeddings import build_embeddings
from core.rag.index import VectorIndex
from core.rag.loaders import load_corpus
from core.rag.retrieve import RetrievalConfig, Retriever, _matches, _where, tokenize


class OverlapRanker:
    """Stands in for FlashRank: score = share of question words found in the passage."""

    def rerank(self, request):
        words = set(re.findall(r"\w+", request.query.lower()))
        out = []
        for p in request.passages:
            found = set(re.findall(r"\w+", p["text"].lower()))
            out.append({"id": p["id"], "score": len(words & found) / max(1, len(words))})
        return sorted(out, key=lambda o: -o["score"])


@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("EMBEDDING_PROVIDER", "fake")
    mp.setenv("CONFERATLAS_INDEX_DIR", str(tmp_path_factory.mktemp("index")))
    settings = get_settings()
    index = VectorIndex(settings, build_embeddings(settings))
    index.build(load_corpus(DEFAULT_DATA_DIR), PRESETS["structure"])
    r = Retriever(index)
    r._ranker = OverlapRanker()
    yield r
    mp.undo()


def test_config_validation():
    with pytest.raises(ValueError):
        RetrievalConfig(mode="sparse")
    with pytest.raises(ValueError):
        RetrievalConfig(chunking="paragraph")
    with pytest.raises(ValueError):
        RetrievalConfig(top_k=10, candidates=5)
    assert RetrievalConfig().score_name == "rerank" and RetrievalConfig(rerank=False).score_name == "hybrid"


def test_tokenize_keeps_times_prices_and_drops_stopwords():
    assert tokenize("By what time must the badge scanners be charged, by 7:00 AM?") == [
        "time", "must", "badge", "scanners", "charged", "7:00", "am?"[:2]]
    assert "$51000" in tokenize("Premier Booth costs $51,000")


@pytest.mark.parametrize("text, tokens", [
    ("Chicago DevSecOps Forum 2024: post-event report", ["chicago", "devsecops", "forum", "2024", "post", "event",
                                                         "report"]),
    ("Document: SOP-03 Sponsor booth logistics", ["document", "sop", "03", "sponsor", "booth", "logistics"]),
    ("Doors open at 7:00. Rated 4.5 out of 5.", ["doors", "open", "7:00", "rated", "4.5", "out", "5"]),
    ("... : .", []),
])
def test_tokenize_trims_punctuation_at_word_edges_only(text, tokens):
    assert tokenize(text) == tokens


def test_heading_years_now_match_question_years():
    assert "2024" in tokenize("Chicago DevSecOps Forum 2024: post-event report")
    assert set(tokenize("What happened at the 2024 forum?")) & set(tokenize("Forum 2024: summary")) >= {"2024", "forum"}


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid"])
def test_every_mode_finds_the_booth_sop(retriever, mode):
    result = retriever.search("badge scanners must be charged and tested by 7:00 AM",
                              RetrievalConfig(mode=mode, rerank=False, dense_threshold=0.0))
    assert not result.refused
    assert "sop_booth_logistics" in [c.doc_id for c in result.hits[:3]]
    assert result.hits[0].citation.startswith("[SOP-03 Sponsor booth logistics") or mode != "bm25"


def test_hybrid_scores_and_ranks_are_recorded(retriever):
    result = retriever.search("Premier Booth price 2026", RetrievalConfig(top_k=3, candidates=10))
    assert len(result.candidates) <= 10 and len(result.hits) == 3
    assert [c.rank_before for c in result.candidates] == list(range(1, len(result.candidates) + 1))
    assert sorted(c.rank_after for c in result.candidates if c.rank_after) == [1, 2, 3]
    assert all(c.rerank is not None and 0 <= c.hybrid <= 1 for c in result.candidates)
    assert set(result.seconds) >= {"embed", "dense", "bm25", "rerank", "total"}
    assert result.hits[0].text and not result.hits[0].text.startswith("Document:")  # body without header
    assert result.as_dict()["config"]["mode"] == "hybrid"


def test_corpora_limit_the_search(retriever):
    result = retriever.search("Premier Booth price", RetrievalConfig(), corpora=["pricing"])
    assert {c.corpus for c in result.candidates} == {"pricing"}


def test_filters_keep_matching_events_and_documents_without_an_event(retriever):
    result = retriever.search("main operational issue venue", RetrievalConfig(rerank=False, dense_threshold=0.0),
                              filters={"city": ["Boston"]})
    assert result.candidates
    for c in result.candidates:
        assert c.metadata["city"] in ("Boston", "")


def test_filter_helpers():
    assert _matches({"city": "Boston", "year": 2025}, {"city": ["Boston"], "year": [2025]})
    assert not _matches({"city": "Austin"}, {"city": ["Boston"]})
    assert _matches({"city": "", "year": 0}, {"city": ["Boston"], "year": [2024]})  # pricing, SOPs pass
    assert _where({}) is None
    assert _where({"city": ["Boston"]}) == {"$or": [{"city": ""}, {"city": {"$in": ["Boston"]}}]}
    assert _where({"city": ["Boston"], "year": [2025]})["$and"][1] == {"$or": [{"year": 0}, {"year": {"$in": [2025]}}]}


def test_refuses_when_nothing_scores_above_the_threshold(retriever):
    result = retriever.search("zebra quantum lasagne", RetrievalConfig(threshold=0.5))
    assert result.refused and result.hits == [] and result.best_score < 0.5
    dense = retriever.search("zebra quantum lasagne", RetrievalConfig(mode="dense", rerank=False, dense_threshold=0.99))
    assert dense.refused


def test_bm25_index_is_shared_and_rebuilt_when_the_index_changes(retriever):
    retriever.search("Premier Booth", RetrievalConfig(mode="bm25", rerank=False))
    key = PRESETS["structure"].key
    counts, _, ids, metas, _ = retriever._bm25[key]
    assert len(ids) == sum(counts) == 250 and {m["corpus"] for m in metas} == {
        "event_reports", "pricing", "sponsor_decks", "operations", "session_abstracts"}
