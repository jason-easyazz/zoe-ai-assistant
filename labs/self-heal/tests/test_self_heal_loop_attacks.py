"""Attacks and failures against the complete loop. Each hostile answer is run twice on the same scratch
repo: as built (the patch check refuses it), and with the patch check switched OFF (the negative
control for that layer), where the NEXT layer (git apply, the diff policy, the supervised gate) must
still refuse it. Nothing ever lands. Fake engine, scratch repos."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canned import CANNED
from conftest import PLANTED_SENTENCE, PLANTED_TOKEN, make_cfg, seed_repo, sh
from selfheal import proposer
from selfheal.locallander import HardKill
from selfheal.loop import HealLoop
from selfheal.proposer import ScriptedProposer
from test_self_heal import NOON, state
from test_self_heal_locallanding import ledger, main, sold
from test_self_heal_loop import fault, loop  # noqa: F401 (fixture)
from test_self_heal_propose import EMAIL, LEAK_MSG, LEAKY_TEST
from test_self_heal_sandbox import needs_bwrap

pytestmark = needs_bwrap
ATTACKS = {
    # the engine's answer: (refused by the patch check as, refused by the next layer as)
    "edits_test": ("patch_outside_allowlist", "gate_failed"),        # the base copy of the test runs, not the edit
    "pinned": ("patch_outside_allowlist", "forbidden_path"),         # conftest.py
    "workflow": ("patch_outside_allowlist", "forbidden_path"),
    "symlink": ("patch_symlink", "outside_repo"),                    # a link out of the tree
    "rename": ("patch_rename", "test_deleted"),                      # a rename is a delete + an add
    "scratch": ("patch_path_escape", "scratch_planted"),             # code planted in the gates' scratch dir
    "delete": ("patch_delete", "gate_failed"),
    "too_many_lines": ("diff_cap", "diff_cap"),
    "breaks_other": (None, "gate_failed"),                           # in bounds, fixes the test, breaks another
}


def _unchecked(patch, packet, cfg):
    return []   # no check at all: whatever the engine sent goes to `git apply`


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_attack_is_refused_by_the_patch_check_and_by_the_layer_behind_it(local_scratch, fault, monkeypatch, name):  # noqa: F811
    cfg = sold(local_scratch, daily_cap=3)
    before, (first, second) = main(cfg), ATTACKS[name]
    if first:
        res, _ = loop(cfg, fault, name, max_rounds=1)
        assert not res["healed"] and res["refusals_by_reason"] == {first: 1}, res
    monkeypatch.setattr(proposer, "check_patch", _unchecked)   # negative control: the first layer is gone
    res, _ = loop(cfg, fault, name, max_rounds=1)
    assert not res["healed"] and res["refusals_by_reason"] == {second: 1}, res
    assert main(cfg) == before and not [e for e in ledger(cfg) if e["event"] == "landed"]
    assert not list(Path(cfg.worktree_root).glob("heal-*"))


@pytest.mark.parametrize("name,reason", [("over_tokens", "budget_tokens"), ("over_turns", "budget_turns"),
                                         ("slow", "budget_time"), ("oversized", "patch_oversized")])
def test_over_budget_answers_are_refused_and_the_round_cap_ends_it(local_scratch, fault, name, reason):  # noqa: F811
    cfg = sold(local_scratch, engine_timeout_s=1 if name == "slow" else 30)
    before = main(cfg)
    res, fake = loop(cfg, fault, name)
    assert not res["healed"] and res["refusals_by_reason"] == {reason: 2} and res["attempts"] == 2
    assert main(cfg) == before and ledger(cfg) == [] and len(fake.packets) == 2


def test_a_kill_mid_landing_then_the_next_loop_start_rolls_back_before_anything_else(local_scratch, fault):  # noqa: F811
    cfg = sold(local_scratch)
    before = main(cfg)

    def die():
        raise HardKill()
    with pytest.raises(HardKill):
        HealLoop(cfg, ScriptedProposer(CANNED["good"]), now=lambda: NOON, hooks={"advanced": die}).run(fault)
    assert main(cfg) != before                                   # advanced, unproven, the process is gone
    res, fake = loop(cfg, fault, "good")
    assert main(cfg) == before and fake.packets == []            # rolled back first; the parked fault gets no engine
    assert res["refusals_by_reason"] == {"all_capped": 1} and res["ledger"] == {"chain_ok": True, "entries": 1}
    assert [(e["event"], e["reason"], e["recovered"]) for e in ledger(cfg)] == [("rolled_back", "interrupted", True)]
    assert all(r.get("landing_intent") is None for r in state(cfg)["tickets"].values())


def test_no_planted_sentinel_reaches_the_result_the_ledger_or_any_state_file(tmp_path, fault):  # noqa: F811
    live, repo = tmp_path / "live", tmp_path / "repo.git"
    private = tmp_path / "private" / "household.txt"   # readable on the host, hidden from every sandboxed run
    private.parent.mkdir()
    private.write_text(PLANTED_SENTENCE)
    seed_repo(live, LEAKY_TEST.format(state=str(private)[::-1], msg=LEAK_MSG[::-1])
              .replace("import settings\n", "").replace("from app_secret import VALUE\n", ""))
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    sh("git", "-C", str(repo), "remote", "remove", "origin")
    cfg = make_cfg(tmp_path, repo=repo, live=live, dispatch="full")
    chatty = lambda p: CANNED["good"](p)   # noqa: E731
    res, fake = loop(cfg, fault, "garbage", chatty)
    assert res["healed"] and res["attempts"] == 2, res
    assert PLANTED_TOKEN not in fake.packets[0].to_json() and "<redacted:token>" in fake.packets[0].failure
    blob = json.dumps(res) + "".join(p.read_text(errors="replace") for p in Path(cfg.state_dir).rglob("*") if p.is_file())
    for sentinel in (PLANTED_TOKEN, PLANTED_SENTENCE, EMAIL, "hunter2", "AssertionError"):
        assert sentinel not in blob, sentinel
