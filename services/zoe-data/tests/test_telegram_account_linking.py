"""Tests for Telegram account linking (feat/telegram-account-linking).

Covers three pieces:
  1. Profile telegram_id set/get/unlink + uniqueness (routers/user_profile.py).
  2. The internal resolver telegram_id → user_id (routers/system.py).
  3. The /api/chat TRUSTED forwarded-user override security boundary
     (auth.resolve_acting_user): internal caller may set X-Zoe-User-Id;
     a public caller may NOT (no impersonation).

The user_preferences table is modelled by a tiny in-memory fake that understands
exactly the SQL these routers issue (SELECT prefs by user_id, INSERT ... ON
CONFLICT, and the `prefs::jsonb ->> 'telegram_id'` match).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
from routers import system, user_profile


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class FakePrefsDB:
    """In-memory user_preferences store keyed by user_id → prefs dict."""

    def __init__(self, seed: dict[str, dict] | None = None):
        # user_id → (prefs_json_str, updated_at_counter)
        self.store: dict[str, tuple[str, int]] = {}
        self._clock = 0
        for uid, prefs in (seed or {}).items():
            self._clock += 1
            self.store[uid] = (json.dumps(prefs), self._clock)
        self.commits = 0

    async def execute(self, sql: str, params=()):
        p = list(params or [])
        norm = " ".join(sql.split())

        if norm.startswith("SELECT prefs FROM user_preferences WHERE user_id ="):
            uid = p[0]
            entry = self.store.get(uid)
            return _Cursor([{"prefs": entry[0]}] if entry else [])

        if norm.startswith("SELECT user_id, prefs FROM user_preferences WHERE prefs::jsonb ->> 'telegram_id' ="):
            tid = p[0]
            exclude = p[1] if "user_id != ?" in norm else None
            matches = []
            for uid, (prefs_str, ts) in self.store.items():
                if exclude is not None and uid == exclude:
                    continue
                try:
                    prefs = json.loads(prefs_str)
                except (json.JSONDecodeError, TypeError):
                    continue
                if prefs.get("telegram_id") == tid:
                    matches.append((uid, prefs_str, ts))
            # ORDER BY updated_at ASC (deterministic); LIMIT 1 when no exclude.
            matches.sort(key=lambda m: m[2])
            rows = [{"user_id": u, "prefs": pr} for (u, pr, _ts) in matches]
            if "LIMIT 1" in norm:
                rows = rows[:1]
            return _Cursor(rows)

        if norm.startswith("INSERT INTO user_preferences"):
            uid, prefs_json = p[0], p[1]
            self._clock += 1
            self.store[uid] = (prefs_json, self._clock)
            return _Cursor()

        raise AssertionError(f"unexpected SQL: {norm}")

    async def commit(self):
        self.commits += 1


def _profile_client(db: FakePrefsDB, *, role: str = "user", user_id: str = "jason") -> TestClient:
    app = FastAPI()
    app.include_router(user_profile.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[user_profile.get_current_user] = lambda: {
        "user_id": user_id,
        "role": role,
    }
    app.dependency_overrides[user_profile.get_db] = fake_get_db
    return TestClient(app)


def _system_client(db: FakePrefsDB) -> TestClient:
    app = FastAPI()
    app.include_router(system.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[system.get_db] = fake_get_db
    return TestClient(app)


# ─── 1. Profile telegram_id set / get / unlink / uniqueness ──────────────────


def test_profile_set_and_get_telegram_id():
    db = FakePrefsDB()
    client = _profile_client(db)

    r = client.put("/api/user/profile/telegram", json={"telegram_id": "99999"})
    assert r.status_code == 200, r.text
    assert r.json() == {"telegram_id": "99999", "linked": True}

    r = client.get("/api/user/profile/telegram")
    assert r.status_code == 200
    assert r.json() == {"telegram_id": "99999"}


def test_profile_get_when_unlinked_is_null():
    db = FakePrefsDB()
    client = _profile_client(db)
    r = client.get("/api/user/profile/telegram")
    assert r.status_code == 200
    assert r.json() == {"telegram_id": None}


def test_profile_unlink_clears_id():
    db = FakePrefsDB(seed={"jason": {"telegram_id": "99999", "theme": "dark"}})
    client = _profile_client(db)
    r = client.put("/api/user/profile/telegram", json={"telegram_id": None})
    assert r.status_code == 200
    assert r.json() == {"telegram_id": None, "linked": False}
    # Other prefs are preserved.
    assert json.loads(db.store["jason"][0]) == {"theme": "dark"}


def test_profile_rejects_non_numeric_id():
    db = FakePrefsDB()
    client = _profile_client(db)
    r = client.put("/api/user/profile/telegram", json={"telegram_id": "not-a-number"})
    assert r.status_code == 400


def test_profile_guest_cannot_link():
    db = FakePrefsDB()
    client = _profile_client(db, role="guest", user_id="guest")
    r = client.put("/api/user/profile/telegram", json={"telegram_id": "99999"})
    assert r.status_code == 403


def test_profile_uniqueness_last_writer_wins():
    # jason already owns 99999; family-admin claims it → jason loses it.
    db = FakePrefsDB(seed={"jason": {"telegram_id": "99999"}})
    client = _profile_client(db, user_id="family-admin")
    r = client.put("/api/user/profile/telegram", json={"telegram_id": "99999"})
    assert r.status_code == 200
    assert r.json() == {"telegram_id": "99999", "linked": True}
    assert json.loads(db.store["family-admin"][0])["telegram_id"] == "99999"
    assert "telegram_id" not in json.loads(db.store["jason"][0])


def test_profile_set_preserves_other_prefs():
    db = FakePrefsDB(seed={"jason": {"openclaw_auto_update": "notify"}})
    client = _profile_client(db)
    client.put("/api/user/profile/telegram", json={"telegram_id": "12345"})
    stored = json.loads(db.store["jason"][0])
    assert stored == {"openclaw_auto_update": "notify", "telegram_id": "12345"}


# ─── 2. Internal resolver telegram_id → user_id ──────────────────────────────


def test_resolver_registered_returns_user_id(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    db = FakePrefsDB(seed={"jason": {"telegram_id": "99999"}})
    client = _system_client(db)
    # Loopback (TestClient) satisfies require_internal_token; token also works.
    r = client.get("/api/system/resolve-telegram/99999", headers={"X-Internal-Token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"user_id": "jason"}


def test_resolver_unregistered_returns_null(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    db = FakePrefsDB(seed={"jason": {"telegram_id": "99999"}})
    client = _system_client(db)
    r = client.get("/api/system/resolve-telegram/77777", headers={"X-Internal-Token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"user_id": None}


def test_resolver_requires_internal_token_when_not_loopback(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    db = FakePrefsDB(seed={"jason": {"telegram_id": "99999"}})
    app = FastAPI()
    app.include_router(system.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[system.get_db] = fake_get_db
    # Non-loopback client host, no token → 403.
    client = TestClient(app)
    r = client.get(
        "/api/system/resolve-telegram/99999",
        headers={"X-Forwarded-For": "8.8.8.8"},
    )
    # TestClient reports client.host as "testclient" (not loopback), so with a
    # token configured and none supplied this must be rejected.
    assert r.status_code == 403


# ─── 3. /api/chat trusted forwarded-user override security ────────────────────
#
# We exercise auth.resolve_acting_user directly through a tiny app so the test
# is independent of the (heavy) chat router import graph.


from fastapi import Depends as _Depends
from fastapi import Request as _Request

_acting_app = FastAPI()


@_acting_app.post("/echo-identity")
async def _echo_identity(request: _Request, user: dict = _Depends(auth.resolve_acting_user)):  # noqa: ARG001
    return {"user_id": user["user_id"], "role": user.get("role")}


def _acting_user_app() -> FastAPI:
    return _acting_app


def test_internal_caller_may_set_acting_user(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    # Valid X-Internal-Token → override honoured (loopback alone is NOT enough).
    client = TestClient(_acting_user_app())
    r = client.post(
        "/echo-identity",
        headers={"X-Zoe-User-Id": "jason", "X-Internal-Token": "tok"},
    )
    assert r.status_code == 200
    assert r.json() == {"user_id": "jason", "role": "user"}


def test_loopback_without_token_cannot_impersonate(monkeypatch):
    """CRITICAL (2026-07-04 review residual): bare loopback + X-Zoe-User-Id must
    NOT grant impersonation — a loopback SSRF or compromised local process would
    otherwise inherit acting-as-any-user. Token required, full stop."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    # TestClient is loopback-equivalent; force the internal check True to model
    # the strongest version of the old trust.
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: True)
    client = TestClient(_acting_user_app())
    r = client.post("/echo-identity", headers={"X-Zoe-User-Id": "jason"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "guest"


def test_override_disabled_when_token_unprovisioned(monkeypatch):
    """With ZOE_INTERNAL_TOKEN unset the override is disabled entirely — even a
    caller that SENDS some token header cannot impersonate (nothing to match)."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "")
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: True)
    client = TestClient(_acting_user_app())
    r = client.post(
        "/echo-identity",
        headers={"X-Zoe-User-Id": "jason", "X-Internal-Token": "anything"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == "guest"


def test_public_caller_cannot_impersonate(monkeypatch):
    """CRITICAL: a NON-internal caller that sets X-Zoe-User-Id must be ignored —
    the header must never grant impersonation. Falls through to guest."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    # Force _is_internal_request to be False regardless of loopback, simulating a
    # public request (wrong/absent token AND non-loopback host).
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: False)
    client = TestClient(_acting_user_app())
    r = client.post(
        "/echo-identity",
        headers={"X-Zoe-User-Id": "jason"},  # attacker-supplied, no valid internal auth
    )
    assert r.status_code == 200
    # Ignored → normal auth ran → guest identity, NOT "jason".
    body = r.json()
    assert body["user_id"] == "guest"
    assert body["user_id"] != "jason"


def test_internal_caller_without_header_uses_session_auth(monkeypatch):
    """An internal caller that does NOT send X-Zoe-User-Id still goes through
    normal session/guest auth — the override is opt-in, not automatic."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(auth, "_is_internal_request", lambda request: True)
    client = TestClient(_acting_user_app())
    r = client.post("/echo-identity")  # no X-Zoe-User-Id
    assert r.status_code == 200
    assert r.json()["user_id"] == "guest"


def test_is_internal_request_matches_token_boundary(monkeypatch):
    """_is_internal_request must accept a valid token and reject a bad one for a
    non-loopback host (the exact boundary the override relies on)."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")

    class _Req:
        def __init__(self, host, token):
            self.client = type("C", (), {"host": host})()
            self.headers = {"X-Internal-Token": token} if token else {}

    assert auth._is_internal_request(_Req("127.0.0.1", "")) is True  # loopback
    assert auth._is_internal_request(_Req("8.8.8.8", "tok")) is True  # valid token
    assert auth._is_internal_request(_Req("8.8.8.8", "wrong")) is False
    assert auth._is_internal_request(_Req("8.8.8.8", "")) is False
