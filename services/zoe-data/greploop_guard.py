"""Bounded Greptile PR guard for cheap-model repair packets.

The guard keeps deterministic state outside model context. Cheap models get one
validated packet at a time; Hermes remains the escalation path for broad or risky
work.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_REPO = os.environ.get("ZOE_GITHUB_REPO", "jason-easyazz/zoe-ai-assistant")
DEFAULT_BASE_BRANCH = os.environ.get("ZOE_GITHUB_DEFAULT_BRANCH", "main")
REPO_ROOT = Path(os.environ.get("ZOE_ASSISTANT_ROOT", Path(__file__).resolve().parents[2]))
STATE_ROOT = Path(os.environ.get("ZOE_PR_GUARD_STATE_DIR", "/home/zoe/assistant/.cursor/tmp/pr_guard"))
MAX_ITERATIONS = int(os.environ.get("ZOE_PR_GUARD_MAX_ITERATIONS", "5"))
NO_PROGRESS_LIMIT = int(os.environ.get("ZOE_PR_GUARD_NO_PROGRESS_LIMIT", "3"))
SAME_ERROR_LIMIT = int(os.environ.get("ZOE_PR_GUARD_SAME_ERROR_LIMIT", "3"))
MAX_COST_USD = float(os.environ.get("ZOE_PR_GUARD_MAX_COST_USD", "0.25"))
MAX_OUTPUT_CHARS = int(os.environ.get("ZOE_PR_GUARD_MAX_OUTPUT_CHARS", "12000"))
TRIGGER_COOLDOWN_SECONDS = int(os.environ.get("ZOE_PR_GUARD_TRIGGER_COOLDOWN_SECONDS", "900"))
GREPTILE_WAIT_TIMEOUT_SECONDS = int(os.environ.get("ZOE_PR_GUARD_GREPTILE_WAIT_TIMEOUT_SECONDS", "1800"))
GREPTILE_WAIT_POLL_SECONDS = int(os.environ.get("ZOE_PR_GUARD_GREPTILE_WAIT_POLL_SECONDS", "120"))
GREPTILE_REPO_ACTIVE_REVIEW_LIMIT = int(os.environ.get("ZOE_PR_GUARD_MAX_ACTIVE_GREPTILE_REVIEWS", "1"))
GREPTILE_REPO_ACTIVE_REVIEW_STALE_SECONDS = int(
    os.environ.get(
        "ZOE_PR_GUARD_ACTIVE_GREPTILE_STALE_SECONDS",
        str(max(GREPTILE_WAIT_TIMEOUT_SECONDS, GREPTILE_WAIT_POLL_SECONDS * 2)),
    )
)
# P1: do not (re)trigger a review until the PR head SHA has been unchanged for this
# many seconds, so reviews are not restarted faster than the head settles.
HEAD_STABILITY_WINDOW_SECONDS = int(os.environ.get("ZOE_PR_GUARD_HEAD_STABILITY_SECONDS", "90"))
# P2c: bound how many times the guard re-triggers a clean-but-sub-confidence review
# for the same head before escalating to Hermes instead of looping forever.
NO_FINDINGS_RETRIGGER_LIMIT = int(os.environ.get("ZOE_PR_GUARD_NO_FINDINGS_RETRIGGER_LIMIT", "3"))

# Autonomous close (Phase 2 executor migration): when the ONLY thing keeping an
# otherwise-clear PR from merging is a strict-mode BEHIND branch, self-heal with
# `gh pr update-branch` instead of stranding the PR for a human. Safe — updating
# a branch merges base into it and the merge STILL requires every gate afterward
# (Greptile 5/5, threads resolved, CI green) — but it re-triggers CI + Greptile,
# so it is cooldown-guarded and only fires when BEHIND is the sole blocker.
# Default OFF: this file is load-bearing for every PR; the operator flips it on.
AUTO_UPDATE_BRANCH = os.environ.get("ZOE_PR_GUARD_AUTO_UPDATE_BRANCH", "0").strip().lower() in {
    "1", "true", "yes", "on",
}
UPDATE_BRANCH_COOLDOWN_SECONDS = int(
    os.environ.get("ZOE_PR_GUARD_UPDATE_BRANCH_COOLDOWN_SECONDS", "300")
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


# P2c: when the Greptile check has SUCCEEDED with zero unresolved/actionable threads,
# treat the PR as mergeable even if the summary confidence is below target.
CLEAN_MERGE_IGNORES_CONFIDENCE = _env_flag("ZOE_PR_GUARD_CLEAN_MERGE_IGNORES_CONFIDENCE", True)

# Cheap-model repair packets must never merge or bypass hooks; use merge_pr_when_ready().
FORBIDDEN_ACTIONS = [
    "merge_pr",
    "force_push",
    "manual_deploy",
    "delete_branch",
    "amend_commit",
    "bypass_hooks",
]
_CI_OK_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
ALLOWED_TASK_TYPES = {"FIX_GREPTILE_FINDING", "FIX_CI_FAILURE", "SUMMARIZE_BLOCKER"}
HIGH_RISK_PREFIXES = (
    ".github/workflows/",
    "services/zoe-data/alembic/",
    "services/zoe-data/auth.py",
    "services/zoe-data/database.py",
    "services/zoe-data/routers/chat.py",
    "docker-compose",
)
SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization|bearer)\s*[:=]\s*['\"]?[^'\"\s]+"
)
BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer)\s+[^'\"\s]+")


class GuardError(RuntimeError):
    pass


@dataclass
class GuardPacket:
    task_type: str
    pr: int
    head_sha: str | None
    base_branch: str
    allowed_files: list[str]
    max_files: int
    max_changed_lines: int
    issue_text: str
    commands_to_run: list[str]
    success_condition: str
    stop_condition: str
    forbidden_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "base_branch": self.base_branch,
            "allowed_files": self.allowed_files,
            "max_files": self.max_files,
            "max_changed_lines": self.max_changed_lines,
            "issue_text": self.issue_text,
            "commands_to_run": self.commands_to_run,
            "success_condition": self.success_condition,
            "stop_condition": self.stop_condition,
            "forbidden_actions": self.forbidden_actions,
        }


def redact(value: Any) -> Any:
    if isinstance(value, str):
        value = BEARER_RE.sub(lambda m: f"{m.group(1)} <redacted>", value)
        return SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


def _state_dir(pr_number: int) -> Path:
    return STATE_ROOT / f"pr-{int(pr_number)}"


def _json_path(pr_number: int, name: str) -> Path:
    return _state_dir(pr_number) / name


def _write_json(pr_number: int, name: str, payload: dict[str, Any]) -> None:
    path = _json_path(pr_number, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(redact(payload), indent=2, sort_keys=True) + "\n"
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _fsync_dir(path: Path) -> None:
    try:
        dir_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def read_guard_state(pr_number: int) -> dict[str, Any]:
    path = _json_path(pr_number, "status.json")
    if not path.exists():
        return {"pr": int(pr_number), "state": "MISSING"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "pr": int(pr_number),
            "state": "STALE_READ_RETRY",
            "terminal_state": "STALE_READ_RETRY",
            "error": "invalid_json_state",
        }
    except OSError as exc:
        return {
            "pr": int(pr_number),
            "state": "STALE_READ_RETRY",
            "terminal_state": "STALE_READ_RETRY",
            "error": exc.__class__.__name__,
        }


def read_observed_guard_state(pr_number: int, *, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    """Return local guard state plus a live GitHub observation when available."""
    state = read_guard_state(pr_number)
    observed = _gh_pr_observation(pr_number, repo=repo)
    if observed.get("ok"):
        state = {**state, "observed": observed}
        if observed.get("state") == "MERGED" and state.get("terminal_state") != "MERGED":
            state["historical_terminal_state"] = state.get("terminal_state")
            state["terminal_state"] = "MERGED"
        elif _gh_greptile_check_success(observed) and state.get("terminal_state") in {
            "WAITING_GREPTILE",
            "BLOCKED_GREPTILE_STUCK",
        }:
            state["historical_terminal_state"] = state.get("terminal_state")
            state["terminal_state"] = "GITHUB_GREPTILE_COMPLETE"
    return state


def finalize_merged_guard_state(
    *, repo: str = DEFAULT_REPO, pr_numbers: list[int] | None = None
) -> dict[str, Any]:
    """P3: mark guard state for already merged/closed PRs as finalized.

    Stale ``WAITING_GREPTILE`` state for PRs that merged out-of-band (e.g. via the
    GitHub UI) pollutes capacity and backlog scans. This sweep finalizes only PRs that
    GitHub reports as MERGED or CLOSED; it never clears or mutates mid-flight (open)
    PR state, so in-progress reviews are left untouched.
    """
    finalized: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if pr_numbers is None:
        if not STATE_ROOT.exists():
            return {"finalized": finalized, "skipped": skipped}
        candidates: list[int] = []
        for status_path in STATE_ROOT.glob("pr-*/status.json"):
            try:
                candidates.append(int(status_path.parent.name.removeprefix("pr-")))
            except ValueError:
                continue
    else:
        candidates = [int(pr) for pr in pr_numbers]
    for pr in sorted(set(candidates)):
        state = read_guard_state(pr)
        if state.get("state") in ("MISSING", "STALE_READ_RETRY"):
            skipped.append({"pr": pr, "reason": "no_local_state"})
            continue
        if state.get("finalized"):
            skipped.append({"pr": pr, "reason": "already_finalized"})
            continue
        observation = _gh_pr_observation(pr, repo=repo)
        if not observation.get("ok"):
            skipped.append({"pr": pr, "reason": "observation_failed"})
            continue
        gh_state = str(observation.get("state") or "").upper()
        if gh_state not in ("MERGED", "CLOSED"):
            # CRITICAL: never finalize an open / mid-flight PR.
            skipped.append({"pr": pr, "reason": "still_open", "state": gh_state})
            continue
        state["finalized"] = True
        state["finalized_at"] = time.time()
        state["historical_terminal_state"] = state.get("terminal_state")
        state["terminal_state"] = "MERGED" if gh_state == "MERGED" else "CLOSED"
        _clear_greptile_wait_state(state)
        _write_json(pr, "status.json", state)
        finalized.append({"pr": pr, "state": state["terminal_state"]})
    return {"finalized": finalized, "skipped": skipped}


def _append_progress(pr_number: int, line: str) -> None:
    path = _json_path(pr_number, "progress.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {redact(line)}\n")


def _record_guardrail(pr_number: int, line: str) -> None:
    path = _json_path(pr_number, "guardrails.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {redact(line)}\n")



_HELD_LOCKS: set[int] = set()


@contextmanager
def acquire_lock(pr_number: int):
    pr_number = int(pr_number)
    if pr_number in _HELD_LOCKS:
        raise GuardError(f"guard already running for PR #{pr_number}")
    path = _json_path(pr_number, "lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_id = uuid.uuid4().hex
    payload = {
        "pid": os.getpid(),
        "created_at": time.time(),
        "owner": "zoe-greploop-guard",
        "lock_id": lock_id,
    }
    handle = path.open("a+", encoding="utf-8")
    locked = False
    registered = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GuardError(f"guard already running for PR #{pr_number}") from exc
        locked = True
        _HELD_LOCKS.add(pr_number)
        registered = True
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(payload, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        try:
            yield
        finally:
            released = {**payload, "released_at": time.time()}
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(released, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if registered:
            _HELD_LOCKS.discard(pr_number)
        if locked:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, text=True, capture_output=True, check=check)


def _local_head_sha() -> str | None:
    try:
        return _run_git(["rev-parse", "HEAD"]).stdout.strip()
    except Exception:
        return None


def _diff_files(base_sha: str | None = None) -> list[str]:
    paths: list[str] = []
    commands = [["diff", "--name-only"]]
    if base_sha:
        commands.insert(0, ["diff", "--name-only", f"{base_sha}..HEAD"])
    for args in commands:
        proc = _run_git(args, check=False)
        for line in proc.stdout.splitlines():
            path = line.strip()
            if path and path not in paths:
                paths.append(path)
    return paths


def _diff_changed_lines(base_sha: str | None = None) -> int:
    total = 0
    commands = [["diff", "--numstat"]]
    if base_sha:
        commands.insert(0, ["diff", "--numstat", f"{base_sha}..HEAD"])
    for args in commands:
        proc = _run_git(args, check=False)
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                for value in parts[:2]:
                    if value.isdigit():
                        total += int(value)
    return total


def validate_packet(packet: GuardPacket) -> None:
    if packet.task_type not in ALLOWED_TASK_TYPES:
        raise GuardError(f"unsupported task_type: {packet.task_type}")
    if not packet.pr or not packet.issue_text.strip():
        raise GuardError("BLOCKED_MISSING_CONTEXT: pr and issue_text are required")
    if not packet.allowed_files:
        raise GuardError("BLOCKED_MISSING_CONTEXT: allowed_files is required")
    if len(packet.allowed_files) > packet.max_files:
        raise GuardError("packet exceeds max_files")
    for action in FORBIDDEN_ACTIONS:
        if action not in packet.forbidden_actions:
            raise GuardError(f"packet missing forbidden action: {action}")


def _finding_hash(finding: dict[str, Any]) -> str:
    raw = f"{finding.get('id')}|{finding.get('file_path')}|{finding.get('body')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _effective_pr_head_sha(status: dict[str, Any] | None, observation: dict[str, Any] | None = None) -> str | None:
    return _head_sha_for_trigger(status, observation) or _local_head_sha()


def _packet_for_finding(
    pr_number: int,
    status: dict[str, Any],
    finding: dict[str, Any],
    *,
    head_sha: str | None = None,
) -> GuardPacket:
    allowed_file = finding.get("file_path") or ""
    if not allowed_file:
        raise GuardError("BLOCKED_MISSING_CONTEXT: finding has no file path")
    packet = GuardPacket(
        task_type="FIX_GREPTILE_FINDING",
        pr=int(pr_number),
        head_sha=head_sha or _effective_pr_head_sha(status),
        base_branch=DEFAULT_BASE_BRANCH,
        allowed_files=[allowed_file],
        max_files=1,
        max_changed_lines=120,
        issue_text=str(finding.get("body") or "")[:4000],
        commands_to_run=[
            "python3 tools/audit/validate_structure.py",
            "python3 tools/audit/validate_critical_files.py",
            "git diff --check",
        ],
        success_condition="Focused fix is applied, local checks pass, and Greptile can be re-run on the current head.",
        stop_condition="Stop if files outside allowed_files are required, the finding is ambiguous, or a forbidden action is needed.",
        forbidden_actions=FORBIDDEN_ACTIONS,
    )
    validate_packet(packet)
    return packet


def analyze_result(packet: GuardPacket, result_text: str, *, pre_run_sha: str | None = None) -> dict[str, Any]:
    changed = _diff_files(pre_run_sha)
    changed_lines = _diff_changed_lines(pre_run_sha)
    outside = [path for path in changed if path not in set(packet.allowed_files)]
    forbidden = [action for action in packet.forbidden_actions if action.replace("_", " ") in result_text.lower()]
    if outside or len(changed) > packet.max_files or changed_lines > packet.max_changed_lines or forbidden:
        return {
            "classification": "REJECTED",
            "changed_files": changed,
            "changed_lines": changed_lines,
            "outside_allowlist": outside,
            "forbidden_actions": forbidden,
        }
    if "BLOCKED" in result_text.upper():
        return {"classification": "BLOCKED", "changed_files": changed, "changed_lines": changed_lines}
    return {"classification": "APPLIED", "changed_files": changed, "changed_lines": changed_lines}


def _load_status(pr_number: int) -> dict[str, Any]:
    state = read_guard_state(pr_number)
    if state.get("state") in ("MISSING", "STALE_READ_RETRY"):
        return {
            "pr": int(pr_number),
            "iteration": 0,
            "no_progress_count": 0,
            "same_error_count": 0,
            "last_progress_key": "",
            "last_error_hash": "",
        }
    return state


def _update_circuit_breakers(pr_number: int, state: dict[str, Any], progress_key: str, error: str | None = None) -> str | None:
    state["iteration"] = int(state.get("iteration") or 0) + 1
    if state["iteration"] > MAX_ITERATIONS:
        return "BLOCKED_MAX_ITERATIONS"
    if progress_key == state.get("last_progress_key"):
        state["no_progress_count"] = int(state.get("no_progress_count") or 0) + 1
    else:
        state["no_progress_count"] = 0
    state["last_progress_key"] = progress_key
    if state["no_progress_count"] >= NO_PROGRESS_LIMIT:
        return "BLOCKED_NO_PROGRESS"
    if error:
        error_hash = hashlib.sha256(error.encode()).hexdigest()[:16]
        if error_hash == state.get("last_error_hash"):
            state["same_error_count"] = int(state.get("same_error_count") or 0) + 1
        else:
            state["same_error_count"] = 1
        state["last_error_hash"] = error_hash
        if state["same_error_count"] >= SAME_ERROR_LIMIT:
            return "BLOCKED_REPEATED_ERROR"
    _write_json(pr_number, "status.json", state)
    return None


def _current_branch() -> str | None:
    proc = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    branch = (proc.stdout or "").strip()
    return branch or None


def _remote_tracking_branch() -> str | None:
    proc = _run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], check=False)
    branch = (proc.stdout or "").strip()
    return branch or None


def _pr_checkout_observation(pr_number: int, *, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    proc = _run_gh(
        [
            "pr",
            "view",
            str(int(pr_number)),
            "--json",
            "headRefName,headRefOid,state",
        ],
        repo=repo,
    )
    if proc.returncode != 0:
        return {"ok": False, "reason": "GH_PR_VIEW_FAILED", "detail": (proc.stderr or proc.stdout or "").strip()}
    try:
        data = _parse_gh_json(proc)
    except GuardError as exc:
        return {"ok": False, "reason": "GH_PR_VIEW_INVALID", "detail": str(exc)}
    return {"ok": True, **data}


def verify_pr_checkout_for_repair(packet: GuardPacket, *, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    branch = _current_branch()
    local_head = _local_head_sha()
    upstream = _remote_tracking_branch()
    observation = _pr_checkout_observation(packet.pr, repo=repo)
    if not observation.get("ok"):
        return {**observation, "ok": False, "branch": branch, "local_head": local_head, "upstream": upstream}
    expected_branch = str(observation.get("headRefName") or "")
    expected_head = str(observation.get("headRefOid") or "")
    state = str(observation.get("state") or "").upper()
    if not expected_branch or not expected_head:
        return {
            "ok": False,
            "reason": "GH_PR_VIEW_INCOMPLETE",
            "detail": "headRefName or headRefOid missing from GitHub response",
            "branch": branch,
            "local_head": local_head,
        }
    if state != "OPEN":
        return {"ok": False, "reason": "PR_NOT_OPEN", "state": state, "branch": branch, "local_head": local_head}
    if not branch or branch in {"HEAD", DEFAULT_BASE_BRANCH, "main", "master"}:
        return {"ok": False, "reason": "UNSAFE_BRANCH", "branch": branch, "expected_branch": expected_branch}
    if expected_branch and branch != expected_branch:
        return {"ok": False, "reason": "BRANCH_MISMATCH", "branch": branch, "expected_branch": expected_branch}
    if upstream and expected_branch and upstream != f"origin/{expected_branch}":
        return {"ok": False, "reason": "UPSTREAM_MISMATCH", "upstream": upstream, "expected_upstream": f"origin/{expected_branch}"}
    if expected_head and local_head != expected_head:
        return {"ok": False, "reason": "HEAD_MISMATCH", "local_head": local_head, "expected_head": expected_head}
    if packet.head_sha and expected_head and packet.head_sha != expected_head:
        return {"ok": False, "reason": "PACKET_HEAD_MISMATCH", "packet_head": packet.head_sha, "expected_head": expected_head}
    return {
        "ok": True,
        "branch": branch,
        "local_head": local_head,
        "expected_branch": expected_branch,
        "expected_head": expected_head,
        "upstream": upstream,
    }


async def _record_cost_event(task_id: str | None, estimated_cost_usd: float) -> None:
    if estimated_cost_usd <= 0:
        return
    try:
        from db_pool import get_db_ctx
        import uuid

        async with get_db_ctx() as db:
            await db.execute(
                """INSERT INTO agent_cost_events
                   (id, agent_name, model, task_id, user_id, input_tokens, output_tokens, estimated_cost_usd, ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                uuid.uuid4().hex,
                "greploop_guard",
                os.environ.get("ZOE_CHEAP_PR_AGENT_MODEL", "cheap-pr-agent"),
                task_id,
                "system",
                0,
                0,
                estimated_cost_usd,
                time.time(),
            )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "greploop_guard: failed to record cost event for task=%s ($%.4f) — "
            "spend under-reported: %s", task_id, estimated_cost_usd, exc)
        return


