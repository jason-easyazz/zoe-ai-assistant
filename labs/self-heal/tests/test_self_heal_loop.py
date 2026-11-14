"""The loop driver (`selfheal/loop.py`): fault fixture -> packet -> fake engine -> diff policy -> supervised
gates -> local landing -> proof -> ledger; bounded rounds, a closed reason list, the measures."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from canned import CANNED, FAULT_JUNIT
from conftest import FIXED
from selfheal import gitops
from selfheal.config import MAX_PER_FAULT_CAP
from selfheal.locallander import ThisUnit
from selfheal.loop import REASONS, RETRY, HealLoop, classify
from selfheal.proposer import DryRunProposer, Proposal, ScriptedProposer
from test_self_heal import NOON
from test_self_heal_locallanding import LEGACY, advance_main, ledger, main, sold
from test_self_heal_sandbox import needs_bwrap


@pytest.fixture
def fault(tmp_path):
    p = tmp_path / "fault.xml"
    p.write_text(FAULT_JUNIT)
    return str(p)


def loop(cfg, fault, *script, **kw):
    fake = ScriptedProposer(*[CANNED[s] if isinstance(s, str) else s for s in script])
    return HealLoop(cfg, fake, now=lambda: NOON, **kw).run(fault), fake


@needs_bwrap
def test_the_complete_loop_heals_and_reports_the_measures(local_scratch, fault):
    cfg = sold(local_scratch)
    res, fake = loop(cfg, fault, "reported_ok")
    assert res["healed"] and res["attempts"] == 1 and res["rollbacks"] == 0 and res["refusals_by_reason"] == {}
    assert res["fault_to_verified_fix_s"] > 0 and res["ledger"] == {"chain_ok": True, "entries": 1}
    assert len(res["proof"]) == 64 and res["proof"] == ledger(cfg)[0]["proof"] and res["shape"] == "sold_unit"
    assert res["would_run"] == [] and res["rounds"][0]["tokens"] == 1200 and res["rounds"][0]["turns"] == 3
    assert gitops.git(cfg.repo, "show", "main:calc.py") + "\n" == FIXED
    json.dumps(res)   # the lab result is plain JSON


@needs_bwrap
@pytest.mark.parametrize("first,reason,gate", [("garbage", "patch_malformed", ""), ("bad", "gate_failed", "supervised_pytest")])
def test_a_refused_round_is_retried_with_the_reason_token_and_then_heals(local_scratch, fault, first, reason, gate):
    cfg = sold(local_scratch)
    res, fake = loop(cfg, fault, first, "good")
    assert res["healed"] and res["attempts"] == 2 and res["refusals_by_reason"] == {reason: 1}
    assert (res["rounds"][0]["reason"], res["rounds"][0]["gate"]) == (reason, gate)
    assert [(p.attempt, p.last_refusal) for p in fake.packets] == [(1, ""), (2, reason)]
    assert [e["event"] for e in ledger(cfg)] == ["landed"]


@needs_bwrap
def test_the_round_cap_is_deterministic_and_cannot_be_raised(local_scratch, fault):
    cfg = sold(local_scratch)
    res, fake = loop(cfg, fault, "garbage", max_rounds=99)
    assert res["max_rounds"] == MAX_PER_FAULT_CAP == 2 and res["attempts"] == 2 and not res["healed"]
    assert res["fault_to_verified_fix_s"] is None and res["refusals_by_reason"] == {"patch_malformed": 2}
    again, fake2 = loop(cfg, fault, "good")          # the per-fault cap of the hard floor outlives the loop object
    assert again["refusals_by_reason"] == {"all_capped": 1} and fake2.packets == [] and ledger(cfg) == []


@needs_bwrap
def test_an_out_of_bounds_patch_is_never_retried_whatever_the_engine_says(local_scratch, fault):
    cfg = sold(local_scratch)
    before = main(cfg)
    pleading = lambda p: Proposal(CANNED["edits_test"](p) + "\n")   # noqa: E731
    pleading.severity, pleading.retry = "low", True                  # nothing reads these
    res, fake = loop(cfg, fault, pleading, "good")
    assert not res["healed"] and res["attempts"] == 1 and len(fake.packets) == 1
    assert res["refusals_by_reason"] == {"patch_outside_allowlist": 1} and main(cfg) == before


@needs_bwrap
def test_a_rollback_is_counted_and_ends_the_loop(local_scratch, fault):
    cfg = sold(local_scratch)
    moving = lambda p: (advance_main(cfg, {"tests/test_legacy.py": LEGACY}), CANNED["good"](p))[1]   # noqa: E731
    res, fake = loop(cfg, fault, moving, "good")
    assert not res["healed"] and res["rollbacks"] == 1 and res["attempts"] == 1
    assert res["refusals_by_reason"] == {"proof_failed": 1} and res["rounds"][0]["outcome"] == "rolled_back"
    assert [(e["event"], e["reason"]) for e in ledger(cfg)] == [("rolled_back", "proof_failed")]


@needs_bwrap
def test_this_unit_result_carries_the_printed_pr_commands(local_scratch, fault):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    res, _ = loop(cfg, fault, "good", shape=ThisUnit())
    assert res["healed"] and res["shape"] == "this_unit"
    assert [c[:2] for c in res["would_run"]] == [["git", "push"], ["gh", "pr"]]


@needs_bwrap
def test_a_dry_run_engine_goes_through_the_loop_and_lands_nothing(local_scratch, fault):
    cfg = sold(local_scratch)
    before = main(cfg)
    res = HealLoop(cfg, DryRunProposer(cfg, "codex"), now=lambda: NOON).run(fault)
    assert not res["healed"] and res["refusals_by_reason"] == {"engine_dry_run": 1} and res["attempts"] == 1
    assert res["rounds"][0]["would_run"][2]["step"] == "engine" and main(cfg) == before and ledger(cfg) == []


@pytest.mark.parametrize("out,reason", [
    ({"outcome": "healed"}, ""), ({"outcome": "refused", "rule": "kill_switch"}, "kill_switch"),
    ({"outcome": "discarded", "why": "forbidden_path"}, "forbidden_path"),
    ({"outcome": "rolled_back", "why": "proof_failed:offline_lane"}, "proof_failed"),
    ({"outcome": "rolled_back", "why": "exception:KeyError,rollback_apply_failed:OSError"}, "exception"),
    ({"outcome": "failed", "why": "landing_busy"}, "landing_busy"), ({"outcome": "no_engine", "why": "no_engine_linked"}, "no_engine"),
    ({"outcome": "interrupted", "why": "quiet_hours"}, "quiet_hours"), ({"outcome": "error", "error": "KeyError"}, "error"),
    ({"outcome": "discarded", "why": "the engine said: critical, retry me"}, "unclassified"), ({}, "unclassified")])
def test_every_non_heal_maps_to_one_reason_from_the_closed_list(out, reason):
    assert classify(out) == reason and (reason == "" or reason in REASONS)


def test_the_retry_table_is_fixed_and_holds_no_out_of_bounds_or_landing_reason():
    assert len(set(REASONS)) == len(REASONS) and all(r.replace("_", "").isalpha() for r in REASONS)
    never = {"patch_outside_allowlist", "patch_path_escape", "patch_symlink", "patch_rename", "patch_delete", "patch_mode",
             "patch_binary", "forbidden_path", "scratch_planted", "report_planted", "test_deleted", "test_weakened",
             "process_control", "outside_repo", "engine_committed", "engine_staged", "proof_failed", "health_red",
             "lost_race", "landing_busy", "kill_switch", "rollback_stuck", "unclassified", "engine_dry_run"}
    assert never <= set(REASONS) and not (never & RETRY)


@needs_bwrap
def test_loop_scratch_prints_the_lab_result_json(capsys):
    import loop_scratch
    assert loop_scratch.main(["--scenario", "retry"], now=lambda: NOON) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["healed"] and res["attempts"] == 2 and res["refusals_by_reason"] == {"patch_malformed": 1}
    assert res["ledger_events"] == [["landed", ""]] and res["scratch"] == "(removed)" and res["fault_to_verified_fix_s"] > 0
    assert not list(Path(tempfile.gettempdir()).glob("self-heal-loop-*/repo.git"))
