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


def test_local_text_logs_are_pinned_off_the_live_logs():
    """``MemoryService.delete_user`` and the forget cascade rewrite these (local_text_logs). Unpinned, a unit test of either strips the
    household's real router shadow log and intent-miss log."""
    import local_text_logs as ltl

    assert ltl.log_path(ltl.ROUTER_SHADOW) == _pinned("ZOE_ROUTER_HEAD_LOG")
    assert ltl.log_path(ltl.INTENT_MISSES) == _pinned("ZOE_INTENT_MISS_LOG")
    assert ltl.log_path(ltl.INTENT_MISSES) != (_HOME / "training" / "data" / "intent-misses.jsonl").resolve()


def test_router_retrain_ledger_is_pinned_off_home():
    """Every forget / user delete reaches the Phase 3 retrain ledger; a test's forget must never edit the live one."""
    path = _pinned("ZOE_ROUTER_RETRAIN_DIR")
    import router_retrain_ledger

    assert router_retrain_ledger.root().resolve() == path
    assert path != (_HOME / ".zoe" / "router_retrain").resolve()


_STATE_VARS = (
    "ZOE_WARM_SPEAKER_STATE", "ZOE_TIMER_STORE", "ZOE_OMI_AUDIT_PATH", "ZOE_OMI_AUDIO_DIR", "ZOE_CHANGE_NOTICES_STATE_DIR",
    "ZOE_DEPLOY_STATE_DIR", "NIGHT_DIR", "ZOE_PIPELINE_STORE_PATH", "ZOE_MULTICA_DISPATCH_PAUSE_FILE", "ZOE_VOICE_GREETING_STATE_PATH",
    "ZOE_MULTICA_KILL_SWITCH", "ZOE_BLUETOOTH_AUDIT_PATH", "ZOE_VAPID_KEY_PATH",
)


@pytest.mark.parametrize("var", _STATE_VARS)
def test_every_dot_zoe_state_path_is_off_the_live_home(var):
    """2026-10-29: the suite wrote 6 test panel ids into the live ``~/.zoe/warm-speaker.json``. Every state path that defaults under
    ``~/.zoe`` must be pinned off it for the whole session (new knobs: add to ``conftest._STATE_PINS`` AND this tuple)."""
    value = os.environ.get(var)
    assert value, f"{var} must be pinned by services/zoe-data/tests/conftest.py"
    resolved = pathlib.Path(value).resolve()
    assert not str(resolved).startswith(str(_HOME / ".zoe") + "/"), f"{var}={resolved} is under the live ~/.zoe"
    if var != "ZOE_WARM_SPEAKER_STATE":     # that one is per test (autouse fixture, pytest tmp_path); the rest are session-wide
        assert "zoe-test-stores-" in str(resolved)


def test_warm_speaker_session_pin_holds_when_the_test_guard_is_off(monkeypatch):
    """The real leak: ``warm_speaker``'s guard keys on ``PYTEST_CURRENT_TEST``, but its save thread outlives the test, so the save ran with
    the guard off and hit ``~/.zoe``. Prove the SESSION layer on its own (the per-test fixture overrides the variable, so read the value
    conftest recorded): with the guard variable gone and only the session pin in place, the path is the throwaway file."""
    import warm_speaker

    session = os.environ.get("ZOE_TEST_SESSION_WARM_SPEAKER_STATE")
    assert session and "zoe-test-stores-" in session, "conftest must record the session-wide warm-speaker pin"
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ZOE_WARM_SPEAKER_STATE", session)
    assert warm_speaker._path() == pathlib.Path(session)


def test_per_test_fixture_gives_each_test_its_own_warm_speaker_file():
    """The autouse fixture's own layer: the file is under pytest's tmp_path (not the shared session file) and the memo starts empty."""
    import warm_speaker

    path = pathlib.Path(os.environ["ZOE_WARM_SPEAKER_STATE"])
    assert path != pathlib.Path(os.environ["ZOE_TEST_SESSION_WARM_SPEAKER_STATE"])
    assert not path.exists() and warm_speaker._mem == {} and warm_speaker._loaded is False


@pytest.mark.parametrize("module, accessor", [
    ("skybridge_service", "_timer_store_path"), ("voice_greeting", "_state_path"), ("pipeline_store", "store_path"),
    ("multica_dispatch_control", "pause_path"), ("change_notices", "state_dir"), ("change_notices", "deploy_marker"),
    ("omi_devices", "audit_path"),
])
def test_resolved_state_paths_are_off_the_live_home(monkeypatch, module, accessor):
    """Behavioural control, no subprocess: ask each module where it WOULD write (guard variable gone, as in a background thread) and
    require an answer off ``~/.zoe``. Red when a pin is removed from conftest."""
    mod = pytest.importorskip(module)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    resolved = pathlib.Path(str(getattr(mod, accessor)())).resolve()
    assert not str(resolved).startswith(str(_HOME / ".zoe") + "/"), f"{module}.{accessor}() -> {resolved}"
