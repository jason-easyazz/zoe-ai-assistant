"""Dedicated unit tests for the pure log-reason detectors in kanban_phase_budget.

The harness in ``services/zoe-data/kanban_phase_budget.py`` owns two pure
log-reason detectors that gate every implement run:

* ``implement_pre_edit_drift_reason_from_log`` — block workers that keep
  exploring before making any edit.
* ``implement_edit_safety_reason_from_log`` — block workers that patch Python
  and then keep reading/grepping before a syntax check.

Both accept a ``session=`` keyword that injects a synthetic Hermes session
log, so the tests in this module need no real log file and no network.
"""
from __future__ import annotations

import pytest

import kanban_phase_budget as kb


# ---------------------------------------------------------------------------
# Step-line builders
# ---------------------------------------------------------------------------


def _grep_step(pattern: str) -> str:
    """A grep step line in the canonical Hermes log shape."""
    return f"  \u250a \U0001f50e grep      {pattern}  0.1s"


def _read_step(path: str) -> str:
    """A read step line in the canonical Hermes log shape."""
    return f"  \u250a \U0001f4d6 read      {path}  0.1s"


def _patch_step(path: str) -> str:
    """A patch step line in the canonical Hermes log shape."""
    return f"  \u250a \U0001f527 patch     {path}  1.5s"


def _py_compile_step(target: str) -> str:
    """A py_compile check step line in the canonical Hermes log shape."""
    return f"  \u250a \U0001f4bb $         python3 -m py_compile {target}  0.2s"


def _pytest_step(target: str) -> str:
    """A focused pytest check step line in the canonical Hermes log shape."""
    return f"  \u250a \U0001f4bb $         python3 -m pytest {target}  0.2s"


def _terminal_step(verb: str = "kanban_complete") -> str:
    """A terminal kanban call step line in the canonical Hermes log shape."""
    return f"  \u250a \u26a1 {verb}  0.0s"


# ---------------------------------------------------------------------------
# implement_pre_edit_drift_reason_from_log
# ---------------------------------------------------------------------------


def test_pre_edit_drift_returns_none_for_non_implement_phases():
    """Pre-edit drift only fires during implement or implement_revision."""
    session = "\n".join(_grep_step(f"symbol_{i}") for i in range(20)) + "\n"
    for phase in ("scout", "verify", "review", "closeout", "retro"):
        assert (
            kb.implement_pre_edit_drift_reason_from_log(
                "t_drift", phase, session=session
            )
            is None
        )


def test_pre_edit_drift_returns_none_for_empty_session():
    assert (
        kb.implement_pre_edit_drift_reason_from_log(
            "t_drift", "implement", session=""
        )
        is None
    )


def test_pre_edit_drift_returns_none_within_explore_budget(monkeypatch):
    """A handful of explore steps under budget must not trip the guard."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "5")
    session = "\n".join(_grep_step(f"symbol_{i}") for i in range(5)) + "\n"
    assert (
        kb.implement_pre_edit_drift_reason_from_log(
            "t_drift", "implement", session=session
        )
        is None
    )


def test_pre_edit_drift_fires_when_explore_exceeds_budget(monkeypatch):
    """More explore steps than the budget trips the IMPLEMENT_HANDOFF_DRIFT reason."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "3")
    session = "\n".join(_grep_step(f"symbol_{i}") for i in range(4)) + "\n"
    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_drift", "implement", session=session
    )
    assert reason is not None
    assert "BLOCKER=IMPLEMENT_HANDOFF_DRIFT" in reason
    assert "pre-edit exploration exceeded budget" in reason
    assert "steps=4" in reason
    assert "limit=3" in reason


def test_pre_edit_drift_fires_when_repeat_reads_exceed_budget(monkeypatch):
    """Reading the same file more than repeat-read budget trips the reason."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_REPEAT_READ_BUDGET", "2")
    session = (
        "\n".join(
            _read_step("/work/services/zoe-data/intent_router.py")
            for _ in range(3)
        )
        + "\n"
    )
    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_drift", "implement", session=session
    )
    assert reason is not None
    assert "BLOCKER=IMPLEMENT_HANDOFF_DRIFT" in reason
    assert "repeated pre-edit reads" in reason
    assert "file=/work/services/zoe-data/intent_router.py" in reason


def test_pre_edit_drift_stands_down_once_a_patch_appears(monkeypatch):
    """A patch mid-session stands the pre-edit guard down even with more greps after."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "3")
    pre = "\n".join(_grep_step(f"symbol_{i}") for i in range(2))
    post = "\n".join(_grep_step(f"late_{i}") for i in range(10))
    session = (
        f"{pre}\n"
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{post}\n"
    )
    assert (
        kb.implement_pre_edit_drift_reason_from_log(
            "t_drift", "implement", session=session
        )
        is None
    )


