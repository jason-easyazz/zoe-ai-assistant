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
_PYTHON_PATCH_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+patch\s+.*\.py\b")
_PYTHON_CHECK_RE = re.compile(
    r"^\s*(?:┊|\|)\s+\S+\s+\$\s+.*\b(py_compile|pytest|mypy|ruff|validate_structure|validate_critical_files)\b",
    re.IGNORECASE,
)
_PATCH_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+patch\b", re.IGNORECASE)
_PATCH_REVIEW_DIFF_RE = re.compile(r"^\s*(?:┊|\|)\s+review\s+diff\b", re.IGNORECASE)
_TERMINAL_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+kanban_(?:complete|block)\b", re.IGNORECASE)
_EXPLORE_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+(?:read|grep|find)\b", re.IGNORECASE)
_READ_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+read\s+(?P<path>\S+)", re.IGNORECASE)
_MARK_REVIEWED_VERDICT_RE = re.compile(
    r"\bmark-reviewed\b(?=.*--critical-count)(?=.*--summary)",
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


def _latest_log_session(task_id: str, *, max_lines: int = 120) -> str:
    try:
        lines = _log_path(task_id).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    query_starts = [index for index, line in enumerate(lines) if line.startswith("Query:")]
    if query_starts:
        lines = lines[query_starts[-1]:]
    if max_lines > 0:
        lines = lines[-max_lines:]
    return "\n".join(lines)


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
    tail = _latest_log_session(task_id, max_lines=120)
    if not tail:
        return None
    matches = list(_ITERATION_BUDGET_RE.finditer(tail))
    if not matches:
        return None
    match = matches[-1]
    return (
        f"BLOCKER=ITERATION_BUDGET: Hermes iteration budget reached during {phase} "
        f"(steps={match.group('used')}, limit={match.group('limit')})"
    )


def implement_edit_safety_reason_from_log(task_id: str, phase: str, *, session: str | None = None) -> str | None:
    """Block implement runs that patch Python then keep exploring before syntax checks.

    Prompt instructions are not enough for cost control: a worker can apply a
    malformed Python patch and spend the rest of its iteration budget reading and
    grepping. Treat the latest task log as an enforceable event stream: after a
    Python patch, the next tool step must be a narrow syntax/test check.
    """
    if phase not in {"implement", "implement_revision"}:
        return None
    if session is None:
        session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return None

    pending_python_patch = False
    for line in session.splitlines():
        if not _STEP_LINE_RE.match(line):
            continue
        if _PYTHON_PATCH_RE.search(line):
            pending_python_patch = True
            continue
        if not pending_python_patch:
            continue
        if _PATCH_REVIEW_DIFF_RE.search(line):
            continue
        if _PYTHON_CHECK_RE.search(line):
            pending_python_patch = False
            continue
        return (
            "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
            "exploration before py_compile/focused tests"
        )
    return None


def _pre_edit_explore_budget() -> int:
    try:
        return max(1, int(os.environ.get("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")))
    except ValueError:
        return 12


def _pre_edit_repeat_read_budget() -> int:
    try:
        return max(1, int(os.environ.get("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_REPEAT_READ_BUDGET", "6")))
    except ValueError:
        return 6


def _read_path_key(raw_path: str) -> str:
    return re.sub(r":\d+(?:-\d+)?$", "", raw_path)


def implement_pre_edit_drift_reason_from_log(task_id: str, phase: str, *, session: str | None = None) -> str | None:
    """Block implement runs that keep exploring before making any edit.

    Real production failures showed workers ignoring a narrow scout handoff and
    repeatedly reading/grepping the same files until Hermes' full iteration
    budget was gone. This guard cuts that loop off earlier. Once a patch or a
    terminal Kanban call appears, this pre-edit guard stands down and the normal
    edit-safety / evidence gates take over.
    """
    if phase not in {"implement", "implement_revision"}:
        return None
    if session is None:
        session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return None

    step_lines = [line for line in session.splitlines() if _STEP_LINE_RE.match(line)]
    if any(_PATCH_STEP_RE.search(line) or _TERMINAL_STEP_RE.search(line) for line in step_lines):
        return None

    explore_budget = _pre_edit_explore_budget()
    repeat_read_budget = _pre_edit_repeat_read_budget()
    explore_steps = 0
    read_counts: dict[str, int] = {}
    for line in step_lines:
        if not _EXPLORE_STEP_RE.search(line):
            continue
        explore_steps += 1
        read_match = _READ_STEP_RE.search(line)
        if explore_steps > explore_budget:
            return (
                "BLOCKER=IMPLEMENT_HANDOFF_DRIFT: pre-edit exploration exceeded "
                f"budget without patch (steps={explore_steps}, limit={explore_budget})"
            )
        if read_match:
            path = _read_path_key(read_match.group("path"))
            read_counts[path] = read_counts.get(path, 0) + 1
            if read_counts[path] > repeat_read_budget:
                return (
                    "BLOCKER=IMPLEMENT_HANDOFF_DRIFT: repeated pre-edit reads "
                    f"without patch (file={path}, reads={read_counts[path]})"
                )
    return None


def review_wrapup_tool_grace(task_id: str, phase: str) -> int:
    """Grant review terminal headroom only after a verdict marker is written."""
    if phase != "review":
        return 0
    session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return 0
    for line in session.splitlines():
        if not _STEP_LINE_RE.match(line):
            continue
        lowered = line.lower()
        if "mark-reviewed" not in lowered or "--help" in lowered:
            continue
        if _MARK_REVIEWED_VERDICT_RE.search(line):
            try:
                return max(0, int(os.environ.get("ZOE_KANBAN_REVIEW_WRAPUP_TOOL_GRACE", "3")))
            except ValueError:
                return 3
    return 0


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
    wrapup_grace = review_wrapup_tool_grace(task_id, phase)
    hard_limit = tool_limit + terminal_grace + wrapup_grace
    if tool_steps > hard_limit:
        return (
            f"BLOCKER={phase.upper()}_BUDGET: code-enforced tool budget exceeded "
            f"(steps={tool_steps}, guidance_limit={tool_limit}, hard_limit={hard_limit})"
        )
    session = _latest_log_session(task_id, max_lines=0) if phase in {"implement", "implement_revision"} else None
    edit_safety_reason = implement_edit_safety_reason_from_log(task_id, phase, session=session)
    if edit_safety_reason:
        return edit_safety_reason
    pre_edit_drift_reason = implement_pre_edit_drift_reason_from_log(task_id, phase, session=session)
    if pre_edit_drift_reason:
        return pre_edit_drift_reason
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
