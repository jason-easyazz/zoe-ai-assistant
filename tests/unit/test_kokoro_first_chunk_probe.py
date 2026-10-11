"""The Kokoro first-chunk probe loads its own ~2.3 GB model, so it must stay opt-in and refuse to
run beside the live sidecar. Pins the guard (not the measurements)."""
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.ci_safe

PROBE = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "perf" / "kokoro_first_chunk_probe.py"


def test_probe_skips_without_zoe_perf():
    env = {"PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, "-I", str(PROBE)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "ZOE_PERF" in r.stdout


def test_probe_guard_precedes_any_heavy_import():
    src = PROBE.read_text()
    guard = src.index('os.environ.get("ZOE_PERF") != "1"')
    refuse = src.index("refusing to load a second Kokoro")
    assert guard < refuse < src.index("import torch")
