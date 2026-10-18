"""Relay ingress (panel-anywhere R1, flag ``ZOE_RELAY_INGRESS``): a request that came through the panel relay is never a guest, has
no address, resolves to the PANEL principal only, and is bound to its client certificate.

Cells (design docs/architecture/panel-anywhere.md section 10): T5 (header trust, no address match), T10 (no device credential ->
401, home control refused, LAN control), T11 (a bound token is the panel and nothing more; a member needs a fresh sign-in), the
fingerprint binding, and "flag off = the relay header is inert". They go over REAL HTTP (``TestClient``) through the REAL
``RelayIngressMiddleware``, ``auth.get_current_user``, ``panel_principal`` and the REAL panel/certificate SQL (alembic 0059 run on
in-memory SQLite). Only zoe-auth, the HA bridge and the voice lane's freshness lookup are faked. Every positive control is next
to its negative: the LAN request without the relay marker proves the probe reaches the route. All data is invented.

The nginx half (the headers are overwritten, a client without a certificate gets nowhere) is
``tests/unit/test_relay_nginx_listener.py`` (static) and ``test_relay_nginx_live.py`` (a real nginx container, opt-in).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import re
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import panel_kiosk_credential as kiosk
import panel_principal
import relay_ingress
from routers import ha_control, panel_auth, panel_kiosk, panel_relay

pytestmark = pytest.mark.ci_safe

NGINX = "172.18.0.5"        # the zoe-ui container: the TCP peer of every relayed AND LAN request that came through nginx
LAN_HOST = "192.0.2.77"     # a phone on the Wi-Fi that talks to :8000 directly (not our proxy)
PANEL_IP = "192.0.2.50"     # the kitchen panel's REGISTERED LAN address
FP_KITCHEN = "a" * 40       # nginx $ssl_client_fingerprint of the kitchen panel's certificate
FP_HALL = "b" * 40
FP_REVOKED = "c" * 40
FP_INACTIVE = "d" * 40
FP_UNKNOWN = "e" * 40
TOK_KITCHEN = "tok-kitchen-panel"
TOK_HALL = "tok-hall-panel"
TOK_INACTIVE = "tok-inactive-panel"
SID_CASEY = "sid-casey"       # a member's real zoe-auth session
SID_ADMIN = "sid-admin"
SID_GUEST = "sid-guest"       # what POST /api/auth/guest mints for ANY caller
SID_DEGRADED = "sid-degraded"
HOST = "h1a2b3c4d5.zoe.internal"
CONTROL = {"entity_id": "light.kitchen", "action": "toggle"}


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def relay_headers(fp=FP_KITCHEN, token=TOK_KITCHEN, session=None, extra=None):
    """What nginx's relay listener sends, plus the credentials the panel adds. ``fp=None`` / ``token=None`` omit them."""
    h = {"X-Zoe-Ingress": "relay"}
    if fp:
        h["X-Zoe-Client-Cert-Fp"] = fp
    if token:
        h["X-Device-Token"] = token
    if session:
        h["X-Session-ID"] = session
    h.update(extra or {})
    return h


# ── the registry: real alembic 0059 on SQLite ────────────────────────────────────────────────────────────────────────────


class _Cur:
    def __init__(self, cur):
        self._cur = cur

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()


