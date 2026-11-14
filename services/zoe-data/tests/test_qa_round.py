"""The QA round runner (``qa_round.py``) on the lab household: the seeded round 10a passes in ``--lab`` mode, its output is stable
and diffable, and a deliberately WRONG expected outcome fails the round (the negative controls).

``QA_ROUND_FILE`` / ``QA_ROUND_OUT`` (set by ``qa_round.py --lab``) pick another round file / write its results; unset, the seeded
round runs and nothing is written outside ``tmp_path``. All data synthetic.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import lab_member  # noqa: E402
import qa_round  # noqa: E402

pytestmark = pytest.mark.ci_safe

SEEDED = ROOT / "tests" / "fixtures" / "qa_rounds" / "round-10a.json"


@pytest.fixture
def make_household(monkeypatch, tmp_path):
    """A factory: a fresh household per case (its own store), torn down at the end."""
    import skybridge_service as sky

    made = []

    def _make():
        from _pytest.monkeypatch import MonkeyPatch

        mp = MonkeyPatch()
        hh = lab_member.install(mp, tmp_path / f"case-{len(made)}")
        mp.setattr(sky, "today_for_zoe_tz", lambda: date(2026, 11, 23))
        made.append((mp, hh))
        return hh

    yield _make
    for mp, hh in reversed(made):
        lab_member.teardown(hh)
        mp.undo()


async def _run(data, make_household, tmp_path):
    results = [await qa_round.run_case_lab(c, make_household) for c in data["cases"]]
    return qa_round.summarise(data["round"], "lab", results)


async def test_the_round_passes_in_the_lab(make_household, tmp_path):
    path = Path(os.environ.get("QA_ROUND_FILE") or SEEDED)
    data = qa_round.load_round(path)
    summary = await _run(data, make_household, tmp_path)
    out = qa_round.write_results(summary, os.environ.get("QA_ROUND_OUT") or tmp_path / "out")
    bad = [c for c in summary["cases"] if c["status"] in ("fail", "unexpected_pass")]
    assert not bad, json.dumps(bad, indent=1)
    assert summary["ok"] and json.loads(out.read_text())["scorecard"].startswith(f"QA_ROUND round={data['round']} mode=lab")
    lab_cases = [c for c in data["cases"] if not c.get("live_only")]
    assert sum(1 for c in summary["cases"] if c["status"] != "not_run") == len(lab_cases)


def _case(case_id):
    data = qa_round.load_round(SEEDED)
    return next(c for c in data["cases"] if c["id"] == case_id)


@pytest.mark.parametrize("case_id,step,exp,wrong", [
    ("calendar.repeat.delete_one", 2, 0, {"path": "spoken_summary", "contains": "Deleted"}),     # expects the bug
    ("timers.voice.announce", 3, 1, {"path": "announcements.0.panel_id", "equals": "another-panel"}),
    ("lists.private.child_cannot_see", 2, 0, {"path": "lists", "len": 1}),                         # expects a leak
    ("safety.floor.idiom", 0, 0, {"path": "medical", "truthy": True}),
])
async def test_a_wrong_expected_outcome_fails_the_round(make_household, tmp_path, case_id, step, exp, wrong):
    case = copy.deepcopy(_case(case_id))
    case["steps"][step]["expect"][exp] = wrong
    summary = qa_round.summarise("ctl", "lab", [await qa_round.run_case_lab(case, make_household)])
    assert summary["cases"][0]["status"] == "fail" and not summary["ok"], summary


async def test_a_known_bug_that_passes_is_an_unexpected_pass(make_household, tmp_path):
    case = {**copy.deepcopy(_case("lists.fridge.panel_items")), "known_bug": "synthetic control"}
    summary = qa_round.summarise("ctl", "lab", [await qa_round.run_case_lab(case, make_household)])
    assert summary["cases"][0]["status"] == "unexpected_pass" and not summary["ok"]


def test_live_mode_prints_only_turns_and_no_http_step():
    data = qa_round.load_round(SEEDED)
    turns = qa_round.live_turns(data)
    assert turns and all(set(t) == {"case", "theme", "turn", "as", "lane", "text", "expect_note"} for t in turns)
    assert {t["case"] for t in turns} >= {c["id"] for c in data["cases"] if c.get("live_only")}
    assert not any(t["text"].startswith("/api") for t in turns)


def test_output_is_stable_and_diffable(tmp_path):
    a = qa_round.summarise("9", "live", [{"id": "z.b.c", "theme": "z", "status": "pass", "why": ""},
                                         {"id": "a.b.c", "theme": "a", "status": "fail", "why": "x | y"}])
    b = qa_round.summarise("9", "live", list(reversed(a["cases"])))
    assert qa_round.markdown(a) == qa_round.markdown(b)                      # order-independent
    assert "| a.b.c | a | fail | x / y |" in qa_round.markdown(a)
    assert qa_round.scorecard_line(a) == "QA_ROUND round=9 mode=live a=0/1 z=1/1"


def test_a_malformed_round_is_refused(tmp_path):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"round": "x", "cases": [{"id": "a.b", "theme": "t", "steps": [{}]}, {"id": "a.b", "theme": "t", "steps": [{}]}]}))
    with pytest.raises(ValueError, match="duplicate"):
        qa_round.load_round(bad)
    bad.write_text(json.dumps({"round": "x", "cases": [{"id": "Bad Id", "theme": "t", "steps": [{}]}]}))
    with pytest.raises(ValueError, match="malformed"):
        qa_round.load_round(bad)
