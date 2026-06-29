#!/usr/bin/env python3
"""
Dry-run-first cleanup for generated Zoe artifacts.

The old script removed source routers, configs, backups, and checkpoints without
confirmation. This replacement only deletes allowlisted generated artifacts, never
source files. Use --execute to delete safe generated files. Add
--include-state-artifacts plus typed confirmation to delete generated state such
as backups/checkpoints/training DBs.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


STATE_CONFIRMATION = "DELETE GENERATED STATE"
SOURCE_SUFFIXES = {
    ".c",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".html",
    ".js",
    ".jsx",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".ts",
    ".tsx",
    ".yml",
    ".yaml",
}
SAFE_ROOT_FILES = (
    "database_analysis.json",
    "database_audit_report.json",
    "database_violations.json",
)
STATE_PATHS = (
    "backups",
    "checkpoints",
    "logs",
    "data/training.db",
)


@dataclass(frozen=True)
class Candidate:
    path: Path
    reason: str
    stateful: bool = False


def get_path_size_mb(path: Path) -> int:
    if not path.exists():
        return 0
    result = subprocess.run(["du", "-sm", str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return 0
    return int(result.stdout.split()[0])


def is_source_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES


def iter_candidates(repo: Path, include_state: bool) -> tuple[list[Candidate], list[Candidate]]:
    candidates: list[Candidate] = []
    skipped_state: list[Candidate] = []

    for pycache in repo.rglob("__pycache__"):
        if pycache.is_dir():
            candidates.append(Candidate(pycache, "Python bytecode cache"))

    for pattern, reason in (("*.pyc", "Python bytecode"), ("*.pyo", "Python optimized bytecode")):
        for path in repo.rglob(pattern):
            if path.is_file():
                candidates.append(Candidate(path, reason))

    for rel in SAFE_ROOT_FILES:
        path = repo / rel
        if path.exists():
            candidates.append(Candidate(path, "generated audit/report artifact"))

    for rel in STATE_PATHS:
        path = repo / rel
        if not path.exists():
            continue
        candidate = Candidate(path, "generated state artifact", stateful=True)
        if include_state:
            candidates.append(candidate)
        else:
            skipped_state.append(candidate)

    seen: set[Path] = set()
    unique: list[Candidate] = []
    for candidate in sorted(candidates, key=lambda item: len(item.path.resolve().parts)):
        resolved = candidate.path.resolve()
        if resolved in seen:
            continue
        if any(parent in seen for parent in resolved.parents):
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique, skipped_state


def assert_safe_candidate(repo: Path, candidate: Candidate) -> None:
    path = candidate.path.resolve()
    if not path.is_relative_to(repo):
        raise SystemExit(f"Refusing path outside repo: {path}")
    if is_source_file(path):
        raise SystemExit(f"Refusing to delete source/config file: {path}")


def remove_candidate(candidate: Candidate) -> None:
    path = candidate.path
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean allowlisted generated Zoe artifacts.")
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root to clean. Defaults to current directory.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete allowlisted generated artifacts. Default is dry-run.",
    )
    parser.add_argument(
        "--include-state-artifacts",
        action="store_true",
        help="Also delete generated state artifacts such as backups/checkpoints/logs/training DB.",
    )
    parser.add_argument(
        "--confirm-state",
        default="",
        help=f"Required with --include-state-artifacts and --execute: {STATE_CONFIRMATION!r}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        raise SystemExit(f"Refusing to clean non-repository path: {repo}")
    if args.execute and args.include_state_artifacts and args.confirm_state != STATE_CONFIRMATION:
        raise SystemExit(f"Refusing state cleanup without --confirm-state {STATE_CONFIRMATION!r}")

    candidates, skipped_state = iter_candidates(repo, args.include_state_artifacts)
    total_mb = 0

    print("Aggressive cleanup")
    print(f"Repo: {repo}")
    print(f"Mode: {'execute' if args.execute else 'dry-run'}")
    print("Source/config deletion: retired; this script never deletes source files or configs.")

    if skipped_state:
        print("\nStateful generated artifacts skipped:")
        for candidate in skipped_state:
            print(f"  - {candidate.path.relative_to(repo)} ({get_path_size_mb(candidate.path)}MB)")
        print(f"Use --include-state-artifacts --confirm-state {STATE_CONFIRMATION!r} to include them.")

    print("\nDeletion plan:")
    if not candidates:
        print("  No allowlisted generated artifacts found.")
        return 0

    for candidate in candidates:
        assert_safe_candidate(repo, candidate)
        size_mb = get_path_size_mb(candidate.path)
        total_mb += size_mb
        marker = "STATE " if candidate.stateful else ""
        print(f"  - {marker}{candidate.path.relative_to(repo)} ({size_mb}MB): {candidate.reason}")
        if args.execute:
            remove_candidate(candidate)

    action = "Deleted" if args.execute else "Would delete"
    print(f"\n{action} {len(candidates)} allowlisted paths, approximately {total_mb}MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
