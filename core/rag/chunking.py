"""Cut loaded documents into chunks for search, three ways, so the strategies can be compared.

- fixed       the baseline: windows of ``chunk_size`` tokens with ``overlap``, cut wherever the count
              falls, through sentences and tables.
- structure   follows the document: one chunk per section (heading, Word section, slide), joined
              across PDF page breaks. A section too big for one chunk is split between paragraphs,
              and a big table between rows, repeating its header row. Text before the first
              heading (a title block, a one-line overview) merges into the next section when it is
              smaller than ``min_tokens``; a section with its own heading always stays on its own.
- semantic    splits where the topic changes: each sentence (with its neighbours) is embedded, and
              a chunk ends where consecutive sentences are least similar (above the document's
              ``breakpoint_percentile`` of distances) or the chunk would exceed ``chunk_size``. Headings stay
              with the text after them, step numbers and bullets with their sentence, and a topic
              change never leaves a chunk under ``min_tokens``.
              Needs an ``embed`` function (Step 5 supplies the real model; tests use a fake one).

With ``contextual=True`` every chunk starts with a header naming its document, section and page or
slide, so a chunk that only says "the venue" still matches a question about Boston. The header is
kept in the metadata too, so the app can show the chunk body without it.

Token counts use tiktoken's cl100k encoding (close to, not identical with, the Qwen tokenizer), or
about four characters per token if tiktoken is unavailable.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from langchain_core.documents import Document

STRATEGIES = ("fixed", "structure", "semantic")
CHUNK_KEYS = ("chunk_id", "chunk_index", "strategy", "tokens", "header")
Embed = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class ChunkConfig:
    strategy: str = "structure"
    chunk_size: int = 400            # tokens
    overlap: int = 0                 # tokens, fixed strategy only
    contextual: bool = True
    min_tokens: int = 60             # structure: heading-less text this small merges into the next section;
                                     # semantic: a topic change never ends a chunk smaller than this
    breakpoint_percentile: float = 90.0  # semantic: distance percentile that ends a chunk

    def __post_init__(self):
        if self.strategy not in STRATEGIES:
            raise ValueError(f"Unknown chunking strategy '{self.strategy}'; use one of {', '.join(STRATEGIES)}")
        if self.chunk_size < 50 or not 0 <= self.overlap < self.chunk_size:
            raise ValueError("chunk_size must be at least 50 tokens and overlap smaller than chunk_size")

    @property
    def key(self) -> str:
        """A short name for this setting, used in index collection names: "structure_400_ctx"."""
        overlap = f"_o{self.overlap}" if self.strategy == "fixed" and self.overlap else ""
        return f"{self.strategy}_{self.chunk_size}{overlap}{'_ctx' if self.contextual else ''}"


# The settings compared in the Step 10 evaluation.
PRESETS = {
    "fixed": ChunkConfig("fixed", chunk_size=500, overlap=50),
    "structure": ChunkConfig("structure", chunk_size=400),
    "semantic": ChunkConfig("semantic", chunk_size=400),
}


# --- tokens ---------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - not installed, or the encoding file can't be downloaded
        return None


def count_tokens(text: str) -> int:
    enc = _encoding()
    return len(enc.encode(text)) if enc is not None else max(1, len(text) // 4) if text else 0


def _token_windows(text: str, size: int, overlap: int) -> list[str]:
    enc = _encoding()
    if enc is None:  # character windows of the same approximate size
        step, width = (size - overlap) * 4, size * 4
        return [text[i:i + width] for i in range(0, max(1, len(text) - overlap * 4), step)]
    ids = enc.encode(text)
    return [enc.decode(ids[i:i + size]) for i in range(0, max(1, len(ids) - overlap), size - overlap)]


# --- blocks: paragraphs with their location ---------------------------------------------------------

@dataclass
class _Block:
    text: str
    page: int
    slide: int
    section: str
    unit: int        # index of the loaded unit it came from
    heading: bool    # starts a new section


def _blocks(units: list[Document]) -> list[_Block]:
    blocks: list[_Block] = []
    for i, unit in enumerate(units):
        meta = unit.metadata
        section = meta["section"]
        for part in re.split(r"\n\s*\n", unit.page_content):
            part = part.strip()
            if not part:
                continue
            heading = part.startswith("## ")
            if heading:
                section = part.splitlines()[0][3:].strip()
            if (blocks and not heading and meta["source_type"] == "pdf" and blocks[-1].unit != i
                    and not re.search(r"[.!?:|]$", blocks[-1].text) and part[:1].islower()):
                blocks[-1].text += " " + part  # a sentence cut by a page break
                continue
            blocks.append(_Block(part, meta["page"], meta["slide"], section, i, heading))
    return blocks


def _header(meta: dict, section: str, page: int, slide: int) -> str:
    parts = [f"Document: {meta['title']}"]
    if section:
        parts.append(f"Section: {section}")
    if page:
        parts.append(f"Page {page}")
    if slide:
        parts.append(f"Slide {slide}")
    return " | ".join(parts)


def _chunk(body: str, base: dict, config: ChunkConfig, index: int, *, page: int, slide: int, section: str) -> Document:
    header = _header(base, section, page, slide) if config.contextual else ""
    text = f"{header}\n\n{body}" if header else body
    metadata = {**base, "page": page, "slide": slide, "section": section,
                "chunk_id": f"{base['doc_id']}:{config.key}:{index:03d}", "chunk_index": index,
                "strategy": config.strategy, "tokens": count_tokens(text), "header": header}
    return Document(page_content=text, metadata=metadata)


# --- structure --------------------------------------------------------------------------------------

def _split_table(table: str, size: int) -> list[str]:
    lines = table.splitlines()
    head, rows, parts, current = lines[:2], lines[2:], [], []
    for row in rows:
        if current and count_tokens("\n".join(head + current + [row])) > size:
            parts.append("\n".join(head + current))
            current = []
        current.append(row)
    return parts + ["\n".join(head + current)] if current else parts


def _split_long(text: str, size: int) -> list[str]:
    if text.lstrip().startswith("|"):
        return _split_table(text, size)
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=0, length_function=count_tokens,
                                              separators=["\n", ". ", "; ", ", ", " ", ""])
    return splitter.split_text(text)


def _structure(units: list[Document], config: ChunkConfig) -> list[tuple[str, _Block]]:
    """(body, first block) per chunk."""
    sections: list[list[_Block]] = []
    for block in _blocks(units):
        new_slide = sections and units[block.unit].metadata["slide"] and sections[-1][-1].unit != block.unit
        if not sections or block.heading or new_slide:
            sections.append([block])
        else:
            sections[-1].append(block)

    is_slides = units[0].metadata["source_type"] == "pptx"
    merged: list[list[_Block]] = []
    carry: list[_Block] = []
    for section in sections:
        section = carry + section
        carry = []
        headless = not any(b.heading for b in section)
        if headless and not is_slides and section is not sections[-1] \
                and count_tokens("\n\n".join(b.text for b in section)) < config.min_tokens:
            carry = section
            continue
        merged.append(section)
    if carry:
        merged.append(carry)

    def emit(blocks: list[_Block]) -> tuple[str, _Block]:
        # Located at the first block, labelled with the first heading (an intro merged into a section
        # belongs to that section).
        heading = next((b for b in blocks if b.heading), blocks[0])
        first = blocks[0]
        return "\n\n".join(b.text for b in blocks), _Block("", first.page, first.slide, heading.section, first.unit,
                                                             first.heading)

    out: list[tuple[str, _Block]] = []
    for section in merged:
        current: list[_Block] = []
        for block in section:
            pieces = [block.text] if count_tokens(block.text) <= config.chunk_size else _split_long(
                block.text, config.chunk_size)
            for piece in pieces:
                candidate = "\n\n".join([b.text for b in current] + [piece])
                if current and count_tokens(candidate) > config.chunk_size:
                    out.append(emit(current))
                    current = []
                current.append(_Block(piece, block.page, block.slide, block.section, block.unit, block.heading))
        if current:
            out.append(emit(current))
    return out


# --- fixed ----------------------------------------------------------------------------------------------

def _fixed(units: list[Document], config: ChunkConfig) -> list[tuple[str, _Block]]:
    blocks = _blocks(units)
    text, starts = "", []
    for block in blocks:
        starts.append(len(text) + (2 if text else 0))
        text = f"{text}\n\n{block.text}" if text else block.text
    out, cursor = [], 0
    for window in _token_windows(text, config.chunk_size, config.overlap):
        body = window.strip()
        if not body:
            continue
        found = text.find(body[:80], max(0, cursor - config.overlap * 8))
        cursor = found if found >= 0 else cursor
        first = blocks[max(0, int(np.searchsorted(starts, cursor, side="right")) - 1)]
        out.append((body, first))
    return out


# --- semantic -------------------------------------------------------------------------------------------

def _sentences(blocks: Iterable[_Block]) -> list[_Block]:
    out = []
    for block in blocks:
        if block.text.lstrip().startswith("|"):
            out.append(block)  # tables stay whole
            continue
        lines = block.text.splitlines()
        if block.heading:  # the heading line is one unit; the rest of the block (Word sections, slides) splits
            out.append(_Block(lines[0], block.page, block.slide, block.section, block.unit, True))
            lines = lines[1:]
        for line in lines:
            marker = re.match(r"\s*(\d+\. |- )", line)  # a step number or bullet stays with its sentence
            prefix, rest = (marker.group(0).strip() + " ", line[marker.end():]) if marker else ("", line)
            for n, sentence in enumerate(re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"$])", rest)):
                if sentence.strip():
                    text = (prefix if n == 0 else "") + sentence.strip()
                    out.append(_Block(text, block.page, block.slide, block.section, block.unit, False))
    return out


def _semantic(units: list[Document], config: ChunkConfig, embed: Embed) -> list[tuple[str, _Block]]:
    sentences = _sentences(_blocks(units))
    if len(sentences) < 3:
        return [("\n".join(s.text for s in sentences), sentences[0])] if sentences else []
    windows = [" ".join(s.text for s in sentences[max(0, i - 1):i + 2]) for i in range(len(sentences))]
    vectors = np.asarray(embed(windows), dtype=float)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    distances = 1.0 - np.sum(vectors[:-1] * vectors[1:], axis=1)
    threshold = float(np.percentile(distances, config.breakpoint_percentile))

    out, current = [], [sentences[0]]
    for i, sentence in enumerate(sentences[1:], start=1):
        slide_change = sentence.slide and sentence.unit != current[-1].unit
        too_big = count_tokens(" ".join(s.text for s in current + [sentence])) > config.chunk_size
        only_headings = all(s.heading for s in current)  # a heading stays with the text that follows it
        big_enough = count_tokens(_join(current)) >= config.min_tokens  # no tiny fragments on a topic change
        topic_change = distances[i - 1] > threshold and not only_headings and big_enough
        if too_big or slide_change or (not only_headings and (topic_change or sentence.heading)):
            out.append((_join(current), current[0]))
            current = []
        current.append(sentence)
    out.append((_join(current), current[0]))
    return out


def _own_line(block: _Block) -> bool:
    return block.heading or bool(re.match(r"(#+ |\||- |\d+\. |Speaker notes:)", block.text))


def _join(sentences: list[_Block]) -> str:
    """Prose sentences rejoin with spaces; headings, table rows, steps and bullets keep their own lines."""
    text = sentences[0].text
    for prev, s in zip(sentences, sentences[1:]):
        text += ("\n" if _own_line(s) or _own_line(prev) else " ") + s.text
    return text


# --- entry points ---------------------------------------------------------------------------------------

def chunk_document(units: list[Document], config: ChunkConfig, embed: Embed | None = None) -> list[Document]:
    """Chunks for one document, given its loaded units in order."""
    if not units:
        return []
    if config.strategy == "semantic":
        if embed is None:
            raise ValueError("Semantic chunking needs an embed function (an embedding model).")
        pieces = _semantic(units, config, embed)
    else:
        pieces = _fixed(units, config) if config.strategy == "fixed" else _structure(units, config)
    base = {k: v for k, v in units[0].metadata.items() if k not in ("page", "slide", "section")}
    return [_chunk(body, base, config, i, page=first.page, slide=first.slide, section=first.section)
            for i, (body, first) in enumerate(pieces)]


def chunk_corpus(units: list[Document], config: ChunkConfig, embed: Embed | None = None) -> list[Document]:
    """Chunks for every document in ``units`` (as ``load_corpus`` returns them)."""
    by_doc: dict[str, list[Document]] = {}
    for unit in units:
        by_doc.setdefault(unit.metadata["doc_id"], []).append(unit)
    return [chunk for doc_units in by_doc.values() for chunk in chunk_document(doc_units, config, embed)]


def chunk_body(chunk: Document) -> str:
    """The chunk text without its contextual header."""
    header = chunk.metadata.get("header", "")
    return chunk.page_content[len(header):].lstrip("\n") if header else chunk.page_content


def chunk_stats(chunks: list[Document]) -> pd.DataFrame:
    """One row per chunk: corpus, document, strategy and token count, for the RAG Lab histogram."""
    return pd.DataFrame([{"corpus": c.metadata["corpus"], "doc_id": c.metadata["doc_id"],
                          "strategy": c.metadata["strategy"], "tokens": c.metadata["tokens"]} for c in chunks],
                        columns=["corpus", "doc_id", "strategy", "tokens"])
