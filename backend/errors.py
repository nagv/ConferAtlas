"""Turn AI failures into HTTP errors whose ``detail`` keeps the kind, message and usage records.

The pages word each failure themselves (hints, prefixes), exactly as before the split;
this module only decides the status code and carries what they need.
"""
from __future__ import annotations

from fastapi import HTTPException

from core.ai.feedback import FeedbackAnalysisError
from core.ai.llm import LLMError, LLMNotConfiguredError, UsageRecorder
from core.ai.portfolio_agent import AgentError


def usage_records(recorder: UsageRecorder) -> list[dict]:
    return [c.to_json() for c in recorder.calls]


def ai_error(exc: Exception, recorder: UsageRecorder) -> HTTPException:
    if isinstance(exc, LLMNotConfiguredError):
        status, kind = 503, "not_configured"
    elif isinstance(exc, (FeedbackAnalysisError, AgentError, LLMError)):
        kind = exc.kind
        status = 504 if kind == "timeout" else 502
    elif isinstance(exc, ValueError):  # e.g. no comments to analyse
        status, kind = 422, "invalid_request"
    else:
        status, kind = 500, "unexpected"
    return HTTPException(status, {"kind": kind, "message": str(exc), "exception_type": type(exc).__name__,
                                  "usage": usage_records(recorder)})
