"""Shared Hermes helpers (gateway auth, CLI path).

``zoe_repo_root`` moved to ``repo_paths.py`` — it never had Hermes semantics,
and living here made six path-only modules look Hermes-coupled.
"""

from __future__ import annotations

import os
import shutil

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
