"""The probe's COLD FIRST-TURN stage: a first turn that misses the KV cache, or whose TTFT regresses against the
baseline's own cold TTFT, fails the run; a stage that could not measure is an error, never a pass.

Negative controls: a cold miss (cache_n=0, the 2026-10-27 signature) and a 2x TTFT both go red.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("vrp_cold", "scripts/maintenance/voice_regression_probe.py")
mcf = _load("mcf_cold", "scripts/perf/measure_cold_first_turn.py")

#: env keys the code under test writes DIRECTLY (os.environ[...] =), which monkeypatch cannot restore
_DIRECT_ENV_WRITES = ("ZOE_FLUE_STREAM_ENABLED", "ZOE_TRAFFIC_CLASS")
#: the caller's own values at import: a restore is judged against THESE, never against "unset"
_ENV_AT_IMPORT = {k: os.environ.get(k) for k in _DIRECT_ENV_WRITES}


@pytest.fixture(autouse=True)
def _restore_direct_env_writes():
    """measure() / main() set these with os.environ directly; snapshot and restore them around EVERY test so a later
    test in the lane never inherits streaming-on or a probe traffic class (test-order independence)."""
    saved = {k: os.environ.get(k) for k in _DIRECT_ENV_WRITES}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

HIT = {"status": "ok", "warm": "warmed", "memo_restart": True, "first_prompt_n": 120, "first_cache_n": 3400, "cache_frac": 0.966,
       "ttft_ms": 1300.0}
MISS = {**HIT, "first_prompt_n": 3500, "first_cache_n": 0, "cache_frac": 0.0, "ttft_ms": 6100.0}
BASE = {"cold_first_turn": {"ttft_ms": 1250.0}}


def _judge(m, base=BASE):
    return vrp.judge_cold_start(m, base, 1.5, 1500)


def test_warm_hit_within_baseline_passes():
    r = _judge(HIT)
    assert r["status"] == "pass" and r["baseline_ttft_ms"] == 1250.0 and r["first_cache_n"] == 3400


def test_negative_control_cold_miss_fails_on_the_cache_floor():
    r = _judge({**MISS, "ttft_ms": 1300.0})          # even with a lucky TTFT the cache floor trips
    assert r["status"] == "fail" and "missed the KV cache" in r["reason"]


def test_negative_control_ttft_regression_fails_even_with_a_cache_hit():
    r = _judge({**HIT, "ttft_ms": 2600.0})
    assert r["status"] == "fail" and "TTFT" in r["reason"] and r["ttft_ratio"] > 2


def test_small_jitter_is_not_a_regression():
    assert _judge({**HIT, "ttft_ms": 1350.0})["status"] == "pass"


def test_no_cold_baseline_checks_the_floor_only_and_says_so():
    ok = _judge(HIT, base={})
    assert ok["status"] == "pass" and "no cold-turn baseline" in ok["reason"]
    assert _judge(MISS, base={})["status"] == "fail"


def test_nan_is_unmeasured_not_a_pass():
    lying = _judge({**MISS, "cache_frac": 1.0})           # a child reporting a good fraction over a cold miss
    assert lying["status"] == "fail" and lying["cache_frac"] == 0.0
    assert _judge({**HIT, "ttft_ms": float("nan")})["status"] == "error"


def test_unmeasurable_is_an_error_not_a_pass():
    assert _judge({"status": "error", "reason": "boom"})["status"] == "error"
    assert _judge({**HIT, "warm": "skipped:inflight"})["status"] == "error"
    assert _judge(None)["status"] == "error"
    assert _judge({"status": "skip", "reason": "not flue"})["status"] == "error"   # enabled but measured nothing
    for drop in ("first_prompt_n", "first_cache_n"):
        assert _judge({k: v for k, v in HIT.items() if k != drop})["status"] == "error"   # absent is not zero
    assert _judge({**HIT, "first_prompt_n": 0, "first_cache_n": 0})["status"] == "error"
    assert _judge({**HIT, "first_prompt_n": -1})["status"] == "error"


def test_fold_only_tightens():
    fold = vrp.fold_cold_status
    assert fold("pass", {"status": "fail"}) == "fail" and fold("skip", {"status": "fail"}) == "fail"
    assert fold("pass", {"status": "error"}) == "error" and fold("fail", {"status": "error"}) == "fail"
    assert fold("pass", {"status": "skip"}) == "pass" and fold("pass", {"status": "pass"}) == "pass"


def test_artifact_carries_the_block_and_the_trend(tmp_path):
    args = SimpleNamespace(results=tmp_path / "last.json", trend=tmp_path / "trend.jsonl",
                           baseline=tmp_path / "b.json", alert_after_non_pass=3)
    cold = _judge(MISS)
    vrp.emit_result(args, status="fail", summary=dict(vrp.EMPTY_SUMMARY), said_vs_did=[], speed_deltas={},
                    baseline={}, reason="x", cold=cold)
    art = json.loads(args.results.read_text())
    assert art["cold_first_turn_stage"] is True and art["cold_first_turn"]["status"] == "fail"
    assert json.loads(args.trend.read_text().splitlines()[-1])["cold_first_turn"]["first_cache_n"] == 0
    vrp.emit_result(args, status="error", summary=dict(vrp.EMPTY_SUMMARY), said_vs_did=[], speed_deltas={},
                    baseline={}, reason="x")                       # an exit path that never ran the stage says so
    assert json.loads(args.results.read_text())["cold_first_turn"]["status"] == "skip"


def test_disabled_stage_is_a_recorded_skip():
    r = vrp.run_cold_start_stage(SimpleNamespace(cold_check=False), {})
    assert r["status"] == "skip" and "disabled" in r["reason"]


def _fake_service(monkeypatch, *, prompt_n, cache_n, chunks=("hello",), delay=0.0, pool_ok=True, evict="warmed",
                  manner_on=True, manner_block_text="BLOCK", turn_manner=True,
                  persona_text="P", turn_persona=True, adult=True, guest=False, adult_raises=False):
    """Stub the production modules the child imports: this tests ITS sequencing, arithmetic and guards."""
    order: list[str] = []
    bd = types.ModuleType("brain_dispatch")
    bd.use_flue_brain = lambda: True
    sg = types.ModuleType("brain_slot_guard")
    sg.quiet_s = lambda: 45.0
    dp = types.ModuleType("db_pool")

    async def init_pool():
        if not pool_ok:
            raise RuntimeError("no db")
        order.append("pool_open")

    async def close_pool():
        order.append("pool_close")

    dp.init_pool, dp.close_pool = init_pool, close_pool
    mb = types.ModuleType("manner_block")
    mb.enabled = lambda: manner_on
    mb.is_guest = lambda u: guest

    async def _adult(u):
        if adult_raises:
            raise RuntimeError("db")
        return adult

    mb._account_is_adult = _adult
    zf = types.ModuleType("zoe_flue_client")

    zf._wrap_message_with_replay = lambda m, on: " zoe-replay:1\n" + m if on else m

    async def manner_ctx(member, message):
        return manner_block_text

    zf._manner_context_block = manner_ctx
    zf._leading_envelope_lines = lambda m: {"manner": "x" if turn_manner else "", "persona": "p" if turn_persona else ""}

    async def persona_ctx(member):
        return persona_text

    zf._persona_context_block = persona_ctx
    zf._note_turn_envelope = lambda uid, sid, outbound: None

    async def kv_turn(sid, state, message=""):
        assert message.startswith(" zoe-replay:1\n"), "every raw sidecar request of the probe must be replay-isolated"
        order.append("evict")
        return evict, 1, 0

    async def warm(member=""):
        order.append(f"warm:{member}")
        return "warmed"

    def log_cache(session_id, terminal):
        order.append("log")

    async def turn(message, session_id, user_id="", **kw):
        assert kw.get("replay_isolation") is True
        assert os.environ.get("ZOE_FLUE_STREAM_ENABLED") == "1", "the stage must measure the streaming path the panel uses"
        order.append("turn")
        zf._note_turn_envelope(user_id, session_id, "wire")
        zf._log_prompt_cache("s", {"prompt_cache": [{"prompt_n": prompt_n, "cache_n": cache_n}]})
        for c in chunks:
            await asyncio.sleep(delay)
            yield c

    async def wrapper(*a, **k):    # the brief / proactive claim-and-settle wrapper: the probe must never go through it
        raise AssertionError("cold probe called run_flue_brain_streaming: it claims household state")
        yield ""

    zf.run_flue_brain_streaming = wrapper
    zf._kv_warm_turn, zf.warm_kv_prefix, zf._log_prompt_cache, zf._run_flue_brain_streaming_turn = kv_turn, warm, log_cache, turn
    for name, mod in (("brain_dispatch", bd), ("brain_slot_guard", sg), ("zoe_flue_client", zf), ("db_pool", dp),
                      ("manner_block", mb)):
        monkeypatch.setitem(sys.modules, name, mod)
    # NOT monkeypatch.delenv: a second _fake_service call in one test would make monkeypatch record the stage's own "1" as
    # the original and restore it at teardown, after the autouse restore below. The autouse fixture owns this key.
    os.environ.pop("ZOE_FLUE_STREAM_ENABLED", None)
    return order


def test_child_sequence_and_arithmetic(monkeypatch):
    order = _fake_service(monkeypatch, prompt_n=100, cache_n=300)
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    # pool open -> evict -> wait -> wake warm -> first turn -> pool closed
    assert order == ["pool_open", "evict", "warm:jason", "turn", "log", "pool_close"]
    assert r["status"] == "ok" and r["cache_frac"] == 0.75 and r["warm"] == "warmed" and r["ttft_ms"] >= 0
    assert _judge(r)["status"] == "fail"                              # 75% < the 80% floor, end to end


def test_child_missing_counts_are_not_zeroed(monkeypatch):
    _fake_service(monkeypatch, prompt_n=None, cache_n=300)               # prompt_n absent from the terminal
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "prompt_cache" in r["reason"]


def test_no_db_pool_is_an_error_not_a_blockless_measurement(monkeypatch):
    order = _fake_service(monkeypatch, prompt_n=10, cache_n=900, pool_ok=False)
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "db pool" in r["reason"] and "evict" not in order


def test_member_blocks_enabled_but_not_rendered_is_an_error(monkeypatch):
    _fake_service(monkeypatch, prompt_n=10, cache_n=900, manner_block_text="")          # adult lookup failed closed
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "manner" in r["reason"]


def test_turn_envelope_without_the_enabled_block_is_an_error(monkeypatch):
    _fake_service(monkeypatch, prompt_n=10, cache_n=900, turn_manner=False)
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "no manner block" in r["reason"]


def test_blocks_off_live_means_an_empty_envelope_is_fine(monkeypatch):
    _fake_service(monkeypatch, prompt_n=10, cache_n=900, manner_on=False, manner_block_text="", turn_manner=False)
    assert asyncio.run(mcf.measure("jason", 0.0, 0.0))["status"] == "ok"


def test_ttft_is_the_first_reply_text_not_a_tool_or_thinking_sentinel(monkeypatch):
    _fake_service(monkeypatch, prompt_n=10, cache_n=900, delay=0.06,
                  chunks=("__THINKING__:{}", "__TOOL__:{}", "  ", "hello"))
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["ttft_ms"] >= 3 * 60 * 0.9, r          # waited through three non-text chunks (the control: ~60 ms if counted)
    assert mcf._is_reply_text("hello") and not mcf._is_reply_text("__TOOL__:x") and not mcf._is_reply_text("")


def test_an_incomplete_eviction_is_an_error(monkeypatch):
    order = _fake_service(monkeypatch, prompt_n=10, cache_n=900, evict="http_503")
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "eviction" in r["reason"] and not any(o.startswith("warm") for o in order)


def test_recording_a_baseline_skips_the_old_ttft_bar_but_not_the_floor():
    slow = {**HIT, "ttft_ms": 6000.0}
    assert _judge(slow)["status"] == "fail"                                                  # normal run: regression
    rec = vrp.judge_cold_start(slow, BASE, 1.5, 1500, recording_baseline=True)
    assert rec["status"] == "pass" and rec["baseline_ttft_ms"] is None
    assert vrp.judge_cold_start(MISS, BASE, 1.5, 1500, recording_baseline=True)["status"] == "fail"
    assert vrp.judge_cold_start({**slow, "warm": "skipped:off"}, BASE, 1.5, 1500,
                                recording_baseline=True)["status"] == "error"


def test_persona_block_rendered_for_the_member_must_be_in_the_measured_turn(monkeypatch):
    _fake_service(monkeypatch, prompt_n=10, cache_n=900, turn_persona=False)
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "persona" in r["reason"]
    _fake_service(monkeypatch, prompt_n=10, cache_n=900, persona_text="", turn_persona=False)   # fixed persona: fine
    assert asyncio.run(mcf.measure("jason", 0.0, 0.0))["status"] == "ok"


def test_ineligible_speakers_legitimately_have_no_manner_block(monkeypatch):
    for kw in ({"guest": True}, {"adult": False}):                     # a guest, a child
        _fake_service(monkeypatch, prompt_n=10, cache_n=900, manner_block_text="", turn_manner=False, **kw)
        assert asyncio.run(mcf.measure("jason", 0.0, 0.0))["status"] == "ok", kw


def test_a_failed_adult_lookup_is_an_error_not_an_absent_block(monkeypatch):
    _fake_service(monkeypatch, prompt_n=10, cache_n=900, manner_block_text="", turn_manner=False, adult_raises=True)
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "adult lookup failed" in r["reason"]


def test_a_direct_run_obeys_the_harness_quiet_hours(monkeypatch, capsys):
    """Run directly (not by the lander) in waking hours, the stage refuses before any brain traffic: it is an
    unmeasured run (error), and measure() is never called."""
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(sys, "argv", ["measure_cold_first_turn.py", "--service-dir", "/nonexistent"])
    called: list = []
    monkeypatch.setattr(mcf, "measure", lambda *a, **k: called.append(a))
    monkeypatch.setattr(mcf, "_quiet_gate", lambda: SimpleNamespace(allowed=False, reason="harness quiet hours (07:00-22:00)"))
    assert mcf.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "error" and "quiet hours" in out["reason"]
    assert called == [], "no eviction / warm / turn may be sent once the gate refuses"


def test_the_quiet_gate_keeps_the_landing_exemption(monkeypatch):
    import datetime as dt

    hq = _load("hq_cold", "scripts/maintenance/harness_quiet.py")
    monkeypatch.setitem(sys.modules, "harness_quiet", hq)
    noon = dt.datetime(2026, 10, 28, 12, 0, 0)
    monkeypatch.delenv("ZOE_HARNESS_FORCE", raising=False)
    monkeypatch.setenv("ZOE_HARNESS_LANDING", "1")
    assert hq.check("measure_cold_first_turn", now=noon, panel=lambda: True).allowed
    monkeypatch.delenv("ZOE_HARNESS_LANDING")
    assert not hq.check("measure_cold_first_turn", now=noon, panel=lambda: True).allowed
    assert mcf._quiet_gate.__code__.co_names.count("check") == 1   # the stage asks THIS gate, not a copy


def test_the_cold_warm_gets_its_member_from_the_persisted_memo_not_the_argument(monkeypatch):
    """2026-10-28: the stage told the warm the member explicitly, so it could not see a speaker memo that did not survive a
    restart. The warm's member now comes only from a simulated-restart read of the memo (warm_speaker), never from --user."""
    import warm_speaker

    order = _fake_service(monkeypatch, prompt_n=100, cache_n=3400)
    seen = []
    real_get = warm_speaker.get
    monkeypatch.setattr(warm_speaker, "get", lambda panel, **k: seen.append(panel) or real_get(panel, **k))
    saved = os.environ.get("ZOE_WARM_SPEAKER_STATE")
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert seen == [mcf.PROBE_PANEL] and r["memo_restart"] is True and "warm:jason" in order
    assert _judge(r)["status"] == "pass"
    assert os.environ.get("ZOE_WARM_SPEAKER_STATE") == saved, "the throwaway state override must not leak"


