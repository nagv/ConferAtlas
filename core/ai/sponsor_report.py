"""Sponsor reports written with the Sponsor Report Writer skill (``skills/sponsor-report-writer``).

1. ``build_sponsor_facts`` computes the fact sheet for one sponsor from the
   (filtered) dataset with ``core.analysis``: profile, totals, per-event
   results, audience reached and engagement. Aggregates only, no person records.
2. ``generate_sponsor_report`` fills the skill's prompt template and asks the
   LLM for a Markdown report.
3. ``check_report`` runs the skill's checklist in code: exact section
   structure, numbers traceable to the fact sheet, every event named, events
   below 80% named in "Leads vs. target", booth visits framed as event-wide,
   no placeholders. Failed checks get one revision request; whatever still
   fails is returned so the UI can show it.
4. ``template_report`` builds the same structure without an LLM, used when
   no API key is set or the AI request fails.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from string import Template

import pandas as pd

from core import analysis as an
from core.ai.portfolio_agent import _clean, _text, unverified_numbers
from core.ai.skills import Skill, load_skill
from core.ai.llm import LLMError, UsageRecorder, build_chat_model, resolve_settings, retry_call
from core.config import Settings

SKILL_NAME = "sponsor-report-writer"
REQUEST_TIMEOUT_S = 90.0
TOP_N_SEGMENTS = 3
_BOOTH_FRAMING = ("all booths", "event-wide", "not only yours", "every booth", "not just yours")
_PLACEHOLDERS = re.compile(r"\{[^}]*\}|\$[a-z_]+\b|\bTBD\b|\bXX+\b|lorem ipsum|<fact_sheet>", re.I)


@dataclass
class CheckResult:
    item: str      # the checklist line from SKILL.md
    passed: bool
    detail: str = ""


@dataclass
class SponsorReport:
    markdown: str
    checks: list[CheckResult]
    generated_by: str              # "AI (<model>)" or "Template (no AI)"
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


class ReportError(RuntimeError):
    """``kind`` is 'timeout' or 'provider'."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


# --- fact sheet -------------------------------------------------------------------

def _top(values: pd.Series, n: int = TOP_N_SEGMENTS) -> list[dict]:
    shares = values.value_counts(normalize=True).head(n)
    return [{"value": str(k), "share": float(v)} for k, v in shares.items()]


def build_sponsor_facts(t: an.Tables, sponsor_id: str, history: an.Tables | None = None,
                        scope: str = "all data") -> dict:
    """Everything the report may say about one sponsor, computed from ``t`` (already filtered)."""
    scoped = an.filter_dataset(t, sponsor_ids=[sponsor_id])
    cards = an.sponsor_scorecards(scoped)
    if cards.empty:
        raise KeyError(f"Sponsor {sponsor_id} has no sponsorships in the current selection")
    card = cards.iloc[0]
    results = an.sponsorship_results(scoped)
    events = an.event_kpis(scoped).set_index("event_id")
    hist = history if history is not None else t
    renewals = an.sponsor_renewals(hist["sponsorships.csv"], hist["events.csv"]).set_index("sponsor_id")

    attendees = scoped["registrations.csv"][scoped["registrations.csv"]["attended"]]
    engagement = scoped["engagement.csv"]
    ratings = scoped["feedback.csv"]["rating"]
    facts = {
        "sponsor": {"name": card["sponsor"], "tier": card["tier"], "industry": card["industry"],
                    "goals": card["goals"],
                    "renewal_status": renewals["renewal_status"].get(sponsor_id, "Unknown")},
        "scope": scope,
        "totals": {"events": int(card["events"]), "leads": int(card["leads"]),
                   "leads_target": int(card["leads_target"]), "attainment": card["attainment"],
                   "targets_met": int(card["targets_met"]), "events_below_80": int(card["events_below_alert"]),
                   "spend": card["spend"], "cost_per_lead": card["cost_per_lead"],
                   "leads_booth": int(card["leads_booth"]), "leads_demo": int(card["leads_demo"]),
                   "leads_virtual": int(card["leads_virtual"]), "alert_threshold": an.LEAD_TARGET_ALERT},
        "events": [
            {"name": r.event, "date": r.start_date, "format": r.format, "city": events.loc[r.event_id, "city"],
             "package": r.package, "leads": int(r.leads), "leads_target": int(r.leads_target),
             "attainment": r.attainment, "cost_per_lead": r.cost_per_lead,
             "registrations": int(events.loc[r.event_id, "registrations"]),
             "attendees": int(events.loc[r.event_id, "attended"])}
            for r in results.itertuples(index=False)
        ],
        "audience": {
            "registrations": int(len(scoped["registrations.csv"])), "attendees": int(len(attendees)),
            "show_up_rate": an.portfolio_kpis(scoped).show_up_rate,
            "top_seniority": _top(attendees["seniority"]), "top_roles": _top(attendees["attendee_role"]),
            "top_industries": _top(attendees["industry"]), "top_countries": _top(attendees["country"]),
        },
        "engagement": {
            "sessions": int(len(scoped["sessions.csv"])),
            "avg_session_rating": float(ratings.mean()) if len(ratings) else None,
            "nps": an.nps(ratings),
            "booth_visits_event_total": int(engagement["booth_visits"].sum()),
            "avg_watch_minutes": float(engagement["watch_minutes"].mean()) if len(engagement) else None,
            "polls_per_attendee": float(engagement["polls"].mean()) if len(engagement) else None,
            "questions_per_attendee": float(engagement["questions"].mean()) if len(engagement) else None,
            "rating_scale": "1-5",
        },
    }
    return _clean(facts)


