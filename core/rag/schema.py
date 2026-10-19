"""The one Document shape every loader returns, and its metadata.

Documents are LangChain ``Document`` objects, so splitters, embeddings and Chroma accept them as they
are. Every document carries every key in ``METADATA_KEYS``, with a scalar value: Chroma rejects None,
so "not applicable" is ``""`` for text and ``0`` for numbers (no event, page 0 = not a page).

One document is one natural unit of its file: a PDF page, a Word section, a slide or a Markdown
section. Its text is Markdown-like for every format ("# " title, "## " headings, "1. " steps,
"- " bullets, "|" tables), so chunking (Step 4) can treat all formats the same way.
"""
from __future__ import annotations

from langchain_core.documents import Document

from core.docgen import DocRecord

METADATA_KEYS = (
    "doc_id", "corpus", "source_type", "title", "source_path",
    "event_id", "event_type", "city", "year", "sponsor_id",
    "page", "slide", "section",
)


def make_document(text: str, record: DocRecord, *, page: int = 0, slide: int = 0, section: str = "") -> Document:
    """A Document for one unit of ``record``'s file, with every metadata key filled."""
    metadata = {
        "doc_id": record.doc_id, "corpus": record.corpus, "source_type": record.source_type,
        "title": record.title, "source_path": record.path,
        "event_id": record.event_id, "event_type": record.event_type, "city": record.city,
        "year": record.year or 0, "sponsor_id": record.sponsor_id,
        "page": page, "slide": slide, "section": section,
    }
    return Document(page_content=text, metadata=metadata)


def citation(metadata: dict) -> str:
    """How an answer cites this text: "[Sponsorship packages 2026, p. 2]"."""
    where = (f"p. {metadata['page']}" if metadata.get("page") else
             f"slide {metadata['slide']}" if metadata.get("slide") else metadata.get("section", ""))
    return f"[{metadata['title']}, {where}]" if where else f"[{metadata['title']}]"