async def _run_cheap_agent(packet: GuardPacket, *, task_id: str | None = None) -> tuple[str, str]:
    checkout = verify_pr_checkout_for_repair(packet)
    if not checkout.get("ok"):
        return "BLOCKED_WRONG_WORKTREE", json.dumps(redact(checkout), sort_keys=True)
    cmd = os.environ.get("ZOE_CHEAP_PR_AGENT_CMD")
    url = os.environ.get("ZOE_CHEAP_PR_AGENT_URL")
    if not cmd and not url:
        return "BLOCKED_CHEAP_RUNNER_NOT_CONFIGURED", ""
    estimated_cost = float(os.environ.get("ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD", "0"))
    if estimated_cost > MAX_COST_USD:
        return "BLOCKED_BUDGET_EXCEEDED", f"estimated_cost_usd={estimated_cost} max_cost_usd={MAX_COST_USD}"
    payload = json.dumps(packet.to_dict())
    start = time.time()
    if cmd:
        argv = shlex.split(cmd)
        if not argv:
            return "BLOCKED_CHEAP_RUNNER_NOT_CONFIGURED", "empty ZOE_CHEAP_PR_AGENT_CMD"
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=REPO_ROOT,
        )
        stdout, stderr = await proc.communicate(payload.encode())
        output = (stdout + stderr).decode(errors="replace")[:MAX_OUTPUT_CHARS]
        status = "OK" if proc.returncode == 0 else f"BLOCKED_CHEAP_RUNNER_EXIT_{proc.returncode}"
    else:
        import httpx

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url or "", json=packet.to_dict())
            output = resp.text[:MAX_OUTPUT_CHARS]
            status = "OK" if resp.status_code < 400 else f"BLOCKED_CHEAP_RUNNER_HTTP_{resp.status_code}"
    elapsed = time.time() - start
    await _record_cost_event(task_id, estimated_cost)
    return status, f"elapsed={elapsed:.1f}s estimated_cost_usd={estimated_cost}\n{output}"


