"""Serena must ignore BOTH worktree roots, or start-up walks every nested repo.

Measured 2026-07-22: `.worktrees/` (34 nested checkouts, 2.1 GB) was absent from
`ignored_paths` while `.claude/worktrees/` was present. Serena therefore spent
15+ minutes collecting .gitignore files from the nested repos, sat at ~1 GB RSS
before serving a single request, and looked permanently wedged — which also
tripped the shared-server recycle threshold during its own warm-up (#1511).

A config typo with a 15-minute blast radius is exactly the kind of thing that
should fail a test rather than be rediscovered from a log at midnight.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

_PROJECT_YML = pathlib.Path(__file__).resolve().parents[2] / ".serena" / "project.yml"

# Every directory that holds nested git checkouts. Add to this list, not to a
# comment, when a new worktree root appears.
_WORKTREE_ROOTS = (".claude/worktrees", ".worktrees")


def _ignored() -> list[str]:
    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8")) or {}
    return list(data.get("ignored_paths") or [])


def test_project_yml_is_readable():
    # Guard: a malformed file must fail loudly here, not silently ignore nothing.
    assert _ignored(), "ignored_paths is empty or unreadable"


@pytest.mark.parametrize("root", _WORKTREE_ROOTS)
def test_worktree_root_is_ignored(root):
    ignored = _ignored()
    assert root in ignored, (
        f"{root!r} missing from .serena/project.yml ignored_paths — Serena will "
        f"walk every nested checkout under it on start-up (15+ min, ~1 GB)"
    )


@pytest.mark.parametrize("root", _WORKTREE_ROOTS)
def test_worktree_root_is_ignored_recursively(root):
    # The bare path alone does not cover nested occurrences.
    assert f"**/{root}/**" in _ignored(), f"recursive glob for {root!r} missing"
