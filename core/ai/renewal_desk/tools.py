"""The Renewal Desk's tools, bound to one run (one dataset selection and its document search).

Guardrails
- Figures come from ``build_sponsor_facts`` and the Week 1 analysis functions only. The fact sheet a
  tool returns is also kept here, so ``check_pack`` verifies a draft against the real facts, not
  against whatever the agent copied into its files.
- Document searches reuse ``DocumentSearch`` (Week 2): sources are numbered S1, S2, ... across the
  run, carry quoted text only, and are data, never instructions.
- ``check_pack`` is the skill's checklist evaluated in code. It never calls a model.
- ``finalize_pack`` is the only write. The agent pauses before it (``interrupt_on``), so a person
  approves, edits or rejects every pack. Approval saves the pack here; nothing is ever sent.
- A run is capped at ``MAX_SPONSORS`` sponsors and ``MAX_TOOL_CALLS`` tool calls.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from core import analysis as an
from core.ai.portfolio_agent import _clean, assert_no_raw_records, unverified_numbers
from core.ai.renewal_desk.schemas import (FinalizeArgs, ListSponsorsArgs, PackArgs, PackCheck, PackStatus,
                                          Review, SponsorArgs)
from core.ai.skills import Skill, load_skill
from core.ai.sponsor_report import _PLACEHOLDERS, _section_text, build_sponsor_facts

SKILL_NAME = "sponsor-renewal-pack"
MAX_SPONSORS = 5           # sponsors per run
MAX_TOOL_CALLS = 120       # all tools together, across every subagent in a run
MAX_DRAFT_ATTEMPTS = 2     # check_pack calls per sponsor before the pack goes to a person as "needs edit"

_CITATION = re.compile(r"\[(S\d+)\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


class BudgetExceeded(RuntimeError):
    """The run used all its tool calls."""


@dataclass
class SponsorPack:
    sponsor_id: str
    name: str
    status: PackStatus = "queued"
    facts: dict | None = None
    draft: str = ""
    review: Review | None = None
    attempts: int = 0
    final: str = ""
    feedback: list[str] = field(default_factory=list)  # reviewer rejections, in order


class RenewalWorkspace:
    """Tools and state for one run. ``documents`` is a ``core.rag.agent_tools.DocumentSearch`` or None."""

    def __init__(self, tables: an.Tables, history: an.Tables | None = None, scope: str = "all data",
                 documents=None, skill: Skill | None = None, max_sponsors: int | None = None,
                 allowed: list[str] | None = None):
        """``max_sponsors`` and ``allowed`` hold the person's request in code: how many sponsors the run may
        take on, and (when sponsors were named) exactly which ones, whatever the model asks for."""
        self.t = tables
        self.max_sponsors = min(max_sponsors or MAX_SPONSORS, MAX_SPONSORS)
        self.allowed = sorted(allowed) if allowed else None
        self.history = history if history is not None else tables
        self.scope = scope
        self.documents = documents
        self.skill = skill or load_skill(SKILL_NAME)
        self.packs: dict[str, SponsorPack] = {}   # by sponsor name
        self.calls: Counter = Counter()
        sponsors = tables["sponsors.csv"]
        self._ids = dict(zip(sponsors["name"], sponsors["sponsor_id"]))

    # -- helpers --
    def _count(self, tool: str) -> None:
        if sum(self.calls.values()) >= MAX_TOOL_CALLS:
            raise BudgetExceeded(f"The run used all {MAX_TOOL_CALLS} tool calls.")
        self.calls[tool] += 1

    def _pack(self, sponsor: str) -> SponsorPack | None:
        if sponsor in self.packs:
            return self.packs[sponsor]
        match = next((n for n in self.packs if n.lower() == sponsor.strip().lower()), None)
        return self.packs.get(match) if match else None

    def _not_listed(self, sponsor: str) -> dict:
        names = ", ".join(self.packs) or "none yet; call list_sponsors first"
        return {"error": f"'{sponsor}' is not one of this run's sponsors. Sponsors in this run: {names}."}

    def set_status(self, sponsor: str, status: PackStatus) -> None:
        if (pack := self._pack(sponsor)) is not None:
            pack.status = status

    # -- tools --
    def list_sponsors(self, tier: str | None = None, sponsor: str | None = None, top_n: int = MAX_SPONSORS) -> dict:
        """The sponsors for this run, lowest lead attainment first. Adds them to the run."""
        self._count("list_sponsors")
        cards = an.sponsor_scorecards(self.t)
        renewals = an.sponsor_renewals(self.history["sponsorships.csv"], self.history["events.csv"])
        cards = cards.merge(renewals[["sponsor_id", "renewal_status"]], on="sponsor_id", how="left")
        if tier:
            cards = cards[cards["tier"] == tier]
        if sponsor:
            cards = cards[cards["sponsor"].str.lower().str.contains(re.escape(sponsor.lower()))]
        if self.allowed:
            cards = cards[cards["sponsor"].str.lower().isin({a.lower() for a in self.allowed})]
        if cards.empty:
            return {"error": "No sponsors match in the current selection.", "tier": tier, "sponsor": sponsor}
        room = self.max_sponsors - len(self.packs)
        cards = cards.sort_values("attainment", na_position="last").head(max(0, min(top_n, room)))
        for row in cards.itertuples(index=False):
            self.packs.setdefault(row.sponsor, SponsorPack(row.sponsor_id, row.sponsor))
        rows = cards[["sponsor", "tier", "events", "leads", "leads_target", "attainment", "renewal_status"]]
        result = _clean({"scope": self.scope, "sponsors": rows.to_dict("records"),
                         "note": f"This run is limited to {self.max_sponsors} sponsor"
                                 f"{'' if self.max_sponsors == 1 else 's'}"
                                 + (f" ({', '.join(self.allowed)})" if self.allowed else "")
                                 + "; these are all it may prepare."})
        assert_no_raw_records(result)
        return result

    def sponsor_facts(self, sponsor: str) -> dict:
        """The fact sheet for one sponsor of this run (the only source of its figures)."""
        self._count("sponsor_facts")
        pack = self._pack(sponsor)
        if pack is None:
            return self._not_listed(sponsor)
        pack.status = "researching"
        try:
            pack.facts = build_sponsor_facts(self.t, pack.sponsor_id, history=self.history, scope=self.scope)
        except KeyError as exc:
            pack.status = "failed"
            return {"error": str(exc).strip("'\"")}
        assert_no_raw_records(pack.facts)
        return pack.facts

    def search(self, corpus: str, query: str) -> dict:
        self._count(f"search_{corpus}")
        if self.documents is None:
            return {"corpus": corpus, "query": query, "found": False,
                    "note": "Document search is not available, so write 'no record found' for this part."}
        result = self.documents.search(corpus, query)
        if not result.get("found"):
            result["note"] = ("Nothing relevant was found. Try one broader query (for example without the event "
                              "name); if that also finds nothing, write 'no record found' for this part.")
        return result

    def save_draft(self, sponsor: str, markdown: str) -> dict:
        """The writer's draft, kept on the pack so the reviewer checks exactly this text."""
        self._count("save_draft")
        pack = self._pack(sponsor)
        if pack is None:
            return self._not_listed(sponsor)
        pack.draft, pack.status = markdown, "in_review"
        return {"sponsor": pack.name, "saved": True, "words": len(markdown.split()),
                "note": "Saved. The reviewer checks this draft next."}

    def check_pack(self, sponsor: str, markdown: str | None = None) -> dict:
        """The skill checklist, evaluated in code, on the saved draft (or on ``markdown``, which replaces it)."""
        self._count("check_pack")
        pack = self._pack(sponsor)
        if pack is None:
            return self._not_listed(sponsor)
        if pack.facts is None:
            return {"error": f"Get the fact sheet for {pack.name} with sponsor_facts before checking a draft."}
        markdown = markdown or pack.draft
        if not markdown:
            return {"error": f"There is no saved draft for {pack.name}; the writer saves one with save_draft."}
        pack.draft, pack.attempts = markdown, pack.attempts + 1
        review = review_pack(markdown, pack.facts, self.sources(), self.skill, pack.name)
        pack.review = review
        if review.passed:
            pack.status = "in_review"
        else:
            pack.feedback.append("; ".join(review.problems))
            pack.status = "needs_edit" if pack.attempts >= MAX_DRAFT_ATTEMPTS else "drafting"
        out = {"sponsor": pack.name, "passed": review.passed, "attempt": pack.attempts,
               "attempts_left": max(0, MAX_DRAFT_ATTEMPTS - pack.attempts),
               "checks": [c.model_dump() for c in review.checks]}
        if not review.passed and pack.attempts >= MAX_DRAFT_ATTEMPTS:
            out["note"] = "No attempts left: send this draft for approval as it is; a person will edit it."
        return out

    def finalize_pack(self, sponsor: str, markdown: str | None = None) -> dict:
        """Runs only after a person approves (or edits) the pack. Saves it; sends nothing.

        Without ``markdown`` the reviewed draft is saved exactly as checked; a person's edit supplies it."""
        self._count("finalize_pack")
        pack = self._pack(sponsor)
        if pack is None:
            return self._not_listed(sponsor)
        markdown = markdown or pack.draft
        if not markdown:
            return {"error": f"There is no checked draft for {pack.name}; run the reviewer first."}
        pack.final, pack.status = markdown, "approved"
        return {"sponsor": pack.name, "saved": True, "words": len(markdown.split()),
                "note": "Approved and saved. It was not sent; the sponsorship manager sends it."}

    def sources(self) -> list:
        return list(self.documents.sources) if self.documents is not None else []

    # -- persistence (so a run can continue after a restart) --
    def snapshot(self) -> dict:
        """Everything a run has gathered, JSON-safe: packs, numbered sources and tool counts."""
        packs = [{**{k: v for k, v in vars(p).items() if k != "review"},
                  "review": p.review.model_dump() if p.review else None} for p in self.packs.values()]
        return _clean({"packs": packs, "sources": [vars(s) for s in self.sources()], "calls": dict(self.calls),
                       "max_sponsors": self.max_sponsors, "allowed": self.allowed})

    def restore(self, snapshot: dict) -> None:
        from core.rag.agent_tools import Source

        self.packs = {}
        for raw in snapshot.get("packs", []):
            review = Review.model_validate(raw["review"]) if raw.get("review") else None
            self.packs[raw["name"]] = SponsorPack(**{**raw, "review": review})
        if self.documents is not None:
            self.documents.sources[:] = [Source(**s) for s in snapshot.get("sources", [])]
        self.calls = Counter(snapshot.get("calls", {}))
        self.max_sponsors = snapshot.get("max_sponsors", self.max_sponsors)
        self.allowed = snapshot.get("allowed", self.allowed)

    def _isolated(self, func):
        """An unexpected error in one tool fails that sponsor only: the agent gets the error and moves on.

        The budget still stops the whole run, on purpose."""
        import functools
        import logging

        @functools.wraps(func)
        def run(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - reported to the agent instead of ending every pack
                logging.getLogger(__name__).exception("Renewal desk tool %s failed", func.__name__)
                sponsor = kwargs.get("sponsor") or (args[0] if args else "")
                if (pack := self._pack(sponsor)) is not None:
                    pack.status = "failed"
                    pack.feedback.append(f"Tool {func.__name__} failed: {exc}")
                return {"error": f"{func.__name__} failed ({type(exc).__name__}: {exc}). "
                                 "Mark this sponsor failed and continue with the next one."}
        return run

    # -- LangChain wiring --
    def tools(self) -> dict[str, list]:
        """Tools grouped by the agent that uses them."""
        from langchain_core.tools import StructuredTool

        from core.rag.agent_tools import CORPUS_TOOLS, SearchArgs

        def make(func, schema, description, name=None):
            return StructuredTool.from_function(func=self._isolated(func), name=name or func.__name__,
                                                description=description, args_schema=schema)

        def searcher(corpus):
            name, description = CORPUS_TOOLS[corpus]
            return make(lambda query: self.search(corpus, query), SearchArgs, description, name)

        list_tool = make(self.list_sponsors, ListSponsorsArgs,
                         "The sponsors for this run (by tier or name), lowest lead attainment first, with leads, "
                         "target and renewal status. Adds them to the run; at most 5 per run.")
        facts_tool = make(self.sponsor_facts, SponsorArgs,
                          "The fact sheet for one sponsor of this run: profile, renewal status, totals, one entry "
                          "per event (package, leads, target, attainment, cost per lead), audience and engagement. "
                          "The only source of the sponsor's figures.")
        draft_tool = make(self.save_draft, PackArgs,
                          "Save your renewal pack draft for one sponsor (the full Markdown). Call it once per draft; "
                          "a new call replaces the earlier draft.")
        check_tool = make(self.check_pack, SponsorArgs,
                          "Check the sponsor's saved draft against the skill checklist: sections, every number "
                          "traced to the fact sheet or a cited passage, events cited, events below 80% named, "
                          "package named as in the pricing passages, citations, placeholders.")
        final_tool = make(self.finalize_pack, FinalizeArgs,
                          "Send one sponsor's reviewed pack to a person for approval, and save it once approved. "
                          "Pass only the sponsor name: the reviewed draft is used as it is. The person may approve, "
                          "edit or reject it with feedback. It never sends anything to the sponsor.")
        return {
            "orchestrator": [list_tool, final_tool],
            "analyst": [facts_tool, searcher("sponsor_decks"), searcher("event_reports")],
            "advisor": [facts_tool, searcher("pricing")],
            "writer": [facts_tool, draft_tool],
            "reviewer": [check_tool],
        }


# --- the checklist in code ------------------------------------------------------------

def review_pack(markdown: str, facts: dict, sources: list, skill: Skill, sponsor: str) -> Review:
    """The renewal-pack checklist, item by item. ``sources`` are the run's ``Source`` objects."""
    labels = list(skill.checklist) + [f"Check {i}" for i in range(len(skill.checklist), 6)]
    headings = [h.strip() for h in re.findall(r"^## (.+?)\s*$", markdown, flags=re.M)]
    expected = list(skill.sections)
    structure_ok = [h.lower() for h in headings] == [s.lower() for s in expected]
    missing = [s for s in expected if s.lower() not in {h.lower() for h in headings}]
    structure_detail = ("" if structure_ok else f"Missing: {', '.join(missing)}" if missing else
                        f"Found {', '.join(headings)}; expected exactly {', '.join(expected)} in that order")

    texts = [s.text for s in sources]
    misses = unverified_numbers(markdown, [facts] + texts)
    events = [e["name"] for e in facts.get("events", [])]
    uncited = [n for n in events if n not in markdown]
    results_text = _section_text(markdown, "Your results")
    threshold = facts.get("totals", {}).get("alert_threshold", an.LEAD_TARGET_ALERT)
    low = [e["name"] for e in facts.get("events", []) if e.get("attainment") is not None
           and e["attainment"] < threshold and e["name"] not in results_text]

    pricing = [s.text for s in sources if s.corpus == "pricing"]
    named = _BOLD.findall(_section_text(markdown, "Recommended package"))
    if not pricing:
        package_ok, package_detail = False, "No pricing passages were retrieved, so the package cannot be checked"
    elif not named:
        package_ok, package_detail = False, "Put the package name in bold under Recommended package"
    else:
        package_ok = any(named[0] in text for text in pricing)
        package_detail = "" if package_ok else f"'{named[0]}' is not named in the pricing passages"
    placeholders = sorted(set(m.group(0) for m in _PLACEHOLDERS.finditer(markdown)))

    known = {s.id for s in sources}
    bad_citations = sorted(set(_CITATION.findall(markdown)) - known, key=lambda c: int(c[1:]))

    results = [
        (structure_ok, structure_detail),
        (not misses, f"Not in the fact sheet or a passage: {', '.join(misses)}" if misses else ""),
        (not uncited, f"Not mentioned: {', '.join(uncited)}" if uncited else ""),
        (not low, f"Below target but not named under Your results: {', '.join(low)}" if low else ""),
        (package_ok, package_detail),
        (not placeholders, f"Found: {', '.join(placeholders)}" if placeholders else ""),
    ]
    checks = [PackCheck(label=label, ok=ok, detail=detail) for label, (ok, detail) in zip(labels, results)]
    checks.append(PackCheck(label="Citations point to retrieved passages", ok=not bad_citations,
                            detail=f"Unknown: {', '.join(bad_citations)}" if bad_citations else ""))
    return Review(sponsor=sponsor, passed=all(c.ok for c in checks), checks=checks)
