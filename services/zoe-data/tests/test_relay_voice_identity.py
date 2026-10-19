"""The relay x voice-path identity gap, closed end to end (#2095 x #2097): over the relay a stolen panel's certificate + device token
must not become a MEMBER by writing ``identified_user_id`` or another panel's ``panel_id`` into ``/api/voice/command``.

The relay gate (``RelayIngressMiddleware``) is REAL, the voice router, ``_require_voice_auth``, ``_lane_panel_id`` and the identity
block of ``voice_command`` are REAL; the brain, TTS and the panel tables are faked exactly as ``test_voice_path_identity.py`` does.
Each refusal has its positive control. Invented data. Also pins that ``ZOE_HA_CONTROL_SCOPE=open`` (a LAN rollback) never reaches a
relayed turn, and that ``relay_ingress.is_relay`` keeps the name and signature ``voice_tts._request_via_relay`` calls.
"""
from __future__ import annotations

import hashlib
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import panel_principal
import relay_ingress
import routers.voice_tts as voice_tts
from routers import panel_auth
from test_voice_identity_freshness import IDLE_WINDOW_S, _ts, _wire
from test_voice_path_identity import PANEL_A, PANEL_B, _KeyedStore

pytestmark = pytest.mark.ci_safe

NGINX = "172.18.0.5"
FP_A = "a" * 40
FP_B = "b" * 40
TOKEN_A = "tok-voice-panel-a"
TOKEN_B = "tok-voice-panel-b"


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _relay_on(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ZOE_RELAY_INGRESS", "1")
    monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", NGINX)
    for raw, panel in ((TOKEN_A, PANEL_A), (TOKEN_B, PANEL_B)):
        monkeypatch.setitem(panel_auth._token_cache, _hash(raw),
                            dict(panel_id=panel, role="voice", scopes="[]", expires_at=None, revoked=0))

    async def _bound(fp):  # the certificate registry (its real SQL is covered in test_relay_ingress.py)
        return {FP_A: PANEL_A, FP_B: PANEL_B}.get(fp)

    monkeypatch.setattr(relay_ingress, "_fetch_bound_panel", _bound)
    relay_ingress.invalidate_cache()
    token = panel_principal._home_control_turn.set(None)
    yield
    panel_principal._home_control_turn.reset(token)
    relay_ingress.invalidate_cache()


def _headers(fp=FP_A, token=TOKEN_A):
    h = {"X-Zoe-Ingress": "relay"}
    if fp:
        h["X-Zoe-Client-Cert-Fp"] = fp
    if token:
        h["X-Device-Token"] = token
    return h


def _client(store):
    app = FastAPI()
    app.add_middleware(relay_ingress.RelayIngressMiddleware)
    app.include_router(voice_tts.router)

    async def _db():
        yield store

    app.dependency_overrides[voice_tts.get_db] = _db
    return TestClient(app, client=(NGINX, 50000))


def _command(client, seen, headers, **payload):
    seen["skybridge_user"].clear()
    r = client.post("/api/voice/command", json={"text": "what's on my calendar", **payload}, headers=headers)
    return r, (seen["skybridge_user"][-1] if seen["skybridge_user"] else None)


def test_a_relayed_voice_command_ignores_a_forged_panel_id_and_identified_user_id(monkeypatch):
    store = _KeyedStore()
    seen = _wire(monkeypatch, store)
    store.sessions[PANEL_B] = {"user_id": "bob", "last_seen_at": _ts(30)}   # Bob just signed in at panel B
    client = _client(store)

    # FORGED: panel A's certificate + token name panel B and claim to be Bob -> the turn is nobody's (a guest), not Bob.
    r, who = _command(client, seen, _headers(), panel_id=PANEL_B, identified_user_id="bob")
    assert r.status_code == 200, r.text
    assert who == "guest", "a relayed panel borrowed another panel's member / a raw identity claim"
    r, who = _command(client, seen, _headers(), identified_user_id="bob")
    assert who == "guest"

    # POSITIVE CONTROLS: panel B's OWN certificate + token is Bob (his fresh PIN session at B); panel A's own fresh member is alice.
    r, who = _command(client, seen, _headers(FP_B, TOKEN_B), panel_id=PANEL_B)
    assert r.status_code == 200 and who == "bob"
    store.sessions[PANEL_A] = {"user_id": "alice", "last_seen_at": _ts(IDLE_WINDOW_S - 60)}
    r, who = _command(client, seen, _headers(), panel_id=PANEL_B, identified_user_id="bob")
    assert who == "alice", "the token's own panel decides the member"

    # The relay gate still comes first: a certificate that is not the token's panel never reaches the voice lane.
    r, who = _command(client, seen, _headers(FP_B, TOKEN_A), panel_id=PANEL_B)
    assert r.status_code == 401 and r.json()["detail"] == "relay_certificate_credential_mismatch"
    assert who is None, "the voice lane must not have run"


def test_the_voice_lane_sees_the_request_as_relayed(monkeypatch):
    store = _KeyedStore()
    _wire(monkeypatch, store)
    from starlette.requests import Request

    def req(headers, peer=NGINX):
        return Request({"type": "http", "method": "POST", "path": "/api/voice/command", "client": (peer, 1),
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    assert voice_tts._request_via_relay(req({"X-Zoe-Ingress": "relay"})) is True
    assert voice_tts._request_via_relay(req({})) is False                       # the LAN
    assert voice_tts._request_via_relay(req({"X-Zoe-Ingress": "relay"}, peer="172.18.0.99")) is False  # not the nginx container
    # the seam #2097 calls: this exact name and signature
    assert list(inspect.signature(relay_ingress.is_relay).parameters) == ["conn"]


def test_ha_control_open_rollback_never_reaches_a_relayed_turn(monkeypatch):
    panel_principal.record_home_control(False)           # the turn's caller may not drive the house
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert panel_principal.home_control_refused("test") is False, "LAN: the rollback still opens it"
    tok = relay_ingress._relay_panel.set(PANEL_A)        # ... but this turn is relayed (the middleware sets this)
    try:
        assert panel_principal.home_control_refused("test") is True
    finally:
        relay_ingress._relay_panel.reset(tok)
