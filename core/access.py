"""Fail-closed authorization for staff identities (implemented in ``shared.access``)."""
from shared.access import access_required, staff_access_allowed

__all__ = ["access_required", "staff_access_allowed"]
