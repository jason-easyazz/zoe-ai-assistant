"""The panel daemon's media watch (``_MediaWatch``, LOG-ONLY, flag MEDIA_WATCH_ENABLED default off).

Is anything that is not the daemon playing into the panel's PulseAudio server? A watcher thread keeps a cached snapshot;
the close only reads it. Pins:

  1. the sink-input parse (corked, pid; a block without a Corked line counts as playing);
  2. every way to be UNKNOWN (no read yet, dead thread, failed read, stale snapshot, too short a clean run) is None;
  3. True for a foreign uncorked stream and for the 1000 ms after it went; ours (self or a direct child) never counts,
     a stream that cannot be attributed always does;
  4. the thread survives pactl being absent, the subscribe child dying and the listing failing; events trigger reads;
  5. the close never runs pactl and never waits on a slow read;
  6. FIRST_SOUND carries ``media=`` / ``media_age_ms=`` from the close's snapshot, -1/-1 with the flag off.
Real pactl is NEVER run: an autouse fixture makes the daemon's subprocess calls fail the test.

Negative controls are automated: ``test_each_media_rule_is_load_bearing`` removes one rule at a time from a COPY of the
class source and shows the matching case changes answer.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import re
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


def _load(name: str):
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    fake_requests = types.ModuleType("requests")
    fake_requests.exceptions = types.SimpleNamespace(
        SSLError=type("SSLError", (Exception,), {}), HTTPError=type("HTTPError", (Exception,), {}),
        RequestException=type("RequestException", (Exception,), {}))
    stubs = {"pyaudio": fake_pyaudio, "requests": fake_requests}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, prev in saved.items():
            if prev is not None:
                sys.modules[n] = prev
            else:
                sys.modules.pop(n, None)


@pytest.fixture(scope="module")
def daemon():
    return _load("zoe_voice_daemon_media_watch_test")


@pytest.fixture(autouse=True)
def _no_real_pactl(daemon, monkeypatch):
    def _refuse(*a, **k):
        pytest.fail(f"a test reached a real subprocess: {a[:1]}")
    monkeypatch.setattr(daemon.subprocess, "run", _refuse)
    monkeypatch.setattr(daemon.subprocess, "Popen", _refuse)
    monkeypatch.setattr(daemon, "_MEDIA", None)
    daemon._turn_close.clear()
    yield
    daemon._turn_close.clear()


OWN = 5000


def _block(idx: int, pid, corked: str | None = "no") -> str:
    out = [f"Sink Input #{idx}", "\tDriver: protocol-native.c", "\tSink: 1"]
    if corked is not None:
        out.append(f"\tCorked: {corked}")
    out += ["\tVolume: front-left: 65536 / 100% / 0.00 dB", "\tProperties:", '\t\tapplication.name = "synthetic"']
    if pid is not None:
        out.append(f'\t\tapplication.process.id = "{pid}"')
    return "\n".join(out) + "\n"


PARENTS = {OWN + 1: OWN, OWN + 2: OWN + 1, 7000: 1}   # OWN+1 = our aplay; OWN+2 = its child (a grandchild); 7000 = foreign


class _Clock:
    def __init__(self, t: float = 100.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class _Alive:
    def is_alive(self):
        return True


def _watch(mod, listing, clock, *, alive=True):
    box = {"text": listing}
    w = mod._MediaWatch(list_inputs=lambda: box["text"], spawn_subscribe=lambda: pytest.fail("spawn"),
                        clock=clock, own_pid=OWN, parent_of=PARENTS.get)
    if alive:
        w._thread = _Alive()
    return w, box


# ── 1. the parse ─────────────────────────────────────────────────────────────

def test_parse_sink_inputs(daemon):
    text = _block(1, 7000, "no") + _block(2, OWN + 1, "yes") + _block(3, None, None)
    assert daemon._parse_sink_inputs(text) == [(False, 7000), (True, OWN + 1), (False, None)]
    assert daemon._parse_sink_inputs("") == []
    assert daemon._parse_sink_inputs("garbage\nCorked: yes\n") == []          # no block header: nothing


def test_proc_ppid_reads_the_field_after_the_last_paren(daemon, tmp_path, monkeypatch):
    stat = tmp_path / "stat"
    stat.write_bytes(b"4242 (a (weird) name) S 777 4242 4242 0 -1")
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: real_open(stat if str(p).endswith("/stat") else p, *a, **k))
    assert daemon._proc_ppid(4242) == 777
    stat.write_bytes(b"nonsense")
    assert daemon._proc_ppid(4242) is None


# ── 2/3. the snapshot ───────────────────────────────────────────────────────

def test_unknown_until_a_read_completes_and_a_full_clean_hold(daemon):
    clock = _Clock()
    w, _ = _watch(daemon, _block(1, OWN + 1), clock)
    assert w.snapshot() == (None, -1)                      # no read yet
    w.refresh()
    assert w.snapshot() == (None, 0)                       # clean, but for 0 ms: not yet evidence
    clock.t += 0.5
    w.refresh()
    assert w.snapshot()[0] is None
    clock.t += 0.5
    w.refresh()
    assert w.snapshot() == (False, 0)                      # 1000 ms of clean reads: False
    clock.t += 0.3
    assert w.snapshot() == (False, 300)                    # the age is the snapshot's


@pytest.mark.parametrize("case", ["dead_thread", "never_started", "failed_read", "raising_read", "stale"])
def test_every_unknown_is_none(daemon, case):
    clock = _Clock()
    w, box = _watch(daemon, "", clock)
    for _ in range(3):
        w.refresh()
        clock.t += 0.6
    w.refresh()
    assert w.snapshot()[0] is False                         # the control: a healthy watcher says False here
    if case == "dead_thread":
        w._thread = types.SimpleNamespace(is_alive=lambda: False)
    elif case == "failed_read":
        box["text"] = None
        w.refresh()
    elif case == "raising_read":
        w._list = lambda: 1 / 0
        w.refresh()
    elif case == "stale":
        clock.t += 2.001
    if case == "never_started":
        w._thread = None
    assert w.snapshot()[0] is None


def test_stale_boundary(daemon):
    clock = _Clock()
    w, _ = _watch(daemon, "", clock)
    w.refresh()
    clock.t += 1.0
    w.refresh()
    clock.t += 2.0
    assert w.snapshot() == (False, 2000)                   # exactly 2000 ms is still fresh
    clock.t += 0.002
    assert w.snapshot() == (None, 2002)


def test_a_failed_read_restarts_the_clean_hold(daemon):
    clock = _Clock()
    w, box = _watch(daemon, "", clock)
    w.refresh()
    clock.t += 1.0
    w.refresh()
    assert w.snapshot()[0] is False
    box["text"] = None
    w.refresh()
    box["text"] = ""
    clock.t += 0.1
    w.refresh()
    assert w.snapshot()[0] is None                          # recovered, but the gap is unknown: a new 1000 ms hold


@pytest.mark.parametrize("listing,expect", [
    (_block(1, 7000, "no"), True),                          # foreign, playing
    (_block(1, None, "no"), True),                          # cannot be attributed: foreign
    (_block(1, OWN + 2, "no"), True),                       # a grandchild is not a direct child: foreign
    (_block(1, 7000, None), True),                          # no Corked line: playing
    (_block(1, 7000, "yes"), False),                        # foreign but corked (paused)
    (_block(1, OWN, "no"), False),                          # the daemon itself
    (_block(1, OWN + 1, "no"), False),                      # its own player (a direct child)
    (_block(1, OWN + 1, "no") + _block(2, 7000, "no"), True),
])
def test_foreign_attribution(daemon, listing, expect):
    clock = _Clock()
    w, box = _watch(daemon, "", clock)
    w.refresh()
    clock.t += 1.0
    w.refresh()
    box["text"] = listing
    clock.t += 0.01
    w.refresh()
    assert w.snapshot()[0] is expect


def test_true_holds_for_1000_ms_after_the_stream_goes(daemon):
    clock = _Clock()
    w, box = _watch(daemon, _block(1, 7000), clock)
    w.refresh()
    assert w.snapshot()[0] is True
    box["text"] = ""
    clock.t += 1.0
    w.refresh()                                             # first seen gone at t=101.0
    assert w.snapshot()[0] is True
    clock.t += 0.999
    w.refresh()
    assert w.snapshot()[0] is True
    clock.t += 0.002
    w.refresh()
    assert w.snapshot()[0] is False


# ── negative controls: each rule removed from a COPY of the source ───────────

_RULES = {
    # rule: (old text, new text, case builder) - the case's answer as written, then with the rule removed
    "alive": ("self._thread is None or not self._thread.is_alive() or ", "", "dead_thread"),
    "ok": ("or not ok or age", "or age", "failed_read"),
    "fresh": ("or age > _MEDIA_FRESH_MS", "", "stale"),
    "gone_hold": ("or (gone is not None and (now - gone) * 1000.0 < _MEDIA_HOLD_MS)", "", "just_gone"),
    "clean_hold": ("        if clean is None or (now - clean) * 1000.0 < _MEDIA_HOLD_MS:\n            return None, age\n", "",
                   "just_started"),
    "parent": ("return self._parent_of(pid) == self._own", "return False", "own_child"),
    "attribution": ("if pid is None:\n            return False", "if pid is None:\n            return True",
                    "no_pid"),
    "corked": ("not corked and ", "", "corked"),
}


def _class_ns(src: str) -> dict:
    """Exec ONLY the media-watch helpers + class (no daemon import), from ``src``."""
    tree = ast.parse(src)
    keep = {"_parse_sink_inputs", "_MediaWatch"}
    parts = [ast.get_source_segment(src, n) for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in keep]
    assert len(parts) == 2
    ns = {"re": re, "threading": threading, "time": time, "os": __import__("os"),
          "_MEDIA_FRESH_MS": 2000, "_MEDIA_HOLD_MS": 1000, "_MEDIA_HEARTBEAT_S": 1.0, "_MEDIA_MIN_GAP_S": 0.2,
          "_MEDIA_MAX_LIST_BYTES": 256 * 1024, "_MEDIA_BACKOFF_S": (1.0, 60.0),
          "_media_list_sink_inputs": None, "_media_spawn_subscribe": None, "_proc_ppid": None}
    exec(compile("from __future__ import annotations\n" + "\n\n".join(parts), str(DAEMON), "exec"), ns)
    return ns


def _case(mod, name):
    clock = _Clock()
    if name == "just_started":
        w, _ = _watch(mod, "", clock)
        w.refresh()
        return w.snapshot()[0]
    if name == "just_gone":
        w, box = _watch(mod, _block(1, 7000), clock)
        w.refresh()
        clock.t += 1.0
        w._clean_since = 0.0                               # isolate the gone-hold from the clean-hold
        box["text"] = ""
        w.refresh()
        w._clean_since = 0.0
        return w.snapshot()[0]
    listing = {"own_child": _block(1, OWN + 1), "no_pid": _block(1, None), "corked": _block(1, 7000, "yes")}.get(name, "")
    w, box = _watch(mod, "", clock)
    w.refresh()
    clock.t += 1.0
    w.refresh()
    box["text"] = listing
    w.refresh()
    if name == "dead_thread":
        w._thread = types.SimpleNamespace(is_alive=lambda: False)
    elif name == "failed_read":
        box["text"] = None
        w.refresh()
        w._clean_since = 0.0                               # isolate the ok rule from the clean-hold
    elif name == "stale":
        clock.t += 2.5
    return w.snapshot()[0]


_EXPECT = {"dead_thread": (None, False), "failed_read": (None, False), "stale": (None, False),
           "just_gone": (True, False), "just_started": (None, False), "own_child": (False, True),
           "no_pid": (True, False), "corked": (False, True)}


def test_rule_count_is_pinned():
    assert len(_RULES) == 8 and set(r[2] for r in _RULES.values()) == set(_EXPECT)


@pytest.mark.parametrize("rule", sorted(_RULES))
def test_each_media_rule_is_load_bearing(rule):
    old, new, case = _RULES[rule]
    src = DAEMON.read_text(encoding="utf-8")
    assert src.count(old) == 1, rule
    as_written, mutated = _EXPECT[case]
    real = types.SimpleNamespace(**_class_ns(src))
    assert _case(real, case) is as_written
    mut = types.SimpleNamespace(**_class_ns(src.replace(old, new)))
    assert _case(mut, case) is mutated


# ── 4. the threads ──────────────────────────────────────────────────────────

class _Sub:
    def __init__(self, lines: bytes):
        self.stdout = io.BytesIO(lines)
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_thread_reads_on_events_and_survives_a_dying_subscribe(daemon):
    reads, spawns = [], []

    def _spawn():
        spawns.append(1)
        if len(spawns) == 2:
            raise FileNotFoundError("pactl")                # absent for one attempt
        return _Sub(b"Event 'new' on sink-input #7\n" * 3 + b"Event 'change' on card #0\n")

    w = daemon._MediaWatch(list_inputs=lambda: (reads.append(1), "")[1], spawn_subscribe=_spawn, own_pid=OWN,
                           parent_of=PARENTS.get, heartbeat_s=5.0, min_gap_s=0.0, backoff_s=(0.01, 0.05))
    w.start()
    try:
        assert _wait(lambda: len(spawns) >= 4)              # died (EOF), failed to start, respawned: still going
        assert len(reads) >= 2                              # the reads came from events, not the 5 s heartbeat
        assert w._thread.is_alive() and w._reader.is_alive()
        assert w.snapshot()[1] >= 0
    finally:
        w.stop()
    assert _wait(lambda: not w._thread.is_alive() and not w._reader.is_alive())


def test_heartbeat_keeps_it_fresh_with_no_subscribe_at_all(daemon):
    reads = []

    def _never():
        raise FileNotFoundError("pactl")

    w = daemon._MediaWatch(list_inputs=lambda: (reads.append(1), "")[1], spawn_subscribe=_never, own_pid=OWN,
                           parent_of=PARENTS.get, heartbeat_s=0.02, min_gap_s=0.0, backoff_s=(0.05, 0.05))
    w.start()
    try:
        assert _wait(lambda: len(reads) >= 5)
        assert w._thread.is_alive()
    finally:
        w.stop()


def test_a_listing_that_raises_never_kills_the_thread(daemon):
    calls = []

    def _boom():
        calls.append(1)
        raise OSError("pulse restarting")

    w = daemon._MediaWatch(list_inputs=_boom, spawn_subscribe=lambda: _Sub(b""), own_pid=OWN, parent_of=PARENTS.get,
                           heartbeat_s=0.02, min_gap_s=0.0, backoff_s=(0.05, 0.05))
    w.start()
    try:
        assert _wait(lambda: len(calls) >= 3)
        assert w._thread.is_alive() and w.snapshot()[0] is None
    finally:
        w.stop()


# ── 5. the close never runs pactl and never waits ───────────────────────────

def test_the_close_reads_the_snapshot_only(daemon, monkeypatch):
    clock = _Clock()
    w, _ = _watch(daemon, "", clock)
    w.refresh()
    clock.t += 1.0
    w.refresh()
    w._list = lambda: pytest.fail("the close ran a listing")
    monkeypatch.setattr(daemon, "_MEDIA", w)
    ep = types.SimpleNamespace(tail_rule="deep", close_ms=640, _quiet=8, _hesitating=False)
    daemon._stamp_close(clock.t + 0.25, ep, "silence")
    assert (daemon._turn_close["media"], daemon._turn_close["media_age_ms"]) == (False, 250)


def test_a_hung_listing_does_not_block_the_close(daemon, monkeypatch):
    gate, entered = threading.Event(), threading.Event()

    def _hang():
        entered.set()
        gate.wait(5)
        return ""

    w = daemon._MediaWatch(list_inputs=_hang, spawn_subscribe=lambda: _Sub(b""), own_pid=OWN, parent_of=PARENTS.get,
                           heartbeat_s=0.01, min_gap_s=0.0, backoff_s=(1.0, 1.0))
    w.start()
    try:
        assert entered.wait(2)                              # the watcher is stuck inside pactl
        monkeypatch.setattr(daemon, "_MEDIA", w)
        t0 = time.monotonic()
        assert daemon._media_at_close(time.monotonic()) == (None, -1)
        assert time.monotonic() - t0 < 0.05
    finally:
        gate.set()
        w.stop()


def test_a_broken_watcher_object_is_unknown_never_an_exception(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_MEDIA", types.SimpleNamespace(snapshot=lambda now: 1 / 0))
    assert daemon._media_at_close(1.0) == (None, -1)


# ── 6. the FIRST_SOUND fields ───────────────────────────────────────────────

def test_flag_is_off_by_default_and_off_logs_unknown(daemon):
    assert daemon.MEDIA_WATCH_ENABLED is False
    src = DAEMON.read_text(encoding="utf-8")
    assert src.count('os.environ.get("MEDIA_WATCH_ENABLED", "false")') == 1
    ep = types.SimpleNamespace(tail_rule="deep", close_ms=640, _quiet=8, _hesitating=False)
    daemon._stamp_close(10.0, ep, "silence")
    line = daemon._FirstSound(speculative=False).line((-1, 0, 0))
    assert line.endswith(" muted_ms=-1 first_end_ms=-1 media=-1 media_age_ms=-1")


@pytest.mark.parametrize("media,age,tail", [(True, 120, "media=1 media_age_ms=120"), (False, 0, "media=0 media_age_ms=0"),
                                            (None, 2400, "media=-1 media_age_ms=2400"), (None, -1, "media=-1 media_age_ms=-1")])
def test_first_sound_carries_the_close_snapshot(daemon, monkeypatch, media, age, tail):
    monkeypatch.setattr(daemon, "_MEDIA", types.SimpleNamespace(snapshot=lambda now: (media, age)))
    ep = types.SimpleNamespace(tail_rule="deep", close_ms=640, _quiet=8, _hesitating=False)
    daemon._stamp_close(10.0, ep, "silence")
    line = daemon._FirstSound(speculative=False).line((-1, 0, 0))
    assert line.endswith(" " + tail)
    assert line.split(" ")[-3].startswith("first_end_ms=")      # appended AFTER first_end_ms, at the very end
