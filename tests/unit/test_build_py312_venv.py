"""Behaviour of `scripts/setup/build_py312_venv.sh`'s build path, run against a
FAKE uv and a FAKE venv interpreter — no network, no install, no real venv.

Pins the post-sync damage scan (#1706): the scanner's exit status must reach the
script. Read through a process substitution into `mapfile`, a crashed scanner
looked exactly like "no damage", the repair was skipped, and the build could
announce a ready venv (Greptile, #1706).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup" / "build_py312_venv.sh"

FAKE_PY = """#!/usr/bin/env bash
# fake venv interpreter: version probe, damage scanner, everything else succeeds
if [[ "$1" == "-c" ]]; then echo 3.12; exit 0; fi
for a in "$@"; do
  if [[ "$a" == "--list-damaged" ]]; then
    printf '%s' "${FAKE_SCAN_OUT:-}"
    exit "${FAKE_SCAN_RC:-0}"
  fi
done
cat >/dev/null
exit 0
"""


def _build(tmp_path: Path, scan_rc: int, scan_out: str = "") -> subprocess.CompletedProcess:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    py = venv / "bin" / "python"
    py.write_text(FAKE_PY)
    py.chmod(0o755)
    calls = tmp_path / "uv-calls.log"
    uv = tmp_path / "uv"
    uv.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{calls}"\n'
                  '[[ "$1" == "--version" ]] && echo "uv 0.0.0-fake"\nexit 0\n')
    uv.chmod(0o755)
    env = {**os.environ, "UV_BIN": str(uv), "ZOE_PY312_VENV": str(venv),
           "ZOE_PY312_MIN_MEM_MB": "0", "FAKE_SCAN_RC": str(scan_rc),
           "FAKE_SCAN_OUT": scan_out}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


def test_failed_damage_scan_aborts_the_build(tmp_path: Path) -> None:
    proc = _build(tmp_path, scan_rc=3)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "damage scan failed" in out
    assert "venv ready" not in out
    # nothing after the failed scan ran: no phase-2 install reached uv
    assert "--no-deps" not in (tmp_path / "uv-calls.log").read_text()


def test_negative_control_clean_scan_completes(tmp_path: Path) -> None:
    """The harness itself must reach the end — otherwise the test above could
    pass because the fakes break the script somewhere else."""
    proc = _build(tmp_path, scan_rc=0)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "venv ready" in out
    assert "--reinstall-package" not in (tmp_path / "uv-calls.log").read_text()


def test_damaged_names_are_reinstalled(tmp_path: Path) -> None:
    proc = _build(tmp_path, scan_rc=0, scan_out="webrtcvad-wheels\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = (tmp_path / "uv-calls.log").read_text()
    assert "--reinstall-package webrtcvad-wheels" in calls
