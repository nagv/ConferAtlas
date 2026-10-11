import pandas as pd
import pytest

from core.generator import generate_dataset, write_dataset
from core.schema import DatasetValidationError, load_dataset, read_raw_tables, validate_dataset


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("dataset")
    write_dataset(generate_dataset(seed=1), out)
    return out


@pytest.fixture
def raw(dataset_dir):
    return read_raw_tables(dataset_dir)


def _errors(raw_tables) -> list[str]:
    with pytest.raises(DatasetValidationError) as exc:
        validate_dataset(raw_tables)
    return exc.value.errors


def test_valid_dataset_loads_with_types(dataset_dir):
    tables = load_dataset(dataset_dir)
    assert pd.api.types.is_datetime64_any_dtype(tables["events.csv"]["start_date"])
    assert tables["registrations.csv"]["attended"].dtype == bool
    assert pd.api.types.is_integer_dtype(tables["sessions.csv"]["capacity"])


def test_missing_file_reported(raw):
    del raw["leads.csv"]
    assert "leads.csv: file is missing" in _errors(raw)


def test_missing_column_reported(raw):
    raw["events.csv"] = raw["events.csv"].drop(columns=["budget", "actual_cost"])
    assert _errors(raw) == ["events.csv: missing required column(s): budget, actual_cost"]


def test_bad_date_format_reports_line_and_value(raw):
    raw["events.csv"].loc[2, "start_date"] = "03/15/2024"
    (msg,) = _errors(raw)
    assert "start_date" in msg and "YYYY-MM-DD" in msg and "'03/15/2024'" in msg and "on line 4" in msg


def test_impossible_date_rejected(raw):
    raw["registrations.csv"].loc[0, "reg_date"] = "2025-02-30"
    assert any("reg_date" in e for e in _errors(raw))


def test_wrong_types_reported(raw):
    raw["sessions.csv"].loc[0, "capacity"] = "lots"
    raw["sessions.csv"].loc[1, "attendance"] = "12.5"
    raw["registrations.csv"].loc[0, "attended"] = "maybe"
    errors = " | ".join(_errors(raw))
    assert "'capacity' must be a whole number" in errors
    assert "'attendance' must be a whole number" in errors
    assert "'attended' must be True or False" in errors


def test_disallowed_value_and_range(raw):
    raw["events.csv"].loc[0, "format"] = "onsite"
    raw["feedback.csv"].loc[0, "rating"] = "7"
    errors = " | ".join(_errors(raw))
    assert "unexpected value 'onsite'" in errors and "allowed: in-person, virtual, hybrid" in errors
    assert "'rating' must be at most 5" in errors


def test_empty_required_value_but_blank_comment_ok(raw):
    raw["feedback.csv"].loc[0, "comment"] = ""
    raw["leads.csv"].loc[0, "sponsor_id"] = ""
    errors = _errors(raw)
    assert any("leads.csv: column 'sponsor_id' is empty" in e for e in errors)
    assert not any("comment" in e for e in errors)


def test_all_problems_reported_together(raw):
    raw["events.csv"].loc[0, "budget"] = "n/a"
    raw["sponsors.csv"].loc[0, "tier"] = "diamond"
    assert len(_errors(raw)) == 2


def test_cross_table_checks(raw):
    raw["leads.csv"].loc[0, "event_id"] = "EV999"
    raw["registrations.csv"].loc[0, "reg_id"] = raw["registrations.csv"].loc[1, "reg_id"]
    errors = " | ".join(_errors(raw))
    assert "duplicate reg_id" in errors
    # Foreign keys are only checked once each table is valid on its own.
    raw["registrations.csv"].loc[0, "reg_id"] = "REG999999"
    errors = " | ".join(_errors(raw))
    assert "'EV999'" in errors and "does not exist in events.csv" in errors


def test_person_id_is_optional_but_checked_when_present(raw):
    assert "person_id" in raw["registrations.csv"].columns  # generated data has it
    without = dict(raw, **{"registrations.csv": raw["registrations.csv"].drop(columns="person_id")})
    assert "person_id" not in validate_dataset(without)["registrations.csv"].columns
    raw["registrations.csv"].loc[0, "person_id"] = ""
    assert any("'person_id' is empty" in e for e in _errors(raw))


@pytest.mark.parametrize("table, column, parent, parent_column", [
    ("feedback.csv", "session_id", "sessions.csv", "session_id"),
    ("engagement.csv", "session_id", "sessions.csv", "session_id"),
    ("engagement.csv", "attendee_id", "registrations.csv", "reg_id"),
])
def test_rejects_cross_event_links(raw, table, column, parent, parent_column):
    event = raw[table].loc[0, "event_id"]
    other = raw[parent].loc[raw[parent]["event_id"] != event, parent_column].iloc[0]
    raw[table].loc[0, column] = other
    errors = " | ".join(_errors(raw))
    assert table in errors and "line 2" in errors and "event_id" in errors


def test_lead_must_match_a_sponsorship(raw):
    pairs = set(zip(raw["sponsorships.csv"]["sponsor_id"], raw["sponsorships.csv"]["event_id"]))
    sponsor, event = next((s,e) for s in raw["sponsors.csv"]["sponsor_id"]
                          for e in raw["events.csv"]["event_id"] if (s,e) not in pairs)
    raw["leads.csv"].loc[0, ["sponsor_id", "event_id"]] = [sponsor, event]
    assert any("leads.csv" in error and "sponsorships.csv" in error for error in _errors(raw))
