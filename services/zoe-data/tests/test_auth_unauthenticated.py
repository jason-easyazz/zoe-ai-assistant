"""Fail-closed default: no X-Session-ID → guest role.

Old behaviour (promote unauthenticated to family-admin) is still reachable via
ZOE_UNAUTHENTICATED_ROLE=family-admin but is opt-in and logs a warning on every
request. See plan `memory_and_self-learning_audit` > `auth-close-unauth`.
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import asyncio
import importlib
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _restore_auth_after_each(monkeypatch):
    """Restore the ORIGINAL auth module object after each test.

    The old teardown del-and-reloaded auth, which "resets" env-derived state but
    swaps in a brand-new module object. Everything imported at collection time
    (e.g. routers' ``Depends(get_current_user)``) still holds the ORIGINAL
    function, so later tests that key ``app.dependency_overrides`` by the new
    object never match → the real auth runs → guest → 403s. That was the
    cross-test isolation leak behind the validate.yml --deselect exclusions
    (bisected 2026-07-06: this file was the necessary poison). Restoring the
    saved module IDENTITY (same pattern as test_telegram_link) heals it.
    """
    saved = sys.modules.get("auth")
    monkeypatch.delenv("ZOE_UNAUTHENTICATED_ROLE", raising=False)
    yield
    monkeypatch.delenv("ZOE_UNAUTHENTICATED_ROLE", raising=False)
    if saved is not None:
        sys.modules["auth"] = saved
    else:
        sys.modules.pop("auth", None)


def _reload_auth(monkeypatch, *, role: str | None):
    if role is None:
        monkeypatch.delenv("ZOE_UNAUTHENTICATED_ROLE", raising=False)
    else:
        monkeypatch.setenv("ZOE_UNAUTHENTICATED_ROLE", role)
    if "auth" in sys.modules:
        del sys.modules["auth"]
    import auth as m

    return importlib.reload(m)


def test_no_session_defaults_to_guest(monkeypatch):
    """Unset env → guest. Fail-closed is the new default."""
    auth = _reload_auth(monkeypatch, role=None)
    req = MagicMock()
    req.headers.get.return_value = ""
    user = asyncio.run(auth.get_current_user(req))
    assert user["role"] == "guest"
    assert user["user_id"] == "guest"


def test_no_session_guest_when_env_set(monkeypatch):
    """Explicit ZOE_UNAUTHENTICATED_ROLE=guest still resolves to guest."""
    auth = _reload_auth(monkeypatch, role="guest")
    req = MagicMock()
    req.headers.get.return_value = ""
    user = asyncio.run(auth.get_current_user(req))
    assert user["role"] == "guest"
    assert user["user_id"] == "guest"


def test_family_admin_opt_in_still_possible(monkeypatch):
    """Legacy LAN deployments can flip ZOE_UNAUTHENTICATED_ROLE=family-admin to
    restore the pre-Phase-0 behaviour during rollout. The override is noisy
    (WARNING per request) but still functional."""
    auth = _reload_auth(monkeypatch, role="family-admin")
    req = MagicMock()
    req.headers.get.return_value = ""
    req.url.path = "/api/foo"
    req.method = "GET"
    user = asyncio.run(auth.get_current_user(req))
    assert user["role"] == "admin"
    assert user["user_id"] == auth.DEFAULT_USER_ID


def test_degraded_user_is_guest_never_admin(monkeypatch):
    """Fail-OPEN degraded mode (auth service down, ZOE_AUTH_FAIL_CLOSED disabled)
    must resolve to GUEST, never the household admin — an outage can't elevate."""
    auth = _reload_auth(monkeypatch, role=None)
    # Patch the already-loaded module global directly (no env-set + reload).
    monkeypatch.setattr(auth, "_AUTH_FAIL_CLOSED", False)
    degraded = auth._degraded_user()
    assert degraded is not None
    assert degraded["user_id"] == "guest"
    assert degraded["role"] == "guest"
    assert degraded["user_id"] != auth.DEFAULT_USER_ID


def test_validated_user_without_id_falls_back_to_guest(monkeypatch):
    """A malformed auth response (200 but no user_id/id) normalises to guest,
    not the admin id."""
    auth = _reload_auth(monkeypatch, role=None)
    # No id anywhere → guest, never family-admin.
    assert auth._normalize_auth_user({"role": "user"})["user_id"] == "guest"
    assert auth._normalize_auth_user({})["user_id"] == "guest"
    assert auth._normalize_auth_user({"user": {"role": "member"}})["user_id"] == "guest"
    assert auth._normalize_auth_user({"role": "user"})["user_id"] != auth.DEFAULT_USER_ID
    # CRITICAL: a malformed `{"role": "admin"}` with no id must ALSO drop the role —
    # otherwise user_id=guest + role=admin would pass require_admin.
    bad = auth._normalize_auth_user({"role": "admin"})
    assert bad["user_id"] == "guest" and bad["role"] == "guest"
    assert auth._normalize_auth_user({"user": {"role": "admin"}})["role"] == "guest"
    # A well-formed response still resolves the real user + role.
    assert auth._normalize_auth_user({"user_id": "jason", "role": "user"})["user_id"] == "jason"
    assert auth._normalize_auth_user({"user": {"id": "karen"}})["user_id"] == "karen"
    assert auth._normalize_auth_user({"user_id": "amy", "role": "admin"})["role"] == "admin"
