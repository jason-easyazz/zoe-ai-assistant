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
        if extra_state_vector:  # state vector moved off its measured offset -> different layout, must refuse
            words[21:24] = [0x1111] * 3
            words[24:27] = [a(self.state2), a(self.state2) + 256 * 4, a(self.state2) + 256 * 4]
        self.buf = ctypes.create_string_buffer(struct.pack("<27Q", *words), 27 * 8 + 64)

    @property
    def address(self):
        return ctypes.addressof(self.buf)


@pytest.fixture(autouse=True)
def _supported(monkeypatch):
    monkeypatch.setattr(mvs, "_supported_version", lambda: True)
    monkeypatch.setattr(mvs, "_warned", False)  # restored at teardown, so no test leaks the once-only warning state


def test_reset_zeroes_state_and_context_only(monkeypatch):
    obj = _FakeSilero()
    monkeypatch.setattr(mvs, "_object_address", lambda lib: obj.address)
    assert mvs.reset_silero_state(object()) is True
    assert not any(obj.state) and not any(obj.context)
    assert all(v == 1.0 for v in obj.inp), "the per-call input scratch buffer is not state and must be left alone"


def test_unsupported_version_never_touches_memory(monkeypatch):
    obj = _FakeSilero()
    looked = []
    monkeypatch.setattr(mvs, "_object_address", lambda lib: looked.append(1) or obj.address)
    monkeypatch.setattr(mvs, "_supported_version", lambda: False)
    monkeypatch.setattr(mvs, "_warned", False)
    assert mvs.reset_silero_state(object()) is False
    assert looked == [], "an unvalidated version must not even resolve the object"
    assert all(v == 1.0 for v in obj.state) and all(v == 1.0 for v in obj.context)
    monkeypatch.setattr(mvs, "_supported_version", lambda: True)  # control: same object resets once supported
    assert mvs.reset_silero_state(object()) is True and not any(obj.state)


def test_unexpected_layout_writes_nothing(monkeypatch):
    obj = _FakeSilero(extra_state_vector=True)
    monkeypatch.setattr(mvs, "_object_address", lambda lib: obj.address)
    monkeypatch.setattr(mvs, "_warned", False)
    assert mvs.reset_silero_state(object()) is False
    assert all(v == 1.0 for v in obj.state) and all(v == 1.0 for v in obj.context)


def test_unmapped_object_address_is_an_error_not_a_fault(monkeypatch):
    monkeypatch.setattr(mvs, "_object_address", lambda lib: 0x10)  # never mapped
    monkeypatch.setattr(mvs, "_warned", False)
    assert mvs.reset_silero_state(object()) is False


def test_unreadable_vector_target_writes_nothing(monkeypatch):
    obj = _FakeSilero()
    words = list(struct.unpack("<27Q", obj.buf.raw[:216]))
    words[21:24] = [0x20, 0x20 + 256 * 4, 0x20 + 256 * 4]  # plausible triple pointing at unmapped memory
    ctypes.memmove(obj.buf, struct.pack("<27Q", *words), 216)
    monkeypatch.setattr(mvs, "_object_address", lambda lib: obj.address)
    monkeypatch.setattr(mvs, "_warned", False)
    assert mvs.reset_silero_state(object()) is False
    assert all(v == 1.0 for v in obj.context)


