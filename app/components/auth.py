"""OIDC sign-in and staff allowlist, checked before the app loads any data."""
from __future__ import annotations

import os

import streamlit as st

from core.access import access_required, staff_access_allowed
from core.config import DEFAULT_DATA_DIR, get_settings


def _logout() -> None:
    st.session_state.clear()
    st.logout()


def require_staff_access() -> None:
    settings = get_settings()
    remote = st.get_option("server.address") not in ("127.0.0.1", "localhost", "::1")
    required = access_required(remote=remote,
                               custom_data=(settings.data_dir.resolve() != DEFAULT_DATA_DIR.resolve()
                                            or "uploaded_dataset" in st.session_state
                                            or bool(st.session_state.get("dataset_upload"))),
                               flag=os.getenv("CONFERATLAS_REQUIRE_AUTH", ""))
    if not required:
        return
    allowed = tuple(os.getenv("CONFERATLAS_STAFF_EMAILS", "").split(","))
    try:
        auth = st.secrets.get("auth", {})
        configured = all(auth.get(k) for k in
                         ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"))
    except Exception:
        configured = False
    if not configured or not any(e.strip() for e in allowed):
        st.error("Staff sign-in must be configured before this app can show data. "
                 "Ask the app administrator to complete the authentication setup in the README.")
        st.stop()
    if not st.user.is_logged_in:
        st.title("ConferAtlas staff sign-in")
        st.button("Sign in", on_click=st.login)
        st.stop()
    if not staff_access_allowed(st.user, allowed):
        st.session_state.clear()
        st.error("This account does not have staff access, or its sign-in has expired. "
                 "Use an approved staff account.")
        st.button("Sign out", on_click=_logout)
        st.stop()
    identity = (st.user.get("sub"), st.user.get("email"))
    if st.session_state.get("staff_identity") != identity:
        st.session_state.clear()
        st.session_state["staff_identity"] = identity
    st.sidebar.button("Sign out", on_click=_logout, key="staff_logout")
