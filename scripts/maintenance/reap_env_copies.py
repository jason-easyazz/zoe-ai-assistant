#!/usr/bin/env python3
"""Find stale copies of ``.env*`` secret files and (with ``--apply``) delete them. DRY RUN by default; prints no secret.

Architecture deep dive B9 (2026-10-18): 115 ``.env*`` copies across worktrees and backups, 11 ``.env.bak*`` beside the
live one. Every copy is the household's secrets in one more place, and a copy taken before a rotation still holds the
OLD values (the Telegram token that leaked once is still in some of them). The live files are never touched.

Classes (paths print home-relative; values never print, only ``same`` / ``differs`` against the live file):
  live      the live checkout's ``.env``, ``services|labs|modules/*/.env`` - KEPT, never a candidate
  retired   the ``.env`` of a lab in RETIRED_LABS (the lab is gone from the repo; its secrets are not) - STALE
  worktree  a ``.env*`` inside a git worktree (a ``.git`` FILE at its root). STALE when the worktree is orphaned (its
            gitdir is gone) or idle for ``--idle-days`` (default 7; newest of the gitdir's index / HEAD / logs/HEAD)
            and no running process has its cwd inside it. Read from the files only: no git command is run.
  backup    ``.env.bak*`` / ``*.env.bak*`` beside the live files, and ``.env*`` under ``~/.zoe-backups``,
            ``~/.zoe/env-backups``, ``~/.zoe/backups``. STALE after ``--backup-days`` (default 14, the forget-reach
            rule for ad-hoc backups).
``*.example`` / ``*.sample`` files hold no secrets and are ignored.

    python3 scripts/maintenance/reap_env_copies.py            # dry run: the table + what --apply would delete
    python3 scripts/maintenance/reap_env_copies.py --apply    # delete the STALE rows (owner step)

Exit 0; 2 on bad invocation.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

ENV_NAMES = (".env", ".env.*", "*.env.bak*")
IGNORE = ("*.example", "*.sample", "*.template")
# labs/<x>/.env of a lab that no longer exists in the repo (left behind in the live checkout as untracked dirs)
RETIRED_LABS = {
    "flue-zoe-brain": "Flue 1.x brain sidecar, retired 2026-08-10 (flue-zoe-brain-2x is live)",
    "flue-zoe-telegram": "Flue 1.x Telegram bot, retired (flue-zoe-telegram-2x is live)",
}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "venvs", ".mypy_cache", ".pytest_cache"}


@dataclass
class Row:
    path: Path
    klass: str
    age_days: float
    same_as_live: Optional[bool]
    stale: bool
    why: str


def is_env(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in ENV_NAMES) and not any(fnmatch.fnmatch(name, p) for p in IGNORE)


def walk_envs(root: Path, max_depth: int = 6) -> Iterable[Path]:
    if not root.is_dir():
        return
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        depth = len(Path(dirpath).parts) - base
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and depth < max_depth
                       and not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            p = Path(dirpath) / fn
            if is_env(fn) and p.is_file() and not p.is_symlink():
                yield p


def sha(p: Path) -> Optional[str]:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def live_files(checkout: Path) -> dict[str, Path]:
    """name relative to the checkout -> path, for the live secret files that must never be reaped."""
    out = {}
    for g in (".env", "services/*/.env", "labs/*/.env", "modules/*/.env"):
        for p in checkout.glob(g):
            if p.is_file():
                out[str(p.relative_to(checkout))] = p
    return out


def worktree_roots(dirs: Iterable[Path]) -> list[Path]:
    out = []
    for d in dirs:
        if d.is_dir():
            out += [w for w in sorted(d.iterdir()) if (w / ".git").is_file()]
    return out


def worktree_idle_days(wt: Path, now: float) -> Optional[float]:
    """Days since the worktree's gitdir last changed; None = orphaned (gitdir missing)."""
    try:
        line = (wt / ".git").read_text().strip()
    except OSError:
        return None
    gitdir = Path(line.split(":", 1)[1].strip()) if line.startswith("gitdir:") else None
    if gitdir is None or not gitdir.is_dir():
        return None
    # index + HEAD only: logs/HEAD is touched by fleet-wide ref maintenance (measured: 77 idle worktrees at once)
    mt = [f.stat().st_mtime for f in (gitdir / "index", gitdir / "HEAD") if f.exists()]
    return (now - max(mt)) / 86400 if mt else None


def busy_dirs() -> list[Path]:
    out = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            out.append(Path(os.readlink(p / "cwd")))
        except OSError:
            continue
    return out


