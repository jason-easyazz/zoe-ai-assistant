#!/usr/bin/env python3
"""
Safely rewrite Zoe git history in a disposable mirror clone.

Default mode is a dry-run plan. Execution is only allowed in an explicit bare
mirror clone, with typed confirmation. Remote force-push is a separate opt-in.

Safe path:
  git clone --mirror git@github.com:jason-easyazz/zoe-ai-assistant.git /tmp/zoe-history-cleanup.git
  scripts/maintenance/git_history_cleanup.py --mirror /tmp/zoe-history-cleanup.git
  scripts/maintenance/git_history_cleanup.py --mirror /tmp/zoe-history-cleanup.git --execute \
    --confirm "REWRITE HISTORY IN DISPOSABLE MIRROR"
  scripts/maintenance/git_history_cleanup.py --mirror /tmp/zoe-history-cleanup.git --execute --push \
    --confirm "REWRITE HISTORY IN DISPOSABLE MIRROR" \
    --confirm-push "FORCE PUSH REWRITTEN HISTORY"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_LARGE_FILES = (
    "data/backup/performance_20251004_100047.db",
    "data/zoe.db.backup-20251018-142054",
    "data/zoe.db",
    "mcp_test_env/lib/python3.11/site-packages/pydantic_core/_pydantic_core.cpython-311-aarch64-linux-gnu.so",
)
EXECUTE_CONFIRMATION = "REWRITE HISTORY IN DISPOSABLE MIRROR"
PUSH_CONFIRMATION = "FORCE PUSH REWRITTEN HISTORY"
FORBIDDEN_MIRRORS = {
    Path("/home/zoe/assistant").resolve(),
    Path("/workspace").resolve(),
}


def run_git(mirror: Path, args: list[str], description: str, dry_run: bool) -> str:
    cmd = ["git", "-C", str(mirror), *args]
    print(f"{'DRY-RUN' if dry_run else 'RUN'}: {description}")
    print("  " + " ".join(cmd))
    if dry_run:
        return ""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"FAILED: {description}\n{result.stderr.strip()}")
    if result.stdout.strip():
        print(result.stdout.strip())
    return result.stdout


def git_output(mirror: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(mirror), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def require_disposable_mirror(mirror: Path) -> Path:
    mirror = mirror.expanduser().resolve()
    if mirror in FORBIDDEN_MIRRORS:
        raise SystemExit(f"Refusing to operate on live checkout: {mirror}")
    if not mirror.exists():
        raise SystemExit(f"Mirror path does not exist: {mirror}")
    if not (mirror / "objects").exists() or not (mirror / "refs").exists():
        raise SystemExit(f"Not a bare git repository: {mirror}")
    is_bare = git_output(mirror, ["rev-parse", "--is-bare-repository"])
    if is_bare != "true":
        raise SystemExit(f"Refusing non-bare repository: {mirror}")
    is_mirror = git_output(mirror, ["config", "--bool", "remote.origin.mirror"])
    if is_mirror != "true":
        raise SystemExit(
            "Refusing repository that is not an explicit mirror clone "
            "(expected remote.origin.mirror=true)."
        )
    return mirror


def git_size(mirror: Path) -> str:
    result = subprocess.run(["du", "-sh", str(mirror)], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite Zoe git history only inside a disposable mirror clone.",
    )
    parser.add_argument("--mirror", required=True, help="Path to a bare disposable mirror clone.")
    parser.add_argument("--execute", action="store_true", help="Actually rewrite the local mirror.")
    parser.add_argument("--push", action="store_true", help="Force-push rewritten branches and tags.")
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Required with --execute: {EXECUTE_CONFIRMATION!r}",
    )
    parser.add_argument(
        "--confirm-push",
        default="",
        help=f"Required with --push: {PUSH_CONFIRMATION!r}",
    )
    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        help="Path to remove from all history. May be repeated. Defaults to known large files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mirror = require_disposable_mirror(Path(args.mirror))
    dry_run = not args.execute
    files = tuple(args.files or DEFAULT_LARGE_FILES)

    if args.execute and args.confirm != EXECUTE_CONFIRMATION:
        raise SystemExit(f"Refusing to execute without --confirm {EXECUTE_CONFIRMATION!r}")
    if args.push and not args.execute:
        raise SystemExit("--push requires --execute")
    if args.push and args.confirm_push != PUSH_CONFIRMATION:
        raise SystemExit(f"Refusing to force-push without --confirm-push {PUSH_CONFIRMATION!r}")

    print("Git history cleanup")
    print(f"Mirror: {mirror}")
    print(f"Mode: {'execute' if args.execute else 'dry-run'}")
    print(f"Initial mirror size: {git_size(mirror)}")
    print("Files targeted for history removal:")
    for file_path in files:
        print(f"  - {file_path}")

    for file_path in files:
        run_git(
            mirror,
            [
                "filter-branch",
                "--force",
                "--index-filter",
                f"git rm --cached --ignore-unmatch -- {file_path}",
                "--prune-empty",
                "--tag-name-filter",
                "cat",
                "--",
                "--all",
            ],
            f"remove {file_path} from all history",
            dry_run,
        )

    run_git(
        mirror,
        ["for-each-ref", "--format=delete %(refname)", "refs/original"],
        "list filter-branch backup refs to delete with git update-ref --stdin",
        dry_run,
    )
    if not dry_run:
        refs = git_output(mirror, ["for-each-ref", "--format=delete %(refname)", "refs/original"])
        if refs:
            result = subprocess.run(
                ["git", "-C", str(mirror), "update-ref", "--stdin"],
                input=refs,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise SystemExit(result.stderr.strip())

    run_git(mirror, ["reflog", "expire", "--expire=now", "--all"], "expire reflogs", dry_run)
    run_git(mirror, ["gc", "--prune=now", "--aggressive"], "garbage collect mirror", dry_run)

    if args.push:
        run_git(mirror, ["push", "origin", "--force", "--all"], "force-push all branches", dry_run)
        run_git(mirror, ["push", "origin", "--force", "--tags"], "force-push all tags", dry_run)
    else:
        print("Remote push skipped. Add --push plus typed push confirmation after reviewing the mirror.")

    print(f"Final mirror size: {git_size(mirror)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
