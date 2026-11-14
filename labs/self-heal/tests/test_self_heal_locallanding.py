"""Landing with no GitHub (`selfheal/locallander.py`, `selfheal/ledger.py`): merge, proof on the merged
tree, health window, ledger, rollback, the two shapes, a kill mid-landing, two loops on one repo.
Fake engine, fake probe, noop applier; every repo is a throwaway bare repo under tmp_path."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
from pathlib import Path

import pytest

from conftest import FIXED, FakeEngine, fix_calc, make_cfg, sh
from selfheal import gitops
from selfheal.landing import NoopApplier
from selfheal.ledger import Ledger, LedgerBroken, proof_hash
from selfheal.locallander import REASONS, FakeProbe, HardKill, LedgerLanding, SoldUnit, ThisUnit
from selfheal.runner import Runner
from test_self_heal import NOON, state
from test_self_heal_sandbox import needs_bwrap

LEGACY = "from calc import add\n\n\ndef test_legacy():\n    assert add(5, 3) == 2\n"   # relies on the bug
NETWORK = {"fetch", "push", "pull", "clone", "ls-remote"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def sold(scratch, **kw):
    """A sold unit's repo: full dispatch, local landing, NO remote."""
    cfg = scratch.cfg
    cfg.dispatch = "full"
    for k, v in kw.items():
        setattr(cfg, k, v)
    if gitops.git(cfg.repo, "remote"):
        sh("git", "-C", cfg.repo, "remote", "remove", "origin")
    return cfg


def go(cfg, engine=None, now=NOON, **kw):
    applier = NoopApplier(cfg)
    if "clock" not in kw:   # never the real clock in a test
        kw["clock"] = (c := Clock())
        kw["sleep"] = c.sleep
    land = LedgerLanding(cfg, applier, **kw)
    out = Runner(cfg, engine=engine or FakeEngine(fix_calc), landing=land, now=lambda: now).run_once()
    return out, land


def ledger(cfg):
    return Ledger(str(Path(cfg.state_dir) / "ledger.jsonl")).entries()


def main(cfg):
    return gitops.rev(cfg.repo, "main")


def advance_main(cfg, files: dict) -> str:
    """Someone else lands on the integration branch (a product update, another heal)."""
    wt = str(Path(cfg.worktree_root) / "advance")
    gitops.add_worktree(cfg.repo, wt, base="main")
    try:
        for rel, text in files.items():
            (Path(wt) / rel).write_text(text)
        gitops.git(wt, "add", "-A")
        sha = gitops.commit(wt, "another landing")
        gitops.git(cfg.repo, "update-ref", "refs/heads/main", sha)
        return sha
    finally:
        gitops.remove_worktree(cfg.repo, wt)


def fix_while(cfg, files):
    def edit(wt):
        fix_calc(wt)
        advance_main(cfg, files)
    return FakeEngine(edit)


# --- the ledger ---------------------------------------------------------------------------

