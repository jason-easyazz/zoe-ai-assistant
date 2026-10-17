"""Voice-path identity + home control at every turn entry point (security fix 2026-10-18, fix/voice-path-identity).

Three live holes, each pinned here with a negative control (the same rig DOES allow the legitimate case):

1. Anonymous home control through the turn entry points. ``/api/ha/control`` refused an anonymous LAN caller (#2089), but
   "turn off the light" to ``POST /api/chat`` or ``/ws/voice/`` still reached Home Assistant through ``smart_home_service`` /
   ``intent_router`` / the legacy agent's ``ha_control`` / the brain's intent dispatch. Each entry now records
   ``panel_principal.may_control_home`` for its caller and every executor refuses politely at the moment it would act.
2. The relay go-live blocker (#2095): the voice HTTP lanes took ``panel_id`` and ``identified_user_id`` from the device's own
   payload. ``panel_id`` is now the device token's own panel (a ``replay-`` instrument id may narrow it, on the LAN only), a
   raw ``identified_user_id`` is ignored unless the caller is the internal PIN replay, a member's own session never inherits
   the fresh member of a panel it names, and over the relay the panel's own speaker claim / enrolment / adaptation is refused.
3. (the panel daemon's ``/activate`` lives in tests/unit/test_voice_daemon_platform.py)

Everything that is not under test is faked; the identity block of ``voice_command``, ``_lane_panel_id``,
``_require_voice_auth``, ``panel_principal``, ``smart_home_service.resolve_smart_home`` and the chat / WebSocket / intent-dispatch
entry points are REAL. All data is invented; addresses are TEST-NET.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import panel_principal
import smart_home_service
import routers.voice_tts as voice_tts
from test_ha_control_auth import GUEST_SID, MEMBER_SID, PANEL_IP, PANEL_TOKEN, PHONE_IP, PROXY, RegistryDb
from test_voice_identity_freshness import IDLE_WINDOW_S, _PanelStore, _ts, _wire

pytestmark = pytest.mark.ci_safe

REFUSAL = panel_principal.HOME_CONTROL_REFUSAL
PANEL_A = "panel-a"
PANEL_B = "panel-b"


# ── shared fixtures ────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()

    async def _validate(session_id):
        if session_id == MEMBER_SID:
            return {"user_id": "casey", "role": "user", "username": "casey", "permissions": []}
        if session_id == GUEST_SID:
            return {"user_id": "guest", "role": "guest", "username": "guest", "permissions": []}
        return None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    monkeypatch.delitem(sys.modules, "relay_ingress", raising=False)
    token = panel_principal._home_control_turn.set(None)   # a sync test must not leak its verdict into the next one
    yield
    panel_principal._home_control_turn.reset(token)
    auth._session_cache.clear()


@pytest.fixture
def registry(monkeypatch):
    db = RegistryDb()
    db.panel()

    async def _with_registry(_db, check, value):
        return await check(db, value)

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)
    return db


class _Resp:
    def __init__(self, data):
        self.status_code = 200
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


@pytest.fixture
def bridge(monkeypatch):
    """The HA bridge as ``smart_home_service`` / ``zoe_agent`` reach it (httpx). Records every call; answers an empty house."""
    calls: list = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            calls.append(("GET", url))
            return _Resp([])

        async def post(self, url, json=None, **k):
            calls.append(("POST", url, json))
            return _Resp({"ok": True})

    fake = types.SimpleNamespace(AsyncClient=_Client, ConnectError=ConnectionError)
    monkeypatch.setattr(smart_home_service, "httpx", fake)
    import zoe_agent

    monkeypatch.setattr(zoe_agent, "httpx", fake)
    return calls


def _light_off():
    return types.SimpleNamespace(domain="smart_home", action="turn_off", query="kitchen light", entity_id="", duration_seconds=0)


# ── 1. the executors: one verdict, every Home Assistant door ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("verdict, refused", [(False, True), (True, False), (None, False)], ids=["refused", "allowed", "not_a_turn"])
async def test_resolve_smart_home_obeys_the_turn_verdict(bridge, verdict, refused):
    if verdict is not None:
        panel_principal.record_home_control(verdict)
    out = await smart_home_service.resolve_smart_home(_light_off())
    if refused:
        assert out["spoken_summary"] == REFUSAL and out.get("auth_required") is True
        assert bridge == [], "a refused turn reached the Home Assistant bridge"
    else:
        assert out["spoken_summary"] != REFUSAL
        assert bridge, "NEGATIVE CONTROL: an allowed turn must reach the bridge (else the rig proves nothing)"


async def test_the_bridge_primitives_are_a_backstop(bridge):
    panel_principal.record_home_control(False)
    assert await smart_home_service._ha_post("/devices/control", {"entity_id": "light.kitchen", "action": "turn_off"}) is None
    assert await smart_home_service._ha_get("/entities?domain=light") is None
    assert bridge == []
    panel_principal.record_home_control(True)
    await smart_home_service._ha_post("/devices/control", {"entity_id": "light.kitchen", "action": "turn_off"})
    assert [c[0] for c in bridge] == ["POST"]


async def test_the_scope_rollback_reopens_the_executors(bridge, monkeypatch):
    panel_principal.record_home_control(False)
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    out = await smart_home_service.resolve_smart_home(_light_off())
    assert out["spoken_summary"] != REFUSAL and bridge


async def test_the_smart_home_intent_speaks_the_refusal(bridge):
    import intent_router

    panel_principal.record_home_control(False)
    reply = await intent_router._execute_smart_home_intent(
        intent_router.Intent(name="smart_home", slots={"action": "turn_off", "room": "kitchen"}), "guest")
    assert reply == REFUSAL and bridge == []


async def test_music_through_home_assistant_is_home_control(monkeypatch):
    import intent_router

    resolved: list = []

    async def _resolve(_url, room=None):
        resolved.append(room)
        return "media_player.kitchen"

    monkeypatch.setattr(intent_router, "_resolve_media_player_entity", _resolve)
    play = intent_router.Intent(name="music_control", slots={"action": "pause"})
    panel_principal.record_home_control(False)
    assert await intent_router._execute_music_intent(play, "guest") == REFUSAL
    assert resolved == []

    class _Client:
        posted: list = []

        async def post(self, url, json=None):
            self.posted.append(json)
            return types.SimpleNamespace(status_code=200)

    client = _Client()
    assert await intent_router._post_music_ha_control(client, "http://bridge", {"action": "media_pause"}) == REFUSAL
    assert client.posted == []
    panel_principal.record_home_control(True)   # NEGATIVE CONTROL: the panel / a member reaches the player
    assert await intent_router._post_music_ha_control(client, "http://bridge", {"action": "media_pause"}) is None
    assert client.posted == [{"action": "media_pause"}]


async def test_the_legacy_agent_ha_control_tool(bridge):
    import zoe_agent

    panel_principal.record_home_control(False)
    assert await zoe_agent._ha_control("light.kitchen", "turn_off") == {"error": REFUSAL}
    assert bridge == []
    panel_principal.record_home_control(True)
    assert await zoe_agent._ha_control("light.kitchen", "turn_off") == {"ok": True}
    assert bridge and bridge[0][0] == "POST"


# ── 2. the entry points record the verdict ────────────────────────────────────────────────────────────────────────────────

def _chat_app(monkeypatch):
    import routers.chat as chat_mod

    async def _session(body, user_id, **_k):
        return "ask_test"

    async def _ensure(*_a, **_k):
        return None

    async def _stream(message, session_id, user, **_k):
        # The real executor, run where the real turn runs it: inside the streaming body (a separate task) - so this also
        # proves the verdict reaches the stream, not just the endpoint.
        out = await smart_home_service.resolve_smart_home(_light_off())
        yield "data: " + json.dumps({"reply": out["spoken_summary"], "verdict": panel_principal.home_control_verdict()}) + "\n\n"

    monkeypatch.setattr(chat_mod, "resolve_session_id", _session)
    monkeypatch.setattr(chat_mod, "_ensure_user_and_chat_session", _ensure)
    monkeypatch.setattr(chat_mod, "locked_chat_stream", _stream)
    app = FastAPI()
    app.include_router(chat_mod.router)
    return app


def _chat(monkeypatch, peer, headers=None):
    r = TestClient(_chat_app(monkeypatch), client=(peer, 50000)).post(
        "/api/chat/", json={"message": "turn off the kitchen light"}, headers=headers or {})
    assert r.status_code == 200, r.text
    return json.loads(r.text.split("data: ", 1)[1])


@pytest.mark.parametrize("peer,headers", [
    (PHONE_IP, {}),                                                       # anonymous, straight at :8000 on the LAN
    (PROXY, {"X-Real-IP": PHONE_IP}),                                    # anonymous through nginx
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP}),         # a minted guest session is not the panel
    (PHONE_IP, {"X-Real-IP": PANEL_IP}),                                 # a forged panel address from an untrusted peer
], ids=["anon_direct", "anon_nginx", "guest_session", "forged_xrealip"])
def test_anonymous_chat_cannot_drive_the_house(registry, bridge, monkeypatch, peer, headers):
    got = _chat(monkeypatch, peer, headers)
    assert got == {"reply": REFUSAL, "verdict": False}
    assert bridge == []


@pytest.mark.parametrize("peer,headers", [
    (PROXY, {"X-Real-IP": PANEL_IP}),                                    # the kiosk browser through nginx
    (PHONE_IP, {"X-Session-ID": MEMBER_SID}),                            # a signed-in member
    (PHONE_IP, {"X-Device-Token": PANEL_TOKEN}),                         # the panel's device token
], ids=["kiosk", "member", "device_token"])
def test_the_panel_and_members_still_drive_the_house_from_chat(registry, bridge, monkeypatch, peer, headers):
    import routers.panel_auth as panel_auth

    async def _device(raw):
        if raw == PANEL_TOKEN:
            return {"user_id": "guest", "role": "guest", "username": "panel:kitchen-panel", "permissions": [],
                    "panel_id": "kitchen-panel", "device_token_role": "voice"}
        return None

    monkeypatch.setattr(panel_auth, "_resolve_device_token_user", _device)
    got = _chat(monkeypatch, peer, headers)
    assert got["verdict"] is True and got["reply"] != REFUSAL
    assert bridge


def test_other_chat_still_works_for_an_anonymous_caller(registry, monkeypatch):
    """Only home actions are refused: the anonymous turn itself runs (the stream answers) with the verdict recorded."""
    import routers.chat as chat_mod

    app = _chat_app(monkeypatch)

    async def _stream(message, session_id, user, **_k):
        yield "data: " + json.dumps({"reply": f"echo {message}", "verdict": panel_principal.home_control_verdict()}) + "\n\n"

    monkeypatch.setattr(chat_mod, "locked_chat_stream", _stream)
    r = TestClient(app, client=(PHONE_IP, 50000)).post("/api/chat/", json={"message": "what time is it"})
    assert json.loads(r.text.split("data: ", 1)[1]) == {"reply": "echo what time is it", "verdict": False}


@pytest.fixture
def ws_app(monkeypatch, bridge):
    sys.modules.pop("main", None)
    import main

    async def _cards(text, user_id, context=None):
        out = await smart_home_service.resolve_smart_home(_light_off())
        return {**out, "handled": True, "skybridge_context": {}}

    monkeypatch.setattr(main, "_resolve_voice_cards", _cards)
    return main


def _ws_turn(main, peer, session_id=""):
    client = TestClient(main.app, client=(peer, 50000))
    with client.websocket_connect(f"/ws/voice/?session_id={session_id}") as ws:
        assert ws.receive_json() == {"type": "state", "state": "ambient"}
        ws.send_text(json.dumps({"type": "text", "message": "turn off the kitchen light"}))
        said = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "transcript" and msg.get("role") == "assistant":
                said = msg["text"]
            if msg.get("type") == "done":
                return said
    raise AssertionError("no done frame")


def test_anonymous_voice_websocket_cannot_drive_the_house(registry, ws_app, bridge):
    assert _ws_turn(ws_app, PHONE_IP) == REFUSAL
    assert bridge == []


def test_the_kiosk_and_members_drive_the_house_over_the_voice_websocket(registry, ws_app, bridge, monkeypatch):
    registry.con.execute("UPDATE panels SET ip_address = ?", (PHONE_IP,))   # this peer IS the registered panel now
    assert _ws_turn(ws_app, PHONE_IP) != REFUSAL and bridge
    registry.con.execute("UPDATE panels SET ip_address = '192.0.2.99'")
    bridge.clear()

    async def _member(session_id):
        return "casey" if session_id == "sid-casey" else "voice-guest"

    monkeypatch.setattr(ws_app, "_resolve_ws_user", _member)
    assert _ws_turn(ws_app, PHONE_IP, session_id="sid-casey") != REFUSAL and bridge


def _dispatch(monkeypatch, user_id):
    from routers.system import router as system_router

    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    app = FastAPI()
    app.include_router(system_router)
    return TestClient(app).post("/api/system/intent-dispatch", headers={"X-Internal-Token": "tok"},
                                json={"user_id": user_id, "intent": "smart_home", "slots": {"action": "turn_off", "entity": "light"}})


@pytest.mark.parametrize("user_id", ["guest", "voice-guest", "guest-abc", "anonymous", "voice-daemon"])
def test_the_brain_dispatch_refuses_a_guest_turn(bridge, monkeypatch, user_id):
    """The Flue sidecar fails closed on ``guest`` itself, but not on ``voice-guest``; this hop now refuses every guest id."""
    r = _dispatch(monkeypatch, user_id)
    assert r.status_code == 200 and r.json()["result"] == REFUSAL
    assert bridge == []


def test_the_brain_dispatch_still_runs_a_members_home_command(bridge, monkeypatch):
    r = _dispatch(monkeypatch, "casey")
    assert r.status_code == 200 and r.json()["result"] != REFUSAL
    assert bridge


# ── 3. the voice HTTP lanes: the server decides the panel and the member ─────────────────────────────────────────────────

def _req(peer=PROXY, headers=None):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": "/api/voice/turn", "headers": raw, "client": (peer, 1)})


async def test_require_voice_auth_records_the_panel_and_a_member_as_allowed():
    device = {"panel_id": PANEL_A, "user_id": "voice-daemon", "role": "voice-daemon", "raw_token": "t"}
    caller = await voice_tts._require_voice_auth(_req(), device=device, user={"user_id": "guest", "role": "guest",
                                                                               "auth_degraded": True})
    # judged on the validated TOKEN: even a zoe-auth outage cannot lock the panel's lights
    assert caller["source"] == "device" and caller["via_relay"] is False
    assert panel_principal.home_control_verdict() is True
    panel_principal.record_home_control(False)
    caller = await voice_tts._require_voice_auth(_req(), device=None, user={"user_id": "casey", "role": "user"})
    assert caller["source"] == "session" and panel_principal.home_control_verdict() is True


async def test_an_anonymous_voice_caller_is_still_refused_outright():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await voice_tts._require_voice_auth(_req(), device=None, user={"user_id": "guest", "role": "guest"})
    assert exc.value.status_code == 401


@pytest.mark.parametrize("payload, caller, want", [
    ({"panel_id": PANEL_A}, {"source": "device", "panel_id": PANEL_A}, PANEL_A),
    ({}, {"source": "device", "panel_id": PANEL_A}, PANEL_A),
    ({"panel_id": PANEL_B}, {"source": "device", "panel_id": PANEL_A}, PANEL_A),                       # overridden, logged
    ({"panel_id": "replay-harness"}, {"source": "device", "panel_id": PANEL_A}, "replay-harness"),     # instrument narrowing
    ({"panel_id": "replay-harness"}, {"source": "device", "panel_id": PANEL_A, "via_relay": True}, PANEL_A),
    ({"panel_id": PANEL_B}, {"source": "pin_resume", "panel_id": PANEL_A}, PANEL_A),
    ({"panel_id": PANEL_B}, {"source": "session", "user_id": "casey"}, PANEL_B),                        # display only
    ({}, {"source": "session", "user_id": "casey"}, "unknown"),
], ids=["own", "absent", "other_panel", "instrument", "instrument_over_relay", "pin_replay", "session", "session_none"])
def test_lane_panel_id(payload, caller, want, caplog):
    caplog.set_level(logging.WARNING, logger=voice_tts.logger.name)
    assert voice_tts._lane_panel_id(payload, caller, "test") == want
    overridden = caller.get("source") == "device" and payload.get("panel_id") not in (None, want)
    assert ("is not the device token's panel" in caplog.text) is overridden


class _KeyedStore(_PanelStore):
    """``ui_panel_sessions`` per panel (the freshness rig keeps one row for every panel)."""

    def __init__(self):
        super().__init__()
        self.sessions: dict = {}

    async def execute(self, sql, params=()):
        if "FROM UI_PANEL_SESSIONS" in " ".join(sql.split()).upper():
            from test_voice_identity_freshness import _Cur

            row = self.sessions.get(params[0])
            return _Cur(dict(row) if row else None)
        return await super().execute(sql, params)


async def _cmd(seen, caller, **payload):
    body = {"text": "what's on my calendar", **payload}
    await voice_tts.voice_command(body, caller=caller, stream=False, db=object())
    if seen["spawned"]:
        await asyncio.gather(*seen["spawned"], return_exceptions=True)
        seen["spawned"].clear()
    return seen["skybridge_user"][-1]


def _device(panel=PANEL_A, **extra):
    return {"source": "device", "user_id": "voice-daemon", "panel_id": panel, **extra}


async def test_a_device_cannot_borrow_another_panels_member(monkeypatch):
    store = _KeyedStore()
    seen = _wire(monkeypatch, store)
    store.sessions[PANEL_B] = {"user_id": "bob", "last_seen_at": _ts(30)}       # Bob just signed in at panel B
    assert await _cmd(seen, _device(PANEL_A), panel_id=PANEL_B) == "guest", "panel A's token ran as panel B's member"
    # NEGATIVE CONTROLS: panel B's own token is Bob; panel A's token is panel A's own fresh member.
    assert await _cmd(seen, _device(PANEL_B), panel_id=PANEL_B) == "bob"
    store.sessions[PANEL_A] = {"user_id": "alice", "last_seen_at": _ts(30)}
    assert await _cmd(seen, _device(PANEL_A), panel_id=PANEL_B) == "alice"


async def test_a_raw_identified_user_id_is_not_an_identity(monkeypatch):
    store = _KeyedStore()
    seen = _wire(monkeypatch, store)
    assert await _cmd(seen, _device(), panel_id=PANEL_A, identified_user_id="bob") == "guest"
    assert await _cmd(seen, {"source": "session", "user_id": "casey", "role": "user"}, identified_user_id="bob") == "casey"
    # NEGATIVE CONTROL: the internal PIN replay (panel_auth.submit_pin, after the PIN was checked) still names its member.
    assert await _cmd(seen, {"source": "pin_resume", "panel_id": PANEL_A, "user_id": "bob"},
                      panel_id=PANEL_A, identified_user_id="bob") == "bob"


async def test_a_members_session_never_inherits_the_named_panels_member(monkeypatch):
    store = _KeyedStore()
    seen = _wire(monkeypatch, store)
    store.sessions[PANEL_B] = {"user_id": "bob", "last_seen_at": _ts(30)}
    assert await _cmd(seen, {"source": "session", "user_id": "casey", "role": "user"}, panel_id=PANEL_B) == "casey"
    assert "bob" not in seen["saved_users"], "the member's words were filed under the named panel's member"
    assert "casey" in seen["saved_users"]
    assert store.heartbeats == [] and store.sessions[PANEL_B]["user_id"] == "bob"


async def test_the_replay_instrument_id_resolves_no_member(monkeypatch):
    store = _KeyedStore()
    seen = _wire(monkeypatch, store)
    store.sessions[PANEL_A] = {"user_id": "alice", "last_seen_at": _ts(30)}
    assert await _cmd(seen, _device(PANEL_A), panel_id="replay-harness") == "guest"
    # over the relay the narrowing is refused: the token's own panel stands
    assert await _cmd(seen, _device(PANEL_A, via_relay=True), panel_id="replay-harness") == "alice"


async def test_a_fresh_member_turn_upgrades_a_refused_caller_verdict(monkeypatch):
    """A relayed panel is member-only for home control (#2095): its owner's fresh PIN session makes the turn a member's."""
    store = _KeyedStore()
    seen = _wire(monkeypatch, store)
    panel_principal.record_home_control(False)
    await _cmd(seen, _device(PANEL_A), panel_id=PANEL_A)
    assert panel_principal.home_control_verdict() is False             # nobody verified: still refused
    store.sessions[PANEL_A] = {"user_id": "alice", "last_seen_at": _ts(IDLE_WINDOW_S - 60)}
    await _cmd(seen, _device(PANEL_A), panel_id=PANEL_A)
    assert panel_principal.home_control_verdict() is True


# ── 4. over the relay: no panel-computed claim, no enrolment, no adaptation ──────────────────────────────────────────────

def _claim(member="alice", score=0.99):
    return {"voice_user_id": member, "voice_score": score}


def test_a_claim_is_honoured_on_the_lan_and_refused_over_the_relay():
    assert voice_tts._accept_panel_voice_claim(_claim(), _device()) == "alice"            # NEGATIVE CONTROL: the LAN panel
    assert voice_tts._accept_panel_voice_claim(_claim(), _device(via_relay=True)) is None


@pytest.mark.parametrize("module_state, want", [
    ("absent", False),            # today's main: no relay_ingress, no relay
    ("lan", False),
    ("relay", True),
    ("raises", True),             # fail closed: it only ever takes privileges away
])
def test_request_via_relay_reads_relay_ingress(monkeypatch, module_state, want):
    if module_state != "absent":
        def _is_relay(_conn):
            if module_state == "raises":
                raise RuntimeError("boom")
            return module_state == "relay"

        monkeypatch.setitem(sys.modules, "relay_ingress", types.SimpleNamespace(is_relay=_is_relay))
    assert voice_tts._request_via_relay(_req()) is want


async def test_require_voice_auth_carries_the_relay_verdict(monkeypatch):
    monkeypatch.setitem(sys.modules, "relay_ingress", types.SimpleNamespace(is_relay=lambda _c: True))
    caller = await voice_tts._require_voice_auth(
        _req(), device={"panel_id": PANEL_A, "user_id": "voice-daemon", "raw_token": "t"}, user={"user_id": "guest"})
    assert caller["via_relay"] is True
    assert voice_tts._accept_panel_voice_claim(_claim(), caller) is None


async def test_enrolment_and_adaptation_are_refused_over_the_relay(monkeypatch):
    from fastapi import HTTPException
    from routers import face_id, voice_speaker_adapt

    monkeypatch.setattr(face_id, "_require_enabled", lambda: None)   # the face flag is irrelevant to the relay refusal
    relayed = _device(via_relay=True)
    for call in (voice_tts.voice_enroll({"user_id": "alice", "audio_base64": "AA=="}, caller=relayed),
                 voice_speaker_adapt.voice_adapt({"voice_user_id": "alice"}, caller=relayed),
                 face_id.face_enroll({"user_id": "alice", "consent": True}, caller=relayed)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403 and "relay" in exc.value.detail
