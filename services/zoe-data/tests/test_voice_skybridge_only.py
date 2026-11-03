"""ZOE_SKYBRIDGE_ONLY gates the legacy voice domain-navigation helpers.

When the flag is set, voice must not navigate the touch panel to per-domain
legacy pages (`/touch/weather.html`, `/touch/calendar.html`, `/touch/dashboard.html`)
— the panel stays on Skybridge. (The "let's talk" jump to the retired `/touch/voice.html` is gone, A4 2026-10-19.) The Skybridge-first path
(`_broadcast_skybridge_ui`) is unaffected and still renders real cards.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import routers.voice_tts as vt

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("1", True), ("on", True), ("YES", True),
     ("false", False), ("0", False), ("", False)],
)
def test_skybridge_only_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("ZOE_SKYBRIDGE_ONLY", value)
    assert vt._skybridge_only() is expected


def test_skybridge_only_unset_is_false(monkeypatch):
    monkeypatch.delenv("ZOE_SKYBRIDGE_ONLY", raising=False)
    assert vt._skybridge_only() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "helper,args",
    [
        ("_broadcast_weather_ui", ("zoe-touch-pi", "x")),
        ("_broadcast_calendar_ui", ("zoe-touch-pi", "x")),
        ("_broadcast_reminder_ui", ("zoe-touch-pi", "x")),
    ],
)
async def test_legacy_helpers_noop_when_flag_on(monkeypatch, helper, args):
    monkeypatch.setenv("ZOE_SKYBRIDGE_ONLY", "true")

    # Any attempt to enqueue/broadcast would mean the helper did NOT early-return.
    def _boom(*a, **k):
        raise AssertionError(f"{helper} enqueued a UI action despite ZOE_SKYBRIDGE_ONLY")

    monkeypatch.setattr("ui_orchestrator.enqueue_ui_action", _boom, raising=False)
    # Mock the broadcaster too so a helper that broadcasts before it enqueues is
    # genuinely covered by its early return.
    monkeypatch.setattr("push.broadcaster.broadcast", _boom, raising=False)
    # Returns cleanly (no enqueue, no broadcast, no navigation) — stays on Skybridge.
    await getattr(vt, helper)(*args)
