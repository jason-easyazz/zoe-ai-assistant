"""Repository path helpers.

``zoe_repo_root()`` is ``Path(__file__).parents[2]`` with an env override.
Stdlib-only and dependency-free, so it can be imported from anywhere without
pulling in ``runtime_env``.
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
