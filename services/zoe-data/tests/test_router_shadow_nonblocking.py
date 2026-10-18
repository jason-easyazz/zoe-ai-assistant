"""The router shadow append never waits on retention (codex round on #2110).

``route()`` appends its observation synchronously on a live voice turn; the hourly retention pass holds the same locks across its
whole rewrite. The append must therefore SKIP (counted, debug-logged) rather than wait - a dropped shadow line costs nothing, a
stalled turn does. Routing results must not change.

Break: take ``LOCK_EX`` without ``LOCK_NB`` in ``_shadow_file_lock`` (or a plain ``with _shadow_write_lock``) -> the route tests hang
and the negative control below shows that shape stalls.
"""
from __future__ import annotations

import fcntl
import subprocess
import sys
import threading
import time

import pytest

import local_text_logs as ltl

np = pytest.importorskip("numpy")
import semantic_router as sr  # noqa: E402

pytestmark = pytest.mark.ci_safe

PROMPT = 0.5      # a route() that waited on retention would take as long as the holder keeps the pass


class _FakeHead:
    classes_ = ["chat", "lists"]

    def predict_proba(self, x):
        return np.array([[0.2, 0.8]])


@pytest.fixture
def box(tmp_path, monkeypatch):
    log = tmp_path / "data" / "router_head_shadow.jsonl"
    log.parent.mkdir()
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(log))          # where retention locks
    monkeypatch.setattr(sr, "_HEAD_LOG_PATH", str(log))          # where the router appends
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    # route() runs for real; only the embedding and the head are stubbed
    doms = list(sr.ROUTES)
    monkeypatch.setattr(sr, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(sr, "_ensure_head_loaded", lambda: None)
    monkeypatch.setattr(sr, "_HEAD", _FakeHead())
    monkeypatch.setattr(sr, "embed", lambda text: np.array([1.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(sr, "_MATRIX", np.array([[1.0 if i == 0 else 0.0, 0.0] for i in range(len(doms))], dtype=np.float32))
    monkeypatch.setattr(sr, "_DOM_IDX", {d: np.array([i]) for i, d in enumerate(doms)})
    monkeypatch.setattr(sr, "shadow_append_skipped", 0, raising=False)
    return log


def _routing(out: dict) -> dict:
    return {k: v for k, v in out.items() if k != "ms"}


def _hold_pass(release: threading.Event, held: threading.Event) -> threading.Thread:
    """Another thread inside ``locked(ROUTER_SHADOW)``: the in-process lock AND the flock, as the hourly job holds them."""
    def run():
        with ltl.locked(ltl.ROUTER_SHADOW):
            held.set()
            release.wait(10)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert held.wait(5)
    return t


def test_route_returns_promptly_and_skips_while_retention_holds_both_locks(box):
    baseline = _routing(sr.route("add milk to the list"))
    assert box.read_text().count("\n") == 1                       # unlocked: the observation is written
    release, held = threading.Event(), threading.Event()
    t = _hold_pass(release, held)
    try:
        t0 = time.monotonic()
        out = sr.route("add milk to the list")
        elapsed = time.monotonic() - t0
    finally:
        release.set()
        t.join(5)
    assert elapsed < PROMPT, f"route() waited {elapsed:.2f}s on the retention pass"
    assert sr.shadow_append_skipped == 1
    assert box.read_text().count("\n") == 1                       # skipped, not queued
    assert _routing(out) == baseline                              # routing result unchanged


def test_route_skips_when_only_another_process_holds_the_flock(box):
    """The hourly job / owner script is a separate process: only the flock (not the thread lock) is held."""
    code = ("import fcntl, sys, time\n"
            "fh = open(sys.argv[1] + '.lock', 'a')\n"
            "fcntl.flock(fh, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "time.sleep(30)\n")
    proc = subprocess.Popen([sys.executable, "-c", code, str(box)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        t0 = time.monotonic()
        out = sr.route("add milk to the list")
        elapsed = time.monotonic() - t0
    finally:
        proc.kill()
        proc.wait()
    assert elapsed < PROMPT
    assert sr.shadow_append_skipped == 1
    assert not box.exists() or box.read_text() == ""
    assert out["routed"] in sr.ROUTES or out["routed"] == "chat"


def test_the_observation_resumes_once_retention_lets_go(box):
    release, held = threading.Event(), threading.Event()
    t = _hold_pass(release, held)
    assert sr._append_shadow_line(str(box), '{"a": 1}') is False
    release.set()
    t.join(5)
    assert sr._append_shadow_line(str(box), '{"a": 2}') is True
    assert box.read_text().strip() == '{"a": 2}'


def test_negative_control_the_old_blocking_acquisition_stalls(box, monkeypatch):
    """Proves the harness can see a stall: re-install the OLD blocking append and route() does not return while retention holds the file."""
    def old_append(path, line):
        with sr._shadow_write_lock:
            with open(f"{path}.lock", "a") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    with open(path, "a", encoding="utf-8") as out:
                        out.write(line + "\n")
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    monkeypatch.setattr(sr, "_append_shadow_line", old_append)
    release, held = threading.Event(), threading.Event()
    t = _hold_pass(release, held)
    done = threading.Event()
    w = threading.Thread(target=lambda: (sr.route("add milk to the list"), done.set()), daemon=True)
    w.start()
    try:
        assert not done.wait(PROMPT), "the old blocking acquisition returned promptly, so this harness cannot detect a stall"
    finally:
        release.set()
        t.join(5)
    assert done.wait(5)                                           # it only finished once retention let go
    w.join(5)
