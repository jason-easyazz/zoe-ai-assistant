"""No tracked path may be a host-pointing symlink, or a node_modules at all.

2026-11-06: #2251 committed `labs/flue-zoe-telegram-2x/node_modules` as a tracked
symlink to /home/zoe/assistant/labs/flue-zoe-telegram-2x/node_modules (itself, once
checked out on the live box). Deploy checked it out over the real untracked dir and
flue-zoe-telegram crash-looped with ERR_MODULE_NOT_FOUND. It was the worktree trick
(symlink node_modules into a worktree to run TS tests) leaking into a commit; the
`node_modules/` ignore rule has a trailing slash and so never matched a symlink.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HOST_PREFIXES = ("/home/", "/root/", "/Users/", "/mnt/", "/opt/zoe")


def _symlinks_in(entries: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """entries: (mode, path, target). Returns (path, target) offenders."""
    bad = []
    for mode, path, target in entries:
        parts = path.split("/")
        if "node_modules" in parts:
            bad.append((path, "tracked node_modules"))
        elif mode == "120000" and (target.startswith(HOST_PREFIXES) or target.rstrip("/").endswith(parts[-1]) and target.startswith("/")):
            bad.append((path, target))
    return bad


def _tracked() -> list[tuple[str, str, str]]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "-z"], capture_output=True, check=True).stdout
    entries = []
    for rec in out.decode("utf-8", "surrogateescape").split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode = meta.split()[0]
        target = ""
        if mode == "120000":
            blob = meta.split()[1]
            target = subprocess.run(
                ["git", "-C", str(ROOT), "cat-file", "blob", blob], capture_output=True, check=True
            ).stdout.decode("utf-8", "surrogateescape")
        entries.append((mode, path, target))
    return entries


def test_no_tracked_host_symlinks_or_node_modules() -> None:
    entries = _tracked()
    assert len(entries) > 100, "git ls-files returned too little; the scan would be vacuous"
    offenders = _symlinks_in(entries)
    assert not offenders, (
        f"tracked symlink(s) into the host or tracked node_modules: {offenders}. A worktree helper "
        "symlinked node_modules and it got committed; deploy then replaces the real dir with a loop. "
        "`git rm` them (node_modules is gitignored)."
    )


def test_negative_control() -> None:
    live = "/home/zoe/assistant/labs/flue-zoe-telegram-2x/node_modules"
    assert _symlinks_in([("120000", "labs/x/node_modules", live)])
    assert _symlinks_in([("120000", "labs/x/link", "/home/zoe/assistant/foo")])
    assert _symlinks_in([("100644", "labs/x/node_modules/pkg/index.js", "")])
    assert not _symlinks_in([("120000", "docs/rel", "../README.md"), ("100644", "a/b.py", "")])
