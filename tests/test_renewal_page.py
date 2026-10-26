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

    def fake_build(ds, req, recorder, max_sponsors=None, allowed=None, target_tiers=None):
        workspace = RenewalWorkspace(ds.tables, history=ds.tables, documents=FakeDocs(), max_sponsors=max_sponsors,
                                     allowed=allowed, target_tiers=target_tiers)
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


def _start(at, tier=None):
    at.multiselect(key="rn_sponsors").set_value([SPONSOR]).run()  # each sponsor gets a tier row
    if tier:
        at.selectbox(key="rn_tier_orchidia-cloud").set_value(tier)
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
    button = at.get("download_button")[0]
    assert "PDF" in button.proto.label  # the approved pack downloads as a PDF


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


def test_only_a_sponsor_picker_until_sponsors_are_chosen(desk):
    at = _page("renewals").run()
    _ok(at)
    assert "rn_sponsors" in _keys(at.multiselect) and not at.radio  # no second way of choosing
    assert not any(b.label == "Generate renewal packs" for b in at.button)
    assert "Pick up to 5 sponsors" in _text(at)


def test_each_sponsor_gets_a_tier_that_defaults_to_its_current_one(desk):
    at = _page("renewals").run()
    at.multiselect(key="rn_sponsors").set_value([SPONSOR, "Brightpath Security"]).run()
    assert at.selectbox(key="rn_tier_orchidia-cloud").value == "gold"
    assert at.selectbox(key="rn_tier_brightpath-security").value == "platinum"
    assert ":orange-badge[Gold]" in _text(at) and "of lead target" in _text(at)


def test_the_chosen_tier_reaches_the_agents(desk):
    _until(_start(_page("renewals").run(), tier="platinum"), "Waiting for your decision")
    run = renewals.status(next(iter(renewals._runs)))
    assert run["request"] == "Prepare a renewal pack for Orchidia Cloud (propose the platinum tier)."


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
