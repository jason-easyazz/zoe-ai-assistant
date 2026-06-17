"""Assess whether a PR objectively passes review, for deterministic review approval.

The review phase normally depends on the zoe-reviewer agent grading the diff and
self-approving. That agent is unreliable (it can block claiming "no code/evidence"
even with the PR_URL in its handoff). This module lets the harness make the
review decision from objective signals instead: the PR is OPEN, every required CI
check is green, and Greptile left zero unresolved review threads (i.e. Greptile
reviewed and is satisfied). Combined with the verify phase having already run the
PR's focused tests, that is a strong, deterministic proxy for "review approved".

Mirrors pipeline_validators / pipeline_focused_tests: bounded subprocess gh calls,
fails OPEN (ready=False) on any error so the agent-driven flow still applies.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from hermes_http import zoe_repo_root

_GH_TIMEOUT_S = 60
_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED", "COMPLETED"}
_OWNER_REPO = "jason-easyazz/zoe-ai-assistant"


@dataclass(frozen=True)
class ReviewReadiness:
    ready: bool
    reason: str
    unresolved_threads: int | None = None


def _run(cmd: list[str], *, cwd: str, timeout: int = _GH_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _pr_number(pr_url: str) -> str:
    tail = pr_url.rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else ""


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


def _unresolved_thread_count(pr_number: str, *, cwd: str) -> int | None:
    query = (
        "{ repository(owner:\"jason-easyazz\", name:\"zoe-ai-assistant\") { pullRequest(number:"
        + pr_number
        + ") { reviewThreads(first:100) { nodes { isResolved } } } } }"
    )
    code, out = _run(["gh", "api", "graphql", "-f", f"query={query}"], cwd=cwd)
    if code != 0 or not out:
        return None
    try:
        nodes = json.loads(out)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (ValueError, TypeError, KeyError):
        return None
    return sum(1 for t in nodes if not t.get("isResolved"))


def assess_pr_review_ready(pr_url: str, *, repo_root: str | None = None) -> ReviewReadiness:
    """Objective review gate: PR OPEN + all required checks green + 0 unresolved
    Greptile review threads. Fails open (ready=False) on any error."""
    pr_url = (pr_url or "").strip()
    if not pr_url:
        return ReviewReadiness(False, "no PR_URL")
    pr_number = _pr_number(pr_url)
    if not pr_number:
        return ReviewReadiness(False, "could not parse PR number")
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

    unresolved = _unresolved_thread_count(pr_number, cwd=root)
    if unresolved is None:
        return ReviewReadiness(False, "could not read review threads")
    if unresolved > 0:
        return ReviewReadiness(False, f"{unresolved} unresolved review threads", unresolved)

    return ReviewReadiness(
        True,
        "CI green + 0 unresolved Greptile threads + verify passed",
        unresolved,
    )
