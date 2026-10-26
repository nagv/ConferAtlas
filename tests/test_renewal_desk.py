"""End-to-end tests for the Renewal Desk graph (offline): a scripted model drives the real deep agent.

The orchestrator and its specialists share one model, and they run one after another, so a single
queue of replies scripts the whole run: delegations, specialist tool calls, the approval pause, a
rejection with feedback, the redraft, and the final approval, including after a "restart".
"""
import itertools

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from core.ai.renewal_desk.runner import RUNS_NAMESPACE, RenewalRun, open_storage, read_memory
from core.ai.renewal_desk.tools import RenewalWorkspace
from core.analysis import filter_dataset
from tests.test_renewal_tools import GOOD_PACK, FakeDocs

_ids = itertools.count(1)


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{next(_ids)}"}])


def say(text):
    return AIMessage(content=text)


class ScriptedModel(BaseChatModel):
    """Returns the queued replies in order; ``bind_tools`` is a no-op."""

    replies: list = []
    seen: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if not self.replies:  # behaves like a provider that stopped answering
            raise ConnectionError(f"The script ran out after {self.seen} model calls; last message: "
                                  f"{messages[-1].content!r:.200}")
        self.seen += 1
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


def draft_and_review(pack=GOOD_PACK):
    """The orchestrator asks the writer (who saves a draft) and the reviewer (who checks it)."""
    return [
        call("task", subagent_type="writer", description="Write the renewal pack for Acme."),
        call("save_draft", sponsor="Acme", markdown=pack), say("Draft saved."),
        call("task", subagent_type="reviewer", description="Review the renewal pack for Acme."),
        call("check_pack", sponsor="Acme"), say("Reviewed."),
        call("finalize_pack", sponsor="Acme"),
    ]


def opening():
    return [
        call("list_sponsors", sponsor="Acme"),
        call("write_todos", todos=[{"content": "Acme: renewal pack", "status": "in_progress"}]),
        call("task", subagent_type="analyst", description="Prepare notes for Acme."),
        call("sponsor_facts", sponsor="Acme"),
        call("search_sponsor_decks", query="Acme goals"),
        say("Notes written."),
        call("task", subagent_type="package-advisor", description="Recommend next year's package for Acme."),
        call("search_pricing", query="Premier Booth 2026 price"), say("Recommendation written."),
    ]


@pytest.fixture
def storage(tmp_path):
    return open_storage(tmp_path / "desk.sqlite")


def workspace(tiny_tables):
    return RenewalWorkspace(filter_dataset(tiny_tables), history=tiny_tables, documents=FakeDocs())


def test_run_pauses_for_approval_then_saves_and_remembers(tiny_tables, storage):
    checkpointer, store = storage
    model = ScriptedModel(replies=opening() + draft_and_review() + [say("Acme: approved.")])
    run = RenewalRun(workspace(tiny_tables), model, checkpointer, store)

    paused = run.start("Prepare a renewal pack for Acme.")
    assert paused["pending"] == ["Acme"] and not paused["done"] and not paused["error"]
    acme = paused["packs"][0]
    assert acme["status"] == "awaiting_approval" and acme["passed"] and acme["final"] == ""
    assert paused["todos"] == [{"content": "Acme: renewal pack", "status": "in_progress"}]
    assert {"agent": "orchestrator", "tool": "task", "sponsor": "", "detail": "analyst"} in paused["events"]

    finished = run.decide({"Acme": {"type": "approve"}})
    assert finished["done"] and finished["packs"][0]["status"] == "approved"
    assert finished["packs"][0]["final"] == GOOD_PACK
    assert "pack approved, recommending Premier Booth." in read_memory(store, "Acme")
    assert not model.replies  # the script was followed to the end


def test_rejection_sends_feedback_back_and_the_redraft_is_approved(tiny_tables, storage):
    checkpointer, store = storage
    redraft = GOOD_PACK.replace("Confirm the package for 2026.", "Confirm the package and your 2026 events.")
    model = ScriptedModel(replies=opening() + draft_and_review() + draft_and_review(redraft) +
                          [say("Acme: approved after one redraft.")])
    run = RenewalRun(workspace(tiny_tables), model, checkpointer, store)
    run.start("Prepare a renewal pack for Acme.")

    again = run.decide({"Acme": {"type": "reject", "feedback": "Ask which 2026 events they want."}})
    assert again["pending"] == ["Acme"]  # redrafted, reviewed and back for approval
    pack = again["packs"][0]
    assert pack["draft"] == redraft and pack["feedback"] == ["Manager: Ask which 2026 events they want."]
    assert "Feedback: Ask which 2026 events they want." in pack["memory"]

    done = run.decide({"Acme": {"type": "approve"}})
    assert done["done"] and done["packs"][0]["final"] == redraft


def test_a_persons_edit_is_what_gets_saved(tiny_tables, storage):
    checkpointer, store = storage
    model = ScriptedModel(replies=opening() + draft_and_review() + [say("Acme: approved with edits.")])
    run = RenewalRun(workspace(tiny_tables), model, checkpointer, store)
    run.start("Prepare a renewal pack for Acme.")
    edited = GOOD_PACK + "\nP.S. Thank you for a great year.\n"

    done = run.decide({"Acme": {"type": "edit", "markdown": edited}})
    assert done["packs"][0]["final"] == edited
    assert "approved with edits" in done["packs"][0]["memory"]


def test_a_paused_run_survives_a_restart(tiny_tables, tmp_path):
    db = tmp_path / "desk.sqlite"
    checkpointer, store = open_storage(db)
    first = RenewalRun(workspace(tiny_tables), ScriptedModel(replies=opening() + draft_and_review()),
                       checkpointer, store)
    first.start("Prepare a renewal pack for Acme.")
    thread = first.thread_id
    del first, checkpointer, store  # the API process stops

    checkpointer, store = open_storage(db)  # ...and starts again with fresh objects
    assert store.get(RUNS_NAMESPACE, thread) is not None
    run = RenewalRun.load(thread, workspace(tiny_tables), ScriptedModel(replies=[say("Acme: approved.")]),
                          checkpointer, store)
    assert run.pending and run.pending[0]["sponsor"] == "Acme"
    assert run.workspace.packs["Acme"].draft == GOOD_PACK  # the tools were rebuilt from the snapshot

    done = run.decide({})  # no decision given = approve
    assert done["done"] and done["packs"][0]["status"] == "approved"


def test_a_provider_failure_keeps_the_run_and_retry_continues_it(tiny_tables, storage, monkeypatch):
    from core.ai.renewal_desk import agent

    monkeypatch.setattr(agent, "MODEL_RETRIES", 0)  # fail at once instead of backing off
    checkpointer, store = storage
    model = ScriptedModel(replies=[call("list_sponsors", sponsor="Acme")])  # then the script runs dry
    run = RenewalRun(workspace(tiny_tables), model, checkpointer, store)
    status = run.start("Prepare a renewal pack for Acme.")
    assert not status["done"] and status["can_retry"] and "The run stopped" in status["error"]
    assert status["packs"][0]["status"] == "queued"  # not marked failed: the run can continue

    model.replies.extend(opening()[1:] + draft_and_review())  # the provider is back
    resumed = run.retry()
    assert resumed["pending"] == ["Acme"] and not resumed["error"]
    with pytest.raises(ValueError):
        run.retry()


def test_decide_needs_a_pending_approval(tiny_tables, storage):
    checkpointer, store = storage
    run = RenewalRun(workspace(tiny_tables), ScriptedModel(replies=[]), checkpointer, store)
    with pytest.raises(ValueError):
        run.decide({"Acme": {"type": "approve"}})
