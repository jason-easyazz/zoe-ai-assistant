"""House state and its re-targeting writes need a MEMBER or THE PANEL (security follow-up 2026-10-18, after #2089).

#2089 gated ``/api/ha/*`` (member or panel) but left routes that serve the SAME state, or change what the panel controls, to
any device on the home LAN (``get_current_user`` resolves a credential-less request as ``guest``):

  * ``GET /api/panels/{id}/sleep-gate`` told any caller whether a room's light is on right now (presence);
  * ``GET /api/panels/{id}/config`` and ``GET /api/rooms`` / ``/api/rooms/unassigned`` carry live HA state per device;
  * ``PUT /api/panels/{id}/config`` and every ``/api/rooms`` write let an anonymous caller re-point the dock pins and the
    room "the light in here" means.

Reads now take ``panel_principal.require_member_or_panel`` (the home-control rule, so the kiosk keeps its sleep-gate, dock
config and Rooms surface); writes take ``panel_principal.require_member``. Real HTTP, the REAL ``get_current_user`` and the REAL
device-token resolution (only zoe-auth's session validation is faked) and the REAL panel-registry SQL over SQLite. All data is
invented; addresses are TEST-NET.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import panel_principal
import routers.panel_auth as panel_auth
import routers.panel_config as pc
import routers.rooms as rooms
from database import get_db

pytestmark = pytest.mark.ci_safe

PANEL_IP = "192.0.2.50"     # the registered kiosk
PHONE_IP = "192.0.2.77"     # a visitor's phone / an IoT gadget on the same Wi-Fi
PROXY = "127.0.0.1"         # nginx: the TCP peer for every browser request that came through it
MEMBER_SID = "sid-member"
GUEST_SID = "sid-guest"     # what POST /api/auth/guest mints for ANY caller
PANEL_TOKEN = "tok-kitchen-panel"
LIGHT = "switch.kitchen_light"
CF = {"cf-connecting-ip": PANEL_IP, "cf-ray": "abc123"}


class _Cur:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()


class Db:
    """The AsyncpgCompat surface the routers + ``panel_principal`` use, over synchronous SQLite (loop-agnostic)."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE panels (panel_id TEXT PRIMARY KEY, name TEXT, location TEXT, default_player TEXT, pinned TEXT,
                                 room_id TEXT, ip_address TEXT, is_active INT DEFAULT 1, allow_guest INT DEFAULT 1,
                                 updated_at TIMESTAMP);
            CREATE TABLE rooms (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, ha_area_id TEXT,
                                sort INTEGER DEFAULT 0, created_at TIMESTAMP, updated_at TIMESTAMP);
            CREATE TABLE room_devices (room_id TEXT NOT NULL, entity_id TEXT NOT NULL UNIQUE, sort INTEGER DEFAULT 0,
                                       created_at TIMESTAMP);
            INSERT INTO rooms (id, name, slug) VALUES ('r-kitchen', 'Kitchen', 'kitchen');
            INSERT INTO room_devices (room_id, entity_id) VALUES ('r-kitchen', 'switch.kitchen_light');
            """
        )
        self.con.execute(
            "INSERT INTO panels (panel_id, name, room_id, ip_address, is_active, allow_guest)"
            " VALUES ('kitchen-panel', 'Kitchen', 'r-kitchen', ?, 1, 1)", (PANEL_IP,))
        self.con.commit()

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        self.con.commit()

    def rooms(self):
        return [r["name"] for r in self.con.execute("SELECT name FROM rooms ORDER BY name")]


@pytest.fixture
def db(monkeypatch):
    d = Db()

    async def _with_registry(_db, check, value):  # the real registry SQL, on this database
        return await check(d, value)

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)

    async def _index():  # the kitchen light is ON: "someone is up in the kitchen"
        return {LIGHT: {"entity_id": LIGHT, "state": "on", "attributes": {"friendly_name": "Kitchen Light"}}}

    monkeypatch.setattr(pc, "_entity_index", _index)
    monkeypatch.setattr(pc, "_global_default_player", lambda: None)
    return d


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "ZOE_TRUSTED_PROXY_NETS",
                "ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_DEVICE_TOKEN_BINDS_MEMBER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()

    async def _validate(session_id):  # zoe-auth: one member session, one guest session, nothing else
        if session_id == MEMBER_SID:
            return {"user_id": "casey", "role": "user", "username": "casey", "permissions": []}
        if session_id == GUEST_SID:
            return {"user_id": "guest", "role": "guest", "username": "guest", "permissions": []}
        return None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    # The panel's REAL device token, through the real cache and the real ``_resolve_device_token_user``.
    monkeypatch.setitem(panel_auth._token_cache, panel_auth._hash_token(PANEL_TOKEN),
                        {"panel_id": "kitchen-panel", "role": "voice-daemon", "expires_at": None, "revoked": 0})
    yield
    auth._session_cache.clear()


def _client(db, peer):
    app = FastAPI()
    app.include_router(pc.router)
    app.include_router(rooms.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, client=(peer, 50000))


READS = [
    "/api/panels/kitchen-panel/sleep-gate",
    "/api/panels/kitchen-panel/config",
    "/api/rooms",
    "/api/rooms/unassigned",
]

WRITES = [  # (method, path, body)
    ("PUT", "/api/panels/kitchen-panel/config", {"location": "Hall"}),
    ("POST", "/api/rooms", {"name": "Den"}),
    ("PUT", "/api/rooms/r-kitchen", {"name": "Den"}),
    ("PUT", "/api/rooms/r-kitchen/devices", {"devices": ["switch.den_lamp"]}),
    ("DELETE", "/api/rooms/r-kitchen/devices/switch.kitchen_light", None),
    ("DELETE", "/api/rooms/r-kitchen", None),
]

# Everyone who is neither a member nor the panel.
STRANGERS = [
    (PHONE_IP, {}),                                                        # anonymous, straight at :8000 on the LAN
    (PHONE_IP, {"X-Real-IP": PANEL_IP}),                                  # spoofed X-Real-IP from an untrusted peer
    (PHONE_IP, {"X-Forwarded-For": PANEL_IP}),                            # spoofed X-Forwarded-For
    (PROXY, {"X-Real-IP": PHONE_IP, "X-Forwarded-For": PANEL_IP}),        # through nginx: XFF is never evidence
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP}),          # a minted guest session is not the panel
    (PROXY, {"X-Real-IP": PANEL_IP, **CF}),                               # the panel's address, but over Cloudflare
    (PHONE_IP, {"X-Device-Token": "not-a-real-token"}),                   # an unknown device token falls through to guest
]

PANEL = [
    (PROXY, {"X-Real-IP": PANEL_IP}),                                     # the kiosk browser through nginx (live shape)
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PANEL_IP}),          # the kiosk with its guest session
    (PHONE_IP, {"X-Device-Token": PANEL_TOKEN}),                          # the panel's device token
]

MEMBER = [
    (PHONE_IP, {"X-Session-ID": MEMBER_SID}),                             # a signed-in member, from anywhere on the LAN
    (PROXY, {"X-Session-ID": MEMBER_SID, **CF}),                          # ... or over the tunnel
]


def _send(db, peer, headers, method, path, body=None):
    return _client(db, peer).request(method, path, json=body, headers=headers)


# ---- reads -------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_cannot_read_house_state(db, path, peer, headers):
    r = _send(db, peer, headers, "GET", path)
    assert r.status_code == 403, (path, headers, r.text)
    assert set(r.json()) == {"detail"}  # no state rides along with the refusal


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_the_panel_and_members_read_house_state(db, path, peer, headers):
    r = _send(db, peer, headers, "GET", path)
    assert r.status_code == 200, (path, headers, r.text)


def test_the_kiosk_still_gets_its_live_sleep_gate_vote(db):
    """The kiosk's own call, in its live shape: the lit kitchen keeps it awake."""
    r = _send(db, PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PANEL_IP}, "GET", "/api/panels/kitchen-panel/sleep-gate")
    assert r.json() == {"block": True, "reason": "room-occupied", "entities": [LIGHT]}


