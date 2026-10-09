"""The bounded live check of the structural verifier: its row draw and its hard call cap (no brain is contacted)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "perf" / "structural_verifier_live_check.py"
_spec = importlib.util.spec_from_file_location("structural_verifier_live_check", _PATH)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_the_draw_is_deterministic_synthetic_and_never_over_forty():
    a, b = check.select_items(), check.select_items()
    assert [r["id"] for r in a] == [r["id"] for r in b] and len(a) <= check.MAX_CALLS == 40
    assert all(r["task"] == "support" for r in a)
    assert {r["lang"] for r in a} >= {"en", "es", "fr", "de", "zh", "ja"}
    assert any(r["label"] for r in a) and any(not r["label"] for r in a)


@pytest.mark.parametrize("asked", [1, 7, 40, 41, 400, 10**9])
def test_the_cap_cannot_be_raised(asked):
    assert len(check.select_items(limit=asked)) <= min(asked, 40)


def test_the_summary_counts_a_confusion_matrix():
    rows = check.select_items(limit=4)
    got = {"verdicts": ["yes", "no", None, "yes"], "lat_ms": [100.0, 200.0, 300.0, 400.0]}
    s = check.summarise(rows, got)
    assert s["calls"] == 4 and s["unparsed"] == 1 and s["tp"] + s["tn"] + s["fp"] + s["fn"] == 3 and s["latency_ms"]["p50"] == 250.0
