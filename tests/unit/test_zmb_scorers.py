"""Zoe Memory Bench: the pure parts - scorers, statistics, the seeded world, the spec format, the artifact
and baseline contract, and the arm interface.

No service module, no model, no network, no clock: every scorer has a GOOD fixture that passes and at least
three BAD fixtures that fail (the ``test_s21_*`` style of ``test_samantha_bar.py``) - a scorer that cannot
fail is not a scorer. The in-process lab driver and the negative-control refusal are exercised in
``services/zoe-data/tests/test_zmb_lab.py`` (they need the real MemoryService).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import artifact, scorers, spec, world  # noqa: E402
from zmb.arms import ARMS, make_arm  # noqa: E402
from zmb.arms.base import Arm, IngestReport, Turn, row_text_lower  # noqa: E402


# ── Wilson interval ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("k,n,lo,hi", [
    (56, 56, 0.9358, 1.0),      # the authority matrix, all green: still not "100%"
    (10, 10, 0.7225, 1.0),
    (0, 10, 0.0, 0.2775),
    (5, 10, 0.2366, 0.7634),
    (45, 50, 0.7864, 0.9565),
])
def test_wilson_matches_published_values(k, n, lo, hi):
    got = scorers.wilson(k, n)
    assert got[0] == pytest.approx(lo, abs=5e-4) and got[1] == pytest.approx(hi, abs=5e-4)


def test_wilson_no_data_is_the_widest_interval_and_bad_input_raises():
    assert scorers.wilson(0, 0) == (0.0, 1.0)
    for k, n in ((-1, 5), (6, 5)):
        with pytest.raises(ValueError):
            scorers.wilson(k, n)


def test_wilson_interval_narrows_with_n_and_two_axes_inside_each_others_interval_overlap():
    a, b = scorers.wilson(9, 10), scorers.wilson(90, 100)
    assert (a[1] - a[0]) > (b[1] - b[0])
    assert scorers.wilson(9, 10)[0] < scorers.wilson(8, 10)[1]   # 9/10 and 8/10 are NOT distinguishable


# ── text matching ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,phrase,hit", [
    ("User lives in Hobart.", "hobart", True),
    ("User's name is Devon.", "Dev", False),            # whole word, not a prefix
    ("My brother Tomás is coming", "Tomas", True),       # accent folded
    ("born on 7 August 1991", "7 August 1991", True),
    ("born on 17 August 1991", "7 August 1991", False),  # not a suffix either
    ("", "x", False), ("anything", "", False),
])
def test_contains_phrase(text, phrase, hit):
    assert scorers.contains_phrase(text, phrase) is hit


def test_fact_present_needs_every_needle_in_one_clause():
    assert scorers.fact_present("User's wife is named Tove.", ["Tove", "wife"])
    far = "Tove " + "and so on " * 10 + "wife"
    assert not scorers.fact_present(far, ["Tove", "wife"], window=5)
    assert scorers.fact_present(far, ["Tove", "wife"], window=60)
    assert not scorers.fact_present("User's wife is named Anika.", ["Tove", "wife"])
    assert not scorers.fact_present("anything", [])


# ── needles / anti-needles / canaries ─────────────────────────────────────────

def test_score_needles_good_and_three_bad():
    ok = scorers.score_needles("Your sister is Marisol, flying from Lisbon.", ["Marisol", "Lisbon"],
                               ["Lima"], ["zorbl-17"])
    assert ok.ok and ok.verdict == "PASS" and ok.stage == ""
    missing = scorers.score_needles("Your sister is Marisol.", ["Marisol", "Lisbon"])
    anti = scorers.score_needles("Marisol, flying from Lisbon or Lima.", ["Marisol"], ["Lima"])
    canary = scorers.score_needles("Marisol: zorbl-17", ["Marisol"], [], ["zorbl-17"])
    for bad in (missing, anti, canary):
        assert not bad.ok and bad.stage == "answer" and bad.verdict == "FAIL"
    assert missing.evidence["needles_found"] == 1 and anti.evidence["anti_needle_hits"] == 1
    assert canary.evidence["canary_hits"] == 1


def test_score_canaries_good_and_bad():
    assert scorers.score_canaries(["a row", "another row"], ["zorbl-17"]).ok
    for texts in (["has zorbl-17 in it"], ["x", "ZORBL-17"], ["prefix zorbl-17, suffix"]):
        bad = scorers.score_canaries(texts, ["zorbl-17"])
        assert not bad.ok and bad.stage == "write" and bad.evidence["canary_hits"] == 1
    assert scorers.score_canaries([], ["zorbl-17"]).ok       # nothing stored, nothing leaked


# ── store assertions ──────────────────────────────────────────────────────────

ROWS = [
    {"id": "1", "text": "User lives in Hobart.", "status": "approved", "authority_class": "user_stated",
     "contradicts_id": ""},
    {"id": "2", "text": "User lives in Perth.", "status": "disputed", "authority_class": "model_from_transcript",
     "contradicts_id": "1"},
    {"id": "3", "text": "User lives in Dunedin.", "status": "superseded", "authority_class": "user_stated",
     "contradicts_id": ""},
]


@pytest.mark.parametrize("assertion", [
    {"op": "present", "contains": ["Hobart"], "statuses": ["approved"]},
    {"op": "absent", "contains": ["Perth"], "statuses": ["approved"]},
    {"op": "count_eq", "contains": [], "statuses": ["approved", "disputed"], "n": 2},
    {"op": "count_at_least", "contains": ["lives"], "n": 3},
    {"op": "all_have", "contains": ["Hobart"], "field": "authority_class", "equals": "user_stated"},
    {"op": "field_set", "contains": ["Perth"], "statuses": ["disputed"], "field": "contradicts_id"},
])
def test_store_assertion_good(assertion):
    assert scorers.score_store(ROWS, [assertion]).ok


@pytest.mark.parametrize("assertion", [
    {"op": "present", "contains": ["Perth"], "statuses": ["approved"]},          # it is only disputed
    {"op": "absent", "contains": ["Dunedin"], "statuses": ["superseded"]},
    {"op": "count_eq", "contains": [], "n": 2},
    {"op": "count_at_least", "contains": ["Hobart"], "n": 2},
    {"op": "all_have", "contains": ["Perth"], "field": "authority_class", "equals": "user_stated"},
    {"op": "field_set", "contains": ["Hobart"], "statuses": ["approved"], "field": "contradicts_id"},
    {"op": "present", "contains": ["Nowhere"]},
])
def test_store_assertion_bad(assertion):
    s = scorers.score_store(ROWS, [assertion])
    assert not s.ok and s.stage == "write" and s.evidence["failed"] == [0]


def test_store_assertion_evidence_is_indexes_never_text_and_unknown_op_is_loud():
    s = scorers.score_store(ROWS, [{"op": "present", "contains": ["Hobart"]},
                                   {"op": "present", "contains": ["Nowhere"]}])
    assert s.evidence == {"assertions": 2, "failed": [1]}
    with pytest.raises(ValueError):
        scorers.score_store(ROWS, [{"op": "presnt", "contains": ["Hobart"]}])  # a typo must not PASS


def test_store_assertion_with_no_rows():
    assert scorers.score_store([], [{"op": "absent", "contains": ["x"]}]).ok
    assert not scorers.score_store([], [{"op": "present", "contains": ["x"]}]).ok
    assert not scorers.score_store([], [{"op": "all_have", "contains": [], "field": "a", "equals": 1}]).ok


# ── facts, entities, precision / recall ───────────────────────────────────────

def test_score_facts_good_and_three_bad():
    texts = ["User's wife is named Tove", "User's son is named Mika"]
    gold = [["Tove", "wife"], ["Mika", "son"]]
    assert scorers.score_facts(texts, gold, [["Tove", "husband"], ["Mika", "wife"]]).ok
    missing = scorers.score_facts(["User's wife is named Tove"], gold)
    wrong_role = scorers.score_facts(texts + ["User's husband is named Tove"], gold, [["Tove", "husband"]])
    flipped = scorers.score_facts(["User's birthday is July 8 1991"], [["7 August 1991"]], [["July 8"]])
    for bad in (missing, wrong_role, flipped):
        assert not bad.ok and bad.stage == "write"
    assert missing.evidence["gold_found"] == 1 and wrong_role.evidence["anti_hits"] == 1
    assert flipped.evidence["gold_found"] == 0 and flipped.evidence["anti_hits"] == 1


def test_score_facts_min_recall_and_empty_gold():
    texts = ["Tove is a doctor"]
    assert scorers.score_facts(texts, [["Tove", "doctor"], ["Mika", "son"]], min_recall=0.5).ok
    assert not scorers.score_facts(texts, [["Tove", "doctor"], ["Mika", "son"]], min_recall=1.0).ok
    assert scorers.score_facts([], [], [["x"]]).ok                      # nothing said, nothing wrong
    assert not scorers.score_facts(["x is here"], [], [["x"]]).ok       # an anti-fact fires on its own


def test_prf_edge_cases():
    assert scorers.prf(4, 0, 0)["f1"] == 1.0
    assert scorers.prf(0, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}
    m = scorers.prf(3, 1, 1)
    assert m["precision"] == 0.75 and m["recall"] == 0.75 and m["f1"] == 0.75
    assert scorers.prf(0, 2, 2)["f1"] == 0.0


def test_score_entities_good_and_three_bad():
    gold = ["Tove", "Mika", "Biscuit"]
    texts = ["User's wife is named Tove", "User's son is named Mika", "User's dog is named Biscuit"]
    assert scorers.score_entities(texts, gold).ok
    hallucinated = scorers.score_entities(texts + ["User's husband is named Ravi"], gold)
    missing = scorers.score_entities(texts[:1], gold)
    nothing = scorers.score_entities([], gold)
    for bad in (hallucinated, missing, nothing):
        assert not bad.ok and bad.stage == "write"
    assert hallucinated.evidence["entities"]["fp"] == 1 and hallucinated.evidence["entities"]["precision"] < 1
    assert missing.evidence["entities"]["recall"] < 0.75
    # role words, months and labels are not entities
    assert scorers.entities_in(["User's wife is named Tove in March; Person the user met"]) == {"tove"}


def test_merge_first_failure_wins_and_all_must_pass():
    good, bad = scorers.Score(True), scorers.Score(False, "read", {"x": 1})
    assert scorers.merge(good, good).ok
    m = scorers.merge(good, bad, scorers.Score(False, "write"))
    assert not m.ok and m.stage == "read"
    with pytest.raises(ValueError):
        scorers.Score(False, "nonsense")


def test_scorer_evidence_never_carries_household_text():
    w = world.make_world()
    s = w.slots
    texts = [f"User's wife is named {s['spouse']}.", f"User lives in {s['home']}."]
    scores = [scorers.score_needles(texts[0], [s["spouse"]], [s["home"]]),
              scorers.score_facts(texts, [[s["spouse"], "wife"]], [[s["intruder"]]]),
              scorers.score_entities(texts, [s["spouse"]]),
              scorers.score_store([{"text": t, "status": "approved"} for t in texts],
                                  [{"op": "present", "contains": [s["home"]]}]),
              scorers.score_canaries(texts, [s["canary"]])]
    blob = json.dumps([x.evidence for x in scores])
    assert artifact.household_strings_in(blob, w.all_strings()) == []


# ── the seeded world ──────────────────────────────────────────────────────────

def test_world_is_deterministic_per_seed_and_differs_across_seeds():
    a, b, c = world.make_world("s1"), world.make_world("s1"), world.make_world("s2")
    assert a.slots == b.slots and a.slots != c.slots
    assert world.make_world().seed == world.BASELINE_SEED


@pytest.mark.parametrize("seed", ["zmb-v1", "s1", "s2", "fresh-0001", "fresh-beef", "x", "y", "z"])
def test_world_invariants_hold_for_every_seed(seed):
    s = world.make_world(seed).slots
    people = [s[k] for k in ("owner", "spouse", "friend", "sibling", "male", "male2", "kid1", "kid2", "kid3")]
    assert len(set(people)) == len(people), "two roles share a name"
    assert len({s["pet"], s["pet_intruder"]}) == 2 and s["intruder"] not in people
    assert len({s["home"], s["home_intruder"], s["home_old"], s["lima"]}) == 4
    assert len({s["job"], s["job_intruder"]}) == 2 and s["age"] != s["age_intruder"]
    assert s["dob_d"] <= 12 and s["dob_m"] <= 12 and s["dob_d"] != s["dob_m"]  # the date reads two ways
    assert s["dob_text"].startswith(f"{s['dob_d']} ") and s["dob_flip"].endswith(f" {s['dob_m']}")
    assert s["dob_numeric"] == f"{s['dob_d']}/{s['dob_m']}/{s['dob_y']}"
    assert s["dob_text"] != s["dob_intruder"] and s["canary"].startswith("zorbl-")


def test_world_render_is_strict_and_deep():
    w = world.make_world()
    assert w.render("{owner} lives in {home}") == f"{w.slots['owner']} lives in {w.slots['home']}"
    with pytest.raises(KeyError):
        w.render("{nobody_by_that_name}")
    out = w.render_deep({"a": ["{owner}", ("{pet}",)], "n": 3})
    assert out == {"a": [w.slots["owner"], (w.slots["pet"],)], "n": 3}
    assert world.placeholders("{a} and {b_c} not {D}") == {"a", "b_c"}
    assert world.fresh_seed().startswith("fresh-") and world.fresh_seed() != world.fresh_seed()


# ── the spec format ──────────────────────────────────────────────────────────

def _cell(**kw):
    base = {"id": "A9.x", "title": "t", "tier": "store", "kind": "script",
            "probes": [{"kind": "store", "assertions": [{"op": "present", "contains": ["{owner}"]}]}]}
    base.update(kw)
    return {"spec_version": 1, "axis": "authority", "cells": [base]}


def test_shipped_specs_load_valid_and_unique():
    cells = spec.load_cells()
    ids = [c.id for c in cells]
    assert len(ids) == len(set(ids)) and len(cells) > 100
    a1 = [c for c in cells if c.id.startswith("A1.")]
    assert len(a1) == 56                                  # 8 model writers x 7 attributes
    assert {c.axis for c in cells} >= {"authority", "extraction", "identity", "forgetting", "abstention",
                                       "emotional"}
    for c in cells:
        if c.tier == "full":
            assert c.skip_reason and not c.controls       # a declared skip says why, and is never a control
        else:
            assert c.kind == "script" and c.probes
        if c.expected == "FAIL":
            assert not c.controls                         # a known failure is already red
        assert all(x in spec.CONTROLS for x in c.controls)


def test_matrix_expansion_substitutes_and_applies_overrides():
    doc = {"spec_version": 1, "axis": "authority", "cells": [{
        "id": "A9.<w>.<a.n>", "title": "<w> and <a.n>", "tier": "store", "kind": "script", "controls": ["authority"],
        "matrix": {"w": ["x", "y"], "a": [{"n": "home", "_controls": ["identity"]}, {"n": "pet"}]},
        "probes": [{"kind": "store", "assertions": [{"op": "present", "contains": ["<a.n>"]}]}]}]}
    cells = spec.parse_spec(doc)
    assert [c.id for c in cells] == ["A9.x.home", "A9.x.pet", "A9.y.home", "A9.y.pet"]
    home = cells[0]
    assert home.controls == ("identity",) and cells[1].controls == ("authority",)
    assert home.probes[0]["assertions"][0]["contains"] == ["home"] and home.title == "x and home"


@pytest.mark.parametrize("mutate,msg", [
    ({"axis": "nonsense"}, "unknown axis"),
    ({"tier": "weird"}, "tier must be"),
    ({"expected": "MAYBE"}, "expected must be"),
    ({"controls": ["no_such_control"]}, "unknown control"),
    ({"kind": ""}, "needs a kind"),
    ({"tier": "full", "kind": ""}, "must say why"),
    ({"skip_reason": "because"}, "cannot also be skipped"),
    ({"expected": "FAIL", "controls": ["authority"]}, "already red"),
    ({"surprise": 1}, "unknown key"),
    ({"probes": [{"kind": "store", "assertions": [{"op": "present", "contains": ["{not_a_slot}"]}]}]},
     "unknown world slot"),
])
def test_malformed_cells_are_rejected_loudly(mutate, msg):
    with pytest.raises(spec.SpecError, match=msg):
        spec.parse_spec(_cell(**mutate))


def test_malformed_documents_are_rejected():
    for doc in ({"spec_version": 2, "axis": "authority", "cells": [{}]},
                {"spec_version": 1, "axis": "nope", "cells": [{}]},
                {"spec_version": 1, "axis": "authority", "cells": []},
                {"spec_version": 1, "axis": "authority", "cells": [{"id": "x"}]}):
        with pytest.raises(spec.SpecError):
            spec.parse_spec(doc)
    with pytest.raises(spec.SpecError, match="matrix.w"):
        spec.parse_spec({"spec_version": 1, "axis": "authority",
                         "cells": [dict(_cell()["cells"][0], matrix={"w": []})]})


def test_load_cells_refuses_duplicates_and_bad_json(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_cell()))
    (tmp_path / "b.json").write_text(json.dumps(_cell()))
    with pytest.raises(spec.SpecError, match="duplicate cell id"):
        spec.load_cells(tmp_path)
    (tmp_path / "b.json").write_text("{not json")
    with pytest.raises(spec.SpecError, match="b.json"):
        spec.load_cells(tmp_path)
    with pytest.raises(spec.SpecError, match="no scenario specs"):
        spec.load_cells(tmp_path / "empty")


def test_select_only_axis_prefix_and_typos():
    cells = spec.load_cells()
    assert [c.id for c in spec.select(cells, only="A1.digest.home")] == ["A1.digest.home"]
    assert len(spec.select(cells, only="A1.digest.*")) == 7
    assert {c.axis for c in spec.select(cells, axis="b")} == {"extraction"}
    assert {c.axis for c in spec.select(cells, axis="identity,forgetting")} == {"identity", "forgetting"}
    for only, axis in (("A1.nope", None), ("", None), (None, "zz"), (None, ""), ("A1.digest.home", "identity")):
        with pytest.raises(spec.SpecError):
            spec.select(cells, only, axis)


def test_rendered_cell_fills_every_placeholder():
    w = world.make_world()
    for c in spec.load_cells():
        r = c.rendered(w)
        blob = json.dumps([r.params, list(r.events), list(r.probes)])
        assert not world.placeholders(blob), (c.id, world.placeholders(blob))


# ── the artifact, hard invariants, baseline ───────────────────────────────────

def _c(id_, axis="authority", expected="PASS", controls=("authority",), sanity=False):
    return spec.Cell(id=id_, axis=axis, title="t", tier="store", kind="script", expected=expected,
                     controls=tuple(controls), sanity=sanity)


def _r(cell, verdict):
    return {"id": cell.id, "axis": cell.axis, "verdict": verdict, "expected": cell.expected,
            "controls": list(cell.controls), "sanity": cell.sanity}


def test_axis_stats_counts_wilson_sanity_targets_and_claimability():
    cs = [_c("A1"), _c("A2"), _c("A3"), _c("A4", expected="FAIL", controls=()), _c("A5", sanity=True, controls=()),
          _c("A6", controls=())]
    by = {c.id: c for c in cs}
    rows = [_r(cs[0], "PASS"), _r(cs[1], "PASS"), _r(cs[2], "FAIL"), _r(cs[3], "FAIL"), _r(cs[4], "PASS"),
            _r(cs[5], "PASS")]
    s = artifact.axis_stats(rows, by, instrument_ok=True)["authority"]
    assert (s["cells"], s["n"], s["pass"], s["fail"]) == (6, 5, 3, 2)      # the sanity cell is not evidence
    assert s["sanity_pass"] == 1 and s["targets_failing"] == ["A4"]
    assert s["pass_rate"] == 0.6 and s["wilson95"][0] < 0.6 < s["wilson95"][1]
    assert s["hard_violations"] == ["A3"] and s["uncontrolled"] == ["A6"]
    assert s["claimable"] is False                                         # an uncontrolled PASS blocks it
    only_controlled = artifact.axis_stats(rows[:2], by, True)["authority"]
    assert only_controlled["claimable"] is True
    assert artifact.axis_stats(rows[:2], by, instrument_ok=False)["authority"]["claimable"] is False
    empty = artifact.axis_stats([], by, True)["identity"]
    assert empty["n"] == 0 and empty["wilson95"] == [0.0, 1.0] and empty["claimable"] is False


def test_an_error_cell_blocks_claimability_and_skips_are_not_counted_as_evidence():
    a, b = _c("A1"), _c("A2")
    rows = [_r(a, "PASS"), _r(b, "ERROR")]
    s = artifact.axis_stats(rows, {"A1": a, "A2": b}, True)["authority"]
    assert s["error"] == 1 and s["claimable"] is False and s["hard_violations"] == ["A2"]
    skipped = artifact.axis_stats([_r(a, "SKIP")], {"A1": a}, True)["authority"]
    assert skipped["n"] == 0 and skipped["skip"] == 1 and skipped["claimable"] is False


def test_hard_violations_are_zero_tolerance_but_targets_and_graded_axes_are_not():
    hard, target, graded = _c("A1"), _c("A2", expected="FAIL", controls=()), _c("B1", axis="extraction")
    by = {c.id: c for c in (hard, target, graded)}
    rows = [_r(hard, "FAIL"), _r(target, "FAIL"), _r(graded, "FAIL")]
    assert artifact.hard_violations(rows, by) == ["A1"]
    assert artifact.is_hard(hard) and not artifact.is_hard(target) and not artifact.is_hard(graded)
    assert not artifact.is_hard(_c("A9", sanity=True, controls=()))


def test_baseline_compare_only_a_previous_pass_regresses_and_a_hard_failure_is_red_without_one():
    cur = {"A1": "FAIL", "A2": "FAIL", "A3": "PASS", "A4": "SKIP"}
    base = {"cells": {"A1": "PASS", "A2": "FAIL", "A3": "FAIL", "A4": "PASS"}, "corpus_seed": "s",
            "spec_digest": "d", "scorer_version": scorers.SCORER_VERSION}
    cmp = artifact.compare_baseline(cur, base, corpus_seed="s", digest="d")
    assert cmp["regressions"] == ["A1", "A4"] and cmp["improvements"] == ["A3"] and cmp["red"]
    assert artifact.compare_baseline(cur, None, corpus_seed="s", digest="d")["has_baseline"] is False
    # a hard invariant is red with NO baseline at all
    assert artifact.decide_status(run_error=None, refused=None, partial=False, results=[{"verdict": "FAIL"}],
                                  hard=["A1"], cmp=None, compare_requested=False) == "regression"


@pytest.mark.parametrize("field,new", [("corpus_seed", "other"), ("spec_digest", "other"),
                                       ("scorer_version", "99")])
def test_a_changed_seed_digest_or_scorer_version_is_not_comparable_never_red(field, new):
    base = {"cells": {"A1": "PASS"}, "corpus_seed": "s", "spec_digest": "d",
            "scorer_version": scorers.SCORER_VERSION}
    kw = {"corpus_seed": "s", "digest": "d"}
    if field == "scorer_version":
        base["scorer_version"] = new
    else:
        kw["corpus_seed" if field == "corpus_seed" else "digest"] = new
    cmp = artifact.compare_baseline({"A1": "FAIL"}, base, **kw)
    assert cmp["comparable"] is False and cmp["regressions"] == [] and not cmp["red"]
    assert any("not comparable" in n for n in cmp["notes"])


def test_decide_status_table():
    ok = [{"verdict": "PASS"}]
    ds = artifact.decide_status
    assert ds(run_error="x", refused=None, partial=False, results=ok, hard=[], cmp=None, compare_requested=False) == "error"
    assert ds(run_error=None, refused="y", partial=False, results=ok, hard=[], cmp=None, compare_requested=False) == "error"
    assert ds(run_error=None, refused=None, partial=False, results=[{"verdict": "SKIP"}], hard=[], cmp=None,
              compare_requested=False) == "skip"                      # nothing measured is never ok
    assert ds(run_error=None, refused=None, partial=True, results=ok, hard=[], cmp=None, compare_requested=False) == "partial"
    assert ds(run_error=None, refused=None, partial=False, results=ok, hard=[], cmp=None, compare_requested=False) == "ok"
    assert ds(run_error=None, refused=None, partial=False, results=ok, hard=[], cmp={"red": True},
              compare_requested=True) == "regression"
    assert ds(run_error=None, refused=None, partial=False, results=ok, hard=[], cmp={"red": True},
              compare_requested=False) == "ok"


def test_load_baseline_names_every_problem(tmp_path):
    p = tmp_path / "b.json"
    assert "no baseline" in artifact.load_baseline(p)[1]
    p.write_text("{nope")
    assert "not valid JSON" in artifact.load_baseline(p)[1]
    p.write_text(json.dumps({"cells": {}}))
    assert "no 'cells'" in artifact.load_baseline(p)[1]
    p.write_text(json.dumps({"cells": {"A1": "MAYBE"}}))
    assert "unrecognised verdict" in artifact.load_baseline(p)[1]
    p.write_text(json.dumps({"cells": {"A1": "PASS"}}))
    data, problem = artifact.load_baseline(p)
    assert problem is None and data["cells"] == {"A1": "PASS"}


def test_spec_digest_moves_when_a_cell_changes_meaning_not_when_it_changes_title():
    cells = spec.load_cells()
    d0 = artifact.spec_digest(cells)
    from dataclasses import replace
    assert artifact.spec_digest([replace(cells[0], title="renamed")] + cells[1:]) == d0
    assert artifact.spec_digest([replace(cells[0], expected="FAIL")] + cells[1:]) != d0
    assert artifact.spec_digest([replace(cells[0], controls=())] + cells[1:]) != d0


def test_write_json_trend_and_household_string_check(tmp_path):
    payload = {"status": "ok", "finished_at": "t", "arm": "Z0", "axes": {"authority": {"pass": 3, "n": 4}},
               "hard_violations": [], "compare": None, "instrument": {"ok": True}, "revision": {"commit": "c"}}
    artifact.write_json(tmp_path / "r.json", payload)
    assert json.loads((tmp_path / "r.json").read_text())["status"] == "ok"
    artifact.append_trend(tmp_path / "t.jsonl", payload)
    artifact.append_trend(tmp_path / "t.jsonl", payload)
    lines = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["axes"] == {"authority": [3, 4]} and lines[0]["commit"] == "c"
    assert artifact.household_strings_in({"x": "Mika went to the Kit"}, ["Mika", "Dev", "Kit"]) == ["Kit", "Mika"]
    assert artifact.household_strings_in({"x": "developer skipped"}, ["Dev", "Kip"]) == []   # whole words only


# ── arms: the interface and the stubs ─────────────────────────────────────────

class FakeArm(Arm):
    """A dict-backed memory with no wall at all: enough to prove the cell script is arm-agnostic."""
    name = "fake"
    capabilities = frozenset({"clock"})

    def __init__(self):
        self.rows, self.clock = [], 0.0

    def reset(self, user_id):
        self.rows = []

    def ingest(self, turns):
        rep = IngestReport()
        for t in turns:
            rep.turns += 1
            for text in (t.proposes or (t.text,)):
                self.rows.append({"id": str(len(self.rows)), "text": text, "status": "approved"})
                rep.written += 1
        return rep

    def recall(self, query, k=10):
        return [r for r in self.rows if r["status"] == "approved"
                and any(w in r["text"].lower() for w in query.lower().split())][:k]

    def forget(self, entity):
        for r in self.rows:
            if entity.lower() in r["text"].lower():
                r["status"] = "archived"
        return "ok"

    def as_of(self, query, ts):
        raise NotImplementedError("fake has no as-of")

    def stats(self):
        return {"rows": list(self.rows), "counts": {}, "writes_refused": 0}

    def advance_clock(self, seconds):
        self.clock += seconds


def test_turn_validates_its_vocabulary():
    assert Turn("hi").speaker == "owner_typed" and Turn("hi", "owner_taught").op == "say"
    for kw in ({"speaker": "someone"}, {"op": "delete"}, {"speaker": "system_writer"}):
        with pytest.raises(ValueError):
            Turn("x", **kw)
    assert Turn("x", "system_writer", writer="digest", proposes=("a",)).proposes == ("a",)


def test_arm_registry_lists_every_arm_of_the_bakeoff():
    assert set(ARMS) == {"Z0", "Z0-off", "Z0n", "Z0e", "H0", "H1", "H2", "G", "MV", "HM", "MPA", "HMA", "ZMA"}     # MV + HM: the verbatim and combined arms; Z0e: Z0 over real Chroma + MiniLM (2026-10-06)
    with pytest.raises(ValueError):
        make_arm("mem0")


@pytest.mark.parametrize("name,hint", [("G", "graphiti-core")])         # the Hindsight arms are implemented now (tests/unit/test_zmb_hindsight_arm.py)
def test_stub_arms_raise_not_implemented_with_the_install_hint_on_every_call(name, hint):
    arm = make_arm(name)
    assert arm.name == name and arm.capabilities == frozenset()
    calls = [lambda: arm.reset("demo_bar_00000000"), lambda: arm.ingest([Turn("x")]),
             lambda: arm.recall("q"), lambda: arm.forget("x"), lambda: arm.as_of("q", "2026-01-01T00:00:00Z"),
             lambda: arm.stats()]
    for call in calls:
        with pytest.raises(NotImplementedError, match=hint):
            call()
    with pytest.raises(NotImplementedError):
        arm.advance_clock(1)              # the optional capabilities default to "not available"


def test_a_stub_arm_never_imports_or_opens_anything():
    import ast
    import zmb.arms.graphiti as g
    allowed = {"__future__", "typing", "base"}          # the stubs import nothing but the interface
    for mod in (g,):
        tree = ast.parse(Path(mod.__file__).read_text())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0] if node.level == 0 else (node.module or "base"))
        assert mods <= allowed, (mod.__name__, mods - allowed)


def test_row_text_lower_filters_by_status():
    rows = [{"text": "A B", "status": "approved"}, {"text": "C", "status": "archived"}]
    assert row_text_lower(rows) == "a b\nc" and row_text_lower(rows, statuses=("approved",)) == "a b"


def test_the_cell_script_is_arm_agnostic_and_never_names_a_memory_system():
    from zmb import cells as cellmod
    src = Path(cellmod.__file__).read_text()
    for banned in ("memory_service", "MemoryService", "chromadb", "hindsight", "graphiti", "Z0Arm"):
        assert banned not in src.replace("Hindsight and Graphiti", ""), banned
    w = world.make_world()
    cell = spec.load_cells()
    pick = next(c for c in cell if c.id == "F1.sweep_keeps_the_rest").rendered(w)
    out = cellmod.run_cell(pick, w, FakeArm())
    assert out.verdict == "PASS" and out.brain_turns == 0           # runs on ANY arm that speaks the interface
    broken = FakeArm()
    broken.forget = lambda entity: "ok"                              # a forget that forgets nothing
    out = cellmod.run_cell(pick, w, broken)
    assert out.verdict == "FAIL" and out.stage == "write"


def test_run_cell_skips_never_passes_when_an_arm_cannot_do_the_work():
    from zmb import cells as cellmod
    w = world.make_world()
    allc = {c.id: c for c in spec.load_cells()}
    # a brain-tier cell is a declared SKIP with its own reason
    full = allc["E.never_said_reply"].rendered(w)
    out = cellmod.run_cell(full, w, FakeArm())
    assert out.verdict == "SKIP" and "brain" in out.reason
    # a missing capability is a SKIP naming it
    ident = allc["G3.consent.minor"].rendered(w)
    out = cellmod.run_cell(ident, w, FakeArm())
    assert out.verdict == "SKIP" and "identities" in out.reason
    # a stub arm is a SKIP carrying the install hint
    out = cellmod.run_cell(allc["A1.digest.home"].rendered(w), w, make_arm("H1"))
    assert out.verdict == "SKIP" and "Hindsight" in out.reason
    # a cell with no probes proves nothing
    empty = spec.Cell(id="X", axis="authority", title="t", tier="store", kind="script")
    assert cellmod.run_cell(empty, w, FakeArm()).verdict == "ERROR"


def test_demo_user_ids_are_deterministic_and_demo_shaped():
    from zmb import cells as cellmod
    import re
    w = world.make_world()
    c = spec.load_cells()[0]
    u = cellmod.demo_user(w, c)
    assert re.fullmatch(r"demo_bar_[0-9a-f]{8}", u) and u == cellmod.demo_user(w, c)
    assert u != cellmod.demo_user(world.make_world("other"), c)


def test_no_cell_id_title_note_or_skip_reason_names_a_household_string():
    """A cell's public text (what lands in an artifact) must never carry an invented-household name: the
    names live in the world and reach the store only through placeholders."""
    pool = world.pool_strings()
    assert len(pool) > 40
    for c in spec.load_cells():
        public = " ".join((c.id, c.title, c.note, c.skip_reason, c.lme_map))
        hits = scorers.found_needles(public, pool)
        assert not hits, (c.id, hits)


def test_score_disk_names_the_postgres_relations_and_live_tables_but_never_text():
    dirty = {"tokens": {"Marisol": {"total": 3, "files": {"pg_wal/0001": 1, "base/1/16": 2}, "pg_relations": {"audit_log": 2, "pg_wal": 1},
                         "live_rows": {"llm_requests": 1, "audit_log": 0}}}}
    s = scorers.score_disk(dirty)
    assert not s.ok and s.stage == "write" and s.evidence["byte_hits"] == 3
    assert s.evidence["pg_relations"] == ["audit_log", "pg_wal"] and s.evidence["live_row_tables"] == ["llm_requests"]      # a zero-count table is not residue
    assert "Marisol" not in json.dumps(s.evidence)
    clean = scorers.score_disk({"tokens": {"Marisol": {"total": 0, "files": {}, "pg_relations": {}, "live_rows": {}}}})
    assert clean.ok and clean.evidence["pg_relations"] == [] and clean.evidence["live_row_tables"] == []
    assert not scorers.score_disk({"tokens": {}}).ok                          # a scan of nothing proves nothing
    chroma = scorers.score_disk({"tokens": {"x": {"total": 0, "files": {}, "sqlite_pages": {}}}})
    assert chroma.ok and "pg_relations" not in chroma.evidence                    # the Chroma evidence keeps its shape
