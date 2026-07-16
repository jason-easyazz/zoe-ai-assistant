"""Household-write endpoints must require a SIGNED-IN user, not just resolve one.

The bug: `get_current_user` RESOLVES an identity but never enforces it — an
unauthenticated caller comes back as GUEST (least privilege) instead of a 403. So
`Depends(get_current_user)` alone authenticates nothing. `PUT /api/voice/voice`
depended on it, which meant any LAN client could change the voice Zoe speaks with
for the whole household, with no session at all (verified live: an unauthenticated
PUT returned ok:true and persisted the new voice).

These pin BOTH halves so it can't silently regress:
  1. require_signed_in's logic (guest/unauthenticated → 403, real user → passes);
  2. the WIRING — the household-write endpoints actually depend on it. A cleanup
     that swaps the dependency back to get_current_user reopens the hole while every
     behaviour test still passes, so the wiring is asserted at the source level.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]


def _auth():
    """Import `auth` lazily, INSIDE each test.

    Deliberately not a module-level import: this suite has a known auth-module
    identity hazard (a test that swaps sys.modules['auth'] leaves later
    dependency_overrides keyed to a stale module object). Binding auth at
    COLLECTION time perturbs that ordering and made an unrelated integration test
    (test_zoe_core_client::test_tool_action_dispatches) fail in the full lane while
    passing standalone. Importing inside the test keeps this file inert until it runs.
    """
    import auth as _a
    return _a


def _src(rel: str) -> str:
    return (SVC / rel).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_require_signed_in_rejects_guest():
    # Exactly what get_current_user returns for an unauthenticated caller.
    guest = {"user_id": "guest", "role": "guest", "username": "guest", "permissions": []}
    with pytest.raises(HTTPException) as exc:
        await _auth().require_signed_in(guest)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [
    {"user_id": None, "role": None},
    {"user_id": "guest", "role": "admin"},   # guest id must not be laundered by a role
    {"user_id": "jason", "role": "guest"},   # guest role must not be laundered by an id
])
async def test_require_signed_in_rejects_partial_identities(user):
    with pytest.raises(HTTPException) as exc:
        await _auth().require_signed_in(user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_signed_in_allows_a_real_user():
    user = {"user_id": "jason", "role": "user", "username": "jason"}
    assert await _auth().require_signed_in(user) is user


@pytest.mark.asyncio
async def test_require_signed_in_allows_any_household_member_not_just_admin():
    # It is NOT require_admin — a normal signed-in member may change these.
    user = {"user_id": "sam", "role": "family", "username": "sam"}
    assert await _auth().require_signed_in(user) is user


def test_household_write_endpoints_depend_on_require_signed_in():
    # WIRING guard: these two PUTs change household-wide state. If either falls back
    # to get_current_user, an unauthenticated caller is silently allowed again.
    voice = _src("routers/voice_settings.py")
    assert "require_signed_in" in voice, "voice picker lost its signed-in gate"
    assert 'async def set_voice(payload: dict, user: dict = Depends(require_signed_in))' in voice, \
        "PUT /api/voice/voice must depend on require_signed_in, not get_current_user"

    system = _src("routers/system.py")
    assert "user: dict = Depends(require_signed_in)," in system, \
        "PUT /api/system/panel/idle-logout must depend on require_signed_in"


def test_reads_stay_open():
    # The catalogue read is deliberately NOT gated — the panel renders it and it
    # exposes nothing sensitive. Guarding this keeps the tightening from creeping.
    voice = _src("routers/voice_settings.py")
    assert 'async def list_voices(user: dict = Depends(get_current_user))' in voice, \
        "GET /api/voice/voices should stay readable (not signed-in gated)"


def test_require_signed_in_is_documented_as_distinct_from_require_admin():
    doc = inspect.getdoc(_auth().require_signed_in) or ""
    assert "require_admin" in doc, "the helper must say how it differs from require_admin"
