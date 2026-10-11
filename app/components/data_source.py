"""Sidebar data-source picker, cached data access and shared pickers for all views."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from core.analysis import MetricChange, Tables, filter_dataset, period_comparison
from core.config import DEFAULT_DATA_DIR, get_settings
from core.filters import FilterState
from core.generator import ensure_dataset
from core.schema import SCHEMAS, DatasetValidationError, load_dataset, read_tables_from_bytes, validate_dataset

_UPLOAD_KEY = "uploaded_dataset"
_UPLOAD_DIGEST_KEY = "uploaded_dataset_digest"
APPLIED_FILTERS_KEY = "filters"  # the FilterState every page uses


@st.cache_data(show_spinner="Loading data...")
def _load_folder(directory: str) -> Tables:
    if Path(directory) == DEFAULT_DATA_DIR:
        ensure_dataset(directory)  # first run: create the built-in synthetic data
    return load_dataset(directory)


@st.cache_data(show_spinner="Checking uploaded files...")
def _load_uploaded(files: dict[str, bytes]) -> Tables:
    return validate_dataset(read_tables_from_bytes(files))


def render_brand_sidebar(about_page=None) -> None:
    """App name, what it is for, and a clear pointer to the fact that demo data is synthetic."""
    with st.sidebar:
        st.title("ConferAtlas", anchor=False)
        st.caption("Past conference performance, ready for sponsors.")
        if not using_uploaded_data():
            st.markdown(":violet-badge[:material/science: Synthetic demo data]")
            if about_page is not None:
                st.page_link(about_page, label="What does that mean?", icon=":material/info:")


_UPLOADER_WIDGET = "dataset_upload"


def sync_uploaded_files() -> None:
    """Pick up newly uploaded files at the start of a run, before the filters and pages read the data.
    (The uploader widget itself is drawn lower in the sidebar.)"""
    uploads = st.session_state.get(_UPLOADER_WIDGET)
    if not uploads:
        return
    files = {Path(u.name).name.lower(): u.getvalue() for u in uploads}
    st.session_state["uploaded_unknown_files"] = sorted(set(files) - set(SCHEMAS))
    files = {k: v for k, v in sorted(files.items()) if k in SCHEMAS}
    st.session_state[_UPLOAD_KEY] = files
    st.session_state[_UPLOAD_DIGEST_KEY] = hashlib.sha256(b"".join(files.values())).hexdigest()


def _use_built_in_data() -> None:
    for key in (_UPLOAD_KEY, _UPLOAD_DIGEST_KEY, _UPLOADER_WIDGET, "uploaded_unknown_files"):
        st.session_state.pop(key, None)


def render_data_source_sidebar() -> None:
    """Upload controls, tucked into a collapsed section: most viewers never need them."""
    with st.sidebar.expander("Data source", icon=":material/database:", expanded=using_uploaded_data()):
        st.file_uploader("Upload your dataset (all 8 CSVs)", type="csv", accept_multiple_files=True,
                         key=_UPLOADER_WIDGET, help="Expected files: " + ", ".join(SCHEMAS))
        if unknown := st.session_state.get("uploaded_unknown_files"):
            st.warning(f"Ignoring unrecognised file(s): {', '.join(unknown)}")
        if using_uploaded_data():
            st.button("Use built-in demo data", on_click=_use_built_in_data, key="use_builtin_data")
        st.caption("Using uploaded data" if using_uploaded_data() else "Using the built-in synthetic demo data")


def using_uploaded_data() -> bool:
    return _UPLOAD_KEY in st.session_state


def data_source_key() -> str:
    """Identifies the active dataset; changes whenever different data is loaded."""
    if using_uploaded_data():
        return f"upload:{st.session_state[_UPLOAD_DIGEST_KEY]}"
    return f"folder:{get_settings().data_dir}"


def _load_active() -> Tables:
    if using_uploaded_data():
        return _load_uploaded(st.session_state[_UPLOAD_KEY])
    return _load_folder(str(get_settings().data_dir))


def get_data(show_errors: bool = True) -> Tables | None:
    """Return the full (unfiltered) dataset, or report the problems found and return None."""
    try:
        return _load_active()
    except DatasetValidationError as exc:
        if show_errors:
            st.error("Could not load data. Please fix these problems:\n\n"
                     + "\n".join(f"- {e}" for e in exc.errors))
    except (FileNotFoundError, pd.errors.ParserError) as exc:
        if show_errors:
            st.error(f"Could not load data: {exc}")
    return None


def applied_filters() -> FilterState:
    return st.session_state.get(APPLIED_FILTERS_KEY, FilterState())


@st.cache_data(show_spinner="Applying filters...", max_entries=64)
def _filtered(source_key: str, filters: tuple[tuple[str, object], ...]) -> Tables:
    # source_key is part of the cache key so a new upload never reuses old results.
    return filter_dataset(_load_active(), **dict(filters))


def get_filtered_data() -> Tables | None:
    """The active dataset narrowed by the applied sidebar filters (cached per filter set)."""
    if get_data() is None:
        return None
    kwargs = applied_filters().to_kwargs(date.today())
    return _filtered(data_source_key(), tuple(sorted(kwargs.items())))


@st.cache_data(show_spinner=False, max_entries=64)
def _comparison(source_key: str, start: date, end: date,
                filters: tuple[tuple[str, object], ...]) -> list[MetricChange]:
    return period_comparison(_load_active(), start, end, **dict(filters))


def get_period_comparison(start: date, end: date) -> list[MetricChange]:
    """KPI changes for [start, end] vs the equal period before it, under the other applied filters."""
    filters = applied_filters().non_date_kwargs(date.today())
    return _comparison(data_source_key(), start, end, tuple(sorted(filters.items())))


def show_empty_state(what: str = "events") -> None:
    """Empty state with a one-click reset (kept here so existing page imports keep working)."""
    from app.components.layout import empty_state

    empty_state(what, key=f"empty_reset_{what}")


def _shared_picker(label: str, options: list[str], names: dict[str, str], state_key: str,
                   widget_key: str) -> str:
    if st.session_state.get(widget_key) not in options:
        st.session_state.pop(widget_key, None)  # filters removed the previous choice
    current = st.session_state.get(state_key)
    index = options.index(current) if current in options else 0
    choice = st.selectbox(label, options, index=index, key=widget_key, format_func=names.get)
    st.session_state[state_key] = choice
    return choice


def event_picker(t: Tables, key: str) -> str:
    """Selectbox of events (newest first); selection is shared across pages."""
    events = t["events.csv"].sort_values("start_date", ascending=False)
    return _shared_picker("Event", list(events["event_id"]),
                          dict(zip(events["event_id"], events["name"])), "selected_event", key)


def sponsor_picker(t: Tables, key: str) -> str:
    """Selectbox of sponsors that have at least one sponsorship, by name."""
    active = t["sponsors.csv"][t["sponsors.csv"]["sponsor_id"].isin(t["sponsorships.csv"]["sponsor_id"])]
    active = active.sort_values("name")
    return _shared_picker("Sponsor", list(active["sponsor_id"]),
                          dict(zip(active["sponsor_id"], active["name"])), "selected_sponsor", key)
