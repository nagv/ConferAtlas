"""Renewal Desk API (offline): start, poll, decide, validation, restart and errors.

The model is the scripted one from test_renewal_desk.py and the database is a temporary file, so
each step runs in its background thread exactly as in the app, only fast.
"""
import time
from datetime import date

import pytest

from backend.main import store
from backend.services import renewals
from core.ai.renewal_desk.runner import open_storage
from core.ai.renewal_desk.tools import RenewalWorkspace
from shared.models import RenewalRunResponse
from tests.test_renewal_desk import ScriptedModel, draft_and_review, opening, say
from tests.test_renewal_tools import GOOD_PACK, FakeDocs

TODAY = date(2026, 10, 25).isoformat()


@pytest.fixture
def desk(install_api, tiny_tables, tmp_path, monkeypatch):
    """The API with a scripted model; ``desk.replies`` is the script for the next run."""
    monkeypatch.setattr(renewals, "_storage", open_storage(tmp_path / "desk.sqlite"))
    monkeypatch.setattr(renewals, "_runs", {})
    script = {"replies": []}

    def fake_build(ds, req, recorder, max_sponsors=None, allowed=None):
        workspace = RenewalWorkspace(ds.tables, history=ds.tables, documents=FakeDocs(), max_sponsors=max_sponsors,
                                     allowed=allowed)
        return workspace, ScriptedModel(replies=list(script["replies"])), "test:scripted"

    monkeypatch.setattr(renewals, "_build", fake_build)
    install_api.dataset_id = store.put_tables(tiny_tables).id
    install_api.script = script
    return install_api


def _wait(api, run_id: str) -> dict:
    for _ in range(200):
        body = api.get(f"/renewals/{run_id}").json()
        if not body["running"]:
            RenewalRunResponse.model_validate(body)  # the response matches the contract
            return body
        time.sleep(0.05)
    raise AssertionError("The run did not finish its step")


def _start(api, **body) -> dict:
    response = api.post(f"/datasets/{api.dataset_id}/renewals",
                        json={"filters": {}, "today": TODAY, "sponsors": ["Acme"], **body})
    assert response.status_code == 202, response.text
    return response.json()


def test_start_poll_approve(desk):
    desk.script["replies"] = opening() + draft_and_review() + [say("Acme: approved.")]
    started = _start(desk)
    assert started["request"] == "Prepare renewal packs for these sponsors: Acme." and started["model"] == "test:scripted"

    paused = _wait(desk, started["run_id"])
    assert paused["pending"] == ["Acme"] and not paused["done"]
    assert paused["packs"][0]["status"] == "awaiting_approval" and paused["todos"]

    response = desk.post(f"/renewals/{started['run_id']}/decisions", json={"decisions": {"Acme": {"type": "approve"}}})
    assert response.status_code == 202
    done = _wait(desk, started["run_id"])
    assert done["done"] and done["packs"][0]["final"] == GOOD_PACK
    assert "pack approved" in done["packs"][0]["memory"]
    assert desk.get("/renewals").json()[0]["run_id"] == started["run_id"]


def test_decisions_are_validated(desk):
    desk.script["replies"] = opening() + draft_and_review()
    run_id = _start(desk)["run_id"]
    _wait(desk, run_id)
    url = f"/renewals/{run_id}/decisions"
    assert "missing: Acme" in desk.post(url, json={"decisions": {}}).json()["detail"]
    assert "Not waiting" in desk.post(url, json={"decisions": {"Brio": {"type": "approve"},
                                                               "Acme": {"type": "approve"}}}).json()["detail"]
    assert "Say why" in desk.post(url, json={"decisions": {"Acme": {"type": "reject"}}}).json()["detail"]
    assert "empty" in desk.post(url, json={"decisions": {"Acme": {"type": "edit", "markdown": " "}}}).json()["detail"]
    assert desk.post(url, json={"decisions": {"Acme": {"type": "ship"}}}).status_code == 422


def test_a_paused_run_is_rebuilt_after_an_api_restart(desk, monkeypatch):
    desk.script["replies"] = opening() + draft_and_review()
    run_id = _start(desk)["run_id"]
    _wait(desk, run_id)
    monkeypatch.setattr(renewals, "_runs", {})  # the API restarted: nothing in memory

    desk.script["replies"] = [say("Acme: approved.")]
    rebuilt = desk.get(f"/renewals/{run_id}").json()
    assert rebuilt["pending"] == ["Acme"] and rebuilt["packs"][0]["draft"] == GOOD_PACK
    desk.post(f"/renewals/{run_id}/decisions", json={"decisions": {"Acme": {"type": "approve"}}})
    assert _wait(desk, run_id)["packs"][0]["status"] == "approved"


def test_unknown_run_and_nothing_to_retry(desk):
    assert desk.get("/renewals/nope").status_code == 404
    desk.script["replies"] = opening() + draft_and_review()
    run_id = _start(desk)["run_id"]
    _wait(desk, run_id)
    assert desk.post(f"/renewals/{run_id}/retry").status_code == 422


def test_a_busy_run_refuses_a_second_step(desk, monkeypatch):
    desk.script["replies"] = opening() + draft_and_review()
    run_id = _start(desk)["run_id"]
    _wait(desk, run_id)
    live = renewals._runs[run_id]
    monkeypatch.setattr(type(live), "running", property(lambda self: True))
    response = desk.post(f"/renewals/{run_id}/decisions", json={"decisions": {"Acme": {"type": "approve"}}})
    assert response.status_code == 409


def test_start_without_an_api_key_is_explained(install_api, tiny_tables, monkeypatch, tmp_path):
    monkeypatch.setattr(renewals, "_storage", open_storage(tmp_path / "desk.sqlite"))
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    dataset_id = store.put_tables(tiny_tables).id
    response = install_api.post(f"/datasets/{dataset_id}/renewals", json={"filters": {}, "today": TODAY, "tier": "gold"})
    assert response.status_code == 503 and response.json()["detail"]["kind"] == "not_configured"
