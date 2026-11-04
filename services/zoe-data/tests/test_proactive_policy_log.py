"""Proactive policy LOG-ONLY run (``proactive/policy_log.py``, ``ZOE_PROACTIVE_POLICY_LOG``, lane F E3).

Fixtures only - synthetic ids and kinds, no household text. Negative controls (each run red before commit):
  * ``mode()`` always ``log``                                  -> the off-does-nothing tests red;
  * ``observe`` calls ``engine.fire_notification``             -> the never-delivers spy test red;
  * the log line carries ``item.text`` / the raw member id     -> the no-text-no-names test red;
  * drop the deny-class row (row 2)                            -> the deny-list test red (a leak);
  * drop the guest row (row 1)                                 -> the guest test red;
  * ``decide`` returns ``card`` for every owner-near kind      -> the table-mapping test red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # every edge is stubbed; slim-dep modules only

import contextlib
import logging
from datetime import datetime, timezone

from proactive import policy_log as pl
from proactive import pull
from proactive.pull import Item

NOW = datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc)   # 09:00 Perth
MEMBER = "member-a"
SECRET = "kestrel-juniper-aquarium"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    pl._reset_state()
    monkeypatch.delenv("ZOE_PROACTIVE_POLICY_LOG", raising=False)
    yield
    pl._reset_state()


def _item(kind="open_loop", n=1, sens="", surfaced=0):
    return Item(f"id{n}", kind, f"{kind}s:{n}", SECRET, "hint", 0.5, "cue", surfaced, sens)


# ── the decision table (pure) ────────────────────────────────────────────────
@pytest.mark.parametrize("kw,row,rung,action", [
    (dict(kind="open_loop", guest_present=True), 1, "silent", "none"),
    (dict(kind="open_loop", verified=False), 1, "silent", "none"),
    (dict(kind="open_loop", deny_class=True), 2, "silent", "none"),
    (dict(kind="emotional"), 2, "silent", "none"),
    (dict(kind="reminder"), 3, "speak", "speak"),
    (dict(kind="open_loop", requested_time=True), 3, "speak", "speak"),
    (dict(kind="open_loop", busy=True), 4, "silent", "hold"),
    (dict(kind="event", rest_window=True), 4, "silent", "hold"),
    (dict(kind="change_notice"), 5, "dot", "surface"),
    (dict(kind="change_notice", panel_near=True), 5, "card", "surface"),
    (dict(kind="event", panel_near=True), 5, "card", "surface"),
    (dict(kind="night_raise", panel_near=True), 6, "dot", "surface"),
    (dict(kind="inferred_fact"), 7, "card", "surface"),
    (dict(kind="date"), 8, "dot", "surface"),
    (dict(kind="date", date_is_today=True, panel_near=True), 8, "card", "surface"),
    (dict(kind="keep_in_touch"), 9, "silent", "none"),
    (dict(kind="keep_in_touch", cadence_opt_in=True), 9, "dot", "surface"),
    (dict(kind="open_loop"), 10, "dot", "surface"),
    (dict(kind="open_loop", panel_near=True), 10, "card", "surface"),
    (dict(kind="routine"), 11, "silent", "none"),
    (dict(kind="routine", routine_hints_enabled=True), 11, "dot", "surface"),
    (dict(kind="open_loop", ignored_in_class=2), 12, "silent", "suppress"),
    (dict(kind="open_loop", panel_near=True, week_cards=3), 13, "silent", "hold"),
    (dict(kind="night_raise", week_dots=7), 13, "silent", "hold"),
    (dict(kind="commitment"), 14, "silent", "none"),
    (dict(kind="other"), 14, "silent", "none"),
])
def test_table_maps_signals_to_documented_rungs(kw, row, rung, action):
    d = pl.decide(pl.Signals(**kw))
    assert (d.row, d.rung, d.action) == (row, rung, action), d


def test_privacy_rows_beat_everything():
    # a guest or a deny-list class wins over a member-requested time and over a due open loop
    assert pl.decide(pl.Signals(kind="reminder", guest_present=True)).row == 1
    assert pl.decide(pl.Signals(kind="reminder", deny_class=True)).row == 2
    assert pl.decide(pl.Signals(kind="open_loop", deny_class=True, panel_near=True)).rung == "silent"


def test_only_row_3_ever_speaks():
    for kind in pl.KINDS:
        for near in (True, False):
            d = pl.decide(pl.Signals(kind=kind, panel_near=near, date_is_today=True, cadence_opt_in=True,
                                     routine_hints_enabled=True))
            assert d.rung != "speak" or d.row == 3, (kind, d)


# ── flag ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("val,want", [("", "off"), ("off", "off"), ("1", "off"), ("true", "off"), ("LOG", "log"), ("log", "log")])
def test_mode_only_log_turns_it_on(monkeypatch, val, want):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", val)
    assert pl.mode() == want


# ── the seam ─────────────────────────────────────────────────────────────────
class _Spies:
    def __init__(self, monkeypatch):
        self.calls: list[str] = []
        from proactive import engine, lines, selector

        def spy(name):
            def _f(*a, **k):
                self.calls.append(name)
                raise AssertionError(f"delivery path touched: {name}")
            return _f

        async def aspy(*a, **k):
            self.calls.append("async")
            raise AssertionError("delivery path touched")

        monkeypatch.setattr(engine, "fire_notification", aspy)
        monkeypatch.setattr(lines, "record", aspy, raising=False)
        monkeypatch.setattr(pull, "compose", spy("compose"))
        monkeypatch.setattr(pull, "pull", aspy)
        monkeypatch.setattr(pull, "tap", aspy)
        monkeypatch.setattr(selector, "settle", aspy)
        monkeypatch.setattr(selector, "prepare", aspy)


def _env(monkeypatch, tier="owner", busy=False, quiet=False, night=()):
    async def _tier(uid):
        return tier

    async def _night(uid, now):
        return list(night)

    monkeypatch.setattr(pl, "_tier", _tier)
    monkeypatch.setattr(pl, "_busy", lambda: busy)
    monkeypatch.setattr(pull, "_quiet", lambda now: quiet)
    monkeypatch.setattr(pl, "_night_raise_refs", _night)


async def test_off_does_nothing(monkeypatch, caplog):
    def boom(*a, **k):
        raise AssertionError("signal read while off")

    monkeypatch.setattr(pl, "_tier", boom)
    monkeypatch.setattr(pl, "_busy", boom)
    monkeypatch.setattr(pl, "_night_raise_refs", boom)
    with caplog.at_level(logging.DEBUG):
        assert await pl.observe(MEMBER, [_item()], NOW) is None
    assert not [r for r in caplog.records if "PROACTIVE_POLICY" in r.getMessage()]
    assert pl._seen == set() and pl._week == {}


async def test_log_mode_records_rungs_and_never_delivers(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    spies = _Spies(monkeypatch)
    _env(monkeypatch, tier="owner", night=["night_threads:t1"])
    items = [_item("open_loop", 1), _item("event", 2), _item("emotional", 3), _item("open_loop", 4, sens="health")]
    with caplog.at_level(logging.INFO, logger=pl.logger.name):
        counts = await pl.observe(MEMBER, items, NOW)
    assert spies.calls == []
    assert counts == {"silent": 2, "dot": 1, "card": 2, "speak_on_pull": 0, "speak": 0}
    msgs = [r.getMessage() for r in caplog.records if "PROACTIVE_POLICY " in r.getMessage()]
    assert len(msgs) == 5
    assert any("kind=open_loop row=10 rung=card" in m for m in msgs)
    assert any("kind=emotional row=2 rung=silent" in m for m in msgs)
    assert any("kind=night_raise row=6 rung=dot" in m for m in msgs)
    assert any("row=2" in m and "id4" in m for m in msgs)          # the health-class item is on the deny row


async def test_deny_class_item_is_never_surfaced(monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    _env(monkeypatch, tier="owner")
    counts = await pl.observe(MEMBER, [_item("open_loop", 1, sens="grief,money"), _item("event", 2, sens="other_member")], NOW)
    assert counts["card"] == 0 and counts["dot"] == 0 and counts["silent"] == 2


async def test_guest_busy_and_quiet_hold_everything(monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    for kw in (dict(tier="bound_guest"), dict(busy=True), dict(quiet=True)):
        pl._reset_state()
        _env(monkeypatch, **kw)
        counts = await pl.observe(MEMBER, [_item("open_loop", 1), _item("event", 2)], NOW)
        assert counts["silent"] == 2, kw


async def test_absent_panel_degrades_cards_to_dots(monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    _env(monkeypatch, tier="absent")
    counts = await pl.observe(MEMBER, [_item("open_loop", 1)], NOW)
    assert counts["dot"] == 1 and counts["card"] == 0


async def test_logs_carry_no_text_and_no_member_name(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    _env(monkeypatch, tier="owner", night=["night_threads:kestrel"])
    with caplog.at_level(logging.DEBUG):
        await pl.observe(MEMBER, [_item("open_loop", 1), _item("emotional", 2, sens="grief")], NOW)
    blob = "\n".join(r.getMessage() for r in caplog.records).lower()
    assert "proactive_policy" in blob
    for banned in ("kestrel", "juniper", "aquarium", MEMBER, "grief", "night_threads"):
        assert banned not in blob, banned


async def test_repeat_observation_logs_once_and_caps_weekly(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    _env(monkeypatch, tier="owner")
    with caplog.at_level(logging.INFO, logger=pl.logger.name):
        await pl.observe(MEMBER, [_item("open_loop", 1)], NOW)
        await pl.observe(MEMBER, [_item("open_loop", 1)], NOW)
    assert len([r for r in caplog.records if "PROACTIVE_POLICY " in r.getMessage()]) == 1
    # four distinct due loops in one week: the fourth card is held by the weekly cap (row 13)
    pl._reset_state()
    counts = await pl.observe(MEMBER, [_item("open_loop", n) for n in range(1, 5)], NOW)
    assert counts["card"] == 3 and counts["silent"] == 1


async def test_observe_never_raises(monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")

    async def boom(uid):
        raise RuntimeError("down")

    monkeypatch.setattr(pl, "_tier", boom)
    assert await pl.observe(MEMBER, [_item()], NOW) is None


# ── wired through the orb poll ───────────────────────────────────────────────
async def _poll(monkeypatch, items):
    import db_compat

    @contextlib.asynccontextmanager
    async def _db():
        yield object()

    async def _items(db, uid, now=None):
        return items

    monkeypatch.setattr(db_compat, "get_compat_db", _db)
    monkeypatch.setattr(pull, "pending_items", _items)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    return await pull.pending_state(MEMBER, NOW)


async def test_orb_poll_state_is_byte_identical_off_and_log(monkeypatch):
    _env(monkeypatch, tier="owner")
    off = await _poll(monkeypatch, [_item("open_loop", 1)])
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    spies = _Spies(monkeypatch)
    pl._reset_state()
    on = await _poll(monkeypatch, [_item("open_loop", 1)])
    assert off == on and set(on) == {"enabled", "count", "top", "quiet"}
    assert spies.calls == []
    assert pl._seen, "log mode did observe through the poll"


async def test_poll_observer_failure_never_breaks_the_poll(monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")

    async def boom(uid):
        raise RuntimeError("x")

    monkeypatch.setattr(pl, "_tier", boom)
    state = await _poll(monkeypatch, [_item("open_loop", 1)])
    assert state["count"] == 1


async def test_observer_level_fault_or_stall_never_breaks_the_poll(monkeypatch):
    import asyncio

    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")

    async def boom(*a, **k):
        raise RuntimeError("observer exploded")

    monkeypatch.setattr(pl, "observe", boom)
    assert (await _poll(monkeypatch, [_item("open_loop", 1)]))["count"] == 1

    async def stall(*a, **k):
        await asyncio.sleep(60)

    real = asyncio.wait_for
    monkeypatch.setattr(asyncio, "wait_for", lambda aw, timeout: real(aw, timeout=0.05))
    monkeypatch.setattr(pl, "observe", stall)
    assert (await _poll(monkeypatch, [_item("open_loop", 1)]))["count"] == 1


async def test_unverified_member_is_row_1_through_the_observer(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_PROACTIVE_POLICY_LOG", "log")
    _env(monkeypatch, tier="owner")
    with caplog.at_level(logging.INFO, logger=pl.logger.name):
        counts = await pl.observe("guest", [_item("open_loop", 1), _item("event", 2)], NOW)
    assert counts["silent"] == 2 and counts["card"] == 0 and counts["dot"] == 0
    assert all("row=1 " in r.getMessage() for r in caplog.records if "PROACTIVE_POLICY " in r.getMessage())