class Registry:
    """The AsyncpgCompat surface the panel code uses (``execute(sql, params)`` + ``?``), over SQLite with the REAL 0059 schema."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("CREATE TABLE panels (panel_id TEXT PRIMARY KEY, ip_address TEXT, is_active INT DEFAULT 1, allow_guest INT DEFAULT 1)")
        self.con.execute("CREATE TABLE panel_user_bindings (panel_id TEXT, user_id TEXT, binding_type TEXT, priority INT DEFAULT 0)")
        _migration("upgrade", self.con)

    def panel(self, panel_id, ip=None, active=1, allow_guest=1):
        self.con.execute("INSERT INTO panels (panel_id, ip_address, is_active, allow_guest) VALUES (?, ?, ?, ?)",
                         (panel_id, ip, active, allow_guest))

    def cert(self, fp, panel_id, revoked=None):
        self.con.execute("INSERT INTO panel_client_certs (fingerprint, panel_id, note, created_at, revoked_at) VALUES (?, ?, '', '2026-10-17', ?)",
                         (fp, panel_id, revoked))

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        self.con.commit()


def _migration(fn, con):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import sqlalchemy as sa

    path = ROOT / "alembic/versions/0059_panel_client_certs.py"
    spec = importlib.util.spec_from_file_location("mig_0059", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = sa.create_engine("sqlite://", creator=lambda: con)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn)()


@pytest.fixture
def reg(monkeypatch):
    r = Registry()
    r.panel("kitchen-panel", PANEL_IP)
    r.panel("hall-panel", "192.0.2.60")
    r.panel("inactive-panel", "192.0.2.61", active=0)
    r.con.execute("INSERT INTO panel_user_bindings VALUES ('kitchen-panel', 'casey', 'default', 0)")
    r.cert(FP_KITCHEN, "kitchen-panel")
    r.cert(FP_HALL, "hall-panel")
    r.cert(FP_REVOKED, "kitchen-panel", revoked="2026-10-17T00:00:00+00:00")
    r.cert(FP_INACTIVE, "inactive-panel")

    @contextlib.asynccontextmanager
    async def _ctx():
        yield r

    import db_pool

    monkeypatch.setattr(db_pool, "get_db_ctx", _ctx)
    relay_ingress.invalidate_cache()
    yield r
    relay_ingress.invalidate_cache()


FRESH: dict = {}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_RELAY_INGRESS", "ZOE_RELAY_HOSTNAME", "ZOE_HA_CONTROL_SCOPE", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE",
                "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_DEVICE_TOKEN_BINDS_MEMBER", "ZOE_RELAY_TRUSTED_PEER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()
    FRESH.clear()

    async def _validate(session_id):  # zoe-auth: a member, an admin, a guest, a degraded outage answer, nothing else
        if session_id == SID_CASEY:
            return {"user_id": "casey", "role": "user", "username": "casey", "permissions": []}
        if session_id == SID_ADMIN:
            return {"user_id": "jo", "role": "admin", "username": "jo", "permissions": []}
        if session_id == SID_GUEST:
            return {"user_id": "guest", "role": "guest", "username": "guest", "permissions": []}
        if session_id == SID_DEGRADED:
            return {auth._DEGRADED_MARK: True, "user_id": "guest", "role": "guest", "username": "guest", "permissions": []}
        return None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    for raw, panel in ((TOK_KITCHEN, "kitchen-panel"), (TOK_HALL, "hall-panel"), (TOK_INACTIVE, "inactive-panel")):
        monkeypatch.setitem(panel_auth._token_cache, _hash(raw),
                            dict(panel_id=panel, role="voice", scopes="[]", expires_at=None, revoked=0))

    # The voice lane's freshness authority (a fresh PIN panel session), faked at its seam: FRESH[panel_id] = member id.
    stub = types.ModuleType("routers.voice_tts")

    async def _resolve_fresh_panel_member(panel_id, fallback_db=None):
        return FRESH.get(panel_id)

    stub._resolve_fresh_panel_member = _resolve_fresh_panel_member
    monkeypatch.setitem(sys.modules, "routers.voice_tts", stub)
    yield
    auth._session_cache.clear()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("ZOE_RELAY_INGRESS", "1")
    monkeypatch.setenv("ZOE_RELAY_HOSTNAME", HOST)
    monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", NGINX + "/32")  # the zoe-ui container: ONE address, never the docker /12


@pytest.fixture
def bridge(monkeypatch):
    calls = []

    async def _post(path, body):
        calls.append(("POST", path, body))
        return {"changed": True}

    async def _get(path):
        calls.append(("GET", path, None))
        return {"entities": []}

    monkeypatch.setattr(ha_control, "_bridge_post", _post)
    monkeypatch.setattr(ha_control, "_bridge_get", _get)
    return calls


def make_app(reg, *, middleware=True):
    app = FastAPI()
    if middleware:
        app.add_middleware(relay_ingress.RelayIngressMiddleware)

    @app.get("/api/whoami")
    async def whoami(request: Request, user: dict = Depends(auth.get_current_user)):
        return {
            "user": user,
            "client_ip": panel_principal.trusted_client_ip(request),
            "panel_registered": await panel_principal.is_panel_guest(request, user, mode="registered"),
            "panel_lan_scope": await panel_principal.is_panel_guest(request, user, mode="lan"),
        }

    @app.get("/api/member")
    async def member(user: dict = Depends(auth.require_signed_in)):
        return {"user_id": user["user_id"]}

    @app.websocket("/ws/push")
    async def push(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    app.include_router(ha_control.router)
    app.include_router(panel_relay.router)
    app.include_router(panel_relay.gate_router)
    app.include_router(panel_kiosk.router)

    async def _db():
        yield reg

    app.dependency_overrides[panel_relay.get_db] = _db
    app.dependency_overrides[panel_kiosk.get_db] = _db
    return app


def client(reg, peer=NGINX, *, middleware=True):
    return TestClient(make_app(reg, middleware=middleware), base_url="https://zoe.test", client=(peer, 50000),
                      follow_redirects=False)


# ── T5: the marker is trusted only from our proxy, a relayed request has no address ───────────────────────────────────────


def test_t5_the_marker_counts_only_from_a_trusted_proxy_with_the_exact_value(reg, on):
    def req(peer, **headers):
        return Request(dict(type="http", method="GET", path="/", client=(peer, 1),
                            headers=[(k.lower().encode(), v.encode()) for k, v in headers.items()]))

    assert relay_ingress.is_relay(req(NGINX, **{"X-Zoe-Ingress": "relay"})) is True
    # NEGATIVE CONTROLS: nginx overwrites a client-sent value, so "lan" (or anything but "relay") is the LAN ...
    assert relay_ingress.is_relay(req(NGINX, **{"X-Zoe-Ingress": "lan"})) is False
    assert relay_ingress.is_relay(req(NGINX)) is False
    # ... and a host that talks to :8000 directly (not our proxy) cannot claim it, even with a real-looking fingerprint.
    assert relay_ingress.is_relay(req(LAN_HOST, **{"X-Zoe-Ingress": "relay", "X-Zoe-Client-Cert-Fp": FP_KITCHEN})) is False
    # ... and so cannot ANOTHER container in the same docker /12 that ZOE_TRUSTED_PROXY_NETS trusts for X-Real-IP (the trust is the
    # nginx container's single address, not the subnet), nor loopback.
    assert relay_ingress.is_relay(req("172.18.0.99", **{"X-Zoe-Ingress": "relay"})) is False
    assert relay_ingress.is_relay(req("172.17.0.2", **{"X-Zoe-Ingress": "relay"})) is False
    assert relay_ingress.is_relay(req("127.0.0.1", **{"X-Zoe-Ingress": "relay"})) is False


def test_t5_the_trusted_peer_is_a_single_address_and_unset_trusts_nobody(reg, on, monkeypatch):
    def req(peer):
        return Request(dict(type="http", method="GET", path="/", client=(peer, 1), headers=[(b"x-zoe-ingress", b"relay")]))

    for wide in ("172.16.0.0/12", "172.18.0.0/24", "0.0.0.0/0", "not-an-address"):
        monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", wide)
        assert relay_ingress.trusted_peer_nets() == [], wide
        assert relay_ingress.is_relay(req(NGINX)) is False, wide  # a network is ignored, never widened into trust
    monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", "172.18.0.7, " + NGINX)
    assert relay_ingress.is_relay(req(NGINX)) is True and relay_ingress.is_relay(req("172.18.0.7")) is True
    monkeypatch.delenv("ZOE_RELAY_TRUSTED_PEER")
    assert relay_ingress.is_relay(req(NGINX)) is False, "unset trusts nobody"
    # FAIL CLOSED, never to the LAN rules: with the flag on, a marker from any other peer (a forgery, or the nginx container's
    # address changed after a recreate and the setting is stale) is refused by the gate instead of being judged as a LAN request.
    r = client(reg).get("/api/whoami", headers=relay_headers())
    assert r.status_code == 403 and r.json()["detail"] == "relay_marker_untrusted_peer"
    monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", NGINX)
    assert client(reg).get("/api/whoami", headers=relay_headers()).status_code == 200  # POSITIVE: the same request once pinned


def test_t5_a_host_that_is_not_nginx_writing_the_relay_headers_is_refused_not_believed(reg, on):
    # A phone on the Wi-Fi, or another container on the docker bridge, writes both headers itself and reaches zoe-data directly.
    for peer in (LAN_HOST, "172.18.0.99"):
        r = client(reg, peer).get("/api/whoami", headers=relay_headers())
        assert r.status_code == 403 and r.json()["detail"] == "relay_marker_untrusted_peer", (peer, r.text)
    # POSITIVE CONTROL: the same request from the pinned nginx address is the relay.
    assert client(reg).get("/api/whoami", headers=relay_headers()).json()["user"]["ingress"] == "relay"
    # And the very same host without the headers is the ordinary LAN (a client that never mentions the relay is untouched).
    lan = client(reg, LAN_HOST).get("/api/whoami", headers={"X-Device-Token": TOK_KITCHEN})
    assert lan.status_code == 200 and lan.json()["user"]["panel_id"] == "kitchen-panel" and "ingress" not in lan.json()["user"]


def test_t5_a_relayed_request_has_no_address_and_never_matches_a_registered_panel(reg, on):
    # The relay listener sends no X-Real-IP, but even a (forged or leftover) one must change nothing for a relayed request.
    h = relay_headers(token=None, extra={"X-Real-IP": PANEL_IP})
    # Positive control, same address header WITHOUT the relay marker (the LAN): the registered address IS the panel ...
    lan = client(reg).get("/api/whoami", headers={"X-Real-IP": PANEL_IP, "X-Session-ID": SID_GUEST})
    assert lan.status_code == 200 and lan.json()["panel_registered"] is True and lan.json()["client_ip"] == PANEL_IP
    # ... and the `lan` scope accepts any private client with a guest session.
    assert lan.json()["panel_lan_scope"] is True
    # The same address header on a relayed request: no address, and (no credential) the request does not even get that far.
    relayed = client(reg).get("/api/whoami", headers={**h, "X-Session-ID": SID_GUEST})
    assert relayed.status_code == 401 and relayed.json()["detail"] == "relay_device_credential_required"


def test_t5_even_past_the_gate_the_address_is_ignored(reg, on):
    # Function level (no gate): the principal rules themselves refuse an address for a relayed request.
    req = Request(dict(type="http", method="GET", path="/", client=(NGINX, 1), headers=[
        (b"x-zoe-ingress", b"relay"), (b"x-real-ip", PANEL_IP.encode()), (b"x-session-id", SID_GUEST.encode())]))
    guest = {"user_id": "guest", "role": "guest"}
    import asyncio

    assert panel_principal.trusted_client_ip(req) == ""
    assert asyncio.run(panel_principal.is_panel_guest(req, guest, mode="registered")) is False
    assert asyncio.run(panel_principal.is_panel_guest(req, guest, mode="lan")) is False
    # Second layer, in isolation: even if the address helper regressed and handed back the forged address, a relayed request is
    # still never matched by it (the address rules are skipped for the relay on their own).
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(panel_principal, "trusted_client_ip", lambda _r: PANEL_IP)
        assert asyncio.run(panel_principal.is_panel_guest(req, guest, mode="registered")) is False
        assert asyncio.run(panel_principal.is_panel_guest(req, guest, mode="lan")) is False
    # Positive control: the same facts without the marker.
    lan = Request(dict(type="http", method="GET", path="/", client=(NGINX, 1), headers=[
        (b"x-real-ip", PANEL_IP.encode()), (b"x-session-id", SID_GUEST.encode())]))
    assert asyncio.run(panel_principal.is_panel_guest(lan, guest, mode="registered")) is True
    assert asyncio.run(panel_principal.is_panel_guest(lan, guest, mode="lan")) is True


# ── T10: never guest ───────────────────────────────────────────────────────────────────────────────────────────────────────


def test_t10_no_device_credential_is_401_and_home_control_is_refused_over_the_relay(reg, on, bridge):
    c = client(reg)
    for headers in (relay_headers(token=None), relay_headers(token=None, session=SID_GUEST),
                    relay_headers(token=None, session=SID_CASEY)):
        r = c.post("/api/ha/control", json=CONTROL, headers=headers)
        assert r.status_code == 401, (headers, r.text)
        assert r.json()["detail"] == "relay_device_credential_required"
    assert bridge == [], "home control must never have run"
    # No client certificate at all (nginx would not forward the request; zoe-data refuses it too).
    r = c.get("/api/whoami", headers=relay_headers(fp=None))
    assert r.status_code == 401 and r.json()["detail"] == "relay_client_certificate_required"
    # POSITIVE CONTROL / probe reaches the route: the SAME credential-less request on the LAN is the plain guest #2089 refuses ...
    lan = c.post("/api/ha/control", json=CONTROL, headers={"X-Session-ID": SID_GUEST})
    assert lan.status_code == 403 and bridge == []
    # ... and the LAN panel (device token) is accepted per #2089, which is what proves the route and the bridge fake are live.
    ok = c.post("/api/ha/control", json=CONTROL, headers={"X-Device-Token": TOK_KITCHEN})
    assert ok.status_code == 200 and len(bridge) == 1


def test_t10_a_certificate_and_token_resolve_to_the_guest_class_panel_principal_which_cannot_drive_the_house(reg, on, bridge, monkeypatch):
    c = client(reg)
    who = c.get("/api/whoami", headers=relay_headers())
    assert who.status_code == 200, who.text
    user = who.json()["user"]
    assert user["panel_id"] == "kitchen-panel" and user["user_id"] == "guest" and user["ingress"] == "relay"
    assert who.json()["client_ip"] == ""
    # The panel principal does not drive the house from afar - a verified member does (design 5.2, away-mode D6) ...
    refused = c.post("/api/ha/control", json=CONTROL, headers=relay_headers())
    assert refused.status_code == 403 and bridge == []
    assert c.get("/api/ha/entities", headers=relay_headers()).status_code == 403
    # ... even with the LAN rollback flag, which must not reach the relay.
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert c.post("/api/ha/control", json=CONTROL, headers=relay_headers()).status_code == 403
    # CONTROL: on the LAN the same flag DOES open it (so the relay refusal is the relay rule, not the flag).
    assert c.post("/api/ha/control", json=CONTROL, headers={"X-Session-ID": SID_GUEST}).status_code == 200
    monkeypatch.delenv("ZOE_HA_CONTROL_SCOPE")
    bridge.clear()
    # A member with a FRESH sign-in at the panel may.
    FRESH["kitchen-panel"] = "casey"
    ok = c.post("/api/ha/control", json=CONTROL, headers=relay_headers(session=SID_CASEY))
    assert ok.status_code == 200 and len(bridge) == 1


def test_t10_a_zoe_auth_outage_and_the_legacy_admin_override_never_make_a_guest(reg, on, monkeypatch):
    c = client(reg)
    # ZOE_UNAUTHENTICATED_ROLE=family-admin: LAN-only. Over the relay a credential-less request is 401, never an admin.
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "family-admin")
    assert c.get("/api/whoami", headers=relay_headers(token=None)).status_code == 401
    lan = c.get("/api/whoami")  # control: the override does promote on the LAN
    assert lan.status_code == 200 and lan.json()["user"].get("unauthenticated_override") is True
    # A degraded zoe-auth (fail-open config) answer is refused over the relay (503), not downgraded to a guest.
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    monkeypatch.setattr(auth, "_AUTH_FAIL_CLOSED", False)
    r = c.get("/api/whoami", headers=relay_headers(session=SID_DEGRADED))
    assert r.status_code == 503
    # CONTROL: the LAN accepts the same degraded session as a (marked) guest.
    lan = c.get("/api/whoami", headers={"X-Session-ID": SID_DEGRADED})
    assert lan.status_code == 200 and lan.json()["user"].get("auth_degraded") is True
    # An unknown session is 401 on both.
    assert c.get("/api/whoami", headers=relay_headers(session="sid-nope")).status_code == 401


def test_t10_the_gate_covers_websockets_and_every_route_not_just_the_ones_that_ask(reg, on):
    c = client(reg)
    with pytest.raises(WebSocketDisconnect) as refused:
        with c.websocket_connect("/ws/push", headers=relay_headers(token=None)):
            pass
    assert refused.value.code == 1008
    with c.websocket_connect("/ws/push", headers=relay_headers()) as ws:  # positive control
        assert ws.receive_json() == {"ok": True}
    # A route that never calls get_current_user is still gated (the middleware, not each route, owns it).
    app = make_app(reg)

    @app.get("/open")
    async def open_route():
        return {"open": True}

    c2 = TestClient(app, client=(NGINX, 1))
    assert c2.get("/open", headers=relay_headers(token=None)).status_code == 401
    assert c2.get("/open", headers=relay_headers()).status_code == 200
    assert c2.get("/open").status_code == 200  # the LAN: untouched


def test_t10_get_current_user_enforces_it_without_the_middleware_too(reg, on):
    c = client(reg, middleware=False)
    assert c.get("/api/whoami", headers=relay_headers(token=None)).status_code == 401
    assert c.get("/api/whoami", headers=relay_headers()).json()["user"]["panel_id"] == "kitchen-panel"


# ── T11: a bound token is the panel and nothing more ───────────────────────────────────────────────────────────────────────


def test_t11_a_bound_token_is_the_panel_only_over_the_relay_and_the_member_on_the_lan_when_the_rollback_is_set(reg, on, monkeypatch):
    # The kitchen panel HAS a default member binding (casey). With the rollback flag the LAN resolves the token AS casey ...
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", "1")
    c = client(reg)
    lan = c.get("/api/whoami", headers={"X-Device-Token": TOK_KITCHEN})
    assert lan.status_code == 200 and lan.json()["user"]["user_id"] == "casey", "the binding must be real in this fixture"
    # ... the relay never does, whatever the binding or the flag.
    relayed = c.get("/api/whoami", headers=relay_headers())
    user = relayed.json()["user"]
    assert user["user_id"] == "guest" and user["role"] == "guest" and user["panel_id"] == "kitchen-panel"
    assert c.get("/api/member", headers=relay_headers()).status_code == 403
    monkeypatch.delenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER")
    assert c.get("/api/whoami", headers={"X-Device-Token": TOK_KITCHEN}).json()["user"]["user_id"] == "guest"  # the 2026-10-17 fix


def test_t11_a_member_route_needs_a_fresh_sign_in_at_the_panel(reg, on):
    c = client(reg)
    # A valid member session alone, with no fresh sign-in at this panel: still only the panel (403 on a member route).
    r = c.get("/api/member", headers=relay_headers(session=SID_CASEY))
    assert r.status_code == 403
    assert c.get("/api/whoami", headers=relay_headers(session=SID_CASEY)).json()["user"]["user_id"] == "guest"
    # Someone ELSE signed in at the panel is not casey.
    FRESH["kitchen-panel"] = "jo"
    assert c.get("/api/member", headers=relay_headers(session=SID_CASEY)).status_code == 403
    # Casey signed in at ANOTHER panel is not casey here.
    FRESH.clear()
    FRESH["hall-panel"] = "casey"
    assert c.get("/api/member", headers=relay_headers(session=SID_CASEY)).status_code == 403
    # POSITIVE: a fresh sign-in at THIS panel.
    FRESH.clear()
    FRESH["kitchen-panel"] = "casey"
    ok = c.get("/api/member", headers=relay_headers(session=SID_CASEY))
    assert ok.status_code == 200 and ok.json() == {"user_id": "casey"}
    me = c.get("/api/whoami", headers=relay_headers(session=SID_CASEY)).json()
    assert me["user"]["relay_panel_id"] == "kitchen-panel" and me["panel_registered"] is False  # a member, not "the panel"
    # A guest session adds nothing even when someone is signed in - and not even if the freshness lookup (wrongly) named "guest".
    assert c.get("/api/member", headers=relay_headers(session=SID_GUEST)).status_code == 403
    FRESH["kitchen-panel"] = "guest"
    assert c.get("/api/member", headers=relay_headers(session=SID_GUEST)).status_code == 403
    assert c.get("/api/whoami", headers=relay_headers(session=SID_GUEST)).json()["user"]["panel_id"] == "kitchen-panel"
    FRESH["kitchen-panel"] = "casey"
    # CONTROL (the LAN): the same member session needs no freshness - it is the member's own browser session.
    FRESH.clear()
    assert c.get("/api/member", headers={"X-Session-ID": SID_CASEY}).status_code == 200


def test_t11_a_voice_session_is_not_a_fresh_sign_in_over_the_relay(reg, on, monkeypatch):
    # A voice session opens on a CLAIM the panel itself reports (identified_user_id + a score): over the relay the distrusted device
    # could mint it, so it must not unlock member routes. Only the PIN panel session counts until the voice lanes verify claims.
    import voice_session

    monkeypatch.setattr(voice_session, "enabled", lambda: True)
    monkeypatch.setattr(voice_session, "active_member", lambda panel_id: "casey" if panel_id == "kitchen-panel" else None)
    assert client(reg).get("/api/member", headers=relay_headers(session=SID_CASEY)).status_code == 403
    FRESH["kitchen-panel"] = "casey"  # control: the PIN session for the same member still opens it
    assert client(reg).get("/api/member", headers=relay_headers(session=SID_CASEY)).status_code == 200


def test_the_middleware_publishes_the_proven_panel_to_code_without_a_request_and_clears_it(reg, on):
    app = make_app(reg)

    @app.get("/panel-now")
    async def panel_now():
        return {"panel": relay_ingress.current_panel(), "casey_ok": await relay_ingress.member_allowed_here("casey")}

    c = TestClient(app, client=(NGINX, 1))
    assert c.get("/panel-now", headers=relay_headers()).json() == {"panel": "kitchen-panel", "casey_ok": False}
    FRESH["kitchen-panel"] = "casey"
    assert c.get("/panel-now", headers=relay_headers()).json() == {"panel": "kitchen-panel", "casey_ok": True}
    # not relayed (the LAN): no panel, and no freshness requirement - the websocket resolvers behave exactly as before
    FRESH.clear()
    assert c.get("/panel-now").json() == {"panel": None, "casey_ok": True}
    assert relay_ingress.current_panel() is None


def test_the_relay_gate_is_the_last_middleware_main_adds():
    src = (ROOT / "main.py").read_text()
    assert src.count("app.add_middleware(") == 3 and src.rindex("app.add_middleware(") == src.index("app.add_middleware(relay_ingress.RelayIngressMiddleware)"), \
        "the relay gate must be the LAST middleware added (outermost); a later one would run before it"
    # the real websocket handlers are exercised end to end in test_relay_ws_live.py (cross-panel subscription, stale member session)


# The forged voice-payload identity gap is closed by #2097 and pinned end to end in test_relay_voice_identity.py.


def test_an_open_relayed_websocket_is_closed_when_its_certificate_is_revoked(reg, on, monkeypatch):
    monkeypatch.setattr(relay_ingress, "WS_RECHECK_S", 0.0)
    app = make_app(reg)
    closed = []

    @app.websocket("/ws/echo")
    async def echo(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_text(await websocket.receive_text())
        except WebSocketDisconnect as exc:
            closed.append(exc.code)
            await websocket.close(code=1008)

    c = TestClient(app, client=(NGINX, 1))
    with c.websocket_connect("/ws/echo", headers=relay_headers()) as ws:
        ws.send_text("one")
        assert ws.receive_text() == "one"  # POSITIVE: the socket works while the certificate is bound
        reg.con.execute("UPDATE panel_client_certs SET revoked_at = 'now' WHERE fingerprint = ?", (FP_KITCHEN,))
        reg.con.commit()
        relay_ingress.invalidate_cache()
        ws.send_text("two")  # the next message re-proves the binding: revoked -> the server sees a disconnect
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert closed == [1008]


# ── an IDLE / push-only relayed socket (N1 of the #2095 review) ───────────────────────────────────────────────────────────
#
# ``/ws/push`` sends and (almost) never receives, so a re-proof that only runs on an inbound frame never runs. These cells use a
# push-only handler plus a broadcast route that behaves like ``broadcaster``: send to every open socket, drop the dead ones.

SECRET = {"event": "secret"}
IDLE_DEADLINE_S = 6.0   # the stub's own idle timeout: far past the shrunk re-proof interval, so only a missing fix reaches it


def _push_app(reg):
    app = make_app(reg)
    socks: list = []

    @app.websocket("/ws/idle")
    async def idle(websocket: WebSocket):
        await websocket.accept()
        socks.append(websocket)
        try:
            while True:
                await asyncio.wait_for(websocket.receive_text(), IDLE_DEADLINE_S)  # push-only: the client never sends a frame
        except WebSocketDisconnect:
            pass
        except asyncio.TimeoutError:
            await websocket.close(code=1000)  # a BROKEN fix ends here (1000, not 1008): the cell fails instead of hanging the suite
        finally:
            if websocket in socks:
                socks.remove(websocket)

    @app.post("/test/broadcast")
    async def broadcast():
        sent = 0
        for sock in list(socks):
            try:
                await sock.send_json(SECRET)
                sent += 1
            except Exception:  # noqa: BLE001 - a dead socket is dropped, as the real broadcaster does
                pass
        return {"sent": sent}

    return app


def _revoke_cert_in_db(reg):
    reg.con.execute("UPDATE panel_client_certs SET revoked_at = 'now' WHERE fingerprint = ?", (FP_KITCHEN,))
    reg.con.commit()


def _revoke_token_in_cache():
    panel_auth._token_cache[_hash(TOK_KITCHEN)]["revoked"] = 1


@pytest.mark.parametrize("revoke", ["certificate", "device_token"])
def test_an_idle_relayed_socket_is_closed_within_one_interval_of_a_revocation(reg, on, monkeypatch, revoke):
    monkeypatch.setattr(relay_ingress, "WS_RECHECK_S", 0.3)   # the 15 s production cadence, shrunk
    monkeypatch.setattr(relay_ingress, "WS_SEND_PROOF_TTL_S", 3600.0)  # isolate the TIMER: the send-time check cannot be what closes it
    with TestClient(_push_app(reg), client=(NGINX, 1)) as c:
        with c.websocket_connect("/ws/idle", headers=relay_headers()) as ws:
            assert c.post("/test/broadcast").json() == {"sent": 1}
            assert ws.receive_json() == SECRET                  # POSITIVE: a proven socket gets the broadcast
            # An OUT-OF-BAND revocation: no invalidate_cache() (another worker / a DB edit), so only the timer's registry read sees it.
            _revoke_cert_in_db(reg) if revoke == "certificate" else _revoke_token_in_cache()
            started = time.monotonic()
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()                               # the client sends nothing; the SERVER ends the socket
            assert closed.value.code == 1008                   # policy violation, not a normal close
            assert time.monotonic() - started < 0.3 + 2.0       # within one interval (plus scheduling slack)
        assert c.post("/test/broadcast").json() == {"sent": 0}  # and it is gone from the broadcast set


def test_a_broadcast_is_never_delivered_to_a_revoked_relayed_socket(reg, on, monkeypatch):
    monkeypatch.setattr(relay_ingress, "WS_RECHECK_S", 3600.0)  # isolate the SEND-time check: the timer cannot fire in this cell
    with TestClient(_push_app(reg), client=(NGINX, 1)) as c:
        with c.websocket_connect("/ws/idle", headers=relay_headers()) as ws:
            assert c.post("/test/broadcast").json() == {"sent": 1}
            assert ws.receive_json() == SECRET                  # POSITIVE
            admin = {"X-Session-ID": SID_ADMIN}
            assert c.delete(f"/api/panels/kitchen-panel/client-certs/{FP_KITCHEN}", headers=admin).status_code == 200
            assert c.post("/test/broadcast").json() == {"sent": 0}       # refused before the frame left the box
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()                               # the very next thing the panel sees is the close, never the event
            assert closed.value.code == 1008


def test_a_lan_socket_is_not_guarded_or_closed(reg, on, monkeypatch):
    monkeypatch.setattr(relay_ingress, "WS_RECHECK_S", 0.2)
    lan = {"X-Device-Token": TOK_KITCHEN}                       # through nginx's LAN block: the relay marker is always cleared there
    with TestClient(_push_app(reg), client=(NGINX, 1)) as c:
        with c.websocket_connect("/ws/idle", headers=lan) as lan_ws, c.websocket_connect("/ws/idle", headers=relay_headers()) as relayed_ws:
            assert c.post("/test/broadcast").json() == {"sent": 2}
            assert lan_ws.receive_json() == SECRET and relayed_ws.receive_json() == SECRET
            _revoke_cert_in_db(reg)
            relay_ingress.invalidate_cache()
            _revoke_token_in_cache()                            # even its device token is revoked: the LAN rules never re-prove a socket
            with pytest.raises(WebSocketDisconnect) as closed:
                relayed_ws.receive_json()                       # CONTROL: the relayed one goes ...
            assert closed.value.code == 1008
            time.sleep(0.6)                                     # ... three more intervals, and the LAN one is untouched
            assert c.post("/test/broadcast").json() == {"sent": 1}
            assert lan_ws.receive_json() == SECRET


async def test_a_revoked_socket_is_closed_even_while_a_send_is_stuck_behind_a_stalled_client(reg, on, monkeypatch):
    """Codex P1 on #2115: a data frame blocked by client backpressure holds the send lock, so the 1008 close used to queue behind
    network I/O and the 15 s bound was not kept. Driven at the ASGI level (a TestClient transport never stalls): the server's
    ``send`` of a data frame blocks forever, the close frame passes."""
    monkeypatch.setattr(relay_ingress, "WS_RECHECK_S", 0.3)
    monkeypatch.setattr(relay_ingress, "WS_SEND_PROOF_TTL_S", 3600.0)   # the timer, not the send-time check, is what notices
    sent, outcome = [], []
    in_flight, closed, stall = asyncio.Event(), asyncio.Event(), asyncio.Event()   # `stall` is never set: the client never drains

    async def app(scope, receive, send):
        await receive()                                                   # websocket.connect
        await send({"type": "websocket.accept"})
        try:
            await send({"type": "websocket.send", "text": "broadcast"})
            outcome.append("delivered")
        except OSError:
            outcome.append("send-abandoned")
        await asyncio.sleep(3600)                                         # the handler would idle on; the harness cancels it

    async def receive():
        if not sent:
            sent.append("connect")
            return {"type": "websocket.connect"}
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message["type"])
        if message["type"] == "websocket.send":
            in_flight.set()
            await stall.wait()                                            # backpressure: the transport never drains
        elif message["type"] == "websocket.close":
            sent.append(("code", message["code"]))
            closed.set()

    scope = {
        "type": "websocket", "path": "/ws/idle", "raw_path": b"/ws/idle", "query_string": b"", "scheme": "wss", "root_path": "",
        "client": (NGINX, 1), "server": ("zoe", 8000), "subprotocols": [],
        "headers": [(k.lower().encode(), v.encode()) for k, v in relay_headers().items()],
    }
    task = asyncio.ensure_future(relay_ingress.RelayIngressMiddleware(app)(scope, receive, send))
    try:
        await asyncio.wait_for(in_flight.wait(), 3.0)                      # POSITIVE: the frame is on the wire, the client is not draining
        assert not closed.is_set()                                         # ... and a proven socket is NOT closed
        reg.con.execute("UPDATE panel_client_certs SET revoked_at = 'now' WHERE fingerprint = ?", (FP_KITCHEN,))
        reg.con.commit()                                                   # out-of-band: only the timer's registry read sees it
        await asyncio.wait_for(closed.wait(), 0.3 + 3.0)                   # the close was NOT held behind the stuck frame
        assert ("code", 1008) in sent
        for _ in range(50):
            if outcome:
                break
            await asyncio.sleep(0.05)
        assert outcome == ["send-abandoned"]                               # the stuck frame was cancelled, not delivered late
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_the_proof_lives_under_a_namespaced_scope_key_not_a_bare_relay_ctx(reg, on):
    seen = []
    app = make_app(reg)

    @app.get("/test/state")
    async def state(request: Request):
        seen.append(dict(request.scope.get("state", {})))
        return {}

    client_ = TestClient(app, client=(NGINX, 1))
    assert client_.get("/test/state", headers=relay_headers()).status_code == 200
    assert relay_ingress._CTX_KEY in seen[0] and "relay_ctx" not in seen[0]   # an app's own `state["relay_ctx"]` cannot collide


def test_the_nginx_auth_gate_answers_only_for_a_proven_relayed_request(reg, on):
    c = client(reg)
    ok = c.get("/api/relay/check", headers=relay_headers())
    assert ok.status_code == 204
    assert c.get("/api/relay/check", headers=relay_headers(token=None)).status_code == 401           # no credential
    assert c.get("/api/relay/check", headers=relay_headers(fp=FP_REVOKED)).status_code == 401        # revoked certificate
    assert c.get("/api/relay/check", headers=relay_headers(fp=FP_HALL)).status_code == 401           # another panel's certificate
    assert c.get("/api/relay/check").status_code == 404                                              # the LAN never calls it


# ── the client-certificate binding ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fp,token,code", [
    (FP_HALL, TOK_KITCHEN, "relay_certificate_credential_mismatch"),    # another panel's certificate with this panel's token
    (FP_KITCHEN, TOK_HALL, "relay_certificate_credential_mismatch"),    # and the other way round
    (FP_UNKNOWN, TOK_KITCHEN, "relay_certificate_not_bound"),           # a certificate nobody bound
    (FP_REVOKED, TOK_KITCHEN, "relay_certificate_not_bound"),           # the stolen-panel switch
    (FP_INACTIVE, TOK_INACTIVE, "relay_certificate_not_bound"),         # panels.is_active = 0
    ("not-hex", TOK_KITCHEN, "relay_client_certificate_required"),      # a malformed fingerprint
    ("A" * 39, TOK_KITCHEN, "relay_client_certificate_required"),
])
def test_a_fingerprint_that_is_not_this_panels_is_refused(reg, on, fp, token, code):
    c = client(reg)
    r = c.get("/api/whoami", headers=relay_headers(fp=fp, token=token))
    assert r.status_code == 401 and r.json()["detail"] == code, r.text
    # Sensitivity: the matching pair on the same client/app is accepted, so the refusal came from the binding.
    assert c.get("/api/whoami", headers=relay_headers()).status_code == 200


def test_a_revoked_or_unknown_device_token_is_refused_outright(reg, on, monkeypatch):
    c = client(reg)
    assert c.get("/api/whoami", headers=relay_headers(token="tok-never-issued")).json()["detail"] == "relay_device_token_invalid"
    monkeypatch.setitem(panel_auth._token_cache, _hash(TOK_KITCHEN),
                        dict(panel_id="kitchen-panel", role="voice", scopes="[]", expires_at=None, revoked=1))
    r = c.get("/api/whoami", headers=relay_headers())
    assert r.status_code == 401 and r.json()["detail"] == "relay_device_token_invalid"  # the daemon gets 401 at once


def test_binding_and_revoking_through_the_admin_api_takes_effect_at_once(reg, on):
    c = client(reg)
    new_fp = "f" * 40
    assert c.get("/api/whoami", headers=relay_headers(fp=new_fp)).status_code == 401           # unknown: cached as unbound
    admin = {"X-Session-ID": SID_ADMIN}
    put = c.put(f"/api/panels/kitchen-panel/client-certs/{new_fp}", json={"note": "second cert"}, headers=admin)
    assert put.status_code == 200, put.text
    assert c.get("/api/whoami", headers=relay_headers(fp=new_fp)).status_code == 200            # the cache was invalidated
    assert c.put(f"/api/panels/hall-panel/client-certs/{new_fp}", headers=admin).status_code == 409   # never moved to another panel
    rev = c.delete(f"/api/panels/kitchen-panel/client-certs/{new_fp}", headers=admin)
    assert rev.status_code == 200
    assert c.get("/api/whoami", headers=relay_headers(fp=new_fp)).status_code == 401            # refused at once
    assert c.put(f"/api/panels/kitchen-panel/client-certs/{new_fp}", headers=admin).status_code == 409   # never un-revoked
    listed = c.get("/api/panels/kitchen-panel/client-certs", headers=admin).json()["certs"]
    assert any(x["fingerprint"] == new_fp and x["revoked_at"] for x in listed)
    # The admin API is admin-only and rejects a malformed fingerprint.
    assert c.put(f"/api/panels/kitchen-panel/client-certs/{new_fp}", headers={"X-Session-ID": SID_CASEY}).status_code == 403
    assert c.put("/api/panels/kitchen-panel/client-certs/zzz", headers=admin).status_code == 422
    assert c.put(f"/api/panels/ghost/client-certs/{'9' * 40}", headers=admin).status_code == 404


def test_an_unreadable_registry_is_a_503_never_a_pass(reg, on, monkeypatch):
    async def _boom(_fp):
        raise RuntimeError("database is down")

    monkeypatch.setattr(relay_ingress, "_fetch_bound_panel", _boom)
    relay_ingress.invalidate_cache()
    r = client(reg).get("/api/whoami", headers=relay_headers())
    assert r.status_code == 503 and r.json()["detail"] == "relay_registry_unavailable"


# ── the kiosk credential over the relay ────────────────────────────────────────────────────────────────────────────────────


def _kiosk_cookie(panel_id, raw_token):
    return kiosk.COOKIE_NAME + "=" + kiosk.mint_credential(panel_id, _hash(raw_token))


def test_the_kiosk_credential_is_a_valid_device_credential_bound_to_the_same_certificate(reg, on, monkeypatch):
    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "1")
    c = client(reg)
    cookie = {"Cookie": _kiosk_cookie("kitchen-panel", TOK_KITCHEN)}
    ok = c.get("/api/whoami", headers=relay_headers(token=None, extra=cookie))
    assert ok.status_code == 200 and ok.json()["user"]["panel_id"] == "kitchen-panel" and ok.json()["panel_registered"] is True
    # Another panel's certificate with this kiosk's cookie: refused.
    assert c.get("/api/whoami", headers=relay_headers(fp=FP_HALL, token=None, extra=cookie)).status_code == 401
    # A cookie and a token for DIFFERENT panels disagree: refused.
    r = c.get("/api/whoami", headers=relay_headers(token=TOK_HALL, fp=FP_HALL, extra=cookie))
    assert r.status_code == 401 and r.json()["detail"] == "relay_credentials_disagree"
    # The cookie dies with its token (the stolen-panel runbook) ...
    monkeypatch.setitem(panel_auth._token_cache, _hash(TOK_KITCHEN),
                        dict(panel_id="kitchen-panel", role="voice", scopes="[]", expires_at=None, revoked=1))
    assert c.get("/api/whoami", headers=relay_headers(token=None, extra=cookie)).status_code == 401
    # ... and with the flag off the cookie is ignored altogether (no device credential -> 401).
    monkeypatch.setitem(panel_auth._token_cache, _hash(TOK_KITCHEN),
                        dict(panel_id="kitchen-panel", role="voice", scopes="[]", expires_at=None, revoked=0))
    monkeypatch.delenv("ZOE_PANEL_KIOSK_CREDENTIAL")
    assert c.get("/api/whoami", headers=relay_headers(token=None, extra=cookie)).status_code == 401


def test_kiosk_enter_over_the_relay_needs_the_ticket_panels_certificate(reg, on, monkeypatch):
    from urllib.parse import urlencode

    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "1")
    c = client(reg)

    def enter(fp):
        ticket = c.post("/api/panels/kitchen-panel/kiosk-ticket", headers=relay_headers(fp=FP_KITCHEN)).json()["ticket"]
        return c.post("/api/panels/kiosk-enter", content=urlencode({"ticket": ticket}),
                      headers={"Content-Type": "application/x-www-form-urlencoded", **relay_headers(fp=fp, token=None)})

    good = enter(FP_KITCHEN)  # no device credential is needed on this one path: the ticket is the credential
    assert good.status_code == 303 and good.headers.get("set-cookie", "").startswith(kiosk.COOKIE_NAME + "=")
    bad = enter(FP_HALL)      # another panel's certificate spending the kitchen's ticket
    assert bad.status_code == 303 and "set-cookie" not in bad.headers
    none = c.post("/api/panels/kiosk-enter", content="ticket=x", headers={"Content-Type": "application/x-www-form-urlencoded",
                                                                          "X-Zoe-Ingress": "relay"})
    assert none.status_code == 401  # not even a certificate
    # CONTROL: any OTHER credential-less path with the same headers is refused.
    # (a fresh client: `c` now holds the kiosk cookie the successful redemption set, which IS a credential)
    assert client(reg).post("/api/panels/kiosk-enter/other", headers=relay_headers(fp=FP_KITCHEN, token=None)).status_code == 401
    # and with that cookie the same path gets past the gate to a plain 404 - the cookie alone is enough, as designed.
    assert c.post("/api/panels/kiosk-enter/other", headers=relay_headers(fp=FP_KITCHEN, token=None)).status_code == 404


# ── flag off: inert ────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_flag_off_the_relay_header_is_ignored_and_nothing_new_is_exposed(reg, monkeypatch):
    monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", NGINX)  # only the FLAG may keep it off: the peer is pinned and correct
    c = client(reg)
    # No credentials, relay marker from the proxy, flag OFF: judged exactly as the LAN - an anonymous guest, no 401 gate.
    r = c.get("/api/whoami", headers=relay_headers(fp=None, token=None))
    assert r.status_code == 200 and r.json()["user"]["user_id"] == "guest" and "ingress" not in r.json()["user"]
    # The address rule is untouched: a registered panel address still counts even if the marker is present.
    r = c.get("/api/whoami", headers={"X-Zoe-Ingress": "relay", "X-Real-IP": PANEL_IP, "X-Session-ID": SID_GUEST})
    assert r.json()["panel_registered"] is True and r.json()["client_ip"] == PANEL_IP
    # The device token still resolves exactly as #2089 (panel principal) with no "ingress" key.
    assert "ingress" not in c.get("/api/whoami", headers={"X-Device-Token": TOK_KITCHEN}).json()["user"]
    # The admin API and the origin are off.
    assert c.get("/api/panels/kitchen-panel/client-certs", headers={"X-Session-ID": SID_ADMIN}).status_code == 404
    monkeypatch.setenv("ZOE_RELAY_HOSTNAME", HOST)
    assert relay_ingress.origin() is None and relay_ingress.enabled() is False
    # Home control keeps the #2089 rule on the LAN (the panel may), untouched by the marker.
    assert panel_principal.home_control_scope() == "panel"


# ── origins ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_origin_is_derived_from_the_hostname_and_only_while_the_flag_is_on(monkeypatch):
    monkeypatch.setenv("ZOE_RELAY_INGRESS", "1")
    monkeypatch.setenv("ZOE_RELAY_HOSTNAME", HOST)
    assert relay_ingress.origin() == "https://" + HOST
    monkeypatch.setenv("ZOE_RELAY_HOSTNAME", "  " + HOST.upper() + ".  ")  # normalised
    assert relay_ingress.origin() == "https://" + HOST
    for bad in ("evil.example.com", "h1a2b3c4d5.zoe.internal.evil.com", "*.zoe.internal", "zoe.internal", "a.zoe.internal",
                "https://h1a2b3c4d5.zoe.internal", "h1a2b3c4d5.zoe.internal/x", ""):
        monkeypatch.setenv("ZOE_RELAY_HOSTNAME", bad)
        assert relay_ingress.origin() is None, bad
    monkeypatch.setenv("ZOE_RELAY_HOSTNAME", HOST)
    monkeypatch.setenv("ZOE_RELAY_INGRESS", "0")
    assert relay_ingress.origin() is None


def test_main_wires_the_origin_and_the_gate_as_the_outermost_middleware():
    src = (ROOT / "main.py").read_text()
    body = re.search(r"def _allowed_browser_origins\(\).*?\n\n\n", src, re.S).group(0)
    assert "relay_ingress.origin()" in body, "the push socket refuses the kiosk without the relay origin (2026-07-03 class)"
    cors = src.index("app.add_middleware(\n    CORSMiddleware")
    gate = src.index("app.add_middleware(relay_ingress.RelayIngressMiddleware)")
    assert gate > cors, "the last middleware added is the outermost: the relay gate must run before CORS and every route"
    assert "app.include_router(panel_relay_router)" in src


# ── never widen the internal-trust test ────────────────────────────────────────────────────────────────────────────────────


def test_internal_request_trust_is_not_widened_by_the_relay(reg, on):
    import inspect

    assert "relay" not in inspect.getsource(auth._is_internal_request).lower()
    nginx_peer = Request(dict(type="http", method="GET", path="/", client=(NGINX, 1), headers=[(b"x-zoe-ingress", b"relay")]))
    assert auth._is_internal_request(nginx_peer) is False, "a relayed request reaches zoe-data from the nginx container, never loopback"
    loopback = Request(dict(type="http", method="GET", path="/", client=("127.0.0.1", 1), headers=[]))
    assert auth._is_internal_request(loopback) is True  # unchanged


# ── the migration ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_migration_chain_is_linear_and_rerun_safe():
    versions = ROOT / "alembic/versions"
    revs, downs = {}, set()
    for f in versions.glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        if r:
            revs[r.group(1)] = f.name
        if d:
            downs.add(d.group(1))
    heads = set(revs) - downs
    assert len(heads) == 1, f"more than one alembic head {sorted(heads)}: 0059 chains onto 0057 (#2109); expected until #2109 is on main - see the migration docstring"
    assert "0059" in revs
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("CREATE TABLE panels (panel_id TEXT PRIMARY KEY)")
    _migration("upgrade", con)
    _migration("upgrade", con)  # IF NOT EXISTS: a rerun is a no-op
    cols = {r[1] for r in con.execute("PRAGMA table_info(panel_client_certs)")}
    assert cols == {"fingerprint", "panel_id", "note", "created_at", "revoked_at"}
    con.execute("INSERT INTO panels VALUES ('p1')")
    con.execute("INSERT INTO panel_client_certs VALUES ('" + "1" * 40 + "', 'p1', '', 'now', NULL)")
    with pytest.raises(sqlite3.IntegrityError):  # a fingerprint binds ONE panel, for good
        con.execute("INSERT INTO panel_client_certs VALUES ('" + "1" * 40 + "', 'p1', '', 'now', NULL)")
    con.execute("DELETE FROM panels WHERE panel_id = 'p1'")  # rows go with their panel
    assert con.execute("SELECT count(*) FROM panel_client_certs").fetchone()[0] == 0
    _migration("downgrade", con)
    assert not list(con.execute("SELECT name FROM sqlite_master WHERE name = 'panel_client_certs'"))
