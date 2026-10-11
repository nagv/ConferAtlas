"""Fail-closed authorization for staff identities supplied by OIDC."""
from __future__ import annotations

import math
import time
from collections.abc import Mapping


def access_required(*, remote: bool, custom_data: bool, flag: str = "") -> bool:
    return remote or custom_data or flag.strip().lower() not in ("", "false", "0", "no")


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
