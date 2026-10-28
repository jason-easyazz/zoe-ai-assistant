"""Live bug 2026-10-29: a voice timer set by a recognised member never rang on the wall panel.

Two defects, both pinned here:
1. OWNER MISMATCH - the voice turn stores the timer under the member who spoke (``jason``) but the kiosk polls
   ``GET /api/skybridge/timers`` as a guest (a device token/kiosk is the panel, not a member - #2089), so the
   owner-keyed lookup returned [] and the kiosk's alarm watcher never learned the timer.
2. NOT DURABLE - the registry was process-local, so any zoe-data restart dropped every running timer.
"""

from __future__ import annotations

import asyncio
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


def _as_panel(monkeypatch, is_panel: bool):
    async def fake(request, user, db=None, *, mode=None):
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
