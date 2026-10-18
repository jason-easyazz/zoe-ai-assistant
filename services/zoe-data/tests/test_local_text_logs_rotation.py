"""local_text_logs, codex round 2 on #2110: the pass lock vs rotation, rows appended mid-rewrite, every rotated segment.

Each test names the line that, removed, turns it red.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time

import pytest

import local_text_logs as ltl
import semantic_router as sr

pytestmark = pytest.mark.ci_safe

DAY = 86400.0
NOW = 2_000_000_000.0


def rec(utt: str, text: str, age_days: float = 1) -> str:
    return json.dumps({"ts": NOW - age_days * DAY, "utt": utt, "utt_text": text, "mode": "shadow2"}, sort_keys=True)


@pytest.fixture
def shadow(tmp_path, monkeypatch):
    p = tmp_path / "data" / "router_head_shadow.jsonl"
    p.parent.mkdir()
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(p))
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", str(tmp_path / "misses.jsonl"))
    monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(tmp_path / "adhoc"))
    monkeypatch.delenv("ZOE_TEXT_LOG_RETENTION_DAYS", raising=False)
    return p


def test_every_existing_rotated_segment_is_swept_even_above_keep(shadow, monkeypatch):
    """Break: go back to ``range(1, KEEP+1)`` in ``segments`` -> the ``.5`` / ``.9`` files keep their text forever."""
    monkeypatch.setenv("ZOE_ROUTER_SHADOW_KEEP", "4")
    segs = [shadow.with_name(shadow.name + f".{n}") for n in (1, 5, 9)]
    for seg in segs:
        seg.write_text(rec("b" * 12, "ring Marisol about it") + "\n")
    assert [p.name.rsplit(".", 1)[1] for p in ltl.segments(ltl.ROUTER_SHADOW)] == ["9", "5", "1"]
    assert ltl.erase_entity(re.compile("Marisol")) == 3
    assert all("Marisol" not in p.read_text() for p in segs)


def test_retention_sweeps_the_high_segments_too(shadow, monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_SHADOW_KEEP", "2")
    seg = shadow.with_name(shadow.name + ".7")
    seg.write_text(rec("c" * 12, "old words", 99) + "\n")
    ltl.sweep(NOW)
    assert "old words" not in seg.read_text()


def test_a_row_appended_to_the_old_inode_is_filtered_not_copied_through(shadow, monkeypatch):
    """Break: copy the tail / late rows verbatim again -> a row naming the forgotten entity survives an erase that said success."""
    shadow.write_text(rec("a" * 12, "ring Marisol") + "\n")
    real_replace = os.replace
    held = open(shadow, "a")

    def replace_then_late_rows(src, dst):
        real_replace(src, dst)
        held.write(rec("l" * 12, "Marisol again, late") + "\n" + rec("m" * 12, "late but harmless") + "\n")
        held.flush()

    orig_through = ltl._through
    calls = {"n": 0}

    def through_with_a_tail_row(data, fn):
        calls["n"] += 1
        if calls["n"] == 2:                                      # the tail read, just before the replace
            with open(shadow, "a") as fh:
                fh.write(rec("t" * 12, "tail row about Marisol") + "\n")
        return orig_through(data, fn)

    monkeypatch.setattr(ltl, "_through", through_with_a_tail_row)
    monkeypatch.setattr(ltl.os, "replace", replace_then_late_rows)
    try:
        ltl.erase_entity(re.compile("Marisol"))
    finally:
        held.close()
    text = shadow.read_text()
    assert "Marisol" not in text and "late but harmless" in text


def test_a_rewrite_whose_path_was_renamed_underneath_it_replaces_nothing(shadow):
    """Break: drop the inode check -> the stripped OLD file overwrites the NEW live file a rotation just created."""
    shadow.write_text(rec("a" * 12, "old words", 99) + "\n")

    def fn(line):
        os.rename(shadow, shadow.with_name(shadow.name + ".1"))        # a rotation lands while we transform
        shadow.write_text(rec("n" * 12, "brand new row", 0) + "\n")
        return ltl._strip_or_drop(ltl.ROUTER_SHADOW, line, older_than=NOW - 30 * DAY)

    with pytest.raises(ltl.RewriteRaced):
        ltl.rewrite(shadow, fn)
    assert "brand new row" in shadow.read_text()


def test_a_writer_in_another_process_blocks_on_the_pass_lock_and_no_rotation_interleaves(shadow, monkeypatch):
    """The writer is a SEPARATE PROCESS (the in-process lock cannot help it - this is the hourly-job-vs-zoe-data case).
    Break: remove ``_shadow_file_lock`` from ``semantic_router._append_shadow_line`` (or the flock in ``locked``): the writer rotates
    mid-pass, ``.3`` is renamed to ``.4`` unvisited and its text survives an erase that reports success."""
    import subprocess
    import sys
    from pathlib import Path

    monkeypatch.setenv("ZOE_ROUTER_SHADOW_KEEP", "4")
    for n in (4, 3, 2, 1):
        shadow.with_name(shadow.name + f".{n}").write_text(rec(f"{n}" * 12, f"segment {n} names Marisol") + "\n")
    shadow.write_text(rec("f" * 12, "live names Marisol") + "\n")
    svc = str(Path(sr.__file__).resolve().parent)
    go = shadow.parent / "go"
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import semantic_router as sr\n"
            "sr._SHADOW_KEEP = 4; sr._SHADOW_MAX_BYTES = 1\n"
            "import os, time\n"
            "print('ready', flush=True)\n"
            "while not os.path.exists(%r): time.sleep(0.01)\n"
            "sr._append_shadow_line(%r, %r)\n"
            "print('done', flush=True)\n") % (svc, str(go), str(shadow), rec("w" * 12, "writer row"))
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    first = {"v": True}
    real_rewrite = ltl.rewrite

    def rewrite_then_let_the_writer_try(path, fn):
        if first["v"]:
            first["v"] = False
            go.write_text("1")                                   # NOW the writer races the pass, which holds the lock
            time.sleep(0.5)                                      # long enough for an unlocked writer to rotate
            assert proc.poll() is None, "the writer got through while the pass held the lock"
        return real_rewrite(path, fn)

    monkeypatch.setattr(ltl, "rewrite", rewrite_then_let_the_writer_try)
    ltl.erase_entity(re.compile("Marisol"))
    out, _ = proc.communicate(timeout=20)
    assert "done" in out
    files = [p for p in shadow.parent.glob(shadow.name + "*") if p.suffix != ".lock"]
    assert all("Marisol" not in p.read_text() for p in files)
    assert any("writer row" in p.read_text() for p in files)


def test_lock_wait_fails_closed(shadow, monkeypatch):
    monkeypatch.setattr(ltl, "LOCK_WAIT_S", 0.2)
    holder = open(ltl.lock_path(ltl.ROUTER_SHADOW), "a")
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        with pytest.raises(TimeoutError):
            ltl.erase_all_text()
        assert ltl.sweep(NOW)["router_shadow"] == -1             # the sweep reports it, so the hourly job goes red
    finally:
        holder.close()
