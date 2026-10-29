"""Live bug 2026-10-29: a voice timer set by a recognised member never rang on the wall panel.

Two defects, both pinned here:
1. OWNER MISMATCH - the voice turn stores the timer under the member who spoke (``jason``) but the kiosk polls
   ``GET /api/skybridge/timers`` as a guest (a device token/kiosk is the panel, not a member - #2089), so the
   owner-keyed lookup returned [] and the kiosk's alarm watcher never learned the timer.
2. NOT DURABLE - the registry was process-local, so any zoe-data restart dropped every running timer.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import panel_principal  # noqa: E402
from routers import skybridge as sky_router  # noqa: E402
from skybridge_service import _TimerStore, _timers, create_timer_direct  # noqa: E402

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_TIMER_STORE", str(tmp_path / "timers.json"))
    _timers._by_owner.clear()
    _timers._loaded = True
    yield
    _timers._by_owner.clear()


_MODES: list = []


def _as_panel(monkeypatch, is_panel: bool):
    _MODES.clear()

    async def fake(request, user, db=None, *, mode=None):
        _MODES.append(mode)
        return is_panel

    monkeypatch.setattr(panel_principal, "is_panel_guest", fake)


def _get(user):
    return asyncio.run(sky_router.list_skybridge_timers(object(), user))["timers"]


def _tid(result):
    return result["cards"][0]["props"]["timer_id"]


def test_panel_sees_a_timer_a_member_set_by_voice(monkeypatch):
    create_timer_direct("jason", minutes=0.5)
    _as_panel(monkeypatch, True)
    seen = _get({"user_id": "guest", "role": "guest", "panel_id": "zoe-touch-pi"})
    assert len(seen) == 1 and seen[0]["expires_at_ms"] > time.time() * 1000


def test_non_panel_guest_does_not_see_a_members_timer(monkeypatch):
    create_timer_direct("jason", minutes=1)
    _as_panel(monkeypatch, False)
    assert _get({"user_id": "guest", "role": "guest"}) == []


def test_member_still_sees_only_their_own(monkeypatch):
    create_timer_direct("jason", minutes=1)
    create_timer_direct("someone-else", minutes=1)
    _as_panel(monkeypatch, False)
    assert len(_get({"user_id": "jason"})) == 1


def test_panel_can_cancel_a_members_timer(monkeypatch):
    tid = _tid(create_timer_direct("jason", minutes=1))
    _as_panel(monkeypatch, True)
    out = asyncio.run(sky_router.cancel_skybridge_timer(object(), {"timer_id": tid}, {"user_id": "guest", "panel_id": "p"}))
    assert out["ok"] is True and _timers.all_live() == []


def test_timers_survive_a_restart():
    tid = _tid(create_timer_direct("jason", minutes=2))
    reborn = _TimerStore()  # a fresh process: nothing in memory
    assert [x["timer_id"] for x in reborn.list("jason")] == [tid]


def test_expired_timer_is_not_resurrected_and_cancel_persists():
    store = _TimerStore()
    gone = store.create("jason", "old", 1)
    gone["expires_at"] = time.time() - 5
    kept = store.create("jason", "keep", 600)  # create() saves, expired one is pruned on the way
    assert [t["timer_id"] for t in _TimerStore().list("jason")] == [kept["timer_id"]]
    store.cancel_by_id("jason", kept["timer_id"])
    assert _TimerStore().list("jason") == []


def test_corrupt_snapshot_is_harmless(tmp_path):
    (tmp_path / "timers.json").write_text("{not json")
    assert _TimerStore().list("jason") == []


def test_route_asks_the_strict_registered_predicate(monkeypatch):
    _as_panel(monkeypatch, False)
    _get({"user_id": "guest"})
    asyncio.run(sky_router.cancel_skybridge_timer(object(), {"timer_id": "x"}, {"user_id": "guest"}))
    assert _MODES == ["registered", "registered"]  # a weaker mode ("lan": any phone with a guest session) must fail here


def test_real_predicate_panel_principal_sees_all_member_does_not():
    create_timer_direct("jason", minutes=1)
    panel = {"user_id": "guest", "role": "guest", "panel_id": "zoe-touch-pi"}
    assert len(_get(panel)) == 1  # the REAL is_panel_guest: a device-token principal carries panel_id
    # a signed-in member who is not the panel never gets the household view (and sees only their own bucket)
    assert _get({"user_id": "someone-else", "role": "member"}) == []
    # an auth-outage guest is never evidence of the panel
    assert _get({"user_id": "guest", "role": "guest", "panel_id": "x", "auth_degraded": True}) == []


def test_wrongly_typed_snapshot_record_is_dropped_not_fatal(tmp_path):
    import json

    good = {"timer_id": "g", "label": "ok", "duration_seconds": 60, "expires_at": time.time() + 60}
    bad = {"timer_id": "b", "label": "x", "duration_seconds": 60, "expires_at": "soon"}
    (tmp_path / "timers.json").write_text(json.dumps({"by_owner": {"jason": [bad, good]}}))
    assert [t["timer_id"] for t in _TimerStore().list("jason")] == ["g"]


def test_malformed_owner_collection_does_not_lose_other_owners(tmp_path):
    import json

    good = {"timer_id": "g", "label": "ok", "duration_seconds": 60, "expires_at": time.time() + 60}
    (tmp_path / "timers.json").write_text(json.dumps({"by_owner": {"broken": None, "odd": 7, "jason": [good]}}))
    assert [t["timer_id"] for t in _TimerStore().list("jason")] == ["g"]
    (tmp_path / "timers.json").write_text(json.dumps({"by_owner": [1, 2]}))
    assert _TimerStore().list("jason") == []


def test_load_drops_timers_older_than_the_grace(tmp_path):
    import json

    import skybridge_service

    old = {"timer_id": "o", "label": "x", "duration_seconds": 5, "expires_at": time.time() - skybridge_service.TIMER_RING_GRACE_S - 5}
    (tmp_path / "timers.json").write_text(json.dumps({"by_owner": {"jason": [old]}}))
    store = _TimerStore()
    assert store.all_live() == []


# -- a finished timer stays listed ("ringing") until dismissed or the grace ends --------------------------------------

PANEL = {"user_id": "guest", "role": "guest", "panel_id": "p"}


def _clock(monkeypatch, offset):
    import skybridge_service

    real = time.time
    monkeypatch.setattr(skybridge_service.time, "time", lambda: real() + offset[0])


def test_five_second_timer_polled_twenty_seconds_later_still_rings(monkeypatch):
    offset = [0.0]
    _clock(monkeypatch, offset)
    _as_panel(monkeypatch, True)
    create_timer_direct("jason", minutes=5 / 60)
    offset[0] = 20.0
    seen = _get(PANEL)
    assert len(seen) == 1 and seen[0]["status"] == "ringing"
    assert seen[0]["expires_at_ms"] < (time.time() + 20.0) * 1000  # past due on first sight: the kiosk rings at once


def test_dismissed_timer_does_not_reappear_and_grace_expires(monkeypatch):
    import skybridge_service

    offset = [0.0]
    _clock(monkeypatch, offset)
    _as_panel(monkeypatch, True)
    tid = _tid(create_timer_direct("jason", minutes=5 / 60))
    offset[0] = 20.0
    assert len(_get(PANEL)) == 1
    out = asyncio.run(sky_router.cancel_skybridge_timer(object(), {"timer_id": tid}, PANEL))
    assert out["ok"] is True
    assert _get(PANEL) == []
    # an UNacknowledged one is dropped once the grace passes
    create_timer_direct("jason", minutes=5 / 60)
    offset[0] = 20.0 + 5 + skybridge_service.TIMER_RING_GRACE_S + 1
    assert _get(PANEL) == []


def test_a_ringing_timer_is_not_a_running_one_for_speech(monkeypatch):
    offset = [0.0]
    _clock(monkeypatch, offset)
    create_timer_direct("jason", minutes=5 / 60)
    offset[0] = 20.0
    assert _timers.list("jason") == []  # "how long is left" / "cancel my timer" never target a finished one
    assert _timers.cancel("jason") is None
    assert len(_timers.all_live()) == 1


def test_kiosk_dismiss_acknowledges_through_cancel():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "zoe-ui", "dist", "touch", "home.html")
    html = open(path, encoding="utf-8").read()
    assert "timerAlarm(due.join(' / '),ids)" in html
    alarm = html.split("function timerAlarm")[1].split("_tAlarmRefresh();")[0]
    assert "apiJson('POST','/api/skybridge/timers/cancel',{timer_id:id})" in alarm


_ALARM_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const body = 'function timerAlarm' + src.split('function timerAlarm')[1].split('_tAlarmRefresh();')[0];
const els = {};
function mk(id){ return els[id] = els[id] || {id, innerHTML:'', onclick:null, classList:{add(){}, remove(){}}, appendChild(){}}; }
const document = {getElementById: id => (id === 'tmrAlarm' || id === 'tmrAlarmX') ? mk(id) : null,
                  createElement: () => mk('tmrAlarm'), body: mk('body')};
const cancelled = [];
const apiJson = (m, url, b) => { cancelled.push(b.timer_id); return Promise.resolve({}); };
const _tAlarm = {known:{}, fired:{}, chime:null}, _tmMemo = {p:1}, _cur = 'home', window = {};
const esc = x => x, _beep = () => {}, setInterval = () => 1, clearInterval = () => {};
eval(body);
timerAlarm('Pasta', ['t1']);
timerAlarm('Eggs', ['t2']);            // a second timer ends before the first alarm is dismissed
els.tmrAlarmX.onclick();
console.log(JSON.stringify(cancelled.sort()));
"""


def test_kiosk_dismiss_acknowledges_every_timer_that_rang_before_it():
    """Two timers ending on different ticks before Dismiss: BOTH ids are cancelled (review 2026-10-29). Acking only
    the last one left the first "ringing" on the server, so the next voice-turn reload re-rang it."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    path = os.path.join(os.path.dirname(__file__), "..", "..", "zoe-ui", "dist", "touch", "home.html")
    out = subprocess.run([node, "-e", _ALARM_HARNESS, path], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ["t1", "t2"]