def _float_state(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _update_greptile_wait_state(
    state: dict[str, Any],
    *,
    now: float,
    status: dict[str, Any],
    confidence: int,
    actionable_count: int,
    summary_count: int,
) -> dict[str, Any]:
    started_at = _float_state(state.get("greptile_wait_started_at"))
    if started_at <= 0:
        started_at = now
        poll_due = True
    else:
        poll_due = now >= _float_state(state.get("greptile_next_poll_after"))
    next_poll_after = _float_state(state.get("greptile_next_poll_after"))
    wait_count = int(state.get("waiting_greptile_count") or 0)
    if poll_due:
        wait_count += 1
        next_poll_after = now + max(1, GREPTILE_WAIT_POLL_SECONDS)
        state["greptile_next_poll_after"] = next_poll_after
    elapsed_seconds = max(0.0, now - started_at)
    retry_after_seconds = max(1, int(next_poll_after - now)) if next_poll_after > now else 1
    state["greptile_wait_started_at"] = started_at
    state["greptile_wait_last_seen_at"] = now
    state["greptile_wait_elapsed_seconds"] = int(elapsed_seconds)
    state["waiting_greptile_count"] = wait_count
    state["greptile"] = {
        "status": status.get("reviewCompleteness") or "review_running",
        "confidence": confidence,
        "unaddressed_count": actionable_count,
        "summary_count": summary_count,
        "wait_count": wait_count,
        "wait_elapsed_seconds": int(elapsed_seconds),
        "next_poll_after": int(next_poll_after),
        "retry_after_seconds": retry_after_seconds,
    }
    return {
        "wait_count": wait_count,
        "elapsed_seconds": int(elapsed_seconds),
        "poll_due": poll_due,
        "stuck": elapsed_seconds >= GREPTILE_WAIT_TIMEOUT_SECONDS,
        "retry_after_seconds": retry_after_seconds,
    }


def _clear_greptile_wait_state(state: dict[str, Any]) -> None:
    state["waiting_greptile_count"] = 0
    for key in (
        "greptile_wait_started_at",
        "greptile_wait_last_seen_at",
        "greptile_wait_elapsed_seconds",
        "greptile_next_poll_after",
    ):
        state.pop(key, None)
    state.pop("greptile", None)


def _repo_waiting_greptile_prs(*, exclude_pr_number: int, now: float) -> list[dict[str, Any]]:
    if not STATE_ROOT.exists():
        return []
    active: list[dict[str, Any]] = []
    for status_path in STATE_ROOT.glob("pr-*/status.json"):
        try:
            pr_number = int(status_path.parent.name.removeprefix("pr-"))
        except ValueError:
            continue
        if pr_number == int(exclude_pr_number):
            continue
        try:
            state = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("terminal_state") != "WAITING_GREPTILE":
            continue
        last_seen = _float_state(state.get("greptile_wait_last_seen_at") or state.get("last_triggered_at"))
        if last_seen <= 0 or (now - last_seen) > GREPTILE_REPO_ACTIVE_REVIEW_STALE_SECONDS:
            continue
        active.append(
            {
                "pr": pr_number,
                "last_seen_at": int(last_seen),
                "elapsed_seconds": int(max(0.0, now - _float_state(state.get("greptile_wait_started_at"), last_seen))),
                "next_poll_after": int(_float_state(state.get("greptile_next_poll_after"))),
            }
        )
    return sorted(active, key=lambda item: int(item["pr"]))


def _gh_open_prs_with_running_greptile(*, repo: str = DEFAULT_REPO) -> set[int] | None:
    """Return open PR numbers whose GitHub "Greptile Review" check is IN_PROGRESS.

    This is the authoritative source of in-flight reviews: it sees reviews started by
    the GitHub Greptile app (auto-on-push), raw MCP triggers, and PRs that have no
    local guard state dir. Returns ``None`` when the GitHub query fails so the caller
    can fall back to local state only.
    """
    # Known ceiling: caps active-review counting at 500 open PRs. If the repo ever
    # exceeds this, in-flight Greptile checks beyond the first 500 are invisible to
    # the capacity guard and the active-review limit could be over-triggered.
    proc = _run_gh(
        ["pr", "list", "--state", "open", "--limit", "500", "--json", "number,statusCheckRollup"],
        repo=repo,
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    running: set[int] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        number = row.get("number")
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue
        if _gh_greptile_check_running({"statusCheckRollup": row.get("statusCheckRollup") or []}):
            running.add(number)
    return running


def _repo_greptile_capacity_skip(
    *, pr_number: int, now: float, repo: str = DEFAULT_REPO
) -> dict[str, Any] | None:
    if GREPTILE_REPO_ACTIVE_REVIEW_LIMIT <= 0:
        return None
    local_active = _repo_waiting_greptile_prs(exclude_pr_number=pr_number, now=now)
    local_prs = {int(item["pr"]) for item in local_active}
    github_running = _gh_open_prs_with_running_greptile(repo=repo)
    github_prs: set[int] = set()
    if github_running is not None:
        github_prs = {pr for pr in github_running if pr != int(pr_number)}
    active_prs = sorted(local_prs | github_prs)
    if len(active_prs) < GREPTILE_REPO_ACTIVE_REVIEW_LIMIT:
        return None
    future_polls = [
        int(item["next_poll_after"])
        for item in local_active
        if int(item.get("next_poll_after") or 0) > now
    ]
    retry_after = min(future_polls) - int(now) if future_polls else GREPTILE_WAIT_POLL_SECONDS
    return {
        "skipped": True,
        "reason": "repo_greptile_review_capacity",
        "active_review_count": len(active_prs),
        "active_review_limit": GREPTILE_REPO_ACTIVE_REVIEW_LIMIT,
        "active_prs": active_prs,
        "github_active_prs": sorted(github_prs),
        "local_waiting_prs": sorted(local_prs),
        "retry_after_seconds": max(1, int(retry_after)),
    }


def _run_gh(args: list[str], *, repo: str = DEFAULT_REPO, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args, "--repo", repo],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _parse_gh_json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if proc.returncode != 0:
        raise GuardError((proc.stderr or proc.stdout or "gh command failed").strip())
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise GuardError(f"gh returned non-JSON: {exc}") from exc


async def _gather_or_raise(*aws: Any) -> list[Any]:
    results = await asyncio.gather(*aws, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


_BLOCKED_MERGE_STATE_STATUSES = frozenset({"DIRTY", "UNSTABLE", "BEHIND", "BLOCKED", "UNKNOWN"})


def _ci_status_from_rollup(rollup: list[dict[str, Any]]) -> dict[str, Any]:
    if not rollup:
        return {"ok": False, "reason": "CI_NO_CHECKS", "pending": [], "failures": []}
    pending: list[str] = []
    failures: list[str] = []
    for check in rollup:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name") or "check")
        status = str(check.get("status") or "").upper()
        conclusion = str(check.get("conclusion") or "").upper()
        if status in {"IN_PROGRESS", "PENDING", "QUEUED"}:
            pending.append(name)
            continue
        if status == "COMPLETED" and conclusion:
            if conclusion not in _CI_OK_CONCLUSIONS:
                failures.append(f"{name}:{conclusion}")
        elif status and status not in {"COMPLETED"}:
            pending.append(name)
    if pending:
        return {"ok": False, "reason": "CI_PENDING", "pending": pending, "failures": failures}
    if failures:
        return {"ok": False, "reason": "CI_FAILED", "failures": failures, "pending": pending}
    return {"ok": True, "pending": pending, "failures": failures}


def _actionable_greptile_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inline review comments only; PR-level Greptile summaries are not merge blockers."""
    return [f for f in findings if (f.get("file_path") or "").strip()]


def _comment_title(body: Any) -> str:
    return " ".join(str(body or "").split())[:120]


def _comment_identity_keys(comment: dict[str, Any], *, file_path_key: str = "path") -> list[tuple[str, str]]:
    path = str(comment.get(file_path_key) or "")
    title = _comment_title(comment.get("body"))
    keys: list[tuple[str, str]] = []
    for key_name in ("url", "id"):
        value = str(comment.get(key_name) or "")
        if value:
            keys.append((key_name, value))
    line = comment.get("line")
    if path and line not in (None, ""):
        keys.append(("path_line_title", f"{path}:{line}:{title}"))
    if path:
        keys.append(("path_title", f"{path}:{title}"))
    return keys


def _finding_thread_keys(finding: dict[str, Any]) -> list[tuple[str, str]]:
    return _comment_identity_keys(finding, file_path_key="file_path")


def _finding_thread_key(finding: dict[str, Any]) -> tuple[str, str]:
    """Compatibility key for older tests/callers; prefer _finding_thread_keys()."""
    path = str(finding.get("file_path") or "")
    title = _comment_title(finding.get("body"))
    return ("path_title", f"{path}:{title}")


def _gh_pr_review_threads(pr_number: int, *, repo: str = DEFAULT_REPO) -> list[dict[str, Any]] | None:
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        return None
    query_first = (
        "query($owner:String!,$repo:String!,$pr:Int!){"
        "repository(owner:$owner,name:$repo){pullRequest(number:$pr){"
        "reviewThreads(first:100){"
        "pageInfo{hasNextPage endCursor}"
        "nodes{isResolved comments(first:20){nodes{id author{login} path line body url}}}}}}}"
    )
    query_next = (
        "query($owner:String!,$repo:String!,$pr:Int!,$after:String!){"
        "repository(owner:$owner,name:$repo){pullRequest(number:$pr){"
        "reviewThreads(first:100,after:$after){"
        "pageInfo{hasNextPage endCursor}"
        "nodes{isResolved comments(first:20){nodes{id author{login} path line body url}}}}}}}"
    )
    threads: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(50):
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query_next if after else query_first}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={name}",
            "-F",
            f"pr={int(pr_number)}",
        ]
        if after:
            cmd.extend(["-f", f"after={after}"])
        proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True)
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(proc.stdout or "{}")
            page = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return None
        nodes = page.get("nodes") or []
        threads.extend(node for node in nodes if isinstance(node, dict))
        page_info = page.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break
    return threads


def _gh_thread_counts(pr_number: int, *, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    threads = _gh_pr_review_threads(pr_number, repo=repo)
    if threads is None:
        return {"ok": False, "unresolved": -1, "resolved_greptile_keys": []}
    resolved_greptile_keys: list[tuple[str, str]] = []
    unresolved = 0
    unresolved_greptile_threads = 0
    greptile_thread_count = 0
    for thread in threads:
        comments = ((thread.get("comments") or {}).get("nodes") or [])
        greptile_comments = [
            comment
            for comment in comments
            if "greptile" in str(((comment.get("author") or {}).get("login") or "")).lower()
        ]
        if greptile_comments:
            greptile_thread_count += 1
        if not thread.get("isResolved"):
            unresolved += 1
            if greptile_comments:
                unresolved_greptile_threads += 1
            continue
        for comment in greptile_comments:
            resolved_greptile_keys.extend(_comment_identity_keys(comment, file_path_key="path"))
    return {
        "ok": True,
        "unresolved": unresolved,
        "unresolved_greptile_threads": unresolved_greptile_threads,
        "thread_count": len(threads),
        "greptile_thread_count": greptile_thread_count,
        "resolved_greptile_keys": resolved_greptile_keys,
    }


def _filter_actionable_findings(
    findings: list[dict[str, Any]],
    *,
    pr_number: int,
    repo: str = DEFAULT_REPO,
    thread_counts: dict[str, Any] | None = None,
    clear_when_no_unresolved: bool = False,
) -> list[dict[str, Any]]:
    actionable = [f for f in _actionable_greptile_findings(findings) if not f.get("addressed")]
    counts = thread_counts if thread_counts is not None else _gh_thread_counts(pr_number, repo=repo)
    if not counts.get("ok"):
        return actionable
    if clear_when_no_unresolved and int(counts.get("unresolved") or 0) == 0:
        return []
    resolved_keys = set(counts.get("resolved_greptile_keys") or [])
    if not resolved_keys:
        return actionable
    unresolved_greptile_threads = counts.get("unresolved_greptile_threads", counts.get("unresolved"))
    allow_legacy_match = int(unresolved_greptile_threads or 0) == 0
    filtered: list[dict[str, Any]] = []
    for finding in actionable:
        keys = _finding_thread_keys(finding)
        strong_keys = [key for key in keys if key[0] != "path_title"]
        if any(key in resolved_keys for key in strong_keys):
            continue
        if allow_legacy_match and any(key in resolved_keys for key in keys):
            continue
        filtered.append(finding)
    return filtered


def _effective_greptile_confidence(
    pr_number: int,
    status: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    repo: str = DEFAULT_REPO,
) -> int:
    from greptile_client import parse_confidence_score

    confidence = int(status.get("confidenceScore") or 0)
    for row in findings:
        score = parse_confidence_score(row.get("body"))
        if score is not None:
            confidence = max(confidence, score)
    gh_confidence = _greptile_confidence_from_github_comments(pr_number, repo=repo)
    if gh_confidence is not None:
        confidence = max(confidence, gh_confidence)
    return confidence


def _greptile_clean_without_findings(
    *,
    greptile_check_success: bool,
    thread_counts: dict[str, Any],
    actionable_findings: list[dict[str, Any]],
) -> bool:
    """P2c: GitHub Greptile check passed with zero unresolved/actionable threads.

    A PR in this state has no real review work left even when the summary confidence
    is below target, so it can be treated as mergeable rather than re-triggered forever.
    """
    return bool(
        greptile_check_success
        and not actionable_findings
        and thread_counts.get("ok")
        and int(thread_counts.get("unresolved") or 0) == 0
    )


def _gh_pr_observation(pr_number: int, *, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    proc = _run_gh(
        [
            "pr",
            "view",
            str(int(pr_number)),
            "--json",
            "headRefOid,mergeable,mergeStateStatus,state,statusCheckRollup,mergedAt,mergeCommit,url",
        ],
        repo=repo,
    )
    if proc.returncode != 0:
        return {"ok": False, "reason": "GH_PR_VIEW_FAILED", "detail": (proc.stderr or proc.stdout or "").strip()}
    try:
        data = _parse_gh_json(proc)
    except GuardError as exc:
        return {"ok": False, "reason": "GH_PR_VIEW_FAILED", "detail": str(exc)}
    return {"ok": True, **data}


def _gh_greptile_check_success(observation: dict[str, Any]) -> bool:
    for check in observation.get("statusCheckRollup") or []:
        if not isinstance(check, dict):
            continue
        if str(check.get("name") or "") != "Greptile Review":
            continue
        status = str(check.get("status") or "").upper()
        conclusion = str(check.get("conclusion") or "").upper()
        if status in {"COMPLETED", "SUCCESS"} and conclusion == "SUCCESS":
            return True
    return False


def _gh_greptile_check_running(observation: dict[str, Any]) -> bool:
    for check in observation.get("statusCheckRollup") or []:
        if not isinstance(check, dict):
            continue
        if str(check.get("name") or "") != "Greptile Review":
            continue
        status = str(check.get("status") or "").upper()
        conclusion = str(check.get("conclusion") or "").upper()
        if status in {"IN_PROGRESS", "QUEUED", "PENDING", "REQUESTED"} and not conclusion:
            return True
    return False


def _github_greptile_trigger_skip(
    *,
    pr_number: int,
    repo: str,
    observation: dict[str, Any] | None,
    status: dict[str, Any] | None,
    head_sha: str | None,
    target_confidence: int = 5,
) -> dict[str, Any] | None:
    if not observation or not observation.get("ok"):
        return None
    observed_head = _head_sha_for_trigger(None, observation)
    if head_sha and observed_head and head_sha != observed_head:
        return None
    effective_head = head_sha or observed_head
    if _gh_greptile_check_running(observation):
        return {
            "skipped": True,
            "reason": "github_greptile_check_running",
            "github_head_sha": observed_head,
        }
    if not _gh_greptile_check_success(observation):
        return None
    thread_counts = _gh_thread_counts(pr_number, repo=repo)
    if not thread_counts.get("ok") or int(thread_counts.get("unresolved") or 0) != 0:
        return None
    confidence = int((status or {}).get("confidenceScore") or 0)
    gh_confidence = _greptile_confidence_from_github_comments(pr_number, repo=repo)
    if gh_confidence is not None:
        confidence = max(confidence, gh_confidence)
    if confidence < int(target_confidence):
        return None
    return {
        "skipped": True,
        "reason": "github_greptile_review_already_clear",
        "confidence": confidence,
        "github_head_sha": effective_head,
        "unresolved_review_threads": int(thread_counts.get("unresolved") or 0),
    }


def _head_sha_for_trigger(status: dict[str, Any] | None, observation: dict[str, Any] | None) -> str | None:
    if status:
        value = status.get("headSha") or status.get("headRefOid")
        if value:
            return str(value)
    if observation:
        value = observation.get("headRefOid") or observation.get("headSha")
        if value:
            return str(value)
    return None


def _should_skip_greptile_trigger(
    *,
    state: dict[str, Any],
    status: dict[str, Any] | None,
    head_sha: str | None,
    now: float,
    force: bool = False,
) -> dict[str, Any] | None:
    if force:
        return None
    if status and status.get("reviewIsRunning"):
        return {"skipped": True, "reason": "greptile_review_running"}
    last_head = str(state.get("last_triggered_head_sha") or "")
    try:
        last_at = float(state.get("last_triggered_at") or 0)
    except (TypeError, ValueError):
        last_at = 0.0
    if head_sha and last_head == head_sha and last_at and (now - last_at) < TRIGGER_COOLDOWN_SECONDS:
        return {
            "skipped": True,
            "reason": "recently_triggered_for_head",
            "cooldown_seconds": TRIGGER_COOLDOWN_SECONDS,
            "retry_after_seconds": max(0, int(TRIGGER_COOLDOWN_SECONDS - (now - last_at))),
        }
    return None


def _head_stability_skip(
    state: dict[str, Any], *, head_sha: str | None, now: float, force: bool = False
) -> dict[str, Any] | None:
    """P1: defer (re)triggering until the head SHA has been stable for the window.

    The first time a head SHA is observed we record it and start the stability clock,
    so a head that keeps drifting can never be (re)triggered fast enough to thrash the
    Greptile check. Mutates ``state`` with ``head_seen_sha`` / ``head_seen_at`` so the
    clock persists across guard iterations.
    """
    if force or HEAD_STABILITY_WINDOW_SECONDS <= 0 or not head_sha:
        return None
    if str(state.get("head_seen_sha") or "") != head_sha:
        state["head_seen_sha"] = head_sha
        state["head_seen_at"] = now
    seen_at = _float_state(state.get("head_seen_at"), now)
    stable_for = max(0.0, now - seen_at)
    if stable_for >= HEAD_STABILITY_WINDOW_SECONDS:
        return None
    return {
        "skipped": True,
        "reason": "head_not_stable",
        "head_sha": head_sha,
        "head_stable_seconds": int(stable_for),
        "head_stability_window_seconds": HEAD_STABILITY_WINDOW_SECONDS,
        "retry_after_seconds": max(1, int(HEAD_STABILITY_WINDOW_SECONDS - stable_for)),
    }


async def trigger_review_safely(
    *,
    pr_number: int,
    repo: str = DEFAULT_REPO,
    default_branch: str = DEFAULT_BASE_BRANCH,
    branch: str | None = None,
    status: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    force: bool = False,
    source: str = "greploop_guard",
    write_state: bool = True,
) -> dict[str, Any]:
    """Trigger Greptile once per PR/head within the cooldown window."""
    from greptile_client import get_pr_status, trigger_review

    pr_number = int(pr_number)
    active_state = state if state is not None else _load_status(pr_number)
    active_status = status if status is not None else await get_pr_status(
        repo=repo,
        pr_number=pr_number,
        default_branch=default_branch,
    )
    head_sha = _head_sha_for_trigger(active_status, None)
    now = time.time()
    skipped = _should_skip_greptile_trigger(
        state=active_state,
        status=active_status,
        head_sha=head_sha,
        now=now,
        force=force,
    )
    observation = None
    if not skipped and not force:
        observation = _gh_pr_observation(pr_number, repo=repo)
        head_sha = _head_sha_for_trigger(active_status, observation)
        skipped = _github_greptile_trigger_skip(
            pr_number=pr_number,
            repo=repo,
            observation=observation,
            status=active_status,
            head_sha=head_sha,
        )
    if not skipped:
        skipped = _should_skip_greptile_trigger(
            state=active_state,
            status=active_status,
            head_sha=head_sha,
            now=now,
            force=force,
        )
    if not skipped and not force:
        skipped = _repo_greptile_capacity_skip(pr_number=pr_number, now=now, repo=repo)
    if not skipped:
        skipped = _head_stability_skip(active_state, head_sha=head_sha, now=now, force=force)

    if skipped:
        decision = {
            "success": True,
            "triggered": False,
            "prNumber": pr_number,
            "repo": repo,
            "headSha": head_sha,
            "source": source,
            **skipped,
        }
        active_state["last_trigger_decision"] = decision
        if write_state:
            _write_json(pr_number, "status.json", active_state)
        return decision

    result = await trigger_review(
        repo=repo,
        pr_number=pr_number,
        default_branch=default_branch,
        branch=branch,
    )
    trigger_success = bool(result.get("success", True)) if isinstance(result, dict) else True
    if trigger_success:
        active_state["last_triggered_head_sha"] = head_sha
        active_state["last_triggered_at"] = now
        active_state["last_trigger_source"] = source
    active_state["last_trigger_decision"] = {
        "success": trigger_success,
        "triggered": trigger_success,
        "prNumber": pr_number,
        "repo": repo,
        "headSha": head_sha,
        "source": source,
        "response": result,
    }
    if write_state:
        _write_json(pr_number, "status.json", active_state)
    return result


async def trigger_review_with_guard_lock(
    *,
    pr_number: int,
    repo: str = DEFAULT_REPO,
    default_branch: str = DEFAULT_BASE_BRANCH,
    branch: str | None = None,
    force: bool = False,
    source: str = "mcp:greptile_trigger_review",
) -> dict[str, Any]:
    try:
        with acquire_lock(int(pr_number)):
            return await trigger_review_safely(
                pr_number=int(pr_number),
                repo=repo,
                default_branch=default_branch,
                branch=branch,
                force=force,
                source=source,
            )
    except GuardError as exc:
        return {
            "success": False,
            "triggered": False,
            "skipped": True,
            "reason": "guard_already_running",
            "detail": str(exc),
            "prNumber": int(pr_number),
            "repo": repo,
            "source": source,
        }


def _greptile_confidence_from_github_comments(
    pr_number: int, *, repo: str = DEFAULT_REPO
) -> int | None:
    """Parse Greptile confidence from PR issue comments (summary posts)."""
    from greptile_client import parse_confidence_score

    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{int(pr_number)}/comments", "--paginate"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for row in reversed(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        login = str((row.get("user") or {}).get("login") or "").lower()
        if "greptile" not in login:
            continue
        score = parse_confidence_score(row.get("body"))
        if score is not None:
            return score
    return None


def _gh_unresolved_review_thread_count(
    pr_number: int, *, repo: str = DEFAULT_REPO
) -> int | None:
    """Count open GitHub review threads.

    Returns ``None`` when the GitHub API check fails (caller must block merge).
    """
    counts = _gh_thread_counts(pr_number, repo=repo)
    if not counts.get("ok"):
        return None
    return int(counts.get("unresolved") or 0)


def _gh_mergeable_state(
    pr_number: int,
    *,
    repo: str = DEFAULT_REPO,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if observation and observation.get("ok"):
        data = observation
    else:
        proc = _run_gh(
            [
                "pr",
                "view",
                str(int(pr_number)),
                "--json",
                "mergeable,mergeStateStatus,state,statusCheckRollup",
            ],
            repo=repo,
        )
        if proc.returncode != 0:
            return {"ok": False, "reason": "GH_PR_VIEW_FAILED", "detail": (proc.stderr or proc.stdout or "").strip()}
        data = _parse_gh_json(proc)
    if str(data.get("state") or "").upper() == "MERGED":
        return {"ok": True, "already_merged": True, "mergeStateStatus": data.get("mergeStateStatus")}
    mergeable = str(data.get("mergeable") or "").upper()
    merge_state = str(data.get("mergeStateStatus") or "").upper()
    if mergeable != "MERGEABLE" or merge_state in _BLOCKED_MERGE_STATE_STATUSES:
        return {
            "ok": False,
            "reason": "GH_NOT_MERGEABLE",
            "mergeable": mergeable,
            "mergeStateStatus": data.get("mergeStateStatus"),
        }
    ci = _ci_status_from_rollup(data.get("statusCheckRollup") or [])
    if not ci.get("ok"):
        return {**ci, "mergeStateStatus": data.get("mergeStateStatus")}
    return {"ok": True, "mergeStateStatus": data.get("mergeStateStatus"), "ci": ci}


async def assess_merge_readiness(
    pr_number: int,
    *,
    target_confidence: int = 5,
    repo: str = DEFAULT_REPO,
    default_branch: str = DEFAULT_BASE_BRANCH,
) -> dict[str, Any]:
    """Return whether a PR is safe to squash-merge via normal gh (no admin/force)."""
    from greptile_client import get_pr_status, list_pr_comments

    pr_number = int(pr_number)
    status, comments = await _gather_or_raise(
        get_pr_status(repo=repo, pr_number=pr_number, default_branch=default_branch),
        list_pr_comments(
            repo=repo,
            pr_number=pr_number,
            default_branch=default_branch,
            unaddressed_only=False,
        ),
    )
    findings = comments.get("findings") or []
    thread_counts = _gh_thread_counts(pr_number, repo=repo)
    confidence = _effective_greptile_confidence(pr_number, status, findings, repo=repo)
    gh_observation = _gh_pr_observation(pr_number, repo=repo)
    greptile_check_success = _gh_greptile_check_success(gh_observation)
    actionable = _filter_actionable_findings(
        findings,
        pr_number=pr_number,
        repo=repo,
        thread_counts=thread_counts,
        clear_when_no_unresolved=greptile_check_success,
    )
    unresolved_threads = int(thread_counts.get("unresolved") or 0) if thread_counts.get("ok") else None
    clean_without_findings = _greptile_clean_without_findings(
        greptile_check_success=greptile_check_success,
        thread_counts=thread_counts,
        actionable_findings=actionable,
    )
    # P2c: a clean check with no findings is mergeable even below confidence target.
    confidence_satisfied = confidence >= int(target_confidence) or (
        CLEAN_MERGE_IGNORES_CONFIDENCE and clean_without_findings
    )
    stale_running_review = (
        status.get("reviewIsRunning")
        and clean_without_findings
        and confidence_satisfied
    )
    blockers: list[str] = []
    if status.get("reviewIsRunning") and not stale_running_review:
        blockers.append("GREPTILE_REVIEW_RUNNING")
    if unresolved_threads is None:
        blockers.append("GREPTILE_THREAD_CHECK_FAILED")
    elif unresolved_threads:
        blockers.append(f"GREPTILE_UNRESOLVED_THREADS:{unresolved_threads}")
    if not confidence_satisfied:
        blockers.append(f"GREPTILE_CONFIDENCE:{confidence}<{target_confidence}")
    if actionable:
        blockers.append(f"GREPTILE_ACTIONABLE_FINDINGS:{len(actionable)}")
    gh_state = _gh_mergeable_state(pr_number, repo=repo, observation=gh_observation)
    if not gh_state.get("ok"):
        blockers.append(str(gh_state.get("reason") or "GH_NOT_READY"))
    return {
        "ready": not blockers,
        "blockers": blockers,
        "greptile": status,
        "unaddressed_count": len(actionable),
        "unresolved_review_threads": unresolved_threads if unresolved_threads is not None else -1,
        "gh": gh_state,
        "target_confidence": int(target_confidence),
    }


def _only_behind_blocks(assessment: dict[str, Any]) -> bool:
    """True iff the SOLE reason the PR is not ready is a strict-mode BEHIND branch.

    Requires the Greptile side to be fully clear (no confidence / unresolved-
    thread / actionable-finding blockers) so update-branch never thrashes a
    re-review that still has open work — only a genuinely done PR that just
    needs to catch up to base.
    """
    blockers = set(assessment.get("blockers") or [])
    gh = assessment.get("gh") or {}
    return (
        blockers == {"GH_NOT_MERGEABLE"}
        and str(gh.get("mergeStateStatus") or "").upper() == "BEHIND"
    )


def _gh_update_branch(pr_number: int, *, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    """Bring a BEHIND PR branch up to date with base via `gh pr update-branch`.

    Safe by construction: it merges base into the PR branch (never force, never
    rebase-onto-main here); the merge still passes through every gate afterward.
    Never runs on DIRTY (real conflicts) — `_only_behind_blocks` gates that out,
    and a conflict makes update-branch fail loudly rather than guess.
    """
    proc = _run_gh(["pr", "update-branch", str(int(pr_number))], repo=repo)
    ok = proc.returncode == 0
    return {"ok": ok, "detail": (proc.stderr or proc.stdout or "").strip()}


def _update_branch_cooldown_ok(state: dict[str, Any], *, now: float) -> bool:
    last = float(state.get("last_update_branch_at") or 0)
    return (now - last) >= UPDATE_BRANCH_COOLDOWN_SECONDS


async def merge_pr_when_ready(
    pr_number: int,
    *,
    target_confidence: int = 5,
    repo: str = DEFAULT_REPO,
    default_branch: str = DEFAULT_BASE_BRANCH,
) -> dict[str, Any]:
    """Squash-merge via `gh pr merge` when Greptile + CI are clear. Never uses admin or force."""
    pr_number = int(pr_number)
    from greptile_client import get_pr_status

    with acquire_lock(pr_number):
        state = _load_status(pr_number)
        status = await get_pr_status(repo=repo, pr_number=pr_number, default_branch=default_branch)
        gh_observation = _gh_pr_observation(pr_number, repo=repo)
        if status.get("reviewIsRunning") and _gh_greptile_check_running(gh_observation):
            wait = _update_greptile_wait_state(
                state,
                now=time.time(),
                status=status,
                confidence=int(status.get("confidenceScore") or 0),
                actionable_count=0,
                summary_count=0,
            )
            # P2b: a genuinely stuck review must escalate here too. Without this the
            # merge path overwrote run_guard_once's BLOCKED_GREPTILE_STUCK with
            # WAITING_GREPTILE, so a hung review never reached Hermes escalation.
            if wait["stuck"]:
                state["terminal_state"] = "BLOCKED_GREPTILE_STUCK"
                state["merge_blockers"] = ["GREPTILE_REVIEW_STUCK"]
                _write_json(pr_number, "status.json", state)
                _record_guardrail(
                    pr_number,
                    f"merge path: Greptile review stayed active for {wait['elapsed_seconds']}s past wait limit",
                )
                return {
                    "ok": False,
                    "state": "BLOCKED_GREPTILE_STUCK",
                    "blockers": ["GREPTILE_REVIEW_STUCK"],
                    "retry_after_seconds": wait["retry_after_seconds"],
                    "wait": wait,
                    "assessment": {
                        "ready": False,
                        "blockers": ["GREPTILE_REVIEW_STUCK"],
                        "greptile": status,
                        "gh": gh_observation,
                    },
                }
            state["terminal_state"] = "WAITING_GREPTILE"
            state["merge_blockers"] = ["GREPTILE_REVIEW_RUNNING"]
            _write_json(pr_number, "status.json", state)
            return {
                "ok": False,
                "state": "WAITING_GREPTILE",
                "blockers": ["GREPTILE_REVIEW_RUNNING"],
                "retry_after_seconds": wait["retry_after_seconds"],
                "wait": wait,
                "assessment": {
                    "ready": False,
                    "blockers": ["GREPTILE_REVIEW_RUNNING"],
                    "greptile": status,
                    "gh": gh_observation,
                },
            }
        assessment = await assess_merge_readiness(
            pr_number,
            target_confidence=target_confidence,
            repo=repo,
            default_branch=default_branch,
        )
        if not assessment["ready"]:
            # Autonomous close: if the PR is otherwise done and only BEHIND base,
            # bring it current instead of stranding it for a human. Gated,
            # cooldown-guarded, and only when BEHIND is the sole blocker.
            now = time.time()
            if (
                AUTO_UPDATE_BRANCH
                and _only_behind_blocks(assessment)
                and _update_branch_cooldown_ok(state, now=now)
            ):
                upd = _gh_update_branch(pr_number, repo=repo)
                state["last_update_branch_at"] = now
                # terminal_state MUST mirror the returned state so a caller
                # reading status.json and a caller reading the dict agree.
                returned_state = "UPDATING_BRANCH" if upd["ok"] else "BLOCKED_MERGE_FAILED"
                state["terminal_state"] = returned_state
                if not upd["ok"]:
                    state["merge_blockers"] = assessment["blockers"]
                    state["merge_error"] = (upd.get("detail") or "")[:2000]
                _write_json(pr_number, "status.json", state)
                _record_guardrail(
                    pr_number,
                    f"branch BEHIND base — ran gh pr update-branch (ok={upd['ok']}): {upd['detail'][:200]}",
                )
                return {
                    "ok": False,
                    "state": returned_state,
                    "blockers": assessment["blockers"],
                    "update_branch": upd,
                    "retry_after_seconds": UPDATE_BRANCH_COOLDOWN_SECONDS,
                    "assessment": assessment,
                }
            state["terminal_state"] = "BLOCKED_NOT_READY"
            state["merge_blockers"] = assessment["blockers"]
            _write_json(pr_number, "status.json", state)
            _record_guardrail(pr_number, f"merge blocked: {assessment['blockers']}")
            return {
                "ok": False,
                "state": "BLOCKED_NOT_READY",
                "blockers": assessment["blockers"],
                "assessment": assessment,
            }
        gh_state = assessment.get("gh") or {}
        if gh_state.get("already_merged"):
            proc = _run_gh(
                ["pr", "view", str(pr_number), "--json", "mergeCommit,url,state"],
                repo=repo,
            )
            merged = _parse_gh_json(proc) if proc.returncode == 0 else {}
            state["terminal_state"] = "MERGED"
            _write_json(pr_number, "status.json", state)
            return {
                "ok": True,
                "state": "MERGED",
                "already_merged": True,
                "merge_commit": (merged.get("mergeCommit") or {}).get("oid"),
                "pr_url": merged.get("url"),
            }
        proc = _run_gh(["pr", "merge", str(pr_number), "--squash"], repo=repo)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            state["terminal_state"] = "BLOCKED_MERGE_FAILED"
            state["merge_error"] = detail[:2000]
            _write_json(pr_number, "status.json", state)
            _record_guardrail(pr_number, f"gh pr merge failed: {detail[:500]}")
            return {
                "ok": False,
                "state": "BLOCKED_MERGE_FAILED",
                "error": detail,
                "assessment": assessment,
            }
        view = _run_gh(
            ["pr", "view", str(pr_number), "--json", "mergeCommit,url,state,mergedAt"],
            repo=repo,
        )
        merged = _parse_gh_json(view) if view.returncode == 0 else {}
        state["terminal_state"] = "MERGED"
        state["merge_commit"] = (merged.get("mergeCommit") or {}).get("oid")
        state["pr_url"] = merged.get("url")
        _write_json(pr_number, "status.json", state)
        _append_progress(pr_number, f"merged squash {state.get('merge_commit') or ''}".strip())
        return {
            "ok": True,
            "state": "MERGED",
            "merge_commit": state.get("merge_commit"),
            "pr_url": merged.get("url"),
            "merged_at": merged.get("mergedAt"),
            "assessment": assessment,
        }


# --- Local serial merge queue ------------------------------------------------
#
# A locally-driven merge queue that processes one labelled, ready PR per cycle.
# When a ready PR is only behind the base branch, it is rebased forward (linear,
# NOT a merge-update commit, so Greptile re-reviews the fresh head) and the cycle
# stops; a later cycle merges it once green. This reproduces the GitHub
# merge-queue "test against the combined state" guarantee using ordinary PR
# commits, so it does NOT depend on Greptile posting checks on merge_group refs.
#
# Loop contract (Job/Inputs/Allowed/Forbidden/Output/Evaluation):
#   JOB: advance or merge exactly one labelled, ready PR per cycle.
#   INPUTS: open, non-draft PRs carrying MERGE_QUEUE_LABEL; their Greptile/CI/
#     thread state via assess_merge_readiness.
#   ALLOWED: linear rebase + force-push of a queue-labelled branch in a disposable
#     worktree; squash-merge via merge_pr_when_ready (gh pr merge --squash).
#   FORBIDDEN: NEVER --admin; NEVER force-merge or bypass branch protection; NEVER
#     merge a PR not assessed READY; NEVER act on PRs lacking the queue label;
#     NEVER resolve rebase conflicts automatically (abort + report); NEVER touch
#     the live checkout (rebase happens in a throwaway worktree); at most one
#     rebase OR one merge per cycle; disabled unless explicitly enabled.
#   OUTPUT: at most one PR merged or one branch advanced per cycle; a structured
#     report of the action and any skipped PRs.
#   EVALUATION: never merges a red/behind/unreviewed PR; the queue drains serially
#     across cycles; main's required checks always pass post-merge.

MERGE_QUEUE_LABEL = os.environ.get("ZOE_MERGE_QUEUE_LABEL", "auto-merge")
MERGE_QUEUE_KILL_FILE = STATE_ROOT / "merge_queue.disabled"
MERGE_QUEUE_MAX_CANDIDATES = int(os.environ.get("ZOE_MERGE_QUEUE_MAX_CANDIDATES", "50"))


def _merge_queue_enabled() -> bool:
    """Disabled by default. Requires ZOE_MERGE_QUEUE_ENABLED truthy AND no kill file."""
    enabled = str(os.environ.get("ZOE_MERGE_QUEUE_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return enabled and not MERGE_QUEUE_KILL_FILE.exists()


def _merge_queue_candidates(*, repo: str = DEFAULT_REPO) -> list[dict[str, Any]] | None:
    """Open, non-draft PRs carrying the queue label, oldest-first (FIFO).

    Returns ``None`` (not ``[]``) when the ``gh pr list`` call fails, so a broken
    gh (auth/rate-limit/network) is never mistaken for an empty queue.
    """
    proc = _run_gh(
        [
            "pr",
            "list",
            "--state",
            "open",
            "--label",
            MERGE_QUEUE_LABEL,
            "--limit",
            str(MERGE_QUEUE_MAX_CANDIDATES),
            "--json",
            "number,isDraft,headRefName,createdAt",
        ],
        repo=repo,
    )
    if proc.returncode != 0:
        return None  # signal failure — never conflate a broken gh with "no PRs"
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    candidates = [
        {
            "number": int(row.get("number")),
            "branch": str(row.get("headRefName") or ""),
            "created_at": str(row.get("createdAt") or ""),
        }
        for row in rows
        if isinstance(row, dict) and row.get("number") is not None and not row.get("isDraft")
    ]
    candidates.sort(key=lambda c: (c["created_at"], c["number"]))
    return candidates


def _blocked_only_behind(assessment: dict[str, Any]) -> bool:
    """True iff the PR's sole obstacle is being behind the base branch.

    Greptile must be clear (no review running, no unresolved threads, confidence
    met, no actionable findings) and the merge state must be exactly BEHIND. Any
    Greptile/CI/thread blocker means a rebase would not unblock it.
    """
    gh = assessment.get("gh") or {}
    if str(gh.get("mergeStateStatus") or "").upper() != "BEHIND":
        return False
    blockers = assessment.get("blockers") or []
    if not blockers:
        return False
    return all(str(blocker) == "GH_NOT_MERGEABLE" for blocker in blockers)


def _rebase_pr_branch(
    pr_number: int,
    branch: str,
    *,
    repo: str = DEFAULT_REPO,
    default_branch: str = DEFAULT_BASE_BRANCH,
) -> dict[str, Any]:
    """Rebase a PR branch linearly onto the base branch and force-push.

    Runs in a disposable worktree so the live checkout is never touched. Linear
    rebase (not a merge commit) keeps Greptile reviewing the new head. On
    conflict, abort and report — conflicts are never resolved automatically.
    """
    if not branch:
        return {"ok": False, "pr": pr_number, "error": "NO_BRANCH"}
    fetch = _run_git(["fetch", "origin", default_branch, branch], check=False)
    if fetch.returncode != 0:
        return {"ok": False, "pr": pr_number, "error": "FETCH_FAILED", "detail": (fetch.stderr or "").strip()[:500]}
    tmp = STATE_ROOT / f"mq-rebase-{branch.replace('/', '_')}"
    _run_git(["worktree", "remove", "--force", str(tmp)], check=False)
    add = _run_git(
        ["worktree", "add", "--force", "--detach", str(tmp), f"origin/{branch}"], check=False
    )
    if add.returncode != 0:
        return {"ok": False, "pr": pr_number, "error": "WORKTREE_FAILED", "detail": (add.stderr or "").strip()[:500]}
    try:
        rebase = _run_git(["-C", str(tmp), "rebase", f"origin/{default_branch}"], check=False)
        if rebase.returncode != 0:
            _run_git(["-C", str(tmp), "rebase", "--abort"], check=False)
            return {
                "ok": False,
                "pr": pr_number,
                "error": "REBASE_CONFLICT",
                "detail": (rebase.stdout or rebase.stderr or "").strip()[:500],
            }
        push = _run_git(
            ["-C", str(tmp), "push", "--force-with-lease", "origin", f"HEAD:{branch}"],
            check=False,
        )
        if push.returncode != 0:
            return {"ok": False, "pr": pr_number, "error": "PUSH_FAILED", "detail": (push.stderr or "").strip()[:500]}
        return {"ok": True, "pr": pr_number, "branch": branch}
    finally:
        _run_git(["worktree", "remove", "--force", str(tmp)], check=False)


async def run_merge_queue(
    *,
    repo: str = DEFAULT_REPO,
    default_branch: str = DEFAULT_BASE_BRANCH,
    target_confidence: int = 5,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one serial merge-queue cycle: advance or merge exactly one labelled PR.

    Strictly serial and safe: at most one rebase OR one merge per call. Disabled
    unless ``ZOE_MERGE_QUEUE_ENABLED`` is truthy and no kill file exists. With
    ``dry_run`` it decides but performs no mutation (and ignores the enable gate so
    it can be inspected safely).
    """
    if not dry_run and not _merge_queue_enabled():
        return {
            "ok": True,
            "action": "disabled",
            "detail": "set ZOE_MERGE_QUEUE_ENABLED=1 and remove the kill file to enable",
        }
    candidates = _merge_queue_candidates(repo=repo)
    if candidates is None:
        return {
            "ok": False,
            "action": "list_failed",
            "detail": "gh pr list failed (auth/rate-limit/network); not treating as idle",
        }
    if not candidates:
        return {"ok": True, "action": "idle", "checked": 0, "skipped": []}
    skipped: list[dict[str, Any]] = []
    for cand in candidates:
        pr_number = cand["number"]
        assessment = await assess_merge_readiness(
            pr_number,
            target_confidence=target_confidence,
            repo=repo,
            default_branch=default_branch,
        )
        if assessment.get("ready"):
            if dry_run:
                return {
                    "ok": True,
                    "action": "would_merge",
                    "pr": pr_number,
                    "skipped": skipped,
                    "checked": len(candidates),
                }
            merge = await merge_pr_when_ready(
                pr_number,
                target_confidence=target_confidence,
                repo=repo,
                default_branch=default_branch,
            )
            return {
                "ok": bool(merge.get("ok")),
                "action": "merged" if merge.get("ok") else "merge_attempt_failed",
                "pr": pr_number,
                "merge": merge,
                "skipped": skipped,
                "checked": len(candidates),
            }
        if _blocked_only_behind(assessment):
            if dry_run:
                return {
                    "ok": True,
                    "action": "would_rebase",
                    "pr": pr_number,
                    "branch": cand["branch"],
                    "skipped": skipped,
                    "checked": len(candidates),
                }
            rebase = _rebase_pr_branch(
                pr_number, cand["branch"], repo=repo, default_branch=default_branch
            )
            return {
                "ok": bool(rebase.get("ok")),
                "action": "rebased" if rebase.get("ok") else "rebase_failed",
                "pr": pr_number,
                "rebase": rebase,
                "skipped": skipped,
                "checked": len(candidates),
            }
        skipped.append({"pr": pr_number, "blockers": assessment.get("blockers")})
    return {"ok": True, "action": "idle", "checked": len(candidates), "skipped": skipped}


async def run_guard_once(
    pr_number: int, *, packet_only: bool = False, target_confidence: int = 5
) -> dict[str, Any]:
    from greptile_client import DEFAULT_REPO, get_pr_status, list_pr_comments

    try:
        pr_number = int(pr_number)
    except (TypeError, ValueError) as exc:
        raise GuardError(f"invalid PR number: {pr_number!r}") from exc
    task: dict[str, Any] = {"target_confidence": target_confidence}
    task_id = f"pr-{pr_number}"
    with acquire_lock(pr_number):
        state = _load_status(pr_number)
        status, comments = await _gather_or_raise(
            get_pr_status(repo=DEFAULT_REPO, pr_number=pr_number, default_branch=DEFAULT_BASE_BRANCH),
            list_pr_comments(repo=DEFAULT_REPO, pr_number=pr_number, default_branch=DEFAULT_BASE_BRANCH),
        )
        findings = comments.get("findings") or []
        thread_counts = _gh_thread_counts(pr_number, repo=DEFAULT_REPO)
        confidence = _effective_greptile_confidence(pr_number, status, findings, repo=DEFAULT_REPO)
        gh_observation = _gh_pr_observation(pr_number, repo=DEFAULT_REPO)
        pr_head_sha = _effective_pr_head_sha(status, gh_observation)
        greptile_check_success = _gh_greptile_check_success(gh_observation)
        actionable_findings = _filter_actionable_findings(
            findings,
            pr_number=pr_number,
            repo=DEFAULT_REPO,
            thread_counts=thread_counts,
            clear_when_no_unresolved=greptile_check_success,
        )
        target = int(task.get("target_confidence") or 5)
        clean_without_findings = _greptile_clean_without_findings(
            greptile_check_success=greptile_check_success,
            thread_counts=thread_counts,
            actionable_findings=actionable_findings,
        )
        # P2c: clean check + no findings is mergeable even below the confidence target.
        confidence_satisfied = confidence >= target or (
            CLEAN_MERGE_IGNORES_CONFIDENCE and clean_without_findings
        )
        stale_running_review = (
            status.get("reviewIsRunning")
            and clean_without_findings
            and confidence_satisfied
        )
        if status.get("reviewIsRunning") and not stale_running_review:
            wait = _update_greptile_wait_state(
                state,
                now=time.time(),
                status=status,
                confidence=confidence,
                actionable_count=len(actionable_findings),
                summary_count=max(0, len(findings) - len(actionable_findings)),
            )
            if wait["stuck"]:
                state["terminal_state"] = "BLOCKED_GREPTILE_STUCK"
                _write_json(pr_number, "status.json", state)
                _record_guardrail(
                    pr_number,
                    f"Greptile review stayed active for {wait['elapsed_seconds']}s past wait limit",
                )
                return {"ok": False, "state": "BLOCKED_GREPTILE_STUCK", "greptile": status, "wait": wait}
            state["terminal_state"] = "WAITING_GREPTILE"
            _write_json(pr_number, "status.json", state)
            return {"ok": True, "state": "WAITING_GREPTILE", "greptile": status, "wait": wait}
        _clear_greptile_wait_state(state)
        progress_key = f"{pr_head_sha}:{confidence}:{len(actionable_findings)}:{len(findings)}"
        blocked = _update_circuit_breakers(pr_number, state, progress_key)
        if blocked:
            state["terminal_state"] = blocked
            _write_json(pr_number, "status.json", state)
            _record_guardrail(pr_number, blocked)
            return {"ok": False, "state": blocked, "status": state}
        state["greptile"] = {
            "status": status.get("reviewCompleteness") or "reviewed",
            "confidence": confidence,
            "unaddressed_count": len(actionable_findings),
            "summary_count": max(0, len(findings) - len(actionable_findings)),
        }
        _write_json(pr_number, "status.json", state)
        if clean_without_findings and confidence_satisfied:
            state["terminal_state"] = "READY_TO_MERGE"
            state.pop("no_findings_retrigger_count", None)
            state.pop("no_findings_retrigger_key", None)
            _write_json(pr_number, "status.json", state)
            return {"ok": True, "state": "READY_TO_MERGE", "greptile": {**status, "confidenceScore": confidence}}
        if not actionable_findings:
            # P2c: bound re-triggers for a clean-but-sub-confidence (or not-yet-complete)
            # review so the guard escalates to Hermes instead of looping forever.
            # Anchor the key to the head SHA only: confidence can fluctuate between
            # polls (ping-pong scores) and must NOT reset the bound. The head SHA
            # changes only when real new commits land, which correctly resets it.
            retrigger_key = pr_head_sha
            if state.get("no_findings_retrigger_key") == retrigger_key:
                state["no_findings_retrigger_count"] = int(state.get("no_findings_retrigger_count") or 0) + 1
            else:
                state["no_findings_retrigger_key"] = retrigger_key
                state["no_findings_retrigger_count"] = 1
            if state["no_findings_retrigger_count"] > NO_FINDINGS_RETRIGGER_LIMIT:
                state["terminal_state"] = "ESCALATE_HERMES"
                _write_json(pr_number, "status.json", state)
                _record_guardrail(
                    pr_number,
                    "Greptile review made no progress below confidence target after "
                    f"{state['no_findings_retrigger_count']} re-triggers; escalating to Hermes",
                )
                return {
                    "ok": False,
                    "state": "ESCALATE_HERMES",
                    "reason": "greptile_no_progress_below_confidence",
                    "greptile": {**status, "confidenceScore": confidence},
                }
            triggered = await trigger_review_safely(
                pr_number=pr_number,
                repo=DEFAULT_REPO,
                default_branch=DEFAULT_BASE_BRANCH,
                status=status,
                state=state,
                source="greploop_guard:no_actionable_findings",
                write_state=False,
            )
            state["terminal_state"] = "WAITING_GREPTILE"
            _write_json(pr_number, "status.json", {**state, "triggered_review": triggered})
            return {"ok": True, "state": "WAITING_GREPTILE", "triggered_review": triggered}
        finding = actionable_findings[0]
        if any(str(finding.get("file_path") or "").startswith(prefix) for prefix in HIGH_RISK_PREFIXES):
            state["terminal_state"] = "ESCALATE_HERMES"
            _write_json(pr_number, "status.json", state)
            return {"ok": False, "state": "ESCALATE_HERMES", "finding": finding}
        packet = _packet_for_finding(pr_number, status, finding, head_sha=pr_head_sha)
        _write_json(pr_number, "last_packet.json", packet.to_dict())
        _append_progress(pr_number, f"packet {_finding_hash(finding)} for {finding.get('file_path')}")
        if packet_only:
            state["terminal_state"] = "PACKET_READY"
            _write_json(pr_number, "status.json", state)
            return {"ok": True, "state": "PACKET_READY", "packet": packet.to_dict()}
        pre_run_sha = _local_head_sha()
        runner_status, runner_output = await _run_cheap_agent(packet, task_id=task_id)
        analysis = analyze_result(packet, runner_output, pre_run_sha=pre_run_sha)
        result = {"runner_status": runner_status, "analysis": analysis, "output": runner_output[:MAX_OUTPUT_CHARS]}
        _write_json(pr_number, "last_result.json", result)
        if runner_status != "OK" or analysis["classification"] != "APPLIED":
            state["terminal_state"] = analysis.get("classification") if runner_status == "OK" else runner_status
            _write_json(pr_number, "status.json", state)
            return {"ok": False, "state": state["terminal_state"], "result": result}
        triggered = await trigger_review_safely(
            pr_number=pr_number,
            repo=DEFAULT_REPO,
            default_branch=DEFAULT_BASE_BRANCH,
            state=state,
            source="greploop_guard:cheap_agent_applied",
            write_state=False,
        )
        state["terminal_state"] = "WAITING_GREPTILE"
        _write_json(pr_number, "status.json", {**state, "triggered_review": triggered})
        return {"ok": True, "state": "WAITING_GREPTILE", "result": result, "triggered_review": triggered}
