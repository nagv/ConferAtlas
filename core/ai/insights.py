"""Generate sponsor-facing narratives from computed metrics.

The LLM never sees raw CSV rows or does arithmetic: all numbers are computed
in ``core.analysis`` first and passed in as a compact, pre-formatted context.
That keeps figures accurate and makes the prompt testable without a network.
"""
from __future__ import annotations

import math

import pandas as pd

from core.analysis import LEAD_SOURCE_COLUMNS, FeedbackSummary
from core.ai.llm import LLMError, LLMNotConfiguredError, UsageRecorder, build_chat_model, resolve_settings, retry_call
from core.ai.portfolio_agent import unverified_numbers
from core.config import Settings

SYSTEM_PROMPT = (
    "You are a sponsorship strategist for a conference organizer. You write "
    "concise, credible summaries that help sponsors judge the value of an event. "
    "Use only the figures provided in the metrics context; never invent numbers. "
    "If a result is weak or negative, say so plainly and frame what the organizer "
    "is doing about it rather than hiding it."
)

USER_PROMPT = (
    "Audience: {audience}\n"
    "Tone: {tone}\n\n"
    "Metrics context for {subject}:\n{context}\n\n"
    "{task}"
)

EVENT_TASK = (
    "Write a sponsor pitch for this event with: a one-sentence headline, 3-5 bullet "
    "points on the strongest evidence of sponsor value, one bullet on a risk or "
    "watch-out, and a closing line inviting sponsors to join the next edition."
)

SPONSOR_TASK = (
    "Write a renewal brief addressed to this sponsor with: a one-sentence headline, "
    "3-5 bullet points on the results they got, one honest bullet on where results "
    "fell short of target, and a closing recommendation for their next package."
)




def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _pct(value: float | None) -> str:
    return "n/a" if _missing(value) else f"{value:.0%}"


def _money(value: float | None) -> str:
    return "n/a" if _missing(value) else f"${value:,.0f}"


def _source_mix(rows: pd.DataFrame) -> str:
    totals = {c.removeprefix("leads_"): int(rows[c].sum()) for c in LEAD_SOURCE_COLUMNS}
    return ", ".join(f"{k} {v:,}" for k, v in totals.items() if v) or "none"


def _score(value: float | None, fmt: str) -> str:
    return "n/a" if _missing(value) else format(value, fmt)


def build_event_context(event: pd.Series, sponsorships: pd.DataFrame, feedback: FeedbackSummary) -> str:
    """Render one event's computed KPIs as plain text for the prompt.

    ``event`` is a row of ``event_kpis``; ``sponsorships`` the matching rows of
    ``sponsorship_results``; ``feedback`` the event's ``feedback_summary``.
    """
    lines = [
        f"- Event: {event['name']} ({event['event_type']}, {event['format']}, {event['city']}, "
        f"{event['start_date']:%d %b %Y})",
        f"- Registrations: {event['registrations']:,}; attended: {event['attended']:,} "
        f"(show-up rate {_pct(event['show_up_rate'])} of confirmed)",
        f"- Sessions: {event['sessions']}; average session rating {_score(event['avg_rating'], '.2f')}/5 "
        f"from {event['feedback_count']:,} ratings; NPS {_score(event['nps'], '.0f')}",
        f"- Feedback: {_pct(feedback.positive)} positive, {_pct(feedback.neutral)} neutral, "
        f"{_pct(feedback.negative)} negative",
        f"- Sponsors: {event['sponsors']}; leads delivered {event['leads']:,} vs target "
        f"{event['leads_target']:,} ({_pct(event['lead_attainment'])} of target)",
        f"- Sponsors that met their lead target: {event['sponsors_met_target']} of {event['sponsors']}",
        f"- Leads per sponsor: {_score(event['leads_per_sponsor'], ',.0f')}; estimated cost per lead "
        f"{_money(event['cost_per_lead'])}",
        f"- Lead sources: {_source_mix(sponsorships)}",
        "",
        "Per-sponsor results (sponsor, package: leads / target):",
    ]
    for row in sponsorships.itertuples(index=False):
        flag = "met" if row.met_target else "missed"
        lines.append(f"  {row.sponsor}, {row.package}: {row.leads:,} / {row.leads_target:,} ({flag})")
    return "\n".join(lines)


