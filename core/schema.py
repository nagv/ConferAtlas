"""Schema and validation for the multi-table event dataset (events.csv, leads.csv, ...).

Every problem found is collected and reported together, with CSV line numbers,
so a user can fix a file in one pass instead of one error at a time.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DATE_FORMAT = "YYYY-MM-DD"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}
_MAX_LINES_SHOWN = 5


@dataclass(frozen=True)
class Column:
    name: str
    kind: str = "text"  # text | int | float | date | bool
    nullable: bool = False
    allowed: tuple[str, ...] | None = None
    min_value: float | None = None
    max_value: float | None = None
    required: bool = True  # optional columns are validated only when present


EVENT_TYPES = ("tech", "business", "medical", "education")
FORMATS = ("in-person", "virtual", "hybrid")
TIERS = ("platinum", "gold", "silver", "bronze")
REG_STATUSES = ("confirmed", "cancelled")
LEAD_SOURCES = ("booth", "demo", "virtual")

SCHEMAS: dict[str, tuple[Column, ...]] = {
    "events.csv": (
        Column("event_id"),
        Column("name"),
        Column("event_type", allowed=EVENT_TYPES),
        Column("format", allowed=FORMATS),
        Column("city"),
        Column("start_date", "date"),
        Column("end_date", "date"),
        Column("budget", "float", min_value=0),
        Column("actual_cost", "float", min_value=0),
    ),
    "sponsors.csv": (
        Column("sponsor_id"),
        Column("name"),
        Column("industry"),
        Column("tier", allowed=TIERS),
        Column("goals"),
        Column("contract_value", "float", min_value=0),
    ),
    "sponsorships.csv": (
        Column("sponsor_id"),
        Column("event_id"),
        Column("package"),
        Column("leads_target", "int", min_value=0),
    ),
    "registrations.csv": (
        Column("reg_id"),
        Column("event_id"),
        Column("attendee_role"),
        Column("seniority"),
        Column("industry"),
        Column("country"),
        Column("reg_date", "date"),
        Column("status", allowed=REG_STATUSES),
        Column("attended", "bool"),
        Column("person_id", required=False),  # same person across events; enables repeat-attendee analysis
    ),
    "sessions.csv": (
        Column("session_id"),
        Column("event_id"),
        Column("track"),
        Column("speaker"),
        Column("mode", allowed=FORMATS),
        Column("capacity", "int", min_value=0),
        Column("attendance", "int", min_value=0),
        Column("rating", "float", min_value=1, max_value=5),
    ),
    "engagement.csv": (
        Column("event_id"),
        Column("session_id"),
        Column("attendee_id"),
        Column("watch_minutes", "int", min_value=0),
        Column("polls", "int", min_value=0),
        Column("questions", "int", min_value=0),
        Column("booth_visits", "int", min_value=0),
    ),
    "leads.csv": (
        Column("lead_id"),
        Column("sponsor_id"),
        Column("event_id"),
        Column("source", allowed=LEAD_SOURCES),
        Column("notes", nullable=True),
    ),
    "feedback.csv": (
        Column("event_id"),
        Column("session_id"),
        Column("rating", "int", min_value=1, max_value=5),
        Column("comment", nullable=True),
    ),
}

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "events.csv": ("event_id",),
    "sponsors.csv": ("sponsor_id",),
    "sponsorships.csv": ("sponsor_id", "event_id"),
    "registrations.csv": ("reg_id",),
    "sessions.csv": ("session_id",),
    "leads.csv": ("lead_id",),
}

# (child table, child column, parent table, parent column)
FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("sponsorships.csv", "sponsor_id", "sponsors.csv", "sponsor_id"),
    ("sponsorships.csv", "event_id", "events.csv", "event_id"),
    ("registrations.csv", "event_id", "events.csv", "event_id"),
    ("sessions.csv", "event_id", "events.csv", "event_id"),
    ("engagement.csv", "event_id", "events.csv", "event_id"),
    ("engagement.csv", "session_id", "sessions.csv", "session_id"),
    ("engagement.csv", "attendee_id", "registrations.csv", "reg_id"),
    ("leads.csv", "sponsor_id", "sponsors.csv", "sponsor_id"),
    ("leads.csv", "event_id", "events.csv", "event_id"),
    ("feedback.csv", "session_id", "sessions.csv", "session_id"),
    ("feedback.csv", "event_id", "events.csv", "event_id"),
)


class DataValidationError(ValueError):
    """Raised when data does not match the expected schema."""


class DatasetValidationError(DataValidationError):
    """Raised with every problem found; ``errors`` holds one message per problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) found:\n- " + "\n- ".join(errors))


