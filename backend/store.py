"""In-memory datasets: the built-in demo data plus uploaded datasets.

- The built-in dataset (id ``builtin``) is read from ``CONFERATLAS_DATA_DIR`` on first use
  (generating the synthetic demo data on a first run) and kept for the life of the process.
- Uploads are validated once, kept by id, and expire after an hour unused; at most
  ``MAX_UPLOADS`` are held (least recently used first out). The same bytes uploaded twice
  give the same id, so several browser tabs share one copy.
- Load problems are not cached, so fixing the files and retrying works without a restart.

State lives in this process, so run the API with a single worker. Cached tables are shared
between requests: services must treat them as read-only.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from core.analysis import MetricChange, Tables, filter_dataset, period_comparison
from core.config import DEFAULT_DATA_DIR, get_settings
from core.generator import ensure_dataset
from core.schema import SCHEMAS, DatasetValidationError, load_dataset, read_tables_from_bytes, validate_dataset

BUILTIN_ID = "builtin"
MAX_UPLOADS = 8
UPLOAD_TTL_S = 3600
FILTER_CACHE = 16


class DatasetLoadError(Exception):
    """Files that could not be read or validated; ``message`` and ``issues`` are shown to the user."""

    def __init__(self, message: str, issues: list[str] | None = None):
        super().__init__(message)
        self.message, self.issues = message, list(issues or [])


def _load_error(exc: Exception) -> DatasetLoadError:
    if isinstance(exc, DatasetValidationError):
        return DatasetLoadError("Could not load data. Please fix these problems:", exc.errors)
    return DatasetLoadError(f"Could not load data: {exc}")


LOAD_ERRORS = (DatasetValidationError, FileNotFoundError, pd.errors.ParserError)


def _key(kwargs: dict) -> tuple:
    return tuple(sorted(kwargs.items()))


@dataclass
class StoredDataset:
    id: str
    kind: str                      # "builtin" or "upload"
    source: str                    # "folder:<dir>" or "upload:<digest>"
    tables: Tables
    files: list[str]
    ignored_files: list[str] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)
    _filtered: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _comparisons: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def _cached(self, cache: OrderedDict, key, compute):
        with self._lock:
            if key in cache:
                cache.move_to_end(key)
                return cache[key]
        value = compute()
        with self._lock:
            cache[key] = value
            while len(cache) > FILTER_CACHE:
                cache.popitem(last=False)
        return value

    def filtered(self, kwargs: dict) -> Tables:
        """``filter_dataset`` with these arguments (cached per filter set)."""
        return self._cached(self._filtered, _key(kwargs), lambda: filter_dataset(self.tables, **kwargs))

    def comparison(self, start: date, end: date, kwargs: dict) -> list[MetricChange]:
        """KPI changes for [start, end] vs the equal period before it, under the other filters."""
        return self._cached(self._comparisons, (start, end, _key(kwargs)),
                            lambda: period_comparison(self.tables, start, end, **kwargs))


def _digest(files: dict[str, bytes]) -> str:
    return hashlib.sha256(b"".join(data for _, data in sorted(files.items()))).hexdigest()


class DatasetStore:
    def __init__(self, max_uploads: int = MAX_UPLOADS, ttl_s: float = UPLOAD_TTL_S, clock=time.monotonic):
        self.max_uploads, self.ttl_s, self.clock = max_uploads, ttl_s, clock
        self._builtin: dict[str, StoredDataset] = {}
        self._uploads: OrderedDict[str, StoredDataset] = OrderedDict()
        self._lock = threading.RLock()

    # --- built-in demo data -------------------------------------------------------------------
    def builtin(self) -> StoredDataset:
        data_dir = get_settings().data_dir
        key = str(data_dir)
        with self._lock:
            if key in self._builtin:
                return self._builtin[key]
            try:
                if Path(data_dir) == DEFAULT_DATA_DIR:
                    ensure_dataset(data_dir)  # first run: create the built-in synthetic data
                tables = load_dataset(data_dir)
            except LOAD_ERRORS as exc:
                raise _load_error(exc) from None
            stored = StoredDataset(BUILTIN_ID, "builtin", f"folder:{data_dir}", tables, list(tables))
            self._builtin[key] = stored
            return stored

    # --- uploads ------------------------------------------------------------------------------
    def add_upload(self, files: dict[str, bytes]) -> StoredDataset:
        """Validate and keep an uploaded dataset. Unknown file names are ignored and reported."""
        known = {name: data for name, data in sorted(files.items()) if name in SCHEMAS}
        ignored = sorted(set(files) - set(known))
        digest = _digest(known)
        with self._lock:
            self._expire()
            for stored in self._uploads.values():
                if stored.source == f"upload:{digest}":
                    self._touch(stored)
                    return stored
        try:
            tables = validate_dataset(read_tables_from_bytes(known))
        except LOAD_ERRORS as exc:
            raise _load_error(exc) from None
        return self.put_tables(tables, source=f"upload:{digest}", files=list(known), ignored=ignored)

    def put_tables(self, tables: Tables, source: str | None = None, files: list[str] | None = None,
                   ignored: list[str] | None = None) -> StoredDataset:
        """Keep already-validated tables as an upload (also used by tests)."""
        dataset_id = uuid.uuid4().hex
        stored = StoredDataset(dataset_id, "upload", source or f"upload:{dataset_id}", tables,
                               files if files is not None else list(tables), ignored or [], self.clock())
        with self._lock:
            self._uploads[dataset_id] = stored
            while len(self._uploads) > self.max_uploads:
                self._uploads.popitem(last=False)
        return stored

    def get(self, dataset_id: str) -> StoredDataset:
        """The dataset, or KeyError when it is unknown or expired. May raise DatasetLoadError for builtin."""
        if dataset_id == BUILTIN_ID:
            return self.builtin()
        with self._lock:
            self._expire()
            stored = self._uploads[dataset_id]
            self._touch(stored)
            return stored

    def delete(self, dataset_id: str) -> None:
        with self._lock:
            self._uploads.pop(dataset_id, None)

    def reset(self) -> None:
        with self._lock:
            self._builtin.clear()
            self._uploads.clear()

    def _touch(self, stored: StoredDataset) -> None:
        stored.last_used = self.clock()
        self._uploads.move_to_end(stored.id)

    def _expire(self) -> None:
        now = self.clock()
        for dataset_id in [i for i, s in self._uploads.items() if now - s.last_used > self.ttl_s]:
            del self._uploads[dataset_id]
