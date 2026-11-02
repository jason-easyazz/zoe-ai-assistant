"""``brain_prefix_keeper``: put the household's brain prefix BACK in the one llama slot after something displaced it.

Measured 2026-11-02 (docs/knowledge/cold-wake-2026-11-02.md): 12 of 26 wake warm-ups were a cold ~3.3k-token prefill and 10 of
those followed a helper batch. Each test has a control that must NOT warm, so a keeper that warmed on every tick would fail.
No test sleeps (fake clock) and nothing talks to a real llama-server.
"""
from __future__ import annotations

import asyncio
import fcntl
import os

import pytest

import brain_prefix_keeper as k
import brain_slot_guard as g
import warm_speaker

pytestmark = pytest.mark.ci_safe


class Clock:
    t = 5000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(monkeypatch, tmp_path):
    clk = Clock()
    clk.t = 5000.0
    monkeypatch.setattr(k, "_clock", clk)
    monkeypatch.setattr(k, "WINDOW_LOCK", str(tmp_path / "no-such.lock"))
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.delenv(k.ENV, raising=False)
    monkeypatch.delenv(k.ENV_MIN_TOKENS, raising=False)
    monkeypatch.delenv("ZOE_BRAIN_KV_WARM_ON_WAKE", raising=False)
    k.reset()
    g.reset()
    state = {"slot": {"tokens": 387, "processing": False}, "member": "jason", "warm_calls": [], "warm_outcome": "warmed"}

    async def _slot():
        return state["slot"]

    async def _warm(member="", *, yield_to_helpers=False):
        state["warm_calls"].append(member)
        state.setdefault("yield_flags", []).append(yield_to_helpers)
        state["during_warm"]() if state.get("during_warm") else None
        return state["warm_outcome"]

    import zoe_flue_client as zfc

    monkeypatch.setattr(k, "read_slot", _slot)
    monkeypatch.setattr(zfc, "warm_kv_prefix", _warm)
    monkeypatch.setattr(warm_speaker, "latest", lambda *a, **kw: state["member"])
    yield state, clk
    k.reset()
    g.reset()


def _tick():
    return asyncio.run(k.tick())


def _settle(clk):
    assert _tick() == "settling"
    clk.t += k.SETTLE_S + 1


def test_a_displaced_slot_is_re_warmed_for_the_likely_speaker_after_the_settle(env):
    state, clk = env
    _settle(clk)
    assert _tick() == "warm:warmed"
    assert state["warm_calls"] == ["jason"]


def test_control_a_resident_slot_is_never_warmed(env):
    state, clk = env
    state["slot"] = {"tokens": 3300, "processing": False}
    for _ in range(5):
        assert _tick() == "resident"
        clk.t += 60
    assert state["warm_calls"] == []


def test_the_settle_delay_holds_a_back_to_back_helper_burst_off(env):
    state, clk = env
    assert _tick() == "settling"
    clk.t += k.SETTLE_S - 1
    assert _tick() == "settling" and state["warm_calls"] == []
    state["slot"] = {"tokens": 3300, "processing": False}   # a helper reloaded a big prompt: the debounce restarts
    assert _tick() == "resident"
    state["slot"] = {"tokens": 400, "processing": False}
    assert _tick() == "settling"   # a fresh settle, not the old one


def test_a_busy_slot_is_left_alone_while_a_turn_or_a_helper_batch_is_live(env, monkeypatch):
    state, clk = env
    g.turn_started()
    assert _tick() == "busy"
    g.turn_finished()
    assert _tick() == "busy"   # still hot: the turn ended inside the quiet window
    monkeypatch.setattr(g, "_clock", lambda: g._last_activity + g.quiet_s() + 1)
    monkeypatch.setattr(g, "_waiters", 1)
    assert _tick() == "busy"   # a helper is queued/running
    monkeypatch.setattr(g, "_waiters", 0)
    assert _tick() == "settling"   # control: with nothing live the keeper proceeds
    assert state["warm_calls"] == []


def test_processing_slot_is_not_a_displaced_slot(env):
    state, clk = env
    state["slot"] = {"tokens": 100, "processing": True}
    assert _tick() == "processing" and state["warm_calls"] == []


def test_unreachable_server_never_warms(env):
    state, clk = env
    state["slot"] = None
    assert _tick() == "unreachable"
    clk.t += 100
    assert _tick() == "unreachable" and state["warm_calls"] == []


def test_nobody_used_the_panel_recently_means_no_warm(env):
    state, clk = env
    state["member"] = ""
    assert _tick() == "no_speaker"
    clk.t += 100
    assert _tick() == "no_speaker" and state["warm_calls"] == []


