"""The wake -> command capture must not drop audio.

Regression pin for the "hey zoe gets the first use wrong" bug. On wake the daemon
used to close the mic, play the chime with a BLOCKING subprocess.run, then open a
fresh mic stream before recording. That took several hundred ms, and every word the
user spoke in that window was silently deleted before STT ever saw it. Replaying the
real corpus showed exactly that:

    said "Hey Zoe, what's my name?"            -> captured "My name."
    said "Hey Zoe, what's on my calendar...?"  -> captured "That's not my calendar..."

Every broken capture began mid-word with "Hey" already chopped off; the captures that
worked were the ones where the user happened to PAUSE after the wake word, letting the
hole land in silence.

The fix records the command straight from the still-open wake stream, so the capture is
contiguous with the pre-roll. These tests pin the three properties that make the dead
air impossible to reintroduce:

  1. recording from the wake stream opens NO new stream (no reopen gap),
  2. it does not close the caller's stream (the caller hands the device to the barge
     monitor itself — the Jabra rejects a second input stream),
  3. on_wake() never blocks the recording (the chime is fire-and-forget).
"""
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


@pytest.fixture(scope="module")
def daemon():
    """Import the daemon with its device-only top-level deps stubbed.

    The daemon imports pyaudio and requests at module load (mic + HTTP to the
    Jetson) — neither is installed in the slim unit-test lane, so importing it
    unstubbed fails at collection with ModuleNotFoundError before any assertion
    runs. numpy is NOT stubbed: it is universally available here and the capture
    path under test uses it functionally (the amplitude endpointer).
    """
    stubs = {}
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    stubs["pyaudio"] = fake_pyaudio
    stubs["requests"] = types.ModuleType("requests")

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_undertest", DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for name, prev in saved.items():
            if prev is not None:
                sys.modules[name] = prev
            else:
                sys.modules.pop(name, None)


class FakeStream:
    """A mic stream over a shared, always-running 'room' timeline.

    Chunk k is emitted as CHUNK_SIZE int16 samples whose first sample encodes k, so a
    dropped chunk is detectable as a gap in the captured id sequence. Speech is loud,
    then the room goes silent so the amplitude endpointer terminates the recording.
    """

    SPEECH_CHUNKS = 6

    def __init__(self, room, chunk_size):
        self.room = room
        self.chunk_size = chunk_size
        self.closed = False

    def read(self, n, exception_on_overflow=True):  # noqa: ARG002 - pyaudio signature
        k = self.room["cursor"]
        self.room["cursor"] += 1
        amp = 12000 if k < self.SPEECH_CHUNKS else 0
        samples = [k] + [amp] * (self.chunk_size - 1)
        return b"".join(int(s).to_bytes(2, "little", signed=True) for s in samples)

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class FakePyAudio:
    def __init__(self, room, chunk_size):
        self.room = room
        self.chunk_size = chunk_size
        self.opened = 0

    def open(self, **kw):
        self.opened += 1
        return FakeStream(self.room, self.chunk_size)

    def get_sample_size(self, fmt):  # noqa: ARG002 - pyaudio signature
        return 2


def _chunk_ids(wav_bytes, chunk_size, daemon):
    """Recover the emitted chunk ids from the captured WAV payload."""
    import io
    import wave

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    ids = []
    stride = chunk_size * 2
    for off in range(0, len(raw) - stride + 1, stride):
        ids.append(int.from_bytes(raw[off:off + 2], "little", signed=True))
    return ids


def test_command_is_contiguous_with_preroll(daemon, monkeypatch):
    """No audio may be lost between the wake word and the recorded command."""
    monkeypatch.setattr(daemon, "SILENCE_TIMEOUT_S", 0.2, raising=False)
    room = {"cursor": 0}
    pa = FakePyAudio(room, daemon.CHUNK_SIZE)
    stream = FakeStream(room, daemon.CHUNK_SIZE)

    # The always-on wake loop has been filling the pre-roll ring; wake fires now.
    daemon._PREROLL.clear()
    for _ in range(3):
        daemon._PREROLL.append(stream.read(daemon.CHUNK_SIZE))

    wav = daemon.record_command(pa, stream=stream)
    assert wav, "recording produced nothing"

    ids = _chunk_ids(wav, daemon.CHUNK_SIZE, daemon)
    # The capture must be one unbroken run starting at the first pre-roll chunk.
    assert ids[0] == 0, f"capture did not start at the pre-roll head: {ids[:4]}"
    assert ids == list(range(ids[0], ids[0] + len(ids))), f"AUDIO WAS DROPPED: {ids}"

    # Reopening the device is what created the hole — it must not happen.
    assert pa.opened == 0, "record_command opened a new stream instead of using the wake stream"


def test_caller_keeps_ownership_of_the_wake_stream(daemon, monkeypatch):
    """The caller closes the wake stream itself (the barge monitor needs the device)."""
    monkeypatch.setattr(daemon, "SILENCE_TIMEOUT_S", 0.2, raising=False)
    room = {"cursor": 0}
    pa = FakePyAudio(room, daemon.CHUNK_SIZE)
    stream = FakeStream(room, daemon.CHUNK_SIZE)
    daemon._PREROLL.clear()

    daemon.record_command(pa, stream=stream)
    assert not stream.closed, "record_command closed a stream it does not own"


def test_follow_up_path_still_opens_its_own_stream(daemon, monkeypatch):
    """Follow-up windows run after the wake stream is closed, so they still self-open."""
    monkeypatch.setattr(daemon, "SILENCE_TIMEOUT_S", 0.2, raising=False)
    room = {"cursor": 0}
    pa = FakePyAudio(room, daemon.CHUNK_SIZE)
    daemon._PREROLL.clear()

    wav = daemon.record_command(pa)
    assert wav, "follow-up recording produced nothing"
    assert pa.opened == 1, "follow-up path must open its own stream"


def test_on_wake_does_not_block_the_recording(daemon, monkeypatch):
    """The chime is fire-and-forget: a slow ALSA open must not delay capture."""
    started = threading.Event()

    def slow_beep():
        started.set()
        time.sleep(1.0)

    monkeypatch.setattr(daemon, "play_wake_beep", slow_beep)
    monkeypatch.setattr(daemon, "_wake_panel_agent", lambda: None)
    monkeypatch.setattr(daemon, "_notify_wake_background", lambda: None)

    t0 = time.monotonic()
    daemon.on_wake()
    elapsed = time.monotonic() - t0

    assert started.wait(timeout=2.0), "the chime never played"
    assert elapsed < 0.2, f"on_wake blocked for {elapsed:.2f}s — it delays the capture"