# --- checklist ----------------------------------------------------------------------

def _section_text(markdown: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", markdown, flags=re.M | re.S | re.I)
    return match.group(1) if match else ""


def check_report(markdown: str, facts: dict, skill: Skill) -> list[CheckResult]:
    """The skill's checklist, item by item, evaluated in code."""
    labels = list(skill.checklist) + [f"Check {i}" for i in range(len(skill.checklist), 6)]
    headings = [h.strip() for h in re.findall(r"^## (.+?)\s*$", markdown, flags=re.M)]
    expected = list(skill.sections)
    structure_ok = [h.lower() for h in headings] == [s.lower() for s in expected]
    missing_sections = [s for s in expected if s.lower() not in {h.lower() for h in headings}]
    structure_detail = ("" if structure_ok else
                        f"Missing: {', '.join(missing_sections)}" if missing_sections else
                        f"Found {', '.join(headings)}; expected exactly {', '.join(expected)} in that order")

    misses = unverified_numbers(markdown, [facts])
    event_names = [e["name"] for e in facts.get("events", [])]
    uncited = [n for n in event_names if n not in markdown]
    leads_section = _section_text(markdown, "Leads vs. target")
    threshold = facts.get("totals", {}).get("alert_threshold", an.LEAD_TARGET_ALERT)
    low = [e["name"] for e in facts.get("events", []) if e.get("attainment") is not None
           and e["attainment"] < threshold and e["name"] not in leads_section]
    booth = facts.get("engagement", {}).get("booth_visits_event_total")
    mentions_booth = booth is not None and (f"{booth:,}" in markdown or str(booth) in markdown)
    booth_ok = not mentions_booth or any(p in markdown.lower() for p in _BOOTH_FRAMING)
    placeholders = sorted(set(m.group(0) for m in _PLACEHOLDERS.finditer(markdown)))

    results = [
        (structure_ok, structure_detail),
        (not misses, f"Not in the fact sheet: {', '.join(misses)}" if misses else ""),
        (not uncited, f"Not mentioned: {', '.join(uncited)}" if uncited else ""),
        (not low, f"Below target but not named under Leads vs. target: {', '.join(low)}" if low else ""),
        (booth_ok, "" if booth_ok else "Booth visits are mentioned without saying they count all booths"),
        (not placeholders, f"Found: {', '.join(placeholders)}" if placeholders else ""),
    ]
    return [CheckResult(label, ok, detail) for label, (ok, detail) in zip(labels, results)]


# --- generation -------------------------------------------------------------------

def render_prompt(facts: dict, skill: Skill) -> list[tuple[str, str]]:
    user = Template(skill.user_template).safe_substitute(
        sponsor_name=facts["sponsor"]["name"], scope=facts.get("scope", "all data"),
        instructions=skill.instructions, facts=json.dumps(facts, indent=2),
        example_facts=json.dumps(skill.example_facts or {}, indent=2), example_report=skill.example_report or "",
    )
    return [("system", skill.system_prompt), ("human", user)]


def _clean_markdown(text: str) -> str:
    text = text.strip()
    fenced = re.match(r"^```(?:markdown|md)?\s*\n(.*?)\n```$", text, flags=re.S)
    text = fenced.group(1).strip() if fenced else text
    start = text.find("# ")
    return text[start:] if start > 0 and not text.startswith("#") else text


def generate_sponsor_report(facts: dict, skill: Skill | None = None, settings: Settings | None = None,
                            model=None, recorder: UsageRecorder | None = None) -> SponsorReport:
    """Write the report with the LLM. Raises LLMNotConfiguredError or ReportError."""
    skill = skill or load_skill(SKILL_NAME)
    settings = resolve_settings(settings, model)
    model = model or build_chat_model(settings, "Sponsor report", recorder,
                                      timeout_s=max(settings.llm_timeout_s, REQUEST_TIMEOUT_S))

    def call(messages):
        try:
            return _clean_markdown(_text(retry_call(lambda: model.invoke(messages), settings).content))
        except LLMError as exc:
            raise ReportError("timeout" if exc.kind == "timeout" else "provider", str(exc)) from exc

    messages = render_prompt(facts, skill)
    markdown = call(messages)
    checks = check_report(markdown, facts, skill)
    warnings = []
    failed = [c for c in checks if not c.passed]
    if failed:  # one revision with the exact problems
        issues = "\n".join(f"- {c.item}: {c.detail}" for c in failed)
        try:
            revised = call(messages + [("ai", markdown), ("human", "Your report fails these checklist items:\n"
                                        f"{issues}\nRewrite the full report so every item passes. Return only "
                                        "the report.")])
            if revised:
                markdown, checks = revised, check_report(revised, facts, skill)
        except ReportError as exc:
            warnings.append(f"Could not revise the report ({exc}); showing the first draft.")
    return SponsorReport(markdown, checks, f"AI ({settings.llm_model})", warnings)


# --- template fallback ------------------------------------------------------------

def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _money(x) -> str:
    return "n/a" if x is None else f"${x:,.0f}"


def _segments(items: list[dict]) -> str:
    return ", ".join(f"{i['value']} ({_pct(i['share'])})" for i in items) or "n/a"


def template_report(facts: dict, skill: Skill | None = None) -> SponsorReport:
    """A plain report in the skill's structure, built directly from the facts (no LLM)."""
    skill = skill or load_skill(SKILL_NAME)
    s, tot, aud, eng, evs = facts["sponsor"], facts["totals"], facts["audience"], facts["engagement"], facts["events"]
    threshold = tot.get("alert_threshold", an.LEAD_TARGET_ALERT)
    best = max(evs, key=lambda e: e["attainment"] if e["attainment"] is not None else -1) if evs else None
    low = [e for e in evs if e["attainment"] is not None and e["attainment"] < threshold]
    rating = "n/a" if eng.get("avg_session_rating") is None else f"{eng['avg_session_rating']:.2f}"
    nps = "n/a" if eng.get("nps") is None else f"{eng['nps']:+.0f}"

    lines = [f"# {s['name']}: sponsorship report", "", f"*Covers {facts.get('scope', 'all data')}.*", ""]
    sections = {
        "Executive summary": [
            f"Across {tot['events']} event(s) you received {tot['leads']:,} leads against a target of "
            f"{tot['leads_target']:,} ({_pct(tot['attainment'])} of target), at {_money(tot['cost_per_lead'])} per "
            f"lead. You met your target at {tot['targets_met']} of {tot['events']} event(s)"
            + (f", and {best['name']} was your strongest result." if best else ".")],
        "Audience reached": [
            f"Your events drew {aud['registrations']:,} registrations and {aud['attendees']:,} attendees "
            f"({_pct(aud['show_up_rate'])} show-up rate).", "",
            f"- **Seniority:** {_segments(aud['top_seniority'])}",
            f"- **Roles:** {_segments(aud['top_roles'])}",
            f"- **Industries:** {_segments(aud['top_industries'])}",
            f"- **Countries:** {_segments(aud['top_countries'])}"],
        "Engagement": [
            f"Sessions ({eng['sessions']:,}) were rated {rating} on a 1-5 scale, with an NPS of {nps}. "
            f"Booth traffic at these events totalled {eng['booth_visits_event_total']:,} visits across all booths, "
            "not only yours."],
        "Leads vs. target": [
            *[f"- **{e['name']}** ({e['package']}): {e['leads']:,} leads against a target of {e['leads_target']:,}, "
              f"{_pct(e['attainment'])} of target, at {_money(e['cost_per_lead'])} per lead." for e in evs],
            f"- **Total:** {tot['leads']:,} leads against {tot['leads_target']:,} ({_pct(tot['attainment'])}).",
            "", (f"Below {_pct(threshold)} of target: {', '.join(e['name'] for e in low)}." if low
                 else f"No event fell below {_pct(threshold)} of target.")],
        "Highlights": [
            *([f"- {best['name']} delivered {best['leads']:,} leads ({_pct(best['attainment'])} of target)."]
              if best else []),
            f"- Booth conversations produced {tot['leads_booth']:,} leads, demos {tot['leads_demo']:,} and "
            f"virtual channels {tot['leads_virtual']:,}."],
        "Recommendations": [
            *([f"- Prioritise events like {best['name']}, where results were strongest."] if best else []),
            *([f"- Review lead capture at {', '.join(e['name'] for e in low)} before the next edition."] if low else []),
            "- Keep staffing the booth at session breaks, where most conversations start."],
    }
    for heading in skill.sections:
        lines += [f"## {heading}", "", *sections.get(heading, ["No data."]), ""]
    markdown = "\n".join(lines).strip() + "\n"
    return SponsorReport(markdown, check_report(markdown, facts, skill), "Template (no AI)")


def report_file(report: SponsorReport, facts: dict, generated_on: str) -> str:
    """The downloadable Markdown: the report plus a short provenance footer."""
    return (report.markdown.rstrip() + "\n\n---\n\n"
            f"*Generated by ConferAtlas on {generated_on} with the Sponsor Report Writer skill "
            f"({report.generated_by}). Scope: {facts.get('scope', 'all data')}. Every figure comes from the "
            "ConferAtlas analysis of the event data.*\n")