def classify(home: Path, checkout: Path, worktree_dirs: list[Path], backup_dirs: list[Path], idle_days: float,
             backup_days: float, now: Optional[float] = None, busy: Optional[list[Path]] = None) -> list[Row]:
    now = now or time.time()
    busy = busy_dirs() if busy is None else busy
    live = live_files(checkout)
    live_resolved = {p.resolve() for p in live.values()}
    rows: list[Row] = []
    seen: set[Path] = set()

    def same(rel: str, p: Path) -> Optional[bool]:
        lp = live.get(rel) or live.get(".env")
        return None if lp is None else sha(lp) == sha(p)

    def same_beside(p: Path) -> Optional[bool]:
        lp = p.parent / ".env"
        return None if not lp.is_file() or p.parent == p else sha(lp) == sha(p)

    for rel, p in sorted(live.items()):
        lab = rel.split("/")[1] if rel.startswith("labs/") else ""
        if lab in RETIRED_LABS:
            rows.append(Row(p, "retired", (now - p.stat().st_mtime) / 86400, None, True, RETIRED_LABS[lab]))
        else:
            rows.append(Row(p, "live", (now - p.stat().st_mtime) / 86400, None, False, "the live file"))
        seen.add(p.resolve())
    for wt in worktree_roots(worktree_dirs):
        idle = worktree_idle_days(wt, now)
        in_use = any(b == wt or wt in b.parents for b in busy)
        for p in walk_envs(wt):
            if p.resolve() in seen or p.resolve() in live_resolved:
                continue
            seen.add(p.resolve())
            rel = str(p.relative_to(wt))
            if in_use:
                stale, why = False, "a running process works in this worktree"
            elif idle is None:
                stale, why = True, "orphaned worktree (its gitdir is gone)"
            elif idle >= idle_days:
                stale, why = True, f"worktree idle {idle:.0f} d"
            else:
                stale, why = False, f"worktree active ({idle:.1f} d)"
            rows.append(Row(p, "worktree", (now - p.stat().st_mtime) / 86400, same(rel, p), stale, why))
    backup_roots = list(backup_dirs)
    beside = [p for g in (".env.bak*", "*.env.bak*", "services/*/.env.bak*", "labs/*/.env.bak*") for p in checkout.glob(g)]
    cands = [(p, None) for p in beside] + [(p, r) for r in backup_roots for p in walk_envs(r)]
    for p, _root in cands:
        if not p.is_file() or p.resolve() in seen:
            continue
        seen.add(p.resolve())
        age = (now - p.stat().st_mtime) / 86400
        stale = age >= backup_days
        rows.append(Row(p, "backup", age, same_beside(p) if _root is None else same(".env", p), stale,
                        f"backup {age:.0f} d old" + ("" if stale else f" (< {backup_days:.0f} d)")))
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    home = Path(os.environ.get("HOME", str(Path.home())))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="delete the STALE rows")
    ap.add_argument("--checkout", type=Path, default=home / "assistant")
    ap.add_argument("--idle-days", type=float, default=7)
    ap.add_argument("--backup-days", type=float, default=14)
    ns = ap.parse_args(argv)
    wt_dirs = [ns.checkout / ".claude" / "worktrees", ns.checkout / ".worktrees", home / ".worktrees",
               home / "worktrees"]
    bk_dirs = [home / ".zoe-backups", home / ".zoe" / "env-backups", home / ".zoe" / "backups"]
    rows = classify(home, ns.checkout, wt_dirs, bk_dirs, ns.idle_days, ns.backup_days)

    def show(p: Path) -> str:
        try:
            return "~/" + str(p.relative_to(home))
        except ValueError:
            return str(p)
    for r in rows:
        same = {None: "-", True: "same", False: "differs"}[r.same_as_live]
        print(f"{'STALE' if r.stale else 'keep ':5} {r.klass:8} {r.age_days:6.0f}d {same:7} {show(r.path)}  ({r.why})")
    stale = [r for r in rows if r.stale]
    print(f"\n{len(rows)} .env* files: {sum(r.klass == 'live' for r in rows)} live, "
          f"{sum(r.klass == 'retired' for r in rows)} of retired labs, "
          f"{sum(r.klass == 'worktree' for r in rows)} in worktrees, {sum(r.klass == 'backup' for r in rows)} backups; "
          f"{len(stale)} stale ({sum(r.same_as_live is False for r in stale)} differ from the live file = old values)")
    if not ns.apply:
        print("DRY RUN - pass --apply to delete the STALE rows")
        return 0
    for r in stale:
        try:
            r.path.unlink()
            print(f"deleted {show(r.path)}")
        except OSError as exc:
            print(f"could not delete {show(r.path)}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
