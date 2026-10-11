"""Render pages headlessly and drive the sidebar filters like a user would."""
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.config import DEFAULT_DATA_DIR
from core.filters import CUSTOM, FilterState
from core.schema import load_dataset
from tests.conftest import refresh, seed_upload

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["overview", "event_drilldown", "sponsor_performance", "sponsor_report", "audience", "feedback",
         "ask", "ai_insights", "about"]

# Same setup as frontend/app.py, but renders a single page instead of the navigation.
SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from frontend.components.data_source import render_data_source_sidebar
from frontend.components.filters import render_filter_sidebar
from frontend.views import {page}
render_data_source_sidebar()
render_filter_sidebar()
{page}.render()
"""


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)  # AI page uses its offline fallback


def _page(page: str) -> AppTest:
    return AppTest.from_string(SCRIPT.format(root=str(ROOT), page=page), default_timeout=60)


def _ok(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def _events_card(at: AppTest) -> str:
    return next(m.value for m in at.metric if m.label == "Events")


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in [*at.markdown, *at.caption, *at.info])


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(page):
    at = _page(page).run()
    _ok(at)
    assert at.header[0].value


def test_main_entry_point_runs():
    at = AppTest.from_file(str(ROOT / "frontend" / "app.py"), default_timeout=60).run()
    _ok(at)


def test_cascade_apply_and_reset():
    at = _page("overview").run()
    assert len(at.multiselect(key="f_events").options) == 12

    at.multiselect(key="f_formats").select("virtual").run()
    events = at.multiselect(key="f_events").options
    assert len(events) == 4  # one virtual event per type
    assert "unapplied changes" in _text(at)  # drafted but not applied yet
    assert _events_card(at) == "12"

    at.button(key="apply_filters").click().run()
    _ok(at)
    assert at.session_state["filters"].formats == ("virtual",)
    assert _events_card(at) == "4" and "unapplied changes" not in _text(at)
    assert "Format: **Virtual**" in _text(at)

    at.button(key="reset_filters").click().run()
    _ok(at)
    assert at.session_state["filters"] == FilterState()
    assert at.multiselect(key="f_formats").value == []
    assert _events_card(at) == "12"


def test_earlier_choice_prunes_later_selection():
    at = _page("overview").run()
    at.multiselect(key="f_formats").select("virtual").run()
    at.multiselect(key="f_cities").select(at.multiselect(key="f_cities").options[0]).run()
    at.multiselect(key="f_formats").unselect("virtual").select("in-person").run()
    _ok(at)
    # Any city that has no in-person event is dropped rather than raising an error.
    assert set(at.multiselect(key="f_cities").value) <= set(at.multiselect(key="f_cities").options)


@pytest.mark.parametrize("page", [p for p in PAGES if p != "about"])  # About does not depend on filters
def test_filters_with_no_matches_show_empty_state(page):
    at = _page(page).run()
    at.selectbox(key="f_preset").select(CUSTOM).run()
    at.date_input(key="f_dates").set_value((date(2020, 1, 1), date(2020, 12, 31))).run()
    at.button(key="apply_filters").click().run()
    _ok(at)
    assert "match the current filters" in " ".join(i.value for i in at.info)


def test_sponsor_filter_narrows_sponsor_report():
    at = _page("sponsor_report").run()
    label = at.selectbox(key="f_sponsor").options[1]  # first real sponsor after "All sponsors"
    # AppTest's select()/select_index() match display labels, which a format_func breaks,
    # so set the underlying sponsor_id instead.
    sponsors = load_dataset(DEFAULT_DATA_DIR)["sponsors.csv"]
    sponsor_id = sponsors.loc[sponsors["name"] == label, "sponsor_id"].item()
    at.selectbox(key="f_sponsor").set_value(sponsor_id).run()
    at.button(key="apply_filters").click().run()
    _ok(at)
    assert at.session_state["filters"].sponsor_id == sponsor_id
    assert at.selectbox(key="sponsor_report_sponsor").options == [label]
    assert "Sponsor: **" + label + "**" in _text(at)


def test_overview_options_drive_chart_and_scorecard():
    at = _page("overview").run()
    _ok(at)
    assert [m.label for m in at.metric] == ["Events", "Registrations", "Show-up rate", "Avg NPS",
                                            "Sponsor revenue", "Leads"]
    assert len(at.dataframe[0].value) == 10  # default: top 10 by registrations
    regs = at.dataframe[0].value["registrations"].tolist()
    assert regs == sorted(regs, reverse=True)

    at.selectbox(key="ov_top").select("Top 5").run()
    at.selectbox(key="ov_sort").select("NPS").run()
    _ok(at)
    nps = at.dataframe[0].value["nps"].tolist()
    assert len(nps) == 5 and nps == sorted(nps, reverse=True)

    at.selectbox(key="ov_top").select("All events").run()
    assert len(at.dataframe[0].value) == 12


def test_overview_compares_with_previous_period():
    at = _page("overview").run()
    at.selectbox(key="f_preset").select(CUSTOM).run()
    at.date_input(key="f_dates").set_value((date(2025, 1, 1), date(2025, 12, 31))).run()
    at.button(key="apply_filters").click().run()
    _ok(at)
    cards = {m.label: m for m in at.metric}
    assert cards["Events"].value == "6"
    assert cards["Events"].delta == "+100% vs prev."  # 6 events in 2025 vs 3 in 2024
    assert cards["Show-up rate"].delta.endswith("pts vs prev.")
    # Equal length in days: 2025 has 365, so the window starts 2 Jan in leap-year 2024.
    assert "compared vs 2 Jan 2024" in _text(at) and "31 Dec 2024" in _text(at)


def test_overview_all_time_shows_values_without_misleading_deltas():
    at = _page("overview").run()
    assert all(not m.delta for m in at.metric)  # AppTest reports "no delta" as ""
    assert "Pick a date range" in _text(at)


def test_drilldown_session_filters():
    at = _page("event_drilldown").run()
    _ok(at)
    total = len(at.dataframe[0].value)
    at.slider(key="dd_rating").set_value((4.5, 5.0)).run()
    _ok(at)
    high = at.dataframe[0].value
    assert 0 < len(high) < total and (high["rating"] >= 4.5).all()
    assert f"{len(high)} of {total} sessions match" in _text(at)
    track = at.multiselect(key="dd_tracks").options[0]
    at.slider(key="dd_rating").set_value((1.0, 5.0)).run()
    at.multiselect(key="dd_tracks").select(track).run()
    assert set(at.dataframe[0].value["track"]) == {track}


def test_audience_filters_and_returning_attendees():
    at = _page("audience").run()
    _ok(at)
    cards = {m.label: m.value for m in at.metric}
    assert cards["Registrations"] == "13.4K" and cards["Returning attendees"] == "20%"  # 13,361 in K
    at.multiselect(key="aud_status").select("No-show").run()
    _ok(at)
    cards = {m.label: m.value for m in at.metric}
    assert cards["Attendees"] == "0" and cards["Registrations"] == "2,789"
    assert "no attendees to split" in _text(at)


def test_sponsor_performance_flags_and_filters():
    at = _page("sponsor_performance").run()
    _ok(at)
    cards = {m.label: m for m in at.metric}
    assert list(cards) == ["Contract value", "Leads vs target", "Cost per lead", "Booth visits", "Renewal rate",
                           "Below 80% of target"]
    below = int(cards["Below 80% of target"].value)
    assert below > 0 and len(at.dataframe[1].value) == below  # one row per flagged sponsorship
    assert "came in under 80%" in " ".join(w.value for w in at.warning)

    at.multiselect(key="sp_sources").select("booth").run()  # booth only: everyone looks below target
    assert not at.exception  # the red st.error alert is expected here, so not _ok()
    assert "Lead-source filter on" in _text(at)
    assert at.error and "below 80% of their lead target overall" in at.error[0].value

    at.multiselect(key="sp_sources").unselect("booth").run()
    at.multiselect(key="sp_tiers").select("platinum").run()
    _ok(at)
    assert set(at.dataframe[0].value["tier"]) == {"Platinum"}


def test_feedback_analyzer_without_api_key():
    at = _page("feedback").run()
    _ok(at)
    assert [m.label for m in at.metric][:4] == ["Responses", "Avg rating (of 5)", "NPS", "Commented"]
    assert at.button(key="fb_analyse").disabled
    assert "AI feedback analysis needs an AI model" in _text(at) and "20 distinct comments" in _text(at)


def _fake_analysis():
    from core.ai.feedback import CommentTag, FeedbackAnalysis, TaggedComment, Theme  # noqa: F401
    return FeedbackAnalysis(
        themes=[Theme(name="Audio problems", count=120, example_quote="Audio problems made it hard to follow.")],
        overall_sentiment="mixed", action_items=["Test audio before each session", "Share slides", "Keep to time"],
        tagged=[TaggedComment(comment="Audio problems made it hard to follow.", mentions=120, avg_rating=1.8,
                              theme="Audio problems", sentiment="negative")],
        unique_comments=1, mentions=120, skipped_comments=0, batches=1, warnings=["Example warning"])


def test_feedback_analyzer_button_shows_results(monkeypatch):
    import backend.services.feedback as page
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    calls = []
    monkeypatch.setattr(page, "analyze_feedback", lambda fb, settings, **kw: calls.append(len(fb)) or _fake_analysis())
    at = _page("feedback").run()
    at.button(key="fb_analyse").click().run()
    _ok(at)
    assert calls and calls[0] > 0  # the filtered feedback was passed in
    assert {m.label: m.value for m in at.metric}["Overall sentiment"] == "Mixed"
    assert "Test audio before each session" in _text(at) and "Audio problems" in _text(at)
    assert at.warning[0].value == "Example warning"
    assert at.dataframe[0].value["theme"].tolist() == ["Audio problems"]


def test_feedback_analyzer_reports_timeouts(monkeypatch):
    import backend.services.feedback as page
    from core.ai.feedback import FeedbackAnalysisError
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def timeout(fb, settings, **kw):
        raise FeedbackAnalysisError("timeout", "The AI request timed out after 60 seconds.")
    monkeypatch.setattr(page, "analyze_feedback", timeout)
    at = _page("feedback").run()
    at.button(key="fb_analyse").click().run()
    assert not at.exception
    assert "timed out after 60 seconds" in at.error[0].value and "narrow the selection" in at.error[0].value


def test_ask_page_without_api_key():
    at = _page("ask").run()
    _ok(at)
    assert "Chat needs an AI model" in _text(at)
    starters = [b for b in at.button if b.key and b.key.startswith("ask_starter_")]
    assert len(starters) == 6 and all(b.disabled for b in starters)
    assert at.chat_input[0].disabled


def _fake_answer(question, toolkit, history):
    from core.ai.portfolio_agent import AgentAnswer
    toolkit.portfolio_summary()
    toolkit.event_kpis(sort_by="show_up_rate", top_n=3)
    toolkit.make_chart("T2", "bar", "name", "show_up_rate", "Show-up rate by event")
    return AgentAnswer(text=f"Answer to: {question}", calculations=list(toolkit.ledger), charts=list(toolkit.charts),
                       unverified_numbers=["999"], tool_calls=3)


def test_ask_page_shows_answer_calculations_and_chart(monkeypatch):
    import backend.services.chat as page
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    seen = {}

    def fake(question, toolkit, history, **kw):
        seen["scope"], seen["events"] = toolkit.scope, len(toolkit.t["events.csv"])
        return _fake_answer(question, toolkit, history)
    monkeypatch.setattr(page, "ask_portfolio", fake)
    at = _page("ask").run()
    at.button(key="ask_starter_1").click().run()
    _ok(at)
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert "Answer to: Which sponsors are furthest below" in _text(at)
    assert at.expander[0].label.startswith("View calculations (3 tool calls)")
    assert "999" in at.warning[0].value  # untraceable figures are flagged
    assert len(at.get("plotly_chart")) == 1
    assert seen == {"scope": "all data", "events": 12}

    at.chat_input[0].set_value("And for virtual events?").run()
    _ok(at)
    assert len(at.chat_message) == 4
    at.button(key="ask_clear").click().run()
    assert len(at.chat_message) == 0


def test_ask_page_reports_agent_errors(monkeypatch):
    import backend.services.chat as page
    from core.ai.portfolio_agent import AgentError
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def boom(question, toolkit, history, **kw):
        raise AgentError("timeout", "The AI request timed out after 45 seconds.")
    monkeypatch.setattr(page, "ask_portfolio", boom)
    at = _page("ask").run()
    at.chat_input[0].set_value("Anything").run()
    assert not at.exception
    assert "timed out after 45 seconds" in _text(at) and "simpler or more specific" in _text(at)


def test_generate_sponsor_summary_without_api_key():
    at = _page("sponsor_performance").run()
    at.button(key="sp_generate").click().run()
    _ok(at)
    text = _text(at)
    assert "Template (no AI)" in text and ": sponsorship report" in text
    assert text.count(":material/check_circle:") == 6  # every checklist item passes
    assert any(b.label == "Download report (.md)" for b in at.get("download_button"))


AI_NOTICES = {"ai_insights": "Writing the pitch or brief with AI", "feedback": "AI feedback analysis",
              "ask": "Chat needs an AI model", "sponsor_performance": "An AI-written sponsor report"}


@pytest.mark.parametrize("page", PAGES)
def test_every_page_works_without_an_api_key(page):
    at = _page(page).run()
    _ok(at)
    assert at.header[0].value
    if page in AI_NOTICES:
        notice = " ".join(i.value for i in at.info)
        assert AI_NOTICES[page] in notice and "OPENROUTER_API_KEY" in notice and ".env" in notice
        assert "Still works" in notice


def test_ai_usage_panel_shows_calls_and_totals(monkeypatch):
    from datetime import datetime

    from core.ai.llm import CallRecord, UsageRecorder
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MTOK", "5")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MTOK", "25")
    script = SCRIPT.format(root=str(ROOT), page="overview") + (
        "\nfrom frontend.components.ai_usage import render_ai_usage_panel\n"
        "import streamlit as st\nrender_ai_usage_panel(st.sidebar.container())\n")
    at = AppTest.from_string(script, default_timeout=60)
    recorder = UsageRecorder()
    recorder.add(CallRecord(datetime(2026, 10, 11, 12, 0), "Portfolio chat", "m", 1200, 300, 0.0135, 2.4, "ok"))
    recorder.add(CallRecord(datetime(2026, 10, 11, 12, 1), "Portfolio chat", "m", None, None, None, 30.0, "error",
                            "timeout"))
    at.session_state["ai_usage"] = recorder
    at.run()
    _ok(at)
    assert at.sidebar.expander[-1].label == "AI usage · 2 calls"
    totals, calls = [d.value for d in at.sidebar.dataframe][-2:]
    assert dict(zip(totals["Session total"], totals["Value"]))["Est. cost"] == "$0.0135"
    assert calls["Status"].tolist() == ["error: timeout", "ok"]  # newest first
    at.sidebar.button(key="ai_usage_reset").click().run()
    assert at.sidebar.expander[-1].label == "AI usage · 0 calls"


DESCRIPTIONS = {"overview": "How every event performed", "event_drilldown": "Everything about one event",
                "sponsor_performance": "How sponsors fared", "sponsor_report": "One sponsor's results",
                "audience": "Who registered and attended", "feedback": "What attendees said",
                "ask": "Ask questions in plain English", "ai_insights": "AI-written sponsor pitches",
                "about": "What this app is"}


@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_a_consistent_header(page):
    at = _page(page).run()
    assert at.header[0].value and DESCRIPTIONS[page] in at.caption[0].value
    chips = at.markdown[0].value
    assert ":gray-badge[" in chips and "All events" in chips and "Synthetic demo data" in chips


def test_filter_chips_show_applied_filters():
    at = _page("overview").run()
    at.multiselect(key="f_formats").select("virtual").run()
    at.button(key="apply_filters").click().run()
    chips = at.markdown[0].value
    assert ":blue-badge[:material/filter_alt: Format: Virtual]" in chips and "All events" not in chips


def test_empty_state_offers_a_reset():
    at = _page("audience").run()
    at.selectbox(key="f_preset").select(CUSTOM).run()
    at.date_input(key="f_dates").set_value((date(2020, 1, 1), date(2020, 12, 31))).run()
    at.button(key="apply_filters").click().run()
    assert "No registrations match the current filters" in at.info[0].value
    at.button(key="empty_reset_registrations").click().run()
    _ok(at)
    assert at.session_state["filters"] == FilterState() and at.metric  # data is back


def test_about_page_says_the_data_is_synthetic():
    at = _page("about").run()
    _ok(at)
    assert "The data in this demo is synthetic" in at.warning[0].value
    assert "12 events" in _text(at) and "NPS" in _text(at)


def test_navigation_is_grouped_and_includes_about():
    at = AppTest.from_file(str(ROOT / "frontend" / "app.py"), default_timeout=60).run()
    _ok(at)
    assert any("What does that mean?" in str(getattr(el, "proto", "")) for el in at.sidebar)



@pytest.mark.parametrize("change", ["tone", "dataset", "model", "source", "filters"])
def test_ai_insights_hides_draft_after_inputs_change(monkeypatch, tiny_tables, change):
    import backend.services.insights as page
    from core.analysis import filter_dataset
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    selected = filter_dataset(tiny_tables)
    monkeypatch.setattr(page, "generate_pitch", lambda *args, **kw: "Previous generated draft")
    at = _page("ai_insights")
    stored = seed_upload(at, selected, digest="original-dataset")
    at.run()
    next(b for b in at.button if b.label == "Generate").click().run()
    assert "Previous generated draft" in _text(at)
    if change == "tone":
        next(s for s in at.selectbox if s.label == "Tone").select("Warm and relationship-focused").run()
    elif change == "model":
        monkeypatch.setenv("LLM_MODEL", "different-model")
        at.run()
    elif change == "source":
        seed_upload(at, selected, digest="replacement-dataset")
        at.run()
    elif change == "filters":
        at.multiselect(key="f_formats").select("virtual").run()
        at.button(key="apply_filters").click().run()
    else:
        selected["events.csv"].loc[:, "name"] = "Replacement event"
        refresh(stored)
        at.run()
    assert "Previous generated draft" not in _text(at)



def test_real_feedback_requires_review_for_current_comments(monkeypatch, tiny_tables):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    at = _page("feedback")
    stored = seed_upload(at, tiny_tables)
    at.run()
    assert at.button(key="fb_analyse").disabled
    next(c for c in at.checkbox if c.label.startswith("I have reviewed")).check().run()
    assert not at.button(key="fb_analyse").disabled
    tiny_tables["feedback.csv"].loc[0, "rating"] = 1
    refresh(stored)
    at.run()
    assert at.button(key="fb_analyse").disabled