def build_sponsor_context(card: pd.Series, history: pd.DataFrame) -> str:
    """Render one sponsor's scorecard and per-event results as plain text.

    ``card`` is a row of ``sponsor_scorecards``; ``history`` that sponsor's
    rows of ``sponsorship_results``.
    """
    lines = [
        f"- Sponsor: {card['sponsor']} ({card['industry']}, {card['tier']} tier)",
        f"- Stated goals: {card['goals']}",
        f"- Events sponsored: {card['events']}; targets met at {card['targets_met']} of them",
        f"- Total leads: {card['leads']:,} vs target {card['leads_target']:,} "
        f"({_pct(card['attainment'])} of target)",
        f"- Estimated cost per lead: {_money(card['cost_per_lead'])} "
        f"(contract value {_money(card['contract_value'])})",
        f"- Best event: {card['best_event']} ({card['best_event_leads']:,} leads)",
        f"- Lead sources: {_source_mix(history)}",
        "",
        "Per-event results (event, package: leads / target):",
    ]
    for row in history.itertuples(index=False):
        flag = "met" if row.met_target else "missed"
        lines.append(f"  {row.event}, {row.package}: {row.leads:,} / {row.leads_target:,} ({flag})")
    return "\n".join(lines)


def fallback_event_summary(event: pd.Series) -> str:
    """Deterministic event summary used when no LLM is configured."""
    return "\n".join([
        f"**{event['name']}** ({event['format']} {event['event_type']} event, {event['city']}).",
        f"- {event['attended']:,} of {event['registrations']:,} registrants attended "
        f"(show-up rate {_pct(event['show_up_rate'])}).",
        f"- {event['sponsors']} sponsors received {event['leads']:,} leads, "
        f"{_pct(event['lead_attainment'])} of their combined target; "
        f"{event['sponsors_met_target']} of {event['sponsors']} met their own target.",
        f"- About {_score(event['leads_per_sponsor'], ',.0f')} leads per sponsor at an estimated "
        f"{_money(event['cost_per_lead'])} per lead.",
        f"- Sessions averaged {_score(event['avg_rating'], '.2f')}/5 across {event['feedback_count']:,} "
        f"ratings (NPS {_score(event['nps'], '.0f')}).",
    ])


def fallback_sponsor_summary(card: pd.Series) -> str:
    """Deterministic sponsor summary used when no LLM is configured."""
    return "\n".join([
        f"**{card['sponsor']}** ({card['tier']} tier, {card['industry']}) sponsored "
        f"{card['events']} event(s).",
        f"- {card['leads']:,} leads against a target of {card['leads_target']:,} "
        f"({_pct(card['attainment'])}); target met at {card['targets_met']} of {card['events']} events.",
        f"- Estimated cost per lead: {_money(card['cost_per_lead'])}.",
        f"- Best result: {card['best_event']} with {card['best_event_leads']:,} leads.",
    ])


def generate_pitch(
    subject: str,
    context: str,
    task: str = EVENT_TASK,
    audience: str = "Prospective sponsors",
    tone: str = "Confident and data-driven",
    settings: Settings | None = None,
    recorder: UsageRecorder | None = None,
    model=None,
) -> str:
    """Run the LangChain prompt -> model -> parser chain and return markdown.

    Raises LLMNotConfiguredError without a key and LLMError (friendly message) when the call fails."""
    settings = resolve_settings(settings, model)
    model = model or build_chat_model(settings, "AI insights", recorder)  # raises before heavy imports if no key

    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [("system", SYSTEM_PROMPT), ("human", USER_PROMPT)]
    )
    chain = prompt | model | StrOutputParser()
    inputs = {"audience": audience, "tone": tone, "subject": subject, "context": context, "task": task}
    text = retry_call(lambda: chain.invoke(inputs), settings)
    misses = unverified_numbers(text, [context])
    if misses:
        inputs["task"] += ("\nRewrite the full answer using only the supplied metrics. These figures "
                           f"could not be verified: {', '.join(misses)}. Preserve signs and units.")
        text = retry_call(lambda: chain.invoke(inputs), settings)
        if unverified_numbers(text, [context]):
            raise LLMError("invalid_output", "The AI draft's figures could not be verified. "
                           "Please generate it again.", 2)
    return text
