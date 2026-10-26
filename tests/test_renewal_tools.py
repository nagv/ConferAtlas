"""Tests for the Renewal Desk tools and the renewal-pack checklist (offline: no model, fake document search)."""
import pytest

from core.ai.renewal_desk import tools as rt
from core.ai.renewal_desk.tools import RenewalWorkspace, review_pack
from core.ai.skills import load_skill
from core.analysis import filter_dataset
from core.rag.agent_tools import Source

PRICING = ("Sponsorship packages 2026. Premier Booth, in-person / hybrid, $51,000, 10 x 20 ft. "
           "Sponsored breakout session $8,500. Lead guarantee: below 70% of target earns a credit of 15%.")
DECK = "Acme partnership review: goal is lead generation; we proposed a Premier Booth with a demo slot."


class FakeDocs:
    """Stands in for DocumentSearch: one canned passage per corpus, numbered S1, S2, ..."""

    def __init__(self, texts=None):
        self.texts = texts if texts is not None else {"pricing": PRICING, "sponsor_decks": DECK}
        self.sources = []

    def search(self, corpus, query):
        text = self.texts.get(corpus)
        if text is None:
            return {"corpus": corpus, "query": query, "found": False, "note": "Nothing relevant was found."}
        source = Source(f"S{len(self.sources) + 1}", corpus, f"{corpus}_doc", "c1", corpus, text, 0.9)
        self.sources.append(source)
        return {"corpus": corpus, "query": query, "found": True,
                "sources": [{"source_id": source.id, "citation": corpus, "text": text}]}


GOOD_PACK = """# Acme: renewal proposal

*Covers all data.*

## Summary

Across 2 events you received 6 leads against a target of 6, or 100% of target. We recommend keeping the Premier Booth.

## Your results

- **Alpha Con** (Premier Booth): 5 leads against 4, 125% of target, at $100 per lead.
- **Beta Live** (Virtual Booth): 1 lead against 2, 50% of target, at $500 per lead. This was below 80% of target.

## What we promised

- Your deck set lead generation as the goal [S2]: you met your overall target.

## Recommended package

Keep the **Premier Booth** (same package), because Alpha Con reached 125% of target.

## Your offer

- **Premier Booth**, 2026 price: $51,000 [S1].

## Next steps

- Confirm the package for 2026.
"""


@pytest.fixture(scope="module")
def skill():
    return load_skill("sponsor-renewal-pack")


@pytest.fixture
def ws(tiny_tables, skill):
    return RenewalWorkspace(filter_dataset(tiny_tables), history=tiny_tables, documents=FakeDocs(), skill=skill)


def _ready(ws):
    """A workspace with Acme listed, its facts loaded and pricing + deck passages retrieved (S1, S2)."""
    ws.list_sponsors(sponsor="Acme")
    ws.sponsor_facts("Acme")
    ws.search("pricing", "Premier Booth 2026 price")
    ws.search("sponsor_decks", "Acme goals")
    return ws


def _failed(result):
    return [c["label"] for c in result["checks"] if not c["ok"]]


# --- the skill ----------------------------------------------------------------------------

def test_skill_loads_with_six_sections_and_checks(skill):
    assert skill.sections == ("Summary", "Your results", "What we promised", "Recommended package",
                              "Your offer", "Next steps")
    assert len(skill.checklist) == 6 and "$deck_passages" in skill.user_template
    assert skill.example_report.startswith("# Northwind Analytics: renewal proposal")


def test_skill_example_passes_its_own_structure_check(skill):
    review = review_pack(skill.example_report, skill.example_facts, [], skill, "Northwind Analytics")
    assert review.checks[0].ok  # sections
    assert review.checks[2].ok  # every event named


# --- list_sponsors / sponsor_facts ------------------------------------------------------------

def test_list_sponsors_filters_by_tier_and_adds_to_run(ws):
    result = ws.list_sponsors(tier="gold")
    assert [s["sponsor"] for s in result["sponsors"]] == ["Acme"]
    assert set(ws.packs) == {"Acme"}


