#!/usr/bin/env python3
"""One-time bulk-close Multica Autopilot: wrapper noise (todo/in_progress/in_review)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())


async def _main() -> int:
    _load_dotenv()
    from multica_autopilot_sync import close_stale_autopilot_wrappers

    n = await close_stale_autopilot_wrappers(min_age_hours=0)
    print(f"Closed {n} Autopilot wrapper issue(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
