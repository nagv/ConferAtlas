"""Embeddings and the Chroma index, offline: the fake provider stands in for Nebius."""

import pytest
from langchain_core.embeddings import Embeddings

from core.config import DEFAULT_DATA_DIR, get_settings
from core.docgen import CORPORA
from core.rag.chunking import PRESETS
from core.rag.embeddings import (BATCH_SIZE, QUERY_TASK, ConferAtlasEmbeddings, EmbeddingsNotConfiguredError,
                                 HashEmbeddings, build_embeddings, needs_query_instruction)
from core.rag.index import VectorIndex, collection_name
from core.rag.loaders import load_corpus


class CountingEmbeddings(Embeddings):
    """HashEmbeddings that records every batch it is asked to embed."""

    def __init__(self):
        self.inner, self.batches = HashEmbeddings(), []

    def embed_documents(self, texts):
        self.batches.append(list(texts))
        return self.inner.embed_documents(texts)

    def embed_query(self, text):
        return self.embed_documents([text])[0]


@pytest.fixture(scope="module")
def units():
    return load_corpus(DEFAULT_DATA_DIR)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CONFERATLAS_INDEX_DIR", str(tmp_path / "index"))
    return get_settings()


def test_build_embeddings_needs_configuration(monkeypatch):
    with pytest.raises(EmbeddingsNotConfiguredError, match="EMBEDDING_PROVIDER"):
        build_embeddings(get_settings())


