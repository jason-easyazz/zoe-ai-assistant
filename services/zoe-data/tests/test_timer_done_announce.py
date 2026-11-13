"""A finished timer is said ONCE, in Zoe's own voice, by the panel that set it (skybridge_service._ring_when_done).

Before: nothing server-side fired at expiry; the guest kiosk page chimed and fell back to the browser's speech voice because
``/api/voice/speak`` refuses the guest session. Now a timer set by a device-token voice turn remembers that panel, and its end
queues one panel-scoped ``timer_done`` row on the daemon's announcement lane.

Falsifiable pins: drop the ``announced`` mark and the idempotency/restart tests go red; drop ``timer_done`` from
PANEL_SCOPED_TRIGGERS and the other-panel claim test goes red; drop the disarm on cancel and the dismiss test goes red.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db_compat  # noqa: E402
import skybridge_service as sky  # noqa: E402
import voice_announce  # noqa: E402

pytestmark = pytest.mark.ci_safe

_SCHEMA = """CREATE TABLE voice_announcements (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT, message TEXT NOT NULL, trigger_type TEXT DEFAULT '',
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, delivered_at TEXT, delivered_to TEXT,
    expired INTEGER NOT NULL DEFAULT 0, played_at TEXT)"""


@pytest.fixture
async def ann_db(tmp_path, monkeypatch):
    """A private timer store and registry, quiet hours off, and an in-memory announcement table behind get_compat_db."""
    monkeypatch.setenv("ZOE_TIMER_STORE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(sky._timers, "_by_owner", {})
    monkeypatch.setattr(sky._timers, "_loaded", True)
    monkeypatch.setattr(sky._timers, "_last_write", None)
    monkeypatch.setattr(sky._timers, "_rings", {})
    monkeypatch.setattr(sky, "_timer_quiet_now", lambda: False)
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(_SCHEMA)
    await conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield conn

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    try:
        yield conn
    finally:
        for task in list(sky._timers._rings.values()):
            task.cancel()
        sky._timers.wait_saved()
        await conn.close()


async def _rows(conn):
    cur = await conn.execute("SELECT user_id, panel_id, message, trigger_type FROM voice_announcements")
    return [dict(r) for r in await cur.fetchall()]


def _set(owner: str, label: str, panel: str, ends_in: float) -> dict:
    """Create a timer as a voice turn from ``panel`` would, then pull its end close so the test does not wait minutes."""
    sky.bind_timer_panel(panel)
    t = sky._timers.create(owner, label, 60)
    t["expires_at"] = time.time() + ends_in
    sky._timers._arm(t)
    return t


async def test_expiry_queues_one_line_for_the_panel_that_set_it(ann_db):
    _set("member-a", "Pasta", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    rows = await _rows(ann_db)
    assert rows == [{"user_id": "member-a", "panel_id": "panel-kitchen", "message": "Your Pasta timer is done.",
                     "trigger_type": "timer_done"}]


async def test_never_twice_even_when_re_armed(ann_db):
    t = _set("member-a", "Timer", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    sky._timers._arm(t)  # a second arm / restore after it spoke
    sky._timers.arm_restored()
    await asyncio.sleep(0.2)
    rows = await _rows(ann_db)
    assert [r["message"] for r in rows] == ["Your timer is done."]


async def test_dismiss_before_expiry_queues_nothing(ann_db):
    t = _set("member-a", "Tea", "panel-kitchen", 0.15)
    assert sky.cancel_any_timer(t["timer_id"]) is not None
    await asyncio.sleep(0.4)
    assert await _rows(ann_db) == []
    assert sky._timers._rings == {}


async def test_no_panel_bound_means_no_announcement(ann_db):
    """A typed / browser / intent-dispatch timer binds no panel: the page alone rings it."""
    _set("member-a", "Tea", "", 0.05)
    await asyncio.sleep(0.3)
    assert await _rows(ann_db) == []


async def test_two_timers_each_said_on_its_own_panel(ann_db):
    _set("member-a", "Eggs", "panel-kitchen", 0.05)
    _set("member-b", "Bath", "panel-hall", 0.1)
    await asyncio.sleep(0.4)
    rows = sorted(await _rows(ann_db), key=lambda r: r["panel_id"])
    assert [(r["panel_id"], r["message"]) for r in rows] == [
        ("panel-hall", "Your Bath timer is done."), ("panel-kitchen", "Your Eggs timer is done.")]


async def test_quiet_hours_hold_the_line_and_it_is_not_said_later(ann_db, monkeypatch):
    monkeypatch.setattr(sky, "_timer_quiet_now", lambda: True)
    t = _set("member-a", "Tea", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    assert await _rows(ann_db) == []
    assert t["announced"] is True
    monkeypatch.setattr(sky, "_timer_quiet_now", lambda: False)
    sky._timers._arm(t)
    await asyncio.sleep(0.2)
    assert await _rows(ann_db) == []


def _snapshot(path: str, ended_ago: float, announced: bool = False) -> None:
    now = time.time()
    rec = {"timer_id": "t1", "label": "Bread", "duration_seconds": 600, "set_seconds": 600, "created_at": now - 600,
           "expires_at": now - ended_ago, "panel_id": "panel-kitchen", "announced": announced}
    with open(path, "w") as fh:
        json.dump({"by_owner": {"member-a": [rec]}}, fh)


async def _restart(monkeypatch):
    sky._timers.wait_saved()
    monkeypatch.setattr(sky._timers, "_by_owner", {})
    monkeypatch.setattr(sky._timers, "_loaded", False)
    await sky.preload_timers()
    await asyncio.sleep(0.2)


async def test_restart_inside_the_grace_speaks_once(ann_db, monkeypatch):
    _snapshot(os.environ["ZOE_TIMER_STORE"], ended_ago=30)
    await _restart(monkeypatch)
    assert [r["message"] for r in await _rows(ann_db)] == ["Your Bread timer is done."]
    await _restart(monkeypatch)  # the announced mark was snapshotted: a second restart says nothing
    assert len(await _rows(ann_db)) == 1


async def test_restart_outside_the_grace_says_nothing(ann_db, monkeypatch):
    _snapshot(os.environ["ZOE_TIMER_STORE"], ended_ago=sky.TIMER_RING_GRACE_S + 30)
    await _restart(monkeypatch)
    assert await _rows(ann_db) == []


async def test_restart_before_the_end_rearms(ann_db, monkeypatch):
    _snapshot(os.environ["ZOE_TIMER_STORE"], ended_ago=-0.1)  # still running at restart
    await _restart(monkeypatch)
    await asyncio.sleep(0.2)
    assert len(await _rows(ann_db)) == 1


async def test_trigger_is_panel_scoped_another_panel_cannot_claim_it(ann_db):
    assert sky.TIMER_DONE_TRIGGER in voice_announce.PANEL_SCOPED_TRIGGERS
    _set("member-a", "Pasta", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    assert await voice_announce.claim_announcements(ann_db, panel_id="panel-hall") == []
    mine = await voice_announce.claim_announcements(ann_db, panel_id="panel-kitchen")
    assert [a["text"] for a in mine] == ["Your Pasta timer is done."]


async def test_device_token_voice_auth_binds_the_panel(monkeypatch):
    import panel_principal
    import routers.voice_tts as voice_tts

    monkeypatch.setattr(panel_principal, "device_token_is_panel", lambda request: True)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(panel_principal, "record_turn_home_control", noop)
    monkeypatch.setattr(voice_tts, "_request_via_relay", lambda r: False)
    monkeypatch.setattr(voice_tts, "_request_via_tunnel", lambda r: False)
    sky.bind_timer_panel("")
    await voice_tts._require_voice_auth(object(), device={"panel_id": "panel-kitchen", "raw_token": "x"}, user={})
    bound = sky._timer_panel.get()
    sky.bind_timer_panel("")
    assert bound == "panel-kitchen"


def test_card_tells_the_page_the_panel_speaks_it():
    assert sky._timer_card({"timer_id": "a", "label": "Tea", "duration_seconds": 60, "expires_at": 1.0,
                            "panel_id": "panel-kitchen"})["props"]["spoken_by_panel"] is True
    assert sky._timer_card({"timer_id": "a", "label": "Tea", "duration_seconds": 60,
                            "expires_at": 1.0})["props"]["spoken_by_panel"] is False
