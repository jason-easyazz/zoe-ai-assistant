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


def test_warm_speaker_save_after_the_test_ends_still_cannot_reach_the_live_file(monkeypatch):
    """The real leak: ``warm_speaker``'s guard keys on ``PYTEST_CURRENT_TEST``, but its save thread outlives the test, so the save ran with
    the guard off and hit ``~/.zoe``. With the state path pinned, the path is the throwaway one even when that variable is gone."""
    import warm_speaker

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    path = warm_speaker._path()
    assert path is not None
    assert (_HOME / ".zoe").resolve() not in path.resolve().parents


def test_exercising_the_state_writers_under_an_empty_home_leaves_it_empty(tmp_path):
    """Behavioural control: run the files that exercise warm_speaker / the voice-turn note / the timer store with ``HOME`` an empty dir
    and nothing may appear under it. Red when the conftest pins are removed (``.zoe/warm-speaker.json`` appears under the fake HOME)."""
    import subprocess
    import sys

    here = pathlib.Path(__file__).resolve().parent
    home = tmp_path / "home"
    home.mkdir()
    files = [
        "test_kv_warm_member_prefix.py", "test_kv_warm_prod_path.py", "test_voice_facts_warm.py", "test_wake_prewarm_guest.py",
        "test_warm_speaker_persist.py", "test_timer_ring_owner_and_persistence.py",
    ]
    env = {k: v for k, v in os.environ.items() if k not in _STATE_VARS and k != "PYTEST_CURRENT_TEST"}
    import site

    env["PYTHONUSERBASE"] = site.getuserbase()      # the installed packages live under the real HOME; only the DATA paths move
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *[str(here / f) for f in files]],
        cwd=str(here.parent), env=env, capture_output=True, text=True, timeout=600,
    )
    leaked = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    assert not leaked, f"tests wrote under the real-HOME stand-in: {leaked}"
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-1000:]
