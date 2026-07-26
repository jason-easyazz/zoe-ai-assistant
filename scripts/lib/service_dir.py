"""Resolve the `services/zoe-data` dir holding the LIVE `.env` — ONE ladder.

Every voice-harness entrypoint (`scripts/maintenance/voice_regression_probe.py`,
`scripts/perf/measure_voice.py`, `scripts/perf/measure_tts.py`) needs the same
answer to the same question: *which directory's `.env` reaches the live service?*
The documented agent workflow runs these from a git WORKTREE, whose
`services/zoe-data/.env` is gitignored and therefore absent — so a naive
"repo-root-relative" default skips with "no .env in <worktree>/services/zoe-data"
and the harness silently measures nothing.

This module is the single home of that resolution so the entrypoints cannot
drift apart. The probe invokes measure_voice as a SUBPROCESS, so a shared third
module (rather than one script importing another) is also what keeps the import
graph acyclic.

Import convention (scripts/ is not a package — see the sibling-import pattern in
`scripts/maintenance/pi_intent_fleet_benchmark.py`)::

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    from service_dir import resolve_service_dir

DOCTRINE — this resolver fixes the DEFAULT, never the failure mode. When no
`.env` resolves anywhere it returns the in-tree default so the EXISTING loud
skip/error still fires downstream (measure_voice skips without results -> the
probe reports status=error / exit 2). A skip must never be mistaken for a pass.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# scripts/lib/service_dir.py -> repo root is two levels up.
REPO = Path(__file__).resolve().parents[2]


def main_worktree_root() -> Path | None:
    """The MAIN checkout of this repo, or None when it can't be determined.

    Resolved via git's COMMON dir (shared by every linked worktree) rather than a
    hardcoded host path: from any worktree, `--git-common-dir` points at the main
    checkout's `.git`, so its parent is the main checkout itself. In the main
    checkout it resolves to that same checkout, making this a no-op there."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(REPO), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()).parent


def service_dir_candidates() -> list[Path]:
    """Service dirs that may hold the LIVE `.env`, in resolution order:

    1. ``REPO/services/zoe-data`` — the in-tree run (and the live checkout itself);
    2. ``<main worktree>/services/zoe-data`` — the documented agent workflow runs
       the harness from a git WORKTREE, where `services/zoe-data/.env` is
       gitignored and therefore absent.
    """
    candidates = [REPO / "services" / "zoe-data"]
    main_root = main_worktree_root()
    if main_root is not None:
        main_service_dir = main_root / "services" / "zoe-data"
        if main_service_dir not in candidates:
            candidates.append(main_service_dir)
    return candidates


def resolve_service_dir(explicit: str | None) -> Path:
    """Resolve --service-dir: the dir whose `.env` reaches the LIVE service.

    Ladder: an explicit ``--service-dir`` ALWAYS wins (the operator's choice is
    never second-guessed, so a deliberate bad path still reaches the loud error);
    otherwise the first candidate (see `service_dir_candidates`) that actually
    has a `.env`.

    When nothing resolves we deliberately return the in-tree default so the
    EXISTING loud error still fires downstream. See the module docstring."""
    if explicit:
        return Path(explicit)
    candidates = service_dir_candidates()
    for candidate in candidates:
        if (candidate / ".env").is_file():
            return candidate
    return candidates[0]


SERVICE_DIR_HELP = (
    "services/zoe-data dir holding the LIVE .env. Default: this repo's "
    "services/zoe-data if it has a .env, else the MAIN worktree's "
    "(so a run from a git worktree needs no flag)."
)
