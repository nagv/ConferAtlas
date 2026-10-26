"""Evaluation of the Sponsor Renewal Desk: run each golden sponsor end to end, then score the pack.

Code-based metrics (no model):
- completed: the run reached an approved pack (every approval is given automatically here).
- first_pass / final_pass: the reviewer's checklist passed on the first draft / on the last one.
- figure_accuracy: the share of numbers in the pack traced to the fact sheet or a cited passage.
- package_valid, price_correct: the recommended package is a real 2026 package and its 2026 price
  is quoted correctly (prices come from the pricing brochure's source table).
- low_events_named: every event below 80% of target is named.
- deck_cited: sponsors with a deck get at least one [S#] citation from it.
- sensible_move: the recommendation (upgrade, keep, downgrade) is in the golden set's allowed range.
Reviewer catch rate: each approved pack is broken six ways (wrong price, dropped event, invented
number, missing section, fake citation, placeholder) and ``review_pack`` must reject every one.
LLM judge (a different model family): usefulness, grounding and tone, each 0 to 1.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from pydantic import BaseModel, Field

from core.ai.portfolio_agent import _NUMBER, unverified_numbers
from core.ai.renewal_desk.runner import RenewalRun, recommended_package
from core.ai.renewal_desk.tools import RenewalWorkspace, review_pack
from core.docgen import _ADD_ONS, _PACKAGE_INFO, _year_price

PRICE_YEAR = 2026
PACKAGE_PRICES = {name: _year_price(price, PRICE_YEAR) for name, _, price, _, _ in _PACKAGE_INFO}
ADD_ON_PRICES = {name: _year_price(price, PRICE_YEAR) for name, price in _ADD_ONS}
_RANK = {name: i for i, (name, *_rest) in enumerate(sorted(_PACKAGE_INFO, key=lambda p: p[2]))}
_CITATION = re.compile(r"\[(S\d+)\]")
CODE_METRICS = ("completed", "first_pass", "final_pass", "figure_accuracy", "package_valid", "price_correct",
                "low_events_named", "deck_cited", "sensible_move")
JUDGE_METRICS = ("usefulness", "grounding", "tone")


def load_golden(data_dir: str | Path) -> list[dict]:
    path = Path(data_dir) / "eval" / "renewal_golden.json"
    return json.loads(path.read_text(encoding="utf-8"))["sponsors"]


# --- running one sponsor --------------------------------------------------------------------------

def run_sponsor(sponsor: str, tables, history, documents, model, checkpointer, store, max_rounds: int = 4) -> dict:
    """One run for one sponsor, approving every pack it sends for approval (at most ``max_rounds`` times)."""
    workspace = RenewalWorkspace(tables, history=history, scope="all events on record", documents=documents)
    run = RenewalRun(workspace, model, checkpointer, store)
    started = time.perf_counter()
    status = run.start(f"Prepare a renewal pack for {sponsor}.")
    rounds = 0
    while status["pending"] and rounds < max_rounds:
        status = run.decide({s: {"type": "approve"} for s in status["pending"]})
        rounds += 1
    pack = workspace._pack(sponsor)
    return {"status": status, "workspace": workspace, "pack": pack, "seconds": time.perf_counter() - started}


# --- code-based metrics ---------------------------------------------------------------------------

def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", markdown, flags=re.M | re.S)
    return match.group(1) if match else ""


def current_package(facts: dict) -> str:
    events = sorted(facts.get("events", []), key=lambda e: str(e.get("date", "")))
    return events[-1]["package"] if events else ""


def move(current: str, recommended: str) -> str:
    if current not in _RANK or recommended not in _RANK:
        return "unknown"
    return "upgrade" if _RANK[recommended] > _RANK[current] else \
        "downgrade" if _RANK[recommended] < _RANK[current] else "keep"


def _money(value: int) -> str:
    return f"${value:,}"


def pack_metrics(golden: dict, result: dict) -> dict:
    pack, workspace = result["pack"], result["workspace"]
    if pack is None or pack.facts is None or not (pack.final or pack.draft):
        return {k: 0.0 for k in CODE_METRICS} | {"recommended": "", "move": "none"}
    text = pack.final or pack.draft
    sources = workspace.sources()
    numbers = [m.group(0) for m in _NUMBER.finditer(re.sub(r"(?m)^\s*\d+[.)]\s", " ", text))]
    misses = unverified_numbers(text, [pack.facts] + [s.text for s in sources])
    recommended = recommended_package(text)
    price = PACKAGE_PRICES.get(recommended)
    offer = _section(text, "Your offer")
    threshold = pack.facts.get("totals", {}).get("alert_threshold", 0.8)
    low = [e["name"] for e in pack.facts.get("events", []) if (e.get("attainment") or 1) < threshold]
    deck_ids = {s.id for s in sources if s.corpus == "sponsor_decks"}
    cited = set(_CITATION.findall(text))
    decision = move(current_package(pack.facts), recommended)
    first = pack.attempts <= 1 and bool(pack.review and pack.review.passed)
    return {
        "completed": float(pack.status == "approved"),
        "first_pass": float(first),
        "final_pass": float(bool(pack.review and pack.review.passed)),
        "figure_accuracy": round(1 - len(misses) / len(numbers), 3) if numbers else 1.0,
        "package_valid": float(recommended in PACKAGE_PRICES),
        "price_correct": float(price is not None and _money(price) in offer),
        "low_events_named": float(all(name in text for name in low)),
        "deck_cited": float(bool(cited & deck_ids)) if golden["has_deck"] else None,
        "sensible_move": float(decision in golden["allowed_moves"]),
        "recommended": recommended, "move": decision, "unverified": misses,
    }


# --- reviewer catch rate --------------------------------------------------------------------------

def mutations(text: str, facts: dict) -> dict[str, str]:
    """Six broken copies of a good pack, each with one fault the reviewer must catch."""
    out = {}
    if m := re.search(r"\$\d[\d,]*", _section(text, "Your offer")):
        wrong = int(m.group(0)[1:].replace(",", "")) + 1_234
        out["wrong_price"] = text.replace(m.group(0), f"${wrong:,}", 1)
    if events := [e["name"] for e in facts.get("events", [])]:
        out["dropped_event"] = text.replace(events[0], "one of your events")
    out["invented_number"] = text.replace("## Next steps", "We expect 1,337 extra leads next year.\n\n## Next steps", 1)
    out["missing_section"] = re.sub(r"(?m)^## Next steps\s*$", "", text, count=1)
    out["fake_citation"] = text.replace("## Next steps", "Your satisfaction score was excellent [S999].\n\n"
                                        "## Next steps", 1)
    out["placeholder"] = text.replace("## Next steps", "Discount: TBD.\n\n## Next steps", 1)
    return out


def catch_rate(results: list[tuple[dict, dict]], skill) -> dict:
    """Share of broken packs ``review_pack`` rejects, overall and per kind of fault."""
    caught, per_kind = [], {}
    for _golden, result in results:
        pack = result["pack"]
        if pack is None or not pack.final or pack.facts is None:
            continue
        sources = result["workspace"].sources()
        if not review_pack(pack.final, pack.facts, sources, skill, pack.name).passed:
            continue  # only clean packs can show whether a fault is caught
        for kind, broken in mutations(pack.final, pack.facts).items():
            hit = not review_pack(broken, pack.facts, sources, skill, pack.name).passed
            caught.append(hit)
            per_kind.setdefault(kind, []).append(hit)
    return {"catch_rate": round(sum(caught) / len(caught), 3) if caught else None, "broken_packs": len(caught),
            "per_kind": {k: round(sum(v) / len(v), 3) for k, v in per_kind.items()}}


# --- LLM judge -----------------------------------------------------------------------------------

class PackGrade(BaseModel):
    usefulness: float = Field(ge=0, le=1, description="Would a sponsorship manager send this with few or no edits? "
                                                       "Clear recommendation, concrete offer, next steps")
    grounding: float = Field(ge=0, le=1, description="Is the recommendation justified by the figures and passages "
                                                      "in the evidence, with no claims beyond them?")
    tone: float = Field(ge=0, le=1, description="Professional, sponsor-facing, honest about shortfalls")
    reason: str = Field(description="One sentence explaining the lowest score")


JUDGE_PROMPT = """You review renewal proposals that a conference organizer sends to its sponsors. Grade the PACK from 0 to 1 on usefulness, grounding and tone (definitions in the form). Use only the EVIDENCE to decide what is supported.

