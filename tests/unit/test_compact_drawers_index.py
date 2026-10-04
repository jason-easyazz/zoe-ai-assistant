"""Pure parts of scripts/maintenance/compact_drawers_index.py (no chroma, no palace)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("compact_drawers_index", REPO / "scripts" / "maintenance" / "compact_drawers_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ratio_and_advice_match_the_measured_palace():
    m = _load()
    assert m.tombstone_ratio(1591, 258) == pytest.approx(6.17, abs=0.01)   # the 2026-10-04 drawers index
    assert m.compaction_advised(1591, 258)
    assert not m.compaction_advised(300, 258)                              # a fresh-ish index
    # live == 0 has no ratio: None, never inf/nan (Starlette's JSONResponse rejects non-finite floats)
    assert m.tombstone_ratio(0, 0) is None and m.tombstone_ratio(5, 0) is None
    assert not m.compaction_advised(5, 0)                                  # nothing live: nothing to compact


def test_compact_refuses_without_the_stopped_acknowledgement(capsys):
    m = _load()
    assert m.main(["--compact", "--palace", "/nonexistent"]) == 2
    assert "i-stopped-zoe-data" in capsys.readouterr().err
