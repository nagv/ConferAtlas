"""An approved renewal pack as a sponsor-ready PDF.

Plain rendering, no model: the pack was already written, checked and approved, so turning it into a PDF
must not change a word. Markdown becomes HTML (markdown-it, HTML in the pack itself is not rendered),
PyMuPDF lays it out on Letter pages, and every page gets the organizer header and a numbered footer.
"""
from __future__ import annotations

import io
import re
from datetime import date

from core.docgen import ORGANISER

_CSS = ("body {font-family: sans-serif; font-size: 10.5pt; line-height: 1.4; color: #222;} "
        "h1 {font-size: 19pt; color: #1f3a5f; margin-bottom: 4pt;} "
        "h2 {font-size: 13pt; color: #1f3a5f; margin-top: 14pt; margin-bottom: 4pt;} "
        "p {margin: 4pt 0;} li {margin: 2pt 0;} em {color: #555;}")


def pack_html(markdown: str) -> str:
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False}).render(markdown)


def pack_pdf(markdown: str, sponsor: str, approved_on: date | None = None) -> bytes:
    """The pack as PDF bytes, with the organizer and sponsor in the header and page numbers in the footer."""
    import pymupdf

    story = pymupdf.Story(html=pack_html(markdown), user_css=_CSS)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    page_rect = pymupdf.paper_rect("letter")
    content = page_rect + (60, 72, -60, -72)
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content)
        story.draw(device)
        writer.end_page()
    writer.close()

    doc = pymupdf.open("pdf", buffer.getvalue())
    when = (approved_on or date.today()).strftime("%d %b %Y")
    for number, page in enumerate(doc, start=1):
        page.insert_text((60, 42), f"{ORGANISER} | Renewal proposal for {sponsor}", fontsize=8,
                         color=(0.4, 0.4, 0.4))
        page.insert_text((60, page_rect.height - 36), f"Approved {when} | Page {number} of {doc.page_count}",
                         fontsize=8, color=(0.4, 0.4, 0.4))
    doc.set_metadata({"title": f"{sponsor}: renewal proposal", "author": ORGANISER,
                      "creator": "ConferAtlas Sponsor Renewal Desk"})
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def pdf_filename(sponsor: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", sponsor.lower()).strip("-") or "sponsor") + "-renewal-pack.pdf"
