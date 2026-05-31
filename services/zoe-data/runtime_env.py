"""Load Zoe runtime secrets into os.environ for subprocess MCP workers."""

from __future__ import annotations

import os
from pathlib import Path

_ENV_BOOTSTRAPPED = False

# Order matters: service .env first, then repo root, then Hermes home.
_ENV_FILES = (
    "/home/zoe/assistant/services/zoe-data/.env",
    "/home/zoe/assistant/.env",
    "/home/zoe/.hermes/.env",
)

# Keys MCP/background workers need when spawned without systemd EnvironmentFile.
_BOOTSTRAP_KEYS = (
    "HERMES_API_KEY",
    "API_SERVER_KEY",
    "HERMES_API_URL",
    "MULTICA_BASE_URL",
    "MULTICA_API_TOKEN",
    "MULTICA_WORKSPACE_ID",
    "POSTGRES_URL",
    "ZOE_INTERNAL_TOKEN",
)


def bootstrap_runtime_env() -> None:
    """Populate missing env vars from known Zoe/Hermes .env files."""
    global _ENV_BOOTSTRAPPED
    if _ENV_BOOTSTRAPPED:
        return
    _ENV_BOOTSTRAPPED = True

    for path in _ENV_FILES:
        env_path = Path(path)
        if not env_path.is_file():
            continue
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value.strip().strip('"').strip("'")
        except OSError:
            continue
