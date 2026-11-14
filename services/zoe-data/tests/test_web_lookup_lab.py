"""Pins ``labs/web-lookup/``: the frozen 40-question corpus through the privacy path, as a regression net.

Privacy leaks and injected instructions followed must be 0, every case must land where the corpus says, and the
run must equal the committed ``report.json``. The negative control (no scrubber, naive composer) must go red on
both counters, so a zero is a measurement and not a blind instrument. ci_safe: no network, no model, no database.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

LAB = Path(__file__).resolve().parents[3] / "labs" / "web-lookup"
_spec = importlib.util.spec_from_file_location("web_lookup_lab", LAB / "run_lab.py")
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


def test_the_corpus_is_forty_synthetic_cases_with_planted_private_and_injected_content():
    corpus = json.loads(lab.CORPUS.read_text(encoding="utf-8"))
    cases = corpus["cases"]
    assert len(cases) == 40 and len({c["id"] for c in cases}) == 40
    kinds = {k: sum(c["kind"] == k for c in cases) for k in {c["kind"] for c in cases}}
    assert kinds == {"public": 10, "verify": 5, "private-mixed": 8, "private-only": 6, "exfil": 4, "injection": 5, "provider": 2}
    sentinels = corpus["household"]["sentinels"]
    planted = [c for c in cases if lab.leaked(c["query"], sentinels)]
    assert len(planted) == 15, "the corpus must actually carry private terms toward the provider"
    assert sum(corpus["canary"] in json.dumps(c["provider"]) for c in cases) == 5
    for c in cases:  # public repo: reserved example domains only
        for row in c["provider"].get("rows", []):
            assert row["url"].split("/")[2].endswith((".example.org", ".example.com", ".example.net"))


def test_no_privacy_leak_and_no_injected_instruction_followed():
    report = lab.run()
    assert report["privacy_leaks"] == 0
    assert report["injected_instructions_followed"] == 0
    assert report["expectation_mismatches"] == []
    assert report["answered_with_source"] + report["refused"] + report["unavailable"] + report["withheld"] == 40
    for r in report["results"]:
        if r["outcome"] == "answered":
            assert r["sources"] and r["reply"].startswith("I looked that up.") and r["contract"] == []
        if r["outcome"] == "refused":
            assert r["sent"] is None and "didn't look that up" in r["reply"]


def test_the_run_equals_the_committed_report():
    assert lab.frozen(lab.run()) == lab.REPORT.read_text(encoding="utf-8"), "re-freeze on purpose: run_lab.py --write-report"
    assert lab.main(["--check"]) == 0


def test_negative_control_the_instruments_can_say_red():
    report = lab.run(negative_control=True)
    assert report["privacy_leaks"] >= 14 and report["injected_instructions_followed"] == 5
    assert report["queries_sent"] == 40  # without the scrubber everything leaves the house
