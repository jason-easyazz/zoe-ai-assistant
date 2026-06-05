"""Code-enforced runtime and tool-call budgets for Hermes Kanban phases."""

from __future__ import annotations

import logging
import os
import re
import signal
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TOOL_DEFAULTS = {
    "scout": 8,
    "implement": 24,
    "verify": 10,
    "review": 10,
    "closeout": 12,
    "retro": 8,
}
_RUNTIME_DEFAULTS = {
    "scout": 300,
    "implement": 1800,
    "verify": 600,
    "review": 600,
    "closeout": 900,
    "retro": 300,
}


def _limit(phase: str, kind: str) -> int:
    defaults = _TOOL_DEFAULTS if kind == "tools" else _RUNTIME_DEFAULTS
    suffix = "TOOL_BUDGET" if kind == "tools" else "RUNTIME_BUDGET_SECONDS"
    raw = os.environ.get(f"ZOE_KANBAN_{phase.upper()}_{suffix}", "")
    try:
        return max(1, int(raw)) if raw else defaults.get(phase, 10)
    except ValueError:
        return defaults.get(phase, 10)


def _log_path(task_id: str) -> Path:
    hermes_home = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))
    return hermes_home / "kanban" / "logs" / f"{task_id}.log"


def tool_step_count(task_id: str) -> int:
    try:
        lines = _log_path(task_id).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    return sum(1 for line in lines if re.match(r"^\s*┊\s+\S", line))


def running_worker_pids(detail: dict[str, Any]) -> list[int]:
    pids: list[int] = []
    for run in detail.get("runs") or []:
        if not isinstance(run, dict) or str(run.get("status") or "").lower() != "running":
            continue
        try:
            pid = int(run.get("worker_pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid > 1:
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


def phase_budget_reason(
    task_id: str,
    phase: str,
    detail: dict[str, Any],
    *,
    now: float | None = None,
) -> str | None:
    tool_steps = tool_step_count(task_id)
    tool_limit = _limit(phase, "tools")
    if tool_steps > tool_limit:
        return (
            f"BLOCKER={phase.upper()}_BUDGET: code-enforced tool budget exceeded "
            f"(steps={tool_steps}, limit={tool_limit})"
        )

    task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
    started_at = task.get("started_at")
    if not started_at:
        running = [
            run
            for run in detail.get("runs") or []
            if isinstance(run, dict) and str(run.get("status") or "").lower() == "running"
        ]
        started_at = running[-1].get("started_at") if running else None
    try:
        elapsed = (now if now is not None else time.time()) - float(started_at)
    except (TypeError, ValueError):
        return None
    runtime_limit = _limit(phase, "runtime")
    if elapsed > runtime_limit:
        return (
            f"BLOCKER={phase.upper()}_BUDGET: code-enforced runtime budget exceeded "
            f"(elapsed={int(elapsed)}s, limit={runtime_limit}s)"
        )
    return None
