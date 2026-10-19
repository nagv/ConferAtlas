"""Search the indexed documents: dense, BM25 or hybrid, with filters, re-ranking and a refusal threshold.

For one question:
1. Dense: the question is embedded (with the model's query instruction) and each corpus collection in
   Chroma returns its nearest chunks. Score = cosine similarity (1 - distance).
2. BM25: keyword scores over the same chunks, so exact names ("Quillon Security", "7:00 AM") count.
   One BM25 index covers every corpus, so word statistics (how rare a word is) are shared and
   scores from different corpora can be compared; per-corpus indexes over-rewarded small corpora.
3. Hybrid: both scores are scaled 0..1 across the candidates and mixed, ``alpha`` x dense +
   (1 - alpha) x BM25. The best ``candidates`` (20) go on.
4. Re-ranking: a small cross-encoder (FlashRank, MiniLM) reads the question with each candidate and
   scores how well it answers it; the best ``top_k`` (5) are kept.
5. Refusal: if the best final score is below ``threshold`` (re-rank score) or, without re-ranking,
   the best cosine similarity is below ``dense_threshold``, no evidence is returned, so the answer can
   say "I couldn't find this in our documents" instead of guessing. This catches off-topic
   questions. It cannot catch a question about a real event whose answer is simply not written down
   (a related chunk always scores well), so the answer step also checks that the evidence answers
   the question.

Filters come from the sidebar (event type, city, year, event) and apply to chunks about an event;
chunks with no event (pricing, SOPs, decks) always pass, because they apply to every event. Every
candidate keeps all its scores and its rank before and after re-ranking, for the RAG Lab inspector.
Retrieved text is data from documents: it is returned for citation, never treated as instructions.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from core.docgen import CORPORA
from core.rag.chunking import PRESETS, ChunkConfig, chunk_body
from core.rag.index import VectorIndex
from core.rag.schema import citation

MODES = ("dense", "bm25", "hybrid")
RERANK_MODEL = "ms-marco-MiniLM-L-12-v2"
FILTER_KEYS = ("event_type", "city", "year", "event_id")
_STOPWORDS = set("""a an and are as at be by did do does for from had has have how i in is it its
of on or our than that the their them there these this to was were what when where which who why
will with would you your about after before can could""".split())


@dataclass(frozen=True)
class RetrievalConfig:
    chunking: str = "structure"   # a key of chunking.PRESETS
    chunk_size: int | None = None  # RAG Lab: override the preset's size (that index must be built)
    overlap: int | None = None     # RAG Lab: override the preset's overlap (fixed strategy)
    mode: str = "hybrid"          # dense, bm25 or hybrid
    alpha: float = 0.5            # hybrid: weight of the dense score (1 - alpha for BM25)
    candidates: int = 20          # passed to re-ranking
    top_k: int = 5                # returned as evidence
    rerank: bool = True
    threshold: float = 0.05       # minimum re-rank score; below it the search refuses
    dense_threshold: float = 0.45  # without re-ranking: minimum cosine similarity of the best chunk

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if self.chunking not in PRESETS:
            raise ValueError(f"chunking must be one of {', '.join(PRESETS)}")
        if not 0 <= self.alpha <= 1 or self.top_k < 1 or self.candidates < self.top_k:
            raise ValueError("alpha must be 0..1, top_k at least 1 and candidates at least top_k")

    @property
    def chunk_config(self) -> ChunkConfig:
        """The chunking setting whose index is searched: the preset, with any RAG Lab overrides."""
        preset = PRESETS[self.chunking]
        if self.chunk_size is None and self.overlap is None:
            return preset
        return ChunkConfig(preset.strategy, chunk_size=self.chunk_size or preset.chunk_size,
                           overlap=preset.overlap if self.overlap is None else self.overlap,
                           contextual=preset.contextual)

    @property
    def score_name(self) -> str:
        return "rerank" if self.rerank else self.mode


@dataclass
class Candidate:
    chunk_id: str
    doc_id: str
    corpus: str
    title: str
    citation: str
    text: str                 # the chunk body, without its contextual header
    metadata: dict
    dense: float = 0.0        # cosine similarity, 0 when the chunk was not a dense hit
    bm25: float = 0.0         # raw BM25 score
    hybrid: float = 0.0       # mixed, scaled score
    rerank: float | None = None
    rank_before: int = 0      # 1-based rank before re-ranking
    rank_after: int = 0       # 1-based rank after re-ranking (0 = dropped)

    def score(self, config: RetrievalConfig) -> float:
        """The score used for the refusal check: the re-rank score, or else the cosine similarity
        (BM25 alone has no absolute scale, so it refuses only when nothing matches)."""
        if config.rerank and self.rerank is not None:
            return self.rerank
        return 1.0 if config.mode == "bm25" else self.dense


@dataclass
class SearchResult:
    question: str
    config: RetrievalConfig
    corpora: list[str]
    candidates: list[Candidate]        # everything considered, in rank-before order
    hits: list[Candidate]              # the evidence, best first (empty when refused)
    refused: bool
    best_score: float
    seconds: dict = field(default_factory=dict)  # embed, dense, bm25, rerank, total

    def as_dict(self) -> dict:
        out = asdict(self)
        out["config"] = asdict(self.config)
        return out


def tokenize(text: str) -> list[str]:
    """Lower-case words for BM25. Colons and full stops are kept inside a word (7:00, 2.5) but trimmed from
    its ends, so "2024:" in a heading matches "2024" in a question, and "event." matches "event"."""
    words = (w.strip(".:") for w in re.findall(r"[a-z0-9$%:.]+", text.lower().replace(",", "")))
    return [w for w in words if w and w not in _STOPWORDS]


def _matches(meta: dict, filters: dict) -> bool:
    for key, values in filters.items():
        if not values or key not in FILTER_KEYS:
            continue
        value = meta.get(key)
        if value in ("", 0, None):
            continue  # not about one event: pricing, SOPs and decks apply everywhere
        if value not in values:
            return False
    return True


def _where(filters: dict) -> dict | None:
    """The same filter as ``_matches``, in Chroma's query language."""
    clauses = []
    for key in FILTER_KEYS:
        values = [v for v in (filters.get(key) or [])]
        if values:
            empty = 0 if key == "year" else ""
            clauses.append({"$or": [{key: empty}, {key: {"$in": values}}]})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _scale(values: np.ndarray) -> np.ndarray:
    low, high = float(values.min()), float(values.max())
    return np.zeros_like(values) if high - low < 1e-12 else (values - low) / (high - low)


