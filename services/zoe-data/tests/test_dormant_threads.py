"""dormant_threads: a quiet story touched by something new becomes ONE candidate - two pointers and a score, never words.

Precision first (lane C E4: "always remind" buys recall with false alarms), so every exclusion has its own case and every match its
negative control. Synthetic threads only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

import dormant_threads as dtm
import night_store
from dormant_threads import Signal

pytestmark = pytest.mark.ci_safe

TODAY = dt.date(2026, 10, 30)


def _thread(tid="t-shed", anchors="@harlan repaint shed primer weekend", status="quiet", last_day="2026-08-20", **kw):
    return night_store.thread_row(id=tid, user_id="member-a", title="repaint the shed", anchors=anchors, status=status,
                                  last_day=last_day, **kw)


def _sig(text, ref="m-1", day="2026-10-30", kind="turn"):
    return Signal(kind, ref, text, day)


def test_a_new_turn_that_touches_a_quiet_story_is_one_candidate():
    [c] = dtm.candidates([_thread()], [_sig("picked up primer for the shed this morning")], TODAY)
    assert (c.thread_id, c.signal_ref, c.signal_kind, c.names, c.words) == ("t-shed", "m-1", "turn", 0, 2)
    assert c.score == pytest.approx(3.0)
    assert set(c.as_dict()) == {"thread_id", "signal_kind", "signal_ref", "score", "names", "words"}     # pointers + counts only
    # negative control: one shared word is below the threshold
    assert dtm.candidates([_thread()], [_sig("the primer was on sale")], TODAY) == []


def test_a_shared_name_and_a_word_match_and_a_different_name_does_not():
    assert dtm.candidates([_thread()], [_sig("Harlan asked about the shed")], TODAY)
    # negative control: the same words about somebody else are another story
    assert dtm.candidates([_thread()], [_sig("Marisol asked about the shed and the primer")], TODAY) == []


def test_an_open_thread_quiet_long_enough_is_dormant_and_a_recent_one_is_not():
    old_open = _thread(status="open", last_day="2026-10-01")
    assert dtm.candidates([old_open], [_sig("primer for the shed")], TODAY)
    fresh_open = _thread(status="open", last_day="2026-10-28")
    assert dtm.candidates([fresh_open], [_sig("primer for the shed")], TODAY) == []


@pytest.mark.parametrize("thread", [
    _thread(status="resolved"),
    _thread(leave_reason="owner_said_no"),
    _thread(raise_policy="leave"),
    _thread(ignored_raises=2),
    _thread(topic="health"),
])
def test_never_a_leave_finished_or_sensitive_story(thread):
    assert dtm.candidates([thread], [_sig("primer for the shed")], TODAY) == []


def test_a_sensitive_signal_is_never_a_cue():
    t = _thread(anchors="repaint shed primer knee")
    assert dtm.candidates([t], [_sig("my knee hurt after painting the shed, primer everywhere")], TODAY) == []
    assert dtm.candidates([t], [_sig("painted the shed, primer everywhere")], TODAY)        # negative control


def test_only_a_signal_newer_than_the_last_mention_counts():
    t = _thread(last_day="2026-10-30", status="quiet")
    assert dtm.candidates([t], [_sig("primer for the shed", day="2026-10-30")], TODAY) == []
    assert dtm.candidates([_thread()], [_sig("primer for the shed", day="")], TODAY) == []          # an undated signal is not newer


def test_at_most_one_candidate_per_scan_the_best_score():
    a = _thread(tid="t-a", anchors="shed primer repaint")
    b = _thread(tid="t-b", anchors="@harlan shed primer")
    got = dtm.candidates([a, b], [_sig("Harlan brought primer for the shed")], TODAY)
    assert [c.thread_id for c in got] == ["t-b"]
    assert len(dtm.candidates([a, b], [_sig("Harlan brought primer for the shed")], TODAY, limit=5)) == 2


def test_scan_is_off_by_default_and_shadow_logs_ids_never_words(monkeypatch, caplog):
    store = night_store.MemoryBackend()
    asyncio.run(store.put_thread(_thread()))
    sig = [_sig("picked up primer for the shed", ref="msg-0001")]
    monkeypatch.delenv(dtm.ENV, raising=False)
    assert asyncio.run(dtm.scan("member-a", sig, today=TODAY, backend=store)) == []
    monkeypatch.setenv(dtm.ENV, "shadow")
    caplog.set_level("INFO", logger="dormant_threads")
    [c] = asyncio.run(dtm.scan("member-a", sig, today=TODAY, backend=store))
    line = next(r.getMessage() for r in caplog.records if "DORMANT_THREADS" in r.getMessage())
    assert "candidates=1" in line and "t-shed<-msg-0001" in line
    assert "primer" not in line and "shed " not in line and "repaint" not in line


def test_scan_reads_only_the_members_threads():
    store = night_store.MemoryBackend()
    asyncio.run(store.put_thread({**_thread(), "user_id": "member-b"}))
    assert asyncio.run(dtm.scan("member-a", [_sig("primer for the shed")], today=TODAY, backend=store, force=True)) == []


def test_a_forgotten_story_leaves_no_candidate():
    import re

    store = night_store.MemoryBackend()
    asyncio.run(store.put_thread(_thread()))
    sig = [_sig("primer for the shed")]
    assert asyncio.run(dtm.scan("member-a", sig, today=TODAY, backend=store, force=True))
    asyncio.run(store.erase_matching("member-a", re.compile("shed", re.IGNORECASE)))
    assert asyncio.run(dtm.scan("member-a", sig, today=TODAY, backend=store, force=True)) == []
