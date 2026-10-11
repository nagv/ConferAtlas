"""Ask the portfolio: one question to the tool-calling agent, with the prior chat sent by the page."""
from __future__ import annotations

from backend.serialize import json_safe, table
from backend.services.selection import scope_text, scoped
from backend.store import StoredDataset
from core.ai.llm import UsageRecorder
from core.ai.portfolio_agent import PortfolioToolkit, ask_portfolio
from shared.models import ChatRequest


def ask(ds: StoredDataset, req: ChatRequest, recorder: UsageRecorder) -> dict:
    """The agent's answer as a JSON-ready dict (raises the core errors unchanged).

    The toolkit is rebuilt for every question, so no chat state is kept on the server."""
    state, t = scoped(ds, req)
    toolkit = PortfolioToolkit(t, history=ds.tables, scope=scope_text(state, ds.tables, req.today))
    prior = [m.model_dump() for m in req.history]
    answer = ask_portfolio(req.question, toolkit, history=prior, recorder=recorder)
    return {
        "text": answer.text,
        "calculations": [{"id": r.id, "tool": r.tool, "args": json_safe(r.args), "result": json_safe(r.result),
                          "table": table(r.table)} for r in answer.calculations],
        "charts": [vars(c) for c in answer.charts],
        "unverified_numbers": list(answer.unverified_numbers), "warnings": list(answer.warnings),
        "tool_calls": answer.tool_calls,
    }
