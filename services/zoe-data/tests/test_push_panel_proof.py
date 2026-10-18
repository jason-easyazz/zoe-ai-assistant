"""Only the panel joins the panel's push channel (security follow-up 2026-10-19).

``/ws/push?panel_id=<id>`` admitted ANY socket with no session to a registered panel that allows guests, so a phone on the home
Wi-Fi could join ``panel_zoe-touch-pi`` and receive the per-turn voice text, member cards and forms (#2129's residual risk). Now a
socket WITHOUT a member session must prove it is THAT panel: its device token (only its own panel), its kiosk credential naming
the panel, or the panel's registered address via nginx's X-Real-IP from a trusted proxy. The live kiosk connects with no session
from its registered address, so it keeps working with no kiosk-side change. A member socket can no longer join a panel channel
BY NAME (``?channel=panel_<id>``).

Drives the real ``main.websocket_push`` -> ``_resolve_subscribable_panel`` -> ``_panel_push_proven`` ->
``panel_principal.request_is_this_panel`` with a fake handshake (zoe-auth, the panel registry and the broadcaster faked).
Negative control: ``ZOE_PANEL_PUSH_PROOF=off`` restores the old behaviour, under which the same stranger gets in. Invented data;
TEST-NET addresses.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
import panel_kiosk_credential  # noqa: E402
import panel_principal  # noqa: E402
import routers.panel_auth as panel_auth  # noqa: E402

pytestmark = pytest.mark.ci_safe

PANEL, OTHER_PANEL = "zoe-touch-pi", "kitchen-panel"
PANEL_IP, OTHER_IP, PHONE_IP, PROXY = "192.0.2.61", "192.0.2.62", "192.0.2.77", "172.18.0.1"
PANEL_TOKEN, OTHER_TOKEN = "tok-touch-pi", "tok-kitchen"
MEMBER_SID, ADMIN_SID, GUEST_SID = "sid-member", "sid-admin", "sid-guest"
REGISTRY = {PANEL: PANEL_IP, OTHER_PANEL: OTHER_IP}  # both active + allow guests
LOCKED_PANEL, LOCKED_IP = "study-panel", "192.0.2.63"     # active, registered, but does NOT allow guests
ACTIVE = {**REGISTRY, LOCKED_PANEL: LOCKED_IP}
NO_PANEL_TOKEN = "tok-no-panel"


class _WS:
    def __init__(self, peer, headers=None, query=None, cookies=None):
        self.client = SimpleNamespace(host=peer, port=50000)
        self.headers = Headers(headers=headers or {})
        self.query_params = query or {}
        self.cookies = cookies or {}
        self.url = SimpleNamespace(path="/ws/push")
        self.closed = None

    async def accept(self):
        pass

    async def send_json(self, _data):
        pass

    async def receive_text(self):
        raise main.WebSocketDisconnect()

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class _Broadcaster:
    def __init__(self):
        self.panels, self.channels = [], []

    async def connect_panel(self, _ws, panel_id):
        self.panels.append(panel_id)

    async def connect(self, _ws, channel, user_id=None):
        self.channels.append(channel)

    def disconnect(self, _ws, channel="all"):
        pass

    async def catchup(self, _ws, _seq):
        pass


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_PANEL_PUSH_PROOF", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_PANEL_KIOSK_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "test-internal-token")

    async def _session(sid):
        return {MEMBER_SID: {"user_id": "casey", "role": "user"}, ADMIN_SID: {"user_id": "alex", "role": "admin"},
                GUEST_SID: {"user_id": "guest", "role": "guest"}}.get(sid)

    monkeypatch.setattr(main, "_resolve_ws_session", _session)

    async def _allows_guest(panel_id):
        return panel_id in REGISTRY

    monkeypatch.setattr(main, "_panel_allows_guest_push", _allows_guest)

    async def _bound(user_id, _sid, panel_id):
        if user_id == "casey" and panel_id == PANEL:
            return PANEL  # casey's session is bound to the kiosk
        if user_id == "guest" and panel_id == LOCKED_PANEL:
            return LOCKED_PANEL  # the guest session has a bound row on the members-only panel
        return None

    monkeypatch.setattr(main, "_resolve_bound_panel", _bound)

    async def _with_registry(_db, check, value):
        if check is panel_principal._panel_at_address:
            pid, _, ip = value.partition("\n")
            return ACTIVE.get(pid) == ip
        if check is panel_principal._panel_is_active:
            return value in ACTIVE
        return False

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)
    monkeypatch.setitem(panel_auth._token_cache, panel_auth._hash_token(NO_PANEL_TOKEN),
                        {"panel_id": "", "role": "voice-daemon", "expires_at": None, "revoked": 0})
    for tok, pid in ((PANEL_TOKEN, PANEL), (OTHER_TOKEN, OTHER_PANEL)):
        monkeypatch.setitem(panel_auth._token_cache, panel_auth._hash_token(tok),
                            {"panel_id": pid, "role": "voice-daemon", "expires_at": None, "revoked": 0})
    bc = _Broadcaster()
    monkeypatch.setattr(main, "broadcaster", bc)
    return bc


async def _connect(peer, headers=None, panel_id=PANEL, channel="all", session=None, cookies=None):
    ws = _WS(peer, headers, {"session_id": session} if session else {}, cookies)
    await main.websocket_push(ws, channel=channel, panel_id=panel_id)
    return ws


# ── the kiosk keeps working; strangers do not get in ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_live_kiosk_shape_is_admitted(_env):
    """No session, no token, from its registered address through nginx: exactly how the live kiosk subscribes."""
    ws = await _connect(PROXY, {"X-Real-IP": PANEL_IP})
    assert ws.closed is None and _env.panels == [PANEL]


@pytest.mark.parametrize("peer,headers,session,label", [
    (PROXY, {"X-Real-IP": PHONE_IP}, None, "a phone on the Wi-Fi, through nginx"),
    (PHONE_IP, {"X-Real-IP": PANEL_IP}, None, "a forged X-Real-IP straight at :8000"),
    (PROXY, {"X-Real-IP": PANEL_IP, "cf-ray": "abc"}, None, "the panel's address over the Cloudflare tunnel"),
    (PROXY, {"X-Real-IP": PHONE_IP}, GUEST_SID, "a minted guest session from a phone"),
    (PROXY, {"X-Real-IP": PHONE_IP}, "sid-invalid", "an invalid session"),
    (PROXY, {"X-Real-IP": PHONE_IP, "X-Device-Token": "not-a-token"}, None, "an unknown device token"),
    (PROXY, {"X-Real-IP": OTHER_IP}, None, "ANOTHER registered panel's address"),
])
@pytest.mark.asyncio
async def test_a_socket_that_is_not_the_panel_gets_no_panel_channel(_env, peer, headers, session, label):
    ws = await _connect(peer, headers, session=session)
    assert ws.closed and ws.closed[0] == 1008, label
    assert _env.panels == [], label


@pytest.mark.asyncio
async def test_negative_control_off_restores_the_hole(_env, monkeypatch):
    """Without the check the same stranger gets in - so the refusals above are the check, not the fixture."""
    monkeypatch.setenv("ZOE_PANEL_PUSH_PROOF", "off")
    ws = await _connect(PROXY, {"X-Real-IP": PHONE_IP})
    assert ws.closed is None and _env.panels == [PANEL]


@pytest.mark.asyncio
async def test_shadow_admits_and_says_it_would_refuse(_env, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_PANEL_PUSH_PROOF", "shadow")
    with caplog.at_level(logging.WARNING, logger="main"):
        ws = await _connect(PROXY, {"X-Real-IP": "192.0.2.199"})
    assert ws.closed is None and _env.panels == [PANEL]
    assert any("WOULD REFUSE" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_unknown_mode_is_enforce(_env, monkeypatch):
    monkeypatch.setenv("ZOE_PANEL_PUSH_PROOF", "shadw")
    ws = await _connect(PROXY, {"X-Real-IP": PHONE_IP})
    assert ws.closed and _env.panels == []


@pytest.mark.asyncio
async def test_a_guest_session_from_the_kiosk_address_is_admitted(_env):
    """The non-home touch pages connect with the kiosk's guest session; from the kiosk itself that still works."""
    ws = await _connect(PROXY, {"X-Real-IP": PANEL_IP}, session=GUEST_SID)
    assert ws.closed is None and _env.panels == [PANEL]