def test_pre_edit_drift_stands_down_on_terminal_kanban_call(monkeypatch):
    """A kanban_complete or kanban_block step stands the pre-edit guard down."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "3")
    pre = "\n".join(_grep_step(f"symbol_{i}") for i in range(4))
    session = f"{pre}\n{_terminal_step('kanban_complete')}\n"
    assert (
        kb.implement_pre_edit_drift_reason_from_log(
            "t_drift", "implement", session=session
        )
        is None
    )


def test_pre_edit_drift_covers_implement_revision_phase(monkeypatch):
    """The detector applies equally to implement_revision."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "4")
    session = "\n".join(_grep_step(f"symbol_{i}") for i in range(5)) + "\n"
    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_drift", "implement_revision", session=session
    )
    assert reason is not None
    assert "BLOCKER=IMPLEMENT_HANDOFF_DRIFT" in reason


# ---------------------------------------------------------------------------
# implement_edit_safety_reason_from_log
# ---------------------------------------------------------------------------


def test_edit_safety_returns_none_for_non_implement_phases():
    """Edit-safety only fires during implement or implement_revision."""
    session = (
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
    )
    for phase in ("scout", "verify", "review", "closeout", "retro"):
        assert (
            kb.implement_edit_safety_reason_from_log(
                "t_safety", phase, session=session
            )
            is None
        )


def test_edit_safety_returns_none_for_empty_session():
    assert (
        kb.implement_edit_safety_reason_from_log(
            "t_safety", "implement", session=""
        )
        is None
    )


def test_edit_safety_returns_none_when_no_python_patch_occurs():
    """A worker that only reads/greps never trips the post-patch guard."""
    session = (
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
    )
    assert (
        kb.implement_edit_safety_reason_from_log(
            "t_safety", "implement", session=session
        )
        is None
    )


def test_edit_safety_returns_none_when_check_follows_python_patch(monkeypatch):
    """A py_compile after the Python patch satisfies the safety guard."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET", "2")
    session = (
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_py_compile_step('/work/services/zoe-data/intent_router.py')}\n"
    )
    assert (
        kb.implement_edit_safety_reason_from_log(
            "t_safety", "implement", session=session
        )
        is None
    )


def test_edit_safety_fires_on_excess_patched_file_reads(monkeypatch):
    """A third read of the patched file (budget=2) trips the guard."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET", "2")
    session = (
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
    )
    assert kb.implement_edit_safety_reason_from_log(
        "t_safety", "implement", session=session
    ) == (
        "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
        "exploration before py_compile/focused tests"
    )


def test_edit_safety_fires_on_unrelated_file_read_after_patch(monkeypatch):
    """Reading any other file (not just the patched one) also trips the guard."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET", "2")
    session = (
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/main.py')}\n"
    )
    assert kb.implement_edit_safety_reason_from_log(
        "t_safety", "implement", session=session
    ) == (
        "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
        "exploration before py_compile/focused tests"
    )


def test_edit_safety_repatch_resets_the_post_patch_read_budget(monkeypatch):
    """A second Python patch resets the patched-file read budget."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET", "2")
    session = (
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
    )
    assert (
        kb.implement_edit_safety_reason_from_log(
            "t_safety", "implement", session=session
        )
        is None
    )


def test_edit_safety_allows_non_python_patch_followed_by_python_check():
    """Patching a non-Python file after a Python patch is productive work."""
    session = (
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_patch_step('/work/.github/workflows/validate.yml')}\n"
        f"{_pytest_step('services/zoe-data/tests/test_pipeline_evidence.py')}\n"
    )
    assert (
        kb.implement_edit_safety_reason_from_log(
            "t_safety", "implement", session=session
        )
        is None
    )


def test_edit_safety_covers_implement_revision_phase(monkeypatch):
    """The detector applies equally to implement_revision."""
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_PATCH_FILE_READ_BUDGET", "1")
    session = (
        f"{_patch_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
        f"{_read_step('/work/services/zoe-data/intent_router.py')}\n"
    )
    assert kb.implement_edit_safety_reason_from_log(
        "t_safety", "implement_revision", session=session
    ) == (
        "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
        "exploration before py_compile/focused tests"
    )
