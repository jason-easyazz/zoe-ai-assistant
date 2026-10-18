"""STT under speech: the Moonshine half of "prefill under speech" (flag-dark ``ZOE_STT_STREAM_UNDER_SPEECH``).

The panel daemon uploads mic audio WHILE the user speaks; a session feeds a Moonshine stream on its own worker thread and
``/turn_stream`` takes the finished transcript instead of transcribing the clip afterwards. Numbers, enable/rollback:
docs/knowledge/prefill-under-speech-2026-10-10.md. Safety: the WAV is still POSTed and a stream result is used ONLY when
its sample count equals the WAV's and the text is non-empty (gap, mismatch, timeout, empty, engine error -> batch STT);
sessions are bounded (count, audio length, idle TTL).
"""
from __future__ import annotations

import contextlib
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
# The longest ONE recording the panel daemon sends: a conversation window's hard cap (CONV_RECORD_SECONDS_HARD_MAX,
# default 60 s) plus the snap-to-a-pause tail (RECORD_SNAP_S, default 3 s) = 63 s, rounded up. It was 30 s, so every
# 31-63 s conversation turn broke its stream and silently paid full post-speech batch STT (A, 2026-10-18). Measured on
# the Orin (MEDIUM_STREAMING, real corpus speech, 0.32 s batches): once the allocator is warm a 63 s session adds ~8 MB
# RSS over a 30 s one (first ever 63 s session: high-water +142 MB vs +89 MB for 30 s, then reused); feeding 63 s costs
# ~28 s of CPU spread under the speech, stop() 0.11 s. MAX_SESSIONS stays 2. Pinned against the daemon's defaults by
# tests/test_voice_stt_stream.py.
MAX_TURN_AUDIO_S = 64
MAX_SAMPLES = MAX_TURN_AUDIO_S * SAMPLE_RATE
IDLE_TTL_S = 30.0
MAX_QUEUE = 512  # queued, not-yet-fed chunks; the daemon sends ~0.32 s chunks, so this is minutes of backlog
OPEN_WAIT_S = 10.0  # a concurrent chunk for an id whose stream is still being created waits this long
STALL_LOCK_WAIT_S = 5.0  # batch STT's wait behind a stalled stream's worker before giving up


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
                try:
                    item = self._q.get(timeout=IDLE_TTL_S)
                except queue.Empty:
                    if time.monotonic() - self.touched < IDLE_TTL_S:
                        continue
                    self.broken = "idle"  # abandoned: no feed/finish within the TTL -> release the model stream
                    _deregister(self)
                    return
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
            _stalled.discard(self)
            self._close_stream()
            with _registry_lock:
                _live.discard(self)  # only now does the session stop counting against MAX_SESSIONS
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
        if not pcm:  # an empty chunk carries no audio: it must not advance seq, refresh idle or bypass the bounds
            return False
        self.touched = time.monotonic()
        if self.broken or self._done.is_set():
            return False
        if (seq != self.next_seq or len(pcm) % 2 or self.samples_fed + len(pcm) // 2 > MAX_SAMPLES
                or self._q.qsize() >= MAX_QUEUE):
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
            _stalled.add(self)  # the worker may still hold the shared infer lock inside stop(); batch STT must not wait forever
            return None, "timeout"
        if self.broken or self._lines is None:
            return None, self.broken or "no_result"
        return self._lines, "ok"

    def abort(self) -> None:
        if not self._done.is_set():
            self._q.put(False)


_sessions: dict[str, SttStreamSession] = {}
_pending: dict[str, threading.Event] = {}  # ids whose stream is being created OUTSIDE the registry lock
_stalled: set = set()  # sessions that timed out in finish() and whose worker is still running
# Every session whose worker thread has not exited, registered or already taken by a turn: a consumed (or reaped)
# session keeps its Moonshine stream and thread until the worker closes, so the bound counts it until then.
_live: set = set()
_registry_lock = threading.Lock()


def _deregister(sess: SttStreamSession) -> None:
    with _registry_lock:
        if _sessions.get(sess.stream_id) is sess:
            _sessions.pop(sess.stream_id, None)


def _reap_locked(now: float) -> None:
    for sid, sess in list(_sessions.items()):
        if now - sess.touched > IDLE_TTL_S or sess._done.is_set():
            _sessions.pop(sid, None)
            sess.abort()


def get_or_open(stream_id: str, seq: int, make_stream: Callable[[], object],
                infer_lock: threading.Lock) -> Optional[SttStreamSession]:
    """Existing session, or a new one iff ``seq == 0`` and the bound allows. None -> refuse (daemon latches off).

    The (slow) model stream is created OUTSIDE ``_registry_lock``: the slot is reserved under the lock, the stream built
    without it, then published (or the reservation dropped on failure) - so ``take()`` never waits on a model load.
    """
    now = time.monotonic()
    with _registry_lock:
        _reap_locked(now)
        sess = _sessions.get(stream_id)
        if sess is not None:
            return sess
        ev = _pending.get(stream_id)
        owner = ev is None
        if owner:
            if seq != 0 or len(_live | set(_sessions.values())) + len(_pending) >= MAX_SESSIONS:
                return None
            ev = _pending[stream_id] = threading.Event()
    if not owner:  # another chunk of this id is mid-creation: wait for it (outside the lock), bounded
        ev.wait(OPEN_WAIT_S)
        with _registry_lock:
            return _sessions.get(stream_id)
    sess = None
    try:
        sess = SttStreamSession(stream_id, make_stream, infer_lock)
        return sess
    finally:
        with _registry_lock:
            _pending.pop(stream_id, None)
            if sess is not None:
                _sessions[stream_id] = sess
                if not sess._done.is_set():
                    _live.add(sess)
        ev.set()


@contextlib.contextmanager
def infer_guard(lock: threading.Lock):
    """Batch STT's acquire of the shared Moonshine lock. Identical to ``with lock`` unless a stream timed out in
    finish() and its worker is still inside stop() holding the lock: then the wait is bounded (``TimeoutError``)."""
    stall_seen = None
    while not lock.acquire(timeout=0.25):
        if _stalled:
            stall_seen = stall_seen or time.monotonic()
            if time.monotonic() - stall_seen >= STALL_LOCK_WAIT_S:
                raise TimeoutError("moonshine infer lock held by a stalled STT stream")
        else:
            stall_seen = None
    try:
        yield
    finally:
        lock.release()


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
