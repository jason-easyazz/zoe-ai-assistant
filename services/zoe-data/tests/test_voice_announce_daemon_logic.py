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


@pytest.mark.parametrize("speak_ok,ttl,expect_acks", [
    (True, 60.0, ["a1"]),   # played → ACKed (played_at on the server)
    (False, 60.0, []),      # TTS/playback failed after the claim → no ACK
    (True, 0.0, []),        # expired on the daemon → never played, no ACK
])
def test_ack_is_sent_only_after_playback(speak_ok, ttl, expect_acks):
    """The server's claim (delivered_at) is not proof of playback; the ACK is."""
    clock = FakeClock()
    acks = []
    p, _ = _poller(clock, speak=lambda ann: speak_ok, ack=lambda ann: acks.append(ann["id"]))
    p.deliver({"id": "a1", "text": "x", "expires_in_s": ttl}, wait=lambda s: clock.advance(s))
    assert acks == expect_acks


def test_ack_failure_never_escapes():
    clock = FakeClock()

    def broken_ack(ann):
        raise RuntimeError("zoe-data restarting")

    p, _ = _poller(clock, ack=broken_ack)
    assert p.deliver({"id": "a1", "text": "x", "expires_in_s": 60.0},
                     wait=lambda s: clock.advance(s)) == "spoken"


def test_ack_retries_on_its_schedule_until_the_server_takes_it():
    outcomes = iter([False, RuntimeError("zoe-data restarting"), True])
    slept, posted = [], []

    def post(ann_id):
        posted.append(ann_id)
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    assert va.ack_with_retries(post=post, ann_id="a1", sleep=slept.append) is True
    assert posted == ["a1"] * 3 and slept == [2.0, 8.0]  # 3 attempts over ~10 s


def test_ack_stops_at_the_first_success():
    posted = []
    assert va.ack_with_retries(post=lambda i: posted.append(i) or True, ann_id="a1",
                               sleep=lambda s: None) is True
    assert posted == ["a1"]


def test_ack_lost_after_all_retries_is_reported_never_raised():
    """The accepted failure mode: the ACK is lost, the server later treats the
    heard brief as unheard, and the first conversation repeats it."""
    warnings = []

    class _Log:
        def warning(self, msg, *args):
            warnings.append(msg % args)

    def post(ann_id):
        raise RuntimeError("down")

    assert va.ack_with_retries(post=post, ann_id="a1", sleep=lambda s: None, logger=_Log()) is False
    assert len(va.ACK_RETRY_DELAYS_S) == 3 and sum(va.ACK_RETRY_DELAYS_S) <= 10
    assert warnings and "lost after retries" in warnings[0]


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


# ── run(): outage observability (2026-10-04 log review) ─────────────────────
# The first failure of a streak is the only WARNING, so the daemon log counted
# 50 outages in one day but never said when the server came back or how long
# the poll was blind. One INFO line closes each streak - only on a REAL fetch.

class _Recorder:
    def __init__(self):
        self.lines = []

    def info(self, msg, *a):
        self.lines.append(("info", msg % a))

    def warning(self, msg, *a):
        self.lines.append(("warning", msg % a))


def _run_cycles(p, clock, cycles):
    """Run `cycles` waits, advancing the fake clock by the timeout the poller
    ASKS for - so the real 5/10/20/40/60 s backoff schedule is exercised."""
    n = {"i": 0}

    def shutdown_wait(timeout):
        clock.advance(timeout)
        n["i"] += 1
        return n["i"] > cycles

    p.run(shutdown_wait)


def test_recovery_is_logged_once_with_the_failed_count_and_outage_length():
    clock = FakeClock()
    calls = {"n": 0}

    def flaky_fetch():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise ConnectionError("502")
        return []

    rec = _Recorder()
    p, _ = _poller(clock, fetch=flaky_fetch, logger=rec)
    _run_cycles(p, clock, 5)
    warns = [m for lvl, m in rec.lines if lvl == "warning"]
    recovered = [m for lvl, m in rec.lines if lvl == "info" and "recovered" in m]
    assert len(warns) == 1, "still one WARNING per streak"
    assert len(recovered) == 1
    assert "3 failed polls" in recovered[0]
    # waits 5 (poll 1 fails at t=5), 10 (t=15 fails), 20 (t=35 fails), 40 (t=75 ok):
    # blind 70 s, not the 15 s a fixed-step clock would show - the backoff is in it.
    assert "poll blind ~70s" in recovered[0]
    assert "backoff included" in recovered[0]


def test_a_healthy_run_never_logs_a_recovery():
    clock = FakeClock()
    rec = _Recorder()
    p, _ = _poller(clock, logger=rec)
    _run_cycles(p, clock, 4)
    assert not [m for _, m in rec.lines if "recovered" in m]


def test_a_busy_cycle_is_not_evidence_the_server_is_back():
    """A live turn skips the fetch entirely. It must neither reset the backoff
    nor log a recovery for a server nobody has talked to."""
    clock = FakeClock()
    calls = {"n": 0}

    def down_then_up():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("502")
        return []

    rec = _Recorder()
    # cycle 1: idle -> fetch fails; cycle 2: busy (no fetch); cycle 3: idle -> ok
    p, _ = _poller(clock, fetch=down_then_up, busy_seq=[False, True, False], logger=rec)
    waits = []
    n = {"i": 0}

    def shutdown_wait(timeout):
        waits.append(timeout)
        clock.advance(timeout)
        n["i"] += 1
        return n["i"] > 3

    p.run(shutdown_wait)
    recovered = [m for _, m in rec.lines if "recovered" in m]
    assert len(recovered) == 1 and "1 failed poll;" in recovered[0]
    # the busy cycle kept the backoff (10 s), it did not snap back to 5 s
    assert waits[2] == 10.0