def test_off_switch_and_non_flue_lane(env, monkeypatch):
    state, clk = env
    assert _tick() == "settling"      # a settle in progress ...
    monkeypatch.setenv(k.ENV, "0")
    assert _tick() == "off"
    assert k._state["displaced_since"] is None   # ... is forgotten when the flag goes off
    monkeypatch.setenv(k.ENV, "1")
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")
    assert _tick() == "lane_off"
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.setenv("ZOE_BRAIN_KV_WARM_ON_WAKE", "0")
    assert _tick() == "lane_off"
    assert state["warm_calls"] == []


def test_min_tokens_env_moves_the_resident_line(env, monkeypatch):
    state, clk = env
    state["slot"] = {"tokens": 1500, "processing": False}
    assert _tick() == "settling"   # below the 2000 default
    monkeypatch.setenv(k.ENV_MIN_TOKENS, "1000")
    assert _tick() == "resident"


def test_rate_gap_cap_and_failure_backoff(env):
    state, clk = env
    _settle(clk)
    assert _tick() == "warm:warmed"
    # the slot is displaced again straight away (e.g. an unguarded caller): the gap holds the next warm off
    assert _tick() == "settling"
    clk.t += k.SETTLE_S + 1
    assert _tick() == "rate_gap"
    clk.t += k.MIN_GAP_S
    assert _tick() == "warm:warmed" and len(state["warm_calls"]) == 2
    # hourly cap: pre-fill the budget
    k._state["warms"].extend([clk.t - 10] * (k.MAX_PER_HOUR - len(k._state["warms"])))
    clk.t += k.MIN_GAP_S + k.SETTLE_S
    _tick()
    clk.t += k.SETTLE_S + 1
    assert _tick() == "rate_cap"
    assert len(state["warm_calls"]) == 2
    # a window later the budget is back
    clk.t += 3700
    k._state["displaced_since"] = clk.t - k.SETTLE_S - 1
    assert _tick() == "warm:warmed"


def test_a_failed_warm_backs_off_and_a_skipped_one_costs_nothing(env):
    state, clk = env
    state["warm_outcome"] = "failed:timeout"
    _settle(clk)
    assert _tick() == "warm:failed:timeout"
    clk.t += k.MIN_GAP_S + 1
    assert _tick() == "backoff"       # the slot is still displaced, but the 300 s back-off after the failure holds
    assert len(state["warm_calls"]) == 1
    clk.t += k.FAIL_BACKOFF_S
    assert _tick() == "warm:failed:timeout" and len(state["warm_calls"]) == 2   # control: back-off over -> tries again
    # skipped:* (the warm declined: inflight / hot) ran nothing - no budget, no backoff, retried on the next poll
    k.reset()
    state["warm_calls"].clear()
    state["warm_outcome"] = "skipped:inflight"
    _settle(clk)
    assert _tick() == "warm:skipped:inflight"
    assert _tick() == "warm:skipped:inflight"
    assert len(k._state["warms"]) == 0 and k._state["backoff_until"] == 0.0


def test_a_broken_warm_cannot_take_the_loop_down(env, monkeypatch):
    state, clk = env
    import zoe_flue_client as zfc

    async def _boom(member="", *, yield_to_helpers=False):
        raise RuntimeError("sidecar exploded")

    monkeypatch.setattr(zfc, "warm_kv_prefix", _boom)
    _settle(clk)
    assert _tick() == "error"


