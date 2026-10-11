"""KPI tile row and number formatting shared by the views."""
from __future__ import annotations

import math
from dataclasses import dataclass

import streamlit as st

from core.analysis import MetricChange


@dataclass(frozen=True)
class Tile:
    label: str
    value: str
    delta: str | None = None
    help: str | None = None
    delta_color: str = "normal"  # "inverse" when a rise is bad, "off" for neutral context
    border: bool = True


def kpi_row(tiles: list[Tile], per_row: int | None = None) -> None:
    """Draw tiles in rows of ``per_row`` (default: all on one row)."""
    per_row = per_row or len(tiles)
    for i in range(0, len(tiles), per_row):
        for col, tile in zip(st.columns(per_row), tiles[i:i + per_row]):
            col.metric(tile.label, tile.value, delta=tile.delta, help=tile.help,
                       delta_color=tile.delta_color, border=tile.border,
                       # Neutral context ("22% no-shows") gets no arrow, so it doesn't read as a trend.
                       delta_arrow="off" if tile.delta_color == "off" else "auto")


def pct(value: float | None, signed: bool = False) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:+.0%}" if signed else f"{value:.0%}"


def money(value: float | None) -> str:
    return "n/a" if value is None or math.isnan(value) else f"${value:,.0f}"


def count(value: int | float | None) -> str:
    """Whole numbers for KPI cards: 9,876 as is, 13,361 as 13.4K, 1,250,000 as 1.25M."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    value = float(value)
    if abs(value) >= 1e6:
        return f"{value / 1e6:.2f}M"
    if abs(value) >= 1e4:
        return f"{value / 1e3:.1f}K"
    return f"{value:,.0f}"


def money_compact(value: float | None) -> str:
    """$1.32M / $845K / $950, for KPI cards where space is tight."""
    if value is None or math.isnan(value):
        return "n/a"
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"${value / size:,.{2 if abs(value) < size * 100 else 1}f}{suffix}"
    return f"${value:,.0f}"


def score(value: float | None, fmt: str = ".2f") -> str:
    return "n/a" if value is None or math.isnan(value) else format(value, fmt)


def vs_target(attainment: float | None) -> str | None:
    """'+18% vs target' from an attainment ratio, or None when there is no target."""
    if attainment is None or math.isnan(attainment):
        return None
    return f"{attainment - 1:+.0%} vs target"


def change_delta(change: MetricChange | None, kind: str = "count") -> str | None:
    """Delta text for a KPI card: '+12% vs prev.' for counts and money, '+3.1 pts' for
    rates (0-1, shown as percentage points) and scores. None when there is no comparison."""
    if change is None or change.change is None:
        return None
    if kind == "count":
        if change.pct_change is not None:
            return f"{change.pct_change:+.0%} vs prev."
        return "New: none in prev. period" if change.previous == 0 else None
    points = change.change * 100 if kind == "rate" else change.change
    return f"{points:+.1f} pts vs prev."


def delta_color(change: MetricChange | None) -> str:
    """Green for an improvement, red for a decline, grey when unchanged or not comparable."""
    if change is None or change.improved is None:
        return "off"
    higher_is_better = (change.change > 0) == change.improved
    return "normal" if higher_is_better else "inverse"
