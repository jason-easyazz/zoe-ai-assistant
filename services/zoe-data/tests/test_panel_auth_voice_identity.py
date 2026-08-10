"""Voice PIN challenge resolves to the person who JUST AUTHENTICATED (deferred B2).

A voice-originated PIN challenge carries no user (``create_pin_challenge_internal``
is called with ``user_id=None``). Before this fix, ``submit_pin`` resolved the
acting identity ONLY from the panel's ``binding_type='default'`` row, so a
NON-DEFAULT household member who picked their profile and typed their CORRECT PIN
was validated against the DEFAULT user → 403 → a burned lockout attempt on a
CORRECTLY answered challenge → the parked voice turn silently dropped.

The fix binds approval to the principal proven by a SERVER-VERIFIED session
credential (``X-Session-ID``, validated by zoe-auth and checked against
``panel_user_bindings``) — never a caller-asserted ``user_id`` (the endpoint is
unauthenticated; that assertion is still ignored). Plus a mitigation: a PIN
failure on the default-binding FALLBACK (identity not attributable to the
answerer) does NOT advance the challenge lockout.

Every test is negative-controlled against a contrasting case (see the class-level
docstrings): a wrong PIN for a verified principal still fails AND still counts;
a caller-asserted id alone never shifts validation identity; an unbound session
falls back to the default rather than hijacking.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import auth  # noqa: E402
import routers.panel_auth as panel_auth  # noqa: E402
from routers import voice_tts  # noqa: E402
from fastapi import HTTPException  # noqa: E402

pytestmark = pytest.mark.ci_safe

PANEL = "zoe-touch-pi"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDb:
    """Fake async DB dispatching on SQL text.

    ``bindings`` = user_ids bound to PANEL; ``default_user`` = the
    binding_type='default' user (or None). Records UPDATE/INSERT params so tests
    can assert the challenge was resolved and the panel session persisted.
    """

    def __init__(self, challenge, bindings, default_user):
        self.challenge = dict(challenge)
        self.bindings = set(bindings)
        self.default_user = default_user
        self.updates = []
        self.inserts = []
        self.commits = 0

    async def execute(self, sql, params=()):
        u = " ".join(sql.split()).upper()
        if u.startswith("SELECT * FROM PANEL_AUTH_CHALLENGES"):
            return _Cursor(dict(self.challenge))
        if "FROM PANEL_USER_BINDINGS" in u and "BINDING_TYPE = 'DEFAULT'" in u:
            return _Cursor({"user_id": self.default_user} if self.default_user else None)
        if "FROM PANEL_USER_BINDINGS" in u and "AND USER_ID = ?" in u:
            uid = params[1]
            return _Cursor({"1": 1} if uid in self.bindings else None)
        if "FROM PANEL_USER_BINDINGS" in u:  # has-binding (panel only)
            return _Cursor({"1": 1} if self.bindings else None)
        if u.startswith("UPDATE PANEL_AUTH_CHALLENGES"):
            self.updates.append(params)
            return _Cursor()
        if u.startswith("INSERT INTO UI_PANEL_SESSIONS"):
            self.inserts.append(params)
            return _Cursor()
        return _Cursor()

    async def commit(self):
        self.commits += 1


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _make_challenge(challenge_id="chal-1", user_id=None, with_pending=True):
    action_ctx = {}
    if with_pending:
        action_ctx = {
            "kind": "voice_turn",
            "pending_id": "pend-123",
            "panel_id": PANEL,
            "pending_transcript": "what is on my calendar",
            "pending_session_id": "vsess-1",
        }
    return {
        "challenge_id": challenge_id,
        "panel_id": PANEL,
        "user_id": user_id,
        "status": "pending",
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
        "action_context": json.dumps(action_ctx),
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Reset in-memory lockout + voice state; stub the passcode + session backends."""
    panel_auth._pin_attempts.clear()
    voice_tts._PENDING_VOICE_IDENT.clear()
    voice_tts._VOICE_SESSIONS.clear()

    posted = []

    class _Resp:
        def __init__(self, body):
            self.status_code = 200
            self._b = body

        def json(self):
            return self._b

    valid = set()  # (user_id, passcode) tuples that zoe-auth would accept

    class _FakeHc:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted.append(dict(json or {}))
            ok = (json.get("user_id"), json.get("passcode")) in valid
            return _Resp({"success": ok})

    monkeypatch.setattr(panel_auth.httpx, "AsyncClient", _FakeHc)

    sessions = {}  # session_id -> user dict (as zoe-auth would return), or None

    async def _fake_validate(session_id):
        return sessions.get(session_id)

    monkeypatch.setattr(auth, "_validate_with_auth_service", _fake_validate)

    replays = []

    async def _fake_voice_command(payload, caller=None, stream=False, db=None):
        replays.append({"payload": payload, "caller": caller})
        return {"ok": True}

    monkeypatch.setattr(voice_tts, "voice_command", _fake_voice_command)

    return {"posted": posted, "valid": valid, "sessions": sessions, "replays": replays}


