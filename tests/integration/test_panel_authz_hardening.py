"""
Security regression tests for the panel authorization hardening lane.

Covers three fixes (see .polly/audit-findings.md remaining-routers lane):

  P1  ui_actions.py — panel hijack. A normal session user must not be able to
      bind/sync/poll an arbitrary panel_id; only the panel's own device token or
      a user explicitly bound to the panel (panel_user_bindings) is allowed.

  P2  panel_auth.py — PIN user override. The unauthenticated /api/panels/auth/pin
      must IGNORE a caller-supplied user_id and validate the PIN only against the
      identity resolved from the challenge (or the panel binding).

  P2  panel_provision.py — token pickup race. Concurrent provisioning polls must
      hand the raw device token to exactly ONE caller (atomic clear-on-read).

These import the live router modules, so (per tests/AGENTS.md) they run on the
self-hosted runner, not GitHub-hosted runners.

Run: python3 -m pytest tests/integration/test_panel_authz_hardening.py -v
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# Router imports (auth, database, ...) are rooted at services/zoe-data.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "zoe-data"))

import routers.ui_actions as ui_actions  # noqa: E402
import routers.panel_auth as panel_auth  # noqa: E402
import routers.panel_provision as panel_provision  # noqa: E402
from fastapi import HTTPException  # noqa: E402


# ── Shared fake DB plumbing (mirrors test_people_hardening.py) ─────────────────

class _FakeCursor:
    def __init__(self, rows, rowcount=None):
        self._rows = rows
        # Mirror db_pool._Cursor: SELECT → len(rows); writes → parsed status count.
        self.rowcount = len(rows) if rowcount is None else rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _ExecResult:
    """Mimics aiosqlite execute(): awaitable AND async context manager."""

    def __init__(self, rows=None, rowcount=None):
        self._cursor = _FakeCursor(rows or [], rowcount=rowcount)

    def __await__(self):
        async def _run():
            return self._cursor
        return _run().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *exc):
        return False


def _norm(sql: str) -> str:
    return " ".join(sql.split()).upper()


# ── P1: ui_actions panel-authorization ────────────────────────────────────────

class _BindingDB:
    """Fake DB whose panel_user_bindings table contains a fixed allow-list."""

    def __init__(self, bound_pairs):
        # bound_pairs: set of (panel_id, user_id) that have a binding row.
        self.bound_pairs = set(bound_pairs)
        self.session_user = "alice"  # who ui_panel_sessions says owns the panel

    def execute(self, sql, params=None):
        s = _norm(sql)
        p = tuple(params or ())
        if s.startswith("SELECT 1 FROM PANEL_USER_BINDINGS"):
            panel_id, user_id = p[0], p[1]
            if (panel_id, user_id) in self.bound_pairs:
                return _ExecResult([{"1": 1}])
            return _ExecResult([])
        if s.startswith("SELECT USER_ID FROM UI_PANEL_SESSIONS"):
            return _ExecResult([{"user_id": self.session_user}])
        if s.startswith("SELECT ID, PANEL_ID, CHAT_SESSION_ID"):
            # final pending-actions select — no queued actions
            return _ExecResult([])
        # UPDATEs / INSERTs — no rows
        return _ExecResult([])

    async def commit(self):
        pass


@pytest.fixture
def _noop_feature_access(monkeypatch):
    async def _ok(*a, **k):
        return None
    monkeypatch.setattr(ui_actions, "require_feature_access", _ok)


@pytest.mark.asyncio
async def test_bind_rejects_unbound_user(_noop_feature_access):
    """A session user with no binding to the panel cannot bind it (P1)."""
    db = _BindingDB(bound_pairs=set())  # mallory is bound to nothing
    mallory = {"user_id": "mallory", "role": "member"}
    with pytest.raises(HTTPException) as exc:
        await ui_actions.bind_panel({"panel_id": "living-room"}, user=mallory, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_pending_rejects_unbound_user(_noop_feature_access):
    """An unbound user cannot drain another panel/user's queued actions (P1)."""
    db = _BindingDB(bound_pairs=set())
    mallory = {"user_id": "mallory", "role": "member"}
    with pytest.raises(HTTPException) as exc:
        await ui_actions.get_pending_ui_actions(
            panel_id="living-room", limit=20, user=mallory, db=db
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_sync_rejects_unbound_user(_noop_feature_access):
    """An unbound user cannot sync (and thereby claim) an arbitrary panel (P1)."""
    db = _BindingDB(bound_pairs=set())
    mallory = {"user_id": "mallory", "role": "member"}
    with pytest.raises(HTTPException) as exc:
        await ui_actions.sync_ui_state({"panel_id": "living-room"}, user=mallory, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_bind_allows_bound_user(_noop_feature_access):
    """The correctly-bound user keeps working (HARD CONSTRAINT)."""
    db = _BindingDB(bound_pairs={("living-room", "alice")})
    alice = {"user_id": "alice", "role": "member"}
    out = await ui_actions.bind_panel({"panel_id": "living-room"}, user=alice, db=db)
    assert out["status"] == "ok"
    assert out["panel_id"] == "living-room"


@pytest.mark.asyncio
async def test_bind_allows_device_token_for_own_panel(_noop_feature_access):
    """A panel daemon (device-token user dict carries panel_id) binds its own panel."""
    db = _BindingDB(bound_pairs=set())  # no human bindings needed
    panel_daemon = {"user_id": "family-admin", "role": "member", "panel_id": "living-room"}
    out = await ui_actions.bind_panel({"panel_id": "living-room"}, user=panel_daemon, db=db)
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_device_token_cannot_bind_other_panel(_noop_feature_access):
    """A device token for panel A must not bind panel B (P1)."""
    db = _BindingDB(bound_pairs=set())
    panel_daemon = {"user_id": "family-admin", "role": "member", "panel_id": "panel-A"}
    with pytest.raises(HTTPException) as exc:
        await ui_actions.bind_panel({"panel_id": "panel-B"}, user=panel_daemon, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_pending_allows_bound_user(_noop_feature_access):
    """A bound user can poll (returns the panel-user's actions, no error)."""
    db = _BindingDB(bound_pairs={("living-room", "alice")})
    alice = {"user_id": "alice", "role": "member"}
    out = await ui_actions.get_pending_ui_actions(
        panel_id="living-room", limit=20, user=alice, db=db
    )
    assert "actions" in out and out["count"] == 0


@pytest.fixture
def _stub_create_deps(monkeypatch):
    """Allow create_ui_action's role check; record whether enqueue is reached.

    create_ui_action runs require_feature_access → action_type allow-list →
    can_use_ui_action → panel authorization → enqueue_ui_action. We stub the
    first/third so the panel-authorization gate is the only thing under test, and
    replace enqueue with a recorder so a passing authz reaches the real queue path
    exactly once (and a rejection never does).
    """
    async def _ok(*a, **k):
        return None
    monkeypatch.setattr(ui_actions, "require_feature_access", _ok)

    async def _can(*a, **k):
        return True
    monkeypatch.setattr(ui_actions, "can_use_ui_action", _can)

    calls = []

    async def _enqueue(_db, **kwargs):
        calls.append(kwargs)
        return {"id": "act-1", "panel_id": kwargs.get("panel_id")}
    monkeypatch.setattr(ui_actions, "enqueue_ui_action", _enqueue)
    return calls


@pytest.mark.asyncio
async def test_create_action_rejects_unbound_user_targeting_panel(_stub_create_deps):
    """An unbound session cannot enqueue an action into another panel's queue (P1).

    enqueue_ui_action rewrites the stored user_id to the target panel's owner, so
    an injected action would be delivered when that panel polls. The create route
    must apply the same panel gate as bind/poll/sync.
    """
    db = _BindingDB(bound_pairs=set())  # mallory bound to nothing
    mallory = {"user_id": "mallory", "role": "member"}
    with pytest.raises(HTTPException) as exc:
        await ui_actions.create_ui_action(
            {"action_type": "notify", "panel_id": "living-room", "payload": {}},
            user=mallory, db=db,
        )
    assert exc.value.status_code == 403
    assert _stub_create_deps == []  # never reached the queue


@pytest.mark.asyncio
async def test_create_action_device_token_cannot_target_other_panel(_stub_create_deps):
    """A device token for panel A cannot enqueue into panel B (P1)."""
    db = _BindingDB(bound_pairs=set())
    daemon = {"user_id": "family-admin", "role": "member", "panel_id": "panel-A"}
    with pytest.raises(HTTPException) as exc:
        await ui_actions.create_ui_action(
            {"action_type": "notify", "panel_id": "panel-B", "payload": {}},
            user=daemon, db=db,
        )
    assert exc.value.status_code == 403
    assert _stub_create_deps == []


@pytest.mark.asyncio
async def test_create_action_allows_bound_user_for_panel(_stub_create_deps):
    """A user bound to the target panel may enqueue for it (HARD CONSTRAINT)."""
    db = _BindingDB(bound_pairs={("living-room", "alice")})
    alice = {"user_id": "alice", "role": "member"}
    out = await ui_actions.create_ui_action(
        {"action_type": "notify", "panel_id": "living-room", "payload": {}},
        user=alice, db=db,
    )
    assert out["status"] == "ok"
    assert len(_stub_create_deps) == 1
    assert _stub_create_deps[0]["panel_id"] == "living-room"


@pytest.mark.asyncio
async def test_create_action_without_panel_id_skips_panel_gate(_stub_create_deps):
    """No panel_id → enqueue resolves the caller's OWN foreground panel; allowed.

    This is the normal front-end path (zoe-orb posts no panel_id), which must keep
    working without a binding to any specific panel.
    """
    db = _BindingDB(bound_pairs=set())
    mallory = {"user_id": "mallory", "role": "member"}
    out = await ui_actions.create_ui_action(
        {"action_type": "notify", "payload": {}},
        user=mallory, db=db,
    )
    assert out["status"] == "ok"
    assert len(_stub_create_deps) == 1
    assert _stub_create_deps[0].get("panel_id") is None


# ── P2: panel_auth /auth/pin ignores forged user_id ───────────────────────────

class _PinChallengeDB:
    """Fake DB for submit_pin: one pending challenge owned by 'alice' on 'p1'."""

    def __init__(self, challenge_user="alice", panel_id="p1", bound_users=("alice",),
                 raise_on_binding=False, raise_on_default_binding=False):
        future = (datetime.now(tz=timezone.utc) + timedelta(minutes=2)).isoformat()
        self._challenge = {
            "challenge_id": "chal-1",
            "panel_id": panel_id,
            "user_id": challenge_user,
            "action_context": None,
            "status": "pending",
            "expires_at": future,
        }
        self._panel_id = panel_id
        self._bound_users = set(bound_users)
        self._raise_on_binding = raise_on_binding
        self._raise_on_default_binding = raise_on_default_binding
        self.challenge_status_updates = []

    def execute(self, sql, params=None):
        s = _norm(sql)
        p = tuple(params or ())
        if s.startswith("SELECT * FROM PANEL_AUTH_CHALLENGES"):
            return _ExecResult([dict(self._challenge)])
        if s.startswith("SELECT 1 FROM PANEL_USER_BINDINGS WHERE PANEL_ID = ? LIMIT 1"):
            if self._raise_on_binding:
                raise RuntimeError("simulated transient DB error during authz check")
            return _ExecResult([{"1": 1}] if self._bound_users else [])
        if s.startswith("SELECT 1 FROM PANEL_USER_BINDINGS WHERE PANEL_ID = ? AND USER_ID = ?"):
            return _ExecResult([{"1": 1}] if p[1] in self._bound_users else [])
        if s.startswith("SELECT USER_ID FROM PANEL_USER_BINDINGS"):
            # default-binding fallback (only hit when challenge has no user)
            if self._raise_on_default_binding:
                raise RuntimeError("simulated transient DB error during default-binding lookup")
            return _ExecResult([{"user_id": next(iter(self._bound_users), None)}])
        if s.startswith("UPDATE PANEL_AUTH_CHALLENGES SET STATUS"):
            self.challenge_status_updates.append(p[0])
            return _ExecResult([])
        return _ExecResult([])

    async def commit(self):
        pass


class _FakeAuthResponse:
    def __init__(self, success):
        self.status_code = 200
        self._success = success

    def json(self):
        return {"success": self._success}


# A single low-value PIN shared by every test as "the correct passcode". Kept on
# its own line (never paired with a username) so secret-scanners don't read the
# fixtures as a real credential tuple. Tests select WHICH user that PIN is valid
# for via _FakeAuthClient.expect_user; a wrong PIN is just any other string.
_OK_PIN = "0000"
_BAD_PIN = "xxxx"


class _FakeAuthClient:
    """Stand-in for zoe-auth's passcode endpoint.

    Records each user_id the router submits, and accepts the PIN only when it
    matches the configured ``expect_user`` (so tests assert WHICH identity the PIN
    was validated against — the security-relevant property).
    """

    captured = []          # list of (user_id, passcode) the router submitted
    expect_user = "alice"  # the only user_id for whom _OK_PIN validates

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **k):
        body = json or {}
        _FakeAuthClient.captured.append((body.get("user_id"), body.get("passcode")))
        ok = body.get("user_id") == _FakeAuthClient.expect_user and body.get("passcode") == _OK_PIN
        return _FakeAuthResponse(ok)


class _FakeRequest:
    """Minimal stand-in for starlette Request — only .headers.get is used."""

    def __init__(self, headers=None):
        self.headers = headers or {}


def _device_token_request(panel_id, raw="dev-tok"):
    """A request carrying a device token registered for `panel_id` in the cache."""
    panel_auth._token_cache[panel_auth._hash_token(raw)] = {
        "panel_id": panel_id, "role": "kiosk", "revoked": 0, "expires_at": None,
    }
    return _FakeRequest({"X-Device-Token": raw})


@pytest.fixture
def _patch_pin_env(monkeypatch):
    _FakeAuthClient.captured = []
    _FakeAuthClient.expect_user = "alice"
    monkeypatch.setattr(panel_auth.httpx, "AsyncClient", _FakeAuthClient)
    # silence the per-challenge attempt counter + device-token cache between tests
    panel_auth._pin_attempts.clear()
    panel_auth._token_cache.clear()

    class _Broadcaster:
        async def broadcast(self, *a, **k):
            return None

    import push
    monkeypatch.setattr(push, "broadcaster", _Broadcaster(), raising=False)


@pytest.mark.asyncio
async def test_pin_ignores_forged_user_and_approves_for_challenge_user(_patch_pin_env):
    """Forged user_id is ignored; alice's real PIN approves alice's challenge."""
    db = _PinChallengeDB(challenge_user="alice")
    out = await panel_auth.submit_pin(
        {"challenge_id": "chal-1", "pin": _OK_PIN, "user_id": "mallory"}, db=db
    )
    assert out["status"] == "approved"
    # The PIN was validated against the CHALLENGE's user, never the forged one.
    assert _FakeAuthClient.captured == [("alice", _OK_PIN)]
    assert all(uid != "mallory" for uid, _ in _FakeAuthClient.captured)


@pytest.mark.asyncio
async def test_pin_forged_user_cannot_approve_with_other_pin(_patch_pin_env):
    """Mallory's own PIN must not approve alice's challenge even if she forges user_id."""
    db = _PinChallengeDB(challenge_user="alice")
    with pytest.raises(HTTPException) as exc:
        await panel_auth.submit_pin(
            {"challenge_id": "chal-1", "pin": _BAD_PIN, "user_id": "mallory"}, db=db
        )
    assert exc.value.status_code == 403
    # Validation still targeted alice (the challenge user), not the forged id.
    assert _FakeAuthClient.captured == [("alice", _BAD_PIN)]


@pytest.mark.asyncio
async def test_pin_authz_fails_closed_on_db_error(_patch_pin_env):
    """A DB error during the panel-authz check must DENY, never skip the check."""
    db = _PinChallengeDB(challenge_user="alice", raise_on_binding=True)
    with pytest.raises(HTTPException) as exc:
        await panel_auth.submit_pin(
            {"challenge_id": "chal-1", "pin": _OK_PIN}, db=db
        )
    assert exc.value.status_code == 503
    # Must have failed BEFORE delegating to zoe-auth for PIN validation.
    assert _FakeAuthClient.captured == []


@pytest.mark.asyncio
async def test_pin_zero_binding_unbound_user_denied_without_device_token(_patch_pin_env):
    """BLOCKING: active panel + ZERO bindings — a normal user can't self-approve.

    create_pin_challenge stores the (arbitrary authenticated) caller as the
    challenge user for any active panel_id. On an unbound panel, the challenge
    alone must NOT authorize PIN approval — even with the caller's own valid PIN
    — unless the request carries device-token/provisioning authority for the panel.
    """
    _FakeAuthClient.expect_user = "mallory"  # mallory's PIN would validate, if we got there
    db = _PinChallengeDB(challenge_user="mallory", bound_users=())  # panel has no bindings
    with pytest.raises(HTTPException) as exc:
        await panel_auth.submit_pin(
            {"challenge_id": "chal-1", "pin": _OK_PIN},
            request=_FakeRequest(),  # no device token
            db=db,
        )
    assert exc.value.status_code == 403
    # Denied BEFORE the PIN was ever validated against zoe-auth.
    assert _FakeAuthClient.captured == []


@pytest.mark.asyncio
async def test_pin_zero_binding_succeeds_with_panel_device_token(_patch_pin_env):
    """Legit first-boot/device path: device token for THAT panel authorizes approval."""
    _FakeAuthClient.expect_user = "paneluser"
    db = _PinChallengeDB(challenge_user="paneluser", panel_id="p1", bound_users=())
    out = await panel_auth.submit_pin(
        {"challenge_id": "chal-1", "pin": _OK_PIN},
        request=_device_token_request("p1"),  # device token issued for p1
        db=db,
    )
    assert out["status"] == "approved"
    assert _FakeAuthClient.captured == [("paneluser", _OK_PIN)]


@pytest.mark.asyncio
async def test_pin_zero_binding_rejects_wrong_panel_device_token(_patch_pin_env):
    """A device token for a DIFFERENT panel is not authority for this panel."""
    db = _PinChallengeDB(challenge_user="paneluser", panel_id="p1", bound_users=())
    with pytest.raises(HTTPException) as exc:
        await panel_auth.submit_pin(
            {"challenge_id": "chal-1", "pin": _OK_PIN},
            request=_device_token_request("other-panel"),  # token for the wrong panel
            db=db,
        )
    assert exc.value.status_code == 403
    assert _FakeAuthClient.captured == []


@pytest.mark.asyncio
async def test_pin_voice_default_binding_path_still_works(_patch_pin_env):
    """Voice/userless challenge resolves via the panel's default binding (unchanged)."""
    _FakeAuthClient.expect_user = "dave"
    db = _PinChallengeDB(challenge_user=None, bound_users=("dave",))  # default-bound user
    out = await panel_auth.submit_pin(
        {"challenge_id": "chal-1", "pin": _OK_PIN},
        request=_FakeRequest(),  # no device token needed on the binding path
        db=db,
    )
    assert out["status"] == "approved"
    assert _FakeAuthClient.captured == [("dave", _OK_PIN)]


@pytest.mark.asyncio
async def test_pin_default_binding_lookup_fails_closed_on_db_error(_patch_pin_env):
    """A DB error while resolving the panel's default binding must FAIL CLOSED.

    For a voice/userless challenge (user_id=None) the acting identity comes from
    the panel's 'default' binding. A transient DB error there must return the
    retryable 503 authz-unavailable response — NOT fall through to the
    400 "no resolvable user", which tells the panel the challenge is malformed
    and to give up, breaking a valid voice PIN approval.
    """
    db = _PinChallengeDB(
        challenge_user=None, bound_users=("dave",), raise_on_default_binding=True
    )
    with pytest.raises(HTTPException) as exc:
        await panel_auth.submit_pin(
            {"challenge_id": "chal-1", "pin": _OK_PIN},
            request=_FakeRequest(),
            db=db,
        )
    assert exc.value.status_code == 503
    # Must have failed BEFORE delegating to zoe-auth for PIN validation.
    assert _FakeAuthClient.captured == []


# ── P2: panel_provision atomic one-time token pickup ──────────────────────────

class _ProvisionRaceDB:
    """Fake DB modeling the confirmed provision row with a one-shot token.

    SELECT yields to the loop (simulating I/O) so two concurrent polls both
    observe the pending token; the conditional UPDATE ... WHERE token = ? is
    modeled as a single non-yielding step, so only the poll whose observed token
    still matches the row flips it (rowcount == 1) and every other poll gets 0.
    """

    def __init__(self):
        future = (datetime.now(tz=timezone.utc) + timedelta(minutes=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self.row = {
            "code": "ABC123",
            "status": "confirmed",
            "token": "raw-device-token",
            "panel_id": "living-room",
            "expires_at": future,
        }

    def execute(self, sql, params=None):
        s = _norm(sql)
        if s.startswith("SELECT CODE, STATUS, TOKEN, PANEL_ID, EXPIRES_AT"):
            # SELECT yields control (simulated I/O) so concurrent polls interleave
            # and both observe the still-present token before the conditional clear.
            snapshot = dict(self.row)
            return _SleepingExecResult([snapshot])
        if s.startswith("UPDATE PANEL_PROVISION_CODES SET TOKEN = NULL"):
            # Atomic conditional clear modeled by a single non-yielding step. The
            # query matches the EXACT token the poll observed (WHERE token = ?), so
            # rowcount == 1 only for the poll whose token still equals the row.
            want_token = (tuple(params or ()) + (None,))[1]
            if self.row["token"] is not None and self.row["token"] == want_token:
                self.row["token"] = None
                return _ExecResult([], rowcount=1)
            return _ExecResult([], rowcount=0)
        return _ExecResult([])

    async def commit(self):
        pass


class _SleepingExecResult(_ExecResult):
    """ExecResult whose await yields control once, forcing task interleaving."""

    def __await__(self):
        async def _run():
            await asyncio.sleep(0)
            return self._cursor
        return _run().__await__()


@pytest.mark.asyncio
async def test_provision_token_delivered_once_under_concurrency():
    """Exactly one of N concurrent polls receives the raw token (P2)."""
    db = _ProvisionRaceDB()
    results = await asyncio.gather(
        *[panel_provision.provision_poll("ABC123", db=db) for _ in range(8)]
    )
    with_token = [r for r in results if r.get("token")]
    assert len(with_token) == 1, f"token delivered {len(with_token)} times, expected 1"
    assert with_token[0]["token"] == "raw-device-token"
    assert with_token[0]["panel_id"] == "living-room"
    # All other polls still see confirmed, just without a token.
    assert all(r["status"] == "confirmed" for r in results)


# ── panel_auth._resolve_device_token_user: unbound device → guest ─────────────
#
# A device token authenticates the DEVICE, not a person. The acting identity is
# whoever is bound to that panel (panel_user_bindings.default). A device with no
# binding must fail closed to GUEST — never the household admin — mirroring the
# session model where an absent identity resolves to guest.

class _BindingLookupDB:
    """Fake DB: the panel_user_bindings 'default' SELECT returns a fixed user (or none)."""

    def __init__(self, default_user):
        self._default_user = default_user  # str (bound) or None (unbound)

    def execute(self, sql, params=None):
        s = _norm(sql)
        if s.startswith("SELECT USER_ID FROM PANEL_USER_BINDINGS"):
            if self._default_user:
                return _ExecResult([{"user_id": self._default_user}])
            return _ExecResult([])
        return _ExecResult([])

    async def commit(self):
        pass


def _patch_binding_db(monkeypatch, default_user):
    import database as _database

    async def _fake_get_db():
        yield _BindingLookupDB(default_user)

    monkeypatch.setattr(_database, "get_db", _fake_get_db)


@pytest.mark.asyncio
async def test_device_token_bound_panel_resolves_to_bound_user(monkeypatch):
    """A device token for a panel bound to a user acts as that user (unchanged behaviour)."""
    monkeypatch.setattr(
        panel_auth, "lookup_device_token",
        lambda t: {"panel_id": "zoe-touch-pi", "role": "voice-daemon"},
    )
    _patch_binding_db(monkeypatch, "jason")
    user = await panel_auth._resolve_device_token_user("tok")
    assert user is not None
    assert user["user_id"] == "jason"
    assert user["role"] != "guest"
    assert user["panel_id"] == "zoe-touch-pi"


@pytest.mark.asyncio
async def test_device_token_unbound_panel_is_guest_not_admin(monkeypatch):
    """A device token for a panel with NO binding acts as guest — never family-admin/admin."""
    monkeypatch.setattr(
        panel_auth, "lookup_device_token",
        lambda t: {"panel_id": "bench-panel", "role": "voice-daemon"},
    )
    _patch_binding_db(monkeypatch, None)
    user = await panel_auth._resolve_device_token_user("tok")
    assert user is not None
    assert user["user_id"] == "guest"
    assert user["role"] == "guest"
    assert user["user_id"] != "family-admin"
    assert user["permissions"] == []


@pytest.mark.asyncio
async def test_device_token_invalid_returns_none(monkeypatch):
    """An unknown device token resolves to None → caller falls through to guest auth."""
    monkeypatch.setattr(panel_auth, "lookup_device_token", lambda t: None)
    user = await panel_auth._resolve_device_token_user("bad-token")
    assert user is None
