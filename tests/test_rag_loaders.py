"""Loaders: every format becomes clean, Markdown-like Documents with complete metadata."""
from collections import Counter

import pytest

from core.config import DEFAULT_DATA_DIR
from core.docgen import load_manifest
from core.rag.loaders import clean_text, load_corpus
from core.rag.schema import METADATA_KEYS, citation


@pytest.fixture(scope="module")
def docs():
    return load_corpus(DEFAULT_DATA_DIR)


def _of(docs, doc_id):
    return [d for d in docs if d.metadata["doc_id"] == doc_id]


def test_every_manifest_document_loads_with_complete_scalar_metadata(docs):
    loaded = Counter(d.metadata["doc_id"] for d in docs)
    assert set(loaded) == {r.doc_id for r in load_manifest(DEFAULT_DATA_DIR)}
    for d in docs:
        assert tuple(d.metadata) == METADATA_KEYS
        assert all(isinstance(v, (str, int)) for v in d.metadata.values())  # Chroma rejects None
        assert d.page_content.strip()


def test_pdf_pages_drop_headers_footers_and_ligatures(docs):
    pages = _of(docs, "report_EV008")
    assert [p.metadata["page"] for p in pages] == [1, 2]
    text = "\n".join(p.page_content for p in pages)
    assert "Page 1 of" not in text and "Confidential" not in text
    assert "ConferAtlas Events | San Francisco" not in text           # the repeated page header
    assert "ﬁ" not in text and "confirmed registrations" in text  # NFKC fixed the "fi" ligature
    assert text.startswith("# San Francisco DevSecOps Forum 2025")
    assert "## 4. Venue and operations" in text


def test_pdf_tables_become_markdown_without_duplicate_loose_text(docs):
    page = _of(docs, "report_EV008")[0].page_content
    assert "| Sponsor | Package | Leads | Target | Attainment |" in page
    assert page.count("Attainment") == 1  # the table's own words are not repeated as loose lines
    assert "| Bluefin Software |" in page
    pricing = _of(docs, "pricing_2026")[0].page_content
    assert "| Premier Booth | in-person / hybrid | $51,000 |" in pricing
    assert "| Lead | target" not in pricing  # a wrapped header cell is not a table of its own


def test_word_sections_have_numbered_steps_and_skip_the_page_header(docs):
    sections = _of(docs, "sop_booth_logistics")
    assert [s.metadata["section"] for s in sections] == ["Purpose", "Move-in", "Lead retrieval", "Teardown"]
    lead = sections[2].page_content
    assert lead.startswith("## Lead retrieval\n1. Badge scanners must be charged and tested by 7:00 AM")
    assert "2. Each sponsor receives a lead export within 48 hours" in lead
    assert all("Operations manual" not in s.page_content for s in sections)


def test_slides_include_title_bullets_and_speaker_notes(docs):
    slides = _of(docs, "deck_SP016")
    assert [s.metadata["slide"] for s in slides] == [1, 2, 3, 4, 5, 6]
    last = slides[-1].page_content
    assert last.startswith("## Proposal for next year\n- ") and "Speaker notes: Our proposal to" in last


def test_markdown_splits_by_section(docs):
    sections = _of(docs, "abstracts_EV001")
    assert sections[0].metadata["section"] == "Overview" and len(sections) == 11
    assert sections[1].page_content.startswith("## ") and "**Speaker:**" in sections[1].page_content


@pytest.mark.parametrize("raw, clean", [
    ("conﬁrmed", "confirmed"),
    ("regis-\ntration desk", "registration desk"),
    ("a  b \t c", "a b c"),
    ("one\n\n\n\ntwo  ", "one\n\ntwo"),
])
def test_clean_text(raw, clean):
    assert clean_text(raw) == clean


def test_citation_names_the_document_and_location(docs):
    assert citation(_of(docs, "pricing_2026")[0].metadata) == "[Sponsorship packages 2026, p. 1]"
    assert citation(_of(docs, "deck_SP016")[2].metadata).endswith(", slide 3]")
    assert citation(_of(docs, "sop_booth_logistics")[3].metadata) == "[SOP-03 Sponsor booth logistics, Teardown]"
