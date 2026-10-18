#!/usr/bin/env python3
"""Move the Web Push VAPID private key out of the checkout. DRY RUN by default; never prints the key.

Architecture deep dive B9 (2026-10-18): ``services/zoe-data/data/vapid_keys.json`` (0664) is a private key inside the
working tree. ``routers/push.py`` now reads ``ZOE_VAPID_KEY_PATH`` (default ``~/.zoe/secrets/vapid_keys.json``) first
and the legacy path only as a fallback, so this move needs no restart and loses nothing: the SAME key moves, which
keeps every browser's push subscription valid (a new key would orphan all of them).

  1. copy legacy -> new (dir 0700, file 0600), via a temp file + rename;
  2. re-read both and compare the parsed JSON;
  3. delete the legacy file.
Refuses when the new path already holds a DIFFERENT key (two keys = a decision for the owner, not a script).

    python3 scripts/maintenance/migrate_vapid_keys.py            # dry run
    python3 scripts/maintenance/migrate_vapid_keys.py --apply

Exit 0 done / nothing to do, 1 refused or failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def migrate(legacy: Path, new: Path, apply: bool) -> tuple[int, str]:
    if not legacy.exists():
        return 0, f"nothing to do: no legacy key at {legacy}" + (f" (key at {new})" if new.exists() else "")
    try:
        old = json.loads(legacy.read_text())
    except (OSError, ValueError) as exc:
        return 1, f"refused: legacy key unreadable ({type(exc).__name__})"
    if not {"public_key", "private_key"} <= set(old):
        return 1, "refused: legacy file is not a VAPID key pair"
    if new.exists():
        try:
            same = json.loads(new.read_text()) == old
        except (OSError, ValueError):
            same = False
        if not same:
            return 1, f"refused: {new} already holds a DIFFERENT key; keep one (owner decision), then re-run"
        if apply:
            legacy.unlink()
        return 0, f"{'removed' if apply else 'would remove'} the legacy copy (identical key already at {new})"
    if not apply:
        return 0, f"would move {legacy} -> {new} (0600, dir 0700) and delete the legacy file"
    new.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(new.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=new.parent, prefix=".vapid-")
    with os.fdopen(fd, "w") as fh:
        json.dump(old, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, new)
    if json.loads(new.read_text()) != old:
        return 1, f"FAILED: {new} does not read back identical; legacy left in place"
    legacy.unlink()
    return 0, f"moved to {new} (0600); legacy deleted. No restart needed."


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--legacy", type=Path, default=REPO_ROOT / "services" / "zoe-data" / "data" / "vapid_keys.json")
    ap.add_argument("--new", type=Path,
                    default=Path(os.path.expanduser(os.environ.get("ZOE_VAPID_KEY_PATH")
                                                    or "~/.zoe/secrets/vapid_keys.json")))
    ns = ap.parse_args(argv)
    rc, msg = migrate(ns.legacy, ns.new, ns.apply)
    print(msg + ("" if ns.apply or rc else "\nDRY RUN - pass --apply"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
