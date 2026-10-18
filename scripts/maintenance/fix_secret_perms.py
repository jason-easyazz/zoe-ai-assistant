#!/usr/bin/env python3
"""Tighten the file modes of the box's secrets. DRY RUN by default; prints modes and paths, never contents.

Architecture deep dive B9 (2026-10-18) measured the live checkout's ``.env`` at 0664 (group- and world-readable).
Every reader of these files runs as the owner (the ``zoe`` uid): zoe-data and the Flue sidecars, ``docker compose``
(which reads ``env_file`` on the host, as the invoking user), the self-hosted runner, and the one container that
mounts the checkout (zoe-omnigent) runs as uid 1000 too. So 0600 removes no reader. Containers that read a key
from a ``:ro`` bind mount (zoe-ui's nginx reads ``ssl/``) do so as root, which file modes do not restrict.

Targets (only ever TIGHTENED, never loosened; a file already at or below the target is left alone):
  0600  ``.env`` / ``.env.*`` of the live checkout and ``services|labs|modules/*/``, ``ssl/*.key`` / ``*.pem``,
        ``services/zoe-data/data/vapid_keys.json`` (until migrate_vapid_keys.py moves it)
  0700  ``~/.zoe/env``, ``~/.zoe/env-backups``, ``~/.zoe/secrets``, ``~/.config/zoe-backup``
  0750  ``ssl/`` itself
NOT a target: the cloudflared tunnel credentials (``config/<tunnel-uuid>.json``, 0644). The zoe-cloudflared
container runs as uid 65532 and reads them through a ``:ro`` mount, so 0600 (owner 1000) would take the tunnel down
(checked 2026-10-18: ``docker inspect zoe-cloudflared`` User=65532:65532). The fix there is ownership (``chown
65532`` or a shared group), an owner decision recorded in the runbook, not a chmod.
A SYMLINK always shows 0777 (``ssl/zoe.crt -> cert.pem`` is why the review saw "ssl 777"): its own mode means
nothing, the target's mode is what counts, so symlinks are reported and skipped.

    python3 scripts/maintenance/fix_secret_perms.py           # dry run
    python3 scripts/maintenance/fix_secret_perms.py --apply   # owner step; no restart needed

Exit 0 (dry run reports; --apply also 0 unless a chmod failed, then 1).
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Optional

FILE_GLOBS_600 = (".env", ".env.*", "services/*/.env*", "labs/*/.env*", "modules/*/.env*", "ssl/*.key", "ssl/*.pem",
                  "ssl/*.crt", "services/zoe-data/data/vapid_keys.json")
SKIP = ("*.example", "*.sample")


def plan(home: Path, checkout: Path) -> list[tuple[Path, int, int, str]]:
    """``(path, current mode, target mode, note)`` for every target that is LOOSER than its target."""
    out: list[tuple[Path, int, int, str]] = []
    seen: set[Path] = set()

    def want(p: Path, target: int, note: str = "") -> None:
        if p in seen or not (p.exists() or p.is_symlink()):
            return
        seen.add(p)
        if p.is_symlink():
            out.append((p, 0o777, 0o777, f"symlink -> {os.readlink(p)}: its mode is meaningless, skipped"))
            return
        cur = stat.S_IMODE(p.stat().st_mode)
        if cur & ~target:                # any bit beyond the target = looser
            out.append((p, cur, cur & target, note))

    for g in FILE_GLOBS_600:
        for p in sorted(checkout.glob(g)):
            if (p.is_file() or p.is_symlink()) and not any(p.match(s) for s in SKIP):
                want(p, 0o600)
    for d in (home / ".zoe" / "env", home / ".zoe" / "env-backups", home / ".zoe" / "secrets",
              home / ".config" / "zoe-backup"):
        if d.is_dir():
            want(d, 0o700)
    if (checkout / "ssl").is_dir():
        want(checkout / "ssl", 0o750, "nginx (root in its container) still reads the :ro mount")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    home = Path(os.environ.get("HOME", str(Path.home())))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--checkout", type=Path, default=home / "assistant")
    ns = ap.parse_args(argv)
    rows = plan(home, ns.checkout)
    rc = 0
    for p, cur, tgt, note in rows:
        try:
            shown = "~/" + str(p.relative_to(home))
        except ValueError:
            shown = str(p)
        if cur == tgt:
            print(f"skip  {shown}  {note}")
            continue
        print(f"{'chmod' if ns.apply else 'would'} {oct(cur)[2:]:>4} -> {oct(tgt)[2:]:>4}  {shown}  {note}")
        if ns.apply:
            try:
                os.chmod(p, tgt)
            except OSError as exc:
                print(f"  FAILED: {exc}", file=sys.stderr)
                rc = 1
    if not rows:
        print("nothing looser than its target")
    if not ns.apply:
        print("DRY RUN - pass --apply to chmod")
    return rc


if __name__ == "__main__":
    sys.exit(main())
