"""The checkout-bound half of the P-family tests (UNMARKED: runs in the Jetson full-directory lane,
not the slim GitHub lane, because it imports services/zoe-data modules): the in-tree proactive
selector against the P1/P4 fixtures, the style regexes against persona_drift, and the bar/day-sim
constants the family reuses. The pure scorer / control / judge-gate tests are
tests/unit/test_samantha_person.py (ci_safe).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"
ZD = REPO / "services" / "zoe-data"


def _load():
    sys.path.insert(0, str(PERF))
    spec = importlib.util.spec_from_file_location("samantha_person", PERF / "samantha_person.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_person"] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load()


def _real_selector_or_skip():
    sys.path.insert(0, str(ZD))
    try:
        from proactive import selector  # noqa: F401
        from memory_digest import fact_has_topic  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"services/zoe-data selector not importable here: {type(exc).__name__}")
    return sp.real_selector


def test_the_in_tree_selector_passes_the_salience_cell():
    real = _real_selector_or_skip()
    acc = sp.run_selector_tier(sp.World(), real)
    res = {h: (sum(1 for c, _ in v if c), len(v)) for h, v in acc.items()}
    # P1: top-1 is the worry, the three decoys are never kept, one raise, order worry > event > mum
    assert res["P1.a"] == (20, 20) and res["P1.b"] == (0, 20) and res["P1.c"] == (20, 20) and res["P1.d"] == (20, 20)


def test_the_in_tree_selector_has_no_sensitive_class_p4_is_the_known_target():
    real = _real_selector_or_skip()
    acc = sp.run_selector_tier(sp.World(), real)
    sensitive_raised = sum(1 for c, _ in acc["P4.a"] if c)
    # EXPECTED FAIL until SAL3 exists (record 5.1): the health worry is raised to an unconfirmed
    # voice. When this starts passing the class was built: move P4.a out of the targets deliberately.
    assert sensitive_raised == 0 and sp.HALF["P4.a"].expected == "FAIL"
    first = acc["P4.a"][0][1]
    assert first["sensitive_raised"]


def test_the_real_selector_adapter_drops_what_gather_drops():
    real = _real_selector_or_skip()
    fx = sp.p1_fixture(sp.World(), 0)
    ids = [p["id"] for p in real(fx)]
    assert "resolved" not in ids and "mood" not in ids and "task" not in ids
    assert ids[0] == "worry" and [p["raised"] for p in real(fx)].count(True) == 1


def test_a_selector_that_keeps_the_passing_mood_is_caught_by_p1():
    # the instrument sees a leak: feed the scorer the in-tree result PLUS the decoy
    fx = sp.p1_fixture(sp.World(), 0)
    leaky = [{"id": "worry", "raised": True}, {"id": "event", "raised": False}, {"id": "mood", "raised": False}]
    out = sp.score_p1(leaky, fx)
    assert out["P1.b"][0] is True and out["P1.c"][0] is False


def test_style_regexes_match_persona_drift():
    sys.path.insert(0, str(ZD))
    try:
        import persona_drift as pdm  # type: ignore
        import persona_layer as pl  # type: ignore
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"persona_drift not importable here: {type(exc).__name__}")
    rec = pl.default_persona()
    for text in ("Night. Sleep well.", "Absolutely! Here you go.", "As an AI language model I can't.",
                 "**Bold** text", "- a\n- b", "One. Two. Three. Four.", "Sure thing, done.", ""):
        mine = sp.style_checks(text)
        theirs = pdm.style_checks(text, rec)
        for check in ("opener", "no_selfid", "no_markdown"):
            assert mine[check] == theirs[check], (text, check)
    band = sp.load_drift_band()
    assert band is None or band("Night. Sleep well.") in ("aligned", "neutral", "deviation")


def test_the_bar_and_day_sim_constants_the_family_relies_on():
    assert sp.sb.VERBATIM_RUN == 7
    assert sp.ds.MEAT and sp.ds.SEAFOOD and "dentist" in sp.ds.TOPICS
    assert callable(sp.ds.score_raise_open) and callable(sp.ds.score_no_reraise) and callable(sp.ds.score_spacing)
    # the family reuses the day-sim's live class (demo users, sessions, proven teardown), unchanged
    assert issubclass(sp.PersonLive, sp.ds.DayLive) and issubclass(sp.ds.DayLive, sp.sb.Live)
    live = sp.PersonLive("t", "", "", False)
    assert live.session("demo_bar_0123abcd", "x").startswith("bar-pf-x-")
    with pytest.raises(ValueError):
        live.chat("jason", "x", "hello")             # the demo-user guard fires before any request