def test_list_sponsors_sorts_lowest_attainment_first_and_caps_the_run(ws):
    ws.max_sponsors = 1
    result = ws.list_sponsors()
    assert [s["sponsor"] for s in result["sponsors"]] == ["Brio"]  # 1/2 leads before Acme's 6/6
    assert ws.list_sponsors()["sponsors"] == [] and set(ws.packs) == {"Brio"}


def test_the_persons_choice_limits_the_run_whatever_the_model_asks(tiny_tables, skill):
    # "the 1 gold sponsor": the model asked for 5 (the default) and used to get all of them
    one = RenewalWorkspace(filter_dataset(tiny_tables), history=tiny_tables, documents=FakeDocs(), skill=skill,
                           max_sponsors=1)
    assert len(one.list_sponsors(top_n=5)["sponsors"]) == 1 and len(one.packs) == 1
    assert "limited to 1 sponsor" in one.list_sponsors()["note"]
    # named sponsors: only those, even if the model searches more widely
    named = RenewalWorkspace(filter_dataset(tiny_tables), history=tiny_tables, documents=FakeDocs(), skill=skill,
                             max_sponsors=1, allowed=["Acme"])
    assert [s["sponsor"] for s in named.list_sponsors()["sponsors"]] == ["Acme"]
    assert "error" in named.list_sponsors(sponsor="Brio")
    restored = RenewalWorkspace(filter_dataset(tiny_tables), documents=FakeDocs(), skill=skill)
    restored.restore(named.snapshot())
    assert restored.max_sponsors == 1 and restored.allowed == ["Acme"]  # the limit survives a restart


def test_list_sponsors_reports_no_match(ws):
    assert "error" in ws.list_sponsors(sponsor="Nobody")


def test_sponsor_facts_needs_a_listed_sponsor(ws):
    assert "not one of this run's sponsors" in ws.sponsor_facts("Acme")["error"]


def test_sponsor_facts_returns_and_keeps_the_fact_sheet(ws):
    ws.list_sponsors(sponsor="acme")
    facts = ws.sponsor_facts("ACME")  # names match case-insensitively
    assert facts["totals"]["leads"] == 6 and [e["name"] for e in facts["events"]] == ["Alpha Con", "Beta Live"]
    assert ws.packs["Acme"].facts is facts and ws.packs["Acme"].status == "researching"


# --- search ---------------------------------------------------------------------------------

def test_empty_search_tells_the_agent_to_broaden_once(ws):
    result = ws.search("event_reports", "Alpha Con sponsor results")
    assert not result["found"] and "broader" in result["note"] and "no record found" in result["note"]


def test_search_without_documents_says_so(tiny_tables, skill):
    ws = RenewalWorkspace(filter_dataset(tiny_tables), documents=None, skill=skill)
    assert "not available" in ws.search("pricing", "price")["note"]


# --- check_pack ------------------------------------------------------------------------------

def test_good_pack_passes_every_check(ws):
    result = _ready(ws).check_pack("Acme", GOOD_PACK)
    assert result["passed"], _failed(result)
    assert ws.packs["Acme"].status == "in_review" and ws.packs["Acme"].draft == GOOD_PACK


def test_invented_number_fails(ws):
    result = _ready(ws).check_pack("Acme", GOOD_PACK.replace("$51,000", "$45,000"))
    assert _failed(result) == [load_skill("sponsor-renewal-pack").checklist[1]]
    assert "$45,000" in result["checks"][1]["detail"]


def test_hidden_low_event_missing_section_and_bad_citation_fail(ws):
    bad = (GOOD_PACK.replace("- **Beta Live** (Virtual Booth): 1 lead against 2, 50% of target, at $500 per lead. "
                             "This was below 80% of target.\n", "")
           .replace("## Next steps\n\n- Confirm the package for 2026.\n", "")
           .replace("[S2]", "[S9]"))
    checklist = load_skill("sponsor-renewal-pack").checklist
    failed = _failed(_ready(ws).check_pack("Acme", bad))
    assert failed == [checklist[0], checklist[2], checklist[3], "Citations point to retrieved passages"]


