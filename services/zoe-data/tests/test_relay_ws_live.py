"""The REAL websocket handlers of ``main.app`` behind the REAL relay gate: cross-panel subscription and stale member sessions.

``test_relay_ingress.py`` proves the gate and the principal rules with a stub app; this file drives ``main.app`` itself (its real
``/ws/push``, ``/api/lists/ws``, ``_resolve_ws_session``, ``_resolve_subscribable_panel`` and middleware stack), because the review
of #2095 found that a test which only greps ``main.py`` for helper names passes while the handlers are wrong.

Not ``ci_safe``: importing ``main`` needs the full dependency set (Jetson lane). It skips, with the reason, where ``main`` cannot be
imported. Only zoe-auth, the panel/certificate registry (real alembic-0056 SQL on SQLite) and the push loop's DB catch-up are
replaced. Negative controls: each refusal has the positive control next to it, and the LAN control proves the refusal is the relay
rule. Invented data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import relay_ingress  # noqa: E402
from test_relay_ingress import FRESH, NGINX, SID_CASEY, TOK_KITCHEN, _env, on, relay_headers, reg  # noqa: E402,F401


@pytest.fixture(scope="module")
def zoe_main():
    try:
        import main
    except Exception as exc:  # noqa: BLE001 - the full dependency set is a Jetson-lane thing
        pytest.skip("main.app is not importable here (%s: %s)" % (type(exc).__name__, exc))
    return main


@pytest.fixture
def app_client(zoe_main, reg, on, monkeypatch):
    async def _no_loop(*_a, **_k):  # the push loop's DB catch-up is not under test; the handshake decisions are
        return None

    monkeypatch.setattr(zoe_main, "_run_push_ws_loop", _no_loop)
    return TestClient(zoe_main.app, client=(NGINX, 50000))


def _connect_ok(client, url, headers):
    with client.websocket_connect(url, headers=headers):
        return True


def _refused(client, url, headers):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(url, headers=headers):
            pass
    return e.value.code


def test_a_relayed_panel_cannot_subscribe_to_another_panels_channel(app_client):
    own = relay_headers()  # certificate + device token of the KITCHEN panel
    assert _connect_ok(app_client, "/ws/push?panel_id=kitchen-panel", own)                    # POSITIVE: its own channel
    assert _refused(app_client, "/ws/push?panel_id=hall-panel", own) == 1008                 # another panel's channel
    # CONTROL: the same device token and query on the LAN (no relay marker) still subscribes - so the refusal is the relay rule,
    # not something the handler always did.
    assert _connect_ok(app_client, "/ws/push?panel_id=hall-panel", {"X-Device-Token": TOK_KITCHEN})


def test_a_stale_member_session_does_not_open_a_member_socket_over_the_relay(app_client):
    url = "/api/lists/ws"
    member = relay_headers(session=SID_CASEY)
    assert _refused(app_client, url, member) == 1008                                          # a valid session, no fresh sign-in
    FRESH["kitchen-panel"] = "jo"
    assert _refused(app_client, url, member) == 1008                                          # someone ELSE signed in at the panel
    FRESH["kitchen-panel"] = "casey"
    assert _connect_ok(app_client, url, member)                                               # POSITIVE: a fresh sign-in at THIS panel
    FRESH.clear()
    assert _connect_ok(app_client, url, {"X-Session-ID": SID_CASEY})                         # CONTROL: the LAN needs no freshness


def test_an_idle_push_socket_of_a_revoked_certificate_is_closed_by_the_real_handler(zoe_main, reg, on, monkeypatch):
    """N1 of the #2095 review on the REAL ``/ws/push`` handler and its REAL receive loop (not the stub): the panel is subscribed and
    sends nothing, its certificate is revoked out of band, and the server ends the socket with 1008 within one interval."""
    import time

    from push import broadcaster

    monkeypatch.setattr(relay_ingress, "WS_RECHECK_S", 0.3)
    monkeypatch.setattr(zoe_main, "WS_IDLE_TIMEOUT_SECONDS", 6.0)               # so a broken fix fails the cell instead of hanging it
    before = broadcaster.panel_subscriber_count("kitchen-panel")                 # earlier cells (loop stubbed out) leave sockets behind
    client = TestClient(zoe_main.app, client=(NGINX, 50000))
    with client.websocket_connect("/ws/push?panel_id=kitchen-panel", headers=relay_headers()) as ws:
        assert ws.receive_json()["type"] == "connected"                         # POSITIVE: subscribed, then idle
        assert broadcaster.panel_subscriber_count("kitchen-panel") == before + 1
        reg.con.execute("UPDATE panel_client_certs SET revoked_at = 'now' WHERE fingerprint = ?", ("a" * 40,))
        reg.con.commit()
        started = time.monotonic()
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()                                                   # a missing fix ends at the 6 s idle deadline (1001)
        assert closed.value.code == 1008 and time.monotonic() - started < 0.3 + 2.0
    assert broadcaster.panel_subscriber_count("kitchen-panel") == before         # the handler's cleanup ran
