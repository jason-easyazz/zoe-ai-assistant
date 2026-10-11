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


def _run_with_fake_systemctl(tmp_path, word):
    """ZOE_PERF=1 with a stub `systemctl` printing ``word``; :10201/health is unreachable (hermetic)."""
    fake = tmp_path / "systemctl"
    fake.write_text(f"#!/bin/sh\necho {word}\n")
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "ZOE_PERF": "1",
           "KOKORO_PROBE_HEALTH_URL": "http://127.0.0.1:9/health"}   # discard port: health is unreachable, hermetically
    return subprocess.run([sys.executable, "-I", str(PROBE)], env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("word", ["active", "activating", "failed", "garbage"])
def test_failed_health_with_a_live_or_unknown_service_is_refused(tmp_path, word):
    r = _run_with_fake_systemctl(tmp_path, word)
    assert r.returncode == 2 and "refusing" in r.stdout
