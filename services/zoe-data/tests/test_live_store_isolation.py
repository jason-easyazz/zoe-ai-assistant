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
