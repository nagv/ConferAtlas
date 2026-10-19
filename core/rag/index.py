"""The vector index: chunks and their embeddings in a local Chroma database.

Layout: one Chroma collection per corpus and chunking setting and embedding model, named
``<corpus>__<chunking key>__<model hash>`` (for example ``pricing__structure_400_ctx__1a2b3c4d``), saved
under ``<index dir>/chroma``. Separate collections per corpus are what let the agent choose where to
search (Agentic RAG) and keep citations clean; separate collections per chunking setting let the
evaluation compare them on the same questions.

Each collection's metadata records the corpus, chunking setting, embedding model and vector size, so
vectors from different models are never mixed. Building again with another model creates new
collections and removes the old ones for the same corpus and setting.

Builds are incremental: every chunk stores a hash of its document's chunks. A document whose hash is
unchanged is skipped; a changed one is deleted and re-added; one no longer in the corpus is removed.
Distances are cosine (0 = same direction, 2 = opposite).

Collections are created with ``sync_threshold`` 2, so Chroma writes each collection's vector index to disk
as chunks are added. With its default (1,000 vectors) none of our small collections was ever written:
Chroma kept them in memory, and once it dropped one from its in-memory cache (with 15 collections in use)
reloading it failed with "Error creating hnsw segment reader: Nothing found on disk".
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document

from core.config import Settings
from core.docgen import CORPORA
from core.rag.chunking import ChunkConfig, chunk_corpus
from core.rag.embeddings import ConferAtlasEmbeddings

CHROMA_DIR = "chroma"
HNSW_CONFIG = {"space": "cosine", "batch_size": 2, "sync_threshold": 2}  # see the module docstring
LAYOUT = 2   # collections built before HNSW_CONFIG (layout 1) are rebuilt
_READ_ATTEMPTS = 5


@dataclass
class CollectionReport:
    collection: str
    corpus: str
    chunking: str
    added: int        # documents new to this collection
    updated: int      # documents whose chunks changed
    unchanged: int
    removed: int      # documents no longer in the corpus
    chunks: int       # chunks in the collection after the build


# Chroma calls on one index folder are made one at a time (the backend serves requests from a thread pool and
# the evaluation searches in parallel); each takes milliseconds, and builds never interleave with searches.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(Path(path).resolve()), threading.RLock())


def _retry(read):
    """Run a Chroma read, retrying briefly on "Nothing found on disk" (see the module docstring)."""
    for attempt in range(_READ_ATTEMPTS):
        try:
            return read()
        except Exception as exc:  # noqa: BLE001 - chromadb.errors.InternalError
            if "Nothing found on disk" not in str(exc) or attempt == _READ_ATTEMPTS - 1:
                raise
            time.sleep(0.25 * (attempt + 1))
    raise AssertionError("unreachable")


def collection_name(corpus: str, config: ChunkConfig, model: str) -> str:
    return f"{corpus}__{config.key}__{hashlib.sha1(model.encode()).hexdigest()[:8]}"


def _doc_hash(chunks: list[Document]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk.page_content.encode())
        digest.update(repr(sorted((k, v) for k, v in chunk.metadata.items() if k != "doc_hash")).encode())
    return digest.hexdigest()[:16]


class VectorIndex:
    def __init__(self, settings: Settings, embeddings: ConferAtlasEmbeddings, path: Path | None = None):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.embeddings = embeddings
        self.path = path or settings.index_dir / CHROMA_DIR
        self.lock = _lock_for(self.path)
        self.client = chromadb.PersistentClient(path=str(self.path),
                                                settings=ChromaSettings(anonymized_telemetry=False))

    @property
    def model(self) -> str:
        return self.embeddings.model

    def collection(self, corpus: str, config: ChunkConfig, create: bool = False):
        name = collection_name(corpus, config, self.model)
        with self.lock:
            return self._collection(name, corpus, config, create)

    def _collection(self, name: str, corpus: str, config: ChunkConfig, create: bool):
        if create:
            metadata = {"corpus": corpus, "chunking": config.key, "embedding_model": self.model, "layout": LAYOUT}
            collection = self.client.get_or_create_collection(name, metadata=metadata,
                                                              configuration={"hnsw": HNSW_CONFIG})
            if (collection.metadata or {}).get("layout") != LAYOUT:  # built before HNSW_CONFIG: rebuild it
                self.client.delete_collection(name)
                collection = self.client.create_collection(name, metadata=metadata,
                                                           configuration={"hnsw": HNSW_CONFIG})
            return collection
        try:
            return self.client.get_collection(name)
        except Exception:  # noqa: BLE001 - chromadb raises its own NotFoundError
            return None

    def get(self, corpus: str, config: ChunkConfig, include: list[str], where: dict | None = None) -> dict:
        """Read stored chunks (retried, see ``_retry``)."""
        collection = self.collection(corpus, config)
        if collection is None:
            return {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
        with self.lock:
            return _retry(lambda: collection.get(include=include, where=where))

    def query(self, corpus: str, config: ChunkConfig, vector: list[float], n: int, where: dict | None = None):
        """Nearest chunks to ``vector`` in one corpus (retried, see ``_retry``); None if not built."""
        collection = self.collection(corpus, config)
        if collection is None or not collection.count():
            return None
        with self.lock:
            n = min(n, collection.count())
            return _retry(lambda: collection.query(query_embeddings=[vector], n_results=n, where=where,
                                                   include=["documents", "metadatas", "distances"]))

    def is_built(self, config: ChunkConfig) -> bool:
        return all((c := self.collection(corpus, config)) is not None and c.count() for corpus in CORPORA)

    def build(self, units: list[Document], config: ChunkConfig) -> list[CollectionReport]:
        """Chunk ``units`` with ``config`` and bring every corpus collection up to date."""
        embed = self.embeddings.embed_documents if config.strategy == "semantic" else None
        chunks = chunk_corpus(units, config, embed=embed)
        by_corpus: dict[str, dict[str, list[Document]]] = {c: defaultdict(list) for c in CORPORA}
        for chunk in chunks:
            by_corpus[chunk.metadata["corpus"]][chunk.metadata["doc_id"]].append(chunk)

        with self.lock:  # one writer, and no reads while collections change
            reports = []
            for corpus in CORPORA:
                collection = self.collection(corpus, config, create=True)
                stored = _retry(lambda: collection.get(include=["metadatas"]))
                stored_hash = {m["doc_id"]: m["doc_hash"] for m in stored["metadatas"]}
                added = updated = unchanged = 0
                for doc_id, doc_chunks in by_corpus[corpus].items():
                    digest = _doc_hash(doc_chunks)
                    if stored_hash.get(doc_id) == digest:
                        unchanged += 1
                        continue
                    if doc_id in stored_hash:
                        collection.delete(where={"doc_id": doc_id})
                        updated += 1
                    else:
                        added += 1
                    vectors = self.embeddings.embed_documents([c.page_content for c in doc_chunks])
                    collection.add(ids=[c.metadata["chunk_id"] for c in doc_chunks], embeddings=vectors,
                                   documents=[c.page_content for c in doc_chunks],
                                   metadatas=[{**c.metadata, "doc_hash": digest} for c in doc_chunks])
                    if "dimension" not in (collection.metadata or {}):
                        collection.modify(metadata={**collection.metadata, "dimension": len(vectors[0])})
                gone = set(stored_hash) - set(by_corpus[corpus])
                for doc_id in gone:
                    collection.delete(where={"doc_id": doc_id})
                reports.append(CollectionReport(collection.name, corpus, config.key, added, updated, unchanged,
                                                len(gone), collection.count()))
            self._remove_other_models(config)
        return reports

    def _remove_other_models(self, config: ChunkConfig) -> None:
        """Collections for the same corpus and setting built with a different model are stale."""
        for collection in self.client.list_collections():
            meta = collection.metadata or {}
            if meta.get("chunking") == config.key and meta.get("embedding_model") != self.model:
                self.client.delete_collection(collection.name)

    def status(self) -> list[dict]:
        """Every collection with its corpus, setting, model, vector size and chunk count."""
        rows = []
        with self.lock:
            collections = self.client.list_collections()
            counts = {c.name: c.count() for c in collections}
        for collection in collections:
            meta = collection.metadata or {}
            rows.append({"collection": collection.name, "corpus": meta.get("corpus", ""),
                         "chunking": meta.get("chunking", ""), "embedding_model": meta.get("embedding_model", ""),
                         "dimension": meta.get("dimension", 0), "chunks": counts[collection.name]})
        return sorted(rows, key=lambda r: (r["chunking"], r["corpus"]))

    def reset(self) -> None:
        with self.lock:
            for collection in self.client.list_collections():
                self.client.delete_collection(collection.name)


def build(settings: Settings, units: list[Document], configs: list[ChunkConfig],
          embeddings: ConferAtlasEmbeddings) -> tuple[VectorIndex, list[CollectionReport], float]:
    """Build the index for each setting; return the index, the reports and the seconds taken."""
    started = time.monotonic()
    index = VectorIndex(settings, embeddings)
    reports = [report for config in configs for report in index.build(units, config)]
    return index, reports, time.monotonic() - started