def test_no_silero_yet_or_missing_symbol_is_a_noop(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(mvs, "_object_address", lambda lib: 0)
        assert mvs.reset_silero_state(object()) is False  # pristine zeros already
    # real _object_address on a plain object has no symbol: ctypes raises -> swallowed
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
    assert _run_two_turns(monkeypatch, tmp_path, "1") == ["Turn on the lights"] * 3


def test_negative_control_without_reset_history_empties_later_turns(monkeypatch, tmp_path):
    # Flag off == the pre-fix behaviour: the identical clip decodes to "" once any earlier call dirtied the state.
    assert _run_two_turns(monkeypatch, tmp_path, "0") == ["Turn on the lights", "", ""]


# --- STT_EMPTY diagnostics (model-free): reason logged for every empty return, never for a real transcript -------

def _run_with(monkeypatch, tmp_path, lines, rate=16000, flat=None, audio=None):
    from routers import voice_tts

    class _Tr:
        _lib = None

        def transcribe_without_streaming(self, a, sr):
            out = types.SimpleNamespace(lines=[types.SimpleNamespace(text=t) for t in lines])
            if flat is not None:
                out.text = flat
            return out

    audio = [0.0] * 16000 if audio is None else audio
    monkeypatch.setattr(voice_tts, "_ensure_moonshine", lambda: _Tr())
    fake_utils = types.SimpleNamespace(load_wav_file=lambda p: (audio, rate))
    monkeypatch.setitem(sys.modules, "moonshine_voice.utils", fake_utils)
    monkeypatch.setitem(sys.modules, "moonshine_voice", types.SimpleNamespace(utils=fake_utils))
    wav = tmp_path / "t.wav"
    wav.write_bytes(b"RIFF....")
    return asyncio.run(voice_tts._run_moonshine(str(wav)))


def _empty_logs(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("STT_EMPTY")]


def test_zero_lines_logs_no_segments(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    assert _run_with(monkeypatch, tmp_path, []) == ""
    logs = _empty_logs(caplog)
    assert len(logs) == 1 and "reason=no_segments" in logs[0] and "lines=0" in logs[0]


def test_blank_lines_logs_blank_lines(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    assert _run_with(monkeypatch, tmp_path, ["", ""]) == ""
    logs = _empty_logs(caplog)
    assert len(logs) == 1 and "reason=blank_lines" in logs[0] and "lines=2" in logs[0]


def test_bad_sample_rate_logs_bad_sample_rate(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    assert _run_with(monkeypatch, tmp_path, ["x"], rate=0) == ""
    assert _empty_logs(caplog) == ["STT_EMPTY reason=bad_sample_rate"]


def test_non_empty_result_does_not_log_stt_empty(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    assert _run_with(monkeypatch, tmp_path, ["turn on the lights"]) == "Turn on the lights"
    assert _empty_logs(caplog) == []


# --- under-speech streaming sessions start from the same clean VAD state ----------------------------------------

class _FakeStreamTr:
    _lib = object()

    def __init__(self, events):
        self.events = events

    def create_stream(self):
        self.events.append("create")
        ev = self.events
        return types.SimpleNamespace(start=lambda: ev.append("start"))


def _stream_setup(monkeypatch, flag="1"):
    from routers import voice_tts

    events = []
    monkeypatch.setattr(voice_tts, "_ensure_moonshine", lambda: _FakeStreamTr(events))
    monkeypatch.setattr(voice_tts, "reset_silero_state", lambda lib: events.append("reset") or True)
    monkeypatch.setenv("ZOE_MOONSHINE_VAD_RESET", flag)
    return voice_tts, events


def test_stream_open_resets_after_create_and_before_start(monkeypatch):
    vt, events = _stream_setup(monkeypatch)
    vt._open_moonshine_stream()
    assert events == ["create", "reset", "start"]
    assert vt._moonshine_infer_lock.acquire(timeout=1), "infer lock must be released after the reset"
    vt._moonshine_infer_lock.release()


def test_stream_open_flag_off_does_not_reset(monkeypatch):
    vt, events = _stream_setup(monkeypatch, flag="0")
    vt._open_moonshine_stream()
    assert events == ["create", "start"]


def test_stream_open_skips_reset_instead_of_stalling_behind_a_held_infer_lock(monkeypatch):
    vt, events = _stream_setup(monkeypatch)
    assert vt._moonshine_infer_lock.acquire(timeout=1)
    try:
        t0 = __import__("time").monotonic()
        vt._open_moonshine_stream()
        assert __import__("time").monotonic() - t0 < 2.0
    finally:
        vt._moonshine_infer_lock.release()
    assert events == ["create", "start"]
