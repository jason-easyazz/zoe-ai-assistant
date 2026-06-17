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
# Hermes patch tool emits "[Found N matches ...]" when an old_string anchor is not unique.
_PATCH_AMBIGUITY_RE = re.compile(
    r"^\s*(?:┊|\|)\s+\S+\s+patch\b.*\[Found\s+\d+\s+matches\b",
    re.IGNORECASE,
)
_PATCH_PATH_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+patch\s+(?P<path>\S+)", re.IGNORECASE)
_PATCH_REVIEW_DIFF_RE = re.compile(r"^\s*(?:┊|\|)\s+review\s+diff\b", re.IGNORECASE)
_TERMINAL_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+kanban_(?:complete|block)\b", re.IGNORECASE)
_EXPLORE_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+(?:read|grep|find)\b", re.IGNORECASE)
_READ_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+read\s+(?P<path>\S+)", re.IGNORECASE)
_GREP_STEP_RE = re.compile(r"^\s*(?:┊|\|)\s+\S+\s+(?:grep|find)\b", re.IGNORECASE)
_SHELL_CD_STEP_RE = re.compile(
    r"^\s*(?:┊|\|)\s+\S+\s+\$\s+cd\s+(?P<cwd>'[^']+'|\"[^\"]+\"|\S+)\s+&&\s+(?P<command>.+)$",
    re.IGNORECASE,
)
_STEP_TIMING_SUFFIX_RE = re.compile(r"\s+\d+(?:\.\d+)?s(?:\s+\[.*?\])?\s*$")
_FOCUSED_HARNESS_TEST_RE = re.compile(
    r"\bpython3\s+-m\s+pytest\b.*services/zoe-data/tests/\S+\.py::\S+",
    re.IGNORECASE,
)
_FOCUSED_HARNESS_TEST_PATH_RE = re.compile(
    r"(?P<path>services/zoe-data/tests/\S+\.py)::\S+",
    re.IGNORECASE,
)
_CODE_AUDIT_BODY_RE = re.compile(
    r"CODE-AUDIT FAST PATH|\bcode_audit\b",
    re.IGNORECASE,
)
_INTENT_GAP_BODY_RE = re.compile(
    r'INTENT-GAP IMPLEMENT FAST PATH|\bintent_gap:[\w-]+|"source"\s*:\s*"intent_gap',
    re.IGNORECASE,
)
_BROAD_FIND_GREP_RE = re.compile(r"^find\s+\S+.*\b(?:grep|rg)\b", re.IGNORECASE)
_POST_PATCH_VALIDATION_RE = re.compile(
    r"^(?:"
    r"(?:\w+=\S+\s+)*python3?\s+-m\s+(?:py_compile|pytest|mypy|ruff)\b|"
    r"(?:\w+=\S+\s+)*python3?\s+tools/audit/(?:validate_structure|validate_critical_files)\.py\b|"
    r"(?:\w+=\S+\s+)*tools/audit/(?:validate_structure|validate_critical_files)\.py\b|"
    r"nginx\s+-t\b|"
    r"curl\s+-I\b"
    r")",
    re.IGNORECASE,
)
_POST_PATCH_SHIP_RE = re.compile(
    r"^(?:git\s+(?:commit|push)\b|gh\s+pr\s+create\b)",
    re.IGNORECASE,
)
_INTENT_GAP_HELPER_EDIT_RE = re.compile(
    r"\bpython3?\s+(?:\./)?scripts/maintenance/zoe_apply_intent_gap_contract\.py\b",
    re.IGNORECASE,
)
_GIT_ADD_RE = re.compile(r"^git\s+add\b", re.IGNORECASE)
# Zoe needs a PR handoff, not only a pushed branch; require all four steps in order.
_CHAINED_SHIP_RE = re.compile(
    r"^git\s+add\b.*&&\s*git\s+commit\b.*&&\s*git\s+push\b.*&&\s*gh\s+pr\s+create\b",
    re.IGNORECASE,
)
_STEP_EXIT_RE = re.compile(r"\[exit\s+(?P<code>\d+)\]", re.IGNORECASE)
_ENGINEERING_BLOCKER_FOLLOWUP_SOURCE_RE = re.compile(
    r'"source"\s*:\s*"engineering_blocker_followup"'
)
_MARK_REVIEWED_VERDICT_RE = re.compile(
    r"\bmark-reviewed\b(?=.*--critical-count)(?=.*--summary)",
    re.IGNORECASE,
)
_ZERO_STEP_WARNED: set[str] = set()
_POST_FOCUS_READ_BUDGET_DEFAULT = 2
_POST_FOCUS_FOCUSED_TEST_READ_BUDGET_DEFAULT = 4
_POST_FOCUS_GREP_BUDGET_DEFAULT = 3


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


