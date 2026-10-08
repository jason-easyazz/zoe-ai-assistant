"""Every WebSocket endpoint admits every SIGNED-IN zoe-auth role, never a guest.

zoe-auth creates ordinary accounts with role ``user`` (its default; family_member and
child exist too) and zoe-data passes the role through verbatim. Until 2026-10-08 the
push socket and all six per-resource sockets accepted only member/admin/agent, so a
real ``user`` account was closed 1008 on every socket and reconnected forever — the
shopping widget's lists socket, the desktop push hub, all of it. The policy now lives
in main._ws_role_signed_in / _ws_channel_forbidden, shared by every handler.

Helper-level tests cover the policy; the TestClient tests prove the wiring with the
ACTUAL default role on the lists socket and the push socket.
"""
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_ZOE_DATA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ZOE_DATA))
sys.modules.pop("main", None)
import main  # noqa: E402

pytestmark = pytest.mark.ci_safe

ORIGIN = {"origin": "https://zoe.local"}


# --- policy -----------------------------------------------------------------

@pytest.mark.parametrize("role", ["user", "family_member", "child", "member", "admin", "family-admin", "agent", "teenager", "housemate"])
def test_every_signed_in_role_is_admitted_including_custom_ones(role):
    # zoe-auth admins can create arbitrary roles; signed-in is decided by exclusion.
    assert main._ws_role_signed_in({"user_id": "u1", "role": role}) is True


@pytest.mark.parametrize("user", [
    None, {}, "not-a-dict",
    {"user_id": "guest", "role": "guest"},      # the guest sentinel
    {"user_id": "guest", "role": "admin"},      # a guest identity never gets in, whatever the role says
    {"user_id": "u1", "role": "guest"},
    {"user_id": "u1"}, {"user_id": "u1", "role": ""}, {"user_id": "u1", "role": None},
    {"user_id": "", "role": "user"}, {"role": "user"},
])
def test_guest_or_identity_less_is_refused(user):
    assert main._ws_role_signed_in(user) is False


@pytest.mark.parametrize("role", ["user", "family_member", "child", "member", "teenager"])
def test_non_admin_roles_are_scoped_to_their_own_channel(role):
    assert main._ws_channel_forbidden({"user_id": "asya", "role": role}, "asya") is False
    assert main._ws_channel_forbidden({"user_id": "asya", "role": role}, "jason") is True


@pytest.mark.parametrize("role", ["admin", "family-admin", "agent"])
def test_admin_alias_and_agent_may_subscribe_for_anyone(role):
    assert main._ws_channel_forbidden({"user_id": "jason", "role": role}, "asya") is False


def test_no_handler_inlines_a_role_tuple():
    # The policy must stay in ONE place: a handler that grows its own tuple drifts.
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert '("member", "admin", "agent")' not in src
    assert 'user.get("role") == "member" and' not in src


def test_every_per_user_socket_enforces_ownership():
    # Every /api/<x>/ws/{user_id} handler must call _ws_channel_forbidden (journal did not).
    import re
    src = Path(main.__file__).read_text(encoding="utf-8")
    for m in re.finditer(r'@app\.websocket\("(/api/\w+/ws/\{user_id\})"\)\n(async def \w+\(.*?\n(?:(?!@app\.).*\n)*)', src):
        assert "_ws_channel_forbidden(user, user_id)" in m.group(2), m.group(1) + " does not enforce channel ownership"


# --- wiring, with the real default role ---------------------------------------

@pytest.fixture
def client():
    return TestClient(main.app)


def _resolver(monkeypatch, user):
    async def _fake_resolve(session_id):
        return user
    monkeypatch.setattr(main, "_resolve_ws_session", _fake_resolve)


def test_default_user_role_opens_its_own_lists_socket(client, monkeypatch):
    _resolver(monkeypatch, {"user_id": "asya", "role": "user"})
    with client.websocket_connect("/api/lists/ws/asya?session_id=s", headers=ORIGIN) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected" and hello["channel"] == "lists"
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}


def test_default_user_role_cannot_open_someone_elses_channel(client, monkeypatch):
    _resolver(monkeypatch, {"user_id": "asya", "role": "user"})
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/lists/ws/jason?session_id=s", headers=ORIGIN):
            pass
    assert exc.value.code == 1008


def test_guest_session_is_refused(client, monkeypatch):
    _resolver(monkeypatch, {"user_id": "guest", "role": "guest"})
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/lists/ws/guest?session_id=s", headers=ORIGIN):
            pass
    assert exc.value.code == 1008


def test_admin_may_subscribe_on_behalf_of_another_user(client, monkeypatch):
    _resolver(monkeypatch, {"user_id": "jason", "role": "admin"})
    with client.websocket_connect("/api/lists/ws/asya?session_id=s", headers=ORIGIN) as ws:
        assert ws.receive_json()["type"] == "connected"


def test_custom_role_opens_its_own_socket_and_family_admin_alias_may_subscribe_for_anyone(client, monkeypatch):
    _resolver(monkeypatch, {"user_id": "kid", "role": "teenager"})
    with client.websocket_connect("/api/notes/ws/kid?session_id=s", headers=ORIGIN) as ws:
        assert ws.receive_json()["type"] == "connected"
    _resolver(monkeypatch, {"user_id": "jason", "role": "family-admin"})
    with client.websocket_connect("/api/notes/ws/kid?session_id=s", headers=ORIGIN) as ws:
        assert ws.receive_json()["type"] == "connected"


def test_default_user_role_cannot_open_someone_elses_journal(client, monkeypatch):
    _resolver(monkeypatch, {"user_id": "asya", "role": "user"})
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/journal/ws/jason?session_id=s", headers=ORIGIN):
            pass
    assert exc.value.code == 1008


def test_default_user_role_opens_the_push_socket(client, monkeypatch):
    _resolver(monkeypatch, {"user_id": "asya", "role": "user"})
    with client.websocket_connect("/ws/push?channel=all&session_id=s", headers=ORIGIN) as ws:
        assert ws.receive_json()["type"] == "connected"


# --- delegated sockets receive the member's scoped broadcasts ----------------

def test_delegated_socket_is_registered_under_the_requested_user(client, monkeypatch):
    # The broadcaster filters scoped deliveries by the user_id a socket was REGISTERED
    # under; an admin subscribed to /api/lists/ws/asya must therefore register as asya.
    _resolver(monkeypatch, {"user_id": "jason", "role": "family-admin"})
    with client.websocket_connect("/api/lists/ws/asya?session_id=s", headers=ORIGIN) as ws:
        assert ws.receive_json()["type"] == "connected"
        owners = set(main.broadcaster._ws_users.values())
        assert "asya" in owners and "jason" not in owners


@pytest.mark.anyio
async def test_scoped_broadcast_reaches_the_channel_owner_only():
    from push import PushBroadcaster

    class _WS:
        def __init__(self): self.sent = []
        async def accept(self): pass
        async def send_json(self, m): self.sent.append(m)

    bc = PushBroadcaster()
    delegated, other = _WS(), _WS()
    await bc.connect(delegated, "lists", user_id="asya")   # an admin registered under asya
    await bc.connect(other, "lists", user_id="jason")
    delivered = await bc.broadcast("lists", "list_updated", {"id": 1}, user_id="asya")
    assert delivered == 1
    assert [m["type"] for m in delegated.sent] == ["connected", "list_updated"]
    assert [m["type"] for m in other.sent] == ["connected"]
