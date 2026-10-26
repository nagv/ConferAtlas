"""The Sponsor renewals page, driven like a user: start a run, follow it, decide, see the result (offline)."""
import time

import pytest

from backend.services import renewals
from core.ai.renewal_desk.runner import open_storage
from core.ai.renewal_desk.tools import RenewalWorkspace
from tests.test_app_pages import _ok, _page, _text
from tests.test_renewal_desk import ScriptedModel, call, say
from tests.test_renewal_tools import FakeDocs

SPONSOR = "Orchidia Cloud"   # a sponsor in the built-in data
PACK = f"""# {SPONSOR}: renewal proposal

*Covers all events on record.*

## Summary

We recommend keeping the **Premier Booth**.

## Your results

- Results.

## What we promised

- No record found.

## Recommended package

Keep the **Premier Booth**.

## Your offer

- **Premier Booth**.

## Next steps

- Confirm the package.
"""


def _script(after_approval):
    return [
        call("list_sponsors", sponsor=SPONSOR),
        call("write_todos", todos=[{"content": f"{SPONSOR}: renewal pack", "status": "in_progress"}]),
        call("task", subagent_type="analyst", description=f"Prepare notes for {SPONSOR}."),
        call("sponsor_facts", sponsor=SPONSOR), say("Notes written."),
        call("task", subagent_type="writer", description=f"Write the renewal pack for {SPONSOR}."),
        call("save_draft", sponsor=SPONSOR, markdown=PACK), say("Draft saved."),
        call("task", subagent_type="reviewer", description=f"Review the renewal pack for {SPONSOR}."),
        call("check_pack", sponsor=SPONSOR), say("Reviewed."),
        call("finalize_pack", sponsor=SPONSOR),
    ] + after_approval


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(renewals, "_storage", open_storage(tmp_path / "desk.sqlite"))
    monkeypatch.setattr(renewals, "_runs", {})
    script = {"replies": _script([say("Approved.")])}

    def fake_build(ds, req, recorder, max_sponsors=None, allowed=None):
        workspace = RenewalWorkspace(ds.tables, history=ds.tables, documents=FakeDocs(), max_sponsors=max_sponsors,
                                     allowed=allowed)
        return workspace, ScriptedModel(replies=list(script["replies"])), "test:scripted"

    monkeypatch.setattr(renewals, "_build", fake_build)
    return script


def _until(at, text: str):
    """Reruns the page (as its polling does) until ``text`` shows."""
    for _ in range(100):
        at.run()
        if text in _text(at) or any(text in s.value for s in at.subheader):
            return at
        time.sleep(0.05)
    raise AssertionError(f"'{text}' never appeared; page text: {_text(at)[:400]}")


def _start(at):
    at.radio(key="rn_how").set_value("Named sponsors").run()  # only now is the sponsor picker shown
    at.multiselect(key="rn_sponsors").set_value([SPONSOR])
    _generate(at).click()
    return at.run()


def _generate(at):
    return next(b for b in at.button if b.label == "Generate renewal packs")


def _keys(widgets) -> set:
    return {w.key for w in widgets}


def test_without_an_api_key_the_page_explains(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    at = _page("renewals").run()
    _ok(at)
    assert "Preparing renewal packs needs an AI model" in _text(at)


def test_start_review_and_approve(desk):
    at = _page("renewals").run()
    _ok(at)
    assert at.header[0].value == "Sponsor renewals"
    at = _until(_start(at), "Waiting for your decision")
    _ok(at)
    assert f"{SPONSOR}: renewal pack" in _text(at)          # the plan
    assert "delegated to analyst" in _text(at)              # the activity log
    assert at.radio(key="rn_choice_orchidia-cloud").value == "Approve"

    at.button(key="rn_submit").click().run()
    at = _until(at, "Renewal packs")
    _ok(at)
    assert ":green-badge[Approved]" in _text(at)
    assert at.get("download_button")


def test_reject_needs_feedback_then_goes_back_to_the_writer(desk):
    redraft = PACK.replace("Confirm the package.", "Confirm the package and your 2026 events.")
    desk["replies"] = _script([
        call("task", subagent_type="writer", description=f"Fix the pack for {SPONSOR}."),
        call("save_draft", sponsor=SPONSOR, markdown=redraft), say("Redrafted."),
        call("finalize_pack", sponsor=SPONSOR),
    ])
    at = _until(_start(_page("renewals").run()), "Waiting for your decision")
    at.radio(key="rn_choice_orchidia-cloud").set_value("Reject with feedback").run()
    at.button(key="rn_submit").click().run()
    assert "Say what should change" in at.warning[0].value   # no feedback, no rejection

    at.text_area(key="rn_feedback_orchidia-cloud").set_value("Ask which 2026 events they want.").run()
    at.button(key="rn_submit").click().run()
    at = _until(at, "your 2026 events")                      # the redraft is back for a decision
    _ok(at)
    assert "Waiting for your decision" in [s.value for s in at.subheader]
    assert "Ask which 2026 events they want." in _text(at)  # the memory note shows the feedback


def test_edit_is_saved_as_the_final_pack(desk):
    at = _until(_start(_page("renewals").run()), "Waiting for your decision")
    at.radio(key="rn_choice_orchidia-cloud").set_value("Approve with my edits").run()
    at.text_area(key="rn_edit_orchidia-cloud").set_value(PACK + "\nThank you for a great year.\n").run()
    at.button(key="rn_submit").click().run()
    at = _until(at, "Renewal packs")
    assert "Thank you for a great year." in _text(at)


def test_named_sponsors_need_a_choice(desk):
    at = _page("renewals").run()
    at.radio(key="rn_how").set_value("Named sponsors").run()
    _generate(at).click()
    at.run()
    assert "Pick at least one sponsor" in at.warning[0].value


def test_each_way_of_choosing_shows_only_its_own_controls(desk):
    at = _page("renewals").run()
    assert {"rn_tier"} <= _keys(at.selectbox) and "rn_limit" in _keys(at.slider)
    assert "rn_sponsors" not in _keys(at.multiselect)
    at.radio(key="rn_how").set_value("Named sponsors").run()
    assert "rn_sponsors" in _keys(at.multiselect)
    assert "rn_tier" not in _keys(at.selectbox) and "rn_limit" not in _keys(at.slider)


def test_activity_names_each_search_and_merges_identical_steps():
    from frontend.views.renewals import _activity_lines

    def event(tool, detail="", sponsor="", agent="package-advisor"):
        return {"agent": agent, "tool": tool, "detail": detail, "sponsor": sponsor}

    lines = _activity_lines([event("search_pricing", "2026 package prices"),
                             event("search_pricing", "payment terms"),
                             event("read_file"), event("read_file"),
                             event("save_draft", sponsor="Acme", agent="writer")])
    assert lines == ["- **Package advisor** searched pricing “2026 package prices”",
                     "- **Package advisor** searched pricing “payment terms”",
                     "- **Package advisor** read a file (×2)",
                     "- **Writer** saved a draft Acme"]
