"""Moonshine's process-global Silero VAD state must not leak from one batch STT call into the next.

moonshine-voice segments every clip with ONE global Silero whose recurrent state is never cleared, so a clip's
segmentation/transcript depended on the audio heard before it (measured: one clip, 19 different predecessors -> up
to 12 segmentations). ``reset_silero_state`` zeroes that state; ``_run_moonshine`` calls it before every batch call.
"""
import asyncio
import ctypes
import struct
import sys
import types

import pytest

import moonshine_vad_state as mvs

pytestmark = pytest.mark.ci_safe


class _FakeSilero:
    """A heap object laid out like SileroVad's head: 27 8-byte words with std::vector<float> triples where the real
    class has them (``_context`` @48, ``input`` @136, ``_state`` @168), other words being pointers/ints."""

    def __init__(self, extra_state_vector=False):
        self.context = (ctypes.c_float * 64)(*([1.0] * 64))
        self.inp = (ctypes.c_float * 576)(*([1.0] * 576))
        self.state = (ctypes.c_float * 256)(*([1.0] * 256))
        self.state2 = (ctypes.c_float * 256)(*([1.0] * 256))
        a = lambda arr: ctypes.addressof(arr)  # noqa: E731
        words = [0x1111] * 27
        words[6:9] = [a(self.context), a(self.context) + 64 * 4, a(self.context) + 64 * 4]
        words[17:20] = [a(self.inp), a(self.inp) + 576 * 4, a(self.inp) + 576 * 4]
        words[21:24] = [a(self.state), a(self.state) + 256 * 4, a(self.state) + 256 * 4]
        if extra_state_vector:  # a second 256-float vector -> ambiguous layout, must refuse
            words[24:27] = [a(self.state2), a(self.state2) + 256 * 4, a(self.state2) + 256 * 4]
        self.buf = ctypes.create_string_buffer(struct.pack("<27Q", *words), 27 * 8 + 64)

    @property
    def address(self):
        return ctypes.addressof(self.buf)


def test_reset_zeroes_state_and_context_only(monkeypatch):
    obj = _FakeSilero()
    monkeypatch.setattr(mvs, "_object_address", lambda lib: obj.address)
    assert mvs.reset_silero_state(object()) is True
    assert not any(obj.state) and not any(obj.context)
    assert all(v == 1.0 for v in obj.inp), "the per-call input scratch buffer is not state and must be left alone"


def test_unexpected_layout_writes_nothing(monkeypatch):
    obj = _FakeSilero(extra_state_vector=True)
    monkeypatch.setattr(mvs, "_object_address", lambda lib: obj.address)
    monkeypatch.setattr(mvs, "_warned", False)
    assert mvs.reset_silero_state(object()) is False
    assert all(v == 1.0 for v in obj.state) and all(v == 1.0 for v in obj.context)


def test_no_silero_yet_or_missing_symbol_is_a_noop(monkeypatch):
    monkeypatch.setattr(mvs, "_object_address", lambda lib: 0)
    assert mvs.reset_silero_state(object()) is False  # pristine zeros already
    monkeypatch.setattr(mvs, "_warned", False)
    assert mvs.reset_silero_state(object()) is False  # a plain object has no symbol: ctypes raises -> swallowed
    monkeypatch.undo()
    assert mvs.reset_silero_state(object()) is False


class _StatefulTranscriber:
    """Fake Moonshine whose output depends on VAD state carried from the previous call (like the real library):
    after any call the state is dirty, and a dirty state decodes the next clip to nothing."""

    def __init__(self):
        self.dirty = False
        self._lib = object()

    def transcribe_without_streaming(self, audio, sr):
        text = "" if self.dirty else "turn on the lights"
        self.dirty = True
        return types.SimpleNamespace(lines=[types.SimpleNamespace(text=text)] if text else [])


def _run_two_turns(monkeypatch, tmp_path, flag):
    from routers import voice_tts

    tr = _StatefulTranscriber()
    monkeypatch.setattr(voice_tts, "_ensure_moonshine", lambda: tr)
    monkeypatch.setattr(voice_tts, "reset_silero_state", lambda lib: setattr(tr, "dirty", False) or True)
    fake_utils = types.SimpleNamespace(load_wav_file=lambda p: ([0.1, 0.2, 0.3], 16000))
    monkeypatch.setitem(sys.modules, "moonshine_voice.utils", fake_utils)
    monkeypatch.setitem(sys.modules, "moonshine_voice", types.SimpleNamespace(utils=fake_utils))
    monkeypatch.setenv("ZOE_MOONSHINE_VAD_RESET", flag)
    wav = tmp_path / "turn.wav"
    wav.write_bytes(b"RIFF....")
    return [asyncio.run(voice_tts._run_moonshine(str(wav))) for _ in range(3)]


def test_every_batch_call_starts_from_clean_vad_state(monkeypatch, tmp_path):
    assert _run_two_turns(monkeypatch, tmp_path, "1") == ["turn on the lights"] * 3


def test_negative_control_without_reset_history_empties_later_turns(monkeypatch, tmp_path):
    # Flag off == the pre-fix behaviour: the identical clip decodes to "" once any earlier call dirtied the state.
    assert _run_two_turns(monkeypatch, tmp_path, "0") == ["turn on the lights", "", ""]
