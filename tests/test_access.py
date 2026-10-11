"""Staff access rules reject unknown, unverified and expired identities."""
import pytest


def test_access_requires_verified_allowlisted_unexpired_identity():
    from core.access import staff_access_allowed
    claims = {"email": "Staff@Example.com", "email_verified": True, "exp": 2000}
    assert staff_access_allowed(claims, ("staff@example.com",), now=1000)
    for change in ({"email": "other@example.com"}, {"email_verified": False},
                   {"email_verified": "true"}, {"exp": 999}, {"exp": None}, {"exp": float("inf")}):
        assert not staff_access_allowed({**claims, **change}, ("staff@example.com",), now=1000)
    assert not staff_access_allowed(claims, (), now=1000)


@pytest.mark.parametrize("remote, custom, flag, expected", [
    (False, False, "", False), (False, False, "true", True),
    (True, False, "false", True), (False, True, "false", True),
    (False, False, "typo", True),
])
def test_access_required_for_remote_or_custom_data(remote, custom, flag, expected):
    from core.access import access_required
    assert access_required(remote=remote, custom_data=custom, flag=flag) is expected


@pytest.mark.parametrize("remote, custom, flag, public_demo, expected", [
    (True, False, "", "true", False),    # opt-in: the remote synthetic demo is open
    (True, False, "", "", True),         # off by default
    (True, False, "", "maybe", True),    # anything but an explicit yes keeps it closed
    (True, True, "", "true", True),      # uploads and custom data folders still need sign-in
    (True, False, "true", "true", True),  # REQUIRE_AUTH always wins
    (False, False, "", "true", False),
])
def test_public_demo_only_lifts_the_remote_rule(remote, custom, flag, public_demo, expected):
    from shared.access import access_required
    assert access_required(remote=remote, custom_data=custom, flag=flag, public_demo=public_demo) is expected