def latest_log_session(task_id: str, *, max_lines: int = 120) -> str:
    """Return only the latest Hermes session from a task log."""
    return _latest_log_session(task_id, max_lines=max_lines)


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
    patched_python_paths: set[str] = set()
    post_patch_read_counts: dict[str, int] = {}
    allowed_post_patch_reads = _post_patch_file_read_budget()
    for line in session.splitlines():
        if not _STEP_LINE_RE.match(line):
            continue
        any_patch = _PATCH_PATH_RE.search(line)
        if any_patch and _PYTHON_PATCH_RE.search(line):
            pending_python_patch = True
            path_key = _read_path_key(any_patch.group("path"))
            patched_python_paths.add(path_key)
            post_patch_read_counts.pop(path_key, None)
            continue
        if any_patch:
            # A patch/edit of another file (e.g. a .yml or .md) is productive
            # work, not the "patch then keep reading/grepping" failure this guard
            # targets — multi-file tasks routinely edit a .py then a config file.
            # Allow it; the pending Python patch still requires its syntax/test
            # check before any *exploration* (reads beyond budget / greps).
            continue
        if not pending_python_patch:
            continue
        if _PATCH_REVIEW_DIFF_RE.search(line):
            continue
        if _PYTHON_CHECK_RE.search(line):
            pending_python_patch = False
            patched_python_paths.clear()
            post_patch_read_counts.clear()
            continue
        read_match = _READ_STEP_RE.search(line)
        if read_match:
            path = _read_path_key(read_match.group("path"))
            matched_patch = next(
                (patched for patched in patched_python_paths if path.endswith(patched)),
                "",
            )
            if matched_patch:
                post_patch_read_counts[matched_patch] = (
                    post_patch_read_counts.get(matched_patch, 0) + 1
                )
                if post_patch_read_counts[matched_patch] <= allowed_post_patch_reads:
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


def _post_patch_file_read_budget() -> int:
    try:
        return max(0, int(os.environ.get("ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET", "2")))
    except ValueError:
        return 2


def _post_focus_read_budget() -> int:
    try:
        return max(
            1,
            int(
                os.environ.get(
                    "ZOE_KANBAN_IMPLEMENT_POST_FOCUS_READ_BUDGET",
                    str(_POST_FOCUS_READ_BUDGET_DEFAULT),
                )
            ),
        )
    except ValueError:
        return _POST_FOCUS_READ_BUDGET_DEFAULT


def _post_focus_focused_test_read_budget() -> int:
    try:
        return max(
            1,
            int(
                os.environ.get(
                    "ZOE_KANBAN_IMPLEMENT_POST_FOCUS_FOCUSED_TEST_READ_BUDGET",
                    str(_POST_FOCUS_FOCUSED_TEST_READ_BUDGET_DEFAULT),
                )
            ),
        )
    except ValueError:
        return _POST_FOCUS_FOCUSED_TEST_READ_BUDGET_DEFAULT


def _focused_harness_test_path(line: str) -> str:
    match = _FOCUSED_HARNESS_TEST_PATH_RE.search(line)
    return match.group("path") if match else ""


def _post_focus_grep_budget() -> int:
    try:
        return max(
            1,
            int(
                os.environ.get(
                    "ZOE_KANBAN_IMPLEMENT_POST_FOCUS_GREP_BUDGET",
                    str(_POST_FOCUS_GREP_BUDGET_DEFAULT),
                )
            ),
        )
    except ValueError:
        return _POST_FOCUS_GREP_BUDGET_DEFAULT