# ── device token: its own panel only ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_device_token_joins_its_own_panel_from_anywhere(_env):
    ws = await _connect(PHONE_IP, {"X-Device-Token": PANEL_TOKEN})
    assert ws.closed is None and _env.panels == [PANEL]


@pytest.mark.asyncio
async def test_a_device_token_cannot_join_another_panel(_env, monkeypatch):
    ws = await _connect(PHONE_IP, {"X-Device-Token": OTHER_TOKEN})
    assert ws.closed and ws.closed[0] == 1008 and _env.panels == []
    monkeypatch.setenv("ZOE_PANEL_PUSH_PROOF", "shadow")  # shadow admits (and logs)
    ws = await _connect(PHONE_IP, {"X-Device-Token": OTHER_TOKEN})
    assert ws.closed is None and _env.panels == [PANEL]


@pytest.mark.asyncio
async def test_a_device_token_that_names_no_panel_joins_none(_env):
    ws = await _connect(PHONE_IP, {"X-Device-Token": NO_PANEL_TOKEN})
    assert ws.closed and ws.closed[0] == 1008 and _env.panels == []


# ── proof is identity, not guest policy: a guest bound to a members-only panel ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_bound_guest_on_a_members_only_panel_passes_from_that_panel_only(_env):
    """The reclaim-window case: a guest session with a bound row on a panel that disallows guests. From the panel's own
    address it keeps its channel (proof does not re-apply allow_guest); from anywhere else it gets none."""
    ws = await _connect(PROXY, {"X-Real-IP": LOCKED_IP}, panel_id=LOCKED_PANEL, session=GUEST_SID)
    assert ws.closed is None and _env.panels == [LOCKED_PANEL]
    ws = await _connect(PROXY, {"X-Real-IP": PHONE_IP}, panel_id=LOCKED_PANEL, session=GUEST_SID)
    assert ws.closed and _env.panels == [LOCKED_PANEL]
    ws = await _connect(PROXY, {"X-Real-IP": LOCKED_IP}, panel_id=LOCKED_PANEL)   # session-less: allow_guest still applies
    assert ws.closed and _env.panels == [LOCKED_PANEL]


