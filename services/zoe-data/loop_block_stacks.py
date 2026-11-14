"""Name what blocked the event loop (A6 step 1, speed audit 2026-11-14).

``event_loop_lag`` measures HOW LONG the single worker was blocked, but only after the fact, from inside the loop, so it
cannot say WHAT blocked it. This daemon thread watches the lag monitor's heartbeat from outside: when a 250 ms tick is
``WARN_S`` overdue (the loop is still blocked), it samples the loop thread's stack with ``sys._current_frames()``, once at
detection and once more ``SECOND_S`` later, and reduces it to code locations only: our innermost frames
(``path-under-zoe-data:function:line``) plus the first package our code entered (``via=chromadb``, ``via=std:json``).
Never locals, arguments, source lines or exception text, so the line is privacy-clean by construction.

One ``EVENT_LOOP_BLOCK`` line per episode, logged when the episode ENDS (so the watcher never contends with a blocked loop
for a logging lock), an hourly ``EVENT_LOOP_BLOCK_TOP`` summary, and bounded per-fingerprint counters on ``/metrics``
(internal token). ``fp=unseen`` = a lag the watcher could not sample (the GIL was held by C code for the whole block, or it
ended before the sample). Flag ``ZOE_LOOP_BLOCK_STACKS`` (default on; costs nothing until a block is in progress).
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import os
import sys
import sysconfig
import threading
import time
from typing import Optional

from prometheus_client import Counter

import event_loop_lag as _lag
from memory_metrics import REGISTRY

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
_STD = tuple({os.path.realpath(sysconfig.get_paths()[k]) for k in ("stdlib", "platstdlib")})
OWN_FRAMES = 5        # our innermost frames kept for the fingerprint / DEBUG list
MAX_WALK = 128        # frames walked per capture (innermost first)
MAX_FPS = 64          # fingerprints kept; the rest count under "other"
SECOND_S = 1.0        # second sample: same frame still running (one slow call) or moved (many calls)
POLL_S = 0.05         # only while a block is in progress
SUMMARY_EVERY_S = 3600.0

blocks_total = Counter("zoe_event_loop_block_total", "Event-loop block episodes (>= 250 ms) by stack fingerprint.",
                       ("fp", "top", "via"), registry=REGISTRY)
block_ms_total = Counter("zoe_event_loop_block_ms_total", "Milliseconds the event loop was blocked, by fingerprint.",
                         ("fp", "top", "via"), registry=REGISTRY)

_classified: dict = {}


def _classify(fn: str) -> tuple:
    """('own', relpath) | ('pkg', package) | ('std', module). Cached; never reads the file."""
    hit = _classified.get(fn)
    if hit is not None:
        return hit
    if fn.startswith("<"):
        out = ("std", "frozen")
    else:
        p = fn if os.path.isabs(fn) else os.path.join(ROOT, fn)
        out = None
        for marker in ("/site-packages/", "/dist-packages/"):
            if marker in p:
                out = ("pkg", p.split(marker, 1)[1].split("/", 1)[0].removesuffix(".py"))
                break
        if out is None and p.startswith(ROOT + os.sep):
            out = ("own", os.path.relpath(p, ROOT))
        if out is None:
            rp = os.path.realpath(p)
            std = next((s for s in _STD if rp.startswith(s + os.sep)), None)
            if std:
                out = ("std", os.path.relpath(rp, std).split(os.sep, 1)[0].removesuffix(".py"))
            else:
                out = ("pkg", os.path.basename(p).removesuffix(".py"))
    if len(_classified) < 4096:
        _classified[fn] = out
    return out


def capture(tid: int) -> Optional[list]:
    """(filename, function, line) of the thread's frames, outermost first. Code locations only; frames dropped at once."""
    frames = sys._current_frames()
    f = frames.get(tid)
    del frames
    out = []
    while f is not None and len(out) < MAX_WALK:
        out.append((f.f_code.co_filename, f.f_code.co_name, f.f_lineno))
        f = f.f_back
    del f
    out.reverse()
    return out or None


def reduce(raw: list) -> dict:
    """top / via / depth / fp / frames from a captured stack."""
    cls = [(_classify(fn), name, line) for fn, name, line in raw]
    labels = [f"{c[1]}:{n}:{ln}" if c[0] == "own" else (f"std:{c[1]}:{n}:{ln}" if c[0] == "std" else f"{c[1]}:{n}:{ln}")
              for c, n, ln in cls]
    own = [i for i, (c, _, _) in enumerate(cls) if c[0] == "own"]
    if own:
        top, inner = labels[own[-1]], cls[own[-1] + 1:]
        key = [labels[i] for i in own][-OWN_FRAMES:][::-1]
    else:
        top, inner, key = "-", cls[::-1], labels[-OWN_FRAMES:][::-1]
    via = next((c[1] for c, _, _ in inner if c[0] == "pkg"), None)
    if via is None:
        via = next((f"std:{c[1]}" for c, _, _ in inner if c[0] == "std"), "-")
    fp = hashlib.sha1("|".join(key + [via]).encode()).hexdigest()[:8]
    return {"top": top, "via": via, "depth": len(raw), "fp": fp, "frames": key}


