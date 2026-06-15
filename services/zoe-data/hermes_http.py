"""Shared Hermes helpers (gateway auth, CLI paths, repo root)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from runtime_env import bootstrap_runtime_env


def hermes_bin() -> str:
    """Locate the hermes CLI; honour HERMES_BIN override."""
    override = os.environ.get("HERMES_BIN", "").strip()
    if override:
        return override
    found = shutil.which("hermes")
    if found:
        return found
    return os.path.expanduser("~/.local/bin/hermes")


def zoe_repo_root() -> str:
    """Repo root for subprocess cwd; honour ZOE_REPO_ROOT or derive from this file."""
    env = os.environ.get("ZOE_REPO_ROOT", "").strip()
    if env:
        return env
    # services/zoe-data/hermes_http.py -> repo root is two levels up from zoe-data.
    return str(Path(__file__).resolve().parents[2])


def hermes_api_key() -> str:
    bootstrap_runtime_env()
    return (
        os.environ.get("HERMES_API_KEY")
        or os.environ.get("API_SERVER_KEY")
        or ""
    ).strip()


def hermes_auth_headers(*, session_id: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    key = hermes_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    return headers
