"""Document search (RAG) for the API: availability, the shared retriever, and the RAG Lab views.

One ``Retriever`` is kept per embedding model and index folder, so its embedding client stays warm
(Nebius latency is worst on cold connections), and BM25 indexes and the re-ranker load once.

Documents describe the built-in demo dataset. For an uploaded dataset they would cite events that
are not in the data, so document search is off there and the answer says why.
"""
from __future__ import annotations

import json
import threading
import time
from functools import lru_cache
from pathlib import Path

from core.config import Settings, get_settings
from core.docgen import CORPORA, DOCS_DIR, load_manifest
from core.rag.agent_tools import DocumentSearch
from core.rag.chunking import PRESETS, ChunkConfig, chunk_corpus, chunk_document
from core.rag.embeddings import EmbeddingsNotConfiguredError, build_embeddings
from core.rag.index import VectorIndex
from core.rag.loaders import load_corpus, load_document
from core.rag.projection import pca_2d
from core.rag.retrieve import RetrievalConfig, Retriever
from shared.models import (BuildRequest, ChunkRequest, DocumentStatus, MapRequest, RagSearchRequest,
                           RetrievalSettings)

APP_CHUNKING = "structure"    # the setting the Ask assistant searches
EXPERIMENTS_FILE = Path(__file__).resolve().parents[2] / "docs" / "week2" / "eval_results.json"
_lock = threading.Lock()


@lru_cache(maxsize=2)
def _retriever(model_key: tuple) -> Retriever:
    settings = get_settings()
    return Retriever(VectorIndex(settings, build_embeddings(settings)))


def retriever(settings: Settings | None = None) -> Retriever:
    settings = settings or get_settings()
    return _retriever((settings.embedding_provider, settings.embedding_model, str(settings.index_dir)))


@lru_cache(maxsize=1)
def _units(data_dir: str) -> list:
    return load_corpus(data_dir)


def units(settings: Settings | None = None) -> list:
    return _units(str((settings or get_settings()).data_dir))


def status(settings: Settings | None = None) -> DocumentStatus:
    settings = settings or get_settings()
    if not settings.embeddings_configured:
        return DocumentStatus(available=False, note="Document search is off: set EMBEDDING_PROVIDER and "
                              "EMBEDDING_MODEL (and the provider's key) in .env. See docs/week2/build-guide.md.")
    if not (settings.data_dir / DOCS_DIR / "manifest.json").exists():
        return DocumentStatus(available=False, note="No documents found: run python scripts/generate_data.py.")
    try:
        index = retriever(settings).index
        rows = index.status()
    except EmbeddingsNotConfiguredError as exc:
        return DocumentStatus(available=False, note=str(exc))
    app_key = PRESETS[APP_CHUNKING].key
    app_rows = [r for r in rows if r["chunking"] == app_key]
    built = len(app_rows) == len(CORPORA) and all(r["chunks"] for r in app_rows)
    return DocumentStatus(
        available=built, embedding_model=index.model,
        note="" if built else "The document index isn't built yet: run python scripts/build_index.py.",
        dimension=max((r["dimension"] for r in app_rows), default=0), chunks=sum(r["chunks"] for r in app_rows),
        chunk_settings=sorted({r["chunking"] for r in rows}))


def document_search(ds, tables) -> tuple[DocumentSearch | None, str]:
    """The assistant's document tools for this dataset and filter selection, or (None, why not)."""
    if ds.kind != "builtin":
        return None, ("Document search covers the built-in demo events only, so it is off for uploaded data.")
    state = status()
    if not state.available:
        return None, state.note
    all_events, chosen = ds.tables["events.csv"]["event_id"], tables["events.csv"]["event_id"]
    event_ids = None if len(chosen) == len(all_events) else sorted(chosen.tolist())
    return DocumentSearch(retriever(), RetrievalConfig(chunking=APP_CHUNKING), event_ids=event_ids), ""


# --- RAG Lab --------------------------------------------------------------------------------------

def retrieval_config(settings: RetrievalSettings) -> RetrievalConfig:
    return RetrievalConfig(**settings.model_dump())


def search(ds, tables, req: RagSearchRequest) -> dict:
    config = retrieval_config(req.settings)
    r = retriever()
    built = all((c := r.index.collection(corpus, config.chunk_config)) is not None and c.count()
                for corpus in (req.corpora or CORPORA))
    if not built:
        return {"question": req.question, "candidates": [], "hit_ids": [], "refused": True, "best_score": 0.0,
                "score_name": config.score_name, "threshold": config.threshold if config.rerank
                else config.dense_threshold, "seconds": {}, "needs_build": True,
                "note": f"The '{config.chunk_config.key}' setting isn't indexed yet. Build it first."}
    filters = {}
    if ds.kind == "builtin" and len(tables["events.csv"]) != len(ds.tables["events.csv"]):
        filters = {"event_id": tables["events.csv"]["event_id"].tolist()}
    result = r.search(req.question, config, list(req.corpora) or None, filters)
    return {
        "question": result.question, "refused": result.refused, "best_score": round(result.best_score, 4),
        "score_name": config.score_name, "threshold": config.threshold if config.rerank else config.dense_threshold,
        "seconds": result.seconds, "hit_ids": [h.chunk_id for h in result.hits],
        "candidates": [{"chunk_id": c.chunk_id, "doc_id": c.doc_id, "corpus": c.corpus, "title": c.title,
                        "citation": c.citation, "text": c.text, "dense": round(c.dense, 4), "bm25": round(c.bm25, 4),
                        "hybrid": round(c.hybrid, 4), "rerank": None if c.rerank is None else round(c.rerank, 4),
                        "rank_before": c.rank_before, "rank_after": c.rank_after} for c in result.candidates],
    }