def _read_path_key(raw_path: str) -> str:
    return re.sub(r":\d+(?:-\d+)?$", "", raw_path)


def _is_engineering_blocker_followup_body(body: str) -> bool:
    return bool(_ENGINEERING_BLOCKER_FOLLOWUP_SOURCE_RE.search(body))


def _is_code_audit_body(body: str) -> bool:
    return bool(_CODE_AUDIT_BODY_RE.search(body))


def _is_intent_gap_body(body: str) -> bool:
    return bool(_INTENT_GAP_BODY_RE.search(body))


def _intent_gap_pre_edit_explore_budget() -> int:
    try:
        return max(1, int(os.environ.get("ZOE_KANBAN_INTENT_GAP_PRE_EDIT_EXPLORE_BUDGET", "6")))
    except ValueError:
        return 6


def _intent_gap_repeat_read_budget() -> int:
    try:
        return max(1, int(os.environ.get("ZOE_KANBAN_INTENT_GAP_REPEAT_READ_BUDGET", "2")))
    except ValueError:
        return 2


def _post_patch_explore_budget() -> int:
    try:
        return max(1, int(os.environ.get("ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET", "2")))
    except ValueError:
        return 2


def _shell_command_from_step(line: str) -> str | None:
    if "$" not in line:
        return None
    cd_match = _SHELL_CD_STEP_RE.match(line)
    if cd_match:
        return _STEP_TIMING_SUFFIX_RE.sub("", cd_match.group("command").strip())
    command = line.split("$", 1)[1].strip()
    return _STEP_TIMING_SUFFIX_RE.sub("", command)


def _step_failed(line: str) -> bool:
    match = _STEP_EXIT_RE.search(line)
    return bool(match and int(match.group("code")) != 0)


def implement_code_audit_post_patch_drift_reason_from_log(
    task_id: str,
    phase: str,
    *,
    session: str | None = None,
    task_body: str = "",
) -> str | None:
    """Block code-audit workers that keep exploring after applying a patch.

    Code-audit tickets should be cheap: inspect the named surface, patch it, run
    the nearest validation or structural smoke check, then hand off a PR. A
    production run showed the worker correctly patched a config file but spent
    the rest of its iteration budget reading CI and shell scripts. This guard
    turns that pattern into an explicit blocker before another full-budget loop.
    """
    if phase not in {"implement", "implement_revision"}:
        return None
    if not _is_code_audit_body(task_body):
        return None
    if session is None:
        session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return None

    patch_seen = False
    post_patch_explore_steps = 0
    explore_budget = _post_patch_explore_budget()
    for line in session.splitlines():
        if not _STEP_LINE_RE.match(line):
            continue
        if _PATCH_STEP_RE.search(line):
            patch_seen = True
            continue
        if not patch_seen:
            continue
        shell_command = _shell_command_from_step(line)
        if _TERMINAL_STEP_RE.search(line):
            return None
        if shell_command and (
            _POST_PATCH_VALIDATION_RE.search(shell_command)
            or _POST_PATCH_SHIP_RE.search(shell_command)
        ):
            if not _step_failed(line):
                return None
            continue
        if not _EXPLORE_STEP_RE.search(line):
            continue
        post_patch_explore_steps += 1
        if post_patch_explore_steps > explore_budget:
            return (
                "BLOCKER=CODE_AUDIT_POST_PATCH_DRIFT: code-audit worker kept "
                "exploring after patch instead of validation, commit, PR, or terminal handoff "
                f"(post_patch_explore_steps={post_patch_explore_steps}, limit={explore_budget})"
            )
    return None


