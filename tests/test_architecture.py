"""Guard the boundary: calculation code in core/ must not depend on the UI."""
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"


def test_core_does_not_import_streamlit():
    offenders = [
        p.relative_to(CORE.parent).as_posix()
        for p in CORE.rglob("*.py")
        if "import streamlit" in p.read_text(encoding="utf-8")
        or "from streamlit" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
