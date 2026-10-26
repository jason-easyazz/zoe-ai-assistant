"""local_text_logs (PR #2110 review): the intent-miss log's append, sweep and erase all take ONE file lock.

Break: restore ``if log is not ROUTER_SHADOW: yield; return`` in ``locked()`` -> a pass no longer excludes a writer / another pass.
"""
from __future__ import annotations

import fcntl
import json
import re
import threading

import pytest

import local_text_logs as ltl

pytestmark = pytest.mark.ci_safe


@pytest.fixture
def miss(tmp_path, monkeypatch):
    p = tmp_path / "misses.jsonl"
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", str(p))
    return p


def test_append_line_writes_a_json_row(miss):
    assert ltl.append_line(ltl.INTENT_MISSES, json.dumps({"text": "hello", "ts": 1.0})) is True
    assert json.loads(miss.read_text())["text"] == "hello"


def test_an_append_is_skipped_while_a_pass_holds_the_lock(miss):
    miss.write_text(json.dumps({"text": "x", "ts": 1.0}) + "\n")
    with ltl.locked(ltl.INTENT_MISSES):
        assert ltl.append_line(ltl.INTENT_MISSES, json.dumps({"text": "late", "ts": 2.0})) is False
    assert "late" not in miss.read_text()


def test_a_pass_waits_for_a_holder_of_the_file_lock(miss, monkeypatch):
    """Another holder has the flock: the erase must NOT rewrite meanwhile (it times out, fail closed)."""
    miss.write_text(json.dumps({"text": "forgotten-person", "ts": 1.0}) + "\n")
    monkeypatch.setattr(ltl, "LOCK_WAIT_S", 0.3)
    with open(ltl.lock_path(ltl.INTENT_MISSES), "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        with pytest.raises(TimeoutError):
            ltl.erase_all_text()
        fcntl.flock(other, fcntl.LOCK_UN)
    assert ltl.erase_all_text() == 1


def test_a_write_racing_an_erase_never_survives_it(miss):
    """A writer hammers the log while erases run; once it stops, one more erase leaves no forgotten word."""
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            ltl.append_line(ltl.INTENT_MISSES, json.dumps({"text": "forgotten-name here", "ts": 1.0}))

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(15):
            ltl.erase_entity(re.compile("forgotten-name"))
    finally:
        stop.set()
        t.join()
    ltl.erase_entity(re.compile("forgotten-name"))
    assert "forgotten-name" not in (miss.read_text() if miss.exists() else "")


def test_an_unopenable_lock_file_fails_closed(miss):
    """PR #2110 review: a lock file that cannot be opened must NOT let a pass run unlocked (it could race an append and restore
    forgotten words), and must make the writer skip. Break: restore ``yield`` / ``return`` on OSError in locked()/append_line()."""
    miss.write_text(json.dumps({"text": "forgotten-person", "ts": 1.0}) + "\n")
    ltl.lock_path(ltl.INTENT_MISSES).mkdir()                 # a directory where the lock FILE should be: open(..., "a") -> IsADirectoryError
    try:
        with pytest.raises(OSError):
            ltl.erase_all_text()
        assert ltl.sweep().get("intent_misses") == -1          # the sweep reports the failed log rather than raising
        before = miss.read_text()
        assert ltl.append_line(ltl.INTENT_MISSES, json.dumps({"text": "late", "ts": 2.0})) is False
        assert miss.read_text() == before
    finally:
        ltl.lock_path(ltl.INTENT_MISSES).rmdir()


def _miss_rows(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def test_a_miss_prepared_before_an_erase_is_not_appended_after_it(miss, monkeypatch):
    """detect_intent captures the erase epoch BEFORE detection; an erase completing mid-detect drops the miss.
    Break: drop ``epoch=_miss_epoch`` in intent_router or the epoch check in append_line -> the stale miss lands."""
    import intent_router

    real = intent_router._normalize_chat_intent_text

    def paused(text):
        ltl.erase_entity(re.compile("zzz-never-matches"))      # a whole erase completes while detection is paused
        return real(text)

    monkeypatch.setattr(intent_router, "_normalize_chat_intent_text", paused)
    assert intent_router.detect_intent("qqq wibble frobnicate plonk", user_id="u") is None
    assert _miss_rows(miss) == []


def test_a_normal_miss_still_appends(miss):
    import intent_router

    assert intent_router.detect_intent("qqq wibble frobnicate plonk", user_id="u") is None
    assert [r["text"] for r in _miss_rows(miss)] == ["qqq wibble frobnicate plonk"]


def test_append_line_refuses_while_an_erase_is_in_progress(miss):
    ltl._bump_epoch(+1)
    try:
        assert ltl.append_line(ltl.INTENT_MISSES, json.dumps({"text": "x", "ts": 1.0})) is False
    finally:
        ltl._bump_epoch(-1)
    assert _miss_rows(miss) == []