# ── kiosk credential (flag-dark today): a cookie rides the WebSocket handshake, nothing in the URL ─────────────────────────

@pytest.mark.asyncio
async def test_the_kiosk_credential_cookie_proves_its_own_panel(_env, monkeypatch):
    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "1")
    cred = panel_kiosk_credential.mint_credential(PANEL, panel_auth._hash_token(PANEL_TOKEN))
    jar = {panel_kiosk_credential.COOKIE_NAME: cred}
    ws = await _connect(PROXY, {"X-Real-IP": PHONE_IP}, cookies=jar)              # off the registered address, e.g. a VPN
    assert ws.closed is None and _env.panels == [PANEL]
    ws = await _connect(PROXY, {"X-Real-IP": PHONE_IP}, panel_id=OTHER_PANEL, cookies=jar)
    assert ws.closed and _env.panels == [PANEL]                                    # it names zoe-touch-pi, not the kitchen
    ws = await _connect(PROXY, {"X-Real-IP": PHONE_IP, "cf-ray": "abc"}, cookies=jar)
    assert ws.closed                                                               # never over the tunnel
    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "0")
    ws = await _connect(PROXY, {"X-Real-IP": PHONE_IP}, cookies=jar)
    assert ws.closed                                                               # flag off: the cookie is ignored


