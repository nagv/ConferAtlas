"""The embedding model: turns text into vectors for search.

``build_embeddings(settings)`` returns a LangChain ``Embeddings`` for the configured provider, wrapped so
that:
- questions get the model's task instruction when the model expects one (Qwen3-Embedding is trained
  with "Instruct: <task>\\nQuery: <question>" for queries; documents are embedded as they are);
- every vector is cached on disk by model and exact text, so re-indexing, RAG Lab experiments and
  semantic chunking never pay twice for the same text.

The "fake" provider gives deterministic hashed bag-of-words vectors, so tests and the offline demo
run without a key. One model embeds both documents and questions: vectors from different models are
not comparable, so the index records which model built it.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from pathlib import Path

import numpy as np
from langchain_core.embeddings import Embeddings

from core.config import Settings

QUERY_TASK = "Given a question about past conferences, sponsorships or event operations, retrieve passages that answer it"
BATCH_SIZE = 64       # texts per embedding request
FAKE_DIMENSIONS = 256


class EmbeddingsNotConfiguredError(RuntimeError):
    """Raised when no embedding provider is configured (document search is off)."""


class HashEmbeddings(Embeddings):
    """Offline stand-in: words hashed into a fixed-size count vector, normalised."""

    def __init__(self, dimensions: int = FAKE_DIMENSIONS):
        self.dimensions = dimensions

    def _one(self, text: str) -> list[float]:
        vector = np.zeros(self.dimensions)
        for word in re.findall(r"\w+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
        norm = np.linalg.norm(vector)
        return (vector / norm if norm else vector).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._one(text)


class _VectorCache:
    """model + text -> vector, in one SQLite file (safe to share between threads of one process)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB)")
        self._lock = threading.Lock()

    @staticmethod
    def key(model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()

    def get_many(self, keys: list[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        with self._lock:
            for start in range(0, len(keys), 500):
                part = keys[start:start + 500]
                rows = self._db.execute(f"SELECT key, vector FROM vectors WHERE key IN ({','.join('?' * len(part))})",
                                        part).fetchall()
                found.update({k: np.frombuffer(v, dtype=np.float32).tolist() for k, v in rows})
        return found

    def put_many(self, items: dict[str, list[float]]) -> None:
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?)",
                                 [(k, np.asarray(v, dtype=np.float32).tobytes()) for k, v in items.items()])
            self._db.commit()


class ConferAtlasEmbeddings(Embeddings):
    """The provider's embeddings plus query instructions, batching, a disk cache and call counting."""

    def __init__(self, inner: Embeddings, model: str, cache_path: Path | None = None,
                 query_instruction: str | None = None):
        self.inner, self.model, self.query_instruction = inner, model, query_instruction
        self.cache = _VectorCache(cache_path) if cache_path else None
        self.texts_embedded = 0  # texts actually sent to the provider (cache misses), for reports

    def _embed(self, texts: list[str]) -> list[list[float]]:
        keys = [_VectorCache.key(self.model, t) for t in texts]
        cached = self.cache.get_many(keys) if self.cache else {}
        missing = [i for i, k in enumerate(keys) if k not in cached]
        fresh: dict[str, list[float]] = {}
        for start in range(0, len(missing), BATCH_SIZE):
            batch = missing[start:start + BATCH_SIZE]
            vectors = self.inner.embed_documents([texts[i] for i in batch])
            fresh.update({keys[i]: v for i, v in zip(batch, vectors)})
            self.texts_embedded += len(batch)
        if self.cache and fresh:
            self.cache.put_many(fresh)
        return [cached.get(k) or fresh[k] for k in keys]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(list(texts))

    def embed_query(self, text: str) -> list[float]:
        if self.query_instruction:
            text = f"Instruct: {self.query_instruction}\nQuery: {text}"
        return self._embed([text])[0]


def needs_query_instruction(model: str) -> bool:
    return "qwen3-embedding" in model.lower()


def build_embeddings(settings: Settings, cache: bool = True) -> ConferAtlasEmbeddings:
    """The configured embedding model, ready for indexing and search."""
    if not settings.embeddings_configured:
        raise EmbeddingsNotConfiguredError(
            "Document search is off: set EMBEDDING_PROVIDER and EMBEDDING_MODEL (and the provider's key) in .env, "
            "then restart the app. See docs/week2/build-guide.md, section 0.2.")
    provider, model = settings.embedding_provider, settings.embedding_model or "hash"
    if provider == "fake":
        inner: Embeddings = HashEmbeddings()
        model = f"fake-hash-{FAKE_DIMENSIONS}"
    elif provider == "google_genai":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        inner = GoogleGenerativeAIEmbeddings(model=model, google_api_key=settings.embedding_api_key)
    else:  # nebius, fireworks, openai: the OpenAI embeddings API
        from langchain_openai import OpenAIEmbeddings
        # check_embedding_ctx_length=False sends plain text: otherwise the client sends OpenAI token ids,
        # which other providers' models can't read.
        inner = OpenAIEmbeddings(model=model, base_url=settings.embedding_base_url, api_key=settings.embedding_api_key,
                                 check_embedding_ctx_length=False, chunk_size=BATCH_SIZE,
                                 timeout=settings.llm_timeout_s, max_retries=settings.llm_max_retries)
    return ConferAtlasEmbeddings(inner, model,
                                 cache_path=settings.index_dir / "embedding_cache.sqlite" if cache else None,
                                 query_instruction=QUERY_TASK if needs_query_instruction(model) else None)
