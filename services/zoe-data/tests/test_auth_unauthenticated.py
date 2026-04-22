"""Fail-closed default: no X-Session-ID → guest role.

Old behaviour (promote unauthenticated to family-admin) is still reachable via
ZOE_UNAUTHENTICATED_ROLE=family-admin but is opt-in and logs a warning on every
request. See plan `memory_and_self-learning_audit` > `auth-close-unauth`.
"""

import asyncio
import importlib
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _restore_auth_after_each(monkeypatch):
    monkeypatch.delenv("ZOE_UNAUTHENTICATED_ROLE", raising=False)
    if "auth" in sys.modules:
        del sys.modules["auth"]
    import auth

    importlib.reload(auth)
    yield
    monkeypatch.delenv("ZOE_UNAUTHENTICATED_ROLE", raising=False)
    if "auth" in sys.modules:
        del sys.modules["auth"]
    import auth as a2

    importlib.reload(a2)


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