def test_nebius_embeddings_use_the_openai_api_with_plain_text(monkeypatch, tmp_path):
    for name, value in {"EMBEDDING_PROVIDER": "nebius", "EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-8B",
                        "NEBIUS_API_KEY": "nb-test", "NEBIUS_BASE_URL": "https://nebius.example.test/v1",
                        "CONFERATLAS_INDEX_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    emb = build_embeddings(get_settings())
    assert emb.model == "Qwen/Qwen3-Embedding-8B" and emb.query_instruction == QUERY_TASK
    assert emb.inner.openai_api_base == "https://nebius.example.test/v1"
    assert emb.inner.check_embedding_ctx_length is False  # send text, not OpenAI token ids
    assert needs_query_instruction("qwen/qwen3-embedding-0.6b") and not needs_query_instruction("bge-en-icl")


def test_queries_get_the_instruction_and_vectors_are_cached(tmp_path):
    inner = CountingEmbeddings()
    emb = ConferAtlasEmbeddings(inner, "test-model", cache_path=tmp_path / "cache.sqlite", query_instruction="Find it")
    emb.embed_query("badge scanners")
    assert inner.batches[-1] == ["Instruct: Find it\nQuery: badge scanners"]
    emb.embed_documents(["a", "b"])
    assert inner.batches[-1] == ["a", "b"]  # documents are embedded as they are
    again = ConferAtlasEmbeddings(inner, "test-model", cache_path=tmp_path / "cache.sqlite")
    calls = len(inner.batches)
    assert again.embed_documents(["b", "a"]) == emb.embed_documents(["b", "a"])
    assert len(inner.batches) == calls and again.texts_embedded == 0  # served from the cache
    other_model = ConferAtlasEmbeddings(inner, "another-model", cache_path=tmp_path / "cache.sqlite")
    other_model.embed_documents(["a"])
    assert other_model.texts_embedded == 1  # the cache is per model


def test_embeddings_are_sent_in_batches(tmp_path):
    inner = CountingEmbeddings()
    ConferAtlasEmbeddings(inner, "m").embed_documents([f"text {i}" for i in range(BATCH_SIZE * 2 + 5)])
    assert [len(b) for b in inner.batches] == [BATCH_SIZE, BATCH_SIZE, 5]


def test_build_creates_one_collection_per_corpus_and_records_the_model(settings, units):
    emb = build_embeddings(settings)
    index = VectorIndex(settings, emb)
    reports = index.build(units, PRESETS["structure"])
    assert [r.corpus for r in reports] == list(CORPORA)
    assert sum(r.added for r in reports) == 36 and all(r.updated == r.removed == 0 for r in reports)
    assert index.is_built(PRESETS["structure"]) and not index.is_built(PRESETS["fixed"])
    status = {row["corpus"]: row for row in index.status()}
    assert status["pricing"]["embedding_model"] == emb.model and status["pricing"]["dimension"] == 256
    assert status["pricing"]["collection"] == collection_name("pricing", PRESETS["structure"], emb.model)
    hit = index.collection("operations", PRESETS["structure"]).query(
        query_embeddings=[emb.embed_query("badge scanners charged and tested by 7:00 AM")], n_results=1)
    assert hit["metadatas"][0][0]["doc_id"] == "sop_booth_logistics"


def test_rebuild_is_incremental(settings, units, tmp_path):
    emb = build_embeddings(settings)
    index = VectorIndex(settings, emb)
    index.build(units, PRESETS["structure"])
    sent = emb.texts_embedded

    reports = index.build(units, PRESETS["structure"])
    assert all(r.added == r.updated == r.removed == 0 for r in reports) and emb.texts_embedded == sent

    changed = [u.model_copy(update={"page_content": u.page_content + "\n\nNew: Wi-Fi password at the desk."})
               if u.metadata["doc_id"] == "sop_registration_desk" else u
               for u in units if u.metadata["doc_id"] != "pricing_2025"]
    reports = {r.corpus: r for r in index.build(changed, PRESETS["structure"])}
    assert (reports["operations"].updated, reports["operations"].unchanged) == (1, 3)
    assert (reports["pricing"].removed, reports["pricing"].unchanged) == (1, 1)
    assert reports["event_reports"].unchanged == 12


def test_a_new_embedding_model_replaces_the_old_collections(settings, units):
    VectorIndex(settings, build_embeddings(settings)).build(units, PRESETS["structure"])
    other = ConferAtlasEmbeddings(HashEmbeddings(64), "fake-hash-64")
    index = VectorIndex(settings, other)
    index.build(units, PRESETS["structure"])
    models = {row["embedding_model"] for row in index.status()}
    assert models == {"fake-hash-64"}  # vectors from two models are never kept side by side


def test_cli_builds_and_reports(settings, capsys):
    from scripts.build_index import main
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Embedding model: fake-hash-256 (fake)" in out and "pricing__structure_400_ctx__" in out
    assert main(["--status"]) == 0 and "256" in capsys.readouterr().out
    assert main(["--reset", "--strategy", "fixed"]) == 0
    assert "Deleted every collection." in capsys.readouterr().out
    assert {row["chunking"] for row in VectorIndex(settings, build_embeddings(settings)).status()} == {
        PRESETS["fixed"].key}


def test_cli_explains_missing_configuration(capsys):
    from scripts.build_index import main
    assert main([]) == 1
    assert "Document search is off" in capsys.readouterr().err


def test_pca_projection_places_points_and_new_vectors_on_one_map():
    import numpy as np
    from core.rag.projection import pca_2d
    rng = np.random.default_rng(0)
    a, b = rng.normal(0, 1, 64), rng.normal(0, 1, 64)
    vectors = [a + rng.normal(0, 0.05, 64) for _ in range(10)] + [b + rng.normal(0, 0.05, 64) for _ in range(10)]
    projection = pca_2d(vectors)
    assert projection.points.shape == (20, 2) and 0.5 < projection.explained <= 1.0
    near_a = projection.place([a])[0]
    distance = lambda group: np.linalg.norm(projection.points[group] - near_a, axis=1).mean()  # noqa: E731
    assert distance(slice(0, 10)) < distance(slice(10, 20))  # a lands among its own cluster


def test_plot_script_writes_a_map(settings, units, tmp_path, capsys):
    from scripts.plot_embeddings import main
    VectorIndex(settings, build_embeddings(settings)).build(units, PRESETS["structure"])
    out = tmp_path / "map.html"
    assert main(["--question", "badge scanners charged by 7:00 AM", "--out", str(out)]) == 0
    assert "sop_booth_logistics" in capsys.readouterr().out
    assert "ConferAtlas vector index" in out.read_text(encoding="utf-8")
