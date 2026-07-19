"""P-W2.3 — the voice daemon's announce poll/claim/speak decision logic.

The Pi daemon (`scripts/setup/zoe_voice_daemon.py`) can't be imported in slim
CI (pyaudio/numpy), so the decision logic is EXTRACTED into the stdlib-only
`scripts/setup/zoe_voice_announce.py` and pinned here with fakes — no network,
no audio.

Contract under test:
  * never speak while busy (defer);
  * never speak past the TTL (expire — even if the daemon just became idle);
  * a busy poll cycle doesn't even claim (the server TTL stays authoritative);
  * a failed poll (zoe-data restarting on a deploy) backs off quietly and
    NEVER escapes the loop — the voice path must survive a down server.

Falsifiable pins: reorder decide()'s expiry-vs-busy checks and
test_expire_wins_over_busy goes red; claim while busy and
test_busy_cycle_never_fetches goes red; let a fetch exception escape run()
and test_run_survives_server_down goes red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_MOD_PATH = _REPO / "scripts" / "setup" / "zoe_voice_announce.py"

_spec = importlib.util.spec_from_file_location("zoe_voice_announce", _MOD_PATH)
va = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(va)


# ── decide(): the one-moment decision ───────────────────────────────────────

def test_decide_speak_when_idle_and_fresh():
    assert va.decide(busy=False, remaining_s=90.0) == "speak"


def test_decide_defer_when_busy():
    assert va.decide(busy=True, remaining_s=90.0) == "defer"


def test_decide_expire_when_past_ttl():
    assert va.decide(busy=False, remaining_s=0.0) == "expire"
    assert va.decide(busy=False, remaining_s=-5.0) == "expire"


def test_expire_wins_over_busy():
    """A stale announce must die even while the daemon is busy — otherwise it
    waits for idle and speaks a noon 'good morning'."""
    assert va.decide(busy=True, remaining_s=0.0) == "expire"


# ── a fake clock + poller harness ───────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, s):
        self.t += s


def _poller(clock, *, fetch=None, speak=None, busy_seq=None, **kw):
    state = {"spoken": [], "busy_seq": list(busy_seq or [])}

    def default_fetch():
        return []

    def default_speak(ann):
        state["spoken"].append(ann)
        return True

    def is_busy():
        if state["busy_seq"]:
            return state["busy_seq"].pop(0)
        return False

    p = va.AnnouncePoller(
        fetch=fetch or default_fetch,
        speak=speak or default_speak,
        is_busy=is_busy,
        poll_interval_s=5.0,
        defer_wait_s=1.0,
        monotonic=clock.monotonic,
        **kw,
    )
    return p, state


# ── deliver(): speak / defer / expire on one claimed announce ───────────────

def test_deliver_speaks_when_idle():
    clock = FakeClock()
    p, state = _poller(clock)
    ann = {"id": "a1", "text": "Good morning", "expires_in_s": 90.0}
    assert p.deliver(ann, wait=lambda s: clock.advance(s)) == "spoken"
    assert state["spoken"] == [ann]


def test_deliver_defers_then_speaks_after_turn_ends():
    """Busy for 3 checks (a live turn), idle on the 4th, still inside TTL —
    the announce is deferred, never overlapped, then spoken."""
    clock = FakeClock()
    p, state = _poller(clock, busy_seq=[True, True, True, False])
    ann = {"id": "a1", "text": "brief", "expires_in_s": 90.0}
    assert p.deliver(ann, wait=lambda s: clock.advance(s)) == "spoken"
    assert len(state["spoken"]) == 1


def test_deliver_expires_while_deferring():
    """Busy past the TTL: the announce dies, speak is NEVER called."""
    clock = FakeClock()
    p, state = _poller(clock, busy_seq=[True] * 100)  # busy far past the 10s TTL
    ann = {"id": "a1", "text": "stale brief", "expires_in_s": 10.0}
    assert p.deliver(ann, wait=lambda s: clock.advance(s)) == "expired"
    assert state["spoken"] == [], "an expired announce must never be played"


def test_deliver_speak_failure_reported():
    clock = FakeClock()
    p, _ = _poller(clock, speak=lambda ann: False)
    ann = {"id": "a1", "text": "x", "expires_in_s": 60.0}
    assert p.deliver(ann, wait=lambda s: clock.advance(s)) == "speak_failed"


def test_deliver_missing_ttl_expires_not_speaks():
    """No/garbage expires_in_s → treated as already at the TTL edge (never a
    forever-fresh announce)."""
    clock = FakeClock()
    p, state = _poller(clock)
    assert p.deliver({"id": "a1", "text": "x"}, wait=lambda s: clock.advance(s)) == "expired"
    assert p.deliver({"id": "a2", "text": "x", "expires_in_s": "bad"},
                     wait=lambda s: clock.advance(s)) == "expired"
    assert state["spoken"] == []


# ── poll_once(): busy cycles never claim ────────────────────────────────────

def test_busy_cycle_never_fetches():
    clock = FakeClock()
    calls = {"fetch": 0}

    def fetch():
        calls["fetch"] += 1
        return []

    p, _ = _poller(clock, fetch=fetch, busy_seq=[True])
    assert p.poll_once() == ["busy"]
    assert calls["fetch"] == 0, "claiming while busy would strand rows client-side past their TTL"


def test_idle_cycle_fetches_and_delivers_in_order():
    clock = FakeClock()
    anns = [
        {"id": "a1", "text": "first", "expires_in_s": 60.0},
        {"id": "a2", "text": "second", "expires_in_s": 60.0},
    ]
    p, state = _poller(clock, fetch=lambda: list(anns))
    assert p.poll_once(wait=lambda s: clock.advance(s)) == ["spoken", "spoken"]
    assert [a["id"] for a in state["spoken"]] == ["a1", "a2"]


# ── run(): server-down resilience + backoff ─────────────────────────────────

def test_run_survives_server_down_and_backs_off():
    """zoe-data restarts on every deploy: repeated fetch failures must be
    swallowed, back off the cadence (5→10→20→40→60 cap), and reset after one
    good poll. The thread must never die."""
    clock = FakeClock()
    calls = {"n": 0}

    def flaky_fetch():
        calls["n"] += 1
        if calls["n"] <= 4:
            raise ConnectionError("server restarting")
        return []

    p, _ = _poller(clock, fetch=flaky_fetch)
    waits = []

    def shutdown_wait(timeout):
        waits.append(timeout)
        return len(waits) > 6  # let 6 cycles run, then shut down

    p.run(shutdown_wait)  # must NOT raise
    # waits[0] precedes the first poll (no failures yet) = base interval;
    # then 4 failures double the wait each cycle; the good 5th poll resets.
    assert waits[0] == 5.0
    assert waits[1:5] == [10.0, 20.0, 40.0, 60.0]  # capped at backoff_max_s=60
    assert waits[5] == 5.0, "one good poll must reset the backoff"


def test_run_stops_on_shutdown_immediately():
    clock = FakeClock()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return []

    p, _ = _poller(clock, fetch=fetch)
    p.run(lambda timeout: True)  # already shutting down
    assert calls["n"] == 0
