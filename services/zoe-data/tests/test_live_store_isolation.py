"""Negative control for ``conftest.py``'s live-store pin.

Every assertion here fails under ``pytest --noconftest`` (proved at authoring
time), so a deleted or bypassed pin reddens the lane instead of silently writing
test rows into the household palace again.
"""

from __future__ import annotations

import os
import pathlib

import pytest

pytestmark = pytest.mark.ci_safe

_HOME = pathlib.Path.home().resolve()


def _pinned(var: str) -> pathlib.Path:
    value = os.environ.get(var)
    assert value, f"{var} must be pinned by services/zoe-data/tests/conftest.py"
    path = pathlib.Path(value).resolve()
    assert "zoe-test-stores-" in str(path), f"{var}={path} is not a throwaway test dir"
    assert not str(path).startswith(str(_HOME) + "/."), (
        f"{var}={path} points into the operator's dot-directories"
    )
    return path


def test_palace_dir_is_pinned_off_the_live_palace():
    path = _pinned("MEMPALACE_DATA_DIR")
    assert path != (_HOME / ".mempalace").resolve()


def test_voice_stt_log_is_pinned_off_home():
    path = _pinned("ZOE_VOICE_STT_LOG")
    assert path != (_HOME / ".zoe-voice" / "voice_stt.jsonl").resolve()


def test_memory_service_resolved_the_pin_at_import():
    """The constant is read at import — proves the conftest ran BEFORE the module."""
    memory_service = pytest.importorskip("memory_service")
    assert os.path.realpath(memory_service._MEMPALACE_DATA) == str(
        _pinned("MEMPALACE_DATA_DIR")
    )


def test_app_log_dir_is_pinned_off_the_live_log():
    """A test that enters the FastAPI lifespan runs ``configure_logging()``, which writes to ``$ZOE_LOG_DIR``. Unpinned that is the live
    server's ``~/.zoe-logs/zoe-data.app.log`` (measured 2026-10-13: 355 synthetic-user forget warnings from CI runs)."""
    path = _pinned("ZOE_LOG_DIR")
    assert path != (_HOME / ".zoe-logs").resolve()


def test_configure_logging_with_no_arguments_lands_in_the_pin():
    """What the lifespan does (no ``log_dir``): the handler's file must be under the throwaway dir, never the operator's log."""
    import logging

    logging_setup = pytest.importorskip("logging_setup")
    pin = _pinned("ZOE_LOG_DIR")     # first: if the pin is missing, fail here rather than configure the live log
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    root.handlers[:] = [h for h in saved if getattr(h, "name", None) != logging_setup.HANDLER_NAME]
    try:
        handler = logging_setup.configure_logging()
        assert handler is not None
        assert pathlib.Path(handler.baseFilename).resolve().parent == pin
    finally:
        for h in list(root.handlers):
            if h not in saved:
                root.removeHandler(h)
                h.close()
        root.handlers[:] = saved
        root.setLevel(level)


def test_router_retrain_ledger_is_pinned_off_home():
    """Every forget / user delete reaches the Phase 3 retrain ledger; a test's forget must never edit the live one."""
    path = _pinned("ZOE_ROUTER_RETRAIN_DIR")
    import router_retrain_ledger

    assert router_retrain_ledger.root().resolve() == path
    assert path != (_HOME / ".zoe" / "router_retrain").resolve()
