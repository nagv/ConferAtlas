"""About page: headline figures for the whole dataset."""
from __future__ import annotations

from backend.serialize import dataclass_dict
from backend.store import StoredDataset
from core.analysis import portfolio_kpis
from shared.models import AboutResponse


def build(ds: StoredDataset) -> AboutResponse:
    return AboutResponse(portfolio=dataclass_dict(portfolio_kpis(ds.tables)))
