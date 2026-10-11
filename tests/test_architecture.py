"""Guard the boundaries: calculations and AI live in the backend, the frontend only presents.

- core/      domain code: no Streamlit, no web framework, no knowledge of either app
- backend/   the API: may use core and shared, never Streamlit or the frontend
- frontend/  Streamlit UI: talks to the backend over HTTP; never imports core, backend or LangChain
- shared/    contracts used by both: standard library and pydantic only
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {
    "core": {"streamlit", "fastapi", "backend", "frontend"},
    "backend": {"streamlit", "frontend"},
    "frontend": {"core", "backend", "langchain", "langchain_core", "langchain_openai", "langchain_anthropic"},
    "shared": {"streamlit", "pandas", "numpy", "fastapi", "langchain", "langchain_core", "core", "backend",
               "frontend", "plotly", "httpx"},
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_respects_its_boundary(package):
    offenders = {
        p.relative_to(ROOT).as_posix(): sorted(_imports(p) & FORBIDDEN[package])
        for p in (ROOT / package).rglob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_core_does_not_import_streamlit():
    offenders = [p.relative_to(ROOT).as_posix() for p in (ROOT / "core").rglob("*.py")
                 if "streamlit" in _imports(p)]
    assert offenders == []


def test_old_single_app_package_is_gone():
    assert not (ROOT / "app").exists()
