"""Assess whether a PR objectively passes review, for deterministic review approval.

The review phase normally depends on the zoe-reviewer agent grading the diff and
self-approving. That agent is unreliable (it can block claiming "no code/evidence"
even with the PR_URL in its handoff, or complete with an empty verdict). This
module lets the harness make the review decision from objective signals instead:
the PR is OPEN and every required CI check is green. Combined with the verify
phase having already run the PR's focused tests, that is a deterministic proxy
for "review approved".

Greptile review threads / confidence are intentionally NOT gated here. A fresh
implement PR almost always has open Greptile threads AT REVIEW TIME (Greptile
auto-reviews every PR); those are resolved in CLOSEOUT by the greploop merge
guard, which already gates the merge on confidence + zero unresolved threads.
Gating them at review too is a chicken-and-egg deadlock (review would never
approve a normal PR), so the thread/confidence gate lives only at closeout.

Mirrors pipeline_validators / pipeline_focused_tests: bounded subprocess gh calls,
fails OPEN (ready=False) on any error so the agent-driven flow still applies.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from repo_paths import zoe_repo_root

_GH_TIMEOUT_S = 60
_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED", "COMPLETED"}


@dataclass(frozen=True)
class ReviewReadiness:
    ready: bool
    reason: str


def _run(cmd: list[str], *, cwd: str, timeout: int = _GH_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _checks_all_green(rollup: list) -> bool:
    saw_one = False
    for c in rollup or []:
        saw_one = True
        state = (c.get("conclusion") or c.get("status") or c.get("state") or "").upper()
        if state in {"IN_PROGRESS", "PENDING", "QUEUED", "WAITING", "REQUESTED", ""}:
            return False
        if state not in _GREEN:
            return False
    return saw_one  # empty rollup is not trusted as green


def assess_pr_review_ready(pr_url: str, *, repo_root: str | None = None) -> ReviewReadiness:
    """Objective review gate: PR OPEN + all required CI checks green.

    Greptile threads/confidence are owned by the closeout greploop merge gate,
    NOT review (see module docstring). Fails open (ready=False) on any error so
    the agent-driven review flow still applies.
    """
    pr_url = (pr_url or "").strip()
    if not pr_url:
        return ReviewReadiness(False, "no PR_URL")
    root = repo_root or zoe_repo_root()

    code, out = _run(
        ["gh", "pr", "view", pr_url, "--json", "state,mergeStateStatus,statusCheckRollup"], cwd=root
    )
    if code != 0 or not out:
        return ReviewReadiness(False, f"gh pr view failed: {out[-160:]}")
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return ReviewReadiness(False, "gh pr view returned non-JSON")

    if (data.get("state") or "").upper() != "OPEN":
        return ReviewReadiness(False, f"PR not open (state={data.get('state')})")
    if not _checks_all_green(data.get("statusCheckRollup") or []):
        return ReviewReadiness(False, "CI checks not all green")

    return ReviewReadiness(True, "CI green + verify passed (Greptile threads owned by closeout)")
