"""Bounded Greptile PR guard for cheap-model repair packets.

The guard keeps deterministic state outside model context. Cheap models get one
validated packet at a time; Hermes remains the escalation path for broad or risky
work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_REPO = os.environ.get("ZOE_GITHUB_REPO", "jason-easyazz/zoe-ai-assistant")
DEFAULT_BASE_BRANCH = os.environ.get("ZOE_GITHUB_DEFAULT_BRANCH", "main")
REPO_ROOT = Path(os.environ.get("ZOE_ASSISTANT_ROOT", Path(__file__).resolve().parents[2]))
STATE_ROOT = Path(os.environ.get("ZOE_PR_GUARD_STATE_DIR", "/home/zoe/assistant/.cursor/tmp/pr_guard"))
LOCK_TTL_SECONDS = int(os.environ.get("ZOE_PR_GUARD_LOCK_TTL_SECONDS", "1800"))
MAX_ITERATIONS = int(os.environ.get("ZOE_PR_GUARD_MAX_ITERATIONS", "5"))
NO_PROGRESS_LIMIT = int(os.environ.get("ZOE_PR_GUARD_NO_PROGRESS_LIMIT", "3"))
SAME_ERROR_LIMIT = int(os.environ.get("ZOE_PR_GUARD_SAME_ERROR_LIMIT", "3"))
MAX_COST_USD = float(os.environ.get("ZOE_PR_GUARD_MAX_COST_USD", "0.25"))
MAX_OUTPUT_CHARS = int(os.environ.get("ZOE_PR_GUARD_MAX_OUTPUT_CHARS", "12000"))

FORBIDDEN_ACTIONS = [
    "merge_pr",
    "force_push",
    "manual_deploy",
    "delete_branch",
    "amend_commit",
    "bypass_hooks",
]
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
    path.write_text(json.dumps(redact(payload), indent=2, sort_keys=True) + "\n")


def read_guard_state(pr_number: int) -> dict[str, Any]:
    path = _json_path(pr_number, "status.json")
    if not path.exists():
        return {"pr": int(pr_number), "state": "MISSING"}
    return json.loads(path.read_text())


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


@contextmanager
def acquire_lock(pr_number: int):
    path = _json_path(pr_number, "lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = {}
        created = float(existing.get("created_at") or 0)
        if now - created < LOCK_TTL_SECONDS:
            raise GuardError(f"guard already running for PR #{pr_number}")
        path.unlink(missing_ok=True)
    payload = {"pid": os.getpid(), "created_at": now, "owner": "zoe-greploop-guard"}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, text=True, capture_output=True, check=check)


def _local_head_sha() -> str | None:
    try:
        return _run_git(["rev-parse", "HEAD"]).stdout.strip()
    except Exception:
        return None


def _diff_files() -> list[str]:
    proc = _run_git(["diff", "--name-only"], check=False)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _diff_changed_lines() -> int:
    proc = _run_git(["diff", "--numstat"], check=False)
    total = 0
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


def _packet_for_finding(pr_number: int, status: dict[str, Any], finding: dict[str, Any]) -> GuardPacket:
    allowed_file = finding.get("file_path") or ""
    if not allowed_file:
        raise GuardError("BLOCKED_MISSING_CONTEXT: finding has no file path")
    packet = GuardPacket(
        task_type="FIX_GREPTILE_FINDING",
        pr=int(pr_number),
        head_sha=status.get("headSha") or _local_head_sha(),
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


def analyze_result(packet: GuardPacket, result_text: str) -> dict[str, Any]:
    changed = _diff_files()
    changed_lines = _diff_changed_lines()
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
    if state.get("state") == "MISSING":
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
    except Exception:
        return


async def _run_cheap_agent(packet: GuardPacket, *, task_id: str | None = None) -> tuple[str, str]:
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


async def run_guard_once(task_id: str, *, packet_only: bool = False) -> dict[str, Any]:
    from engineering_workflow import get_engineering_task, update_greptile_state
    from greptile_client import DEFAULT_REPO, get_pr_status, list_pr_comments, trigger_review

    task = await get_engineering_task(task_id)
    if not task:
        raise GuardError(f"engineering task not found: {task_id}")
    pr_number = task.get("pr_number")
    if not pr_number:
        raise GuardError("engineering task has no PR number")
    pr_number = int(pr_number)
    with acquire_lock(pr_number):
        state = _load_status(pr_number)
        status = await get_pr_status(repo=DEFAULT_REPO, pr_number=pr_number, default_branch=DEFAULT_BASE_BRANCH)
        comments = await list_pr_comments(repo=DEFAULT_REPO, pr_number=pr_number, default_branch=DEFAULT_BASE_BRANCH)
        findings = comments.get("findings") or []
        progress_key = f"{status.get('headSha')}:{status.get('confidenceScore')}:{len(findings)}"
        blocked = _update_circuit_breakers(pr_number, state, progress_key)
        if blocked:
            state["terminal_state"] = blocked
            _write_json(pr_number, "status.json", state)
            _record_guardrail(pr_number, blocked)
            return {"ok": False, "state": blocked, "status": state}
        await update_greptile_state(
            task_id,
            greptile_status=status.get("reviewCompleteness") or "reviewed",
            confidence=status.get("confidenceScore"),
            unaddressed_count=len(findings),
        )
        if status.get("reviewIsRunning"):
            state["terminal_state"] = "WAITING_GREPTILE"
            _write_json(pr_number, "status.json", state)
            return {"ok": True, "state": "WAITING_GREPTILE", "greptile": status}
        if (status.get("confidenceScore") or 0) >= int(task.get("target_confidence") or 5) and not findings:
            state["terminal_state"] = "READY_TO_MERGE"
            _write_json(pr_number, "status.json", state)
            return {"ok": True, "state": "READY_TO_MERGE", "greptile": status}
        if not findings:
            triggered = await trigger_review(repo=DEFAULT_REPO, pr_number=pr_number, default_branch=DEFAULT_BASE_BRANCH)
            state["terminal_state"] = "WAITING_GREPTILE"
            _write_json(pr_number, "status.json", {**state, "triggered_review": triggered})
            return {"ok": True, "state": "WAITING_GREPTILE", "triggered_review": triggered}
        finding = findings[0]
        if any(str(finding.get("file_path") or "").startswith(prefix) for prefix in HIGH_RISK_PREFIXES):
            state["terminal_state"] = "ESCALATE_HERMES"
            _write_json(pr_number, "status.json", state)
            return {"ok": False, "state": "ESCALATE_HERMES", "finding": finding}
        packet = _packet_for_finding(pr_number, status, finding)
        _write_json(pr_number, "last_packet.json", packet.to_dict())
        _append_progress(pr_number, f"packet {_finding_hash(finding)} for {finding.get('file_path')}")
        if packet_only:
            state["terminal_state"] = "PACKET_READY"
            _write_json(pr_number, "status.json", state)
            return {"ok": True, "state": "PACKET_READY", "packet": packet.to_dict()}
        runner_status, runner_output = await _run_cheap_agent(packet, task_id=task_id)
        analysis = analyze_result(packet, runner_output)
        result = {"runner_status": runner_status, "analysis": analysis, "output": runner_output[:MAX_OUTPUT_CHARS]}
        _write_json(pr_number, "last_result.json", result)
        if runner_status != "OK" or analysis["classification"] != "APPLIED":
            state["terminal_state"] = analysis.get("classification") if runner_status == "OK" else runner_status
            _write_json(pr_number, "status.json", state)
            return {"ok": False, "state": state["terminal_state"], "result": result}
        triggered = await trigger_review(repo=DEFAULT_REPO, pr_number=pr_number, default_branch=DEFAULT_BASE_BRANCH)
        state["terminal_state"] = "WAITING_GREPTILE"
        _write_json(pr_number, "status.json", {**state, "triggered_review": triggered})
        return {"ok": True, "state": "WAITING_GREPTILE", "result": result, "triggered_review": triggered}