def documents() -> list[dict]:
    settings = get_settings()
    return [{"doc_id": r.doc_id, "corpus": r.corpus, "source_type": r.source_type, "title": r.title,
             "event_id": r.event_id, "year": r.year} for r in load_manifest(settings.data_dir)]


def _chunk_config(strategy: str, size: int | None, overlap: int | None, contextual: bool = True) -> ChunkConfig:
    preset = PRESETS[strategy]
    return ChunkConfig(strategy, chunk_size=size or preset.chunk_size,
                       overlap=preset.overlap if overlap is None else min(overlap, (size or preset.chunk_size) - 1),
                       contextual=contextual)


def chunks(req: ChunkRequest) -> dict:
    settings = get_settings()
    record = next((r for r in load_manifest(settings.data_dir) if r.doc_id == req.doc_id), None)
    if record is None:
        raise KeyError(req.doc_id)
    config = _chunk_config(req.strategy, req.chunk_size, req.overlap if req.strategy == "fixed" else 0, req.contextual)
    embed = None
    if config.strategy == "semantic":
        embed = retriever().index.embeddings.embed_documents  # needs the embedding model; vectors are cached
    doc_units = load_document(record, settings.data_dir / DOCS_DIR)
    pieces = chunk_document(doc_units, config, embed)
    corpus_tokens = [c.metadata["tokens"] for c in chunk_corpus(units(settings), config, embed)]
    index = retriever().index if settings.embeddings_configured else None
    built = index is not None and all((c := index.collection(corpus, config)) is not None and c.count()
                                      for corpus in CORPORA)
    from core.rag.chunking import chunk_body
    return {"doc_id": record.doc_id, "title": record.title, "source_type": record.source_type,
            "units": len(doc_units), "key": config.key, "built": built, "corpus_tokens": corpus_tokens,
            "chunks": [{"chunk_id": c.metadata["chunk_id"], "index": c.metadata["chunk_index"],
                        "tokens": c.metadata["tokens"], "page": c.metadata["page"], "slide": c.metadata["slide"],
                        "section": c.metadata["section"], "header": c.metadata["header"], "body": chunk_body(c)}
                       for c in pieces]}


def embedding_map(req: MapRequest) -> dict:
    import numpy as np

    index = retriever().index
    config = PRESETS[req.chunking]
    rows = []
    for corpus in CORPORA:
        got = index.get(corpus, config, include=["embeddings", "metadatas", "documents"])
        rows += list(zip(got["embeddings"], got["metadatas"], got["documents"]))
    if not rows:
        raise LookupError(f"The '{config.key}' setting isn't indexed yet.")
    projection = pca_2d([r[0] for r in rows])
    out = {"points": [{"x": float(x), "y": float(y), "corpus": m["corpus"], "chunk_id": m["chunk_id"],
                       "title": m["title"], "section": m["section"],
                       "preview": d.split("\n\n", 1)[-1][:220].replace("\n", " ")}
                      for (x, y), (_, m, d) in zip(projection.points, rows)],
           "explained": round(projection.explained, 4), "model": index.model}
    if req.question:
        q = np.asarray(index.embeddings.embed_query(req.question), dtype=float)
        vectors = np.asarray([r[0] for r in rows], dtype=float)
        sims = vectors @ q / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(q))
        out["nearest"] = [rows[int(i)][1]["chunk_id"] for i in np.argsort(-sims)[:5]]
        point = projection.place([q])[0]
        out["question_point"] = (float(point[0]), float(point[1]))
    return out


def build(req: BuildRequest) -> dict:
    """Index one chunking setting (re-embeds only what changed; vectors are cached)."""
    config = _chunk_config(req.chunking, req.chunk_size, req.overlap)
    r = retriever()
    with _lock:  # one build at a time
        before, started = r.index.embeddings.texts_embedded, time.monotonic()
        reports = r.index.build(units(), config)
    return {"key": config.key, "seconds": round(time.monotonic() - started, 2),
            "texts_embedded": r.index.embeddings.texts_embedded - before,
            "collections": [vars(rep) for rep in reports]}


def experiments() -> dict:
    if not EXPERIMENTS_FILE.exists():
        return {"available": False}
    data = json.loads(EXPERIMENTS_FILE.read_text(encoding="utf-8"))
    return {"available": True, "results": data.get("results", []), "generated_at": data.get("generated_at", "")}
