"""The golden set through the REDESIGNED path (memory redesign steps 6-7): ``labs/memory-claims-lab/run_claims.py`` drives
every case through the idle claim pass, the validator and the guarded writer on a scratch store with a scripted model, and
this file pins its score to ``labs/memory-claims-lab/score-claims.json`` so a change that moves a case shows up as a diff.
A report pin, like the baseline pin in ``test_memory_claims_golden.py``; re-record with
``MEMORY_CLAIMS_LAB_WRITE=1 pytest <this file>`` and review the diff. Synthetic data only. ``ci_safe``."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

LAB = Path(__file__).resolve().parents[3] / "labs" / "memory-claims-lab"


def _lab(name: str):
    spec = importlib.util.spec_from_file_location(f"memory_claims_lab_{name}", LAB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report():
    runner = _lab("run_claims")
    rep = runner.run_all()
    if os.environ.get("MEMORY_CLAIMS_LAB_WRITE") == "1":
        runner.SCORE.write_text(runner.dump(rep), encoding="utf-8")
        print("\n" + runner.render(rep))
    return rep


def test_the_claims_runner_reproduces_the_committed_score(report):
    want = json.loads((LAB / "score-claims.json").read_text(encoding="utf-8"))
    for key in ("cases", "totals", "by_class", "failing_ids", "not_runnable", "rule_unobserved", "trigger"):
        assert report[key] == want[key], key
    assert {i: r["status"] for i, r in report["results"].items()} == {i: r["status"] for i, r in want["results"].items()}


def test_the_redesign_fixes_cases_and_breaks_none_without_a_name(report):
    """Against today's code (``baseline-today.json``): every case that passes today and fails on the redesigned path is
    NAMED here with its cause. A new one is a regression until it is explained."""
    today = json.loads((LAB / "baseline-today.json").read_text(encoding="utf-8"))["results"]
    lost = sorted(i for i, r in report["results"].items() if today[i]["status"] == "PASS" and r["status"] != "PASS")
    assert lost == sorted(KNOWN_PARITY_GAPS), lost
    gained = [i for i, r in report["results"].items() if today[i]["status"] != "PASS" and r["status"] == "PASS"]
    assert len(gained) >= len(lost)


def test_no_write_on_a_left_as_mined_case(report):
    """The harm bar: a case whose expected outcome is "left as mined" never has a row rewritten by the redesigned path."""
    assert not [i for i, r in report["results"].items() if "rewrote despite left_as_mined" in r["detail"]]


#: passes today, fails on the redesigned path: case -> why (each is in the PR description)
KNOWN_PARITY_GAPS: dict = {
    "C05": "label equality: the row says 'a dentist appointment', the correction 'the dentist' (design note K)",
    "C06": "label equality: the row says 'car rego', the correction 'the rego'",
    "C41": "a correction that arrives BEFORE the old fact is stored abstains on rule 9 (old value said, nothing stored), so no "
           "registry entry exists and the old value mined later is not dropped as stale",
    "C44": "outcome agrees (left as mined); the golden expects rule 3, the validator reports rule 9 for an unresolved nickname",
    "E14": "the event is filed as user_stated, the golden expects user_stated_derived (ingest cannot resolve authority on the quote)",
    "K08": "two people share the name: the validator holds the candidate pending, the golden expects refuse",
}
