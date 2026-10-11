"""Tests for core/ai/feedback.py using a scripted fake model (no network, no API key)."""
from pathlib import Path

import pandas as pd
import pytest

from core.ai.feedback import (
    UNTAGGED,
    ActionSummary,
    BatchResult,
    CommentTag,
    FeedbackAnalysisError,
    Theme,
    analyze_feedback,
    batch_comments,
    estimate_tokens,
    prepare_comments,
)
from core.ai.insights import LLMNotConfiguredError
from core.config import Settings


class APITimeoutError(Exception):
    """Stands in for a provider's timeout exception (matched by class name)."""


class FakeModel:
    """Mimics ``model.with_structured_output(schema, include_raw=True).invoke(messages)``."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, list]] = []

    def with_structured_output(self, schema, include_raw=False, method=None):
        fake = self
        self.methods = getattr(self, "methods", []) + [method]

        class Runner:
            def invoke(self, messages):
                fake.calls.append((schema.__name__, list(messages)))
                response = fake.responses.pop(0)
                if isinstance(response, BaseException):
                    raise response
                return response(messages) if callable(response) else response

        return Runner()


def ok(parsed):
    return {"parsed": parsed, "parsing_error": None, "raw": None}


def bad(error="missing field 'tags'"):
    return {"parsed": None, "parsing_error": error, "raw": None}


@pytest.fixture
def feedback():
    return pd.DataFrame({
        "event_id": ["E1"] * 6,
        "session_id": ["X1"] * 6,
        "rating": [5, 4, 2, 3, 3, 1],
        "comment": ["Great talk", "Great talk", "Too long", "Fine", None, "  "],
    })


def batch_result(**overrides):
    base = dict(
        themes=[Theme(name="Content", count=99, example_quote="Great talk"),
                Theme(name="Timing", count=1, example_quote="Too long")],
        overall_sentiment="mixed",
        action_items=["Keep sessions to time", "Book more speakers like this", "Share slides", "Extra"],
        tags=[CommentTag(comment_id=1, theme="Content", sentiment="positive"),
              CommentTag(comment_id=2, theme="Content", sentiment="neutral"),
              CommentTag(comment_id=3, theme="Timing", sentiment="negative")],
    )
    return BatchResult(**{**base, **overrides})


# --- preparation and batching ---------------------------------------------------

def test_prepare_comments_collapses_duplicates_and_drops_blanks(feedback):
    prepared = prepare_comments(feedback)
    assert prepared["comment"].tolist() == ["Great talk", "Fine", "Too long"]  # most mentioned first
    assert prepared["comment_id"].tolist() == [1, 2, 3]
    assert prepared["mentions"].tolist() == [2, 1, 1]
    assert prepared.loc[0, "avg_rating"] == pytest.approx(4.5)
    assert prepare_comments(feedback.iloc[4:]).empty


def test_long_comments_are_truncated():
    df = pd.DataFrame({"rating": [3], "comment": ["x" * 5000]})
    assert len(prepare_comments(df).loc[0, "comment"]) == 1000


def test_batch_comments_respects_limits(feedback):
    prepared = prepare_comments(feedback)
    per_line = max(estimate_tokens(c) for c in prepared["comment"])
    batches, skipped = batch_comments(prepared, batch_tokens=per_line, max_total_tokens=10_000)
    assert [len(b) for b in batches] == [1, 1, 1] and skipped == 0
    batches, skipped = batch_comments(prepared, batch_tokens=10_000, max_total_tokens=per_line * 2)
    assert sum(len(b) for b in batches) == 2 and skipped == 1  # least-mentioned comment left out
    assert batches[0]["comment"].iloc[0] == "Great talk"
    with pytest.raises(ValueError):
        batch_comments(prepared, batch_tokens=0)


# --- happy path -------------------------------------------------------------------

@pytest.mark.parametrize("provider, method", [("openrouter", "function_calling"), ("anthropic", "json_schema"), ("openai", "function_calling")])
def test_single_batch_analysis(feedback, provider, method):
    model = FakeModel(ok(batch_result()))
    result = analyze_feedback(feedback, model=model,
                              settings=Settings(provider, "test-model", Path("."), llm_max_retries=0))
    assert [(t.name, t.count) for t in result.themes] == [("Content", 3), ("Timing", 1)]  # counted from tags
    assert result.themes[0].example_quote == "Great talk"
    assert result.overall_sentiment == "mixed"
    assert result.action_items == ["Keep sessions to time", "Book more speakers like this", "Share slides"]
    assert (result.unique_comments, result.mentions, result.batches, result.skipped_comments) == (3, 4, 1, 0)
    tagged = {t.comment: t for t in result.tagged}
    assert tagged["Too long"].theme == "Timing" and tagged["Too long"].sentiment == "negative"
    prompt = model.calls[0][1][1][1]
    assert "[1] (x2, avg rating 4.5) Great talk" in prompt
    assert result.warnings == []
    assert model.methods == [method]


def test_model_may_return_a_plain_dict(feedback):
    result = analyze_feedback(feedback, model=FakeModel(ok(batch_result().model_dump())))
    assert result.themes[0].name == "Content"


def test_bad_tags_and_invented_quotes_are_repaired(feedback):
    parsed = batch_result(
        themes=[Theme(name="Content", count=2, example_quote="An inspiring keynote!")],  # not a real comment
        tags=[CommentTag(comment_id=1, theme="Content", sentiment="positive"),
              CommentTag(comment_id=42, theme="Content", sentiment="positive")],  # unknown id; 2 and 3 untagged
    )
    result = analyze_feedback(feedback, model=FakeModel(ok(parsed)))
    assert result.themes[0].example_quote == "Great talk"
    assert {t.comment: t.theme for t in result.tagged}["Fine"] == UNTAGGED
    text = " ".join(result.warnings)
    assert "comment ids not in the batch" in text and "did not tag 2" in text and "Replaced the example quote" in text


# --- invalid output -----------------------------------------------------------------

def test_invalid_output_gets_one_repair_attempt(feedback):
    model = FakeModel(bad(), ok(batch_result()))
    result = analyze_feedback(feedback, model=model)
    assert result.themes and len(model.calls) == 2
    repair_message = model.calls[1][1][-1][1]
    assert "did not match the required format" in repair_message and "missing field" in repair_message


def test_invalid_output_twice_raises(feedback):
    with pytest.raises(FeedbackAnalysisError) as exc:
        analyze_feedback(feedback, model=FakeModel(bad(), bad("still wrong")))
    assert exc.value.kind == "invalid_output" and "still wrong" in str(exc.value)


# --- timeouts and provider errors ---------------------------------------------------

def test_timeout_on_first_batch_raises(feedback):
    with pytest.raises(FeedbackAnalysisError) as exc:
        analyze_feedback(feedback, model=FakeModel(APITimeoutError("read timed out")), timeout_s=30)
    assert exc.value.kind == "timeout" and "30 seconds" in str(exc.value)


def test_provider_error_raises(feedback):
    with pytest.raises(FeedbackAnalysisError) as exc:
        analyze_feedback(feedback, model=FakeModel(ConnectionError("network down")))
    assert exc.value.kind == "provider" and "Check your internet connection" in str(exc.value)  # friendly


def _per_comment_batches(feedback):
    prepared = prepare_comments(feedback)
    return max(estimate_tokens(c) for c in prepared["comment"])  # one comment per batch


def test_multi_batch_reuses_theme_names_and_summarises(feedback):
    def batch(comment_id, theme, quote, sentiment):
        return ok(BatchResult(themes=[Theme(name=theme, count=1, example_quote=quote)], overall_sentiment="mixed",
                              action_items=[f"Fix {theme.lower()}"],
                              tags=[CommentTag(comment_id=comment_id, theme=theme, sentiment=sentiment)]))
    model = FakeModel(batch(1, "Content", "Great talk", "positive"), batch(2, "Content", "Fine", "neutral"),
                      batch(3, "Timing", "Too long", "negative"),
                      ok(ActionSummary(overall_sentiment="positive", action_items=["A", "B", "C"])))
    result = analyze_feedback(feedback, model=model, batch_tokens=_per_comment_batches(feedback))
    assert result.batches == 3 and [c[0] for c in model.calls][-1] == "ActionSummary"
    assert "reuse these exact names" in model.calls[1][1][1][1] and "Content" in model.calls[1][1][1][1]
    assert [(t.name, t.count) for t in result.themes] == [("Content", 3), ("Timing", 1)]
    assert result.overall_sentiment == "positive" and result.action_items == ["A", "B", "C"]


def test_timeout_after_first_batch_keeps_partial_results(feedback):
    first = ok(BatchResult(themes=[Theme(name="Content", count=2, example_quote="Great talk")],
                           overall_sentiment="positive", action_items=["Keep it up"],
                           tags=[CommentTag(comment_id=1, theme="Content", sentiment="positive")]))
    model = FakeModel(first, APITimeoutError("slow"))
    result = analyze_feedback(feedback, model=model, batch_tokens=_per_comment_batches(feedback))
    assert result.batches == 1 and result.unique_comments == 1 and result.skipped_comments == 2
    assert "Batch 2 of 3 failed" in result.warnings[0]
    assert result.action_items == ["Keep it up"]


def test_time_budget_stops_starting_new_batches(feedback):
    ticks = iter([0, 500, 1000])
    first = ok(BatchResult(themes=[Theme(name="Content", count=2, example_quote="Great talk")],
                           overall_sentiment="positive", action_items=["Keep it up"],
                           tags=[CommentTag(comment_id=1, theme="Content", sentiment="positive")]))
    result = analyze_feedback(feedback, model=FakeModel(first), batch_tokens=_per_comment_batches(feedback),
                              time_budget_s=100, clock=lambda: next(ticks))
    assert result.batches == 1 and "time limit" in result.warnings[0]


def test_failed_summary_falls_back_to_first_batch(feedback):
    def batch(comment_id):
        return ok(BatchResult(themes=[Theme(name="Content", count=1, example_quote="Great talk")],
                              overall_sentiment="mixed", action_items=["First batch action"],
                              tags=[CommentTag(comment_id=comment_id, theme="Content", sentiment="positive")]))
    model = FakeModel(batch(1), batch(2), batch(3), APITimeoutError("slow"))
    result = analyze_feedback(feedback, model=model, batch_tokens=_per_comment_batches(feedback))
    assert result.action_items == ["First batch action"] and "Could not combine" in result.warnings[-1]


# --- configuration -----------------------------------------------------------------

def test_missing_api_key_raises_before_any_call(feedback, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(llm_provider="anthropic", llm_model="claude-opus-5-5", data_dir=Path("unused"))
    with pytest.raises(LLMNotConfiguredError):
        analyze_feedback(feedback, settings=settings)


def test_no_comments_raises_value_error(feedback):
    with pytest.raises(ValueError, match="no comments"):
        analyze_feedback(feedback.iloc[4:], model=FakeModel())


def test_falls_back_when_json_schema_is_unsupported(feedback):
    class OldModel(FakeModel):
        def with_structured_output(self, schema, include_raw=False, method=None):
            if method == "json_schema":
                raise NotImplementedError("json_schema not supported")
            return super().with_structured_output(schema, include_raw)
    assert analyze_feedback(feedback, model=OldModel(ok(batch_result())),
                            settings=Settings("anthropic", "test-model", Path("."), llm_max_retries=0)).themes


def test_batches_respect_the_output_token_cap(feedback):
    from core.ai.feedback import max_comments_per_batch
    assert max_comments_per_batch(2048) == 72 and max_comments_per_batch(500) == 10
    batches, _ = batch_comments(prepare_comments(feedback), max_comments=2)
    assert [len(b) for b in batches] == [2, 1]


@pytest.mark.parametrize("comment", [
    "Email me at alex@example.com about the audio",
    "Call +1 (212) 555-1234 about the audio",
    "See https://example.com/people/alex about the audio",
    "My SSN is 123-45-6789, the audio was poor",
])
def test_feedback_redacts_contact_details_before_model(feedback, comment):
    selected = feedback.iloc[:1].copy()
    selected.loc[selected.index[0], "comment"] = comment
    prepared = prepare_comments(selected)
    assert prepared.iloc[0]["comment"] != comment
    assert "[REDACTED" in prepared.iloc[0]["comment"]
    model = FakeModel(ok(batch_result()))
    result = analyze_feedback(selected, model=model)
    sent = str(model.calls)
    assert comment not in sent
    assert comment not in str(result.model_dump())
