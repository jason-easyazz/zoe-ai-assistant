"""A finished timer is said ONCE, in Zoe's own voice, by the panel that set it (skybridge_service._ring_when_done).

Before: nothing server-side fired at expiry; the guest kiosk page chimed and fell back to the browser's speech voice because
``/api/voice/speak`` refuses the guest session. Now a timer set by a device-token voice turn remembers that panel, and its end
queues one panel-scoped ``timer_done`` row on the daemon's announcement lane.

Falsifiable pins: drop the ``announced`` mark and the idempotency/restart tests go red; drop ``timer_done`` from
PANEL_SCOPED_TRIGGERS and the other-panel claim test goes red; drop the idempotency key and the replay test goes red.
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


async def test_quiet_hours_the_page_speaks_it_instead(ann_db, monkeypatch):
    t = _set("member-a", "Tea", "panel-kitchen", 0.15)
    assert sky._timer_card(t)["props"]["spoken_by_panel"] is True  # outside quiet hours the panel will say it
    monkeypatch.setattr(sky, "_timer_quiet_now", lambda: True)
    assert sky._timer_card(t)["props"]["spoken_by_panel"] is False  # known before the end: the page speaks it
    await asyncio.sleep(0.4)
    assert await _rows(ann_db) == []
    assert not t.get("announced") and t["voice_failed"] is True
    monkeypatch.setattr(sky, "_timer_quiet_now", lambda: False)
    assert sky._timer_card(t)["props"]["spoken_by_panel"] is False  # still the page's, never both
    sky._timers._arm(t)
    await asyncio.sleep(0.2)
    assert await _rows(ann_db) == []


async def test_failed_queue_leaves_it_to_the_page(ann_db, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(voice_announce, "enqueue_announcement", boom)
    t = _set("member-a", "Tea", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    assert not t.get("announced") and t["voice_failed"] is True
    assert sky._timer_card(t)["props"]["spoken_by_panel"] is False


async def test_replay_after_a_crash_before_the_snapshot_adds_no_row(ann_db):
    """The snapshot write is asynchronous: a crash after the row was queued but before ``announced`` reached disk
    replays the queue on restore. The row id is keyed on timer id + due time, so the replay is a no-op."""
    t = _set("member-a", "Bread", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    t["announced"] = False  # as if the snapshot never recorded it
    await sky._queue_timer_done("member-a", t, 60)
    await sky._queue_timer_done("member-a", t, 60)
    rows = await _rows(ann_db)
    assert [r["message"] for r in rows] == ["Your Bread timer is done."]


async def test_a_different_due_time_is_a_different_row(ann_db):
    t = {"timer_id": "t9", "label": "Tea", "expires_at": 1000.0, "panel_id": "panel-kitchen"}
    await sky._queue_timer_done("member-a", t, 60)
    await sky._queue_timer_done("member-a", dict(t, expires_at=1060.0), 60)
    assert len(await _rows(ann_db)) == 2


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


def test_card_tells_the_page_the_panel_speaks_it(monkeypatch):
    monkeypatch.setattr(sky, "_timer_quiet_now", lambda: False)
    assert sky._timer_card({"timer_id": "a", "label": "Tea", "duration_seconds": 60, "expires_at": 1.0,
                            "panel_id": "panel-kitchen"})["props"]["spoken_by_panel"] is True
    assert sky._timer_card({"timer_id": "a", "label": "Tea", "duration_seconds": 60,
                            "expires_at": 1.0})["props"]["spoken_by_panel"] is False


_LATE_SPEECH_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const alarmBody = 'var _ACK_KEY' + src.split('var _ACK_KEY')[1].split('_tAlarmRefresh();')[0];
const refreshBody = 'function _tAlarmRefresh' + src.split('function _tAlarmRefresh')[1].split('function _beep')[0];
function page() {
  const els = {}; const mk = id => els[id] = els[id] || {id, innerHTML:'', onclick:null, classList:{add(){}, remove(){}}, appendChild(){}};
  const document = {getElementById: id => (id === 'tmrAlarm' || id === 'tmrAlarmX') ? mk(id) : null, createElement: () => mk('tmrAlarm'), body: mk('body')};
  const store = {}; const localStorage = {getItem:k => (k in store ? store[k] : null), setItem:(k,v) => { store[k] = v; }};
  const said = [], st = {timers: []};
  const SpeechSynthesisUtterance = function (t) { this.text = t; };
  const window = {speechSynthesis: {cancel(){}, speak(u){ said.push(u.text); }}}, speechSynthesis = window.speechSynthesis;
  const apiJson = () => Promise.resolve({}), timersGet = () => Promise.resolve({timers: st.timers});
  const _tAlarm = {known:{}, fired:{}, chime:null}, _tmMemo = {p:1}, _cur = 'home';
  const esc = x => x, sid = () => '', _beep = () => {}, setInterval = () => 1, clearInterval = () => {};
  eval(alarmBody + refreshBody + '; globalThis.__p = {_tAlarmRefresh, _tAlarmTick, els};');
  return Object.assign(globalThis.__p, {said, st});
}
const wait = ms => new Promise(r => setTimeout(r, ms));
const T = (spoken, exp) => ({timer_id: 't1', label: 'Tea', expires_at_ms: exp, spoken_by_panel: spoken});
(async () => {
  const out = {};
  for (const name of ['late_false', 'always_true', 'dismissed_first']) {
    const p = page(), exp = Date.now() + 40;
    p.st.timers = [T(true, exp)]; await p._tAlarmRefresh(); await wait(10);
    await wait(60); p._tAlarmTick();                          // fires: the panel would say it, so the page is silent
    const atFire = p.said.length;
    if (name === 'dismissed_first') { p.els.tmrAlarmX.onclick(); await wait(10); }
    p.st.timers = [T(name === 'always_true', exp)];
    await p._tAlarmRefresh(); await wait(10);                // the server now says the panel will not (or still will)
    await p._tAlarmRefresh(); await wait(10);                // a second poll must not say it again
    p._tAlarmTick();
    out[name] = {atFire, said: p.said};
  }
  console.log(JSON.stringify(out));
})();
"""


