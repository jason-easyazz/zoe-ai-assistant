"""Home control needs a signed-in MEMBER or THE PANEL - never a plain anonymous LAN guest (security fix 2026-10-17).

Live finding: ``POST /api/ha/control`` accepted any principal, and ``auth.get_current_user`` resolves a credential-less request
as ``guest``, so any device on the home LAN (guest Wi-Fi visitors, IoT gadgets) could drive Home Assistant. The same hole was
reachable through ``POST /api/skybridge/resolve`` ("turn off the lights") and the entity reads leaked presence/lock/camera
state. These tests go over real HTTP with the REAL ``get_current_user`` (only zoe-auth and the device-token cache are faked) and
the REAL panel-registry SQL (in-memory SQLite). All data is invented; addresses are TEST-NET.
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
from routers import ha_control, skybridge

pytestmark = pytest.mark.ci_safe

PANEL_IP = "192.0.2.50"     # the registered kiosk
PHONE_IP = "192.0.2.77"     # a visitor's phone / an IoT gadget on the same Wi-Fi
PROXY = "127.0.0.1"         # nginx: the TCP peer for every browser request that came through it
MEMBER_SID = "sid-member"
GUEST_SID = "sid-guest"     # what POST /api/auth/guest mints for ANY caller
PANEL_TOKEN = "tok-unbound-panel"
CONTROL = {"entity_id": "light.kitchen", "action": "toggle"}
CF = {"cf-connecting-ip": PANEL_IP, "cf-ray": "abc123"}


class _Cur:
    def __init__(self, cur):
        self._cur = cur

    async def fetchone(self):
        return self._cur.fetchone()


class RegistryDb:
    """The AsyncpgCompat surface ``panel_principal`` uses (``execute(sql, params)`` + ``?``), over SQLite: the registry SQL runs."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.execute("CREATE TABLE panels (panel_id TEXT PRIMARY KEY, ip_address TEXT, is_active INT, allow_guest INT)")

    def panel(self, ip=PANEL_IP, active=1, allow_guest=1):
        self.con.execute("INSERT INTO panels VALUES ('kitchen-panel', ?, ?, ?)", (ip, active, allow_guest))

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params or ())))


@pytest.fixture
def registry(monkeypatch):
    db = RegistryDb()
    db.panel()

    async def _with_registry(_db, check, value):
        return await check(db, value)

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)
    return db


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL"):
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

    import routers.panel_auth as panel_auth

    async def _device(raw):  # an UNBOUND panel's device token -> the panel principal (guest + panel_id)
        if raw == PANEL_TOKEN:
            return {"user_id": "guest", "role": "guest", "username": "panel:kitchen-panel", "permissions": [],
                    "panel_id": "kitchen-panel", "device_token_role": "voice"}
        return None

    monkeypatch.setattr(panel_auth, "_resolve_device_token_user", _device)
    yield
    auth._session_cache.clear()


@pytest.fixture
def bridge(monkeypatch):
    calls = []

    async def _post(path, body):
        calls.append(("POST", path, body))
        return {"changed": True}

    async def _get(path):
        calls.append(("GET", path, None))
        return {"entities": [{"entity_id": "lock.front_door", "state": "unlocked"}]} if path == "/entities" else {"state": "on"}

    monkeypatch.setattr(ha_control, "_bridge_post", _post)
    monkeypatch.setattr(ha_control, "_bridge_get", _get)
    return calls


def _ha(peer):
    app = FastAPI()
    app.include_router(ha_control.router)
    return TestClient(app, client=(peer, 50000))


def _control(peer, headers=None):
    return _ha(peer).post("/api/ha/control", json=CONTROL, headers=headers or {})


# ---- refused: everyone who is neither a member nor the panel ----------------------------------------------------------------
@pytest.mark.parametrize("peer,headers", [
    (PHONE_IP, {}),                                                        # anonymous, straight at :8000 on the LAN
    (PHONE_IP, {"X-Real-IP": PANEL_IP}),                                  # spoofed X-Real-IP from an untrusted peer
    (PHONE_IP, {"X-Forwarded-For": PANEL_IP}),                            # spoofed X-Forwarded-For
    (PHONE_IP, {"X-Forwarded-For": PANEL_IP, "X-Real-IP": PANEL_IP}),     # both
    (PROXY, {"X-Real-IP": PHONE_IP, "X-Forwarded-For": PANEL_IP}),        # through nginx: XFF is never evidence
    (PROXY, {}),                                                          # through nginx with no address at all
    (PROXY, {"X-Real-IP": PANEL_IP, **CF}),                               # the panel's address, but over Cloudflare
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP}),          # a minted guest session is not the panel
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PANEL_IP, **CF}),    # guest session + panel address over the tunnel
    (PHONE_IP, {"X-Device-Token": "not-a-real-token"}),                   # an unknown device token falls through to guest
])
def test_anonymous_and_forged_callers_cannot_drive_the_house(registry, bridge, peer, headers):
    r = _control(peer, headers)
    assert r.status_code == 403, r.text
    assert "control the house" in r.json()["detail"]
    assert bridge == []  # nothing reached Home Assistant


