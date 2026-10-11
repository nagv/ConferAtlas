"""Tests for core/ai/portfolio_agent.py: tools, guardrails and the agent loop (fake model, offline)."""
import json
from pathlib import Path

import pandas as pd
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.ai import portfolio_agent as pa
from core.ai.insights import LLMNotConfiguredError
from core.analysis import filter_dataset
from core.config import Settings


class APITimeoutError(Exception):
    """Stands in for a provider timeout (matched by class name)."""


class FakeToolModel:
    """Scripted chat model: ``bind_tools`` returns itself; each ``invoke`` pops the next reply."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen: list[list] = []
        self.tool_names: list[str] = []

    def bind_tools(self, tools):
        self.tool_names = [t.name for t in tools]
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply(messages) if callable(reply) else reply


def tool_call(name, args=None, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


@pytest.fixture
def toolkit(tiny_tables):
    return pa.PortfolioToolkit(filter_dataset(tiny_tables), scope="all data")


# --- tools ------------------------------------------------------------------------

def test_six_fixed_tools(toolkit):
    assert [t.name for t in toolkit.tools()] == ["portfolio_summary", "event_kpis", "audience_mix",
                                                 "compare_events", "sponsor_outcomes", "make_chart"]


def test_tools_respect_the_filtered_tables(tiny_tables):
    kit = pa.PortfolioToolkit(filter_dataset(tiny_tables, event_ids=["E1"]), history=tiny_tables, scope="E1 only")
    summary = kit.portfolio_summary()
    assert summary["events"] == 1 and summary["registrations"] == 4 and summary["scope"] == "E1 only"
    assert [r["name"] for r in kit.event_kpis()["rows"]] == ["Alpha Con"]
    assert {r["sponsor"] for r in kit.sponsor_outcomes()["rows"]} == {"Acme", "Brio"}


def test_results_are_recorded_with_ids(toolkit):
    toolkit.portfolio_summary()
    toolkit.event_kpis(sort_by="registrations", top_n=1)
    assert [r.id for r in toolkit.ledger] == ["T1", "T2"]
    assert toolkit.ledger[1].result["rows"][0]["name"] == "Alpha Con"
    assert list(toolkit.ledger[1].table["name"]) == ["Alpha Con"]


def test_event_and_sponsor_matching(toolkit):
    assert toolkit.event_kpis(event="beta")["rows"][0]["name"] == "Beta Live"
    assert "No event matches" in toolkit.event_kpis(event="Gamma")["error"]
    compared = toolkit.compare_events(["Alpha", "E2"])
    assert [r["name"] for r in compared["rows"]] == ["Alpha Con", "Beta Live"]
    assert "no event matches 'Zeta'" in toolkit.compare_events(["Alpha", "Zeta"])["error"]
    assert toolkit.sponsor_outcomes(sponsor="brio")["rows"][0]["sponsor"] == "Brio"
    assert "No sponsor matches" in toolkit.sponsor_outcomes(sponsor="Nope")["error"]


def test_sponsor_outcomes_sorting_and_counts(toolkit):
    out = toolkit.sponsor_outcomes(sort_by="attainment", descending=False)
    assert [r["sponsor"] for r in out["rows"]] == ["Brio", "Acme"]  # 50% then 100%
    assert out["sponsors_below_threshold"] == 1 and out["total_leads"] == 7 and out["total_target"] == 8


def test_audience_mix_dimensions(toolkit, tiny_tables):
    assert toolkit.audience_mix("country")["rows"][0] == {
        "country": "Canada", "registrations": 4, "share": pytest.approx(0.6667), "confirmed": 3, "attended": 2,
        "show_up_rate": pytest.approx(0.6667)}
    status = {r["attendance_status"]: r["registrations"] for r in toolkit.audience_mix("attendance_status")["rows"]}
    assert status == {"Attended": 3, "No-show": 2, "Cancelled": 1}
    assert "person_id" in toolkit.audience_mix("new_vs_returning")["error"]  # tiny data has no person ids


def test_make_chart_only_uses_tool_results(toolkit):
    toolkit.portfolio_summary()                     # T1: no rows
    toolkit.event_kpis(sort_by="registrations")     # T2: rows
    assert "not an earlier tool result with rows" in toolkit.make_chart("T1", "bar", "name", "x", "t")["error"]
    assert "not an earlier tool result" in toolkit.make_chart("T9", "bar", "name", "leads", "t")["error"]
    assert "Columns must come from T2" in toolkit.make_chart("T2", "bar", "name", "salary", "t")["error"]
    assert "not numeric" in toolkit.make_chart("T2", "bar", "leads", "name", "t")["error"]
    ok = toolkit.make_chart("T2", "bar", "name", "registrations", "Registrations by event")
    assert ok["chart_id"] == "C1" and toolkit.charts[0].source_id == "T2"


def test_bad_arguments_and_unknown_tools_return_errors(toolkit):
    assert "Invalid arguments" in toolkit.run("event_kpis", {"top_n": 500})["error"]
    assert "Invalid arguments" in toolkit.run("audience_mix", {"dimension": "salary"})["error"]
    assert "Unknown tool" in toolkit.run("delete_everything", {})["error"]


def test_no_raw_records_reach_the_model(dataset):
    kit = pa.PortfolioToolkit(filter_dataset(dataset), scope="all")
    kit.portfolio_summary()
    kit.event_kpis(top_n=15)
    for dim in ("attendee_role", "seniority", "industry", "country", "attendance_status", "new_vs_returning"):
        kit.audience_mix(dim, top_n=15)
    kit.sponsor_outcomes(top_n=15)
    kit.compare_events([kit.ledger[1].result["rows"][0]["name"], kit.ledger[1].result["rows"][1]["name"]])
    for record in kit.ledger:
        pa.assert_no_raw_records(record.result)  # would raise
        assert len(record.result.get("rows", [])) <= pa.MAX_ROWS
    text = json.dumps([r.result for r in kit.ledger])
    assert "REG0" not in text and "person_id" not in text and "reg_id" not in text


def test_assert_no_raw_records_catches_leaks():
    with pytest.raises(ValueError):
        pa.assert_no_raw_records({"rows": [{"reg_id": "x"}]})
    with pytest.raises(ValueError):
        pa.assert_no_raw_records({"note": "see REG000123"})
    pa.assert_no_raw_records({"rows": [{"name": "Chicago DevSecOps Forum 2024", "registrations": 951}]})


# --- number verification ------------------------------------------------------------

@pytest.mark.parametrize("answer, misses", [
    ("Show-up rate was 78% across 13,361 registrations.", []),
    ("Revenue was $2.66M and cost per lead $140.", []),
    ("Revenue was $2,661,300.", []),
    ("NPS averaged -5.5.", []),
    ("The 2025 Chicago event did well.", []),     # years inside names in the results count
    ("1. First point\n2. Second point", []),     # list numbering ignored
    ("Show-up rate was 81%.", ["81%"]),
    ("We expect 20,000 registrations.", ["20,000"]),
])
def test_unverified_numbers(answer, misses):
    result = {"registrations": 13361, "show_up_rate": 0.7754, "sponsor_revenue": 2661300.0, "cost_per_lead": 140.27,
              "nps": -5.4954, "rows": [{"name": "Chicago DevSecOps Forum 2025"}]}
    assert pa.unverified_numbers(answer, [result]) == misses


def test_trim_history_caps_messages_and_characters():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"} for i in range(20)]
    trimmed = pa.trim_history(history)
    assert len(trimmed) == pa.HISTORY_MESSAGES and trimmed[-1]["content"] == "message 19"
    long = [{"role": "user", "content": "x" * 3000}, {"role": "assistant", "content": "y" * 3000}]
    assert pa.trim_history(long) == [long[1]]


# --- the agent loop ------------------------------------------------------------------

def test_answer_uses_tool_results(toolkit):
    model = FakeToolModel(tool_call("portfolio_summary"),
                          AIMessage(content="Across 2 events there were 6 registrations and a 60% show-up rate."))
    answer = pa.ask_portfolio("How did we do?", toolkit, model=model)
    assert answer.text.startswith("Across 2 events") and answer.unverified_numbers == []
    assert answer.tool_calls == 1 and [r.tool for r in answer.calculations] == ["portfolio_summary"]
    tool_msg = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
    assert json.loads(tool_msg.content)["registrations"] == 6
    assert model.tool_names[0] == "portfolio_summary"


def test_untraceable_numbers_get_one_rewrite(toolkit):
    model = FakeToolModel(tool_call("portfolio_summary"),
                          AIMessage(content="There were 6 registrations and 950 attendees."),
                          AIMessage(content="There were 6 registrations and 3 attendees."))
    answer = pa.ask_portfolio("How many?", toolkit, model=model)
    assert answer.text == "There were 6 registrations and 3 attendees." and answer.unverified_numbers == []
    assert "do not appear in any tool result: 950" in model.seen[-1][-1].content


def test_numbers_still_untraceable_are_flagged(toolkit):
    model = FakeToolModel(tool_call("portfolio_summary"), AIMessage(content="Attendance will hit 5,000."),
                          AIMessage(content="Attendance will hit 5,000 next year."))
    answer = pa.ask_portfolio("Forecast?", toolkit, model=model)
    assert answer.unverified_numbers == ["5,000"]


def test_model_turn_limit_forces_an_answer(toolkit):
    many = [tool_call("portfolio_summary", call_id=f"c{i}") for i in range(pa.MAX_MODEL_TURNS)]
    model = FakeToolModel(*many, AIMessage(content="Done."))
    answer = pa.ask_portfolio("Loop forever", toolkit, model=model)
    assert answer.tool_calls == pa.MAX_MODEL_TURNS  # one call per turn, then no more turns
    assert any("used all its steps" in w for w in answer.warnings)
    assert "Stop calling tools" in model.seen[-1][-1].content and answer.text == "Done."


def test_parallel_tool_calls_beyond_the_limit_are_refused(toolkit):
    calls = [{"name": "portfolio_summary", "args": {}, "id": f"c{i}"} for i in range(pa.MAX_TOOL_CALLS + 2)]
    model = FakeToolModel(AIMessage(content="", tool_calls=calls), AIMessage(content="Summary ready."))
    answer = pa.ask_portfolio("Everything at once", toolkit, model=model)
    assert answer.tool_calls == pa.MAX_TOOL_CALLS and len(answer.calculations) == pa.MAX_TOOL_CALLS
    refused = [m for m in model.seen[1] if isinstance(m, ToolMessage) and "limit" in m.content]
    assert len(refused) == 2 and any("limit" in w for w in answer.warnings)


def test_chart_requests_are_returned(toolkit):
    model = FakeToolModel(tool_call("event_kpis", {"sort_by": "registrations"}),
                          tool_call("make_chart", {"source_id": "T1", "chart_type": "bar", "x": "name",
                                                   "y": "registrations", "title": "Registrations"}, "c2"),
                          AIMessage(content="Alpha Con had the most registrations."))
    answer = pa.ask_portfolio("Chart registrations", toolkit, model=model)
    assert [c.source_id for c in answer.charts] == ["T1"] and answer.charts[0].y == "registrations"


def test_timeouts_and_provider_errors(toolkit):
    with pytest.raises(pa.AgentError) as exc:
        pa.ask_portfolio("Q", toolkit, model=FakeToolModel(APITimeoutError("slow")))
    assert exc.value.kind == "timeout"
    with pytest.raises(pa.AgentError) as exc:
        pa.ask_portfolio("Q", toolkit, model=FakeToolModel(ConnectionError("offline")))
    assert exc.value.kind == "provider" and "Couldn't reach the AI provider" in str(exc.value)  # friendly


def test_overall_deadline(toolkit):
    ticks = iter([0, 0, 500, 500])
    model = FakeToolModel(tool_call("portfolio_summary"), AIMessage(content="late"))
    with pytest.raises(pa.AgentError) as exc:
        pa.ask_portfolio("Q", toolkit, model=model, clock=lambda: next(ticks), deadline_s=100)
    assert exc.value.kind == "timeout" and "100 seconds" in str(exc.value)


def test_only_trimmed_history_is_sent(toolkit):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"old {i}"} for i in range(12)]
    model = FakeToolModel(AIMessage(content="No figures needed."))
    pa.ask_portfolio("Hello", toolkit, history=history, model=model)
    sent = model.seen[0]
    assert len(sent) == 1 + pa.HISTORY_MESSAGES + 1  # system + trimmed history + question
    assert isinstance(sent[-1], HumanMessage) and sent[-1].content == "Hello"
    assert sent[1].content == "old 6"


def test_missing_api_key(toolkit, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(llm_provider="anthropic", llm_model="claude-opus-5-5", data_dir=Path("unused"))
    with pytest.raises(LLMNotConfiguredError):
        pa.ask_portfolio("Q", toolkit, settings=settings)


# --- limits: tool budget across turns, payload size ----------------------------------------

def test_tool_budget_is_shared_across_turns(toolkit):
    def two_calls(turn):
        return AIMessage(content="", tool_calls=[{"name": "portfolio_summary", "args": {}, "id": f"t{turn}a"},
                                                 {"name": "event_kpis", "args": {}, "id": f"t{turn}b"}])
    model = FakeToolModel(two_calls(1), two_calls(2), two_calls(3), two_calls(4), AIMessage(content="Done."))
    answer = pa.ask_portfolio("Keep going", toolkit, model=model)
    assert answer.tool_calls == pa.MAX_TOOL_CALLS == len(answer.calculations)
    refused = [m for msgs in model.seen for m in msgs if isinstance(m, ToolMessage) and "limit" in m.content]
    assert refused  # the fourth turn's calls were refused, not run


def test_tool_payloads_stay_small(dataset):
    kit = pa.PortfolioToolkit(filter_dataset(dataset), history=dataset)
    payloads = [kit.portfolio_summary(), kit.event_kpis(top_n=pa.MAX_ROWS),
                kit.sponsor_outcomes(top_n=pa.MAX_ROWS), kit.audience_mix("attendee_role", top_n=pa.MAX_ROWS)]
    for payload in payloads:
        assert len(json.dumps(payload)) < 12_000  # bounded tokens per tool result
        assert len(payload.get("rows", [])) <= pa.MAX_ROWS


def test_tools_on_an_empty_selection(tiny_tables):
    kit = pa.PortfolioToolkit(filter_dataset(tiny_tables, event_ids=[]))
    assert kit.portfolio_summary()["events"] == 0
    assert kit.event_kpis()["rows"] == [] and kit.sponsor_outcomes()["rows"] == []
    assert kit.audience_mix("country")["rows"] == []
    assert "not an earlier tool result with rows" in kit.make_chart("T2", "bar", "name", "leads", "x")["error"]


@pytest.mark.parametrize("claim, source", [
    ("NPS is +25.", -25), ("NPS is -25.", 25),
    ("NPS is −25.", 25), ("Revenue is -$250.", 250),
    ("Revenue is $-250.", 250), ("Rate is 25%.", 25),
    ("Revenue is 25K.", 25),
])
def test_number_verification_rejects_wrong_sign_or_scale(claim, source):
    assert pa.unverified_numbers(claim, [{"value": source}])


@pytest.mark.parametrize("claim, source", [("NPS is −25.", -25), ("Revenue is -$250.", -250)])
def test_number_verification_preserves_signed_values(claim, source):
    assert pa.unverified_numbers(claim, [{"value": source}]) == []


def test_question_numbers_are_not_evidence(toolkit):
    model = FakeToolModel(tool_call("portfolio_summary"), AIMessage(content="There were 999999 attendees."),
                          AIMessage(content="There were 3 attendees."))
    answer = pa.ask_portfolio("Did we have 999999 attendees?", toolkit, model=model)
    assert answer.text == "There were 3 attendees."
    assert len(model.seen) == 3
