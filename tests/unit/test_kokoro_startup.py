"""Kokoro start-up couplings found by the 2026-10-04 log review.

* Every start printed ``WARNING: Defaulting repo_id to hexgrad/Kokoro-82M`` — the
  sidecar now passes the repo id explicitly.
* The installed unit had ``TimeoutStartSec=120`` while the sidecar waits up to
  ``_BRAIN_WAIT_S`` (180 s) for the brain before loading: a slow-brain boot made
  systemd SIGTERM it mid-wait and loop. The template (300) and the tracked drop-in
  must both stay above that wait.

Pure logic — no torch, no kokoro, no network (the module lazy-imports them).
"""
import importlib.util
import pathlib
import re
import sys

import pytest

pytestmark = pytest.mark.ci_safe

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "setup" / "kokoro_sidecar.py"
_UNIT_DIR = _SCRIPT.parent / "systemd"


@pytest.fixture(scope="module")
def kok():
    spec = importlib.util.spec_from_file_location("kokoro_sidecar_startup", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar_startup"] = module
    spec.loader.exec_module(module)
    return module


def test_repo_id_is_the_one_kpipeline_defaults_to(kok):
    assert kok._KOKORO_REPO_ID == "hexgrad/Kokoro-82M"


def test_every_kpipeline_construction_passes_the_repo_id():
    src = _SCRIPT.read_text(encoding="utf-8")
    calls = re.findall(r"KPipeline\(([^)]*)\)", src)
    assert len(calls) == 2, "KPipeline construction sites changed — re-point this pin"
    assert all("repo_id=_KOKORO_REPO_ID" in c for c in calls), calls


def _timeout_s(path):
    m = re.search(r"^TimeoutStartSec=(\d+)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, f"{path.name} has no numeric TimeoutStartSec"
    return int(m.group(1))


def test_start_timeout_outlasts_the_brain_wait(kok):
    for f in (_UNIT_DIR / "kokoro-tts.service", _UNIT_DIR / "kokoro-tts.service.d" / "70-start-timeout.conf"):
        assert _timeout_s(f) > kok._BRAIN_WAIT_S + 30, f.name