SPONSOR: {sponsor}
WHAT A GOOD PACK SHOULD DO FOR THIS SPONSOR: {why}

EVIDENCE (the sponsor's fact sheet, then the document passages the agents retrieved):
{evidence}

PACK:
{pack}"""


def judge_passages(text: str, sources: list, limit: int = 9_000) -> str:
    """The passages for the judge: every one the pack cites first (whatever its number), then others while
    they fit. Source ids count up across a run, so taking the first N characters would drop cited ones
    (the first evaluation run did, and the judge then called real citations "nonexistent")."""
    cited = set(_CITATION.findall(text))
    ordered = [s for s in sources if s.id in cited] + [s for s in sources if s.id not in cited]
    parts, used = [], 0
    for s in ordered:
        block = f"[{s.id}] ({s.corpus}) {s.text}"
        if used + len(block) > limit and s.id not in cited:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def judge_pack(grader, golden: dict, result: dict) -> dict:
    pack = result["pack"]
    if pack is None or not (pack.final or pack.draft):
        return {k: None for k in JUDGE_METRICS} | {"judge_reason": "no pack"}
    evidence = json.dumps(pack.facts, indent=1)[:6_000] + "\n\n" + judge_passages(pack.final or pack.draft,
                                                                                  result["workspace"].sources())
    prompt = JUDGE_PROMPT.format(sponsor=golden["sponsor"], why=golden["why"], evidence=evidence,
                                 pack=pack.final or pack.draft)
    why = "the judge returned no grade"
    for _ in range(2):  # a reasoning judge sometimes ends without filling in the form: try once more
        try:
            out = grader.invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - a judge failure is recorded, never counted as a score
            why = f"the judge failed: {type(exc).__name__}: {exc}"[:200]
            continue
        grade = out.get("parsed") or _grade_from_text(out.get("raw"))
        if grade is not None:
            return {"usefulness": grade.usefulness, "grounding": grade.grounding, "tone": grade.tone,
                    "judge_reason": grade.reason}
        finish = (getattr(out.get("raw"), "response_metadata", None) or {}).get("finish_reason")
        why = f"the judge returned no grade (finish reason: {finish})"
    return {k: None for k in JUDGE_METRICS} | {"judge_reason": why}


def _grade_from_text(message) -> PackGrade | None:
    """A grade written as JSON in the reply text instead of through the form."""
    text = getattr(message, "content", "") or ""
    if isinstance(text, list):
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return PackGrade.model_validate_json(match.group(0)) if match else None
    except ValueError:
        return None


# --- summary -------------------------------------------------------------------------------------

def summarize(rows: list[dict]) -> dict:
    def mean(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(values) / len(values), 3) if values else None

    seconds = sorted(r["seconds"] for r in rows)
    return {**{k: mean(k) for k in CODE_METRICS + JUDGE_METRICS}, "sponsors": len(rows),
            "median_seconds": round(seconds[len(seconds) // 2], 1) if seconds else None,
            "mean_tool_calls": mean("tool_calls"), "mean_model_calls": mean("model_calls")}
