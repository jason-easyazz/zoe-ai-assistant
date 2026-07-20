"""Repository path helpers.

``zoe_repo_root()`` lived in ``hermes_http.py`` purely for historical reasons —
it has no Hermes semantics whatsoever, it is ``Path(__file__).parents[2]`` with
an env override. Its presence there made six modules (`worktree_bootstrap`, the
four `pipeline_*` helpers, `kanban_adapter`) *look* Hermes-coupled when they only
wanted a path, which inflated the apparent blast radius of retiring Hermes.

Stdlib-only and dependency-free, so it can be imported from anywhere without
pulling in ``runtime_env`` (which ``hermes_http`` imports at module scope).
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["zoe_repo_root"]


def zoe_repo_root() -> str:
    """Repo root for subprocess cwd; honour ZOE_REPO_ROOT or derive from this file."""
    env = os.environ.get("ZOE_REPO_ROOT", "").strip()
    if env:
        return env
    # services/zoe-data/repo_paths.py -> repo root is two levels up from zoe-data.
    return str(Path(__file__).resolve().parents[2])
