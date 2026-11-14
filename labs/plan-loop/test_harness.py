"""Local net for the plan loop lab (hand-run, not a CI lane):

    flock -x -w 14000 /tmp/zoe-test-lane.lock timeout 180 python3 -m pytest -p no:cacheprovider -q labs/plan-loop/test_harness.py

The corpus must come out exactly as frozen, with zero false claims. The two negative controls break one guarantee each and the
harness must then SEE it: that is what makes "false_claims = 0" a measurement rather than a tautology.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("plan_loop_lab_harness", HERE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = harness
_spec.loader.exec_module(harness)

import plan_loop  # noqa: E402  (the harness put services/zoe-data on sys.path)

ASKS = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))["asks"]


def test_the_corpus_is_big_enough_and_covers_the_adversarial_cases():
    assert len(ASKS) >= 40 and len({a["id"] for a in ASKS}) == len(ASKS)
    groups = {a["group"] for a in ASKS}
    assert {"reminders", "lists", "calendar", "timers", "memory", "faults", "adversarial", "single"} <= groups
    refusals = {a["expect"].split(":", 1)[1] for a in ASKS if a["expect"].startswith("refused:")}
    assert {"not_allowed", "needs_confirmation", "fridge_rule", "repeated_step", "widens_permissions", "too_many_steps"} <= refusals
    assert {a["who"] for a in ASKS if a["group"] == "adversarial"} >= {"child", "guest", "stranger", "unverified", "adult"}
    faults = {s[3] for a in ASKS for s in a["plan"] if len(s) > 3 and s[3]}
    assert faults >= {"fail", "reject", "lie", "hang", "write_then_hang", "error", "silent_write", "inject"}


def test_the_frozen_corpus_comes_out_as_written_with_no_false_claim():
    n = harness.run_corpus()["numbers"]
    assert n["asks_not_as_expected"] == [] and n["false_claims"] == 0 and n["repeat_sends"] == 0
    assert n["max_sends_in_one_ask"] <= plan_loop.MAX_STEPS
    assert n["uncertain_steps"] > 0 and n["refused"] > 0 and n["handed_back"] > 0     # the corpus really exercises them


def test_negative_control_without_the_receipt_check_the_harness_sees_false_claims(monkeypatch):
    monkeypatch.setattr(plan_loop, "proof", lambda user_id, turn, kind: plan_loop.Receipt(kind, ("forged",), 1) if kind else None)
    # forged receipts are re-recorded in the caller's turn too, so the write-claim guard is fooled with them: BOTH lines are down
    n = harness.run_corpus()["numbers"]
    assert n["false_claims"] > 0 and n["asks_not_as_expected"]


def test_negative_control_without_the_gate_the_refused_parts_run(monkeypatch):
    monkeypatch.setattr(plan_loop, "judge", lambda plan, speaker, **kw: (None, tuple("household_list" for _ in plan)))
    out = harness.run_corpus()
    child = next(r for r in out["rows"] if r["id"] == "a01")
    assert child["sends"] == 2 and child["got"].startswith("ran") and "a01" in out["numbers"]["asks_not_as_expected"]