def _lines(mask: pd.Series) -> str:
    # +2 converts a 0-based index into a CSV line number (header is line 1).
    nums = [str(i + 2) for i in mask[mask].index[:_MAX_LINES_SHOWN]]
    extra = int(mask.sum()) - len(nums)
    word = "line" if mask.sum() == 1 else "lines"
    return f"{word} " + ", ".join(nums) + (f" and {extra} more" if extra > 0 else "")


def _example(raw: pd.Series, mask: pd.Series) -> str:
    return repr(raw[mask].iloc[0])


def validate_table(table: str, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Check columns, types, date formats and allowed values for one table.

    ``df`` should be read with ``dtype=str, keep_default_na=False`` so the raw
    text is checked. Returns the typed table plus a list of error messages.
    """
    schema = SCHEMAS[table]
    missing = [c.name for c in schema if c.required and c.name not in df.columns]
    if missing:
        return df, [f"{table}: missing required column(s): {', '.join(missing)}"]

    errors: list[str] = []
    out = df.copy()
    for col in schema:
        if col.name not in df.columns:  # optional and absent
            continue
        raw = df[col.name].astype(str).str.strip()
        empty = raw == ""
        where = f"{table}: column '{col.name}'"
        if empty.any() and not col.nullable:
            errors.append(f"{where} is empty on {_lines(empty)}")

        if col.kind == "text":
            out[col.name] = raw.where(~empty, None)
            if col.allowed:
                bad = ~empty & ~raw.isin(col.allowed)
                if bad.any():
                    errors.append(
                        f"{where} has unexpected value {_example(raw, bad)} on {_lines(bad)}; "
                        f"allowed: {', '.join(col.allowed)}"
                    )
            continue

        if col.kind == "date":
            parsed = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
            bad = ~empty & (parsed.isna() | ~raw.str.match(_DATE_RE))
            if bad.any():
                errors.append(
                    f"{where} must be a date in {DATE_FORMAT} format; bad value "
                    f"{_example(raw, bad)} on {_lines(bad)}"
                )
            out[col.name] = parsed
            continue

        if col.kind == "bool":
            lowered = raw.str.lower()
            bad = ~empty & ~lowered.isin(_TRUE | _FALSE)
            if bad.any():
                errors.append(
                    f"{where} must be True or False; bad value {_example(raw, bad)} on {_lines(bad)}"
                )
            out[col.name] = lowered.isin(_TRUE)
            continue

        nums = pd.to_numeric(raw.where(~empty), errors="coerce")
        bad = ~empty & nums.isna()
        label = "a whole number" if col.kind == "int" else "a number"
        if col.kind == "int":
            bad |= nums.notna() & (nums % 1 != 0)
        if bad.any():
            errors.append(f"{where} must be {label}; bad value {_example(raw, bad)} on {_lines(bad)}")
            out[col.name] = nums
            continue
        if col.min_value is not None and (low := nums < col.min_value).any():
            errors.append(f"{where} must be at least {col.min_value:g}; see {_lines(low)}")
        if col.max_value is not None and (high := nums > col.max_value).any():
            errors.append(f"{where} must be at most {col.max_value:g}; see {_lines(high)}")
        if col.kind == "int":
            nums = nums.astype("Int64" if nums.isna().any() else "int64")
        out[col.name] = nums

    key = PRIMARY_KEYS.get(table)
    if key and not errors and (dup := out.duplicated(subset=list(key), keep=False)).any():
        errors.append(f"{table}: duplicate {' + '.join(key)} on {_lines(dup)}")
    return out, errors


def _cross_table_errors(tables: dict[str, pd.DataFrame]) -> list[str]:
    errors: list[str] = []
    for child, col, parent, parent_col in FOREIGN_KEYS:
        orphan = ~tables[child][col].isin(tables[parent][parent_col])
        if orphan.any():
            errors.append(
                f"{child}: {col} {tables[child][col][orphan].iloc[0]!r} on {_lines(orphan)} "
                f"does not exist in {parent}"
            )

    relationships = (
        ("feedback.csv", ("event_id", "session_id"), "sessions.csv", ("event_id", "session_id")),
        ("engagement.csv", ("event_id", "session_id"), "sessions.csv", ("event_id", "session_id")),
        ("engagement.csv", ("event_id", "attendee_id"), "registrations.csv", ("event_id", "reg_id")),
        ("leads.csv", ("event_id", "sponsor_id"), "sponsorships.csv", ("event_id", "sponsor_id")),
    )
    for child, columns, parent, parent_columns in relationships:
        valid = pd.MultiIndex.from_frame(tables[parent][list(parent_columns)])
        actual = pd.MultiIndex.from_frame(tables[child][list(columns)])
        bad = pd.Series(~actual.isin(valid), index=tables[child].index)
        if bad.any():
            errors.append(f"{child}: {' + '.join(columns)} on {_lines(bad)} "
                          f"does not match a row in {parent}")

    events = tables["events.csv"]
    if (bad := events["end_date"] < events["start_date"]).any():
        errors.append(f"events.csv: end_date is before start_date on {_lines(bad)}")
    sessions = tables["sessions.csv"]
    if (bad := sessions["attendance"] > sessions["capacity"]).any():
        errors.append(f"sessions.csv: attendance exceeds capacity on {_lines(bad)}")
    regs = tables["registrations.csv"]
    if (bad := regs["attended"] & (regs["status"] != "confirmed")).any():
        errors.append(f"registrations.csv: cancelled registrations marked attended on {_lines(bad)}")
    return errors


def validate_dataset(raw_tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Validate every table and the links between them; raise with all problems found."""
    errors = [f"{name}: file is missing" for name in SCHEMAS if name not in raw_tables]
    typed: dict[str, pd.DataFrame] = {}
    for name in SCHEMAS:
        if name in raw_tables:
            typed[name], table_errors = validate_table(name, raw_tables[name])
            errors.extend(table_errors)
    if not errors:
        errors = _cross_table_errors(typed)
    if errors:
        raise DatasetValidationError(errors)
    return typed


ENCODINGS = ("utf-8-sig", "cp1252")  # UTF-8 (with or without BOM), then Excel's Windows encoding


def read_csv_bytes(name: str, data: bytes) -> pd.DataFrame:
    """Parse one CSV as untyped text. Raises DatasetValidationError with a readable message
    for empty files, unknown encodings and malformed CSV."""
    if not data.strip():
        raise DatasetValidationError([f"{name}: the file is empty"])
    for encoding in ENCODINGS:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DatasetValidationError([f"{name}: not a text CSV file (could not read it as UTF-8 or Windows-1252)"])
    if "\x00" in text:
        raise DatasetValidationError([f"{name}: looks like a binary file, not a CSV"])
    try:
        return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        detail = str(exc).strip().splitlines()[-1][:160]
        raise DatasetValidationError([f"{name}: could not be read as CSV ({detail})"]) from exc


def read_tables_from_bytes(files: dict[str, bytes]) -> dict[str, pd.DataFrame]:
    """Parse every uploaded file, collecting all read errors before raising."""
    tables, errors = {}, []
    for name, data in files.items():
        try:
            tables[name] = read_csv_bytes(name, data)
        except DatasetValidationError as exc:
            errors.extend(exc.errors)
    if errors:
        raise DatasetValidationError(errors)
    return tables


def read_raw_tables(directory: str | Path) -> dict[str, pd.DataFrame]:
    """Read whichever dataset CSVs exist in ``directory`` as untyped text."""
    directory = Path(directory)
    return read_tables_from_bytes({name: (directory / name).read_bytes() for name in SCHEMAS
                                   if (directory / name).exists()})


def load_dataset(directory: str | Path) -> dict[str, pd.DataFrame]:
    """Load and validate all eight CSVs from a folder."""
    return validate_dataset(read_raw_tables(directory))
