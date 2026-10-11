"""Rebuild DataFrames and Series from the API's ``Table`` payloads (see ``backend/serialize.py``)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _convert(values: list, dtype: str) -> pd.Series:
    series = pd.Series(values, dtype=object)
    if dtype.startswith("datetime64"):
        return pd.to_datetime(series, format="ISO8601").astype(dtype)
    if dtype.startswith(("float", "int", "uint", "bool")) or dtype in ("Int64", "Float64", "boolean", "string"):
        return series.astype(dtype)
    if dtype == "category":
        return series.astype("category")
    return series.map(_missing)


_MISSING = {"nan": np.nan, "nat": pd.NaT}


def _missing(value):
    return _MISSING[value["__na__"]] if isinstance(value, dict) and "__na__" in value else value


def to_frame(table) -> pd.DataFrame | pd.Series | None:
    """A ``shared.models.Table`` (model or dict) as the DataFrame or Series it was made from."""
    if table is None:
        return None
    t = table if isinstance(table, dict) else table.model_dump()
    rows = t["data"]
    columns = {i: _convert([r[i] for r in rows], dtype) for i, dtype in enumerate(t["dtypes"])}
    frame = pd.concat(columns, axis=1) if columns else pd.DataFrame(index=range(len(rows)))
    frame.columns = pd.Index(t["columns"], dtype=object) if t["columns"] else frame.columns
    if t.get("index") is not None:
        frame.index = pd.Index(_convert(t["index"], t["index_dtype"] or "object"), name=t["index_name"])
    else:
        frame.index = pd.RangeIndex(len(rows))
    if t["kind"] == "series":
        series = frame.iloc[:, 0]
        series.name = t["name"]
        return series
    return frame