def test_window_lock_held_is_detected_and_released(tmp_path):
    p = tmp_path / "w.lock"
    assert k.window_lock_held(str(p)) is False   # no file: not held
    p.write_text("")
    assert k.window_lock_held(str(p)) is False   # file, nobody holds it
    fd = os.open(str(p), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert k.window_lock_held(str(p)) is True   # a harness holds it
    finally:
        os.close(fd)
    assert k.window_lock_held(str(p)) is False      # and the check itself left nothing behind


def test_a_shared_holder_counts_as_held_too(tmp_path):
    """The A/B harnesses hold the brain-window lock SHARED (``flock -s``): the keeper must see them (an exclusive probe)."""
    p = tmp_path / "s.lock"
    p.write_text("")
    fd = os.open(str(p), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        assert k.window_lock_held(str(p)) is True
    finally:
        os.close(fd)
    assert k.window_lock_held(str(p)) is False


def test_a_keeper_warm_does_not_make_the_slot_hot_for_another_members_wake_but_holds_helpers_off(monkeypatch):
    """Greptile #2245: the keeper warmed member A with nobody speaking; member B's wake must not read 'hot' afterwards."""
    t = {"now": 1000.0}
    monkeypatch.setattr(g, "_clock", lambda: t["now"])
    g.reset()
    g.turn_started(keeper=True)
    assert g.is_hot() is True                    # in flight: a second wake in the ~5 s of the warm still sees it busy
    g.turn_finished(keeper=True)
    assert g.is_hot() is False                   # B's wake_warm would not be skipped:hot
    assert g._helpers_wait() is True             # but post-turn helpers stay off the prefix just loaded
    t["now"] += g.quiet_s() + 1
    assert g._helpers_wait() is False
    g.turn_started()                             # control: a real turn does make the slot hot
    g.turn_finished()
    assert g.is_hot() is True


def test_the_window_lock_is_held_for_the_whole_warm_and_released_after(env, monkeypatch, tmp_path):
    """A harness that starts (exclusive) while the warm is in flight must be refused/waited, not run under it."""
    state, clk = env
    p = tmp_path / "w.lock"
    p.write_text("")
    monkeypatch.setattr(k, "WINDOW_LOCK", str(p))
    seen = []

    def _during():
        fd = os.open(str(p), os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append("exclusive-granted")
            except BlockingIOError:
                seen.append("exclusive-refused")
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)   # a harness preflight probes SHARED: it must see the warm
                seen.append("shared-granted")
            except BlockingIOError:
                seen.append("shared-refused")
        finally:
            os.close(fd)

    state["during_warm"] = _during
    _settle(clk)
    assert _tick() == "warm:warmed"
    assert seen == ["exclusive-refused", "shared-refused"]
    assert k.window_lock_held(str(p)) is False      # released once the warm returned
    assert state["yield_flags"] == [True]           # the keeper always asks the warm to yield to a queued helper


def test_the_keeper_warm_yields_to_a_helper_that_queued_meanwhile(env):
    state, clk = env
    state["warm_outcome"] = "skipped:busy"
    _settle(clk)
    assert _tick() == "warm:skipped:busy"
    assert len(k._state["warms"]) == 0 and k._state["backoff_until"] == 0.0


def test_window_lock_blocks_the_warm(env, monkeypatch, tmp_path):
    state, clk = env
    p = tmp_path / "held.lock"
    p.write_text("")
    monkeypatch.setattr(k, "WINDOW_LOCK", str(p))
    fd = os.open(str(p), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert _tick() == "window_lock"
        clk.t += 100
        assert _tick() == "window_lock" and state["warm_calls"] == []
    finally:
        os.close(fd)
    assert _tick() == "settling"   # control: released -> proceeds


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        if isinstance(self._b, Exception):
            raise self._b
        return self._b


def _fake_httpx(monkeypatch, resp=None, exc=None):
    import httpx

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            _C.url = url
            if exc:
                raise exc
            return resp

    monkeypatch.setattr(httpx, "AsyncClient", _C)
    return _C


def test_read_slot_parses_n_prompt_tokens_and_treats_a_fresh_slot_as_empty(monkeypatch):
    c = _fake_httpx(monkeypatch, _Resp(200, [{"id": 0, "is_processing": False, "n_prompt_tokens": 387}]))
    assert asyncio.run(k.read_slot()) == {"tokens": 387, "processing": False}
    assert c.url.endswith("/slots") and "/v1/" not in c.url
    _fake_httpx(monkeypatch, _Resp(200, [{"id": 0, "is_processing": True}]))   # never ran a task: key absent
    assert asyncio.run(k.read_slot()) == {"tokens": 0, "processing": True}


def test_read_slot_fails_closed_without_an_explicit_processing_flag(monkeypatch):
    _fake_httpx(monkeypatch, _Resp(200, [{"id": 0, "n_prompt_tokens": 10}]))              # field missing
    assert asyncio.run(k.read_slot()) is None
    _fake_httpx(monkeypatch, _Resp(200, [{"id": 0, "is_processing": "no", "n_prompt_tokens": 10}]))   # not a bool
    assert asyncio.run(k.read_slot()) is None
    _fake_httpx(monkeypatch, _Resp(200, [{"id": 0, "is_processing": False}, {"id": 1}]))   # one slot says nothing
    assert asyncio.run(k.read_slot()) is None


def test_read_slot_with_several_slots_is_busy_if_any_is_and_resident_if_any_holds_the_prefix(monkeypatch):
    _fake_httpx(monkeypatch, _Resp(200, [{"id": 0, "is_processing": False, "n_prompt_tokens": 3300},
                                         {"id": 1, "is_processing": True, "n_prompt_tokens": 400}]))
    assert asyncio.run(k.read_slot()) == {"tokens": 3300, "processing": True}


@pytest.mark.parametrize("resp,exc", [
    (_Resp(503, []), None), (_Resp(200, {"not": "a list"}), None), (_Resp(200, []), None),
    (_Resp(200, ValueError("not json")), None), (None, OSError("refused")),
])
def test_read_slot_returns_none_when_it_cannot_tell(monkeypatch, resp, exc):
    _fake_httpx(monkeypatch, resp, exc)
    assert asyncio.run(k.read_slot()) is None


def test_warm_speaker_latest_is_the_newest_unexpired_member(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_WARM_SPEAKER_STATE", str(tmp_path / "ws.json"))
    warm_speaker.reset()
    try:
        assert warm_speaker.latest(now=1000.0) == ""
        warm_speaker._ensure_loaded()
        with warm_speaker._lock:
            warm_speaker._mem["panel-a"] = ("old-member", 1000.0)
            warm_speaker._mem["panel-b"] = ("new-member", 2000.0)
        assert warm_speaker.latest(now=2100.0) == "new-member"
        assert warm_speaker.latest(now=2000.0 + warm_speaker.max_age_s() + 1) == ""   # expired: no hint
    finally:
        warm_speaker.reset()


def test_guard_waiters_counts_queued_background_calls(monkeypatch):
    monkeypatch.setenv(g.ENV, "on")
    g.reset()
    seen = []

    async def _go():
        async with g.background_slot("x"):
            seen.append(g.waiters())
        seen.append(g.waiters())

    asyncio.run(_go())
    assert seen == [1, 0]


def test_main_starts_the_keeper_behind_its_flag():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "main.py").read_text()
    assert "brain_prefix_keeper.run_loop()" in src and "brain_prefix_keeper.enabled()" in src


# ── warm_kv_prefix(yield_to_helpers=True): the keeper's warm never takes the slot from under a helper ──────────────────


@pytest.fixture
def warm_env(monkeypatch):
    import zoe_flue_client as zfc

    g.reset()
    monkeypatch.setenv(g.ENV, "on")
    monkeypatch.delenv("ZOE_BRAIN_KV_WARM_ON_WAKE", raising=False)
    monkeypatch.setattr(zfc, "_kv_warm_inflight", False)
    monkeypatch.setattr(zfc, "_kv_warm_session", {"id": "", "uses": 0, "seq": 0})
    calls = {"turns": 0, "during_prep": None}

    async def _outbound(member):
        if calls["during_prep"]:
            calls["during_prep"]()          # a helper queues while the preparation awaits storage
        return "msg", "key"

    async def _turn(session_id, state, message=""):
        calls["turns"] += 1
        state["settled"] = True
        return "warmed", 13, 2657

    monkeypatch.setattr(zfc, "_kv_warm_outbound", _outbound)
    monkeypatch.setattr(zfc, "_kv_warm_turn", _turn)
    yield zfc, calls
    g.reset()


def test_yield_to_helpers_declines_at_entry_when_a_helper_is_queued(warm_env, monkeypatch):
    zfc, calls = warm_env
    monkeypatch.setattr(g, "_waiters", 1)
    assert asyncio.run(zfc.warm_kv_prefix("", yield_to_helpers=True)) == "skipped:busy"
    assert calls["turns"] == 0


def test_yield_to_helpers_declines_when_a_helper_queues_during_preparation(warm_env, monkeypatch):
    zfc, calls = warm_env
    calls["during_prep"] = lambda: monkeypatch.setattr(g, "_waiters", 1)
    assert asyncio.run(zfc.warm_kv_prefix("", yield_to_helpers=True)) == "skipped:busy"
    assert calls["turns"] == 0 and g.active_turns() == 0   # and it never took the slot


def test_control_the_wake_warm_does_not_yield_to_helpers(warm_env, monkeypatch):
    """The wake path keeps its old behaviour (a helper mid-run does not cancel the wake's warm)."""
    zfc, calls = warm_env
    monkeypatch.setattr(g, "_waiters", 1)
    assert asyncio.run(zfc.warm_kv_prefix("")) == "warmed"
    assert calls["turns"] == 1


def test_control_with_no_helper_the_keeper_warm_runs(warm_env):
    zfc, calls = warm_env
    assert asyncio.run(zfc.warm_kv_prefix("", yield_to_helpers=True)) == "warmed"
    assert calls["turns"] == 1
