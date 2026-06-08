"""Code-enforced runtime and tool-call budgets for Hermes Kanban phases."""

from __future__ import annotations

import logging
import os
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TOOL_DEFAULTS = {
    "scout": 8,
    "implement": 24,
    "implement_revision": 30,
    "verify": 16,
    "review": 10,
    "closeout": 12,
    "retro": 8,
}
_TERMINAL_GRACE_DEFAULT = 2
_RUNTIME_DEFAULTS = {
    "scout": 300,
    "implement": 1800,
    "implement_revision": 1800,
    "verify": 600,
    "review": 600,
    "closeout": 900,
    "retro": 300,
}
_STEP_LINE_RE = re.compile(r"^\s*(?:┊|\|)\s+\S")
_FALLBACK_STEP_RE = re.compile(r"^\s*\S.*\s+\d+(?:\.\d+)?s(?:\s+\[.*\])?\s*$")
_ITERATION_BUDGET_RE = re.compile(
    r"Iteration budget reached\s*\((?P<used>\d+)\s*/\s*(?P<limit>\d+)\)",
    re.IGNORECASE,
)
_ZERO_STEP_WARNED: set[str] = set()


def _limit(phase: str, kind: str) -> int:
    defaults = _TOOL_DEFAULTS if kind == "tools" else _RUNTIME_DEFAULTS
    suffix = "TOOL_BUDGET" if kind == "tools" else "RUNTIME_BUDGET_SECONDS"
    raw = os.environ.get(f"ZOE_KANBAN_{phase.upper()}_{suffix}", "")
    if phase == "implement_revision" and not raw:
        raw = os.environ.get(f"ZOE_KANBAN_IMPLEMENT_{suffix}", "")
    try:
        return max(1, int(raw)) if raw else defaults.get(phase, 10)
    except ValueError:
        return defaults.get(phase, 10)


def _budget_phase(phase: str, detail: dict[str, Any]) -> str:
    if phase != "implement":
        return phase
    task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
    body = str(task.get("body") or "")
    if "EXISTING PR REVISION FAST PATH" in body:
        return "implement_revision"
    return phase


def _log_path(task_id: str) -> Path:
    hermes_home = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))
    return hermes_home / "kanban" / "logs" / f"{task_id}.log"


def task_log_tail(task_id: str, *, max_lines: int = 80) -> str:
    """Return the latest Hermes Kanban task log lines for evidence recovery."""
    try:
        lines = _log_path(task_id).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if max_lines <= 0:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def tool_step_count(task_id: str, *, since: float | None = None) -> int:
    path = _log_path(task_id)
    try:
        if since is not None and path.stat().st_mtime < since:
            return 0
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    query_starts = [index for index, line in enumerate(lines) if line.startswith("Query:")]
    if query_starts:
        lines = lines[query_starts[-1]:]
    count = sum(1 for line in lines if _STEP_LINE_RE.match(line))
    if not count:
        count = sum(1 for line in lines if _FALLBACK_STEP_RE.match(line))
    unparsed_activity = any(
        line.strip()
        and not line.startswith("Query:")
        and "Initializing agent" not in line
        and not set(line.strip()) <= {"─"}
        for line in lines
    )
    if not count and unparsed_activity and task_id not in _ZERO_STEP_WARNED:
        _ZERO_STEP_WARNED.add(task_id)
        logger.warning(
            "kanban_phase_budget: could not identify tool steps in non-empty log for %s",
            task_id,
        )
    return count


def phase_budget_reason_from_log(task_id: str, phase: str) -> str | None:
    """Recover Hermes' own iteration-budget stop from a task log.

    Hermes can exit with rc=0 after printing "Iteration budget reached" before it
    calls kanban_block. That used to look like a generic protocol violation and
    hid the useful cost-control signal from the pipeline. Treat the log line as
    explicit blocker evidence so Zoe can stop/recover without redispatch loops.
    """
    tail = task_log_tail(task_id, max_lines=120)
    if not tail:
        return None
    lines = tail.splitlines()
    query_starts = [index for index, line in enumerate(lines) if line.startswith("Query:")]
    if query_starts:
        tail = "\n".join(lines[query_starts[-1]:])
    matches = list(_ITERATION_BUDGET_RE.finditer(tail))
    if not matches:
        return None
    match = matches[-1]
    return (
        f"BLOCKER=ITERATION_BUDGET: Hermes iteration budget reached during {phase} "
        f"(steps={match.group('used')}, limit={match.group('limit')})"
    )


def _is_expected_worker(pid: int) -> bool:
    if pid <= 100:
        return False
    try:
        command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8",
            errors="replace",
        )
    except OSError:
        return False
    return "hermes" in command and "work kanban task" in command


def running_worker_pids(detail: dict[str, Any]) -> list[int]:
    pids: list[int] = []
    for run in detail.get("runs") or []:
        if not isinstance(run, dict) or str(run.get("status") or "").lower() != "running":
            continue
        try:
            pid = int(run.get("worker_pid") or 0)
        except (TypeError, ValueError):
            continue
        if _is_expected_worker(pid):
            pids.append(pid)
    return pids


def terminate_running_workers(detail: dict[str, Any]) -> None:
    for pid in running_worker_pids(detail):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError as exc:
            logger.warning("kanban_phase_budget: failed to stop worker pid=%s: %s", pid, exc)


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _started_timestamp(detail: dict[str, Any]) -> float | None:
    task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
    for run in reversed(detail.get("runs") or []):
        if not isinstance(run, dict):
            continue
        timestamp = _timestamp(run.get("started_at"))
        if timestamp is not None:
            return timestamp
    return _timestamp(task.get("started_at"))


def phase_budget_reason(
    task_id: str,
    phase: str,
    detail: dict[str, Any],
    *,
    now: float | None = None,
) -> str | None:
    started_ts = _started_timestamp(detail)
    tool_steps = tool_step_count(task_id, since=started_ts)
    budget_phase = _budget_phase(phase, detail)
    tool_limit = _limit(budget_phase, "tools")
    try:
        terminal_grace = max(
            0,
            int(os.environ.get("ZOE_KANBAN_TERMINAL_TOOL_GRACE", _TERMINAL_GRACE_DEFAULT)),
        )
    except ValueError:
        terminal_grace = _TERMINAL_GRACE_DEFAULT
    hard_limit = tool_limit + terminal_grace
    if tool_steps > hard_limit:
        return (
            f"BLOCKER={phase.upper()}_BUDGET: code-enforced tool budget exceeded "
            f"(steps={tool_steps}, guidance_limit={tool_limit}, hard_limit={hard_limit})"
        )
    log_reason = phase_budget_reason_from_log(task_id, phase)
    if log_reason:
        return log_reason

    if started_ts is None:
        return None
    elapsed = (now if now is not None else time.time()) - started_ts
    runtime_limit = _limit(budget_phase, "runtime")
    if elapsed > runtime_limit:
        return (
            f"BLOCKER={phase.upper()}_BUDGET: code-enforced runtime budget exceeded "
            f"(elapsed={int(elapsed)}s, limit={runtime_limit}s)"
        )
    return None
