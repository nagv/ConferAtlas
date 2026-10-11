"""OIDC sign-in and staff allowlist, checked before the app loads any data."""
from __future__ import annotations

import os

import streamlit as st

from frontend import client
from shared.access import access_required, public_demo_enabled, staff_access_allowed


def _logout() -> None:
    st.session_state.clear()
    st.logout()


def _backend_uses_custom_data() -> bool:
    """True when the backend reads a data folder other than the built-in demo data."""
    try:
        return bool(client.status()["custom_data_dir"])
    except client.ApiError:
        return False  # no backend means no data to show; the app reports that it cannot reach it


def _served_remotely() -> bool:
    return st.get_option("server.address") not in ("127.0.0.1", "localhost", "::1")


def public_demo_mode() -> bool:
    """CONFERATLAS_PUBLIC_DEMO is on: the built-in demo is shown without sign-in; uploads are not offered."""
    return public_demo_enabled(os.getenv("CONFERATLAS_PUBLIC_DEMO", ""), os.getenv("CONFERATLAS_REQUIRE_AUTH", ""))


def require_staff_access() -> None:
    client.load_env()
    required = access_required(remote=_served_remotely(),
                               custom_data=("uploaded_dataset" in st.session_state
                                            or bool(st.session_state.get("dataset_upload"))),
                               flag=os.getenv("CONFERATLAS_REQUIRE_AUTH", ""),
                               public_demo=os.getenv("CONFERATLAS_PUBLIC_DEMO", "")) or _backend_uses_custom_data()
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
