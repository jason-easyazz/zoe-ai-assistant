"""CSWSH (cross-site WebSocket hijacking) origin-guard tests for zoe-data.

Proves every WebSocket endpoint validates the handshake Origin header against the
shared allowlist:
  - a foreign browser Origin is rejected (handshake closed before accept),
  - an allowed Origin connects,
  - the documented no-Origin native-client case (native kiosk/voice panel, CLI,
    internal services) connects.

The guard logic lives in main._ws_origin_allowed / _enforce_ws_origin and is the
single check shared by all endpoints, so the helper tests cover the policy for
every endpoint; the TestClient tests prove the wiring end-to-end on both an
unauthenticated endpoint (/ws/voice/) and an authenticated one (/api/lists/ws).
"""
import os
import sys

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# conftest puts both services/zoe-data and services/zoe-auth on sys.path (and
# both define a top-level ``main`` module). Force zoe-data's to win here.
_ZOE_DATA = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "services", "zoe-data")
)
if _ZOE_DATA in sys.path:
    sys.path.remove(_ZOE_DATA)
sys.path.insert(0, _ZOE_DATA)
sys.modules.pop("main", None)
import main  # noqa: E402


class _FakeWS:
    """Minimal stand-in for starlette WebSocket for unit-testing the guard."""

    def __init__(self, origin=None):
        self.headers = {} if origin is None else {"origin": origin}

        class _URL:
            path = "/ws/test"

        self.url = _URL()
        self.closed_code = None

    async def close(self, code=1000):
        self.closed_code = code


# --- helper-level policy tests (cover the shared guard for every endpoint) ----

def test_allowed_origin_passes():
    assert main._ws_origin_allowed(_FakeWS("https://zoe.local")) is True


def test_foreign_origin_blocked():
    assert main._ws_origin_allowed(_FakeWS("https://evil.example")) is False


def test_missing_origin_allowed_for_native_clients():
    # Browsers always send Origin on a WS handshake; a missing Origin is a
    # non-browser client (native kiosk/voice panel) and must NOT be blocked.
    assert main._ws_origin_allowed(_FakeWS(origin=None)) is True


def test_env_extra_origins(monkeypatch):
    monkeypatch.setenv("ZOE_ALLOWED_WS_ORIGINS", "https://panel.lan, http://10.0.0.5:8080")
    assert main._ws_origin_allowed(_FakeWS("https://panel.lan")) is True
    assert main._ws_origin_allowed(_FakeWS("http://10.0.0.5:8080")) is True
    assert main._ws_origin_allowed(_FakeWS("https://other.lan")) is False


@pytest.mark.asyncio
async def test_enforce_closes_on_foreign_origin():
    ws = _FakeWS("https://evil.example")
    assert await main._enforce_ws_origin(ws) is False
    assert ws.closed_code == 1008  # policy violation


@pytest.mark.asyncio
async def test_enforce_passes_allowed_and_missing():
    assert await main._enforce_ws_origin(_FakeWS("https://zoe.local")) is True
    assert await main._enforce_ws_origin(_FakeWS(origin=None)) is True


# --- end-to-end wiring tests via TestClient --------------------------------

@pytest.fixture
def client():
    return TestClient(main.app)


def test_voice_ws_rejects_foreign_origin(client):
    # /ws/voice/ is unauthenticated; the origin guard is its only handshake gate.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/voice/", headers={"origin": "https://evil.example"}):
            pass


def test_voice_ws_accepts_allowed_origin(client):
    with client.websocket_connect("/ws/voice/", headers={"origin": "https://zoe.local"}) as ws:
        msg = ws.receive_json()
        assert msg == {"type": "state", "state": "ambient"}


def test_voice_ws_accepts_missing_origin(client):
    # Native kiosk/voice client sends no Origin — must still connect.
    with client.websocket_connect("/ws/voice/") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "state", "state": "ambient"}


def test_authenticated_ws_rejects_foreign_origin_before_auth(client, monkeypatch):
    # Origin guard runs BEFORE session resolution: a foreign origin is rejected
    # even with a (mocked) valid session, and the resolver is never consulted.
    called = {"resolved": False}

    async def _fake_resolve(session_id):
        called["resolved"] = True
        return {"user_id": "u1", "role": "member"}

    monkeypatch.setattr(main, "_resolve_ws_session", _fake_resolve)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/lists/ws",
            headers={"origin": "https://evil.example", "X-Session-ID": "sess-1"},
        ):
            pass
    assert called["resolved"] is False


def test_authenticated_ws_accepts_allowed_origin(client, monkeypatch):
    async def _fake_resolve(session_id):
        return {"user_id": "u1", "role": "member"}

    monkeypatch.setattr(main, "_resolve_ws_session", _fake_resolve)
    with client.websocket_connect(
        "/api/lists/ws",
        headers={"origin": "https://zoe.local", "X-Session-ID": "sess-1"},
    ) as ws:
        # Successful accept emits the broadcaster handshake frame for the channel.
        assert ws.receive_json() == {"type": "connected", "channel": "lists", "sequence": 0}
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}
