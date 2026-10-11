"""Tests for the Sponsor Report Writer skill and core/ai/sponsor_report.py (offline)."""
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from core.ai.insights import LLMNotConfiguredError
from core.ai.portfolio_agent import assert_no_raw_records
from core.ai.skills import load_skill
from core.ai.sponsor_report import (
    ReportError,
    build_sponsor_facts,
    check_report,
    generate_sponsor_report,
    render_prompt,
    report_file,
    template_report,
)
from core.analysis import filter_dataset
from core.config import Settings


@pytest.fixture(scope="module")
def skill():
    return load_skill("sponsor-report-writer")


@pytest.fixture
def facts(tiny_tables):
    return build_sponsor_facts(filter_dataset(tiny_tables), "S1", scope="all data")


class FakeModel:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def invoke(self, messages):
        self.seen.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return AIMessage(content=reply)


def _failed(checks):
    return [c.item for c in checks if not c.passed]


# --- the skill itself -----------------------------------------------------------------

def test_skill_files_load(skill):
    assert skill.name == "sponsor-report-writer" and "sponsor" in skill.description
    assert skill.sections == ("Executive summary", "Audience reached", "Engagement", "Leads vs. target",
                              "Highlights", "Recommendations")
    assert len(skill.checklist) == 6 and "tool" not in skill.checklist[0].lower()
    assert "fact sheet" in skill.checklist[1] and "event" in skill.checklist[2]
    assert "$facts" in skill.user_template and skill.system_prompt
    assert skill.example_facts["sponsor"]["name"] == "Northwind Analytics"


def test_example_report_follows_its_own_checklist(skill):
    # Keeps the example honest: it must pass the checks the model's reports must pass.
    checks = check_report(skill.example_report, skill.example_facts, skill)
    assert _failed(checks) == [], [c.detail for c in checks if not c.passed]


def test_skill_needs_frontmatter(tmp_path):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text("# No frontmatter", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        load_skill("broken", skills_dir=tmp_path)


# --- fact sheet -------------------------------------------------------------------------

def test_fact_sheet(facts):
    assert facts["sponsor"]["name"] == "Acme" and facts["sponsor"]["tier"] == "gold"
    tot = facts["totals"]
    assert (tot["events"], tot["leads"], tot["leads_target"], tot["targets_met"], tot["events_below_80"]) == (2, 6, 6, 1, 1)
    assert tot["spend"] == pytest.approx(1000) and tot["cost_per_lead"] == pytest.approx(166.6667, abs=1e-3)
    assert [e["name"] for e in facts["events"]] == ["Alpha Con", "Beta Live"]
    assert facts["events"][1]["attainment"] == pytest.approx(0.5)
    assert (facts["audience"]["registrations"], facts["audience"]["attendees"]) == (6, 3)
    assert facts["audience"]["top_countries"][0] == {"value": "Canada", "share": pytest.approx(0.6667, abs=1e-3)}
    assert facts["engagement"]["sessions"] == 3 and facts["engagement"]["booth_visits_event_total"] == 2
    assert_no_raw_records(facts)


def test_fact_sheet_respects_filters(tiny_tables):
    facts = build_sponsor_facts(filter_dataset(tiny_tables, event_ids=["E2"]), "S1", history=tiny_tables)
    assert [e["name"] for e in facts["events"]] == ["Beta Live"]
    assert facts["totals"]["spend"] == pytest.approx(500)  # contract split over both events, even when filtered
    with pytest.raises(KeyError):
        build_sponsor_facts(filter_dataset(tiny_tables, event_ids=["E2"]), "S2")


# --- checklist ---------------------------------------------------------------------------

def test_template_report_passes_every_check(facts, skill, tiny_tables):
    report = template_report(facts, skill)
    assert report.passed, [c.detail for c in report.checks if not c.passed]
    assert "Below 80% of target: Beta Live" in report.markdown and report.generated_by == "Template (no AI)"
    brio = template_report(build_sponsor_facts(filter_dataset(tiny_tables), "S2"), skill)
    assert brio.passed


def test_checks_catch_each_problem(facts, skill):
    good = template_report(facts, skill).markdown
    assert _failed(check_report(good.replace("## Highlights", "## Wins"), facts, skill)) == [skill.checklist[0]]
    assert _failed(check_report(good + "\nWe expect 9,999 leads next year.\n", facts, skill)) == [skill.checklist[1]]
    no_beta = good.replace("Beta Live", "the virtual event")
    assert skill.checklist[2] in _failed(check_report(no_beta, facts, skill))
    assert skill.checklist[3] in _failed(check_report(no_beta, facts, skill))
    unframed = good.replace(" across all booths, not only yours", "")
    assert _failed(check_report(unframed, facts, skill)) == [skill.checklist[4]]
    assert _failed(check_report(good + "\nCost per lead: TBD\n", facts, skill)) == [skill.checklist[5]]


# --- generation ---------------------------------------------------------------------------

def test_prompt_contains_skill_example_and_facts(facts, skill):
    system, user = render_prompt(facts, skill)
    assert system[0] == "system" and "account manager" in system[1]
    text = user[1]
    assert "Acme" in text and '"Alpha Con"' in text and "Northwind Analytics" in text
    assert "## Checklist" in text and "$facts" not in text and "$sponsor_name" not in text


def test_generated_report_passes(facts, skill):
    model = FakeModel("```markdown\n" + template_report(facts, skill).markdown + "\n```")
    report = generate_sponsor_report(facts, skill, model=model)
    assert report.passed and report.markdown.startswith("# Acme") and len(model.seen) == 1
    assert report.generated_by.startswith("AI (")


def test_failed_checks_get_one_revision(facts, skill):
    good = template_report(facts, skill).markdown
    model = FakeModel(good.replace("## Engagement", "## Activity") + "\nAbout 12,345 visitors.\n", good)
    report = generate_sponsor_report(facts, skill, model=model)
    assert report.passed and len(model.seen) == 2
    revision_request = model.seen[1][-1][1]
    assert "fails these checklist items" in revision_request and "12,345" in revision_request


def test_still_failing_after_revision_is_reported(facts, skill):
    bad = template_report(facts, skill).markdown + "\nForecast: 50,000 leads.\n"
    report = generate_sponsor_report(facts, skill, model=FakeModel(bad, bad))
    assert not report.passed and _failed(report.checks) == [skill.checklist[1]]


class APITimeoutError(Exception):
    pass


def test_errors_and_missing_key(facts, skill, monkeypatch):
    with pytest.raises(ReportError) as exc:
        generate_sponsor_report(facts, skill, model=FakeModel(APITimeoutError("slow")))
    assert exc.value.kind == "timeout"
    with pytest.raises(ReportError) as exc:
        generate_sponsor_report(facts, skill, model=FakeModel(ConnectionError("down")))
    assert exc.value.kind == "provider"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(llm_provider="anthropic", llm_model="claude-opus-5-5", data_dir=Path("unused"))
    with pytest.raises(LLMNotConfiguredError):
        generate_sponsor_report(facts, skill, settings=settings)


def test_download_file_has_provenance(facts, skill):
    text = report_file(template_report(facts, skill), facts, "2026-10-11")
    assert text.startswith("# Acme: sponsorship report")
    assert "Generated by ConferAtlas on 2026-10-11 with the Sponsor Report Writer skill" in text