class Retriever:
    """Searches a ``VectorIndex``. Keeps BM25 indexes and the re-ranker in memory between questions."""

    def __init__(self, index: VectorIndex, rerank_cache: Path | None = None):
        self.index = index
        self._bm25: dict[str, tuple[tuple, object, list[str], list[dict], list[str]]] = {}
        self._ranker = None
        self._rerank_cache = rerank_cache or index.path.parent / "flashrank"
        self._lock = threading.Lock()

    # --- pieces ----------------------------------------------------------------------------------

    def _bm25_for(self, config: ChunkConfig):
        """One BM25 index over every corpus collection of this chunking setting (rebuilt if counts change)."""
        collections = [self.index.collection(corpus, config) for corpus in CORPORA]
        counts = tuple(c.count() if c is not None else 0 for c in collections)
        cached = self._bm25.get(config.key)
        if cached is None or cached[0] != counts:
            from rank_bm25 import BM25Okapi
            ids, metas, docs = [], [], []
            for corpus in CORPORA:
                got = self.index.get(corpus, config, include=["documents", "metadatas"])
                ids += got["ids"]
                metas += got["metadatas"]
                docs += got["documents"]
            if not ids:
                return None
            cached = (counts, BM25Okapi([tokenize(d) or ["_"] for d in docs]), ids, metas, docs)
            with self._lock:
                self._bm25[config.key] = cached
        return cached

    def ranker(self):
        if self._ranker is None:
            from flashrank import Ranker
            self._rerank_cache.mkdir(parents=True, exist_ok=True)
            self._ranker = Ranker(model_name=RERANK_MODEL, cache_dir=str(self._rerank_cache))
        return self._ranker

    # --- search ---------------------------------------------------------------------------------

    def search(self, question: str, config: RetrievalConfig = RetrievalConfig(),
               corpora: list[str] | None = None, filters: dict | None = None) -> SearchResult:
        started = time.monotonic()
        corpora = [c for c in (corpora or CORPORA) if c in CORPORA]
        filters = filters or {}
        chunking = config.chunk_config
        seconds: dict[str, float] = {}
        pool: dict[str, Candidate] = {}

        def candidate(chunk_id: str, meta: dict, document: str) -> Candidate:
            if chunk_id not in pool:
                pool[chunk_id] = Candidate(chunk_id, meta["doc_id"], meta["corpus"], meta["title"], citation(meta),
                                           chunk_body_text(document, meta), meta)
            return pool[chunk_id]

        if config.mode in ("dense", "hybrid"):
            t = time.monotonic()
            vector = self.index.embeddings.embed_query(question)
            seconds["embed"] = time.monotonic() - t
            t = time.monotonic()
            where = _where(filters)
            for corpus in corpora:
                got = self.index.query(corpus, chunking, vector, config.candidates, where)
                if got is None:
                    continue
                for cid, meta, doc, dist in zip(got["ids"][0], got["metadatas"][0], got["documents"][0],
                                                got["distances"][0]):
                    candidate(cid, meta, doc).dense = 1.0 - float(dist)
            seconds["dense"] = time.monotonic() - t

        if config.mode in ("bm25", "hybrid"):
            t = time.monotonic()
            terms = tokenize(question)
            cached = self._bm25_for(chunking) if terms else None
            if cached is not None:
                _, bm25, ids, metas, docs = cached
                scores = bm25.get_scores(terms)
                order = [i for i in np.argsort(-scores) if scores[i] > 0 and metas[i]["corpus"] in corpora
                         and _matches(metas[i], filters)]
                for i in order[:config.candidates]:
                    candidate(ids[i], metas[i], docs[i]).bm25 = float(scores[i])
            seconds["bm25"] = time.monotonic() - t

        candidates = list(pool.values())
        if candidates:
            dense = _scale(np.array([c.dense for c in candidates]))
            keyword = _scale(np.array([c.bm25 for c in candidates]))
            alpha = {"dense": 1.0, "bm25": 0.0, "hybrid": config.alpha}[config.mode]
            for c, d, k in zip(candidates, dense, keyword):
                c.hybrid = float(alpha * d + (1 - alpha) * k) if config.mode != "dense" else c.dense
        candidates.sort(key=lambda c: (-c.hybrid, c.chunk_id))
        candidates = candidates[:config.candidates]
        for rank, c in enumerate(candidates, start=1):
            c.rank_before = rank

        if config.rerank and candidates:
            from flashrank import RerankRequest
            t = time.monotonic()
            passages = [{"id": i, "text": f"{c.metadata.get('header', '')}\n{c.text}"} for i, c in enumerate(candidates)]
            for item in self.ranker().rerank(RerankRequest(query=question, passages=passages)):
                candidates[int(item["id"])].rerank = float(item["score"])
            seconds["rerank"] = time.monotonic() - t
            ordered = sorted(candidates, key=lambda c: (-(c.rerank or 0.0), c.rank_before))
        else:
            ordered = list(candidates)
        for rank, c in enumerate(ordered, start=1):
            c.rank_after = rank if rank <= config.top_k else 0

        best = ordered[0].score(config) if ordered else 0.0
        floor = config.threshold if config.rerank else (0.0 if config.mode == "bm25" else config.dense_threshold)
        refused = not ordered or best < floor
        hits = [] if refused else ordered[:config.top_k]
        seconds["total"] = time.monotonic() - started
        return SearchResult(question, config, corpora, candidates, hits, refused, best,
                            {k: round(v, 3) for k, v in seconds.items()})


def chunk_body_text(document: str, meta: dict) -> str:
    from langchain_core.documents import Document
    return chunk_body(Document(page_content=document, metadata=meta))
