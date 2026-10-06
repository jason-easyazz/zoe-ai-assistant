"""ZMB bake-off: the HM driver (``scripts/perf/zmb/hm_window.py``) that runs in the bake-off venv: its panel guard, its skip when the real library is absent,
and its result over the doubles (no server, no library, no docker)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import hm_window  # noqa: E402


def _ssh(stdout: str, rc: int = 0):
    return lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=rc)


def test_the_panel_guard_stops_on_a_voice_turn_after_the_window_started_and_not_before():
    start = time.time()
    before = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start - 3600))
    after = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start + 5))
    hm_window.PanelGuard(start, runner=_ssh(before + "\n"))()                                  # an old turn: quiet
    with pytest.raises(SystemExit, match="voice turn"):
        hm_window.PanelGuard(start, runner=_ssh(after + "\n"))()
    hm_window.PanelGuard(start, runner=_ssh("", rc=255))()                                      # an unreachable panel says nothing (the land script's convention)
    hm_window.PanelGuard(None, runner=_ssh(after + "\n"))()                                     # no hook, no guard


def test_the_panel_guard_polls_at_most_every_thirty_seconds():
    calls = []
    guard = hm_window.PanelGuard(time.time(), runner=lambda *a, **k: calls.append(1) or SimpleNamespace(stdout="", returncode=1))
    for _ in range(5):
        guard()
    assert len(calls) == 1


def test_without_the_real_library_the_driver_writes_a_skip_never_a_result(tmp_path, monkeypatch):
    monkeypatch.setattr(hm_window, "library_available", lambda: False)
    out = tmp_path / "hm.json"
    assert hm_window.main(["--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert "not importable" in res["skipped"] and "hm_cells" not in res and "generic" not in res


def test_the_driver_forces_onnxruntimes_telemetry_off_before_anything_loads():
    assert hm_window.os.environ["ORT_DISABLE_TELEMETRY"] == "1"
