"""STT under speech: the Moonshine half of "prefill under speech" (flag-dark ``ZOE_STT_STREAM_UNDER_SPEECH``).

The panel daemon uploads mic audio WHILE the user speaks; a session feeds a Moonshine stream on its own worker thread and
``/turn_stream`` takes the finished transcript instead of transcribing the clip afterwards. Numbers, enable/rollback:
docs/knowledge/prefill-under-speech-2026-10-10.md. Safety: the WAV is still POSTed and a stream result is used ONLY when
its sample count equals the WAV's and the text is non-empty (gap, mismatch, timeout, empty, engine error -> batch STT);
sessions are bounded (count, audio length, idle TTL).
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable, Optional

from typed_env import env_bool, env_int

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
_ID_RE = re.compile(r"^[0-9a-f]{8,64}$")
MAX_SESSIONS = 2
MAX_SAMPLES = 30 * SAMPLE_RATE  # the daemon's record cap is 12 s; 30 s is a hard ceiling, not a target
IDLE_TTL_S = 30.0


def stream_under_speech_enabled() -> bool:
    """Per-call env read (like the other voice flags) so a flip needs no restart."""
    return env_bool("ZOE_STT_STREAM_UNDER_SPEECH", default=False)


def finish_timeout_s() -> float:
    return max(0.2, env_int("ZOE_STT_STREAM_FINISH_TIMEOUT_MS", default=2500) / 1000.0)


def valid_stream_id(stream_id: object) -> bool:
    return isinstance(stream_id, str) and bool(_ID_RE.match(stream_id))


class SttStreamSession:
    """One turn's Moonshine stream. ``feed`` never blocks on the model; a worker thread drains the queue."""

    def __init__(self, stream_id: str, make_stream: Callable[[], object], infer_lock: threading.Lock):
        self.stream_id = stream_id
        self._stream = make_stream()
        self._lock = infer_lock
        self._q: queue.Queue = queue.Queue()
        self.next_seq = 0
        self.samples_fed = 0
        self.broken: Optional[str] = None
        self.touched = time.monotonic()
        self._done = threading.Event()
        self._lines: Optional[list[str]] = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"stt-stream-{stream_id[:8]}")
        self._thread.start()

    # -- worker -----------------------------------------------------------------------------------------------
    def _run(self) -> None:
        import numpy as np

        try:
            while True:
                item = self._q.get()
                if item is None:  # finish
                    with self._lock:
                        res = self._stream.stop()
                    self._lines = [getattr(ln, "text", "") or "" for ln in getattr(res, "lines", [])] if res else []
                    return
                if item is False:  # abort
                    return
                audio = (np.frombuffer(item, dtype="<i2").astype(np.float32) / 32768.0).tolist()
                with self._lock:
                    self._stream.add_audio(audio, SAMPLE_RATE)
        except Exception as exc:  # engine failure: the turn falls back to batch STT
            self.broken = f"engine:{exc.__class__.__name__}"
            logger.warning("STT_STREAM worker failed (%s) - turn will use batch STT", exc)
        finally:
            self._close_stream()
            self._done.set()

    def _close_stream(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        except Exception:
            pass

    # -- API --------------------------------------------------------------------------------------------------
    def feed(self, seq: int, pcm: bytes) -> bool:
        """Queue one PCM16-LE chunk. Out-of-order / oversize / odd-length input breaks the session (batch fallback)."""
        self.touched = time.monotonic()
        if self.broken or self._done.is_set():
            return False
        if seq != self.next_seq or len(pcm) % 2 or self.samples_fed + len(pcm) // 2 > MAX_SAMPLES:
            self.broken = "bad_chunk"
            self._q.put(False)
            return False
        self.next_seq += 1
        self.samples_fed += len(pcm) // 2
        self._q.put(pcm)
        return True

    def finish(self, expected_samples: int, timeout_s: float) -> tuple[Optional[list[str]], str]:
        """(lines, reason). lines is None -> caller must use batch STT; reason says why (or 'ok')."""
        if self.broken:
            return None, self.broken
        if expected_samples != self.samples_fed:
            self.abort()
            return None, "sample_mismatch"
        self._q.put(None)
        if not self._done.wait(timeout_s):
            self.broken = "timeout"
            return None, "timeout"
        if self.broken or self._lines is None:
            return None, self.broken or "no_result"
        return self._lines, "ok"

    def abort(self) -> None:
        if not self._done.is_set():
            self._q.put(False)


_sessions: dict[str, SttStreamSession] = {}
_registry_lock = threading.Lock()


def _reap_locked(now: float) -> None:
    for sid, sess in list(_sessions.items()):
        if now - sess.touched > IDLE_TTL_S or sess._done.is_set():
            _sessions.pop(sid, None)
            sess.abort()


def get_or_open(stream_id: str, seq: int, make_stream: Callable[[], object],
                infer_lock: threading.Lock) -> Optional[SttStreamSession]:
    """Existing session, or a new one iff ``seq == 0`` and the bound allows. None -> refuse (daemon latches off)."""
    now = time.monotonic()
    with _registry_lock:
        _reap_locked(now)
        sess = _sessions.get(stream_id)
        if sess is not None:
            return sess
        if seq != 0 or len(_sessions) >= MAX_SESSIONS:
            return None
        sess = SttStreamSession(stream_id, make_stream, infer_lock)
        _sessions[stream_id] = sess
        return sess


def take(stream_id: str) -> Optional[SttStreamSession]:
    """Remove and return a session (a stream id is single-use: the turn consumes it)."""
    with _registry_lock:
        return _sessions.pop(stream_id, None)


def wav_sample_count(raw: bytes) -> Optional[int]:
    """Frames in a 16 kHz mono PCM16 WAV, or None for anything else (then the stream is not trusted)."""
    import io
    import wave

    try:
        with wave.open(io.BytesIO(raw), "rb") as w:
            if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            return w.getnframes()
    except Exception:
        return None
