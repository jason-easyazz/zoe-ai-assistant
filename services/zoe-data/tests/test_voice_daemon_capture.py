"""Regression net for the panel voice-daemon capture tuning.

The daemon (scripts/setup/zoe_voice_daemon.py) is a device-side script — it imports
pyaudio/openwakeword at module load and runs the mic loop, so it can't be imported
or exercised in CI. We still want a net so a future tweak can't silently revert the
onset-clipping fix, so we pin the tuned constants via source inspection (no import)
and verify the lookback-ring seeding semantics with a pure-stdlib model.
"""
import pytest
import os
import re
from collections import deque

pytestmark = pytest.mark.ci_safe

_DAEMON = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "setup", "zoe_voice_daemon.py")
)
_SRC = open(_DAEMON, encoding="utf-8").read()


def test_preroll_widened_for_onset():
    # ~1.6s pre-roll (20 chunks) so the command onset survives the wake word + the
    # 2-confirm delay + openWakeWord's lag. 12 chunks (~960ms) landed inside the wake
    # word, so real captures began on "Zoe" and lost the command onset (PR #1326).
    assert re.search(r'PREROLL_CHUNKS"\s*,\s*"20"', _SRC), "PREROLL_CHUNKS default should be 20"


def test_followup_vad_threshold_sensitive():
    # Lowered from 0.45 so follow-up speech triggers earlier in the envelope.
    assert re.search(r'FOLLOW_UP_VAD_THRESHOLD"\s*,\s*"0\.35"', _SRC)


def test_followup_lookback_ring_present():
    # The follow-up listen keeps a pre-VAD lookback ring and seeds the recording
    # from it (instead of just the trigger chunk) — this is the onset-clip fix.
    assert re.search(r'FOLLOWUP_LOOKBACK_CHUNKS"\s*,\s*"4"', _SRC)
    assert "lookback.append(data)" in _SRC
    assert "frames = list(lookback)" in _SRC


def test_lookback_ring_keeps_onset_including_trigger():
    # Models the seeding logic: a maxlen-4 ring fed every scanned chunk holds the
    # trigger chunk PLUS the 3 before it, restoring the pre-onset audio VAD latency
    # would otherwise drop.
    ring: deque = deque(maxlen=4)
    for chunk in [b"c0", b"c1", b"c2", b"c3", b"c4", b"TRIGGER"]:
        ring.append(chunk)
    frames = list(ring)
    assert frames[-1] == b"TRIGGER"                      # trigger chunk included
    assert frames == [b"c2", b"c3", b"c4", b"TRIGGER"]   # + the 3 immediately before it