def test_an_inactive_or_guest_barred_panel_address_is_not_the_panel(registry, bridge):
    registry.con.execute("UPDATE panels SET allow_guest = 0")
    assert _control(PROXY, {"X-Real-IP": PANEL_IP}).status_code == 403
    registry.con.execute("UPDATE panels SET allow_guest = 1, is_active = 0")
    assert _control(PROXY, {"X-Real-IP": PANEL_IP}).status_code == 403
    assert bridge == []


async def test_an_auth_outage_principal_is_never_allowed(registry):
    from starlette.requests import Request

    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"x-real-ip", PANEL_IP.encode())],
                   "client": (PROXY, 1)})
    degraded = {"user_id": "guest", "role": "guest", "auth_degraded": True}
    assert await panel_principal.may_control_home(req, degraded) is False
    # ...and a degraded principal never counts as a member, whatever role it carries
    assert panel_principal.is_member({"user_id": "casey", "role": "user", "auth_degraded": True}) is False


# ---- allowed: the panel, members --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("peer,headers", [
    (PROXY, {"X-Real-IP": PANEL_IP}),                                     # the kiosk browser through nginx (live shape)
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PANEL_IP}),          # the kiosk with its guest session
    (PHONE_IP, {"X-Device-Token": PANEL_TOKEN}),                          # the panel daemon's device token (no member bound)
    (PHONE_IP, {"X-Session-ID": MEMBER_SID}),                             # a signed-in member, from anywhere on the LAN
    (PROXY, {"X-Session-ID": MEMBER_SID, **CF}),                          # a signed-in member over the tunnel
])
def test_the_panel_and_members_still_control_the_house(registry, bridge, peer, headers):
    r = _control(peer, headers)
    assert r.status_code == 200, r.text
    assert r.json()["service"] == "toggle"
    assert bridge == [("POST", "/devices/control", {"entity_id": "light.kitchen", "action": "toggle", "data": {}})]


