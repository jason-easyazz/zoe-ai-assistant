"""A device token is the PANEL, never the panel's bound member (security fix 2026-10-17).

``panel_provision.provision_confirm`` binds the confirming member as the panel's ``default`` user, and
``get_current_user`` used to resolve the device token AS that member (a token minted with role ``admin`` as an admin). The
token lives on the panel's disk with no expiry and was sent with certificate checks off, so it was a standing member
credential. Now it is the panel principal (guest-class + ``panel_id``): the household list and home control, nothing personal.
A member at the panel proves who they are per turn (voice claim / fresh PIN session), which the voice lanes already require.
Invented data only.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import panel_principal
import routers.panel_auth as panel_auth

pytestmark = pytest.mark.ci_safe


class _Cur:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _BindingDb:
    """The panel HAS a default member binding: the old rule would have resolved the token to that member."""

    def __init__(self):
        self.reads = 0

    async def execute(self, sql, params=()):
        self.reads += 1
        return _Cur({"user_id": "casey"} if "panel_user_bindings" in sql else None)


@pytest.fixture
def bound_panel(monkeypatch):
    db = _BindingDb()

    @contextlib.asynccontextmanager
    async def _ctx():
        yield db

    import db_pool

    monkeypatch.setattr(db_pool, "get_db_ctx", _ctx)
    monkeypatch.delenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", raising=False)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    return db


def _req(headers):
    return Request({"type": "http", "method": "GET", "path": "/api/x",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "client": ("192.0.2.9", 1)})


@pytest.mark.parametrize("token_role", ["kiosk", "voice", "voice-daemon", "admin"])
async def test_a_bound_panels_token_is_the_panel_principal_not_the_member(bound_panel, monkeypatch, token_role):
    monkeypatch.setattr(panel_auth, "lookup_device_token", lambda raw: {"panel_id": "kitchen-panel", "role": token_role})
    user = await auth.get_current_user(_req({"X-Device-Token": "tok"}))
    assert user["user_id"] == "guest" and user["role"] == "guest" and user["permissions"] == []
    assert user["panel_id"] == "kitchen-panel"
    assert bound_panel.reads == 0  # the member binding is not even consulted
    # ... so it is the panel (household list, home control) and no member, let alone an admin
    assert panel_principal.is_member(user) is False
    assert await panel_principal.is_panel_guest(_req({}), user) is True
    assert await panel_principal.may_control_home(_req({}), user) is True
    with pytest.raises(HTTPException) as signed_in:
        await auth.require_signed_in(user)
    assert signed_in.value.status_code == 403
    with pytest.raises(HTTPException) as admin:
        await auth.require_admin(user)
    assert admin.value.status_code == 403


async def test_the_old_member_binding_is_an_explicit_rollback_only(bound_panel, monkeypatch):
    monkeypatch.setattr(panel_auth, "lookup_device_token", lambda raw: {"panel_id": "kitchen-panel", "role": "kiosk"})
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", value)
        assert (await auth.get_current_user(_req({"X-Device-Token": "tok"})))["user_id"] == "guest", value
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", "1")
    legacy = await auth.get_current_user(_req({"X-Device-Token": "tok"}))
    assert legacy["user_id"] == "casey" and legacy["role"] == "member"


async def test_an_unknown_or_revoked_token_is_still_plain_anonymous(bound_panel, monkeypatch):
    monkeypatch.setattr(panel_auth, "lookup_device_token", lambda raw: None)
    user = await auth.get_current_user(_req({"X-Device-Token": "stolen-then-revoked"}))
    assert user["user_id"] == "guest" and "panel_id" not in user
    assert await panel_principal.may_control_home(_req({}), user) is False
