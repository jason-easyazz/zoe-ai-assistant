"""B3.1 lab — bi-temporal supersession + keep-the-richer-fact controller.

Pins the DETERMINISTIC core of `labs/b3-1-supersession/` (pure Python, no
model, no I/O) so the lab's numbers cannot drift silently:

* the controller scores >= 0.9 precision AND recall on the 50-pair synthetic
  fixture with the FAKE judge (the LLM step is an injectable callable);
* negative control 1: remove the interval-overlap check and disjoint
  historical facts get wrongly invalidated (Graphiti's rule is load-bearing);
* negative control 2: remove the richer-text rule and the distilled fact wins
  over the richer stored one (mem0's rule is load-bearing);
* the write never deletes: an invalidated row keeps its text, gains
  ``valid_until = new.valid_from``, ``expired_at = now``, ``superseded_by``;
* mem0's id rule: a judge naming an id it was not shown, or crashing,
  degrades to ADD — a fact is never lost to the LLM step;
* the M7 shapes ("works at X" → "works at Y"; "father is called" vs
  "dad's name is") now key to the same attribute.

A negative control you have not seen go red is not a control, so every one
here asserts the SPECIFIC wrong outcome, not merely "score is lower".
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

LAB = Path(__file__).resolve().parents[2] / "labs" / "b3-1-supersession"
sys.path.insert(0, str(LAB))

import bitemporal as bt  # noqa: E402
import run_fixture  # noqa: E402
from fake_judge import crashing_judge, fake_judge, hallucinating_judge  # noqa: E402
from fixture import CASES  # noqa: E402

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _fact(fid, text, vf=None, vu=None, **kw):
    return bt.Fact(id=fid, text=text, valid_from=vf, valid_until=vu, created_at="2026-01-01", **kw)


# ── fixture score ─────────────────────────────────────────────────────────────

def test_fixture_is_fifty_synthetic_pairs():
    assert len(CASES) == 50
    cats = {c["cat"] for c in CASES}
    assert cats == {"correction", "paraphrase", "richer", "historical", "transition", "unrelated"}
    # synthetic only: generic subjects, no real names
    for c in CASES:
        assert c["new"].lower().startswith(("person ", "user", "neil, person", "rex the dog")), c["new"]


def test_controller_precision_recall_at_least_0_9_with_fake_judge():
    r = run_fixture.run()
    assert r["macro_precision"] >= 0.9, r["failures"]
    assert r["macro_recall"] >= 0.9, r["failures"]
    for event in run_fixture.EVENTS:
        assert r["precision"][event] >= 0.9, (event, r["failures"])
        assert r["recall"][event] >= 0.9, (event, r["failures"])
    assert r["accuracy"] >= 0.9, r["failures"]


# ── negative control 1: no overlap check → history wrongly invalidated ────────

def test_control_without_overlap_check_invalidates_historical_facts():
    with_rule = run_fixture.run()
    without = run_fixture.run(overlap_check=False)
    assert with_rule["per_cat"]["historical"] == (10, 10)
    ok, n = without["per_cat"]["historical"]
    assert n == 10 and ok == 0, without["per_cat"]
    # and the failure MODE is the specific wrong thing: SUPERSEDE of a fact
    # whose validity window ended before the new one began
    wrong = [f for f in without["failures"] if f[1] == "historical"]
    assert wrong and all(f[4][0] == bt.SUPERSEDE for f in wrong), wrong
    # ADD recall collapses because every historical ADD became a SUPERSEDE
    assert without["recall"][bt.ADD] < 0.5 < with_rule["recall"][bt.ADD]


def test_overlap_rule_boundary_is_half_open():
    old = _fact(1, "person a lives in southbay", "2015-01-01", "2021-07-01")
    new = _fact(0, "person a lives in northport", "2021-07-01", None)
    assert not bt.intervals_overlap(old, new)
    # one day earlier and they do overlap
    new2 = _fact(0, "person a lives in northport", "2021-06-30", None)
    assert bt.intervals_overlap(old, new2)
    # open-ended on both sides overlaps
    assert bt.intervals_overlap(_fact(1, "x"), _fact(2, "y"))


# ── negative control 2: no richer rule → distilled fact wins ──────────────────

def test_control_without_richer_rule_lets_distilled_fact_win():
    with_rule = run_fixture.run()
    without = run_fixture.run(richer_rule=False)
    assert with_rule["per_cat"]["richer"] == (8, 8)
    assert with_rule["per_cat"]["paraphrase"] == (10, 10)
    # the four "existing is richer" cases now UPDATE with the sparser text
    richer_misses = [f for f in without["failures"] if f[1] == "richer"]
    assert len(richer_misses) == 4, richer_misses
    assert all(f[4][0] == bt.UPDATE for f in richer_misses)
    # concretely: the spelled-out name is replaced by the bare name
    existing = _fact(1, "user's dad's name is neil, spelled n-e-i-l")
    new = _fact(0, "User's father's name is Neil")
    keep = bt.reconcile(new, [existing], now=NOW)
    lose = bt.reconcile(new, [existing], richer_rule=False, now=NOW)
    assert keep.event == bt.NONE
    assert lose.event == bt.UPDATE and lose.text == new.text
    store = bt.apply(lose, new, {1: existing}, now=NOW)
    assert "spelled" not in store[1].text  # information lost — the bug the rule prevents


def test_richer_is_value_information_not_length():
    # framing words are not information
    assert bt.richer("person a is employed by globex", "person a works at globex") == "person a works at globex"
    # detail is
    assert bt.richer("person a works at globex as a senior mechanical engineer",
                     "person a works at globex").startswith("person a works at globex as")
    # chatter never replaces a keyed fact
    assert bt.richer("Neil, Person A's dad, says hi and he loves fishing", "person a's father's name is neil") \
        == "person a's father's name is neil"
    # a DIFFERENT detail set is not "richer" (would lose the spelling)
    assert bt.richer("user's dad's name is neil the fisherman",
                     "user's dad's name is neil, spelled n-e-i-l") == "user's dad's name is neil, spelled n-e-i-l"


# ── never delete ──────────────────────────────────────────────────────────────

def test_supersede_never_deletes_and_sets_four_timestamps():
    old = _fact(1, "person a works at acme corporation", "2018-01-01", None)
    new = _fact(0, "Person A works at Globex now", "2024-03-01", None)
    d = bt.reconcile(new, [old], now=NOW)
    assert (d.event, d.target_id) == (bt.SUPERSEDE, 1)
    store = bt.apply(d, new, {1: old}, now=NOW)
    assert set(store) == {1, 2}
    retired = store[1]
    assert retired.text == old.text                     # text untouched
    assert retired.valid_until == "2024-03-01"          # closed at new.valid_from (Graphiti)
    assert retired.expired_at == NOW.isoformat()        # retired now
    assert retired.superseded_by == 2
    assert store[2].is_live and store[2].valid_until is None
    assert bt.live_texts(store) == ["Person A works at Globex now"]
    # idempotent: re-reconciling the same fact against the updated store is NONE
    again = bt.reconcile(new, list(store.values()), now=NOW)
    assert again.event == bt.NONE and again.target_id == 2


def test_transition_records_both_sides():
    d = bt.reconcile(_fact(0, "User switched from Acme to Initech"), [], now=NOW)
    assert d.event == bt.ADD and d.write_as is not None
    assert d.write_as.text == "user works at initech"
    assert [c.text for c in d.extra_closed] == ["user works at acme"]
    store = bt.apply(d, _fact(0, "User switched from Acme to Initech"), {}, now=NOW)
    closed = [f for f in store.values() if f.valid_until is not None]
    assert len(closed) == 1 and closed[0].text == "user works at acme"
    assert bt.live_texts(store) == ["user works at initech"]


# ── judge validation (mem0 integer-id rule) ───────────────────────────────────

def test_judge_only_path_is_reached_and_id_outside_shown_set_degrades_to_add():
    existing = _fact(1, "person a's father's name is neil")
    new = _fact(0, "Neil, Person A's dad, says hi")  # no framing → judge decides
    assert bt.attribute_key(new.text) is None
    good = bt.reconcile(new, [existing], judge=fake_judge, now=NOW)
    assert good.event == bt.NONE and good.target_id == 1
    bad = bt.reconcile(new, [existing], judge=hallucinating_judge, now=NOW)
    assert bad.event == bt.ADD and "not shown" in bad.reason
    crashed = bt.reconcile(new, [existing], judge=crashing_judge, now=NOW)
    assert crashed.event == bt.ADD
    nojudge = bt.reconcile(new, [existing], judge=None, now=NOW)
    assert nojudge.event == bt.ADD


def test_judge_contradiction_still_respects_overlap():
    hist = _fact(1, "person a's father's name is neil", "2000-01-01", "2001-01-01")
    new = _fact(0, "Tom, Person A's dad, says hi", "2026-01-01")
    d = bt.reconcile(new, [hist], judge=fake_judge, now=NOW)
    assert d.event == bt.ADD and "disjoint" in d.reason


# ── M7: attribute-key normalisation ───────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("user works at acme corporation", "user works at globex"),
    ("Person A is employed by Acme", "person a works for globex"),
    ("user's father's name is neil", "user's dad is called tom"),
    ("person a has a dog named rex", "person a's dog is called max"),
    ("user lives in northport", "user moved to southbay"),
])
def test_m7_framings_share_an_attribute_key(a, b):
    assert bt.attribute_key(a) is not None
    assert bt.attribute_key(a) == bt.attribute_key(b)
    assert not bt.same_value(a, b)


def test_non_attribute_text_has_no_key():
    assert bt.attribute_key("the weather is nice today") is None
    assert bt.attribute_key("Neil, Person A's dad, says hi") is None


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_BITEMPORAL_SUPERSEDE", raising=False)
    assert bt.enabled() is False
    monkeypatch.setenv("ZOE_BITEMPORAL_SUPERSEDE", "1")
    assert bt.enabled() is True
