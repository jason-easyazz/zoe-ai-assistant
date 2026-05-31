#!/usr/bin/env python3
"""Copy Zoe Multica/Hermes keys into ~/.hermes/.env when missing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZOE_ENV = ROOT / "services" / "zoe-data" / ".env"
HERMES_ENV = Path.home() / ".hermes" / ".env"

SYNC_KEYS = (
    "MULTICA_BASE_URL",
    "MULTICA_API_TOKEN",
    "MULTICA_WORKSPACE_ID",
    "HERMES_API_KEY",
    "API_SERVER_KEY",
    "GITHUB_TOKEN",
)


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip().strip('"').strip("'")
    return out


def main() -> int:
    source = _parse_env(ZOE_ENV)
    if not source:
        print(f"No env at {ZOE_ENV}", file=sys.stderr)
        return 1

    existing_lines: list[str] = []
    existing_keys: set[str] = set()
    if HERMES_ENV.is_file():
        for raw in HERMES_ENV.read_text(encoding="utf-8").splitlines():
            existing_lines.append(raw)
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                existing_keys.add(line.split("=", 1)[0].strip())

    appended = []
    for key in SYNC_KEYS:
        value = source.get(key, "").strip()
        if not value or key in existing_keys:
            continue
        appended.append(f"{key}={value}")
        existing_keys.add(key)

    if not appended:
        print("Hermes .env already has required keys (nothing to add)")
        return 0

    HERMES_ENV.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(existing_lines)
    if body and not body.endswith("\n"):
        body += "\n"
    body += "\n# Synced from Zoe zoe-data .env by sync_hermes_env_from_zoe.py\n"
    body += "\n".join(appended) + "\n"
    HERMES_ENV.write_text(body, encoding="utf-8")
    print(f"Added {len(appended)} key(s) to {HERMES_ENV}: {', '.join(k.split('=')[0] for k in appended)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
