#!/usr/bin/env python3
"""Apply triage dispositions to Multica (duplicate/wont_fix/monitor done)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Apply the printed changes.")
    args = parser.parse_args()
    _load_dotenv()
    triage_path = ROOT / ".cursor" / "tmp" / "multica-triage.json"
    if not triage_path.exists():
        print("Run generate_multica_triage.py first", file=sys.stderr)
        return 1

    data = json.loads(triage_path.read_text())
    sys.path.insert(0, str(ROOT / "services" / "zoe-data"))
    from multica_client import get_multica_client

    client = get_multica_client()
    if not client.is_configured():
        print("Multica not configured", file=sys.stderr)
        return 1

    import asyncio

    async def run() -> int:
        updated = 0
        labels = await client.list_labels()
        for item in data.get("issues", []):
            disp = item.get("disposition")
            if disp not in ("duplicate", "wont_fix", "monitor"):
                continue
            uid = item.get("uuid")
            if not uid:
                continue
            status = "done" if disp in ("duplicate", "wont_fix", "monitor") else None
            note = item.get("notes", disp)
            print(f"  {item.get('identifier')}: {disp} -> {status}")
            if not args.execute:
                continue
            label_name = disp.replace("_", "-")
            await client.ensure_label(label_name, existing=labels)
            await client.attach_label(uid, label_name)
            await client.append_issue_note(uid, f"Triage: {disp} - {note}")
            await client.update_issue(uid, status=status)
            updated += 1
        return updated

    n = asyncio.run(run())
    if args.execute:
        print(f"Updated {n} issue(s)")
    else:
        print("Dry-run only; pass --execute to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
