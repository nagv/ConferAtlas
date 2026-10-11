"""Fail-closed authorization for staff identities supplied by OIDC."""
from __future__ import annotations

import math
import time
from collections.abc import Mapping


def _on(value: str) -> bool:
    return value.strip().lower() not in ("", "false", "0", "no")


def public_demo_enabled(public_demo: str = "", flag: str = "") -> bool:
    """The explicit opt-in to show the built-in synthetic demo remotely without sign-in.

    Off unless set, and always off when sign-in is required with CONFERATLAS_REQUIRE_AUTH."""
    return public_demo.strip().lower() in ("true", "1", "yes") and not _on(flag)


def access_required(*, remote: bool, custom_data: bool, flag: str = "", public_demo: str = "") -> bool:
    """Sign-in is needed for remote serving, any non-demo data, or when asked for.

    ``public_demo`` (CONFERATLAS_PUBLIC_DEMO) only lifts the remote rule; uploads and custom data
    folders still require sign-in."""
    if custom_data or _on(flag):
        return True
    return remote and not public_demo_enabled(public_demo, flag)


def staff_access_allowed(claims: Mapping, allowed_emails: tuple[str, ...],
                         now: float | None = None) -> bool:
    email = str(claims.get("email", "")).strip().casefold()
    if not email or claims.get("email_verified") is not True:
        return False
    if email not in {e.strip().casefold() for e in allowed_emails if e.strip()}:
        return False
    try:
        expiry = float(claims.get("exp"))
    except (ValueError, TypeError):
        return False
    return math.isfinite(expiry) and expiry > (time.time() if now is None else now)