def test_the_household_list_flag_neither_breaks_nor_loosens_home_control(registry, bridge, monkeypatch):
    # `off` must not silently take the light pins off the kiosk ...
    monkeypatch.setenv("ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "off")
    assert _control(PROXY, {"X-Real-IP": PANEL_IP}).status_code == 200
    # ... and `lan` (any private-LAN guest session counts as the panel, for LISTS) must not open the house to every phone.
    monkeypatch.setenv("ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "lan")
    assert _control(PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP}).status_code == 403


def test_kill_switch_default_is_the_fix_and_open_is_an_explicit_rollback(registry, bridge, monkeypatch):
    for value in ("", "panel", "PANEL", "closed", "true", "0"):
        monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", value)
        assert _control(PHONE_IP).status_code == 403, value
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert _control(PHONE_IP).status_code == 200


# ---- the reads: HA state is presence, locks and cameras ----------------------------------------------------------------------
def test_entity_reads_need_a_member_or_the_panel(registry, bridge):
    assert _ha(PHONE_IP).get("/api/ha/entities").status_code == 403
    assert _ha(PHONE_IP).get("/api/ha/state/person.casey").status_code == 403
    assert _ha(PHONE_IP).get("/api/ha/entities", headers={"X-Real-IP": PANEL_IP}).status_code == 403
    assert bridge == []
    kiosk = _ha(PROXY).get("/api/ha/entities", headers={"X-Real-IP": PANEL_IP})
    assert kiosk.status_code == 200 and kiosk.json()["count"] == 1
    assert _ha(PHONE_IP).get("/api/ha/state/lock.front_door", headers={"X-Session-ID": MEMBER_SID}).status_code == 200


@pytest.mark.parametrize("bad", ["..%2Fdevices%2Fcontrol", "../services", "light.kitchen/../../x", "LIGHT.Kitchen", "light"])
def test_state_read_refuses_anything_that_is_not_an_entity_id(registry, bridge, bad):
    r = _ha(PHONE_IP).get(f"/api/ha/state/{bad}", headers={"X-Session-ID": MEMBER_SID})
    assert r.status_code in (400, 404), r.text
    assert bridge == []


# ---- the same rule on POST /api/skybridge/resolve ---------------------------------------------------------------------------
@pytest.fixture
def sky(registry, monkeypatch):
    seen = []

    async def _resolve(message, user_id, *, context=None, db=None, **_kw):
        seen.append((message, user_id))
        return {"handled": True, "spoken_summary": "done", "cards": []}

    monkeypatch.setattr(skybridge, "resolve_skybridge_request", _resolve)

    def _client(peer):
        app = FastAPI()
        app.include_router(skybridge.router)

        async def _db():
            yield registry

        app.dependency_overrides[skybridge.get_db] = _db
        return TestClient(app, client=(peer, 50000))

    return _client, seen


def test_the_classifier_sees_the_command_as_home_control():
    # Guards the gate below: if this phrase stopped classifying as smart_home the skybridge tests would pass vacuously.
    intent = skybridge.classify_skybridge_intent("turn off the kitchen light", None)
    assert intent is not None and intent.domain == "smart_home"


def test_skybridge_refuses_home_control_to_an_anonymous_lan_caller(sky):
    client, seen = sky
    for peer, headers in [(PHONE_IP, {}), (PHONE_IP, {"X-Real-IP": PANEL_IP}), (PROXY, {"X-Real-IP": PANEL_IP, **CF}),
                          (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP})]:
        r = client(peer).post("/api/skybridge/resolve", json={"message": "turn off the kitchen light"}, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["auth_required"] is True and body["intent"]["domain"] == "smart_home", headers
    assert seen == []  # the resolver - and so Home Assistant - was never reached


def test_skybridge_still_runs_home_control_for_the_panel_and_members_and_other_asks_for_anyone(sky):
    client, seen = sky
    assert client(PROXY).post("/api/skybridge/resolve", json={"message": "turn off the kitchen light"},
                              headers={"X-Real-IP": PANEL_IP}).json()["spoken_summary"] == "done"
    assert client(PHONE_IP).post("/api/skybridge/resolve", json={"message": "turn off the kitchen light"},
                                 headers={"X-Session-ID": MEMBER_SID}).json()["spoken_summary"] == "done"
    # not home control: unchanged for an anonymous caller (the resolver applies its own per-domain rules)
    assert client(PHONE_IP).post("/api/skybridge/resolve", json={"message": "what time is it"}).json()["spoken_summary"] == "done"
    assert [m for m, _ in seen] == ["turn off the kitchen light", "turn off the kitchen light", "what time is it"]


# ---- review round (cross-review on #2089) ------------------------------------------------------------------------------------
def test_the_legacy_unauthenticated_admin_override_does_not_open_the_house(registry, bridge, monkeypatch):
    # ZOE_UNAUTHENTICATED_ROLE=family-admin promotes EVERY header-less caller to admin; home control must not follow it.
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")
    assert _control(PHONE_IP).status_code == 403
    assert _ha(PHONE_IP).get("/api/ha/entities").status_code == 403
    assert bridge == []
    # the kiosk is still the panel, and opening the house stays the explicit ZOE_HA_CONTROL_SCOPE=open
    assert _control(PROXY, {"X-Real-IP": PANEL_IP}).status_code == 200
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert _control(PHONE_IP).status_code == 200


def test_an_auth_outage_over_http_reaches_nothing_even_from_the_panel_address(registry, bridge, monkeypatch):
    # ZOE_AUTH_FAIL_CLOSED=false: an outage resolves ANY session header to a degraded guest - never evidence of the panel.
    monkeypatch.setattr(auth, "_AUTH_FAIL_CLOSED", False)

    async def _outage(_sid):
        return auth._degraded_user()

    monkeypatch.setattr(auth, "_validate_with_auth_service", _outage)
    r = _control(PROXY, {"X-Session-ID": "anything-at-all", "X-Real-IP": PANEL_IP})
    assert r.status_code == 403, r.text
    assert bridge == []


def test_the_kiosk_credential_is_the_panel_from_any_address_but_not_over_the_tunnel(registry, bridge, monkeypatch):
    import panel_kiosk_credential

    monkeypatch.setattr(panel_kiosk_credential, "credential_panel_id", lambda _req: "kitchen-panel")
    assert _control(PROXY, {"X-Real-IP": PHONE_IP}).status_code == 200       # the kiosk on another Wi-Fi / private link
    assert _control(PROXY, {"X-Real-IP": PHONE_IP, **CF}).status_code == 403  # never over Cloudflare
    registry.con.execute("UPDATE panels SET allow_guest = 0")
    assert _control(PROXY, {"X-Real-IP": PHONE_IP}).status_code == 403       # the row still decides
    assert len(bridge) == 1