def implement_code_audit_post_validation_ship_reason_from_log(
    task_id: str,
    phase: str,
    *,
    session: str | None = None,
    task_body: str = "",
) -> str | None:
    """Block code-audit workers that stage changes without shipping after validation."""
    if phase not in {"implement", "implement_revision"}:
        return None
    if not _is_code_audit_body(task_body):
        return None
    if session is None:
        session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return None

    validation_seen = False
    for line in session.splitlines():
        if not _STEP_LINE_RE.match(line):
            continue
        shell_command = _shell_command_from_step(line)
        if _TERMINAL_STEP_RE.search(line):
            return None
        if not shell_command:
            continue
        if _POST_PATCH_SHIP_RE.search(shell_command) or _CHAINED_SHIP_RE.search(shell_command):
            if not _step_failed(line):
                return None
            continue
        if _POST_PATCH_VALIDATION_RE.search(shell_command):
            if not _step_failed(line):
                validation_seen = True
            continue
        if validation_seen and _GIT_ADD_RE.search(shell_command):
            return (
                "BLOCKER=CODE_AUDIT_STAGED_WITHOUT_SHIP: code-audit worker ran "
                "`git add` after validation without chaining commit, push, and PR creation"
            )
    return None


def implement_patch_ambiguity_reason_from_log(
    task_id: str,
    phase: str,
    *,
    session: str | None = None,
) -> str | None:
    """Block implement workers that repeat ambiguous patch anchors."""
    if phase not in {"implement", "implement_revision"}:
        return None
    if session is None:
        session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return None

    ambiguous_patches_by_path: dict[str, int] = {}
    for line in session.splitlines():
        if not _STEP_LINE_RE.match(line):
            continue
        patch_match = _PATCH_PATH_RE.search(line)
        patch_path = patch_match.group("path") if patch_match else "<unknown>"
        if _PATCH_AMBIGUITY_RE.search(line):
            ambiguous_patches_by_path[patch_path] = ambiguous_patches_by_path.get(patch_path, 0) + 1
            if ambiguous_patches_by_path[patch_path] >= 2:
                return (
                    "BLOCKER=PATCH_AMBIGUITY_DRIFT: repeated patch attempts used "
                    "non-unique anchors; use a unique surrounding block or call kanban_block"
                )
            continue
        if _PATCH_STEP_RE.search(line):
            ambiguous_patches_by_path.pop(patch_path, None)
    return None


def implement_intent_gap_pre_edit_reason_from_log(
    task_id: str,
    phase: str,
    *,
    session: str | None = None,
    task_body: str = "",
) -> str | None:
    """Block intent-gap workers that keep locating instead of editing.

    Intent-gap tickets are already narrow and name the router/test surface in the
    task body. A live ZOE-5458 run burned its full Hermes iteration budget by
    rereading intent_router.py and broad find/grep searching without a patch.
    This turns the fast-path handoff into a runtime contract.
    """
    if phase not in {"implement", "implement_revision"}:
        return None
    if not _is_intent_gap_body(task_body):
        return None
    if session is None:
        session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return None

    explore_budget = _intent_gap_pre_edit_explore_budget()
    repeat_read_budget = _intent_gap_repeat_read_budget()
    explore_steps = 0
    read_counts: dict[str, int] = {}
    for line in session.splitlines():
        if not _STEP_LINE_RE.match(line):
            continue
        if _PATCH_STEP_RE.search(line) or _TERMINAL_STEP_RE.search(line):
            return None
        shell_command = _shell_command_from_step(line)
        if shell_command and _INTENT_GAP_HELPER_EDIT_RE.search(shell_command):
            return None
        if shell_command and _BROAD_FIND_GREP_RE.search(shell_command):
            return (
                "BLOCKER=IMPLEMENT_BUDGET: intent-gap worker ran broad repo "
                "search before editing the named router/test surface"
            )
        if not _EXPLORE_STEP_RE.search(line):
            continue
        explore_steps += 1
        if explore_steps > explore_budget:
            return (
                "BLOCKER=IMPLEMENT_BUDGET: intent-gap pre-edit exploration exceeded "
                f"budget without patch (steps={explore_steps}, limit={explore_budget})"
            )
        read_match = _READ_STEP_RE.search(line)
        if read_match:
            path = _read_path_key(read_match.group("path"))
            read_counts[path] = read_counts.get(path, 0) + 1
            if read_counts[path] > repeat_read_budget:
                return (
                    "BLOCKER=IMPLEMENT_BUDGET: intent-gap repeated pre-edit reads "
                    f"without patch (file={path}, reads={read_counts[path]})"
                )
    return None