def test_ledger_is_a_hash_chain_and_refuses_to_extend_a_broken_one(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    for i in range(3):
        led.append("landed", "2026-11-14T12:00:00", ticket=f"t{i}", to="a" * 40, proof="b" * 64, reason="")
    assert led.verify() == (True, 3) and [e["seq"] for e in led.entries()] == [1, 2, 3]
    lines = led.path.read_text().splitlines()
    for tampered in (lines[:1] + [lines[1].replace("t1", "tX")] + lines[2:],   # edited
                     [lines[0], lines[2]],                                     # one removed
                     [lines[1], lines[0], lines[2]]):                          # reordered
        led.path.write_text("\n".join(tampered) + "\n")
        assert led.verify() == (False, 0)
        with pytest.raises(LedgerBroken):
            led.append("landed", "ts", ticket="t9")
    led.path.write_text("\n".join(lines[:2]) + "\n")   # stated limit: a cut TAIL still verifies
    assert led.verify() == (True, 2)


def test_ledger_takes_tokens_only_and_known_keys_only(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    e = led.append("refused", "ts", ticket="t", reason="assert add(2, 3) == 5 failed for the household")
    assert e["reason"] == "<redacted>"
    for bad in (dict(event="merged"), dict(event="landed", stdout="x")):
        with pytest.raises(ValueError):
            led.append(bad.pop("event"), "ts", **bad)
    assert proof_hash(a=1, after=[["g", "pass", "d"]]) != proof_hash(a=1, after=[["g", "fail", "d"]])


# --- the landing --------------------------------------------------------------------------

@needs_bwrap
def test_sold_unit_lands_proves_and_records_with_no_remote_and_no_network(local_scratch):
    cfg = sold(local_scratch)
    gitops.CMD_LOG.clear()
    out, land = go(cfg)
    assert out["outcome"] == "healed" and out["verify"] == "proof:supervised_pytest=pass,offline_lane=pass", out
    assert gitops.git(cfg.repo, "show", "main:calc.py") + "\n" == FIXED
    (e,) = ledger(cfg)
    assert (e["event"], e["shape"], e["merge"], e["reason"], e["to"]) == ("landed", "sold_unit", "ff", "", main(cfg))
    assert len(e["proof"]) == 64 and e["files"] == ["calc.py"] and e["test"] == "tests/test_calc.py::test_add"
    assert land.would_run == [] and land.applier.calls == [("apply", main(cfg))]
    assert not [c for c in gitops.CMD_LOG if c[0] == "gh" or NETWORK & set(c)]
    assert state(cfg)["tickets"][out["ticket"]].get("landing_intent") is None


@needs_bwrap
def test_sold_unit_refuses_a_repo_that_has_a_remote(local_scratch):
    cfg = local_scratch.cfg          # the fixture's bare clone still has `origin`
    cfg.dispatch = "full"
    before = main(cfg)
    out, _ = go(cfg, shape=SoldUnit())
    assert (out["outcome"], out["why"]) == ("failed", "remote_present") and main(cfg) == before
    assert [(e["event"], e["reason"]) for e in ledger(cfg)] == [("refused", "remote_present")]


@needs_bwrap
def test_this_unit_lands_locally_and_only_prints_the_pr_commands(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    gitops.CMD_LOG.clear()
    out, land = go(cfg, shape=ThisUnit())
    assert out["outcome"] == "healed" and ledger(cfg)[0]["shape"] == "this_unit"
    push, pr = land.would_run
    assert push[:3] == ["git", "push", "origin"] and push[3].startswith(main(cfg) + ":refs/heads/self-heal/")
    assert pr[:4] == ["gh", "pr", "create", "--draft"] and "--admin" not in pr and "--force" not in push
    assert not [c for c in gitops.CMD_LOG if c[0] == "gh" or NETWORK & set(c)]   # printed, never run


@needs_bwrap
def test_a_moved_main_is_merged_and_the_merged_tree_is_what_gets_proven(local_scratch):
    cfg = sold(local_scratch)
    out, _ = go(cfg, fix_while(cfg, {"NOTES.txt": "unrelated\n"}))
    assert out["outcome"] == "healed", out
    (e,) = ledger(cfg)
    assert e["merge"] == "merge" and e["to"] == main(cfg) and e["heal"] != e["to"]
    assert len(gitops.git(cfg.repo, "rev-list", "--parents", "-n", "1", "main").split()) == 3
    assert gitops.git(cfg.repo, "show", "main:NOTES.txt") == "unrelated"


@needs_bwrap
def test_a_correct_fix_that_breaks_an_unrelated_test_after_merge_is_rolled_back(local_scratch, monkeypatch):
    cfg = sold(local_scratch)
    eng = fix_while(cfg, {"tests/test_legacy.py": LEGACY})   # lands on main while the engine works
    out, land = go(cfg, eng)
    tip = main(cfg)
    assert (out["outcome"], out["why"]) == ("rolled_back", "proof_failed:offline_lane"), out
    assert gitops.git(cfg.repo, "show", "main:calc.py") + "\n" != FIXED and "test_legacy" in gitops.git(cfg.repo, "show", "main:tests/test_legacy.py")
    (e,) = ledger(cfg)
    assert (e["event"], e["reason"], e["restored"], e["merge"], e["from"]) == ("rolled_back", "proof_failed", True, "merge", tip)
    assert ("apply", e["to"]) not in land.applier.calls      # nothing was applied before the proof
    assert state(cfg)["faults"][e["fault_key"]]["parked"] is True


@needs_bwrap
def test_negative_control_without_the_post_merge_proof_the_broken_merge_stays(local_scratch, monkeypatch):
    cfg = sold(local_scratch)
    monkeypatch.setattr(LedgerLanding, "_prove", lambda self, ctx, intent: (True, "proof:skipped"))
    out, _ = go(cfg, fix_while(cfg, {"tests/test_legacy.py": LEGACY}))
    assert out["outcome"] == "healed" and gitops.git(cfg.repo, "show", "main:calc.py") + "\n" == FIXED   # main is red


@needs_bwrap
def test_a_textual_conflict_refuses_and_main_does_not_move(local_scratch):
    cfg = sold(local_scratch)
    out, _ = go(cfg, fix_while(cfg, {"calc.py": "def add(a, b):\n    return b - a\n"}))
    assert (out["outcome"], out["why"]) == ("failed", "merge_conflict"), out
    assert "b - a" in gitops.git(cfg.repo, "show", "main:calc.py")
    assert [(e["event"], e["reason"]) for e in ledger(cfg)] == [("refused", "merge_conflict")]


@needs_bwrap
@pytest.mark.parametrize("readings,outcome,why", [((True, True, False), "rolled_back", "health_red"),
                                                  ((True,), "healed", "proof:supervised_pytest=pass,offline_lane=pass")])
def test_health_window(local_scratch, readings, outcome, why):
    cfg = sold(local_scratch, health_window_s=30, health_interval_s=10)
    before, clock, probe = main(cfg), Clock(), FakeProbe(*readings)
    out, land = go(cfg, probe=probe, clock=clock, sleep=clock.sleep)
    assert (out["outcome"], out["verify"]) == (outcome, why), out
    (e,) = ledger(cfg)
    if outcome == "healed":
        assert probe.calls == 4 and clock.t == 30 and e["event"] == "landed" and main(cfg) != before
    else:
        assert probe.calls == 3 and main(cfg) == before
        assert (e["event"], e["reason"], e["restored"]) == ("rolled_back", "health_red", True)
        assert [c[0] for c in land.applier.calls] == ["apply", "rollback"]


# --- killed mid-landing: the next start finishes or rolls back ------------------------------

@needs_bwrap
@pytest.mark.parametrize("phase,kept", [("intent", False), ("advanced", False), ("proven", False),
                                        ("watching", True), ("ledgered", True)])
def test_a_kill_mid_landing_is_finished_or_rolled_back_by_the_next_start(local_scratch, phase, kept):
    cfg = sold(local_scratch, health_window_s=10)
    before = main(cfg)

    def die():
        raise HardKill()
    with pytest.raises(HardKill):
        go(cfg, hooks={phase: die}, probe=FakeProbe(True))
    (rec,) = state(cfg)["tickets"].values()
    intent = rec["landing_intent"]
    assert intent and (main(cfg) == intent["to"]) == (phase != "intent")   # advanced, intent on disk, nobody finished
    out, _ = go(cfg, FakeEngine(None), probe=FakeProbe(True))
    (rec,) = [r for r in state(cfg)["tickets"].values() if r["ticket"]["id"] == rec["ticket"]["id"]]
    assert rec["landing_intent"] is None and rec["status"] == ("healed" if kept else "rolled_back")
    assert main(cfg) == (intent["to"] if kept else before)
    events = [(e["event"], e.get("recovered", False)) for e in ledger(cfg)]
    assert events == ([("landed", phase == "watching")] if kept else [("rolled_back", True)]), events
    assert out["outcome"] == ("no_fault" if kept else "all_capped"), out


@needs_bwrap
def test_a_watching_landing_whose_probe_is_red_on_restart_is_rolled_back(local_scratch):
    cfg = sold(local_scratch, health_window_s=10)
    before = main(cfg)

    def die():
        raise HardKill()
    with pytest.raises(HardKill):
        go(cfg, hooks={"watching": die}, probe=FakeProbe(True))
    go(cfg, FakeEngine(None), probe=FakeProbe(False))
    assert main(cfg) == before and [(e["event"], e["reason"]) for e in ledger(cfg)] == [("rolled_back", "interrupted")]


@needs_bwrap
def test_negative_control_without_recovery_main_stays_advanced_and_unproven(local_scratch, monkeypatch):
    cfg = sold(local_scratch)

    def die():
        raise HardKill()
    with pytest.raises(HardKill):
        go(cfg, hooks={"advanced": die})
    heal = main(cfg)
    monkeypatch.setattr(Runner, "_recover", lambda self, st: None)
    go(cfg, FakeEngine(None))
    assert main(cfg) == heal and ledger(cfg) == []   # the hazard the recovery exists for


# --- two loops, one repo --------------------------------------------------------------------

def _second(scratch, tmp_path):
    return make_cfg(tmp_path / "second", repo=scratch.repo, live=scratch.live, dispatch="full")


@needs_bwrap
@pytest.mark.parametrize("locked", [True, False])
def test_two_loops_on_one_repo_the_one_mid_landing_wins_the_other_refuses(local_scratch, tmp_path, locked):
    cfg, other = sold(local_scratch), _second(local_scratch, tmp_path)
    before = main(cfg)
    winner = LedgerLanding(other)   # the other loop (own state dir), mid-landing on the SAME repo
    with (winner._repo_lock() if locked else contextlib.nullcontext()):
        out, _ = go(cfg)
    if locked:
        assert (out["outcome"], out["why"]) == ("failed", "landing_busy") and main(cfg) == before
        assert [(e["event"], e["reason"]) for e in ledger(cfg)] == [("refused", "landing_busy")]
    else:   # negative control: with nobody holding the repo, the same run lands
        assert out["outcome"] == "healed" and main(cfg) != before


@needs_bwrap
def test_a_duplicate_heal_that_finishes_after_the_other_landed_does_not_land(local_scratch, tmp_path):
    cfg, other, seen = sold(local_scratch), _second(local_scratch, tmp_path), {}

    def edit(wt):   # while this loop's engine works, the other loop detects, fixes and lands the same fault
        fix_calc(wt)
        seen.update(first=go(other, now=NOON + dt.timedelta(minutes=1))[0])   # its own ticket id
    out, _ = go(cfg, FakeEngine(edit))
    won = ledger(other)[0]["to"]
    assert seen["first"]["outcome"] == "healed" and main(cfg) == won
    assert (out["outcome"], out["why"]) == ("rolled_back", "proof_failed:supervised_pytest"), out
    assert [(e["event"], e["reason"], e["restored"]) for e in ledger(cfg)] == [("rolled_back", "proof_failed", True)]


@needs_bwrap
def test_a_main_that_moves_under_the_landing_loses_the_compare_and_swap(local_scratch):
    cfg, moved = sold(local_scratch), {}
    out, _ = go(cfg, hooks={"intent": lambda: moved.update(sha=advance_main(cfg, {"NOTES.txt": "x\n"}))})
    assert (out["outcome"], out["why"]) == ("failed", "lost_race") and main(cfg) == moved["sha"]
    assert state(cfg)["tickets"][out["ticket"]].get("landing_intent") is None
    assert [(e["event"], e["reason"]) for e in ledger(cfg)] == [("refused", "lost_race")]


def test_reasons_are_a_closed_list_of_tokens():
    assert all(r.replace("_", "").isalpha() for r in REASONS) and len(set(REASONS)) == len(REASONS)
    assert json.dumps(sorted(REASONS))   # serialisable as-is
