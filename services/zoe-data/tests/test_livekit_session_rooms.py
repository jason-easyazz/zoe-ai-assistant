"""A10 (architecture deep dive 2026-10-18): one LiveKit room per voice session - two sessions cannot hear each other.

Every `/livekit-token` used to grant the SAME room, "zoe-voice", with publish + subscribe, and the agent sat in that
one room: a latent party line (two open sessions heard each other's audio and the agent's replies went to everyone).
Now each token names a fresh `zoe-voice-<panel>-<12 hex>` room and the agent joins exactly that room.

The isolation test runs the REAL agent loop (`_agent_loop`) and the REAL room handlers against a fake LiveKit server
that enforces what the real one enforces: a participant joins the room its JWT grant names, and data published in a
room reaches only that room's participants. A negative control forces both sessions into ONE room (the old fixed-room
behaviour) and shows session B receiving session A's reply, so the assertion is proven able to see the leak.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.voice_livekit as vl
import routers.voice_tts as voice_tts
from auth import get_current_user

pytestmark = pytest.mark.ci_safe

_SECRET = "test-livekit-secret-0123456789abcdef"
_KEY = "APItestkey"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", _KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", _SECRET)
    monkeypatch.setattr(vl, "_room_tasks", {})
    monkeypatch.setattr(vl, "_active_participant_sids", set())
    yield
    for t in list(vl._room_tasks.values()):
        t.cancel()


# --------------------------------------------------------------------------- #
# Token minting: a fresh room per token, never the old fixed room             #
# --------------------------------------------------------------------------- #
def _mint(device: dict, monkeypatch) -> dict:
    joined: list[str] = []

    async def _ensure(wait_ready: float = 8.0, room_name=None):
        joined.append(room_name)
        return True

    monkeypatch.setattr(vl, "ensure_livekit_started", _ensure)
    app = FastAPI()
    app.include_router(voice_tts.router)
    app.dependency_overrides[voice_tts._validate_device_token] = lambda: device
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "guest", "role": "guest"}
    body = TestClient(app).get("/api/voice/livekit-token", headers={"X-Device-Token": "raw"}).json()
    claims = jwt.decode(body["token"], _SECRET, algorithms=["HS256"])
    return {"body": body, "claims": claims, "joined": joined}


def test_each_token_names_its_own_session_room_and_the_agent_joins_it(monkeypatch):
    a = _mint({"panel_id": "kitchen", "user_id": "voice-daemon", "role": "kiosk"}, monkeypatch)
    a2 = _mint({"panel_id": "kitchen", "user_id": "voice-daemon", "role": "kiosk"}, monkeypatch)
    b = _mint({"panel_id": "Hall Panel!", "user_id": "voice-daemon", "role": "kiosk"}, monkeypatch)
    rooms = [x["claims"]["video"]["room"] for x in (a, a2, b)]
    assert len(set(rooms)) == 3, "every token is a new room - even two sessions on the same panel"
    assert all(vl.valid_session_room(r) for r in rooms) and "zoe-voice" not in rooms
    assert rooms[0].startswith("zoe-voice-kitchen-") and rooms[2].startswith("zoe-voice-hall-panel-")
    for x in (a, a2, b):
        assert x["joined"] == [x["claims"]["video"]["room"]], "the agent joins exactly the room the token grants"
        assert x["body"]["room"] == x["claims"]["video"]["room"]


def test_agent_token_is_scoped_to_one_room():
    room = vl.new_session_room("kitchen")
    claims = jwt.decode(vl._mint_agent_token(room), _SECRET, algorithms=["HS256"])
    assert claims["video"]["room"] == room and claims["sub"] == vl._AGENT_IDENTITY


def test_session_room_names():
    assert vl.valid_session_room(vl.new_session_room(None))
    assert vl.new_session_room("").startswith("zoe-voice-web-")
    assert vl.new_session_room("../../x").startswith("zoe-voice-x-")
    assert vl.valid_session_room(vl.new_session_room("a" * 200))
    for bad in ("zoe-voice", "zoe-voice-kitchen", "other", "zoe-voice-kitchen-XYZ", None, 3):
        assert not vl.valid_session_room(bad)


@pytest.mark.asyncio
async def test_room_agents_are_idempotent_bounded_and_refuse_a_non_session_room(monkeypatch):
    started: list[str] = []

    async def _loop(room_name):
        started.append(room_name)
        await asyncio.sleep(3600)

    monkeypatch.setattr(vl, "_agent_loop", _loop)
    monkeypatch.setattr(vl, "_MAX_ROOM_AGENTS", 2)
    r1, r2, r3 = (vl.new_session_room(p) for p in ("a", "b", "c"))
    vl._ensure_room_agent(r1)
    vl._ensure_room_agent(r1)
    vl._ensure_room_agent("zoe-voice")  # the old fixed room is never joined
    await asyncio.sleep(0)
    assert started == [r1]
    first = vl._room_tasks[r1]
    vl._ensure_room_agent(r2)
    vl._ensure_room_agent(r3)  # over the cap: the oldest room agent is dropped
    await asyncio.sleep(0)
    assert list(vl._room_tasks) == [r2, r3] and first.cancelled()


# --------------------------------------------------------------------------- #
# The isolation proof: real agent loop, fake LiveKit server                   #
# --------------------------------------------------------------------------- #
class _ConnState:
    CONN_DISCONNECTED = 0
    CONN_CONNECTED = 1


class _P:
    def __init__(self, identity: str, sid: str):
        self.identity, self.sid = identity, sid


class _Packet:
    def __init__(self, participant, data: bytes):
        self.participant, self.data = participant, data


class _Server:
    """What LiveKit enforces: the JWT grant decides the room; data stays inside the room it was published in."""

    def __init__(self):
        self.rooms: dict[str, list] = {}        # room -> agent _Room objects joined to it
        self.inbox: dict[str, list[dict]] = {}  # human participant sid -> data it received
        self.humans: dict[str, str] = {}        # human sid -> its room

    def agent_join(self, room_obj, token: str) -> str:
        name = jwt.decode(token, _SECRET, algorithms=["HS256"])["video"]["room"]
        self.rooms.setdefault(name, []).append(room_obj)
        return name

    def human_join(self, token: str, sid: str) -> str:
        claims = jwt.decode(token, _SECRET, algorithms=["HS256"])
        name = claims["video"]["room"]
        self.humans[sid] = name
        self.inbox[sid] = []
        for agent in self.rooms.get(name, []):
            p = _P(claims["sub"], sid)
            agent.remote_participants[sid] = p
            agent.emit("participant_connected", p)
        return name

    def human_send(self, sid: str, msg: dict) -> None:
        for agent in self.rooms.get(self.humans[sid], []):
            agent.emit("data_received", _Packet(agent.remote_participants[sid], json.dumps(msg).encode()))

    def publish_from_agent(self, room_name: str, payload: bytes) -> None:
        for sid, r in self.humans.items():
            if r == room_name:
                self.inbox[sid].append(json.loads(payload))


class _Local:
    def __init__(self, room):
        self._room = room

    async def publish_data(self, payload: bytes, reliable: bool = True, **_k):
        self._room.server.publish_from_agent(self._room.name, payload)


class _Room:
    def __init__(self, server: _Server):
        self.server, self.name = server, None
        self.handlers: dict[str, list] = {}
        self.remote_participants: dict[str, _P] = {}
        self.connection_state = _ConnState.CONN_DISCONNECTED
        self.local_participant = _Local(self)

    def on(self, event):
        def deco(fn):
            self.handlers.setdefault(event, []).append(fn)
            return fn
        return deco

    def emit(self, event, *args):
        for fn in self.handlers.get(event, []):
            fn(*args)

    async def connect(self, url, token, **_k):
        self.name = self.server.agent_join(self, token)
        self.connection_state = _ConnState.CONN_CONNECTED

    async def disconnect(self):
        self.connection_state = _ConnState.CONN_DISCONNECTED


def _wire_server(monkeypatch) -> _Server:
    server = _Server()
    monkeypatch.setenv("ZOE_LK_USE_AIORTC", "1")
    monkeypatch.setitem(sys.modules, "livekit_aiortc", types.SimpleNamespace(
        make_room=lambda: _Room(server), _ConnState=_ConnState, _TrackKind=types.SimpleNamespace(KIND_AUDIO=1)))
    monkeypatch.setattr(vl, "_ROOM_POLL_S", 0.01)

    async def _reply(local_participant, message, user_id, session_id):
        await vl._send_data(local_participant, {"type": "transcript", "role": "zoe", "text": f"reply to: {message}"})

    monkeypatch.setattr(vl, "_run_text_pipeline", _reply)
    return server


def _user_token(room: str, who: str) -> str:
    return jwt.encode({"iss": _KEY, "sub": who, "video": {"roomJoin": True, "room": room}}, _SECRET, algorithm="HS256")


async def _until(cond, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def _two_sessions(server: _Server, room_a: str, room_b: str) -> None:
    vl._ensure_room_agent(room_a)
    vl._ensure_room_agent(room_b)
    await _until(lambda: room_a in server.rooms and room_b in server.rooms)
    server.human_join(_user_token(room_a, "member-a"), "PA_a")
    server.human_join(_user_token(room_b, "member-b"), "PA_b")
    server.human_send("PA_a", {"type": "text", "message": "what is on my calendar"})
    await _until(lambda: server.inbox["PA_a"])
    for _ in range(5):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_two_sessions_cannot_hear_each_other(monkeypatch):
    server = _wire_server(monkeypatch)
    room_a, room_b = vl.new_session_room("kitchen"), vl.new_session_room("hall")
    await _two_sessions(server, room_a, room_b)
    assert server.inbox["PA_a"] == [{"type": "transcript", "role": "zoe", "text": "reply to: what is on my calendar"}]
    assert server.inbox["PA_b"] == [], "session B must hear nothing of session A's turn"
    assert len(server.rooms[room_a]) == 1 and len(server.rooms[room_b]) == 1, "one agent per room"


@pytest.mark.asyncio
async def test_negative_control_one_shared_room_is_a_party_line(monkeypatch):
    """The rig sees the leak: two sessions in ONE room (the old fixed "zoe-voice" behaviour) hear each other."""
    server = _wire_server(monkeypatch)
    shared = vl.new_session_room("house")
    vl._ensure_room_agent(shared)
    await _until(lambda: shared in server.rooms)
    server.human_join(_user_token(shared, "member-a"), "PA_a")
    server.human_join(_user_token(shared, "member-b"), "PA_b")
    server.human_send("PA_a", {"type": "text", "message": "what is on my calendar"})
    await _until(lambda: server.inbox["PA_a"])
    assert server.inbox["PA_b"], "in a shared room session B hears session A's reply"


@pytest.mark.asyncio
async def test_a_room_agent_leaves_its_empty_room_and_cancels_its_watchdog(monkeypatch):
    server = _wire_server(monkeypatch)
    monkeypatch.setattr(vl, "_ROOM_IDLE_S", 0.15)
    room = vl.new_session_room("kitchen")
    vl._ensure_room_agent(room)
    task = vl._room_tasks[room]
    await _until(lambda: room in server.rooms)
    server.human_join(_user_token(room, "member-a"), "PA_a")
    await asyncio.sleep(0.3)
    assert not task.done(), "an occupied room keeps its agent"
    agent_room = server.rooms[room][0]
    agent_room.emit("participant_disconnected", agent_room.remote_participants.pop("PA_a"))
    await asyncio.wait_for(task, timeout=2.0)
    assert room not in vl._room_tasks, "a finished room agent forgets its room"
    leftover = [t for t in asyncio.all_tasks() if "cooldown_watchdog" in repr(t.get_coro())]
    assert leftover == [], "the room's cooldown watchdog must not outlive it"