class _Watcher(threading.Thread):
    def __init__(self, summary_every_s: float = SUMMARY_EVERY_S) -> None:
        super().__init__(name="loop-block-stacks", daemon=True)
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()
        self.table: dict = {}
        self.episodes = 0
        self.summary_every_s = summary_every_s
        self.last_summary = time.monotonic()
        self.summarised = 0
        self.accounted = _lag._beat.big[0]

    def run(self) -> None:
        while not self.stop_ev.is_set():
            try:
                self._step()
            except Exception:  # noqa: BLE001 - instrumentation never dies loudly, never touches the loop
                self.stop_ev.wait(1.0)

    def _step(self) -> None:
        beat = _lag._beat
        self._maybe_summary()
        if beat.big[0] > self.accounted:  # a lag we never sampled (GIL held throughout, or too short)
            self.accounted = beat.big[0]
            self._emit(beat.big[1], None, None)
        tid, (seq, deadline) = beat.tid, beat.cur
        due = deadline + _lag.WARN_S
        now = time.monotonic()
        if tid is None or now < due:
            self.stop_ev.wait(0.5 if tid is None else min(due - now, 1.0))
        elif beat.done[0] >= seq:
            self.stop_ev.wait(0.01)  # woke; about to re-arm
        else:
            self._episode(tid, seq, deadline, now)

    def _episode(self, tid: int, seq: int, deadline: float, t0: float) -> None:
        beat = _lag._beat
        raw = capture(tid)
        first = reduce(raw) if raw and beat.done[0] < seq else None
        second = None
        while not self.stop_ev.is_set() and beat.tid == tid and beat.done[0] < seq:
            if second is None and first and time.monotonic() - t0 >= SECOND_S:
                raw = capture(tid)
                second = reduce(raw) if raw and beat.done[0] < seq else {}
            self.stop_ev.wait(POLL_S)
        done_seq, lag = beat.done
        if done_seq < seq:
            return  # shut down mid-block: nothing reliable to say
        if done_seq != seq:
            lag = beat.big[1] if beat.big[0] == seq else time.monotonic() - deadline
        self.accounted = max(self.accounted, seq)
        if lag >= _lag.WARN_S:
            self._emit(lag, first, second or None)

    def _emit(self, lag_s: float, first: Optional[dict], second: Optional[dict]) -> None:
        ms = int(lag_s * 1000)
        r = first or {"top": "-", "via": "-", "depth": 0, "fp": "unseen", "frames": []}
        at1s = "-" if not (first and second) else ("same" if second["fp"] == r["fp"] else "moved")
        tail = f" top1s={second['top']}" if at1s == "moved" else ""
        logger.warning("EVENT_LOOP_BLOCK ms=%d top=%s via=%s depth=%d fp=%s at1s=%s%s",
                       ms, r["top"], r["via"], r["depth"], r["fp"], at1s, tail)
        logger.debug("EVENT_LOOP_BLOCK_FRAMES fp=%s frames=%s", r["fp"], " < ".join(r["frames"]) or "-")
        with self.lock:
            self.episodes += 1
            key = r["fp"] if (r["fp"] in self.table or len(self.table) < MAX_FPS) else "other"
            e = self.table.setdefault(key, {"top": r["top"] if key != "other" else "-",
                                            "via": r["via"] if key != "other" else "-",
                                            "count": 0, "total_ms": 0, "max_ms": 0})
            e["count"] += 1
            e["total_ms"] += ms
            e["max_ms"] = max(e["max_ms"], ms)
        blocks_total.labels(key, e["top"], e["via"]).inc()
        block_ms_total.labels(key, e["top"], e["via"]).inc(ms)

    def snapshot(self) -> list:
        with self.lock:
            rows = [{"fp": k, **v} for k, v in self.table.items()]
        return sorted(rows, key=lambda r: -r["total_ms"])

    def _maybe_summary(self) -> None:
        if time.monotonic() - self.last_summary < self.summary_every_s:
            return
        self.last_summary = time.monotonic()
        if self.episodes == self.summarised:
            return
        self.summarised = self.episodes
        for r in self.snapshot()[:5]:
            logger.warning("EVENT_LOOP_BLOCK_TOP n=%d fp=%s top=%s count=%d total_ms=%d max_ms=%d",
                           self.episodes, r["fp"], r["top"], r["count"], r["total_ms"], r["max_ms"])


_watcher: Optional[_Watcher] = None


def enabled() -> bool:
    from typed_env import flag
    return bool(flag("ZOE_LOOP_BLOCK_STACKS"))


def start(summary_every_s: float = SUMMARY_EVERY_S) -> bool:
    """Start the watcher thread when the flag is on. Idempotent; never raises."""
    global _watcher
    try:
        if not enabled():
            return False
        if _watcher is None or not _watcher.is_alive():
            _watcher = _Watcher(summary_every_s)
            _watcher.start()
            atexit.register(stop)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("loop block watcher not started (non-fatal): %s", type(exc).__name__)
        return False


def stop(timeout: float = 2.0) -> None:
    global _watcher
    w, _watcher = _watcher, None
    if w is not None:
        w.stop_ev.set()
        w.join(timeout)


def snapshot() -> list:
    """The per-fingerprint table (count, total_ms, max_ms), worst first."""
    return _watcher.snapshot() if _watcher else []
