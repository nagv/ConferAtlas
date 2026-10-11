"""Tests for presentation helpers in frontend/components (formatting and chart data prep)."""
import pytest

from frontend.components.kpi_cards import change_delta, delta_color, vs_target
from core.analysis import MetricChange


def _c(current, previous, pct=None, improved=None, metric="leads"):
    change = None if current is None or previous is None else current - previous
    return MetricChange(metric, metric, current, previous, change, pct, improved)


@pytest.mark.parametrize("change, kind, expected", [
    (_c(120, 100, pct=0.2, improved=True), "count", "+20% vs prev."),
    (_c(80, 100, pct=-0.2, improved=False), "count", "-20% vs prev."),
    (_c(7, 0, improved=True), "count", "New: none in prev. period"),  # zero baseline
    (_c(0.80, 0.77, improved=True), "rate", "+3.0 pts vs prev."),
    (_c(-5.0, -1.5, improved=False), "score", "-3.5 pts vs prev."),
    (_c(None, 10), "count", None),
    (None, "count", None),
])
def test_change_delta(change, kind, expected):
    assert change_delta(change, kind) == expected


def test_delta_color():
    assert delta_color(_c(120, 100, improved=True)) == "normal"   # up and good: green
    assert delta_color(_c(80, 100, improved=False)) == "normal"   # down and bad: red
    assert delta_color(_c(80, 100, improved=True)) == "inverse"   # down and good (e.g. cost): green
    assert delta_color(_c(100, 100)) == "off"
    assert delta_color(None) == "off"


def test_vs_target():
    assert vs_target(1.18) == "+18% vs target"
    assert vs_target(None) is None and vs_target(float("nan")) is None


@pytest.mark.parametrize("value, expected", [
    (1_322_646, "$1.32M"), (2_661_300, "$2.66M"), (845_200, "$845.2K"), (12_500, "$12.50K"),
    (950, "$950"), (0, "$0"), (None, "n/a"), (float("nan"), "n/a"), (125_000_000, "$125.0M"),
])
def test_money_compact(value, expected):
    from frontend.components.kpi_cards import money_compact
    assert money_compact(value) == expected


def test_country_map_uses_iso_codes_and_reports_unknown_names():
    import pandas as pd

    from frontend.components.charts import country_map
    df = pd.DataFrame({"country": ["Canada", "Atlantis", "United States"], "attended": [5, 1, 9],
                       "registrations": [6, 1, 10]})
    fig, unmapped = country_map(df)
    assert list(fig.data[0].locations) == ["CAN", "USA"]
    assert unmapped == ["Atlantis"]


@pytest.mark.parametrize("value, expected", [
    (9876, "9,876"), (13361, "13.4K"), (1_250_000, "1.25M"), (0, "0"), (None, "n/a"), (float("nan"), "n/a"),
])
def test_count_formatting(value, expected):
    from frontend.components.kpi_cards import count
    assert count(value) == expected


def test_short_labels_keep_the_year_and_stay_unique():
    import pandas as pd

    from frontend.components.charts import short_labels
    names = pd.Series(["Chicago DevSecOps Forum 2024", "San Francisco Teaching & Learning Forum 2025",
                       "Toronto Clinical Innovation Congress 2024"])
    assert short_labels(names).tolist() == ["Chicago DevSecOps Forum 2024", "San Francisco Teaching\u2026 2025",
                                            "Toronto Clinical Innovation\u2026 2024"]
    clash = pd.Series(["A Very Long Conference Name Number One 2024", "A Very Long Conference Name Number Two 2024"])
    assert short_labels(clash).tolist() == clash.tolist()  # would collide, so full names are kept