def test_page_speaks_a_silently_fired_alarm_once_when_the_panel_will_not():
    """Review round 2: the alarm fired silently (spoken_by_panel true on the last poll), then the server's queue failed
    or quiet hours began. The next poll that says false makes the page speak it ONCE; true stays silent; a dismissed
    alarm is never spoken."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    path = os.path.join(os.path.dirname(__file__), "..", "..", "zoe-ui", "dist", "touch", "home.html")
    out = subprocess.run([node, "-e", _LATE_SPEECH_HARNESS, path], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["late_false"] == {"atFire": 0, "said": ["Tea timer is done"]}
    assert r["always_true"] == {"atFire": 0, "said": []}
    assert r["dismissed_first"] == {"atFire": 0, "said": []}


async def _pending(conn):
    cur = await conn.execute("SELECT id FROM voice_announcements WHERE delivered_at IS NULL AND expired = 0")
    return [r[0] for r in await cur.fetchall()]


async def test_dismiss_withdraws_the_pending_row(ann_db):
    t = _set("member-a", "Pasta", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    assert len(await _pending(ann_db)) == 1
    assert sky.cancel_any_timer(t["timer_id"]) is not None  # the panel's Dismiss
    await asyncio.sleep(0.1)
    assert await _pending(ann_db) == []
    assert await voice_announce.claim_announcements(ann_db, panel_id="panel-kitchen") == []


async def test_owner_cancel_withdraws_the_pending_row(ann_db):
    t = _set("member-a", "Pasta", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    assert sky.cancel_timer_by_id("member-a", t["timer_id"]) is not None
    await asyncio.sleep(0.1)
    assert await _pending(ann_db) == []


async def test_a_claimed_row_is_left_alone(ann_db):
    t = _set("member-a", "Pasta", "panel-kitchen", 0.05)
    await asyncio.sleep(0.3)
    claimed = await voice_announce.claim_announcements(ann_db, panel_id="panel-kitchen")
    assert len(claimed) == 1
    sky.cancel_any_timer(t["timer_id"])
    await asyncio.sleep(0.1)
    cur = await ann_db.execute("SELECT delivered_at, expired FROM voice_announcements")
    row = await cur.fetchone()
    assert row["delivered_at"] is not None and row["expired"] == 0


async def test_revoke_is_a_no_op_without_a_row(ann_db):
    assert await voice_announce.revoke_pending(ann_db, dedupe_key="timer_done:none:1.000") is False
    assert await _rows(ann_db) == []
