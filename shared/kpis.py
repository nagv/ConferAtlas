"""Period-over-period change of one KPI, as computed by the backend and shown on KPI tiles."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MetricChange:
    metric: str
    label: str
    current: float | None
    previous: float | None
    change: float | None       # current - previous (in points for rates and scores)
    pct_change: float | None   # relative change; None for a zero/missing baseline and for rates/scores
    improved: bool | None      # None when unchanged or not comparable

    def to_dict(self) -> dict:
        return asdict(self)
