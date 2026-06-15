#!/usr/bin/env python3
"""Mark Multica issues done per triage ledger after board-repair PR lands."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FIXED_ON_MAIN = {
    "ZOE-4893",
    "ZOE-4366",
    "ZOE-4352",
    "ZOE-4355",
    "ZOE-4353",
    "ZOE-4413",
    "ZOE-4414",
    "ZOE-4424",
    "ZOE-4428",
    "ZOE-4358",
    "ZOE-4953",
    "ZOE-3127",
    "ZOE-3128",
    "ZOE-266",
    "ZOE-256",
    "ZOE-490",
    "ZOE-934",
    "ZOE-267",
    "ZOE-251",
    "ZOE-253",
    "ZOE-3130",
    "ZOE-3129",
    "ZOE-1054",
    "ZOE-48",
}


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
            ident = item.get("identifier") or ""
            disp = item.get("disposition")
            uid = item.get("uuid")
            if not uid:
                continue
            close = False
            note = item.get("notes", "")
            if disp in ("duplicate", "wont_fix", "monitor"):
                close = True
                note = f"triage:{disp} — {note}"
            elif disp == "config" and ident == "ZOE-48":
                close = True
                note = "documented in docker-compose.modules.yml + .env.example"
            elif ident in FIXED_ON_MAIN:
                close = True
                note = "fixed on fix/multica-board-repair branch (board repair)"
            if not close:
                continue
            print(f"  {ident}: done ({note[:60]})")
            if not args.execute:
                continue
            label_name = "board-repair-closed"
            await client.ensure_label(label_name, existing=labels)
            await client.attach_label(uid, label_name)
            await client.append_issue_note(uid, note)
            await client.update_issue(uid, status="done")
            updated += 1
        return updated

    n = asyncio.run(run())
    if args.execute:
        print(f"Closed/updated {n} issue(s)")
    else:
        print("Dry-run only; pass --execute to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
