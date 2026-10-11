"""Failure states through the real pages: invalid uploads, empty datasets, impossible filters, the CLI."""
import io
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.filters import CUSTOM, FilterState
from core.generator import generate_dataset, write_dataset
from core.schema import SCHEMAS

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["overview", "event_drilldown", "sponsor_performance", "sponsor_report", "audience", "feedback", "ask",
         "ai_insights", "about"]
SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from frontend.components.data_source import render_data_source_sidebar
from frontend.components.filters import render_filter_sidebar
from frontend.views import {page}
render_filter_sidebar()
render_data_source_sidebar()
{page}.render()
"""


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture(scope="module")
def good_files(tmp_path_factory) -> dict[str, bytes]:
    out = tmp_path_factory.mktemp("upload")
    write_dataset(generate_dataset(seed=3), out)
    return {name: (out / name).read_bytes() for name in SCHEMAS}


def _run_with_upload(page: str, files: dict[str, bytes]) -> AppTest:
    at = AppTest.from_string(SCRIPT.format(root=str(ROOT), page=page), default_timeout=60)
    at.session_state["uploaded_dataset"] = files
    at.session_state["uploaded_dataset_digest"] = str(hash(tuple(sorted((k, len(v)) for k, v in files.items()))))
    return at.run()


def _edit_csv(data: bytes, edit) -> bytes:
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    return edit(df).to_csv(index=False).encode("utf-8")


def _errors(at: AppTest) -> str:
    return " ".join(e.value for e in at.error)


# --- invalid CSV uploads ----------------------------------------------------------------

def test_uploaded_data_loads_when_valid(good_files):
    at = _run_with_upload("overview", good_files)
    assert not at.exception and not at.error
    assert ":orange-badge[:material/upload_file: Your uploaded data]" in at.markdown[0].value


def test_missing_column_is_reported(good_files):
    files = dict(good_files, **{"events.csv": _edit_csv(good_files["events.csv"], lambda d: d.drop(columns="budget"))})
    at = _run_with_upload("overview", files)
    assert not at.exception
    assert "Please fix these problems" in _errors(at) and "events.csv: missing required column(s): budget" in _errors(at)


def test_bad_values_are_reported_with_line_numbers(good_files):
    def corrupt(df):
        df.loc[0, "start_date"] = "31/12/2024"
        df.loc[1, "actual_cost"] = "lots"
        return df
    at = _run_with_upload("overview", dict(good_files, **{"events.csv": _edit_csv(good_files["events.csv"], corrupt)}))
    errors = _errors(at)
    assert "'start_date' must be a date in YYYY-MM-DD format" in errors and "line 2" in errors
    assert "'actual_cost' must be a number" in errors and "line 3" in errors


def test_missing_file_is_reported(good_files):
    files = {k: v for k, v in good_files.items() if k != "leads.csv"}
    assert "leads.csv: file is missing" in _errors(_run_with_upload("sponsor_performance", files))


@pytest.mark.parametrize("bad_bytes, label", [
    (b'event_id,name\n"EV1,unclosed quote\n', "unclosed quote"),
    (b"", "empty file"),
    (b"\xff\xfe\x00\x81\x8d\x8f\x90" * 50, "binary data"),
])
def test_unreadable_files_show_an_error_not_a_crash(good_files, bad_bytes, label):
    at = _run_with_upload("overview", dict(good_files, **{"events.csv": bad_bytes}))
    assert not at.exception, f"{label}: {[e.value for e in at.exception]}"
    assert "Could not" in _errors(at) or "Please fix" in _errors(at), label


@pytest.mark.parametrize("page", PAGES)
def test_every_page_survives_an_invalid_upload(good_files, page):
    files = dict(good_files, **{"sessions.csv": _edit_csv(good_files["sessions.csv"], lambda d: d.drop(columns="rating"))})
    at = _run_with_upload(page, files)
    assert not at.exception, [e.value for e in at.exception]


# --- empty data ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def headers_only(good_files) -> dict[str, bytes]:
    return {name: data.split(b"\n", 1)[0] + b"\n" for name, data in good_files.items()}


@pytest.mark.parametrize("page", PAGES)
def test_every_page_handles_a_dataset_with_no_rows(headers_only, page):
    at = _run_with_upload(page, headers_only)
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    if page != "about":
        assert "match the current filters" in " ".join(i.value for i in at.info)


@pytest.mark.parametrize("page", ["overview", "audience", "sponsor_performance", "feedback"])
def test_reversed_custom_dates_give_an_empty_state(page):
    at = AppTest.from_string(SCRIPT.format(root=str(ROOT), page=page), default_timeout=60)
    at.run()
    at.selectbox(key="f_preset").select(CUSTOM).run()
    at.date_input(key="f_dates").set_value((date(2025, 12, 31), date(2025, 1, 1))).run()
    at.button(key="apply_filters").click().run()
    assert not at.exception
    assert "match the current filters" in " ".join(i.value for i in at.info)
    assert at.session_state["filters"].date_preset == CUSTOM


def test_sponsor_filter_on_events_page_without_that_sponsor_is_empty():
    at = AppTest.from_string(SCRIPT.format(root=str(ROOT), page="event_drilldown"), default_timeout=60)
    at.session_state["filters"] = FilterState(sponsor_id="SP999")  # a sponsor that does not exist
    at.session_state["filters_source"] = None
    at.run()
    assert not at.exception


# --- command line ----------------------------------------------------------------------------

def _cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_data.py"), *args],
                          capture_output=True, text=True, timeout=300)


def test_cli_generates_and_validates(tmp_path):
    result = _cli("--seed", "5", "--out", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "All 8 files are valid." in result.stdout
    assert sorted(p.name for p in tmp_path.glob("*.csv")) == sorted(SCHEMAS)


def test_cli_validate_reports_problems(tmp_path):
    write_dataset(generate_dataset(seed=5), tmp_path)
    events = pd.read_csv(tmp_path / "events.csv", dtype=str)
    events.loc[0, "format"] = "onsite"
    events.to_csv(tmp_path / "events.csv", index=False)
    result = _cli("--validate", str(tmp_path))
    assert result.returncode == 1
    assert "events.csv: column 'format' has unexpected value 'onsite' on line 2" in result.stderr