# ── member sockets ──────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_member_session_bound_to_the_panel_needs_no_address(_env):
    ws = await _connect(PHONE_IP, session=MEMBER_SID)
    assert ws.closed is None and _env.panels == [PANEL]
    ws = await _connect(PHONE_IP, session=MEMBER_SID, panel_id=OTHER_PANEL)       # not bound there
    assert ws.closed and _env.panels == [PANEL]


@pytest.mark.parametrize("channel", ["panel_zoe-touch-pi", "PANEL_zoe-touch-pi", "panel:none"])
@pytest.mark.asyncio
async def test_a_member_cannot_join_a_panel_channel_by_name(_env, channel):
    ws = await _connect(PHONE_IP, session=MEMBER_SID, panel_id="", channel=channel)
    assert ws.closed and ws.closed[0] == 1008 and _env.channels == []


@pytest.mark.asyncio
async def test_members_keep_their_own_channels_and_admins_keep_panel_channels(_env):
    ws = await _connect(PHONE_IP, session=MEMBER_SID, panel_id="", channel="all")  # the desktop's socket
    assert ws.closed is None and _env.channels == ["all"]
    ws = await _connect(PHONE_IP, session=ADMIN_SID, panel_id="", channel="panel_zoe-touch-pi")
    assert ws.closed is None and _env.channels == ["all", "panel_zoe-touch-pi"]


# ── a REAL Starlette WebSocket handshake (headers, client address and cookies as the ASGI server hands them) ───────────────

def _real_client(peer, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import push

    real = push.PushBroadcaster()
    monkeypatch.setattr(main, "broadcaster", real)
    app = FastAPI()
    app.add_api_websocket_route("/ws/push", main.websocket_push)
    return TestClient(app, client=(peer, 50000)), real


def test_real_handshake_the_kiosk_holds_one_subscriber_and_a_stranger_none(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    client, real = _real_client(PROXY, monkeypatch)
    with client.websocket_connect(f"/ws/push?panel_id={PANEL}", headers={"X-Real-IP": PANEL_IP}) as ws:
        assert ws.receive_json()["type"] == "connected"
        assert real.panel_push_status(PANEL)["subscribers"] == 1          # what zoe_ground_truth / push-status reads
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(f"/ws/push?panel_id={PANEL}", headers={"X-Real-IP": PHONE_IP}) as ws:
            ws.receive_json()
    assert refused.value.code == 1008
    assert real.panel_push_status(PANEL)["subscribers"] == 0


def test_real_handshake_the_kiosk_credential_rides_the_cookie_header(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "1")
    cred = panel_kiosk_credential.mint_credential(PANEL, panel_auth._hash_token(PANEL_TOKEN))
    client, _ = _real_client(PROXY, monkeypatch)
    hdr = {"X-Real-IP": PHONE_IP, "Cookie": f"{panel_kiosk_credential.COOKIE_NAME}={cred}"}
    with client.websocket_connect(f"/ws/push?panel_id={PANEL}", headers=hdr) as ws:
        assert ws.receive_json()["panel_id"] == PANEL
    with pytest.raises(WebSocketDisconnect):                                 # control: the same socket without the cookie
        with client.websocket_connect(f"/ws/push?panel_id={PANEL}", headers={"X-Real-IP": PHONE_IP}) as ws:
            ws.receive_json()


# ── the proof itself ────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_is_this_panel_is_bound_to_the_panel_id():
    kiosk = _WS(PROXY, {"X-Real-IP": PANEL_IP})
    assert await panel_principal.request_is_this_panel(kiosk, PANEL)
    assert not await panel_principal.request_is_this_panel(kiosk, OTHER_PANEL)
    assert not await panel_principal.request_is_this_panel(kiosk, "")
    assert not await panel_principal.request_is_this_panel(None, PANEL)


@pytest.mark.asyncio
async def test_no_socket_cannot_prove_anything():
    assert await main._panel_push_proven(None, PANEL) is False
