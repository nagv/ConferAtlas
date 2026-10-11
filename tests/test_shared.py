"""shared/ must behave exactly like the core code it replaced, and stay free of heavy imports."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from shared.filters import ALL_TIME, CUSTOM, FilterState, active_parts, preset_range
from shared.usage import CallRecord


def _pandas_months_back(today: date, months: int) -> date:
    return (pd.Timestamp(today) - pd.DateOffset(months=months)).date() + timedelta(days=1)


@pytest.mark.parametrize("months, preset", [(6, "Last 6 months"), (12, "Last 12 months")])
def test_month_presets_match_pandas_for_every_day(months, preset):
    day = date(2000, 1, 1)
    while day <= date(2040, 12, 31):
        assert preset_range(preset, day) == (_pandas_months_back(day, months), day), day
        day += timedelta(days=1)


def test_core_reexports_are_the_same_objects():
    import core.access
    import core.ai.llm
    import core.ai.sponsor_report
    import core.analysis
    import core.filters
    import shared.access
    import shared.kpis
    import shared.report
    import shared.usage

    assert core.filters.FilterState is FilterState and core.filters.preset_range is preset_range
    assert core.filters.CUSTOM == CUSTOM and core.filters.ALL_TIME == ALL_TIME
    assert core.analysis.MetricChange is shared.kpis.MetricChange
    assert core.ai.llm.UsageRecorder is shared.usage.UsageRecorder and core.ai.llm.CallRecord is CallRecord
    assert core.ai.sponsor_report.report_file is shared.report.report_file
    assert core.access.access_required is shared.access.access_required


def test_constants_match_core():
    from core.analysis import SENIORITY_ORDER
    from core.schema import SCHEMAS
    from shared.constants import DATASET_FILES, SENIORITY_ORDER as SHARED_SENIORITY

    assert DATASET_FILES == tuple(SCHEMAS)
    assert SHARED_SENIORITY == SENIORITY_ORDER


def test_filter_state_json_round_trip():
    state = FilterState(CUSTOM, date(2025, 1, 1), date(2025, 12, 31), ("tech",), ("virtual",), ("Austin",),
                        ("EV001", "EV002"), "SP001")
    assert FilterState.from_json(state.to_json()) == state
    assert FilterState.from_json(FilterState().to_json()) == FilterState()


def test_call_record_json_round_trip():
    record = CallRecord(datetime(2026, 10, 11, 12, 0, 5), "Portfolio chat", "m", 10, 2, None, 1.5, "error", "timeout")
    assert CallRecord.from_json(record.to_json()) == record


def test_active_parts():
    assert active_parts([("Dates", "All time"), ("Format", "Virtual"), ("City", "All")]) == ["Format: Virtual"]
