"""tests/unit never writes the box's LIVE per-user state (the zoe-data lane's twin is test_live_store_isolation.py).

Measured 2026-11-14, every ``ci_safe`` file alone with a throwaway ``HOME``: this lane wrote
``~/.zoe/forget_session_epochs.json`` (2 files), ``~/.zoe/night-extract/`` (1) and
``~/.cache/zoe/voice_probe_selftest_trend.jsonl`` (1). ``conftest.py`` pins the first two per test; the probe test
pins the third in its own driver.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_LIVE = str(Path.home() / ".zoe") + os.sep


def test_forget_session_epochs_are_per_test(tmp_path):
    import forget_session

    assert tmp_path in forget_session._PATH.parents and not str(forget_session._PATH).startswith(_LIVE)
    assert forget_session.sidecar_session("u1", "s1") == "s1"
    forget_session.note_forget("u1")
    assert forget_session._PATH.is_file() and forget_session.sidecar_session("u1", "s1").startswith("s1~f")


def test_night_extract_dir_is_per_test(tmp_path):
    value = os.environ.get("ZOE_NIGHT_EXTRACT_DIR")
    assert value and tmp_path in Path(value).parents and not value.startswith(_LIVE)
