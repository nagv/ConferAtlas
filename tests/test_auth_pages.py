"""Exercise the access gate before any private page content renders."""
import time
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest


class User(dict):
    is_logged_in = True


@pytest.mark.parametrize("kind", ["unconfigured", "logged_out", "wrong_staff", "expired", "allowed"])
def test_staff_gate_blocks_private_content(monkeypatch, kind):
    monkeypatch.setenv("CONFERATLAS_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CONFERATLAS_STAFF_EMAILS", "staff@example.com")
    secrets = {"auth": dict.fromkeys(("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"), "configured")}
    monkeypatch.setattr(st, "secrets", {} if kind == "unconfigured" else secrets)
    user = User(email="staff@example.com", email_verified=True, exp=time.time()+3600, sub="staff")
    if kind == "logged_out": user.is_logged_in = False
    if kind == "wrong_staff": user["email"] = "outsider@example.com"
    if kind == "expired": user["exp"] = 1
    monkeypatch.setattr(st, "user", user)
    at = AppTest.from_string("from frontend.components.auth import require_staff_access\nimport streamlit as st\nrequire_staff_access()\nst.markdown('PRIVATE CONTENT')").run()
    assert not at.exception
    assert any(m.value == "PRIVATE CONTENT" for m in at.markdown) is (kind == "allowed")


def test_staff_identity_change_discards_old_session(monkeypatch):
    monkeypatch.setenv("CONFERATLAS_REQUIRE_AUTH", "true")
    monkeypatch.setenv("CONFERATLAS_STAFF_EMAILS", "staff@example.com")
    monkeypatch.setattr(st, "secrets", {"auth": dict.fromkeys(("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"), "configured")})
    monkeypatch.setattr(st, "user", User(email="staff@example.com", email_verified=True, exp=time.time()+3600, sub="new-staff"))
    at = AppTest.from_string("from frontend.components.auth import require_staff_access\nrequire_staff_access()").run()
    at.session_state["staff_identity"] = ("old-staff", "old@example.com")
    at.session_state["ask_history"] = ["private old answer"]
    at.run()
    assert not at.exception
    assert "ask_history" not in at.session_state


@pytest.mark.parametrize("source", ["uploaded_dataset", "dataset_upload"])
def test_local_upload_requires_staff_before_data_is_shown(monkeypatch, source):
    monkeypatch.setattr(st, "secrets", {})
    at = AppTest.from_string("from frontend.components.auth import require_staff_access\nimport streamlit as st\nrequire_staff_access()\nst.markdown('PRIVATE UPLOAD')")
    at.session_state[source] = {"events.csv": b"private"} if source == "uploaded_dataset" else ["uploaded file"]
    at.run()
    assert not at.exception
    assert at.error and "Staff sign-in" in at.error[0].value
    assert not any(m.value == "PRIVATE UPLOAD" for m in at.markdown)
