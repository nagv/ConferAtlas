"""Sponsor summary report: the fact sheet, the AI report, and the template fallback when AI fails."""
from __future__ import annotations

from dataclasses import asdict

from backend.services.selection import scoped
from backend.services.sponsors import chosen, report_scope
from backend.store import StoredDataset
from core.ai.llm import LLMNotConfiguredError, UsageRecorder
from core.ai.sponsor_report import ReportError, build_sponsor_facts, generate_sponsor_report, template_report
from core.analysis import filter_sponsorships
from core.config import get_settings
from shared.models import SponsorSummaryRequest


def generate(ds: StoredDataset, req: SponsorSummaryRequest, recorder: UsageRecorder) -> tuple[dict, dict]:
    """(report, facts) as JSON-ready dicts. AI problems fall back to the template draft with a warning."""
    state, t = scoped(ds, req)
    picked = chosen(t, req.sponsor_filters)
    f = filter_sponsorships(t, **picked)
    history = ds.tables
    scope = report_scope(state, picked, history, req.today)
    facts = build_sponsor_facts(f, req.sponsor_id, history=history, scope=scope)
    settings = get_settings()
    report, problem = None, None
    if settings.llm_configured:
        try:
            report = generate_sponsor_report(facts, settings=settings, recorder=recorder)
        except (ReportError, LLMNotConfiguredError) as exc:
            problem = str(exc)
        except Exception as exc:  # keep the page usable whatever happens
            problem = f"{type(exc).__name__}: {exc}"
    if report is None:
        report = template_report(facts)
        if problem:
            report.warnings.append(f"The AI report failed ({problem}), so this is the template draft instead.")
    return asdict(report), facts
