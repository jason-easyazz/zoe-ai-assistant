"""STT_EMPTY says where the empty clip came from (``src=`` panel | pendant | probe | harness | other, plus the panel's ``trigger``).

The marker is logged from the executor thread, which has none of the request's context variables, so 2,137 markers in 8 days
could not be told apart (most were the pendant's offline chunks and the replay probe, a handful real panel turns). The tag is
built in the caller's task and handed down as an ARGUMENT. Fields only: never transcript text.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
import types

import pytest

import omi_recordings
import traffic_class
from routers import voice_tts as vt

pytestmark = pytest.mark.ci_safe

_MARKER = re.compile(r"^STT_EMPTY src=(panel|pendant|probe|harness|other)( trigger=(wake|followup))? "
                     r"reason=\w+ lines=-?\d+ audio_s=-?[\d.]+ active_s=-?[\d.]+ peak=-?[\d.]+$")


def _moonshine_empty(monkeypatch, tmp_path, caplog, **kw):
    class _Tr:
        _lib = None

        def transcribe_without_streaming(self, a, sr):
            return types.SimpleNamespace(lines=[types.SimpleNamespace(text="  ")])

    monkeypatch.setattr(vt, "_ensure_moonshine", lambda: _Tr())
    monkeypatch.setattr(vt, "reset_silero_state", lambda *a, **k: None)
    fake_utils = types.SimpleNamespace(load_wav_file=lambda p: ([0.0] * 16000, 16000))
    monkeypatch.setitem(sys.modules, "moonshine_voice.utils", fake_utils)
    monkeypatch.setitem(sys.modules, "moonshine_voice", types.SimpleNamespace(utils=fake_utils))
    wav = tmp_path / "t.wav"
    wav.write_bytes(b"RIFF....")
    caplog.set_level("INFO")
    assert asyncio.run(vt._run_moonshine(str(wav), **kw)) == ""
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("STT_EMPTY")]


def test_marker_carries_src_and_trigger_and_nothing_else(monkeypatch, tmp_path, caplog):
    logs = _moonshine_empty(monkeypatch, tmp_path, caplog, src_tag=vt._stt_src_tag("panel", "followup"))
    assert len(logs) == 1
    assert logs[0].startswith("STT_EMPTY src=panel trigger=followup reason=blank_lines ")
    assert _MARKER.match(logs[0]), logs[0]


def test_unknown_caller_logs_src_other(monkeypatch, tmp_path, caplog):
    logs = _moonshine_empty(monkeypatch, tmp_path, caplog)  # a caller that passes nothing
    assert logs[0].startswith("STT_EMPTY src=other reason=")
    assert vt._stt_src_tag("telegram") == "src=other"
    assert vt._stt_src_tag("other", "wake") == "src=other"  # a trigger is only a panel field


def test_traffic_class_marks_instrument_turns(monkeypatch):
    monkeypatch.setattr(traffic_class, "current", lambda: traffic_class.PROBE)
    assert vt._stt_src_tag("panel", "wake") == "src=probe"
    assert vt._stt_src_tag("harness") == "src=probe"
    assert vt._stt_src_tag("pendant") == "src=pendant"
    monkeypatch.setattr(traffic_class, "current", lambda: traffic_class.HARNESS)
    assert vt._stt_src_tag("panel", "wake") == "src=harness"
    monkeypatch.setattr(traffic_class, "current", lambda: traffic_class.HOUSEHOLD)
    assert vt._stt_src_tag("panel", "wake") == "src=panel trigger=wake"


def test_turn_lanes_derive_panel_and_trigger():
    device = {"source": "device"}
    assert vt._turn_stt_src({}, device) == {"src": "panel", "trigger": "wake"}
    assert vt._turn_stt_src({"window": "followup"}, device) == {"src": "panel", "trigger": "followup"}
    assert vt._turn_stt_src({"window": "conversation"}, device) == {"src": "panel", "trigger": "followup"}
    assert vt._turn_stt_src({"window": "followup"}, {"source": "session"}) == {"src": "other"}


def test_transcribe_audio_hands_the_tag_down_unchanged_result(monkeypatch):
    seen = []

    async def impl(path, src_tag="src=other"):
        seen.append(src_tag)
        return "same text"

    monkeypatch.setattr(vt, "_transcribe_audio_impl", impl)
    monkeypatch.setattr(traffic_class, "current", lambda: traffic_class.HOUSEHOLD)
    out = asyncio.run(vt._transcribe_audio("/tmp/x.wav", capture=False, src="panel", trigger="wake"))
    assert out == "same text" and seen == ["src=panel trigger=wake"]


def test_pendant_chunks_are_tagged_pendant(monkeypatch):
    seen = {}

    async def fake(path, capture=True, **kw):
        seen.update(kw, capture=capture)
        return ""

    monkeypatch.setattr(vt, "_transcribe_audio", fake)
    asyncio.run(omi_recordings._transcribe_file("/tmp/x.wav"))
    assert seen == {"capture": False, "src": "pendant"}


def test_every_voice_tts_call_site_names_its_source():
    """The four voice_tts lanes (transcribe, turn, turn_stream, speculation resolve) each pass a source; the others
    (LiveKit, Telegram) rely on the ``other`` default."""
    lines = inspect.getsource(vt).splitlines()
    sites = [i for i, ln in enumerate(lines) if "await _transcribe_audio(" in ln]
    assert len(sites) == 4, sites
    for i in sites:
        window = " ".join(lines[i:i + 3])
        assert "src=" in window or "_turn_stt_src(" in window, window