def implement_pre_edit_drift_reason_from_log(
    task_id: str,
    phase: str,
    *,
    session: str | None = None,
    task_body: str = "",
) -> str | None:
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
    harness_followup = _is_engineering_blocker_followup_body(task_body)
    focused_indexes = [
        index
        for index, line in enumerate(step_lines)
        if harness_followup and _FOCUSED_HARNESS_TEST_RE.search(line)
    ]
    terminal_indexes = [
        index
        for index, line in enumerate(step_lines)
        if _PATCH_STEP_RE.search(line) or _TERMINAL_STEP_RE.search(line)
    ]
    focused_harness_test_present = bool(focused_indexes)
    if terminal_indexes and (
        not focused_harness_test_present or terminal_indexes[0] < focused_indexes[0]
    ):
        return None

    explore_budget = _pre_edit_explore_budget()
    repeat_read_budget = _pre_edit_repeat_read_budget()
    explore_steps = 0
    focused_harness_test_seen = False
    focused_harness_test_path = ""
    post_focus_allowed_reads = {
        "services/zoe-data/main.py",
        "services/zoe-data/tests/test_main_multica_poll.py",
        "services/zoe-data/executors/kanban_adapter.py",
    }
    post_focus_allowed_read_paths = set(post_focus_allowed_reads)
    post_focus_read_counts: dict[str, int] = {}
    post_focus_grep_steps = 0
    post_focus_read_budget = _post_focus_read_budget()
    post_focus_focused_test_read_budget = _post_focus_focused_test_read_budget()
    post_focus_grep_budget = _post_focus_grep_budget()
    read_counts: dict[str, int] = {}
    for line in step_lines:
        if _PATCH_STEP_RE.search(line) or _TERMINAL_STEP_RE.search(line):
            return None
        if harness_followup and _FOCUSED_HARNESS_TEST_RE.search(line):
            focused_harness_test_seen = True
            focused_harness_test_path = _focused_harness_test_path(line)
            if focused_harness_test_path:
                post_focus_allowed_read_paths.add(focused_harness_test_path)
            continue
        if not _EXPLORE_STEP_RE.search(line):
            continue
        read_match = _READ_STEP_RE.search(line)
        if focused_harness_test_seen:
            path = _read_path_key(read_match.group("path")) if read_match else ""
            matched_allowed_path = next(
                (allowed for allowed in post_focus_allowed_read_paths if path.endswith(allowed)),
                "",
            )
            if matched_allowed_path:
                post_focus_read_counts[matched_allowed_path] = (
                    post_focus_read_counts.get(matched_allowed_path, 0) + 1
                )
                read_budget = post_focus_read_budget
                if focused_harness_test_path and matched_allowed_path == focused_harness_test_path:
                    read_budget = post_focus_focused_test_read_budget
                if post_focus_read_counts[matched_allowed_path] <= read_budget:
                    continue
            elif _GREP_STEP_RE.search(line):
                post_focus_grep_steps += 1
                if post_focus_grep_steps <= post_focus_grep_budget:
                    continue
            return (
                "BLOCKER=ALREADY_COVERED: focused harness test passed before edit; "
                "no code change required for this blocker follow-up"
            )
        explore_steps += 1
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


