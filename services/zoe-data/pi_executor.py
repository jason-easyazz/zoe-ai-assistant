"""S1 — standalone Pi-based code executor (first step of the Pi migration).

Proves ONE flow end-to-end with NO Hermes and NO Multica: a GitHub issue is
handed to the ``pi`` coding-agent CLI, which writes the change in an isolated git
worktree and opens a PR; the existing deterministic gates then verify, review,
and (greploop-)merge it.

Design constraints (see plan ``S1 — Pi-executor adapter``):
* Standalone + flag-gated (``ZOE_USE_PI_EXECUTOR``, default OFF). Not wired into
  the live poll loop or ``executor_registry`` — invoked only by hand.
* Reuses, unchanged, the repo's runtime-agnostic pieces: ``worktree_bootstrap``
  (isolation), the three gate functions (``run_focused_pr_tests`` /
  ``assess_pr_review_ready`` / ``run_closeout_merge``), and the ``pi`` subprocess
  PATH/env helper from ``pi_intent_classifier``.
* The merge is the same greploop guard everything else uses (Greptile + CI,
  squash-only, never ``--admin``/``--force``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pipeline_closeout import run_closeout_merge
from pipeline_focused_tests import run_focused_pr_tests
from pipeline_review import assess_pr_review_ready
from runtime_env import bootstrap_runtime_env
from worktree_bootstrap import ensure_worktree, remove_task_worktree, worktree_branch

# Reuse the proven nvm-node PATH discovery used by the Pi intent classifier so
# the ``pi`` binary + its node runtime resolve identically here.
from pi_intent_classifier import _path_with_node

logger = logging.getLogger(__name__)

_PI_COMMAND = os.environ.get("ZOE_PI_EXECUTOR_COMMAND", "pi")
_PI_PROVIDER = os.environ.get("ZOE_PI_EXECUTOR_PROVIDER", "openrouter")
_PI_MODEL = os.environ.get("ZOE_PI_EXECUTOR_MODEL", "minimax/minimax-m3")
_PI_MODE = os.environ.get("ZOE_PI_EXECUTOR_MODE", "text")
_HERMES_ENV = os.path.expanduser("~/.hermes/.env")
_PR_URL_RE = re.compile(r"PR_URL=\s*(https://github\.com/[^\s\"']+/pull/\d+)")
_ANY_PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def pi_executor_enabled() -> bool:
    """The S1 kill switch. Default OFF so a stray import/call is inert."""
    return (
        os.environ.get("ZOE_USE_PI_EXECUTOR", "false") or "false"
    ).strip().lower() not in {"0", "false", "no", ""}


def _timeout_s() -> float:
    try:
        return float(os.environ.get("ZOE_PI_EXECUTOR_TIMEOUT_S", "900") or "900")
    except (TypeError, ValueError):
        return 900.0


@dataclass(frozen=True)
class PiImplementResult:
    ok: bool
    stage: str  # disabled|fetch|worktree|implement|no_pr|tests|review|merge|done
    reason: str
    pr_url: str | None = None
    pi_returncode: int | None = None
    merged: bool = False
    merge_sha: str | None = None


def _task_id_for_issue(issue_number: int) -> str:
    return f"pi-gh-{int(issue_number)}"


def _fetch_issue(issue_number: int) -> dict:
    """Fetch the GitHub issue via gh. Raises on failure (no issue = no work)."""
    proc = subprocess.run(
        ["gh", "issue", "view", str(int(issue_number)),
         "--json", "number,title,body,url"],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue view {issue_number} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _build_implement_prompt(issue: dict, branch: str, worktree: Path) -> str:
    """A lean, Pi-native implement prompt (NOT the Hermes-coupled _build_body)."""
    title = str(issue.get("title") or "").strip()
    body = str(issue.get("body") or "").strip()
    number = issue.get("number")
    return (
        f"You are implementing GitHub issue #{number} end-to-end, by yourself.\n\n"
        f"# Scope (hard boundary)\n"
        f"Work ONLY inside this git worktree: {worktree}\n"
        f"The branch `{branch}` is already checked out here. Do NOT touch any file "
        f"outside this directory, any other repo, or any global/system config.\n\n"
        f"# Task\n"
        f"Title: {title}\n\n"
        f"{body}\n\n"
        f"# What to do\n"
        f"1. Make the smallest correct change that satisfies the issue.\n"
        f"2. Run the focused tests for what you changed (e.g. "
        f"`python3 -m pytest <changed test files> -q`) and make them pass.\n"
        f"3. `git add -A` and `git commit` with a clear message.\n"
        f"4. `git push -u origin {branch}`.\n"
        f"5. Open EXACTLY ONE pull request: "
        f"`gh pr create --fill --base main --head {branch}`.\n"
        f"6. Print, on its own line, exactly: `PR_URL=<the full https github PR url>` "
        f"and then STOP.\n\n"
        f"# Rules\n"
        f"- Open only ONE PR. Do NOT merge it — an external gate handles merge.\n"
        f"- Never use `--admin`, `--force`, or force-push. Never delete branches.\n"
        f"- If the task is genuinely already satisfied with no change needed, say so "
        f"and do not open an empty PR.\n"
    )


def _pi_argv(prompt: str) -> list[str]:
    argv = [
        _PI_COMMAND,
        "-p",              # non-interactive: process and exit
        "--no-session",    # ephemeral
        "--approve",       # trust project-local config in the worktree
        "--provider", _PI_PROVIDER,
        "--model", _PI_MODEL,
        "--mode", _PI_MODE,
    ]
    argv.append(prompt)
    return argv


def _pi_env() -> dict[str, str]:
    """Env for the pi subprocess: node PATH + OpenRouter key (online provider).

    NOTE: unlike the offline intent classifier we must KEEP the OpenRouter key,
    so we do not reuse ``_pi_subprocess_env`` (which strips secret markers when
    offline-only). The key is not in the runtime_env allowlist, so source it
    from ~/.hermes/.env when absent.
    """
    bootstrap_runtime_env()
    env = os.environ.copy()
    if not env.get("OPENROUTER_API_KEY"):
        env["OPENROUTER_API_KEY"] = _read_openrouter_key()
    env["PATH"] = _path_with_node(
        env.get("PATH", ""), pi_command=_PI_COMMAND, discover_runtime_bins=True
    )
    # Make sure no offline-only flag causes downstream stripping.
    env.pop("ZOE_PI_OFFLINE_ONLY", None)
    return env


def _read_openrouter_key() -> str:
    try:
        with open(_HERMES_ENV, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


async def run_pi_implement(prompt: str, worktree: Path) -> tuple[int, str]:
    """Spawn ``pi`` in the worktree. Return (returncode, combined output)."""
    argv = _pi_argv(prompt)
    env = _pi_env()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(worktree),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_timeout_s()
        )
        out = (stdout or b"").decode(errors="replace") + (stderr or b"").decode(errors="replace")
        return proc.returncode if proc.returncode is not None else -1, out
    except asyncio.TimeoutError:
        if "proc" in locals():
            proc.kill()
            await proc.communicate()
        return -1, f"pi implement timed out after {_timeout_s()}s"


def _gh_pr_for_branch(branch: str, worktree: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open",
             "--json", "url", "--jq", ".[0].url"],
            capture_output=True, text=True, cwd=str(worktree), timeout=60,
        )
        url = (proc.stdout or "").strip()
        return url or None
    except (OSError, subprocess.SubprocessError):
        return None


def _extract_pr_url(pi_output: str, branch: str, worktree: Path) -> str | None:
    m = _PR_URL_RE.search(pi_output or "")
    if m:
        return m.group(1)
    m = _ANY_PR_URL_RE.search(pi_output or "")
    if m:
        return m.group(0)
    return _gh_pr_for_branch(branch, worktree)


def _worktree_has_commits(branch: str, worktree: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
            capture_output=True, text=True, cwd=str(worktree), timeout=30,
        )
        return (proc.stdout or "0").strip() not in {"", "0"}
    except (OSError, subprocess.SubprocessError):
        return False


def _executor_open_pr(branch: str, worktree: Path) -> str | None:
    """Fallback: if pi committed but didn't push/PR, do it ourselves."""
    try:
        subprocess.run(["git", "push", "-u", "origin", branch],
                       cwd=str(worktree), timeout=120, check=False)
        subprocess.run(["gh", "pr", "create", "--fill", "--base", "main",
                        "--head", branch],
                       cwd=str(worktree), capture_output=True, text=True,
                       timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("pi_executor: fallback PR open failed: %s", exc)
        return None
    return _gh_pr_for_branch(branch, worktree)


async def execute_issue(
    issue_number: int, *, no_merge: bool = False, keep_worktree: bool = False
) -> PiImplementResult:
    """Run the full S1 cycle for one GitHub issue. Flag-gated; safe by default."""
    if not pi_executor_enabled():
        return PiImplementResult(False, "disabled",
                                 "ZOE_USE_PI_EXECUTOR is off (set it to enable S1)")

    try:
        issue = _fetch_issue(issue_number)
    except Exception as exc:  # noqa: BLE001 - report cleanly
        return PiImplementResult(False, "fetch", f"could not fetch issue: {exc}")

    task_id = _task_id_for_issue(issue_number)
    branch = worktree_branch(task_id)
    try:
        worktree = ensure_worktree(task_id)
    except Exception as exc:  # noqa: BLE001
        return PiImplementResult(False, "worktree", f"worktree prep failed: {exc}")

    prompt = _build_implement_prompt(issue, branch, worktree)
    rc, pi_out = await run_pi_implement(prompt, worktree)
    logger.info("pi_executor: pi finished rc=%s for issue #%s", rc, issue_number)

    pr_url = _extract_pr_url(pi_out, branch, worktree)
    if not pr_url and _worktree_has_commits(branch, worktree):
        pr_url = _executor_open_pr(branch, worktree)
    if not pr_url:
        return PiImplementResult(False, "no_pr",
                                 "pi produced no PR and no unpushed commits to recover",
                                 pi_returncode=rc)

    repo_root = str(worktree)  # the worktree is on the PR branch (satisfies the guard)

    ft = run_focused_pr_tests(pr_url, repo_root=repo_root)
    if ft.ran and not ft.passed:
        return PiImplementResult(False, "tests", f"focused tests failed: {ft.summary}",
                                 pr_url=pr_url, pi_returncode=rc)

    rr = assess_pr_review_ready(pr_url, repo_root=repo_root)
    if not rr.ready:
        return PiImplementResult(False, "review", f"review not ready: {rr.reason}",
                                 pr_url=pr_url, pi_returncode=rc)

    if no_merge:
        return PiImplementResult(True, "review", "PR open + gates green (merge skipped)",
                                 pr_url=pr_url, pi_returncode=rc)

    co = run_closeout_merge(pr_url, repo_root=repo_root)
    if not co.merged:
        return PiImplementResult(False, "merge", f"merge not completed: {co.reason}",
                                 pr_url=pr_url, pi_returncode=rc)

    if not keep_worktree:
        try:
            remove_task_worktree(task_id)
        except Exception:  # noqa: BLE001 - best effort
            pass
    return PiImplementResult(True, "done", "merged", pr_url=pr_url, pi_returncode=rc,
                             merged=True, merge_sha=co.merge_sha)


def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="pi_executor",
        description="S1: run the Pi executor on one GitHub issue (Pi → PR → gates → merge).",
    )
    parser.add_argument("issue_number", type=int, help="GitHub issue number")
    parser.add_argument("--no-merge", action="store_true",
                        help="stop after the review gate (do not run the greploop merge)")
    parser.add_argument("--keep-worktree", action="store_true",
                        help="do not remove the task worktree after a successful merge")
    args = parser.parse_args()

    if not pi_executor_enabled():
        print("ZOE_USE_PI_EXECUTOR is off — set ZOE_USE_PI_EXECUTOR=true to enable S1.")
        return 2

    result = asyncio.run(execute_issue(
        args.issue_number, no_merge=args.no_merge, keep_worktree=args.keep_worktree))
    print(json.dumps(result.__dict__, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_amain())
