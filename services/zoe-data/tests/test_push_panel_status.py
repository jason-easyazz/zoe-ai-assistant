"""Deaf-panel observability (2026-10-12).

Every ``voice:*`` event and answer card reaches the wall panel ONLY over its ``/ws/push``
socket, and ``PushBroadcaster.broadcast``'s delivered count was ignored by every caller — so a
panel whose socket had died looked exactly like a healthy one from the server. This pins:

  * ``panel_subscriber_count`` / ``panel_push_status`` (what ``GET /api/panels/<id>/push-status``
    serves) follow connect / disconnect;
  * ``warn_if_panel_deaf`` logs ONE WARNING when the target panel channel has zero subscribers,
    stays silent when one is connected, and is rate-limited per panel;
  * a real voice turn (``voice_command``) triggers it exactly once.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

import push as push_mod
from push import (
    DEAF_PANEL_ERROR_AFTER_S,
    DEAF_PANEL_ERROR_INTERVAL_S,
    DEAF_PANEL_WARN_INTERVAL_S,
    PushBroadcaster,
)

pytestmark = pytest.mark.ci_safe


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_json(self, msg):
        self.sent.append(msg)


def _deaf_records(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING and "ZERO subscribers" in r.getMessage()]


@pytest.mark.asyncio
async def test_status_follows_connect_and_disconnect():
    b = PushBroadcaster()
    s = b.panel_push_status("zoe-touch-pi")
    assert s["panel_id"] == "zoe-touch-pi" and s["subscribers"] == 0
    assert s["last_connect_at"] is None and s["last_disconnect_at"] is None
    assert s["deaf_turns"] == 0 and s["deaf_alert"] is False

    ws = _FakeWS()
    await b.connect_panel(ws, "zoe-touch-pi")
    s = b.panel_push_status("zoe-touch-pi")
    assert s["subscribers"] == 1 and s["last_connect_at"] is not None and s["last_disconnect_at"] is None
    assert s["deaf_for_s"] is None and s["deaf_alert"] is False, "a connected panel is never deaf"
    assert b.panel_subscriber_count("another-panel") == 0, "another panel's channel is separate"

    b.disconnect(ws)
    s = b.panel_push_status("zoe-touch-pi")
    assert s["subscribers"] == 0 and s["last_disconnect_at"] is not None


@pytest.mark.asyncio
async def test_warns_once_for_a_deaf_panel_and_is_rate_limited(caplog):
    b = PushBroadcaster()
    with caplog.at_level(logging.WARNING, logger="push"):
        assert b.warn_if_panel_deaf("zoe-touch-pi", now=1000.0) is True
        assert b.warn_if_panel_deaf("zoe-touch-pi", now=1000.0 + DEAF_PANEL_WARN_INTERVAL_S - 1) is False, "same turn: no second warning"
        assert len(_deaf_records(caplog)) == 1
        assert "zoe-touch-pi" in _deaf_records(caplog)[0].getMessage()
        assert "push-status" in _deaf_records(caplog)[0].getMessage(), "the warning points at the diagnostic"
        assert b.warn_if_panel_deaf("zoe-touch-pi", now=1000.0 + DEAF_PANEL_WARN_INTERVAL_S + 1) is True, "next turn warns again"
        assert b.warn_if_panel_deaf("other-panel", now=1001.0) is True, "the limiter is per panel"
        assert len(_deaf_records(caplog)) == 3


@pytest.mark.asyncio
async def test_silent_when_the_panel_is_connected_or_unaddressable(caplog):
    b = PushBroadcaster()
    await b.connect_panel(_FakeWS(), "zoe-touch-pi")
    with caplog.at_level(logging.WARNING, logger="push"):
        assert b.warn_if_panel_deaf("zoe-touch-pi", now=1.0) is False, "a connected panel is not deaf"
        for pid in ("", None, "unknown"):
            assert b.warn_if_panel_deaf(pid, now=1.0) is False, f"{pid!r} names no panel — nothing to warn about"
    assert not _deaf_records(caplog)


@pytest.mark.asyncio
async def test_socket_dropped_then_warns():
    """The failure this was built for: the socket closes, nothing reconnects, voice turns go on."""
    b = PushBroadcaster()
    ws = _FakeWS()
    await b.connect_panel(ws, "zoe-touch-pi")
    assert b.warn_if_panel_deaf("zoe-touch-pi", now=1.0) is False
    b.disconnect(ws)
    assert b.warn_if_panel_deaf("zoe-touch-pi", now=2.0) is True


def test_push_status_endpoint_reads_the_broadcaster(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import panel_config

    b = PushBroadcaster()
    monkeypatch.setattr(push_mod, "broadcaster", b)
    app = FastAPI()
    app.include_router(panel_config.router)
    with TestClient(app) as client:
        r = client.get("/api/panels/zoe-touch-pi/push-status")   # unauthenticated, like /config
        assert r.status_code == 200
        assert r.json()["subscribers"] == 0 and r.json()["panel_id"] == "zoe-touch-pi"


@pytest.mark.asyncio
async def test_a_real_voice_turn_warns_exactly_once(monkeypatch, caplog):
    """voice_command (and /turn, /turn_stream) call the check on the transcript broadcast."""
    import voice_presence
    import skybridge_service
    import routers.voice_tts as vt

    async def none(*_a, **_k):
        return None

    async def panel_user(*_a, **_k):
        return "panel-user"

    async def history(*_a, **_k):
        return []

    async def agent(*_a, **_k):
        yield "Fine."

    async def tts(*_a, **_k):
        return b"RIFF"

    b = PushBroadcaster()
    monkeypatch.setattr(push_mod, "broadcaster", b)
    monkeypatch.setattr(voice_presence, "_AUDIO_CACHE", {})
    monkeypatch.setattr(vt, "_resolve_recent_panel_session_user", panel_user)
    monkeypatch.setattr(vt, "_load_voice_history", history)
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", tts)
    monkeypatch.setattr(vt, "_VOICE_SESSIONS", {})
    monkeypatch.setitem(sys.modules, "skybridge_service", types.SimpleNamespace(resolve_skybridge_request=none))
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", none)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
    monkeypatch.setitem(sys.modules, "zoe_agent", types.SimpleNamespace(run_zoe_agent_streaming=agent))

    with caplog.at_level(logging.WARNING, logger="push"):
        resp = await vt.voice_command(
            {"text": "could you think about this for me", "panel_id": "zoe-touch-pi", "session_id": "s1"},
            caller={"user_id": "guest", "panel_id": "zoe-touch-pi"}, stream=True, db=object(),
        )
        [_ async for _ in resp.body_iterator]
    assert len(_deaf_records(caplog)) == 1, "one deaf-panel WARNING for the one voice turn"

    # …and a connected panel's turn is silent.
    caplog.clear()
    b2 = PushBroadcaster()
    await b2.connect_panel(_FakeWS(), "zoe-touch-pi")
    monkeypatch.setattr(push_mod, "broadcaster", b2)
    with caplog.at_level(logging.WARNING, logger="push"):
        resp = await vt.voice_command(
            {"text": "could you think about this for me", "panel_id": "zoe-touch-pi", "session_id": "s2"},
            caller={"user_id": "guest", "panel_id": "zoe-touch-pi"}, stream=True, db=object(),
        )
        [_ async for _ in resp.body_iterator]
    assert not _deaf_records(caplog)


# ── a panel deaf for minutes while turns keep arriving: ERROR + counter ────────

def _error_records(caplog):
    return [r for r in caplog.records if r.levelno == logging.ERROR and "PANEL DEAF" in r.getMessage()]


@pytest.mark.asyncio
async def test_long_deafness_logs_a_rate_limited_error_and_counts_turns(caplog):
    """The 2026-10-14 incident: hours of deaf voice turns, one WARNING each, nobody looked."""
    b = PushBroadcaster()
    ws = _FakeWS()
    await b.connect_panel(ws, "zoe-touch-pi")
    b.disconnect(ws)
    gone = b.panel_push_status("zoe-touch-pi")["last_disconnect_at"]
    with caplog.at_level(logging.WARNING, logger="push"):
        # a blip (page reload / deploy restart): warning only
        assert b.warn_if_panel_deaf("zoe-touch-pi", now=1000.0, wall_now=gone + 20) is True
        assert not _error_records(caplog), "a 20 s blip is not an ERROR"
        assert b.panel_push_status("zoe-touch-pi")["deaf_alert"] is False
        # past the threshold: ERROR once
        t0 = 1000.0 + DEAF_PANEL_WARN_INTERVAL_S + 1
        assert b.warn_if_panel_deaf("zoe-touch-pi", now=t0, wall_now=gone + DEAF_PANEL_ERROR_AFTER_S + 5) is True
        errs = _error_records(caplog)
        assert len(errs) == 1 and "zoe-touch-pi" in errs[0].getMessage() and "push-status" in errs[0].getMessage()
        assert "5 min" in errs[0].getMessage() and "2 voice turn" in errs[0].getMessage(), errs[0].getMessage()
        st = b.panel_push_status("zoe-touch-pi", wall_now=gone + DEAF_PANEL_ERROR_AFTER_S + 5)
        assert st["deaf_turns"] == 2 and st["deaf_alert"] is True and st["deaf_for_s"] >= DEAF_PANEL_ERROR_AFTER_S
        # next turn inside the error interval: still a warning + counted, but NO second ERROR
        t1 = t0 + DEAF_PANEL_WARN_INTERVAL_S + 1
        assert b.warn_if_panel_deaf("zoe-touch-pi", now=t1, wall_now=gone + DEAF_PANEL_ERROR_AFTER_S + 40) is True
        assert len(_error_records(caplog)) == 1, "the ERROR is rate-limited"
        assert b.panel_push_status("zoe-touch-pi")["deaf_turns"] == 3
        # after the interval: ERROR again
        t2 = t0 + DEAF_PANEL_ERROR_INTERVAL_S + 1
        b.warn_if_panel_deaf("zoe-touch-pi", now=t2, wall_now=gone + DEAF_PANEL_ERROR_AFTER_S + DEAF_PANEL_ERROR_INTERVAL_S)
        assert len(_error_records(caplog)) == 2
    # reconnect clears the alert and the counter
    await b.connect_panel(_FakeWS(), "zoe-touch-pi")
    st = b.panel_push_status("zoe-touch-pi")
    assert st["deaf_turns"] == 0 and st["deaf_alert"] is False and st["deaf_for_s"] is None


@pytest.mark.asyncio
async def test_never_connected_panel_is_deaf_since_process_start(caplog):
    """last_connect_at null (the live signature): the clock starts at process start."""
    b = PushBroadcaster()
    with caplog.at_level(logging.WARNING, logger="push"):
        b.warn_if_panel_deaf("zoe-touch-pi", now=1.0, wall_now=b._started_at + 10)
        assert not _error_records(caplog)
        b.warn_if_panel_deaf("zoe-touch-pi", now=100.0, wall_now=b._started_at + DEAF_PANEL_ERROR_AFTER_S + 1)
        assert len(_error_records(caplog)) == 1
    assert b.panel_push_status("zoe-touch-pi", wall_now=b._started_at + DEAF_PANEL_ERROR_AFTER_S + 1)["deaf_alert"] is True


# ── panel_live: gate every "check the panel / tap Done" line ──────────────────

def test_panel_live_branches():
    b = PushBroadcaster()
    assert b.panel_live("zoe-touch-pi", now=1000.0) is False, "no subscriber, no ack: not live"
    assert b.panel_live("", now=1000.0) is False and b.panel_live(None, now=1000.0) is False

    b.note_panel_ack("zoe-touch-pi", now=990.0)
    assert b.panel_live("zoe-touch-pi", now=1000.0) is True, "acked 10 s ago: the page is alive (socket may be mid-reconnect)"
    assert b.panel_live("zoe-touch-pi", now=990.0 + 61) is False, "an ack older than ~60 s proves nothing"
    assert b.panel_live("other-panel", now=1000.0) is False, "acks are per panel"


@pytest.mark.asyncio
async def test_panel_live_with_a_subscriber_and_module_helper(monkeypatch):
    b = PushBroadcaster()
    monkeypatch.setattr(push_mod, "broadcaster", b)
    assert push_mod.panel_live("zoe-touch-pi") is False
    ws = _FakeWS()
    await b.connect_panel(ws, "zoe-touch-pi")
    assert b.panel_live("zoe-touch-pi") is True and push_mod.panel_live("zoe-touch-pi") is True
    b.disconnect(ws)
    assert push_mod.panel_live("zoe-touch-pi") is False


def test_form_replies_only_point_at_the_panel_when_it_is_live():
    import routers.voice_tts as vt

    live = vt._list_add_reply_text("milk", ["milk", "eggs"], True)
    # NOT "I've added ...": nothing is saved until Done is tapped (write-claim guard), so the live line says to tap Done to save.
    assert live == "Tap Done on the panel to save milk to your list." and "I've added" not in live
    # Dead panel: the flow only QUEUES a form (the insert happens on Done), so it must NOT claim a write.
    dead = vt._list_add_reply_text("milk", ["milk", "eggs", "bread"], False)
    assert "added" in dead and "haven't added milk" in dead, dead
    assert "I've added" not in dead, "no false write claim"
    assert "tap" not in dead.lower() and "check the panel" not in dead.lower(), dead
    assert "eggs and bread" in dead, "what IS on the list is said aloud"
    only = vt._list_add_reply_text("milk", ["milk"], False)
    assert "haven't added milk" in only and "I've added" not in only
    assert vt._list_add_reply_text("", ["a", "b"], False) == "Here's your shopping list: a and b."
    assert vt._list_add_reply_text("", [], False) == "Your shopping list is empty."

    cal_live = vt._calendar_form_reply_text("Dentist", "2026-10-14", "09:30", True)
    assert "tap Confirm" in cal_live
    cal_dead = vt._calendar_form_reply_text("Dentist", "2026-10-14", "09:30", False)
    assert "tap" not in cal_dead.lower() and "check the details" not in cal_dead
    assert "Dentist" in cal_dead and "haven't saved" in cal_dead, "no false 'saved' claim, and the draft is said aloud"


def test_panel_can_see_fails_closed_and_follows_the_broadcaster(monkeypatch):
    import routers.voice_tts as vt

    b = PushBroadcaster()
    monkeypatch.setattr(push_mod, "broadcaster", b)
    assert vt._panel_can_see("zoe-touch-pi") is False
    b.note_panel_ack("zoe-touch-pi")
    assert vt._panel_can_see("zoe-touch-pi") is True
    monkeypatch.setattr(push_mod, "broadcaster", object())   # liveness unreadable -> closed
    assert vt._panel_can_see("zoe-touch-pi") is False