def worktree_path_violation_reason_from_log(
    task_id: str,
    phase: str,
    *,
    session: str | None = None,
    task: dict[str, Any] | None = None,
) -> str | None:
    """Block workers that leave their pinned isolated worktree.

    The task row is the source of truth for where Hermes should run. A worker
    that shells back into the dirty live checkout can commit unrelated files and
    open a polluted PR, so this guard fails closed as soon as the log shows a
    different `cd ... &&` working directory.
    """
    if phase not in {"implement", "implement_revision", "verify"}:
        return None
    task = task or {}
    if str(task.get("workspace_kind") or "").lower() != "worktree":
        return None
    expected = str(task.get("workspace_path") or "").rstrip("/")
    if not expected:
        return None
    if session is None:
        session = _latest_log_session(task_id, max_lines=0)
    if not session:
        return None

    for line in session.splitlines():
        read_match = _READ_STEP_RE.search(line)
        if read_match:
            path = _read_path_key(read_match.group("path")).rstrip("/")
            if path.startswith("/") and path != expected and not path.startswith(expected + "/"):
                return (
                    "BLOCKER=WORKTREE_PATH_VIOLATION: worker read "
                    f"{path} outside pinned task worktree {expected}"
                )
        match = _SHELL_CD_STEP_RE.match(line)
        if not match:
            continue
        cwd = match.group("cwd").strip("'\"").rstrip("/")
        if cwd != expected and not cwd.startswith(expected + "/"):
            command = _STEP_TIMING_SUFFIX_RE.sub("", match.group("command").strip())
            return (
                "BLOCKER=WORKTREE_PATH_VIOLATION: worker used "
                f"{cwd} instead of pinned task worktree {expected} "
                f"before command `{command[:160]}`"
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


def dead_worker_reason(detail: dict[str, Any], *, grace_s: float | None = None) -> str | None:
    """Detect a zombie 'running' task whose worker process is no longer alive.

    A worker can die without emitting a terminal kanban signal (e.g. an
    out-of-context/HTTP crash, OOM, or external kill), leaving its run marked
    ``running`` with a ``worker_pid`` that no longer points at a live
    ``hermes ... work kanban task`` process. Such a zombie holds the single lane
    (poll_ref reports the chain as running) and blocks dispatch until something
    reaps it. Return a reason string when a running run has a usable ``worker_pid``
    that is definitively NOT a live worker and the run is older than a short grace
    (so a just-started worker whose pid isn't published yet is never misjudged).
    """
    try:
        grace = float(
            grace_s if grace_s is not None
            else os.environ.get("ZOE_KANBAN_DEAD_WORKER_GRACE_S", "180") or "180"
        )
    except (TypeError, ValueError):
        grace = 180.0
    now = time.time()
    for run in detail.get("runs") or []:
        if not isinstance(run, dict) or str(run.get("status") or "").lower() != "running":
            continue
        try:
            pid = int(run.get("worker_pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 100:
            continue  # no usable pid to judge liveness — leave to other guards
        if _is_expected_worker(pid):
            continue  # worker still alive
        started = _timestamp(run.get("started_at"))
        if started is not None and (now - started) < grace:
            continue  # within grace; pid may simply not be published yet
        return f"WORKER_DIED: running worker pid {pid} is no longer alive (zombie running task)"
    return None


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
    session = (
        _latest_log_session(task_id, max_lines=0)
        if phase in {"implement", "implement_revision", "verify"}
        else None
    )
    task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
    worktree_reason = worktree_path_violation_reason_from_log(
        task_id,
        phase,
        session=session,
        task=task,
    )
    if worktree_reason:
        return worktree_reason
    patch_ambiguity_reason = implement_patch_ambiguity_reason_from_log(
        task_id,
        phase,
        session=session,
    )
    if patch_ambiguity_reason:
        return patch_ambiguity_reason
    edit_safety_reason = implement_edit_safety_reason_from_log(task_id, phase, session=session)
    if edit_safety_reason:
        return edit_safety_reason
    code_audit_post_patch_reason = implement_code_audit_post_patch_drift_reason_from_log(
        task_id,
        phase,
        session=session,
        task_body=str(task.get("body") or ""),
    )
    if code_audit_post_patch_reason:
        return code_audit_post_patch_reason
    code_audit_post_validation_reason = implement_code_audit_post_validation_ship_reason_from_log(
        task_id,
        phase,
        session=session,
        task_body=str(task.get("body") or ""),
    )
    if code_audit_post_validation_reason:
        return code_audit_post_validation_reason
    intent_gap_pre_edit_reason = implement_intent_gap_pre_edit_reason_from_log(
        task_id,
        phase,
        session=session,
        task_body=str(task.get("body") or ""),
    )
    if intent_gap_pre_edit_reason:
        return intent_gap_pre_edit_reason
    pre_edit_drift_reason = implement_pre_edit_drift_reason_from_log(
        task_id,
        phase,
        session=session,
        task_body=str(task.get("body") or ""),
    )
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