async def _settle():
    """Let the fire-and-forget replay task run."""
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# 1. Full fix — non-default bound member's correct PIN is approved + replayed.
#    Negative control lives in test 5 (identical flow, session omitted → default).
# --------------------------------------------------------------------------- #
async def test_nondefault_session_principal_approved_and_replayed(_isolate):
    ctx = _isolate
    ctx["sessions"]["sess-bob"] = {"user_id": "bob", "role": "user", "username": "bob"}
    ctx["valid"].add(("bob", "1234"))  # zoe-auth accepts bob's PIN only

    voice_tts._PENDING_VOICE_IDENT[PANEL] = {
        "pending_id": "pend-123",
        "transcript": "what is on my calendar",
        "session_id": "vsess-1",
        "expire_at": time.monotonic() + 120,
    }
    voice_tts._VOICE_SESSIONS[PANEL] = {"session_id": "vsess-1"}

    db = _FakeDb(_make_challenge(), bindings={"jason", "bob"}, default_user="jason")
    req = _Req({"X-Session-ID": "sess-bob"})

    result = await panel_auth.submit_pin(
        {"challenge_id": "chal-1", "pin": "1234"}, request=req, db=db
    )
    await _settle()

    assert result["status"] == "approved"
    # Identity resolved to BOB (the authenticated session), NOT the panel default.
    assert ctx["posted"][-1]["user_id"] == "bob"
    # Correct answer must NOT advance the lockout counter.
    assert "chal-1" not in panel_auth._pin_attempts
    # Session bound + held turn replayed under bob.
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") == "bob"
    assert ctx["replays"], "held voice turn was not replayed"
    assert ctx["replays"][-1]["payload"]["identified_user_id"] == "bob"


# --------------------------------------------------------------------------- #
# 2. A genuinely wrong PIN for a VERIFIED principal is still refused AND still
#    counts (negative control that the mitigation is scoped, not blanket).
# --------------------------------------------------------------------------- #
async def test_wrong_pin_for_verified_principal_refused_and_counts(_isolate):
    ctx = _isolate
    ctx["sessions"]["sess-bob"] = {"user_id": "bob", "role": "user"}
    ctx["valid"].add(("bob", "1234"))  # real PIN; attempt uses the wrong one

    db = _FakeDb(_make_challenge(), bindings={"jason", "bob"}, default_user="jason")
    req = _Req({"X-Session-ID": "sess-bob"})

    with pytest.raises(HTTPException) as ei:
        await panel_auth.submit_pin(
            {"challenge_id": "chal-1", "pin": "9999"}, request=req, db=db
        )
    assert ei.value.status_code == 403
    # Attributable failure (verified principal) DOES advance the challenge lockout.
    assert panel_auth._pin_attempts.get("chal-1", {}).get("count") == 1


# --------------------------------------------------------------------------- #
# 3. SECURITY INVARIANT — a caller-asserted user_id ALONE never shifts the
#    validation identity. No session → default binding is used; the payload's
#    "user_id": "bob" is ignored and jason (the default) is validated.
# --------------------------------------------------------------------------- #
async def test_caller_asserted_user_id_alone_never_shifts_identity(_isolate):
    ctx = _isolate
    ctx["valid"].add(("jason", "1111"))  # only the DEFAULT user's PIN is accepted

    db = _FakeDb(_make_challenge(with_pending=False), bindings={"jason", "bob"}, default_user="jason")
    req = _Req({})  # NO X-Session-ID

    result = await panel_auth.submit_pin(
        {"challenge_id": "chal-1", "pin": "1111", "user_id": "bob"}, request=req, db=db
    )
    assert result["status"] == "approved"
    # Validated against jason (default), NOT the caller-asserted bob.
    assert ctx["posted"][-1]["user_id"] == "jason"


# --------------------------------------------------------------------------- #
# 4. MITIGATION — a PIN failure on the default-binding FALLBACK does NOT advance
#    the lockout (identity not attributable to the answerer). Negative control vs
#    test 2, where an attributable failure DID count.
# --------------------------------------------------------------------------- #
async def test_default_fallback_mismatch_does_not_lock_out(_isolate):
    ctx = _isolate
    ctx["valid"].add(("jason", "1111"))  # jason's PIN; the answerer types 2222

    db = _FakeDb(_make_challenge(), bindings={"jason"}, default_user="jason")
    req = _Req({})  # no session → falls back to the default binding

    with pytest.raises(HTTPException) as ei:
        await panel_auth.submit_pin(
            {"challenge_id": "chal-1", "pin": "2222"}, request=req, db=db
        )
    assert ei.value.status_code == 403
    # The worst harm is removed: a non-attributable failure burns NO lockout budget.
    assert "chal-1" not in panel_auth._pin_attempts


# --------------------------------------------------------------------------- #
# 5. An authenticated but panel-UNBOUND session cannot hijack the challenge — it
#    falls back to the panel default (also the negative control for test 1: same
#    flow, but the session does not belong to the panel).
# --------------------------------------------------------------------------- #
async def test_unbound_session_falls_back_to_default(_isolate):
    ctx = _isolate
    ctx["sessions"]["sess-eve"] = {"user_id": "eve", "role": "user"}  # valid, NOT bound
    ctx["valid"].add(("jason", "1111"))

    db = _FakeDb(_make_challenge(with_pending=False), bindings={"jason"}, default_user="jason")
    req = _Req({"X-Session-ID": "sess-eve"})

    result = await panel_auth.submit_pin(
        {"challenge_id": "chal-1", "pin": "1111"}, request=req, db=db
    )
    assert result["status"] == "approved"
    # eve's validated session did NOT shift identity — jason (default) was used.
    assert ctx["posted"][-1]["user_id"] == "jason"
