"""Chunking: three strategies over the loaded corpus. No text is lost, sizes are respected, structure
is followed, and every chunk keeps its metadata and contextual header."""
import hashlib
import re

import numpy as np
import pytest
from langchain_core.documents import Document

from core.config import DEFAULT_DATA_DIR
from core.docgen import DocRecord
from core.rag import chunking
from core.rag.chunking import PRESETS, ChunkConfig, chunk_body, chunk_corpus, chunk_document, count_tokens
from core.rag.loaders import load_corpus
from core.rag.schema import METADATA_KEYS, make_document


def fake_embed(texts):
    """Deterministic bag-of-hashed-words vectors: similar wording -> similar vectors."""
    out = []
    for text in texts:
        v = np.zeros(128)
        for word in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 128] += 1
        out.append(v)
    return out


@pytest.fixture(scope="module")
def units():
    return load_corpus(DEFAULT_DATA_DIR)


@pytest.fixture(scope="module")
def chunks(units):
    return {name: chunk_corpus(units, cfg, embed=fake_embed) for name, cfg in PRESETS.items()}


def _of(chunks, doc_id):
    return [c for c in chunks if c.metadata["doc_id"] == doc_id]


def test_config_validates_and_names_itself():
    assert PRESETS["fixed"].key == "fixed_500_o50_ctx"
    assert PRESETS["structure"].key == "structure_400_ctx"
    assert ChunkConfig("semantic", 300, contextual=False).key == "semantic_300"
    with pytest.raises(ValueError):
        ChunkConfig("paragraph")
    with pytest.raises(ValueError):
        ChunkConfig("fixed", chunk_size=100, overlap=100)


@pytest.mark.parametrize("strategy", list(PRESETS))
def test_no_text_is_lost_and_metadata_is_complete(units, chunks, strategy):
    words = lambda text: set(re.findall(r"[A-Za-z0-9$%]+", text))  # noqa: E731
    by_doc = {}
    for c in chunks[strategy]:
        by_doc.setdefault(c.metadata["doc_id"], set()).update(words(chunk_body(c)))
    for unit in units:
        missing = words(unit.page_content.replace("## ", "").replace("# ", "")) - by_doc[unit.metadata["doc_id"]]
        assert not missing, (strategy, unit.metadata["doc_id"], sorted(missing)[:5])
    ids = [c.metadata["chunk_id"] for c in chunks[strategy]]
    assert len(ids) == len(set(ids))
    for c in chunks[strategy]:
        assert set(METADATA_KEYS) | set(chunking.CHUNK_KEYS) == set(c.metadata)
        assert c.metadata["strategy"] == strategy


@pytest.mark.parametrize("strategy", list(PRESETS))
def test_chunk_bodies_fit_the_size(chunks, strategy):
    size = PRESETS[strategy].chunk_size
    assert max(count_tokens(chunk_body(c)) for c in chunks[strategy]) <= size + 2  # decode rounding


def test_structure_follows_sections_keeps_tables_whole_and_mends_page_breaks(chunks):
    report = _of(chunks["structure"], "report_EV008")
    assert [c.metadata["section"] for c in report] == [
        "1. Executive summary", "2. Audience", "3. Program highlights", "4. Venue and operations",
        "5. Sponsor results", "6. Lessons learned"]
    assert [c.metadata["page"] for c in report] == [1, 1, 1, 1, 1, 2]
    table = chunk_body(report[4])
    assert table.count("\n| ") == 8 and "Sablewood Therapeutics | Exhibitor Table" in table  # header + 7 sponsors
    terms = chunk_body(_of(chunks["structure"], "pricing_2026")[-1])
    assert "cancellations 30 to 90 days before receive 25%; cancellations within 30 days" in terms


def test_structure_keeps_slides_apart_and_labels_merged_intros(chunks):
    deck = _of(chunks["structure"], "deck_SP016")
    assert [c.metadata["slide"] for c in deck] == [1, 2, 3, 4, 5, 6]
    abstracts = _of(chunks["structure"], "abstracts_EV001")
    assert abstracts[0].metadata["section"] != "Overview"  # the one-line intro joined the first session
    assert "The ten best-attended sessions" in abstracts[0].page_content
    assert len(abstracts) == 10


