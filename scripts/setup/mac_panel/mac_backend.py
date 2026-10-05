"""macOS platform backend for the Zoe voice daemon ("virtual panel").

Loaded BY PATH from scripts/setup/zoe_voice_daemon.py, and only when
PANEL_PLATFORM=mac (or auto on Darwin). The Pi never imports this file, which is
why deploy-pi-voice.sh ships nothing new. Design + what it can and cannot prove:
docs/knowledge/mac-virtual-panel.md.

Why this shape
--------------
The daemon plays through a *subprocess-shaped* object: ``poll()``, ``terminate()``,
``kill()``, ``wait()``, ``pid`` and, for the gapless sentence stream, a ``stdin`` to
write raw PCM into. Barge-in terminates that object and phase 1 ducks it. On the Pi
that object is ``aplay`` and the duck is ``pactl set-sink-input-volume``.

macOS has no per-stream mixer volume to reach from outside a process:

* ``afplay`` takes ``-v`` only at launch and has no device option (ss64's afplay
  page documents no way to change volume mid-playback).
* ``osascript -e 'set volume output volume N'`` is the SYSTEM volume. It ducks every
  app, costs a process spawn (tens of ms) per step and, if the daemon dies while
  ducked, leaves the laptop quiet. It is the wrong tool for a decision that must be
  reversible on every exit path.

So the player here is IN-PROCESS: ``MacPlayer`` is a Popen-lookalike that feeds a
PyAudio output stream (PortAudio, the library the daemon already uses for the mic -
one PortAudio, not two) and multiplies each ~20 ms block by a gain. The duck is that
gain. It dies with the player, so it cannot leak, and ``_BargeEpisode`` /
``_BargeDecider`` / ``_PlayoutLedger`` run UNCHANGED over it - the decide logic is
exercised exactly as on the Pi; only the actuator differs.

``afplay`` remains for MP3 replies only (the daemon never asks for MP3 today); it
cannot be ducked, so a barge on it falls back to the hard stop, which is the
documented "duck unavailable" path.
"""
from __future__ import annotations

import itertools
import subprocess
import threading
import wave

import numpy as np


def db_to_gain(db: float) -> float:
    """Decibels (negative = quieter) -> linear amplitude factor."""
    return 10.0 ** (float(db) / 20.0)


class _PCMPipe:
    """The ``stdin`` of a MacPlayer: write/flush/close, like a pipe to aplay."""

    def __init__(self, player: "MacPlayer"):
        self._player = player

    def write(self, data: bytes) -> int:
        return self._player._write(data)

    def flush(self) -> None:  # nothing is held back: the player thread drains the buffer
        return None

    def close(self) -> None:
        self._player._close_input()


