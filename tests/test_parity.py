"""Parity gate: every page must show exactly what the original single-process app showed.

``tests/parity_baseline.json`` was recorded from the original Streamlit app before the
frontend/backend split. This test renders the same scenarios through the current UI and
compares everything a user can see: text, metrics, tables, widget options and values,
buttons, chart specs and CSV download contents.

Re-record (only when a visible change is intended):  PARITY_WRITE=1 pytest tests/test_parity.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.generator import generate_dataset, write_dataset
from core.schema import SCHEMAS

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("parity_baseline.json")
PKG = "frontend" if (ROOT / "frontend" / "views").is_dir() else "app"
PAGES = ["overview", "event_drilldown", "sponsor_performance", "sponsor_report", "audience", "feedback",
         "ask", "ai_insights", "about"]
SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from {pkg}.components.data_source import render_data_source_sidebar, sync_uploaded_files
from {pkg}.components.filters import render_filter_sidebar
from {pkg}.views import {page}
sync_uploaded_files()
render_filter_sidebar()
render_data_source_sidebar()
{page}.render()
"""
_TEXT = {"markdown", "caption", "text", "info", "warning", "error", "success", "header", "subheader", "title",
         "code", "latex"}


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _element(el) -> dict | None:
    kind = el.type
    proto = getattr(el, "proto", None)
    if kind in _TEXT:
        return {"t": kind, "v": el.value}
    if kind == "metric":
        return {"t": kind, "label": el.label, "v": el.value, "delta": el.delta, "help": proto.help}
    if kind in ("dataframe", "arrow_data_frame"):
        return {"t": "dataframe", "csv": el.value.to_csv(index=False)}
    if kind in ("selectbox", "multiselect", "radio"):
        return {"t": kind, "label": el.label, "options": list(el.options), "v": _plain(el.value)}
    if kind in ("slider", "select_slider", "checkbox", "toggle", "date_input", "number_input", "text_input"):
        return {"t": kind, "label": el.label, "v": _plain(el.value)}
    if kind == "button":
        return {"t": kind, "label": el.label, "disabled": el.disabled}
    if kind == "chat_input":
        return {"t": kind, "disabled": el.disabled, "placeholder": el.placeholder}
    if kind == "expander":
        return {"t": kind, "label": el.label}
    if kind == "tab":
        return {"t": kind, "label": el.label}
    if kind == "chat_message":
        return {"t": kind, "name": el.name}
    if proto is None:
        return None
    name = proto.DESCRIPTOR.name
    if name == "PlotlyChart":
        return {"t": "plotly", "spec": _sha(proto.spec)}
    if name == "DownloadButton":
        out = {"t": "download", "label": proto.label}
        if not proto.label.endswith("(.md)"):  # the report file embeds today's date
            out["url"] = proto.url  # media URLs are content hashes, so this pins the file bytes
        return out
    if name == "PageLink":
        return {"t": "page_link", "label": proto.label}
    if name == "Json":
        return {"t": "json", "v": proto.body}
    if name == "ButtonGroup":
        return {"t": "button_group", "options": [o.content for o in proto.options]}
    return None


def _plain(value):
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def _snapshot(at: AppTest) -> dict:
    assert not at.exception, [e.value for e in at.exception]
    out = {}
    for area, root in (("main", at.main), ("sidebar", at.sidebar)):
        items = []
        for node in root:
            item = _element(node)
            if item is not None:
                items.append(item)
        out[area] = items
    return out


# --- scenarios ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def upload_sets(tmp_path_factory) -> dict[str, dict[str, bytes]]:
    out = tmp_path_factory.mktemp("parity_upload")
    write_dataset(generate_dataset(seed=3), out)
    good = {name: (out / name).read_bytes() for name in SCHEMAS}
    sessions = pd.read_csv(io.BytesIO(good["sessions.csv"]), dtype=str, keep_default_na=False)
    bad = dict(good, **{"sessions.csv": sessions.drop(columns="rating").to_csv(index=False).encode("utf-8")})
    headers = {name: data.split(b"\n", 1)[0] + b"\n" for name, data in good.items()}
    return {"upload": good, "headers_only": headers, "invalid": bad}


def _page(page: str) -> AppTest:
    return AppTest.from_string(SCRIPT.format(root=str(ROOT), pkg=PKG, page=page), default_timeout=90)


def _apply(at):
    at.button(key="apply_filters").click().run()


def _custom_2025(at):
    at.selectbox(key="f_preset").select("Custom dates").run()
    at.date_input(key="f_dates").set_value((date(2025, 1, 1), date(2025, 12, 31))).run()
    _apply(at)


def _virtual(at):
    at.multiselect(key="f_formats").select("virtual").run()
    _apply(at)


def _sponsor(at):
    at.selectbox(key="f_sponsor").set_value(_first_sponsor_id(at)).run()
    _apply(at)


