"""Every empty Moonshine result names its reason in the log (the fast-empty investigation, 2026-10-28)."""
import pytest

pytestmark = pytest.mark.ci_safe


def test_reason_no_segments_for_voiced_audio():
    from routers import voice_tts

    msg = voice_tts._moonshine_empty_reason([], [0.1] * 16000 * 2, 16000)
    assert msg.startswith("reason=no_segments lines=0 ")
    assert "audio_s=2.00" in msg and "active_s=2.00" in msg


def test_reason_blank_lines_and_silence():
    from routers import voice_tts

    msg = voice_tts._moonshine_empty_reason(["", " "], [0.0] * 8000, 16000)
    assert msg.startswith("reason=blank_lines lines=2 ")
    assert "active_s=0.00" in msg


def test_reason_never_raises_on_bad_audio():
    from routers import voice_tts

    assert "reason=no_segments" in voice_tts._moonshine_empty_reason([], None, 0)