class MacPlayer:
    """A Popen-lookalike that plays 16-bit PCM through a PyAudio output stream.

    Input is always 16-bit little-endian; mono is duplicated to stereo because the
    daemon already does that for "USB speakerphones that reject mono playback" and
    CoreAudio devices are stereo. If the device refuses the stream's sample rate the
    stream is reopened at the device's default rate and the audio is resampled
    (linear, per block - a dev tool, not an audiophile path).

    ``stdin.write`` blocks while ``max_buffer_bytes`` are queued, like a full pipe,
    so the reply loop stays paced to the speaker (the barge-in ledger models the
    run-ahead either way).
    """

    _pids = itertools.count(900001)  # not real pids: nothing signals them

    def __init__(self, backend: "MacBackend", rate: int, channels: int, *,
                 block_ms: int = 20, max_buffer_bytes: int = 65536):
        if channels not in (1, 2):
            raise NotImplementedError(f"{channels}-channel PCM")
        self.pid = next(self._pids)
        self.returncode: int | None = None
        self.stdin = _PCMPipe(self)
        self._backend = backend
        self._rate = int(rate)
        self._channels = int(channels)
        self._block_ms = max(1, int(block_ms))
        self._block_frames = max(1, self._rate * self._block_ms // 1000)
        self._block_bytes = self._block_frames * self._channels * 2
        self._max = max(self._block_bytes, int(max_buffer_bytes))
        self._cond = threading.Condition()
        self._buf = bytearray()
        self._input_closed = False
        self._aborted = False
        self._done = threading.Event()
        self._gain = 1.0
        self._target = 1.0
        self._step = 0.0
        self._out_rate = self._rate
        self._thread = threading.Thread(target=self._run, daemon=True, name="mac-player")
        self._thread.start()

    # ── construction helpers ────────────────────────────────────────────────
    @classmethod
    def from_wav_file(cls, backend: "MacBackend", path: str) -> "MacPlayer":
        """Play a whole 16-bit WAV file: the data is queued up front, input closed."""
        with wave.open(path, "rb") as wf:
            if wf.getsampwidth() != 2:
                raise NotImplementedError(f"{wf.getsampwidth() * 8}-bit WAV")
            player = cls(backend, wf.getframerate(), wf.getnchannels())
            pcm = wf.readframes(wf.getnframes())
        with player._cond:
            player._buf += pcm
            player._input_closed = True
            player._cond.notify_all()
        return player

    # ── the subprocess surface ──────────────────────────────────────────────
    def poll(self):
        return self.returncode

    def wait(self, timeout: float | None = None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("mac-player", timeout)
        return self.returncode

    def terminate(self) -> None:
        self._abort(-15)

    def kill(self) -> None:
        self._abort(-9)

    def _abort(self, code: int) -> None:
        with self._cond:
            if self.returncode is None:
                self.returncode = code
                self._aborted = True
                self._done.set()
            self._cond.notify_all()

    # ── gain (the duck) ─────────────────────────────────────────────────────
    def set_gain(self, gain: float, ramp_ms: int = 0) -> None:
        """Move the output gain to ``gain`` (linear) at once, or over ``ramp_ms``."""
        with self._cond:
            self._target = max(0.0, float(gain))
            if ramp_ms <= 0:
                self._gain, self._step = self._target, 0.0
            else:
                blocks = max(1, int(round(ramp_ms / float(self._block_ms))))
                self._step = (self._target - self._gain) / blocks

    @property
    def gain(self) -> float:
        with self._cond:
            return self._gain

    def _gain_for_next_block(self) -> float:
        with self._cond:
            g = self._gain
            if g != self._target:
                g += self._step
                if (self._step >= 0 and g >= self._target) or (self._step < 0 and g <= self._target):
                    g = self._target
                self._gain = g
            return g

    # ── input side ──────────────────────────────────────────────────────────
    def _write(self, data: bytes) -> int:
        view = memoryview(bytes(data))
        pos = 0
        while pos < len(view):
            with self._cond:
                while (self.returncode is None and not self._aborted
                       and len(self._buf) >= self._max):
                    self._cond.wait(0.05)
                if self._aborted or self.returncode is not None:
                    raise BrokenPipeError("player stopped")
                if self._input_closed:
                    raise ValueError("write to closed file")
                piece = view[pos:pos + (self._max - len(self._buf))]
                self._buf += piece
                pos += len(piece)
                self._cond.notify_all()
        return len(view)

    def _close_input(self) -> None:
        with self._cond:
            self._input_closed = True
            self._cond.notify_all()

    def _next_block(self):
        """Next block of input bytes; b"" once closed and drained; None if aborted."""
        with self._cond:
            while True:
                if self._aborted:
                    return None
                if len(self._buf) >= self._block_bytes or (self._input_closed and self._buf):
                    block = bytes(self._buf[:self._block_bytes])
                    del self._buf[:self._block_bytes]
                    self._cond.notify_all()
                    return block
                if self._input_closed:
                    return b""
                self._cond.wait(0.05)

    # ── output side ─────────────────────────────────────────────────────────
    def _open_stream(self):
        pa = self._backend.pa()
        mod = self._backend.pyaudio
        kw = dict(format=mod.paInt16, channels=2, rate=self._rate, output=True,
                  frames_per_buffer=self._block_frames)
        idx = self._backend.output_device_index()
        if idx is not None:
            kw["output_device_index"] = idx
        try:
            return self._backend.open_stream(**kw)
        except OSError as first:
            info = (pa.get_device_info_by_index(idx) if idx is not None
                    else pa.get_default_output_device_info())
            rate = int(info.get("defaultSampleRate") or 0)
            if rate <= 0 or rate == self._rate:
                raise
            kw["rate"] = rate
            kw["frames_per_buffer"] = max(1, rate * self._block_ms // 1000)
            self._backend.note(f"output refused {self._rate} Hz ({first}); resampling to {rate} Hz")
            stream = self._backend.open_stream(**kw)
            self._out_rate = rate
            return stream

    def _render(self, raw: bytes) -> bytes:
        a = np.frombuffer(raw, dtype=np.int16)
        if self._channels == 1:
            a = np.repeat(a, 2)
        if self._out_rate != self._rate:
            frames = a.reshape(-1, 2).astype(np.float32)
            n_out = max(1, int(round(len(frames) * self._out_rate / float(self._rate))))
            x_old = np.linspace(0.0, 1.0, len(frames), endpoint=False)
            x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
            a = np.column_stack([np.interp(x_new, x_old, frames[:, c]) for c in (0, 1)]).reshape(-1)
        g = self._gain_for_next_block()
        if g != 1.0:
            a = np.clip(a.astype(np.float32) * g, -32768, 32767)
        return a.astype(np.int16).tobytes()

    def _run(self) -> None:
        stream = None
        code = 0
        try:
            stream = self._open_stream()
            while True:
                raw = self._next_block()
                if raw is None:
                    return  # aborted: returncode was set by terminate()/kill()
                if raw == b"":
                    break
                stream.write(self._render(raw))
            stream.stop_stream()  # drains what the device still holds
        except Exception as exc:  # a dead device must end the player, never the daemon
            self._backend.note(f"playback failed: {exc}")
            code = 1
        finally:
            if stream is not None:
                try:
                    stream.close()  # PortAudio discards pending buffers on an aborted close
                except Exception:
                    pass
            with self._cond:
                if self.returncode is None:
                    self.returncode = code
                self._done.set()
                self._cond.notify_all()


class GainDucker:
    """The duck for a MacPlayer: a gain on the player's own stream.

    Same surface ``_BargeEpisode`` uses on the Pi's ``_SinkInputDucker`` (``duck``,
    ``restore``, ``db``, ``ducked``, ``baseline``). ``baseline`` stays None: the
    gain lives on the player object, so a player that ends while ducked takes the
    duck with it - the PulseAudio ``module-stream-restore`` leak cannot happen here.
    """

    baseline = None

    def __init__(self, player: MacPlayer, db: float, ramp_ms: int = 0):
        self.player = player
        self.db = float(db)
        self.ramp_ms = int(ramp_ms)
        self.ducked = False

    def resolve(self) -> bool:
        return True

    def duck(self) -> bool:
        if self.ducked:
            return False
        self.ducked = True
        self.player.set_gain(db_to_gain(self.db), self.ramp_ms)
        return True

    def restore(self) -> None:
        if not self.ducked:
            return
        self.ducked = False
        self.player.set_gain(1.0, 0)


class NoDucker:
    """A player that cannot be ducked (afplay): ``duck()`` is False, the barge
    falls back to today's hard stop."""

    baseline = None
    ducked = False

    def __init__(self, db: float):
        self.db = float(db)

    def resolve(self) -> bool:
        return False

    def duck(self) -> bool:
        return False

    def restore(self) -> None:
        return None


class MacBackend:
    """The object the daemon calls instead of aplay / pactl / espeak-ng."""

    name = "mac"
    has_panel_agent = False        # no on-box agent: the "screen" is a browser tab
    default_health_bind = "127.0.0.1"  # /activate is unauthenticated; a laptop roams

    def __init__(self, *, pyaudio_module, output_device: str = "default",
                 duck_params=lambda: (-15.0, 0), log=None):
        self.pyaudio = pyaudio_module
        self._output_device = (output_device or "default").strip() or "default"
        self._duck_params = duck_params
        self._log = log
        self._pa = None
        self._pa_lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._out_index_resolved = False
        self._out_index: int | None = None

    # ── PortAudio plumbing ──────────────────────────────────────────────────
    def note(self, msg: str) -> None:
        if self._log is not None:
            self._log.warning("mac backend: %s", msg)

    def pa(self):
        with self._pa_lock:
            if self._pa is None:
                self._pa = self.pyaudio.PyAudio()
            return self._pa

    def open_stream(self, **kw):
        with self._open_lock:  # PortAudio open/close are not worth racing
            return self.pa().open(**kw)

    def output_device_index(self) -> int | None:
        """AUDIO_OUTPUT_DEVICE -> a PortAudio output device index (None = default).

        The daemon defaults AUDIO_OUTPUT_DEVICE to AUDIO_DEVICE, so a name meant for
        the MICROPHONE often reaches here; a name that matches no output-capable
        device falls back to the system default output, once, loudly."""
        if self._out_index_resolved:
            return self._out_index
        spec = self._output_device
        idx: int | None = None
        if spec != "default":
            pa = self.pa()
            if spec.isdigit():
                try:
                    if int(pa.get_device_info_by_index(int(spec)).get("maxOutputChannels", 0)) > 0:
                        idx = int(spec)
                except Exception:
                    idx = None
            else:
                for i in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(i)
                    if int(info.get("maxOutputChannels", 0)) > 0 and spec.lower() in str(info.get("name", "")).lower():
                        idx = i
                        break
            if idx is None:
                self.note(f"AUDIO_OUTPUT_DEVICE={spec!r} matches no output-capable device - "
                          "using the default output (set AUDIO_OUTPUT_DEVICE explicitly to silence this)")
        self._out_index, self._out_index_resolved = idx, True
        return idx

    # ── what the daemon calls (same names as _PiBackend) ────────────────────
    def start_file_player(self, fpath: str, ext: str):
        if ext == "mp3":
            return subprocess.Popen(["afplay", fpath]), "afplay"
        return MacPlayer.from_wav_file(self, fpath), "pyaudio"

    def play_file_blocking(self, fpath: str, timeout: float | None = None) -> None:
        player = MacPlayer.from_wav_file(self, fpath)
        try:
            player.wait(timeout)
        except subprocess.TimeoutExpired:
            player.terminate()
            raise

    def start_buffer_player(self, fpath: str):
        return MacPlayer.from_wav_file(self, fpath)

    def start_pcm_stream(self, rate: int, ch: int, width: int):
        if width != 2:
            raise NotImplementedError(f"{width * 8}-bit PCM (the Mac player takes 16-bit)")
        return MacPlayer(self, rate, ch)

    def local_tts_cmd(self, text: str) -> list:
        return ["say", text]

    def make_ducker(self, proc):
        db, ramp_ms = self._duck_params()
        if hasattr(proc, "set_gain"):
            return GainDucker(proc, db, ramp_ms)
        return NoDucker(db)