def test_package_must_be_named_in_pricing_passages(ws):
    result = _ready(ws).check_pack("Acme", GOOD_PACK.replace("Keep the **Premier Booth**", "Keep the **Gold Plus**"))
    assert "'Gold Plus' is not named" in result["checks"][4]["detail"]


def test_package_check_fails_without_pricing_passages(tiny_tables, skill):
    ws = RenewalWorkspace(filter_dataset(tiny_tables), history=tiny_tables, documents=FakeDocs({}), skill=skill)
    ws.list_sponsors(sponsor="Acme")
    ws.sponsor_facts("Acme")
    assert "No pricing passages" in ws.check_pack("Acme", GOOD_PACK)["checks"][4]["detail"]


def test_two_failed_drafts_go_to_a_person_as_needs_edit(ws):
    _ready(ws)
    first = ws.check_pack("Acme", "# Acme\n\n" + "no sections here " * 10)
    assert not first["passed"] and first["attempts_left"] == 1 and ws.packs["Acme"].status == "drafting"
    second = ws.check_pack("Acme", "# Acme\n\n" + "still no sections " * 10)
    assert "No attempts left" in second["note"] and ws.packs["Acme"].status == "needs_edit"
    assert len(ws.packs["Acme"].feedback) == 2


def test_check_pack_needs_the_fact_sheet_first(ws):
    ws.list_sponsors(sponsor="Acme")
    assert "sponsor_facts" in ws.check_pack("Acme", GOOD_PACK)["error"]


def test_reviewer_checks_the_writers_saved_draft(ws):
    _ready(ws)
    assert "no saved draft" in ws.check_pack("Acme")["error"]
    assert ws.save_draft("Acme", GOOD_PACK)["saved"] and ws.packs["Acme"].status == "in_review"
    assert ws.check_pack("Acme")["passed"]


# --- finalize_pack, budget, wiring ---------------------------------------------------------------

def test_finalize_saves_the_reviewed_draft_and_sends_nothing(ws):
    _ready(ws)
    assert "no checked draft" in ws.finalize_pack("Acme")["error"]
    ws.save_draft("Acme", GOOD_PACK)
    result = ws.finalize_pack("Acme")
    assert result["saved"] and "not sent" in result["note"]
    assert ws.packs["Acme"].final == GOOD_PACK and ws.packs["Acme"].status == "approved"


def test_finalize_with_a_persons_edit_saves_the_edit(ws):
    _ready(ws)
    ws.save_draft("Acme", GOOD_PACK)
    ws.finalize_pack("Acme", GOOD_PACK + "\nEdited by the manager.\n")
    assert ws.packs["Acme"].final.endswith("Edited by the manager.\n")


def test_budget_stops_the_run(ws, monkeypatch):
    monkeypatch.setattr(rt, "MAX_TOOL_CALLS", 2)
    ws.list_sponsors()
    ws.sponsor_facts("Acme")
    with pytest.raises(rt.BudgetExceeded):
        ws.search("pricing", "price")


def test_tools_are_grouped_by_agent_and_only_the_orchestrator_can_save(ws):
    groups = {k: [t.name for t in v] for k, v in ws.tools().items()}
    assert groups["orchestrator"] == ["list_sponsors", "finalize_pack"]
    assert groups["analyst"] == ["sponsor_facts", "search_sponsor_decks", "search_event_reports"]
    assert groups["advisor"] == ["sponsor_facts", "search_pricing"]
    assert groups["writer"] == ["sponsor_facts", "save_draft"] and groups["reviewer"] == ["check_pack"]
    assert all("finalize_pack" not in names for agent, names in groups.items() if agent != "orchestrator")
    search = next(t for t in ws.tools()["advisor"] if t.name == "search_pricing")
    assert search.invoke({"query": "Premier Booth price"})["found"]
