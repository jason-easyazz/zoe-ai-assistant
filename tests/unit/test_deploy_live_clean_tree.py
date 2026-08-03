"""Pin the deploy_live.sh pre-pull clean-tree gate to its DESIGN INTENT.

Bug (found during a live deploy): require_clean_tree() used a plain
`git status --porcelain`, which is non-empty on a NORMAL live tree because
runtime dirs (data/chroma/, data/music-assistant/ sidecars, HACS, …) are
untracked. So the blessed deploy REFUSED every time and operators bypassed it.
The sibling rollback guard require_no_tracked_dirt (tracked-only) already showed
the intent: runtime artifacts must not block; only uncommitted TRACKED changes
(which a fast-forward would clobber) may.

These tests extract the REAL require_clean_tree function body from the shipped
script (no copy of the logic) and exercise it against throwaway git trees:
  - untracked runtime files present  -> PASS (exit 0)
  - a real uncommitted tracked change -> REFUSE (exit 1)
  - a clean tree                      -> PASS (exit 0)

Pure stdlib + git, so it runs in the fast `ci_safe` lane.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "maintenance" / "deploy_live.sh"


def _script_prelude() -> str:
    """Return the shippable definitions block: everything BEFORE the first
    top-level executable statement (`cur="$(git …`).

    That prelude is only the shebang, comments, `set -euo pipefail`, a few
    var-default assignments, and the guard-function definitions — all safe to
    source — and it contains the REAL require_clean_tree body, not a copy. This
    avoids brace-matching a single function (fragile if the body ever grows a
    column-0 `}` or the script is reformatted): we source the actual functions
    exactly as they ship, then call one.
    """
    lines = SCRIPT.read_text().splitlines()
    end = next(i for i, ln in enumerate(lines) if ln.startswith("cur="))
    return "\n".join(lines[:end])


def _git(tree: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(tree), *args], check=True,
                   capture_output=True, text=True)


def _init_tree(tree: Path) -> None:
    _git(tree, "init", "-q")
    _git(tree, "config", "user.email", "t@t")
    _git(tree, "config", "user.name", "t")
    (tree / "tracked.py").write_text("x = 1\n")
    _git(tree, "add", "tracked.py")
    _git(tree, "commit", "-qm", "init")


def _run_require_clean_tree(tree: Path) -> subprocess.CompletedProcess:
    # Source the real definitions, override LIVE to the throwaway tree (the
    # prelude sets a default we replace), then call the shipped function.
    snippet = f'{_script_prelude()}\nLIVE="{tree}"\nrequire_clean_tree pre-pull\n'
    return subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)


def test_untracked_runtime_files_do_not_block(tmp_path: Path) -> None:
    _init_tree(tmp_path)
    # Simulate the live tree's untracked runtime artifacts.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "chroma").mkdir()
    (tmp_path / "data" / "chroma" / "index.bin").write_text("blob")
    (tmp_path / "untracked_note.txt").write_text("runtime")
    result = _run_require_clean_tree(tmp_path)
    assert result.returncode == 0, (
        f"require_clean_tree must PASS with only untracked files present; "
        f"stderr={result.stderr}"
    )


def test_uncommitted_tracked_change_blocks(tmp_path: Path) -> None:
    _init_tree(tmp_path)
    (tmp_path / "tracked.py").write_text("x = 2  # uncommitted edit\n")
    result = _run_require_clean_tree(tmp_path)
    assert result.returncode == 1, "an uncommitted tracked change MUST refuse the deploy"
    assert "REFUSING TO DEPLOY" in result.stderr


def test_staged_tracked_change_blocks(tmp_path: Path) -> None:
    _init_tree(tmp_path)
    (tmp_path / "tracked.py").write_text("x = 3\n")
    _git(tmp_path, "add", "tracked.py")
    result = _run_require_clean_tree(tmp_path)
    assert result.returncode == 1, "a staged tracked change MUST refuse the deploy"


def test_clean_tree_passes(tmp_path: Path) -> None:
    _init_tree(tmp_path)
    result = _run_require_clean_tree(tmp_path)
    assert result.returncode == 0, f"a clean tree must pass; stderr={result.stderr}"


# --- the WRAPPER must honour the same contract -----------------------------
# deploy_zoe_data_when_ready.sh kept a plain `status --porcelain` after
# deploy_live.sh was fixed, so it re-created the identical bug one layer up: on
# 2026-08-02 it reported NOT-READY purely because of untracked agent/build dirs
# (.polly-work/, modules/omnigent/wheels/, polly-verify-t1/, wt-zauth/), with a
# tracked-clean tree. Fixing one gate and leaving its neighbour is exactly how
# that class of bug survives, so both are pinned together here.

WRAPPER = REPO / "scripts" / "maintenance" / "deploy_zoe_data_when_ready.sh"


def test_wrapper_clean_tree_gate_is_tracked_only() -> None:
    body = WRAPPER.read_text()
    assert "status --porcelain --untracked-files=no" in body, (
        "deploy_zoe_data_when_ready.sh must gate on TRACKED changes only, "
        "matching deploy_live.sh's require_clean_tree"
    )


def test_wrapper_has_no_bare_porcelain_status() -> None:
    """A bare `status --porcelain` anywhere in the readiness gate re-introduces
    the untracked-files refusal."""
    import re
    body = WRAPPER.read_text()
    bare = [
        ln.strip() for ln in body.splitlines()
        if re.search(r"status --porcelain(?!.*--untracked-files=no)", ln)
        and not ln.strip().startswith("#")
    ]
    assert not bare, f"bare `status --porcelain` in the readiness gate: {bare}"


def test_live_runtime_dirs_are_gitignored() -> None:
    """The four dirs that blocked the 2026-08-02 deploy must not be visible to a
    status check at all — belt to the tracked-only braces."""
    ignore = (REPO / ".gitignore").read_text()
    for pattern in ("/.polly-work/", "/polly-verify-*/", "/wt-*/",
                    "modules/omnigent/wheels/"):
        assert pattern in ignore, f"{pattern} must be gitignored"
