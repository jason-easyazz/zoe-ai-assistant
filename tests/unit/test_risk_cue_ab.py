"""risk_cue_ab.py - the integrity of the risk-cue A/B: what its verdict may rest on, and that importing it leaves the manner A/B alone.

Each finding has a negative control (revert the guard => the test goes red):

* importing the module (or running its entry points) NEVER leaves manner_block_ab's globals changed - the two harnesses can share a pytest process;
* the cue arm's fingerprint is the cue DATA: edit a cue and old rows are refused (a new run id), exactly as for a manner text;
* the wire audit: a block on the `none` arm is an instrument failure; a block on a turn the detector does not fire on is an instrument failure; a
  detected turn the block did not ride is RECORDED (it is the delivery gate's input), never hidden;
* the verdict needs the whole plan in both arms, the P5b.b lift, an intact good-plan half, no regression, and the three delivery gates;
* nothing the offline selftest does reads the held-out flaw or good-plan messages through the detector.

No brain, no network, no service, no database, no flag. UNMARKED (Jetson full-directory lane): it imports services/zoe-data (risk_cues,
the Flue client), which the slim GitHub lane does not carry.
"""
from __future__ import annotations

import inspect
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"
if str(PERF) not in sys.path:
    sys.path.insert(0, str(PERF))

import manner_block_ab as mba  # noqa: E402

GLOBALS = ("CACHE", "LEDGER", "TOTAL_BUDGET_S", "HARNESS_VERSION", "PRIMARY_SAMPLES", "variant_text", "build_plan")
BEFORE = {n: getattr(mba, n) for n in GLOBALS}

import risk_cue_ab as rca  # noqa: E402

rca.zoe_path()
import risk_cues  # noqa: E402


def test_importing_the_harness_leaves_the_manner_ab_untouched():
    assert {n: getattr(mba, n) for n in GLOBALS} == BEFORE


def test_the_entry_points_put_the_manner_globals_back(tmp_path, monkeypatch):
    monkeypatch.setattr(rca, "CACHE", tmp_path)
    with rca.rig_globals(3):
        assert mba.CACHE == tmp_path and mba.HARNESS_VERSION == rca.HARNESS_VERSION and mba.PRIMARY_SAMPLES["heldout"]["flaw"] == 3
        assert mba.variant_text is rca.variant_text and mba.build_plan is rca.build_plan
    assert {n: getattr(mba, n) for n in GLOBALS} == BEFORE
    assert rca.selftest() == 0
    assert {n: getattr(mba, n) for n in GLOBALS} == BEFORE
    rca.report("no-such-run")
    assert {n: getattr(mba, n) for n in GLOBALS} == BEFORE


def test_the_selftest_never_runs_the_detector_over_the_heldout_half():
    src = inspect.getsource(rca._selftest)
    assert 'pool["dev"]' in src and 'pool["heldout"]' not in src and "FLAWS_HELD" not in src and "GOODPLANS_HELD" not in src


def test_the_fingerprint_is_the_cue_data(monkeypatch):
    with rca.rig_globals():
        a = mba.fingerprint(rca.ARM, "split")
        assert a != mba.fingerprint("none", "split")
        monkeypatch.setattr(risk_cues, "data_digest", lambda: "another cue set")
        assert mba.fingerprint(rca.ARM, "split") != a


# -- the wire audit ------------------------------------------------------------------------------------------------------------
def _runner(wire: str, text: str):
    r = object.__new__(rca.RiskRunner)
    r.rc, r.fires, r.phase, r.cur, r.last_text = risk_cues, [], "heldout", ("p5b-flawH-0", 0), text
    r.brain = types.SimpleNamespace(last_wire=wire)
    return r


PLAN = "I'm driving to my sister's tonight, three hours, and I'm exhausted."
HEAD = risk_cues.heading("en")


def test_a_block_on_the_none_arm_is_an_instrument_failure():
    with pytest.raises(RuntimeError, match="instrument"):
        _runner(f"x\n{HEAD}\n- y", PLAN).check_wire("none")
    with pytest.raises(RuntimeError, match="instrument"):
        _runner(" zoe-manner:\"x\"\nhi", PLAN).check_wire("none")
    ok = _runner("just the words", PLAN)
    ok.check_wire("none")
    assert ok.fires == [{"arm": "none", "phase": "heldout", "ask": "p5b-flawH-0", "sample": 0, "detected": False, "delivered": False}]


def test_a_block_on_a_turn_the_detector_does_not_fire_on_is_an_instrument_failure():
    with pytest.raises(RuntimeError, match="instrument"):
        _runner(f"x\n{HEAD}\n- y", "I'm going to paint the fence on Saturday.").check_wire(rca.ARM)


def test_a_detected_turn_without_the_block_is_recorded_not_hidden():
    r = _runner("the words only (another block took the turn)", PLAN)
    r.check_wire(rca.ARM)
    assert r.fires[0]["detected"] is True and r.fires[0]["delivered"] is False
    r2 = _runner(f"x\n{HEAD}\n- y", PLAN)
    r2.check_wire(rca.ARM)
    assert r2.fires[0]["detected"] and r2.fires[0]["delivered"]


