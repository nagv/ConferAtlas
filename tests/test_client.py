"""The frontend's HTTP boundary: friendly errors for every way a backend call can fail."""
import httpx
import pytest
from streamlit.testing.v1 import AppTest

import frontend.client as client
from frontend.client import api as real_api  # captured before the autouse fixture swaps it

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.is_error, self.content = status >= 400, b"x"

    def json(self):
        return self._body


def _raise(exc):
    def request(*a, **k):
        raise exc
    return request


def test_timeouts_and_connection_failures_are_friendly(monkeypatch):
    monkeypatch.setattr(httpx, "request", _raise(httpx.ReadTimeout("slow")))
    with pytest.raises(client.ApiError) as exc:
        real_api("GET", "/health")
    assert exc.value.status_code == 504 and "took too long" in exc.value.message
    monkeypatch.setattr(httpx, "request", _raise(httpx.ConnectError("refused")))
    with pytest.raises(client.ApiError) as exc:
        real_api("GET", "/health")
    assert exc.value.status_code == 503 and "Cannot reach the backend" in exc.value.message


@pytest.mark.parametrize("detail, message, kind", [
    ({"message": "Could not load data. Please fix these problems:", "issues": ["a"]},
     "Could not load data. Please fix these problems:", None),
    ({"kind": "timeout", "message": "Timed out.", "usage": [{"purpose": "x"}]}, "Timed out.", "timeout"),
    ([{"loc": ["body"], "msg": "bad"}], "Please check the selected filters and request fields.", None),
    ("Missing or invalid API token.", "Missing or invalid API token.", None),
])
def test_error_details_are_parsed(monkeypatch, detail, message, kind):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: _Response(422, {"detail": detail}))
    with pytest.raises(client.ApiError) as exc:
        real_api("POST", "/x")
    assert exc.value.message == message and exc.value.kind == kind


def test_token_and_url_come_from_the_environment(monkeypatch):
    seen = {}

    def request(method, url, headers=None, timeout=None, **kwargs):
        seen.update(url=url, headers=headers, timeout=timeout)
        return _Response(200, {"status": "ok"})
    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setenv("BACKEND_URL", "http://backend:8000/")
    monkeypatch.setenv("BACKEND_API_TOKEN", "s3cret")
    real_api("GET", "/health", timeout=client.ai_timeout_s())
    assert seen == {"url": "http://backend:8000/health", "headers": {"X-API-Token": "s3cret"}, "timeout": 600.0}


def test_backend_unavailable_is_friendly(monkeypatch):
    def unavailable(*args, **kwargs):
        raise client.ApiError("Cannot reach the backend. Start the API service and try again.", 503)
    monkeypatch.setattr(client, "api", unavailable)
    at = AppTest.from_file(str(ROOT / "frontend" / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("Cannot reach the backend" in e.value for e in at.error)


def test_expired_upload_is_sent_again(monkeypatch, tiny_tables):
    """A backend restart forgets uploads: the page re-registers the session's files and carries on."""
    from backend.main import store
    from tests.conftest import seed_upload

    script = ("import sys\nsys.path.insert(0, {root!r})\nfrom frontend.views import overview\noverview.render()\n"
              .format(root=str(ROOT)))
    at = AppTest.from_string(script, default_timeout=60)
    stored = seed_upload(at, tiny_tables)
    store.delete(stored.id)  # as if the backend restarted
    uploads = []
    monkeypatch.setattr(store, "add_upload", lambda files: uploads.append(files) or store.put_tables(tiny_tables))
    at.run()
    assert not at.exception and not at.error
    assert uploads and {m.label: m.value for m in at.metric}["Events"] == "2"
