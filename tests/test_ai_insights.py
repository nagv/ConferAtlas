from pathlib import Path

import pytest

from core.ai.insights import (
    LLMNotConfiguredError,
    build_event_context,
    build_sponsor_context,
    fallback_event_summary,
    fallback_sponsor_summary,
    generate_pitch,
)
from core.analysis import event_kpis, feedback_summary, filter_dataset, sponsor_scorecards, sponsorship_results
from core.config import Settings


@pytest.fixture
def e1_scope(tiny_tables):
    return filter_dataset(tiny_tables, event_ids=["E1"])


@pytest.fixture
def s1_scope(tiny_tables):
    return filter_dataset(tiny_tables, sponsor_ids=["S1"])


def test_event_context_contains_computed_figures(e1_scope):
    ev = event_kpis(e1_scope).iloc[0]
    context = build_event_context(ev, sponsorship_results(e1_scope), feedback_summary(e1_scope["feedback.csv"]))
    assert "attended: 2" in context and "show-up rate 67%" in context
    assert "NPS -25" in context
    assert "leads delivered 6 vs target 6 (100% of target)" in context
    assert "Acme, Premier Booth: 5 / 4 (met)" in context
    assert "Brio, Standard Booth: 1 / 2 (missed)" in context
    assert "booth 4, demo 2" in context


def test_sponsor_context_contains_computed_figures(s1_scope):
    card = sponsor_scorecards(s1_scope).iloc[0]
    context = build_sponsor_context(card, sponsorship_results(s1_scope))
    assert "Events sponsored: 2; targets met at 1" in context
    assert "$167" in context  # $1,000 / 6 leads
    assert "Beta Live, Virtual Booth: 1 / 2 (missed)" in context


def test_fallback_summaries(e1_scope, s1_scope):
    ev = event_kpis(e1_scope).iloc[0]
    assert "Alpha Con" in fallback_event_summary(ev)
    assert "1 of 2 met their own target" in fallback_event_summary(ev)
    card = sponsor_scorecards(s1_scope).iloc[0]
    assert "target met at 1 of 2 events" in fallback_sponsor_summary(card)


def test_generate_without_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(llm_provider="anthropic", llm_model="claude-opus-5-5", data_dir=Path("unused"))
    with pytest.raises(LLMNotConfiguredError):
        generate_pitch("Alpha Con", "- some context", settings=settings)



def test_generated_pitch_rewrites_sign_flipped_figures():
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    model = GenericFakeChatModel(messages=iter([AIMessage(content="NPS was +25."), AIMessage(content="NPS was -25.")]))
    assert generate_pitch("Event", "NPS: -25", model=model) == "NPS was -25."


def test_generated_pitch_rejects_unverified_revision():
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from core.ai.llm import LLMError
    model = GenericFakeChatModel(messages=iter([AIMessage(content="NPS was +25."), AIMessage(content="NPS was +25.")]))
    with pytest.raises(LLMError, match="verified"):
        generate_pitch("Event", "NPS: -25", model=model)
