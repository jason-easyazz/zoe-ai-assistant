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


# --- --dry-run must resolve the phase-2 (--no-deps) pins too ----------------
# The dry-run is what validate.yml's deps-resolvable job runs. It used to
# resolve only the manifest, so an unavailable PHASE2_NO_DEPS pin passed CI and
# failed first in the operator's real build (Codex, #1706). The fake uv below
# fails `pip compile --no-deps` when its input names a pin listed in
# FAKE_UV_UNRESOLVABLE — standing in for PyPI answering "no such version".

FAKE_UV = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_UV_LOG"
[[ "$1" == "--version" ]] && { echo "uv 0.0.0-fake"; exit 0; }
if [[ "$1 $2" == "pip compile" ]]; then
  src="$3"; out=""; nodeps=0; prev=""
  for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; [[ "$a" == "--no-deps" ]] && nodeps=1; prev="$a"; done
  if (( nodeps )); then
    echo "NO-DEPS INPUT: $(tr '\\n' ' ' < "$src")" >> "$FAKE_UV_LOG"
    for bad in ${FAKE_UV_UNRESOLVABLE:-}; do
      grep -qxF "$bad" "$src" && { echo "error: $bad is unsatisfiable" >&2; exit 1; }
    done
  fi
  [[ -n "$out" && "$out" != /dev/null ]] && echo "pkg==1.0" > "$out"
fi
exit 0
"""


def _dry_run(tmp_path: Path, unresolvable: str = "") -> tuple[subprocess.CompletedProcess, str]:
    uv = tmp_path / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    log = tmp_path / "uv.log"
    env = {**os.environ, "UV_BIN": str(uv), "FAKE_UV_LOG": str(log),
           "FAKE_UV_UNRESOLVABLE": unresolvable, "ZOE_PY312_VENV": str(tmp_path / "venv")}
    proc = subprocess.run(["bash", str(SCRIPT), "--dry-run"], env=env,
                          capture_output=True, text=True, timeout=60)
    return proc, (log.read_text() if log.exists() else "")


def _phase2_pins() -> list[str]:
    import re
    m = re.search(r"^PHASE2_NO_DEPS=\((.*)\)$", SCRIPT.read_text(), re.MULTILINE)
    assert m, "PHASE2_NO_DEPS array not found in the script"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_dry_run_fails_when_a_phase2_pin_does_not_resolve(tmp_path: Path) -> None:
    pins = _phase2_pins()
    assert pins, "vacuity guard: the script declares phase-2 pins"
    proc, log = _dry_run(tmp_path, unresolvable=pins[0])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "phase 2" in out and "does NOT resolve" in out, out


def test_negative_control_dry_run_resolves_the_real_phase2_pins(tmp_path: Path) -> None:
    proc, log = _dry_run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # the no-deps resolve actually ran, for the 3.12 aarch64 target, on the real pins
    nodeps = [line for line in log.splitlines() if "--no-deps" in line and "compile" in line]
    assert nodeps and "--python-version 3.12" in nodeps[0] and "aarch64" in nodeps[0], log
    for pin in _phase2_pins():
        assert pin in log.split("NO-DEPS INPUT:", 1)[1], log
