"""Calendar WRITES: a personal event is its member's; a household event is a member's or THE PANEL's; nobody else's.

Security fix 2026-10-19. ``POST/PUT/DELETE /api/calendar/events`` declared only ``get_current_user`` (which resolves a
credential-less LAN caller as ``guest``) and relied on the role matrix alone: an admin-editable database row, and one that
counts the ``ZOE_UNAUTHENTICATED_ROLE=family-admin`` header-less promotion as an admin writer. They now declare
``panel_principal.require_member_or_panel`` and ``routers/calendar._calendar_writer`` applies the fridge rule the household
lists follow: the panel may add, edit and remove HOUSEHOLD (family) events, never a private one, and writes as the household.

Real HTTP, the REAL ``get_current_user``, the REAL device-token resolution (only zoe-auth's session validation is faked), the
REAL panel-registry SQL and the REAL calendar SQL over SQLite (0052 applied). All data is invented; addresses are TEST-NET.

Negative control (run by hand when written; 10 of the first 30 cases red): restore the pre-fix router (``Depends(get_current_user)`` + the
bare ``require_feature_access``) -> the header-less-promotion cases go red (a stranger writes as ``family-admin``) and every
panel case goes red (the guest matrix refuses the kiosk). The HA-open-rollback case is green on the old code too (the matrix
refuses a plain guest); it pins that the new ``require_member_or_panel`` floor does not open the calendar under that rollback.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import auth  # noqa: E402
import panel_principal  # noqa: E402
import routers.calendar as calendar_router  # noqa: E402
import routers.panel_auth as panel_auth  # noqa: E402
from database import get_db  # noqa: E402
from test_calendar_data_model import _base_engine, _run  # noqa: E402

pytestmark = pytest.mark.ci_safe

PANEL_IP = "192.0.2.50"
PHONE_IP = "192.0.2.77"
PROXY = "127.0.0.1"
MEMBER_SID = "sid-member"   # member-a
GUEST_SID = "sid-guest"
OUTAGE_SID = "sid-during-auth-outage"
PANEL_TOKEN = "tok-hall-panel"
CF = {"cf-connecting-ip": PANEL_IP, "cf-ray": "abc123"}
A, B = "member-a", "member-b"


class _Cur:
    def __init__(self, cur):
        self._rows = cur.fetchall() if cur.description else []
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class Db:
    """The AsyncpgCompat surface the router + ``panel_principal`` use, over synchronous SQLite (loop-agnostic)."""

    def __init__(self, path):
        engine = _base_engine(path)
        _run(engine, "upgrade")
        engine.dispose()
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.create_function("NOW", 0, lambda: "2026-10-19T00:00:00")
        self.con.executescript(
            """
            INSERT INTO users (id, name) VALUES ('family-admin', 'Household');
            CREATE TABLE panels (panel_id TEXT PRIMARY KEY, ip_address TEXT, is_active INT DEFAULT 1, allow_guest INT DEFAULT 1);
            INSERT INTO panels (panel_id, ip_address) VALUES ('hall-panel', '192.0.2.50');
            INSERT INTO events (id, user_id, title, start_date, visibility) VALUES
                ('ev-family', 'member-a', 'Bin night', '2026-10-20', 'family'),
                ('ev-a-private', 'member-a', 'Dentist', '2026-10-21', 'personal'),
                ('ev-b-private', 'member-b', 'Surprise party plan', '2026-10-22', 'personal'),
                ('ev-b-busy', 'member-b', 'Appointment', '2026-10-23', 'busy');
            """
        )
        self.con.commit()

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        self.con.commit()

    def row(self, event_id):
        r = self.con.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(r) if r else None

    def snapshot(self):
        return [tuple(r) for r in self.con.execute("SELECT id, user_id, title, visibility, deleted FROM events ORDER BY id")]


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = Db(tmp_path / "cal.db")

    async def _with_registry(_db, check, value):  # the real registry SQL, on this database
        return await check(d, value)

    async def _no_push(*args, **kwargs):
        return 0

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)
    monkeypatch.setattr(calendar_router.broadcaster, "broadcast", _no_push)
    return d


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "ZOE_TRUSTED_PROXY_NETS",
                "ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_DEVICE_TOKEN_BINDS_MEMBER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()

    async def _validate(session_id):
        if session_id == MEMBER_SID:
            return {"user_id": A, "role": "user", "username": A, "permissions": []}
        if session_id == GUEST_SID:
            return {"user_id": "guest", "role": "guest", "username": "guest", "permissions": []}
        if session_id == OUTAGE_SID:  # zoe-auth unreachable, fail-open configured: the degraded guest
            return {auth._DEGRADED_MARK: True, "user_id": "guest", "role": "guest", "username": "guest", "permissions": []}
        return None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    monkeypatch.setitem(panel_auth._token_cache, panel_auth._hash_token(PANEL_TOKEN),
                        {"panel_id": "hall-panel", "role": "voice-daemon", "expires_at": None, "revoked": 0})
    yield
    auth._session_cache.clear()


def _send(db, peer, headers, method, path, body=None):
    app = FastAPI()
    app.include_router(calendar_router.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, client=(peer, 50000)).request(method, path, json=body, headers=headers)


NEW_FAMILY = {"title": "School concert", "start_date": "2026-10-30", "visibility": "family"}
WRITES = [
    ("POST", "/api/calendar/events", NEW_FAMILY),
    ("PUT", "/api/calendar/events/ev-family", {"title": "changed"}),
    ("DELETE", "/api/calendar/events/ev-family", None),
]

STRANGERS = [
    (PHONE_IP, {}),                                                  # anonymous on the LAN
    (PHONE_IP, {"X-Real-IP": PANEL_IP}),                             # spoofed X-Real-IP from an untrusted peer
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP}),     # a minted guest session is not the panel
    (PROXY, {"X-Real-IP": PANEL_IP, **CF}),                          # the panel's address, but over Cloudflare
    (PHONE_IP, {"X-Device-Token": "not-a-real-token"}),              # unknown device token -> guest
]
PANEL = [
    (PROXY, {"X-Real-IP": PANEL_IP}),                                # the kiosk browser through nginx
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PANEL_IP}),     # the kiosk with its guest session
    (PHONE_IP, {"X-Device-Token": PANEL_TOKEN}),                     # the panel's device token
]
MEMBER = [(PHONE_IP, {"X-Session-ID": MEMBER_SID}), (PROXY, {"X-Session-ID": MEMBER_SID, **CF})]


@pytest.mark.parametrize("method,path,body", WRITES)
@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_write_nothing(db, method, path, body, peer, headers):
    before = db.snapshot()
    r = _send(db, peer, headers, method, path, body)
    assert r.status_code == 403, (method, path, headers, r.text)
    assert db.snapshot() == before


@pytest.mark.parametrize("method,path,body", WRITES)
def test_an_auth_outage_principal_is_not_a_writer_even_at_the_panel(db, monkeypatch, method, path, body):
    """An outage resolves ANY header to a guest: never evidence of the panel (or of anyone)."""
    monkeypatch.setattr(auth, "_AUTH_FAIL_CLOSED", False)
    before = db.snapshot()
    r = _send(db, PROXY, {"X-Session-ID": OUTAGE_SID, "X-Real-IP": PANEL_IP}, method, path, body)
    assert r.status_code == 403, r.text
    assert db.snapshot() == before


@pytest.mark.parametrize("method,path,body", WRITES)
def test_the_header_less_admin_promotion_is_not_a_writer(db, monkeypatch, method, path, body):
    """``ZOE_UNAUTHENTICATED_ROLE=family-admin`` made every header-less LAN caller an ADMIN, which the matrix let write."""
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")
    before = db.snapshot()
    assert _send(db, PHONE_IP, {}, method, path, body).status_code == 403
    assert db.snapshot() == before


@pytest.mark.parametrize("method,path,body", WRITES)
def test_the_home_control_open_rollback_does_not_open_the_calendar(db, monkeypatch, method, path, body):
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    before = db.snapshot()
    assert _send(db, PHONE_IP, {}, method, path, body).status_code == 403
    assert db.snapshot() == before


@pytest.mark.parametrize("peer,headers", PANEL)
def test_the_panel_works_household_events(db, peer, headers):
    r = _send(db, peer, headers, "POST", "/api/calendar/events", NEW_FAMILY)
    assert r.status_code == 200, r.text
    created = db.row(r.json()["id"])
    assert (created["user_id"], created["visibility"]) == ("family-admin", "family")  # the household, never a person

    r = _send(db, peer, headers, "PUT", "/api/calendar/events/ev-family", {"title": "Bin night (green)"})
    assert r.status_code == 200, r.text
    assert db.row("ev-family")["title"] == "Bin night (green)"

    r = _send(db, peer, headers, "PUT", "/api/calendar/events/ev-family", {"visibility": "personal"})
    assert r.status_code == 200 and db.row("ev-family")["visibility"] == "family"  # never hides a household event

    assert _send(db, peer, headers, "DELETE", "/api/calendar/events/ev-family").status_code == 200
    assert db.row("ev-family")["deleted"] == 1


def test_a_panel_edit_is_pushed_to_the_events_owner(db, monkeypatch):
    sent = []

    async def _capture(channel, kind, data, user_id=None):
        sent.append((kind, user_id))
        return 1

    monkeypatch.setattr(calendar_router.broadcaster, "broadcast", _capture)
    panel = {"X-Real-IP": PANEL_IP}
    assert _send(db, PROXY, panel, "PUT", "/api/calendar/events/ev-family", {"title": "Bins"}).status_code == 200
    assert _send(db, PROXY, panel, "DELETE", "/api/calendar/events/ev-family").status_code == 200
    assert sent == [("event_updated", A), ("event_deleted", A)]  # never the HOUSEHOLD_VIEWER sentinel


@pytest.mark.parametrize("peer,headers", PANEL)
def test_the_panel_never_touches_a_private_event(db, peer, headers):
    before = db.snapshot()
    for vis in ("personal", "busy", "private"):
        r = _send(db, peer, headers, "POST", "/api/calendar/events", {**NEW_FAMILY, "visibility": vis})
        assert r.status_code == 403, (vis, r.text)
    for ev in ("ev-a-private", "ev-b-private", "ev-b-busy"):
        assert _send(db, peer, headers, "PUT", f"/api/calendar/events/{ev}", {"title": "x"}).status_code == 404
        assert _send(db, peer, headers, "DELETE", f"/api/calendar/events/{ev}").status_code == 404
    assert db.snapshot() == before


def test_a_header_less_caller_at_the_panel_is_the_panel_not_an_admin(db, monkeypatch):
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")
    r = _send(db, PROXY, {"X-Real-IP": PANEL_IP}, "POST", "/api/calendar/events", {**NEW_FAMILY, "visibility": "personal"})
    assert r.status_code == 403  # judged as the panel: household events only
    r = _send(db, PROXY, {"X-Real-IP": PANEL_IP}, "POST", "/api/calendar/events", NEW_FAMILY)
    assert r.status_code == 200 and db.row(r.json()["id"])["user_id"] == "family-admin"


@pytest.mark.parametrize("peer,headers", MEMBER)
def test_a_member_writes_their_own_and_household_events_only(db, peer, headers):
    r = _send(db, peer, headers, "POST", "/api/calendar/events", {**NEW_FAMILY, "visibility": "personal"})
    assert r.status_code == 200 and db.row(r.json()["id"])["user_id"] == A
    assert _send(db, peer, headers, "PUT", "/api/calendar/events/ev-a-private", {"title": "Dentist 3pm"}).status_code == 200
    assert _send(db, peer, headers, "PUT", "/api/calendar/events/ev-family", {"title": "Bins"}).status_code == 200
    # another member's private event does not exist for A
    assert _send(db, peer, headers, "PUT", "/api/calendar/events/ev-b-private", {"title": "x"}).status_code == 404
    assert _send(db, peer, headers, "DELETE", "/api/calendar/events/ev-b-private").status_code == 404
    assert db.row("ev-b-private")["title"] == "Surprise party plan"
