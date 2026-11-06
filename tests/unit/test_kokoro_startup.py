"""Kokoro start-up pins from the 2026-10-04 log review (pure logic: no torch/kokoro/network).

* Every start printed ``Defaulting repo_id to hexgrad/Kokoro-82M``: both ``KPipeline``
  constructions now pass the repo id explicitly.
* Template consistency: the template (300) and the tracked drop-in agree and stay above the
  sidecar's ``_BRAIN_WAIT_S`` (180 s). Config consistency only -- the directive has no runtime
  effect (Type=simple, no ExecStartPre/Post, so systemd never runs the timeout during that wait).
"""
import importlib.util
import pathlib
import re
import sys

import pytest

pytestmark = pytest.mark.ci_safe

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "setup" / "kokoro_sidecar.py"
_UNITS = _SCRIPT.parent / "systemd"


@pytest.fixture(scope="module")
def kok():
    spec = importlib.util.spec_from_file_location("kokoro_sidecar_startup", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar_startup"] = module
    spec.loader.exec_module(module)
    return module


def test_every_kpipeline_construction_passes_the_default_repo_id(kok):
    assert kok._KOKORO_REPO_ID == "hexgrad/Kokoro-82M"
    calls = re.findall(r"KPipeline\(([^)]*)\)", _SCRIPT.read_text(encoding="utf-8"))
    assert len(calls) == 2 and all("repo_id=_KOKORO_REPO_ID" in c for c in calls), calls


def test_template_and_dropin_timeouts_agree_and_exceed_the_brain_wait(kok):
    for f in (_UNITS / "kokoro-tts.service", _UNITS / "kokoro-tts.service.d" / "70-start-timeout.conf"):
        m = re.search(r"^TimeoutStartSec=(\d+)\s*$", f.read_text(encoding="utf-8"), re.MULTILINE)
        assert m and int(m.group(1)) > kok._BRAIN_WAIT_S + 30, f.name


def test_every_start_logs_one_countable_line_with_the_degraded_flag(kok):
    ok = kok.start_line("cuda", None, 12.34, 77)
    assert ok == "KOKORO_START device=cuda degraded=0 reason=- load_s=12.3 pid=77"
    bad = kok.start_line("cpu", "CUDA unavailable (NVML\nassertion failed)", 40.0, 78)
    assert bad.startswith("KOKORO_START device=cpu degraded=1 reason=CUDA_unavailable_(NVML_assertion_failed)")
    assert len(bad.splitlines()) == 1
    # negative control: a healthy start is never counted as degraded
    assert "degraded=1" not in ok


def test_lifespan_emits_the_start_line_right_after_the_pipeline_loads(kok):
    src = _SCRIPT.read_text(encoding="utf-8")
    life = src[src.index("async def lifespan"):]
    assert life.index("_load_pipeline") < life.index("start_line(") < life.index("Warming up Kokoro")
