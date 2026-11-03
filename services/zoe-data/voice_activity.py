"""Household voice-activity stamp: "a real voice turn is happening now".

Called by every voice entry point (panel command / transcribe, LiveKit brain turns) and read by the
readiness endpoint so a deploy can wait for a quiet household. Harness / probe / CI traffic (the
server-bound ``traffic_class``) never stamps - a replay run must not look like somebody talking.
"""
from __future__ import annotations

import time
from typing import Optional

_LAST_VOICE_ACTIVITY_MONO: Optional[float] = None


def note_voice_activity() -> None:
    """Stamp now, unless the current turn is not household traffic. Never raises."""
    global _LAST_VOICE_ACTIVITY_MONO
    try:
        import traffic_class
        if not traffic_class.is_household():
            return
    except Exception:  # noqa: BLE001 - classification trouble must not hide real activity
        pass
    _LAST_VOICE_ACTIVITY_MONO = time.monotonic()


def last_activity_age_s(now_mono: Optional[float] = None) -> Optional[float]:
    """Seconds since the last stamp, or None when there has been none."""
    if _LAST_VOICE_ACTIVITY_MONO is None:
        return None
    t = time.monotonic() if now_mono is None else now_mono
    return t - _LAST_VOICE_ACTIVITY_MONO
