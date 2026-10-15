"""``scripts/voice/speaker_dual_compare.py`` - the dual-run shadow-week comparison (aggregates only, planted rows, no household data)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "voice" / "speaker_dual_compare.py"
_spec = importlib.util.spec_from_file_location("speaker_dual_compare_under_test", _PATH)
cmp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmp)

R34 = "wespeaker_resnet34+i8qdq-u8s8+c1"


def row(truth, legacy_user, legacy_score, r34_user, r34_accept, source="local", sec_source="local", ms=90.0):
    sec = dict(model_id=R34, user_id=r34_user, score=0.3, accept=r34_accept, source=sec_source, ms=ms)
    return dict(model_id="resemblyzer", user_id=legacy_user, score=legacy_score, source=source, truth=truth, secondary=sec)


def test_each_model_is_scored_against_the_label_on_the_same_turn():
    rows = [
        row("jason", "jason", 0.90, "jason", True),       # both right
        row("jason", "jason", 0.70, "jason", True),       # Resemblyzer below its 0.82 bar: a false reject; ResNet34 right
        row("none", "jason", 0.85, "jason", False),       # a visitor: Resemblyzer false-accepts, ResNet34 rejects
        row("jason", "ada", 0.88, "jason", True),         # Resemblyzer mistakes him for Ada
        row(None, "jason", 0.95, "jason", True),          # unlabelled: excluded
        row("jason", None, None, None, None, source="error", sec_source="no_profiles"),   # not scored: excluded
    ]
    res = cmp.compare(rows)
    leg, new = res["models"]["resemblyzer"], res["models"][R34]
    assert (leg["genuine"], leg["ta"], leg["fr"], leg["misid"], leg["impostor"], leg["fa"]) == (3, 1, 2, 1, 1, 1)
    assert (new["genuine"], new["ta"], new["fr"], new["impostor"], new["fa"]) == (3, 3, 0, 1, 0)
    assert res["labelled"] == 5 and res["unscored"] == 2 and len(res["added_ms"]) == 6
    text = cmp.render(res)
    assert "FA 100.0%" in text and "FA 0.0%" in text and "p50 90" in text


def test_the_legacy_bar_is_the_servers_threshold():
    rows = [row("jason", "jason", 0.80, "jason", True)]
    assert cmp.compare(rows, legacy_threshold=0.82)["models"]["resemblyzer"]["ta"] == 0
    assert cmp.compare(rows, legacy_threshold=0.75)["models"]["resemblyzer"]["ta"] == 1      # negative control
