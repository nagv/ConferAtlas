"""The approved renewal pack as a PDF (offline): content, safety and file name."""
import pymupdf

from core.ai.renewal_desk.export import pack_html, pack_pdf, pdf_filename
from tests.test_renewal_tools import GOOD_PACK


def test_the_pdf_holds_the_pack_with_header_and_page_numbers():
    pdf = pack_pdf(GOOD_PACK, "Acme")
    assert pdf.startswith(b"%PDF")
    doc = pymupdf.open("pdf", pdf)
    text = "".join(page.get_text() for page in doc)
    assert "Acme: renewal proposal" in text and "$51,000 [S1]" in text  # the approved words, unchanged
    assert "Renewal proposal for Acme" in text and f"Page 1 of {doc.page_count}" in text
    assert doc.metadata["title"] == "Acme: renewal proposal"


def test_markdown_becomes_headings_and_lists_but_html_in_the_pack_is_not_rendered():
    html = pack_html("## Your offer\n\n- **Premier Booth**: $51,000\n\n<script>alert(1)</script>")
    assert "<h2>Your offer</h2>" in html and "<strong>Premier Booth</strong>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_the_file_name_is_safe():
    assert pdf_filename("Orchidia Cloud") == "orchidia-cloud-renewal-pack.pdf"
    assert pdf_filename("A/B & C") == "a-b-c-renewal-pack.pdf"