# -- the verdict, end to end on synthetic rows ---------------------------------------------------------------------------------
@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(rca, "CACHE", tmp_path)
    return "rca-test"


def _write(run_id, lift=True, flaw_delivered=1.0, good_delivered=0.0, drop_good=False, skip_regress_arm=None):
    """A complete synthetic run: every planned ask a row in both arms, scored by a rule (the cue arm lifts P5b.b), plus the fires file."""
    with rca.rig_globals():
        world = rca.sp.World(rca.sp.BASE_SEED)
        digest = mba.split_digest(world)
        fps = {"none": mba.fingerprint("none", digest), rca.ARM: mba.fingerprint(rca.ARM, digest)}
        rows, fires, seen = [], [], {}
        for phase in ("heldout", "regress"):
            plan = rca.build_plan(phase, world, 1.0)
            pdig = mba.plan_digest(plan)
            for arm in ("none", rca.ARM):
                if skip_regress_arm == arm and phase == "regress":
                    continue
                for n, (item, s) in enumerate(plan):
                    aid = item[2] if isinstance(item, tuple) else item.id
                    scored = {}
                    for h in mba.required_scores(item):
                        v = True
                        if h == "P5b.b":
                            v = (n % 4 != 0) if (arm == rca.ARM and lift) else (n % 4 == 0)
                        if h == "P5b.u" and drop_good and arm == rca.ARM:
                            v = n % 3 == 0
                        scored[h] = [v, {}]
                    rows.append({"arm": arm, "phase": phase, "ask": aid, "sample": s, "inv": "i", "fp": fps[arm], "plan": pdig, "scale": 1.0,
                                 "replies": [], "scored": scored})
                    if phase == "heldout":
                        kind = rca._kind_of(aid)
                        share = flaw_delivered if kind == "flaw" else good_delivered
                        seen[(arm, kind)] = seen.get((arm, kind), 0) + 1
                        hit = arm == rca.ARM and (seen[(arm, kind)] % 10) < share * 10
                        fires.append({"arm": arm, "phase": phase, "ask": aid, "sample": s, "detected": hit, "delivered": hit})
    rca.CACHE.mkdir(parents=True, exist_ok=True)
    (rca.CACHE / f"{run_id}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    rca.write_fires(run_id, fires)


def _verdict(run_id):
    return rca.report(run_id)[1]


def test_a_complete_run_with_a_lift_and_clean_delivery_is_keep(run):
    _write(run)
    assert "VERDICT (held-out P5b halves + delivery + regression): KEEP" in _verdict(run), rca.report(run)[0]


def _rewrite_fires(run_id, keep):
    p = rca.fires_path(run_id)
    lines = [ln for ln in p.read_text().splitlines() if ln.strip() and keep(json.loads(ln))]
    p.write_text("\n".join(lines) + "\n")


def test_an_audit_with_no_none_arm_records_is_incomplete_not_a_pass(run):
    """P1 (#1997 review): with every none-arm record missing, 'no block rode in none' was vacuously true and the run could KEEP."""
    _write(run)
    assert "regression): KEEP" in _verdict(run)                                                  # positive control: the full audit keeps
    _rewrite_fires(run, lambda f: f["arm"] != "none")
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "INCOMPLETE" in final and "none/flaw" in final and "none/good" in final


def test_a_truncated_audit_is_incomplete(run):
    _write(run)
    seen = {"n": 0}

    def drop_last_good_in_none(f):
        if f["arm"] == "none" and "-good" in f["ask"]:
            seen["n"] += 1
            return seen["n"] < 20                       # keep all but the last none/good record
        return True

    _rewrite_fires(run, drop_last_good_in_none)
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "INCOMPLETE" in final and "none/good: audit has 19 of 20" in final
    _write(run)
    _rewrite_fires(run, lambda f: not (f["arm"] == rca.ARM and "-flaw" in f["ask"] and f["sample"] == 7))      # short on the cue arm too
    assert "INCOMPLETE" in _verdict(run)


def test_no_lift_is_do_not_keep(run):
    _write(run, lift=False)
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "P5b.b" in final


def test_a_good_plan_that_loses_asks_is_do_not_keep(run):
    _write(run, drop_good=True)
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "P5b.u" in final


def test_the_cue_not_reaching_the_flaw_asks_is_do_not_keep(run):
    _write(run, flaw_delivered=0.5)
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "delivery" in final


def test_a_cue_on_good_plans_is_do_not_keep(run):
    _write(run, good_delivered=0.6)
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "false cue" in final


def test_a_missing_arm_of_the_regression_phase_is_do_not_keep(run):
    _write(run, skip_regress_arm=rca.ARM)
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "planned asks have no row" in final


def test_rows_saved_under_other_cue_data_are_refused(run, monkeypatch):
    _write(run)
    monkeypatch.setattr(risk_cues, "data_digest", lambda: "the cues were edited after the numbers")
    final = _verdict(run)
    assert "DO NOT KEEP" in final and "different block text" in final


def test_the_report_with_no_run_is_do_not_keep(run):
    assert "DO NOT KEEP" in _verdict("never-ran")
