"""Run the closeout merge (greploop guard) harness-side, for deterministic closeout.

The closeout phase's job is to run the greploop merge guard (resolve Greptile
findings to target confidence, then squash-merge when CLEAN + green + zero
unresolved threads). That depended on a closeout agent worker actually invoking
the guard script — which is unreliable (observed live: the worker bailed with
"cannot proceed without a valid PR URL"). This module lets the harness invoke the
exact same proven guard CLI directly and confirm the merge, so closeout doesn't
hinge on a flaky agent.

The guard itself enforces safety (squash-only, never --admin/--force, holds on
GREPTILE_CONFIDENCE / UNRESOLVED_THREADS / GH_NOT_MERGEABLE). This wrapper only
runs it and then checks whether the PR actually ended up MERGED. Bounded
subprocess timeout; fails OPEN (merged=False) on any error so the agent-driven
closeout flow still applies.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from repo_paths import zoe_repo_root

_GUARD_REL = "scripts/maintenance/run_greploop_guard.sh"
_GUARD_TIMEOUT_S = 900
_GH_TIMEOUT_S = 60


@dataclass(frozen=True)
class CloseoutResult:
    merged: bool
    merge_sha: str | None
    reason: str


def _pr_number(pr_url: str) -> str:
    tail = (pr_url or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else ""


def _run(cmd: list[str], *, cwd: str, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _merge_state(pr_url: str, *, cwd: str) -> tuple[str, str | None]:
    """Return (state, merge_commit_oid) for the PR via gh.

    Parses gh's STDOUT only — `gh pr view --json` writes JSON to stdout, and any
    stderr notice (login/deprecation warnings) must not corrupt the JSON parse,
    which would otherwise make the harness wrongly report the PR as not-merged.
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "state,mergeCommit"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return "", None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return "", None
    state = str(data.get("state") or "").upper()
    mc = data.get("mergeCommit") or {}
    return state, (mc.get("oid") if isinstance(mc, dict) else None)


def run_closeout_merge(pr_url: str, *, repo_root: str | None = None) -> CloseoutResult:
    """Invoke the greploop guard (--merge-when-ready) on the PR and confirm the merge.

    Returns merged=True with the merge SHA when the PR ends up MERGED, else
    merged=False with the guard/state reason. Fails open (merged=False) on any
    missing-PR / script / gh error.
    """
    pr_url = (pr_url or "").strip()
    if not pr_url:
        return CloseoutResult(False, None, "no PR_URL")
    if not _pr_number(pr_url):
        return CloseoutResult(False, None, "could not parse PR number")
    root = repo_root or zoe_repo_root()
    guard = os.path.join(root, _GUARD_REL)
    if not os.path.exists(guard):
        return CloseoutResult(False, None, f"guard script missing: {_GUARD_REL}")

    # Short-circuit: already merged?
    state, sha = _merge_state(pr_url, cwd=root)
    if state == "MERGED":
        return CloseoutResult(True, sha, "already merged")

    # Run the proven guard CLI (it owns confidence/threads/squash-merge safety).
    code, out = _run(
        [guard, "--pr", _pr_number(pr_url), "--merge-when-ready"], cwd=root, timeout=_GUARD_TIMEOUT_S
    )

    # Authoritative check: did the PR actually merge?
    state, sha = _merge_state(pr_url, cwd=root)
    if state == "MERGED":
        return CloseoutResult(True, sha, "merged by greploop guard")
    return CloseoutResult(False, None, f"guard did not merge (exit {code}); tail: {out[-200:]}")
