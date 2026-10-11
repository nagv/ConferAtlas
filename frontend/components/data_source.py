"""Sidebar data-source picker, the active dataset on the backend, and shared pickers for all views."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import streamlit as st

from frontend import client
from shared.constants import DATASET_FILES
from shared.filters import FilterState

_UPLOAD_KEY = "uploaded_dataset"
_UPLOAD_DIGEST_KEY = "uploaded_dataset_digest"
_UPLOAD_REF_KEY = "uploaded_dataset_ref"  # {"digest", "info" | None, "error" | None}: the upload on the backend
APPLIED_FILTERS_KEY = "filters"  # the FilterState every page uses


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
    st.session_state["uploaded_unknown_files"] = sorted(set(files) - set(DATASET_FILES))
    files = {k: v for k, v in sorted(files.items()) if k in DATASET_FILES}
    st.session_state[_UPLOAD_KEY] = files
    st.session_state[_UPLOAD_DIGEST_KEY] = hashlib.sha256(b"".join(files.values())).hexdigest()


def _use_built_in_data() -> None:
    ref = st.session_state.get(_UPLOAD_REF_KEY) or {}
    if ref.get("info"):
        client.delete_dataset(ref["info"]["dataset_id"])
    for key in (_UPLOAD_KEY, _UPLOAD_DIGEST_KEY, _UPLOAD_REF_KEY, _UPLOADER_WIDGET, "uploaded_unknown_files"):
        st.session_state.pop(key, None)


def render_data_source_sidebar() -> None:
    """Upload controls, tucked into a collapsed section: most viewers never need them."""
    with st.sidebar.expander("Data source", icon=":material/database:", expanded=using_uploaded_data()):
        st.file_uploader("Upload your dataset (all 8 CSVs)", type="csv", accept_multiple_files=True,
                         key=_UPLOADER_WIDGET, help="Expected files: " + ", ".join(DATASET_FILES))
        if unknown := st.session_state.get("uploaded_unknown_files"):
            st.warning(f"Ignoring unrecognised file(s): {', '.join(unknown)}")
        if using_uploaded_data():
            st.button("Use built-in demo data", on_click=_use_built_in_data, key="use_builtin_data")
        st.caption("Using uploaded data" if using_uploaded_data() else "Using the built-in synthetic demo data")


def using_uploaded_data() -> bool:
    return _UPLOAD_KEY in st.session_state


def _register_upload() -> dict:
    """Send the session's uploaded files to the backend once per set of files (and again if it expired)."""
    ss = st.session_state
    digest = ss[_UPLOAD_DIGEST_KEY]
    ref = ss.get(_UPLOAD_REF_KEY)
    if not ref or ref["digest"] != digest:
        with st.spinner("Checking uploaded files..."):
            try:
                ref = {"digest": digest, "info": client.upload(ss[_UPLOAD_KEY]), "error": None}
            except client.ApiError as exc:
                if exc.status_code not in (413, 422):
                    raise  # backend unreachable: try again next run
                ref = {"digest": digest, "info": None, "error": {"message": exc.message, "issues": exc.issues}}
        ss[_UPLOAD_REF_KEY] = ref
    return ref


def _show_load_error(message: str, issues: list[str]) -> None:
    st.error(message + ("\n\n" + "\n".join(f"- {e}" for e in issues) if issues else ""))


def dataset_info(show_errors: bool = True) -> dict | None:
    """The active dataset on the backend (uploaded or built in), or report why it is unavailable."""
    try:
        if using_uploaded_data():
            ref = _register_upload()
            if ref["error"]:
                if show_errors:
                    _show_load_error(ref["error"]["message"], ref["error"]["issues"])
                return None
            return ref["info"]
        return client.dataset_info(client.BUILTIN)
    except client.ApiError as exc:
        if show_errors:
            _show_load_error(exc.message, exc.issues)
        return None


def dataset_id(show_errors: bool = True) -> str | None:
    info = dataset_info(show_errors)
    return None if info is None else info["dataset_id"]


def data_source_key() -> str:
    """Identifies the active dataset; changes whenever different data is loaded."""
    if using_uploaded_data():
        return f"upload:{st.session_state[_UPLOAD_DIGEST_KEY]}"
    info = dataset_info(show_errors=False)
    return info["source"] if info else "folder:builtin"


def call(fn, *args, show_errors: bool = True, reraise: bool = False):
    """``fn(dataset_id, *args)`` against the active dataset; None (with the problem shown) if it fails.

    An upload the backend no longer holds (restart or expiry) is sent again and the call retried once.
    With ``reraise``, other API errors are raised for the caller to word (AI features)."""
    ds = dataset_id(show_errors)
    if ds is None:
        return None
    try:
        return fn(ds, *args)
    except client.ApiError as exc:
        if exc.status_code == 404 and using_uploaded_data():
            st.session_state.pop(_UPLOAD_REF_KEY, None)
            client.clear_caches()
            ds = dataset_id(show_errors)
            if ds is None:
                return None
            try:
                return fn(ds, *args)
            except client.ApiError as retry_exc:
                exc = retry_exc
        if reraise:
            raise exc from None
        if show_errors:
            _show_load_error(exc.message, exc.issues)
        return None


def applied_filters() -> FilterState:
    return st.session_state.get(APPLIED_FILTERS_KEY, FilterState())


def scope_request() -> dict:
    """The applied filters and today's date: the start of every page request."""
    return {"filters": applied_filters().to_json(), "today": date.today().isoformat()}


def page_data(name: str, **options) -> dict | None:
    """One page's data from the backend, or None when the data could not be loaded (problem shown)."""
    return call(client.page, name, {**scope_request(), **options})


def scope_summary(show_errors: bool = False) -> dict | None:
    """The applied filters in words, and how many events, sponsors and registrations they leave."""
    return call(client.scope, applied_filters().to_json(), date.today(), show_errors=show_errors)


def show_empty_state(what: str = "events") -> None:
    """Empty state with a one-click reset (kept here so existing page imports keep working)."""
    from frontend.components.layout import empty_state

    empty_state(what, key=f"empty_reset_{what}")


def _candidates(widget_key: str, state_key: str) -> list[str]:
    """What a shared picker would choose, in order: its own widget value, then the shared selection."""
    return [v for v in (st.session_state.get(widget_key), st.session_state.get(state_key)) if v]


def event_candidates(key: str) -> list[str]:
    return _candidates(key, "selected_event")


def sponsor_candidates(key: str) -> list[str]:
    return _candidates(key, "selected_sponsor")


def _shared_picker(label: str, options: list[str], names: dict[str, str], state_key: str,
                   widget_key: str) -> str:
    if st.session_state.get(widget_key) not in options:
        st.session_state.pop(widget_key, None)  # filters removed the previous choice
    current = st.session_state.get(state_key)
    index = options.index(current) if current in options else 0
    choice = st.selectbox(label, options, index=index, key=widget_key, format_func=names.get)
    st.session_state[state_key] = choice
    return choice


def event_picker(options: list, key: str) -> str:
    """Selectbox of events (newest first, as sent by the backend); selection is shared across pages."""
    names = {event_id: name for event_id, name in options}
    return _shared_picker("Event", list(names), names, "selected_event", key)


def sponsor_picker(options: list, key: str) -> str:
    """Selectbox of sponsors that have at least one sponsorship, by name; shared across pages."""
    names = {sponsor_id: name for sponsor_id, name in options}
    return _shared_picker("Sponsor", list(names), names, "selected_sponsor", key)