def test_negative_control_a_memo_that_does_not_survive_the_restart_is_an_error_and_warms_nothing(monkeypatch):
    import warm_speaker

    order = _fake_service(monkeypatch, prompt_n=100, cache_n=3400)
    monkeypatch.setattr(warm_speaker, "get", lambda panel, **k: "")        # the persistence is broken
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert r["status"] == "error" and "did not survive" in r["reason"]
    assert not any(o.startswith("warm:") for o in order) and "turn" not in order
    assert _judge(r)["status"] == "error"


def test_a_measurement_that_skipped_the_restart_simulation_is_not_a_pass():
    assert _judge({k: v for k, v in HIT.items() if k != "memo_restart"})["status"] == "error"
    assert _judge({**HIT, "memo_restart": False})["status"] == "error"


def test_zz_no_direct_env_write_leaks_past_a_measurement():
    """Runs after the measurement tests in this module: none of their direct env writes may still be set."""
    for k in _DIRECT_ENV_WRITES:
        assert os.environ.get(k) == _ENV_AT_IMPORT[k], f"{k} leaked out of a measurement test"


def test_the_default_wait_is_fixed_and_not_the_slot_guard_quiet_window(monkeypatch):
    """The child never takes the guard, so the wait only simulates a pause: it must not grow with quiet_s() (180 s)."""
    _fake_service(monkeypatch, prompt_n=10, cache_n=900)
    monkeypatch.setattr(sys.modules["brain_slot_guard"], "quiet_s", lambda: 180.0)
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s, *a, **k):
        slept.append(s)
        await real_sleep(0)

    monkeypatch.setattr(mcf.asyncio, "sleep", fake_sleep)
    r = asyncio.run(mcf.measure("jason", None, 0.0))
    assert r["status"] == "ok" and r["wait_s"] == mcf.DEFAULT_WAIT_S == 47.0
    assert 47.0 in slept and 182.0 not in slept
