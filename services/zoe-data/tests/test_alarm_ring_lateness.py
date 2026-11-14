"""ALARM_RING_SEEN: how late a wall panel re-reads the ring state after the ringing set changed (gap audit 2026-11-14.6, the
number #2472's push is judged by). Counts and milliseconds only; one server clock. Before the push it measures the 2 s poll,
after it the push; it does not include the response's trip back or the panel's paint (named in the module docstring).

Pinned: the strict line format, first-panel-read-only, a member's read never consumes it, stale = -1, never raises, and the
ring-state response is byte-identical with the instrument stubbed out. Synthetic ids only. ``ci_safe``.
"""
from __future__ import annotations

import logging
import re

import pytest

import alarm_ring_lateness as arl
from test_alarms import SEVEN, _tick, _tz, _wall_clock, as_member, as_panel, box, dbfile, run  # noqa: F401  (fixtures)

pytestmark = pytest.mark.ci_safe

LINE_RE = re.compile(r"^ALARM_RING_SEEN event=(rang|snoozed|dismissed|auto_stopped|deleted|updated|other) ms=(-1|\d{1,6}) "
                     r"changes=\d{1,6}$")


class Clock:
    def __init__(self):
        self.t = 50.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _clean():
    arl.reset()
    yield
    arl.reset()


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("ALARM_RING_SEEN")]


def test_first_read_after_a_change_logs_its_lateness_once(caplog):
    caplog.set_level(logging.INFO, logger="alarm_ring_lateness")
    clk = Clock()
    arl.seen_by_panel(clock=clk)                                  # nothing changed: nothing to say
    arl.changed("rang", {"id": "a1", "label": "Secret label"}, clock=clk)
    clk.t += 0.25
    arl.changed("snoozed", {"id": "a1"}, clock=clk)               # folded: the lateness is from the FIRST unread change
    clk.t += 1.5
    arl.seen_by_panel(clock=clk)
    arl.seen_by_panel(clock=clk)
    assert _lines(caplog) == ["ALARM_RING_SEEN event=rang ms=1750 changes=2"]
    assert all(LINE_RE.match(x) for x in _lines(caplog)) and "Secret" not in caplog.text and "a1" not in caplog.text


def test_only_ring_set_changes_count():
    clk = Clock()
    for ev, row in (("missed", {}), ("updated", {"state": "idle"}), ("created", {}), ("updated", None)):
        arl.changed(ev, row, clock=clk)
    assert arl._pending is None
    arl.changed("updated", {"state": "ringing"}, clock=clk)       # an edit of a ringing alarm stops it
    assert arl._pending is not None


def test_a_change_nobody_reads_is_minus_one_and_the_next_starts_afresh(caplog):
    caplog.set_level(logging.INFO, logger="alarm_ring_lateness")
    clk = Clock()
    arl.changed("rang", {}, clock=clk)
    clk.t += arl.STALE_MS / 1000 + 1
    arl.changed("dismissed", {}, clock=clk)
    clk.t += 0.04
    arl.seen_by_panel(clock=clk)
    assert _lines(caplog) == ["ALARM_RING_SEEN event=rang ms=-1 changes=1", "ALARM_RING_SEEN event=dismissed ms=40 changes=1"]


def test_never_raises():
    def boom():
        raise RuntimeError("clock gone")
    arl.changed("rang", {}, clock=boom)
    arl.seen_by_panel(clock=boom)
    arl.changed(None, object())


def test_the_strict_format_rejects_text():
    assert not LINE_RE.match("ALARM_RING_SEEN event=rang ms=10 changes=1 label=Wake")
    assert not LINE_RE.match("ALARM_RING_SEEN event=Wake ms=10 changes=1")


def test_emit_notes_the_change_before_the_hooks_and_the_hooks_see_the_same(monkeypatch):
    import alarms

    seen = []

    async def hook(event, alarm):
        seen.append((event, alarm, arl._pending is not None))
    monkeypatch.setattr(alarms, "HOOKS", [hook])
    row = {"id": "a1", "state": "ringing"}
    run(alarms._emit("rang", row))
    assert seen == [("rang", row, True)]
    monkeypatch.setattr(arl, "changed", lambda *a, **k: 1 / 0)    # a broken instrument never breaks the hooks
    run(alarms._emit("dismissed", row))
    assert seen[-1][:2] == ("dismissed", row)


def test_over_http_the_panel_read_logs_and_the_response_is_unchanged(box, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="alarm_ring_lateness")
    c = box["client"]
    c.post("/api/alarms", json={"time": "07:00", "repeat": "daily", "label": "Wake"})
    run(_tick(box["path"], SEVEN))                                 # the engine rings it: one ring-set change
    as_member(box, "bob-0002")
    c.get("/api/alarms/ringing")                                   # a member's read is not the panel's
    assert _lines(caplog) == []
    as_panel(box)
    first = c.get("/api/alarms/ringing").json()
    (line,) = _lines(caplog)
    assert LINE_RE.match(line) and line.startswith("ALARM_RING_SEEN event=rang ")
    monkeypatch.setattr(arl, "seen_by_panel", lambda **k: None)
    assert c.get("/api/alarms/ringing").json() == first            # behaviour-neutral: the same ring state either way
