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
