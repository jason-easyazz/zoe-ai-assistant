"""The probe's COLD FIRST-TURN stage: a first turn that misses the KV cache, or whose TTFT regresses against the
baseline's own cold TTFT, fails the run; a stage that could not measure is an error, never a pass.

Negative controls: a cold miss (cache_n=0, the 2026-10-27 signature) and a 2x TTFT both go red.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
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

HIT = {"status": "ok", "warm": "warmed", "first_prompt_n": 120, "first_cache_n": 3400, "cache_frac": 0.966,
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


def test_unmeasurable_is_an_error_not_a_pass():
    assert _judge({"status": "error", "reason": "boom"})["status"] == "error"
    assert _judge({**HIT, "warm": "skipped:inflight"})["status"] == "error"
    assert _judge(None)["status"] == "error"
    assert _judge({"status": "skip", "reason": "not flue"})["status"] == "skip"


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


def _fake_service(monkeypatch, *, prompt_n, cache_n):
    """Stub the three production modules the child imports: this tests ITS sequencing and arithmetic."""
    order: list[str] = []
    bd = types.ModuleType("brain_dispatch")
    bd.use_flue_brain = lambda: True
    sg = types.ModuleType("brain_slot_guard")
    sg.quiet_s = lambda: 45.0
    zf = types.ModuleType("zoe_flue_client")

    async def kv_turn(sid, state, message=""):
        order.append("evict")
        return "warmed", 1, 0

    async def warm(member=""):
        order.append(f"warm:{member}")
        return "warmed"

    def log_cache(session_id, terminal):
        order.append("log")

    async def turn(message, session_id, user_id="", **kw):
        assert kw.get("replay_isolation") is True
        order.append("turn")
        zf._log_prompt_cache("s", {"prompt_cache": [{"prompt_n": prompt_n, "cache_n": cache_n}]})
        yield "hello"

    zf._kv_warm_turn, zf.warm_kv_prefix, zf._log_prompt_cache, zf.run_flue_brain_streaming = kv_turn, warm, log_cache, turn
    for name, mod in (("brain_dispatch", bd), ("brain_slot_guard", sg), ("zoe_flue_client", zf)):
        monkeypatch.setitem(sys.modules, name, mod)
    return order


def test_child_sequence_and_arithmetic(monkeypatch):
    order = _fake_service(monkeypatch, prompt_n=100, cache_n=300)
    r = asyncio.run(mcf.measure("jason", 0.0, 0.0))
    assert order == ["evict", "warm:jason", "turn", "log"]            # evict -> wait -> wake warm -> first turn
    assert r["status"] == "ok" and r["cache_frac"] == 0.75 and r["warm"] == "warmed" and r["ttft_ms"] >= 0
    assert _judge(r)["status"] == "fail"                              # 75% < the 80% floor, end to end
