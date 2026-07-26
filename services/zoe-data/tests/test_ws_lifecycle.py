"""Regression tests for push WebSocket lifecycle cleanup."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from push import PushBroadcaster  # noqa: E402

pytestmark = pytest.mark.ci_safe


class FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent_json = []
        self.close_calls = []

    async def accept(self):
        pass

    async def send_json(self, payload):
        self.sent_json.append(payload)

    async def close(self, code=1000, reason=None):
        self.close_calls.append((code, reason))

    async def receive_text(self):
        if not self._messages:
            raise WebSocketDisconnect()
        message = self._messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        if asyncio.iscoroutine(message):
            return await message
        return message


async def _never_receives():
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_malformed_catchup_does_not_leak_registration(monkeypatch):
    bc = PushBroadcaster()
    monkeypatch.setattr(main, "broadcaster", bc)
    ws = FakeWebSocket(["catchup:", "catchup:abc", WebSocketDisconnect()])

    await bc.connect(ws, "all", user_id="user-1")
    await main._run_push_ws_loop(ws, "all", allow_catchup=True)

    errors = [payload for payload in ws.sent_json if payload.get("type") == "error"]
    assert [payload["error"] for payload in errors] == [
        "invalid_catchup_sequence",
        "invalid_catchup_sequence",
    ]
    assert ws not in bc._connections["all"]
    assert ws not in bc._ws_users
    assert ws not in bc._ws_panels


@pytest.mark.asyncio
async def test_idle_websocket_is_reaped_after_deadline(monkeypatch):
    bc = PushBroadcaster()
    monkeypatch.setattr(main, "broadcaster", bc)
    monkeypatch.setattr(main, "WS_IDLE_TIMEOUT_SECONDS", 0.01)
    ws = FakeWebSocket([_never_receives()])

    await bc.connect(ws, "notes", user_id="user-1")
    await main._run_push_ws_loop(ws, "notes")

    assert ws.close_calls == [(1001, "Idle timeout")]
    assert ws not in bc._connections["notes"]
    assert ws not in bc._ws_users
    assert ws not in bc._ws_panels


def test_ws_idle_timeout_env_falls_back_for_bad_values(monkeypatch):
    monkeypatch.setenv("ZOE_WS_IDLE_TIMEOUT_SECONDS", "not-a-number")
    assert main._ws_idle_timeout_seconds() == 120.0

    monkeypatch.setenv("ZOE_WS_IDLE_TIMEOUT_SECONDS", "0")
    assert main._ws_idle_timeout_seconds() == 120.0

    monkeypatch.setenv("ZOE_WS_IDLE_TIMEOUT_SECONDS", "-5")
    assert main._ws_idle_timeout_seconds() == 120.0


def test_ws_idle_timeout_env_accepts_positive_values(monkeypatch):
    monkeypatch.setenv("ZOE_WS_IDLE_TIMEOUT_SECONDS", "180.5")
    assert main._ws_idle_timeout_seconds() == 180.5
