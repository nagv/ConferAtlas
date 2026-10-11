"""DataFrames, Series and dataclasses -> JSON payloads that rebuild exactly in the frontend.

Floats are sent at full precision (Python's JSON float form round-trips exactly), dtypes
travel with the data, and missing values become null, so a table rebuilt by
``frontend/components/tables.py`` gives the same charts, cells and CSV bytes.
"""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def json_safe(value: Any) -> Any:
    """Plain JSON types for any nested value: numpy scalars, timestamps and NaN handled."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return None if math.isnan(value) or math.isinf(value) else value
    if value is pd.NaT:
        return None
    return value


def _cells(values: pd.Series | pd.Index) -> list:
    dtype = values.dtype
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return [None if pd.isna(v) else pd.Timestamp(v).isoformat() for v in values]
    if pd.api.types.is_bool_dtype(dtype) and not isinstance(dtype, pd.CategoricalDtype):
        return [bool(v) for v in values]
    if pd.api.types.is_integer_dtype(dtype) and not isinstance(dtype, pd.CategoricalDtype):
        return [None if pd.isna(v) else int(v) for v in values]
    if pd.api.types.is_float_dtype(dtype):
        return [None if (v != v or math.isinf(v)) else float(v) for v in values]
    return [_object_cell(v) for v in values]


def _object_cell(value):
    # Text columns hold None for blank optional values but NaN after merges; keep them distinct.
    if value is None:
        return None
    if value is pd.NaT:
        return {"__na__": "nat"}
    if isinstance(value, (float, np.floating)) and math.isnan(value):
        return {"__na__": "nan"}
    return json_safe(value)


def _default_index(index: pd.Index) -> bool:
    return isinstance(index, pd.RangeIndex) and index.start == 0 and index.step == 1 and index.name is None


def table(obj: pd.DataFrame | pd.Series | None) -> dict | None:
    """``shared.models.Table`` payload for a DataFrame or Series (None stays None)."""
    if obj is None:
        return None
    if isinstance(obj, pd.Series):
        frame = obj.to_frame(name="__value__")
        out = table(frame)
        out.update(kind="series", name=None if obj.name is None else str(obj.name), index=_cells(obj.index),
                   index_name=obj.index.name, index_dtype=str(obj.index.dtype))
        return out
    columns = list(obj.columns)
    rows = list(zip(*[_cells(obj.iloc[:, i]) for i in range(len(columns))])) if len(obj) else []
    out = {"kind": "frame", "columns": [json_safe(c) for c in columns],
           "dtypes": [str(obj.dtypes.iloc[i]) for i in range(len(columns))],
           "data": [list(r) for r in rows], "index": None, "index_name": None, "index_dtype": None, "name": None}
    if not _default_index(obj.index):
        out.update(index=_cells(obj.index), index_name=obj.index.name, index_dtype=str(obj.index.dtype))
    return out


def first_row(frame: pd.DataFrame) -> dict:
    """The first row as a one-row table, keeping column dtypes (the page reads ``.iloc[0]``)."""
    return table(frame.iloc[[0]].reset_index(drop=True))


def dataclass_dict(obj) -> dict:
    """A frozen core dataclass as JSON-safe fields (timestamps become dates)."""
    data = asdict(obj)
    return {k: (v.date() if isinstance(v, pd.Timestamp) else json_safe(v)) for k, v in data.items()}
