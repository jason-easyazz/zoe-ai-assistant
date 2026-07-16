"""Unit tests for PushBroadcaster.broadcast() user_id scoping.

Regression test for: push.py broadcast() missing user_id parameter.
The method signature previously omitted user_id, causing TypeError on every
scoped WS notification (reminders, notes, journal, lists, calendar, people,
transactions).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from push import PushBroadcaster

pytestmark = pytest.mark.ci_safe


def _make_ws():
    """Return a mock WebSocket; user_id mapping is tracked in broadcaster state."""
    ws = MagicMock()
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def bc():
    return PushBroadcaster()


async def _connect(bc, ws, channel="test", user_id=None):
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    await bc.connect(ws, channel=channel, user_id=user_id)
    # reset the send_json call count after the connect ack
    ws.send_json.reset_mock()


# ── signature ────────────────────────────────────────────────────────────────

def test_broadcast_accepts_user_id_kwarg():
    """broadcast() must accept user_id as an optional keyword argument."""
    import inspect
    sig = inspect.signature(PushBroadcaster.broadcast)
    assert "user_id" in sig.parameters, (
        "broadcast() is missing user_id parameter — caller TypeError regression"
    )
    assert sig.parameters["user_id"].default is None


# ── scoped delivery ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_global_reaches_all(bc):
    """Without user_id, broadcast reaches every connection on the channel."""
    ws_a = _make_ws()
    ws_b = _make_ws()
    await _connect(bc, ws_a, channel="ch", user_id="u1")
    await _connect(bc, ws_b, channel="ch", user_id="u2")

    delivered = await bc.broadcast("ch", "evt", {}, user_id=None)
    assert delivered == 2
    ws_a.send_json.assert_awaited_once()
    ws_b.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_scoped_only_reaches_owner(bc):
    """With user_id set, only connections for that user receive the message."""
    ws_a = _make_ws()
    ws_b = _make_ws()
    await _connect(bc, ws_a, channel="ch", user_id="u1")
    await _connect(bc, ws_b, channel="ch", user_id="u2")

    delivered = await bc.broadcast("ch", "evt", {}, user_id="u1")
    assert delivered == 1
    ws_a.send_json.assert_awaited_once()
    ws_b.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_scoped_includes_anon_connections(bc):
    """Connections with no user (panels/anonymous) always receive scoped broadcasts."""
    ws_owner = _make_ws()
    ws_anon = _make_ws()
    ws_other = _make_ws()
    await _connect(bc, ws_owner, channel="ch", user_id="u1")
    await _connect(bc, ws_anon, channel="ch", user_id=None)
    await _connect(bc, ws_other, channel="ch", user_id="u2")

    delivered = await bc.broadcast("ch", "evt", {}, user_id="u1")
    assert delivered == 2  # owner + anon
    ws_owner.send_json.assert_awaited_once()
    ws_anon.send_json.assert_awaited_once()
    ws_other.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_empty_channel_returns_zero(bc):
    """broadcast() on an empty or missing channel returns 0."""
    result = await bc.broadcast("nonexistent", "evt", {}, user_id="u1")
    assert result == 0


@pytest.mark.asyncio
async def test_broadcast_survives_connection_set_mutation_during_send(bc):
    """A send suspension point must not let connect/disconnect abort fan-out."""
    ws_a = _make_ws()
    ws_b = _make_ws()
    ws_new_1 = _make_ws()
    ws_new_2 = _make_ws()
    await _connect(bc, ws_a, channel="ch", user_id="u1")
    await _connect(bc, ws_b, channel="ch", user_id="u1")

    async def mutate_connections(_message):
        bc.disconnect(ws_b, "ch")
        bc._connections["ch"].add(ws_new_1)
        bc._connections["ch"].add(ws_new_2)
        await asyncio.sleep(0)

    ws_a.send_json.side_effect = mutate_connections

    delivered = await bc.broadcast("ch", "evt", {}, user_id="u1")

    assert delivered == 2
    ws_a.send_json.assert_awaited_once()
    ws_b.send_json.assert_awaited_once()
    ws_new_1.send_json.assert_not_called()
    ws_new_2.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_prunes_dead_panel_socket_from_all_state(bc):
    """Failed sends remove dead sockets from channel sets and metadata maps."""
    ws = _make_ws()
    await _connect_panel(bc, ws, panel_id="panel-1")
    ws.send_json.side_effect = RuntimeError("socket closed")

    delivered = await bc.broadcast("all", "evt", {})

    assert delivered == 0
    assert ws not in bc._connections["all"]
    assert ws not in bc._connections["panel_panel-1"]
    assert ws not in bc._ws_users
    assert ws not in bc._ws_panels


async def _connect_panel(bc, ws, panel_id='panel-1'):
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    await bc.connect_panel(ws, panel_id=panel_id)
    ws.send_json.reset_mock()


@pytest.mark.asyncio
async def test_broadcast_to_panel_reports_dedicated_panel_delivery(bc):
    """Dedicated panel channel delivery is counted for durable action retirement."""
    ws = _make_ws()
    await _connect_panel(bc, ws, panel_id='panel-1')

    delivered = await bc.broadcast_to_panel('panel-1', 'ui_action', {'panel_id': 'panel-1'})

    assert delivered == 1
    ws.send_json.assert_awaited_once()
    message = ws.send_json.await_args.args[0]
    assert message['channel'] == 'panel_panel-1'
    assert message['type'] == 'ui_action'


@pytest.mark.asyncio
async def test_broadcast_to_panel_fallback_does_not_report_delivery(bc):
    """Global fallback is not proof the target panel received the action."""
    ws = _make_ws()
    await _connect(bc, ws, channel='all', user_id=None)

    delivered = await bc.broadcast_to_panel('panel-1', 'ui_action', {'panel_id': 'panel-1'})

    assert delivered == 0
    ws.send_json.assert_awaited_once()
    message = ws.send_json.await_args.args[0]
    assert message['channel'] == 'all'
    assert message['type'] == 'ui_action'
