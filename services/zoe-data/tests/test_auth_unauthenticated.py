"""Optional guest principal when no X-Session-ID (ZOE_UNAUTHENTICATED_ROLE=guest)."""

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


def _reload_auth(monkeypatch, *, guest: bool | None):
    if guest is True:
        monkeypatch.setenv("ZOE_UNAUTHENTICATED_ROLE", "guest")
    elif guest is False:
        monkeypatch.delenv("ZOE_UNAUTHENTICATED_ROLE", raising=False)
    if "auth" in sys.modules:
        del sys.modules["auth"]
    import auth as m

    return importlib.reload(m)


def test_no_session_default_admin(monkeypatch):
    auth = _reload_auth(monkeypatch, guest=False)
    req = MagicMock()
    req.headers.get.return_value = ""
    user = asyncio.run(auth.get_current_user(req))
    assert user["role"] == "admin"
    assert user["user_id"] == auth.DEFAULT_USER_ID


def test_no_session_guest_when_env_set(monkeypatch):
    auth = _reload_auth(monkeypatch, guest=True)
    req = MagicMock()
    req.headers.get.return_value = ""
    user = asyncio.run(auth.get_current_user(req))
    assert user["role"] == "guest"
    assert user["user_id"] == "guest"