def test_fixed_is_the_baseline_that_ignores_structure(chunks):
    assert len(chunks["fixed"]) < len(chunks["structure"])
    cut_rows = [c for c in chunks["fixed"] if re.search(r"\| [^|\n]*$", chunk_body(c))]
    assert cut_rows, "fixed windows should sometimes end in the middle of a table row"


def test_semantic_needs_embeddings_and_respects_slides(units, chunks):
    deck = [u for u in units if u.metadata["doc_id"] == "deck_SP016"]
    with pytest.raises(ValueError, match="embed"):
        chunk_document(deck, PRESETS["semantic"])
    for c in _of(chunks["semantic"], "deck_SP016"):
        assert chunk_body(c).count("## ") <= 1  # never spans two slides


def test_contextual_header_is_added_and_can_be_removed(units):
    report = [u for u in units if u.metadata["doc_id"] == "report_EV008"]
    with_header = chunk_document(report, PRESETS["structure"])[3]
    assert with_header.page_content.startswith(
        "Document: San Francisco DevSecOps Forum 2025: post-event report | Section: 4. Venue and operations | Page 1")
    assert chunk_body(with_header).startswith("## 4. Venue and operations")
    plain = chunk_document(report, ChunkConfig("structure", contextual=False))[3]
    assert plain.metadata["header"] == "" and plain.page_content == chunk_body(with_header)


def test_big_tables_split_between_rows_repeating_the_header():
    rows = "\n".join(f"| Sponsor {i} | Premier Booth | {100 + i} | 120 |" for i in range(60))
    unit = make_document(f"## Results\n\n| Sponsor | Package | Leads | Target |\n|---|---|---|---|\n{rows}",
                         DocRecord("t", "event_reports", "pdf", "t.pdf", "Test"), page=1)
    parts = chunk_document([unit], ChunkConfig("structure", chunk_size=150, contextual=False))
    assert len(parts) > 2
    for part in parts[1:]:
        assert part.page_content.startswith("| Sponsor | Package | Leads | Target |\n|---|")


def test_token_count_falls_back_without_tiktoken(monkeypatch):
    monkeypatch.setattr(chunking, "_encoding", lambda: None)
    assert count_tokens("x" * 400) == 100 and count_tokens("") == 0
    unit = Document(page_content="word " * 300, metadata=make_document(
        "", DocRecord("t", "operations", "md", "t.md", "Test")).metadata)
    assert len(chunk_document([unit], ChunkConfig("fixed", chunk_size=100, overlap=10))) >= 3


def test_semantic_keeps_headings_steps_and_bullets_with_their_text(chunks):
    semantic = chunks["semantic"]
    for c in semantic:
        body = chunk_body(c).strip()
        assert not re.fullmatch(r"#+ [^\n]*", body), f"a heading alone: {c.metadata['chunk_id']}"
        assert not re.search(r"(^|\n)\d+\.$", body, re.M), f"a step number cut from its step: {c.metadata['chunk_id']}"
    lead = [c for c in _of(semantic, "sop_booth_logistics") if "Badge scanners" in c.page_content][0]
    assert "## Lead retrieval\n1. Badge scanners must be charged" in chunk_body(lead)
    assert "\n2. Each sponsor receives a lead export" in chunk_body(lead)


def test_semantic_topic_changes_rarely_leave_short_fragments(chunks):
    """A topic change can't end a chunk under min_tokens, but the text after it, up to the next heading, may
    be short (a sponsor-feedback line after a table). Short sections with a heading are fine."""
    small = [c for c in chunks["semantic"] if count_tokens(chunk_body(c)) < PRESETS["semantic"].min_tokens
             and not chunk_body(c).startswith("#")]
    assert len(small) <= 0.1 * len(chunks["semantic"])
