"""The entry page introduces the platform and links to the existing workflows."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_welcome_is_the_default_page_with_workflow_links():
    root = Path(__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "frontend" / "app.py"), default_timeout=60).run()
    assert not at.exception
    text = " ".join(element.value for element in at.get("html"))
    assert "Turn conference data into" in text
    assert "For conference teams" in text
    assert "sponsors receive exported reports" in text
    labels = [link.label for link in at.get("page_link")]
    assert "Explore the portfolio" in labels
    assert "Prepare a sponsor report" in labels
    assert "Explore audience insights" in labels
    assert "Review attendee feedback" in labels
