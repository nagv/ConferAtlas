"""LLM usage records: one ``CallRecord`` per model request, totals kept per browser session.

The backend records each AI request's calls with a fresh ``UsageRecorder`` and returns them
with the response; the frontend adds them to the session's recorder for the sidebar panel.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

MAX_STORED_CALLS = 200


@dataclass
class CallRecord:
    started: datetime
    purpose: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_s: float
    status: str               # "ok" or "error"
    error: str = ""

    def to_json(self) -> dict:
        data = asdict(self)
        data["started"] = self.started.isoformat()
        return data

    @classmethod
    def from_json(cls, data: dict) -> CallRecord:
        return cls(**dict(data, started=datetime.fromisoformat(data["started"])))


@dataclass
class UsageRecorder:
    """Every LLM call in one session, plus running totals (kept even when old rows roll off)."""

    calls: list[CallRecord] = field(default_factory=list)
    total_calls: int = 0
    total_errors: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    cost_known: bool = True    # False once any call's cost could not be estimated
    total_latency_s: float = 0.0

    def add(self, record: CallRecord) -> None:
        self.calls.append(record)
        del self.calls[:-MAX_STORED_CALLS]
        self.total_calls += 1
        self.total_errors += record.status != "ok"
        self.total_input_tokens += record.input_tokens or 0
        self.total_output_tokens += record.output_tokens or 0
        self.total_latency_s += record.latency_s
        if record.status == "ok":
            if record.cost_usd is None:
                self.cost_known = False
            else:
                self.total_cost_usd += record.cost_usd

    @property
    def avg_latency_s(self) -> float | None:
        return self.total_latency_s / self.total_calls if self.total_calls else None