def _first_sponsor_id(at) -> str:
    from core.config import DEFAULT_DATA_DIR
    from core.schema import load_dataset
    sponsors = load_dataset(DEFAULT_DATA_DIR)["sponsors.csv"]
    label = at.selectbox(key="f_sponsor").options[1]
    return sponsors.loc[sponsors["name"] == label, "sponsor_id"].item()


def _draft_only(at):
    at.multiselect(key="f_cities").select(at.multiselect(key="f_cities").options[0]).run()


def _second_option(key):
    def act(at):
        # Selectboxes with a format_func expose labels, so set the underlying id directly.
        at.selectbox(key=key).set_value(_SELECT_VALUES[key]()[1]).run()
    return act


def _event_ids_newest_first():
    from core.config import DEFAULT_DATA_DIR
    from core.schema import load_dataset
    events = load_dataset(DEFAULT_DATA_DIR)["events.csv"].sort_values("start_date", ascending=False)
    return list(events["event_id"])


def _sponsor_ids_by_name():
    from core.config import DEFAULT_DATA_DIR
    from core.schema import load_dataset
    t = load_dataset(DEFAULT_DATA_DIR)
    active = t["sponsors.csv"][t["sponsors.csv"]["sponsor_id"].isin(t["sponsorships.csv"]["sponsor_id"])]
    return list(active.sort_values("name")["sponsor_id"])


_SELECT_VALUES = {"drilldown_event": _event_ids_newest_first, "sponsor_report_sponsor": _sponsor_ids_by_name,
                  "ai_sponsor": _sponsor_ids_by_name, "sp_report_sponsor": _sponsor_ids_by_name}


SCENARIOS = {
    "default": lambda at: None,
    "custom_2025": _custom_2025,
    "virtual": _virtual,
    "sponsor_filter": _sponsor,
    "unapplied_draft": _draft_only,
    "upload": "upload",
    "headers_only": "headers_only",
    "invalid_upload": "invalid",
}

PAGE_ACTIONS = {
    "overview": {
        "top5_nps_lowest": lambda at: (at.selectbox(key="ov_top").select("Top 5").run(),
                                       at.selectbox(key="ov_sort").select("NPS").run(),
                                       _segmented(at, "ov_order", "Lowest first")),
        "mix_registrations": lambda at: _segmented(at, "ov_mix", "Registrations"),
        "all_events": lambda at: at.selectbox(key="ov_top").select("All events").run(),
    },
    "event_drilldown": {
        "high_rating": lambda at: at.slider(key="dd_rating").set_value((4.5, 5.0)).run(),
        "first_track": lambda at: at.multiselect(key="dd_tracks").select(
            at.multiselect(key="dd_tracks").options[0]).run(),
        "second_event": _second_option("drilldown_event"),
    },
    "sponsor_performance": {
        "booth_only": lambda at: at.multiselect(key="sp_sources").select("booth").run(),
        "platinum": lambda at: at.multiselect(key="sp_tiers").select("platinum").run(),
        "template_report": lambda at: at.button(key="sp_generate").click().run(),
    },
    "sponsor_report": {"second_sponsor": _second_option("sponsor_report_sponsor")},
    "audience": {
        "no_show": lambda at: at.multiselect(key="aud_status").select("No-show").run(),
        "first_role": lambda at: at.multiselect(key="aud_roles").select(
            at.multiselect(key="aud_roles").options[0]).run(),
    },
    "feedback": {
        "one_star": lambda at: at.multiselect(key="fb_ratings").set_value([1]).run(),
        "first_track": lambda at: at.multiselect(key="fb_tracks").select(
            at.multiselect(key="fb_tracks").options[0]).run(),
    },
    "ai_insights": {"sponsor_brief": lambda at: at.radio[0].set_value("Renewal brief for one sponsor").run()},
}


def _segmented(at, key, value):
    at.session_state[key] = value
    at.run()


def _cases():
    for page in PAGES:
        for name in SCENARIOS:
            yield page, name
        for name in PAGE_ACTIONS.get(page, {}):
            yield page, name


def _render(page: str, scenario: str, upload_sets) -> dict:
    at = _page(page)
    action = SCENARIOS.get(scenario) or PAGE_ACTIONS[page][scenario]
    if isinstance(action, str):
        files = upload_sets[action]
        at.session_state["uploaded_dataset"] = files
        at.session_state["uploaded_dataset_digest"] = hashlib.sha256(b"".join(files.values())).hexdigest()
        at.run()
    else:
        at.run()
        action(at)
    return _snapshot(at)


def _load_baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}


_RECORDED: dict = {}


@pytest.mark.parametrize("page, scenario", list(_cases()))
def test_page_matches_original(page, scenario, upload_sets):
    snapshot = _render(page, scenario, upload_sets)
    key = f"{page}/{scenario}"
    if os.getenv("PARITY_WRITE"):
        data = _load_baseline()
        data[key] = snapshot
        BASELINE.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        return
    expected = _load_baseline().get(key)
    assert expected is not None, f"No baseline for {key}"
    for area in ("main", "sidebar"):
        assert snapshot[area] == expected[area], f"{key} [{area}] differs"
