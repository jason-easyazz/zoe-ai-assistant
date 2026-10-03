"""The ONE interpreter ladder for the voice gate: run on the zoe-data SERVICE's.

The replay imports zoe-data in-process, so its interpreter decides which chromadb
opens the palace. B0.8 → 2026-10-03 the probe ran on /usr/bin/python3 (chromadb
0.6.3) against the 1.x palace and scored brain turns WITHOUT recall.

Ladder (first wins; the source is reported): explicit `--python` → `ZOE_PROBE_PYTHON`
→ the zoe-data unit's ExecStart (`scripts/deploy/zoe_data_python.sh`: systemd says
what RUNS; venv presence does not) → the B0.7 venv if systemd can't answer → the
current interpreter (off-host / CI).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

REPO = Path(__file__).resolve().parents[2]
AUTHORITY = REPO / "scripts" / "deploy" / "zoe_data_python.sh"
VENV_PYTHON = Path.home() / ".zoe" / "venvs" / "zoe-data-py312" / "bin" / "python"
ENV_VAR = "ZOE_PROBE_PYTHON"


def unit_python() -> str | None:
    """What the zoe-data unit is configured to exec, or None when systemd can't say."""
    try:
        proc = subprocess.run(["bash", str(AUTHORITY)], capture_output=True,
                              text=True, timeout=20)
    except Exception:  # noqa: BLE001 — any failure to ask = "systemd can't say"
        return None
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def _usable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


_PY3_TOKEN = "zoe-service-python-3-ok"
_PY3_CHECK = f"import sys; print({_PY3_TOKEN!r} if sys.version_info[0] == 3 else 'no')"


def runs_python(path: str) -> bool:
    """Can this executable actually run Python 3? An executable that is not a
    Python (`/bin/true`, a stray shell script) would be exec'd by the probe's
    re-exec and exit green WITHOUT writing the result artifact — a scheduled job
    that looks fine while no replay ran (Codex P2, #1811). Exit status alone is
    NOT proof: `/bin/true -c anything` exits 0 (the negative control that caught
    the first version of this check). The candidate must PRINT the token."""
    try:
        proc = subprocess.run([path, "-c", _PY3_CHECK], capture_output=True,
                              text=True, timeout=15)
    except Exception:  # noqa: BLE001 — cannot exec / hangs = not a Python we can trust
        return False
    return proc.returncode == 0 and _PY3_TOKEN in proc.stdout


def resolve_service_python(explicit: str | None = None, *,
                           env: Mapping[str, str] | None = None,
                           query: Callable[[], str | None] = unit_python,
                           venv: Path = VENV_PYTHON,
                           python_ok: Callable[[str], bool] = runs_python) -> tuple[str, str]:
    """(interpreter path, source). An explicit choice that is not an executable
    Python 3 is a hard error — silently falling back would measure a stack nobody
    asked for, and exec'ing a non-Python would report nothing at all."""
    env = os.environ if env is None else env
    for value, source in ((explicit, "--python"), (env.get(ENV_VAR, "").strip(), ENV_VAR)):
        if value:
            if not _usable(value) or not python_ok(value):
                raise SystemExit(f"{source}={value!r} is not an executable Python 3 interpreter")
            return value, source
    unit = query()
    if unit and _usable(unit) and python_ok(unit):
        return unit, "zoe-data unit"
    if _usable(str(venv)) and python_ok(str(venv)):
        return str(venv), "zoe-data venv (systemd unavailable)"
    return sys.executable, "current interpreter (service interpreter unknown)"


def same_python(a: str, b: str) -> bool:
    """Path identity WITHOUT resolving symlinks: a venv's bin/python links to the
    base interpreter, but it is a different site-packages — realpath would equate them."""
    return os.path.normpath(os.path.abspath(a)) == os.path.normpath(os.path.abspath(b))
