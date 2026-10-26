"""Tests for the Renewal Desk evaluation: metrics, the move rule, and the reviewer's catch rate (offline)."""
import pytest

from core.ai.renewal_desk import evaluate as ev
from core.ai.renewal_desk.tools import RenewalWorkspace
from core.ai.skills import load_skill
from core.analysis import filter_dataset
from core.config import DEFAULT_DATA_DIR
from tests.test_renewal_tools import GOOD_PACK, FakeDocs

GOLDEN = {"id": "T1", "sponsor": "Acme", "has_deck": True, "allowed_moves": ["keep", "upgrade"], "why": "test"}


@pytest.fixture
def result(tiny_tables):
    """What run_sponsor returns, for a run whose approved pack is GOOD_PACK."""
    ws = RenewalWorkspace(filter_dataset(tiny_tables), history=tiny_tables, documents=FakeDocs())
    ws.list_sponsors(sponsor="Acme")
    ws.sponsor_facts("Acme")
    ws.search("pricing", "Premier Booth 2026")
    ws.search("sponsor_decks", "Acme goals")
    ws.save_draft("Acme", GOOD_PACK)
    ws.check_pack("Acme")
    ws.finalize_pack("Acme")
    return {"workspace": ws, "pack": ws.packs["Acme"], "status": {"error": ""}, "seconds": 1.0}


def test_golden_file_loads_six_varied_sponsors():
    golden = ev.load_golden(DEFAULT_DATA_DIR)
    assert len(golden) == 6 and {g["has_deck"] for g in golden} == {True, False}
    assert all(set(g["allowed_moves"]) <= {"upgrade", "keep", "downgrade"} for g in golden)


def test_prices_come_from_the_brochure_source():
    assert ev.PACKAGE_PRICES["Premier Booth"] == 51_000 and ev.PACKAGE_PRICES["Title Sponsor"] == 100_500
    assert ev.ADD_ON_PRICES["Keynote speaking slot (20 minutes)"] == 16_000


def test_move_is_ranked_by_package_price():
    assert ev.move("Premier Booth", "Title Sponsor") == "upgrade"
    assert ev.move("Premier Booth", "Premier Booth") == "keep"
    assert ev.move("Premier Booth", "Standard Booth") == "downgrade"
    assert ev.move("Premier Booth", "Gold Plus") == "unknown"


def test_a_good_pack_scores_full_marks(result):
    m = ev.pack_metrics(GOLDEN, result)
    assert m["recommended"] == "Premier Booth"
    assert m["move"] == "upgrade"  # Acme's latest event (Beta Live) used a Virtual Booth
    assert m["sensible_move"] == 1.0
    for key in ("completed", "first_pass", "final_pass", "figure_accuracy", "package_valid", "price_correct",
                "low_events_named", "deck_cited"):
        assert m[key] == 1.0, key


def test_wrong_price_and_unnamed_low_event_are_scored(result):
    pack = result["pack"]
    pack.final = (GOOD_PACK.replace("$51,000", "$45,000")
                  .replace("- **Beta Live** (Virtual Booth): 1 lead against 2, 50% of target, at $500 per lead. "
                           "This was below 80% of target.\n", ""))
    m = ev.pack_metrics(GOLDEN, result)
    assert m["price_correct"] == 0.0 and m["low_events_named"] == 0.0 and m["figure_accuracy"] < 1.0
    assert "$45,000" in m["unverified"]


def test_a_missing_pack_scores_zero(result):
    result["pack"] = None
    assert ev.pack_metrics(GOLDEN, result)["completed"] == 0.0


def test_the_reviewer_catches_every_kind_of_fault(result):
    catch = ev.catch_rate([(GOLDEN, result)], load_skill("sponsor-renewal-pack"))
    assert catch["broken_packs"] == 6 and catch["catch_rate"] == 1.0
    assert set(catch["per_kind"]) == {"wrong_price", "dropped_event", "invented_number", "missing_section",
                                      "fake_citation", "placeholder"}


def test_judge_scores_are_passed_through_and_a_missing_grade_is_not_a_score(result):
    class Grader:
        def __init__(self, parsed):
            self.parsed = parsed

        def invoke(self, prompt):
            assert "Acme" in prompt and "Premier Booth" in prompt  # the pack and evidence are in the prompt
            return {"parsed": self.parsed}

    grade = ev.PackGrade(usefulness=0.9, grounding=1.0, tone=0.8, reason="Clear.")
    assert ev.judge_pack(Grader(grade), GOLDEN, result)["grounding"] == 1.0
    assert ev.judge_pack(Grader(None), GOLDEN, result)["usefulness"] is None


def test_summary_averages_and_skips_missing_values():
    rows = [{"completed": 1.0, "deck_cited": None, "seconds": 50, "tool_calls": 10, "model_calls": 20},
            {"completed": 0.0, "deck_cited": 1.0, "seconds": 70, "tool_calls": 14, "model_calls": 30}]
    s = ev.summarize(rows)
    assert s["completed"] == 0.5 and s["deck_cited"] == 1.0 and s["median_seconds"] == 70
    assert s["mean_tool_calls"] == 12


def test_the_judge_sees_every_cited_passage_even_with_high_numbers():
    from core.rag.agent_tools import Source
    sources = [Source(f"S{i}", "pricing", "d", "c", "p", "x" * 900, 0.9) for i in range(1, 31)]
    shown = ev.judge_passages("Price [S27] and terms [S30].", sources, limit=3_000)
    assert "[S27]" in shown and "[S30]" in shown and shown.index("[S27]") < shown.index("[S1]")