def test_an_inactive_or_guest_barred_panel_address_is_not_the_panel(db):
    db.con.execute("UPDATE panels SET allow_guest = 0")
    assert _send(db, PROXY, {"X-Real-IP": PANEL_IP}, "GET", "/api/panels/kitchen-panel/sleep-gate").status_code == 403
    db.con.execute("UPDATE panels SET allow_guest = 1, is_active = 0")
    assert _send(db, PROXY, {"X-Real-IP": PANEL_IP}, "GET", "/api/rooms").status_code == 403


def test_reads_follow_the_home_control_rollback(db, monkeypatch):
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert _send(db, PHONE_IP, {}, "GET", "/api/panels/kitchen-panel/sleep-gate").status_code == 200


# ---- writes ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,body", WRITES)
@pytest.mark.parametrize("peer,headers", STRANGERS + PANEL)
def test_only_a_member_writes_rooms_and_panel_config(db, method, path, body, peer, headers):
    before = (db.rooms(), db.con.execute("SELECT location FROM panels").fetchall()[0][0],
              db.con.execute("SELECT COUNT(*) FROM room_devices").fetchone()[0])
    r = _send(db, peer, headers, method, path, body)
    assert r.status_code == 403, (method, path, headers, r.text)
    after = (db.rooms(), db.con.execute("SELECT location FROM panels").fetchall()[0][0],
             db.con.execute("SELECT COUNT(*) FROM room_devices").fetchone()[0])
    assert after == before  # nothing changed


@pytest.mark.parametrize("method,path,body", WRITES)
@pytest.mark.parametrize("peer,headers", MEMBER)
def test_a_member_writes_rooms_and_panel_config(db, method, path, body, peer, headers):
    r = _send(db, peer, headers, method, path, body)
    assert r.status_code == 200, (method, path, headers, r.text)


def test_the_unauthenticated_admin_override_is_not_a_member_for_writes(db, monkeypatch):
    """``ZOE_UNAUTHENTICATED_ROLE=family-admin`` promotes a header-less caller; it must not rename rooms."""
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")
    r = _send(db, PHONE_IP, {}, "POST", "/api/rooms", {"name": "Den"})
    assert r.status_code == 403, r.text
    assert db.rooms() == ["Kitchen"]
