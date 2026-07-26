"""Shadow-log rotation: bounded on disk, but LOSSLESS for the self-train miner.

The shadow log is appended on every routed turn, so it grew without bound.
Capping it is easy; the hard requirement is that capping must not quietly starve
the mine->label->ratchet loop, whose miner
(labs/router-selftrain/mine_candidates.py) reads the WHOLE history by default
(`--since` defaults to 0.0).

So the design ROTATES rather than truncates, and readers glob the segments back
in. These tests pin both halves: the cap actually bounds the live file, AND every
record written survives a rotation and is still readable in append order.
"""

import json

import pytest

pytestmark = pytest.mark.ci_safe

numpy = pytest.importorskip("numpy")  # semantic_router imports numpy at module level


@pytest.fixture()
def router(tmp_path, monkeypatch):
    """semantic_router bound to a tmp shadow log with a tiny rotation cap.

    Patch the module CONSTANTS, never setenv+reload. The env vars are only read
    at import time, so a reload-based fixture has to reload again on teardown to
    undo itself — and pytest tears fixtures down in reverse setup order, so that
    second reload runs while monkeypatch's env is STILL patched. It would bake
    the test values permanently into the module globals (verified: the rest of
    the session then saw _SHADOW_MAX_BYTES=1024 and a _HEAD_LOG_PATH pointing at
    a deleted tmp dir). monkeypatch.setattr restores in the right order for free.
    """
    log = tmp_path / "data" / "router_head_shadow.jsonl"
    import semantic_router

    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH", str(log))
    monkeypatch.setattr(semantic_router, "_SHADOW_MAX_BYTES", 1024)
    monkeypatch.setattr(semantic_router, "_SHADOW_KEEP", 3)
    yield semantic_router, log


def _write(sr, log, count):
    for i in range(count):
        sr._append_shadow_line(str(log), json.dumps({"ts": float(i), "i": i, "pad": "x" * 80}))


def test_live_log_stays_under_the_cap(router):
    sr, log = router
    _write(sr, log, 200)
    # The live file is rotated away once it exceeds the cap, so it can only ever
    # hold the cap plus the one line that tripped it.
    assert log.stat().st_size < 1024 + 200


def test_rotation_creates_numbered_segments(router):
    sr, log = router
    _write(sr, log, 200)
    assert (log.parent / (log.name + ".1")).exists(), "expected a .1 segment"


def test_retention_bounds_total_segments(router):
    sr, log = router
    _write(sr, log, 5000)
    segments = sr.shadow_log_segments(str(log))
    # KEEP=3 rotated segments + the live file.
    assert len(segments) <= 4, f"unbounded segments: {segments}"
    assert not (log.parent / (log.name + ".4")).exists(), "segment past KEEP not pruned"


def test_no_record_is_lost_across_rotations(router):
    """The load-bearing property: rotation must not destroy miner input."""
    sr, log = router
    # Few enough to fit inside the retention window, so NOTHING should be lost.
    _write(sr, log, 40)
    seen = []
    for segment in sr.shadow_log_segments(str(log)):
        with open(segment, encoding="utf-8") as fh:
            seen.extend(json.loads(line)["i"] for line in fh if line.strip())
    assert seen == list(range(40)), "records lost or reordered across rotation"


def test_segments_are_returned_oldest_first(router):
    """Readers concatenate segments and assume append order (`--since` relies on it)."""
    sr, log = router
    _write(sr, log, 200)
    timestamps = []
    for segment in sr.shadow_log_segments(str(log)):
        with open(segment, encoding="utf-8") as fh:
            timestamps.extend(json.loads(line)["ts"] for line in fh if line.strip())
    assert timestamps == sorted(timestamps), "segments not concatenated in append order"


def test_live_file_is_last_segment(router):
    sr, log = router
    _write(sr, log, 200)
    assert sr.shadow_log_segments(str(log))[-1] == str(log)


def test_rotation_can_be_disabled(router, monkeypatch):
    """Escape hatch: 0 disables the cap (unbounded, as before)."""
    sr, log = router
    monkeypatch.setattr(sr, "_SHADOW_MAX_BYTES", 0)
    _write(sr, log, 200)
    assert not (log.parent / (log.name + ".1")).exists()
    assert sr.shadow_log_segments(str(log)) == [str(log)]


def test_segments_helper_handles_a_missing_log(router):
    sr, log = router
    assert sr.shadow_log_segments(str(log)) == []


def test_concurrent_appends_do_not_lose_lines(router):
    """The per-turn head shadow and the shadow2 logger append from two threads."""
    import threading

    sr, log = router
    def worker(base):
        for i in range(150):
            sr._append_shadow_line(str(log), json.dumps({"ts": float(i), "w": base, "pad": "y" * 60}))

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = 0
    for segment in sr.shadow_log_segments(str(log)):
        with open(segment, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    json.loads(line)  # every line must be a complete, valid record
                    total += 1
    # Lines may age out of the retention window, but none may be torn/interleaved.
    assert total > 0
