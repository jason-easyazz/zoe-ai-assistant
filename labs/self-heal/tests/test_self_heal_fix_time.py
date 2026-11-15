"""Fault-to-fix time (gap audit 2026-11-14.6): a verified heal carries ``fix_s``, seconds from the detect run that ticketed the
fault to the passing reproduction. A lab measure in the result, the audit row and the ticket record; nothing else changes.
FAKE engine on throwaway repos; no model, no GitHub, no network."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from conftest import FakeEngine, fix_calc
from selfheal import runner as runner_mod
from selfheal.landing import NoopApplier
from selfheal.runner import Runner

NOON = dt.datetime(2026, 11, 13, 12, 0)


class Stepping:
    """A wall clock that moves 30 s every time anything reads it."""

    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return NOON + dt.timedelta(seconds=30 * self.n)


def _audit(cfg):
    return [json.loads(x) for x in (Path(cfg.state_dir) / "audit.jsonl").read_text().splitlines()]


def _state(cfg):
    return json.loads(next(Path(cfg.state_dir).glob("state*.json")).read_text())


def test_a_local_heal_carries_fix_seconds_everywhere(local_scratch):
    cfg, clk = local_scratch.cfg, Stepping()
    out = Runner(cfg, engine=FakeEngine(fix_calc), now=clk).run_once()
    assert out["outcome"] == "healed_local", out
    assert isinstance(out["fix_s"], float) and out["fix_s"] > 0 and out["fix_s"] % 30 == 0
    (row,) = [r for r in _audit(cfg) if r["step"] == "result"]
    assert row["fix_s"] == out["fix_s"]
    rec = next(iter(_state(cfg)["tickets"].values()))
    assert rec["fix_s"] == out["fix_s"] and dt.datetime.fromisoformat(rec["detected_at"]) > NOON


def test_a_full_local_landing_heal_carries_it_too(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    out = Runner(cfg, engine=FakeEngine(fix_calc), applier=NoopApplier(cfg), now=Stepping()).run_once()
    assert out["outcome"] == "healed" and out["fix_s"] > 0


def test_a_run_that_does_not_end_in_a_verified_fix_has_no_fix_seconds(local_scratch):
    cfg = local_scratch.cfg
    out = Runner(cfg, engine=FakeEngine(fix_calc, ok=False), now=Stepping()).run_once()
    assert out["outcome"] == "discarded" and "fix_s" not in out
    assert all("fix_s" not in r for r in _audit(cfg))


def test_behaviour_neutral_the_outcome_is_the_same_with_the_measure_off(local_scratch, monkeypatch, tmp_path):
    cfg = local_scratch.cfg
    on = Runner(cfg, engine=FakeEngine(fix_calc), now=lambda: NOON).run_once()
    assert on["fix_s"] == 0.0                                    # a frozen clock: zero, not absent
    from conftest import make_cfg, seed_repo, sh
    live2, repo2 = tmp_path / "live2", tmp_path / "repo2.git"
    seed_repo(live2)
    sh("git", "clone", "-q", "--bare", str(live2), str(repo2))
    cfg2 = make_cfg(tmp_path / "b", repo=repo2, live=live2)
    monkeypatch.setattr(runner_mod, "VERIFIED", ())              # the measure off
    off = Runner(cfg2, engine=FakeEngine(fix_calc), now=lambda: NOON).run_once()
    strip = lambda d: {k: v for k, v in d.items() if k not in ("fix_s", "sha", "ticket", "branch")}  # noqa: E731
    assert strip(on) == strip(off) and "fix_s" not in off


def test_fix_seconds_is_none_when_it_cannot_be_known(local_scratch):
    r = Runner(local_scratch.cfg, engine=FakeEngine(fix_calc), now=lambda: NOON)
    assert r._fix_seconds({}) is None
    assert r._fix_seconds({"detected_at": "not a time"}) is None
    assert r._fix_seconds({"detected_at": (NOON + dt.timedelta(seconds=5)).isoformat()}) is None   # a clock step back
    assert r._fix_seconds({"detected_at": (NOON - dt.timedelta(seconds=90)).isoformat()}) == 90.0
