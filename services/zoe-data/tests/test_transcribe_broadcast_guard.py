"""The replay harness must be able to transcribe without touching the panels.

websocket-sync.js applies NO panel_id filter to voice events — every kiosk flips
its orb and resets its auto-home timer on each voice:thinking. The guard is
server-side: callers that are instruments, not users, carry the replay- prefix
and the endpoint stays silent. This was originally claimed to be handled by
client-side filtering that does not exist (caught by Copilot on #1572).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _guard():
    # Import lazily: routers.voice_tts is heavy, but the predicate is module-level.
    from routers.voice_tts import _suppress_ui_broadcast
    return _suppress_ui_broadcast


def test_replay_harness_is_suppressed():
    assert _guard()("replay-harness") is True
    assert _guard()("replay-anything") is True


def test_real_panels_still_broadcast():
    for pid in ("zoe-panel", "kitchen", "unknown", ""):
        assert _guard()(pid) is False


def test_none_is_not_suppressed():
    assert _guard()(None) is False
