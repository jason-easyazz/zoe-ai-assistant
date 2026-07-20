"""Run a PR's focused tests harness-side and return structured pipeline evidence.

This makes the verify phase deterministic: rather than trusting the verify
worker (an LLM agent) to actually check out the PR and run its tests, the
harness runs the PR's changed test files itself and records the ``test``
evidence. The agent can still skip or spuriously block — the objective harness
run is what drives the verify evidence gate.

Mirrors ``pipeline_validators`` (subprocess + bounded timeout + content hash).
Fails *open*: any infrastructure error returns ``ran=False`` so the caller falls
back to the existing agent-driven flow rather than blocking on a harness hiccup.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from repo_paths import zoe_repo_root
from pipeline_evidence import EvidenceItem, content_hash

_TEST_TIMEOUT_S = 600
_GH_TIMEOUT_S = 60
_SNIPPET_MAX = 500
# Only treat files that look like pytest modules under a tests/ dir as focused
# tests we can run deterministically.
_TEST_PATH_HINT = "tests/"


@dataclass(frozen=True)
class FocusedTestResult:
    ran: bool  # did we find changed test files AND execute pytest?
    passed: bool
    summary: str
    content_hash: str
    test_paths: tuple[str, ...]


def _no_run(summary: str) -> FocusedTestResult:
    return FocusedTestResult(
        ran=False, passed=False, summary=summary[:_SNIPPET_MAX], content_hash="", test_paths=()
    )


def _run(cmd: list[str], *, cwd: str, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _changed_test_files(pr_url: str, *, root: str) -> tuple[list[str], str, str]:
    """Return (changed_test_files, head_ref, head_oid) for a PR, via gh."""
    code, out = _run(
        ["gh", "pr", "view", pr_url, "--json", "files,headRefName,headRefOid"],
        cwd=root,
        timeout=_GH_TIMEOUT_S,
    )
    if code != 0 or not out:
        return [], "", ""
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return [], "", ""
    head_ref = str(data.get("headRefName") or "")
    head_oid = str(data.get("headRefOid") or "")
    tests: list[str] = []
    for f in data.get("files") or []:
        path = str(f.get("path") or "")
        base = os.path.basename(path)
        if (
            path.endswith(".py")
            and _TEST_PATH_HINT in path
            and (base.startswith("test_") or base.endswith("_test.py"))
        ):
            tests.append(path)
    return tests, head_ref, head_oid


def run_focused_pr_tests(pr_url: str, *, repo_root: str | None = None) -> FocusedTestResult:
    """Check out a PR head into a throwaway worktree and run its changed tests.

    Returns ``ran=False`` (fall back to agent flow) when there is no resolvable
    PR, no changed test files, or any git/gh/pytest infrastructure error. Returns
    ``ran=True`` with ``passed`` reflecting the pytest exit code when tests ran.
    """
    pr_url = (pr_url or "").strip()
    if not pr_url:
        return _no_run("no PR_URL to verify")
    root = repo_root or zoe_repo_root()

    test_files, head_ref, head_oid = _changed_test_files(pr_url, root=root)
    if not test_files:
        return _no_run("no changed test files in PR diff to run")
    if not head_oid:
        return _no_run("could not resolve PR head sha")

    # Fetch the PR head so the sha is available locally, then run the focused
    # tests in a detached throwaway worktree (never the live checkout).
    _pr_tail = pr_url.rstrip("/").rsplit("/", 1)[-1]
    fetch_ref = f"pull/{_pr_tail}/head" if _pr_tail.isdigit() else head_ref
    code, out = _run(["git", "fetch", "origin", fetch_ref], cwd=root, timeout=_GH_TIMEOUT_S)
    if code != 0:
        # Fall back to fetching by branch name before giving up.
        if head_ref:
            code, out = _run(["git", "fetch", "origin", head_ref], cwd=root, timeout=_GH_TIMEOUT_S)
        if code != 0:
            return _no_run(f"git fetch failed for PR head: {out[-200:]}")

    workdir = tempfile.mkdtemp(prefix="zoe-verify-")
    try:
        code, out = _run(
            ["git", "worktree", "add", "--detach", workdir, head_oid], cwd=root, timeout=_GH_TIMEOUT_S
        )
        if code != 0:
            return _no_run(f"git worktree add failed: {out[-200:]}")
        env_pp = os.path.join(workdir, "services", "zoe-data")
        pytest_cmd = ["python3", "-m", "pytest", "-q", *test_files]
        # Run from the worktree root with PYTHONPATH=services/zoe-data (the repo's
        # convention, matching the verify prompt and pytest.ini).
        try:
            proc = subprocess.run(
                pytest_cmd,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=_TEST_TIMEOUT_S,
                check=False,
                env={**os.environ, "PYTHONPATH": env_pp},
            )
            code = proc.returncode
            combined = ((proc.stdout or "") + (proc.stderr or "")).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _no_run(f"pytest execution error: {exc}")
        tail = combined[-_SNIPPET_MAX:] if combined else "(no output)"
        summary = f"focused pytest ({len(test_files)} file(s)): exit {code}\n{tail}"
        return FocusedTestResult(
            ran=True,
            passed=code == 0,
            summary=summary[:_SNIPPET_MAX],
            content_hash=content_hash(summary),
            test_paths=tuple(test_files),
        )
    finally:
        _run(["git", "worktree", "remove", "--force", workdir], cwd=root, timeout=_GH_TIMEOUT_S)
        shutil.rmtree(workdir, ignore_errors=True)


def focused_test_evidence_item(
    result: FocusedTestResult, *, source: str = "harness", phase: str = "verify"
) -> EvidenceItem:
    return EvidenceItem(
        kind="test",
        summary=result.summary[:500],
        content_hash=result.content_hash or None,
        passed=result.passed,
        metadata={"source": source, "phase": phase, "test_paths": list(result.test_paths)},
    )
