"""Reset Moonshine's process-global Silero VAD recurrent state between batch STT calls.

WHY. moonshine-voice 0.1.3 (and 0.1.5, byte-identical here) segments every clip with ONE process-global Silero
instance: ``VoiceActivityDetector::silero_vad`` (core/voice-activity-detector.cpp L22, created once at L35). Its
LSTM ``_state`` and 64-sample ``_context`` (core/silero-vad.cpp L48-49, advanced at L158/L161) are never cleared
between ``transcribe_without_streaming`` calls -- ``VoiceActivityDetector::start()`` resets only the per-detector
buffers, and the library exposes no reset. A clip's segmentation (and so its transcript, or an empty one) therefore
depends on whatever audio the process heard before it: measured, the same clip yielded up to 12 distinct
segmentations / 5 distinct transcripts across 19 different predecessors, and identical segmentations always decoded
identically (so the model is stateless; only the VAD carries history). Feeding silence or noise first does NOT
converge (8 s of zeros still left 5 distinct outcomes), so the only way to a clean state is to clear it.

HOW. The Silero object's address is the exported data symbol ``_ZN21VoiceActivityDetector10silero_vadE``. Only for
the library versions whose layout was measured (``_SUPPORTED_VERSIONS``; silero-vad.{h,cpp} are byte-identical in
0.1.3 and 0.1.5) do we read the object's head and require ``std::vector<float>`` (begin, end, cap) triples of
exactly 64 floats at byte 48 (``_context``), 576 at 136 (``input``) and 256 at 168 (``_state``) - the offsets
measured on the live 0.1.3 build. Only if all three match is anything written (the first two zeroed). Any other
version or layout is a harmless no-op returning False, so a library bump can at worst lose the fix. Callers must
hold the Moonshine infer lock.
"""
from __future__ import annotations

import ctypes
import logging
import os
import struct
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SYMBOL = "_ZN21VoiceActivityDetector10silero_vadE"
_SCAN_BYTES = 216  # head of SileroVad; sizeof is larger, so the read stays in-object (on supported versions)
_SUPPORTED_VERSIONS = frozenset({"0.1.3", "0.1.5"})
# (byte offset, float count): _context, input (per-call scratch, checked only to confirm the layout), _state
_LAYOUT_CONTEXT = (48, 64)
_LAYOUT_INPUT = (136, 576)
_LAYOUT_STATE = (168, 256)

_warned = False


def _safe_read(addr: int, size: int) -> bytes:
    """Read ``size`` bytes at ``addr`` of this process through /proc/self/mem: an unmapped/unreadable address raises
    OSError instead of faulting (ctypes.string_at would SIGSEGV, which no ``except`` can catch)."""
    fd = os.open("/proc/self/mem", os.O_RDONLY)
    try:
        data = os.pread(fd, size, addr)
    finally:
        os.close(fd)
    if len(data) != size:
        raise OSError(f"short read at object+{size}")
    return data


def _safe_write(addr: int, data: bytes) -> None:
    """Write ``data`` at ``addr`` through /proc/self/mem (fault-safe: an unwritable address raises OSError instead
    of killing the process the way ``ctypes.memset`` would)."""
    fd = os.open("/proc/self/mem", os.O_WRONLY)
    try:
        if os.pwrite(fd, data, addr) != len(data):
            raise OSError("short write")
    finally:
        os.close(fd)


def _supported_version() -> bool:
    try:
        import moonshine_voice

        return getattr(moonshine_voice, "__version__", None) in _SUPPORTED_VERSIONS
    except Exception:
        return False


def _vector_at(words: tuple, offset: int, floats: int) -> int:
    """Begin address of the ``std::vector<float>`` at byte ``offset`` iff it holds exactly ``floats`` floats."""
    begin, end, cap = words[offset // 8 : offset // 8 + 3]
    if (not begin or begin % 4 or end % 4 or cap % 4 or not (begin <= end <= cap)
            or end - begin != floats * 4):
        raise RuntimeError(f"unexpected Silero layout at +{offset}")
    return begin


def _object_address(lib: Any) -> int:
    return ctypes.c_void_p.in_dll(lib, _SYMBOL).value or 0


def reset_silero_state(lib: Any, path: str = "batch") -> bool:
    """Zero the Silero ``_state`` + ``_context`` inside the already-loaded libmoonshine ``lib`` (ctypes CDLL).

    True when both were cleared; False when there is no Silero yet or the layout is not the one validated, with
    state and context both left unchanged (all-or-nothing: both targets are proven writable by a no-op write first,
    and a failure mid-way restores the original bytes of whatever was already zeroed). ``path`` ("batch" or
    "stream") only labels the warning. Never raises."""
    global _warned
    try:
        if not _supported_version():
            raise RuntimeError("unvalidated moonshine-voice version")
        obj = _object_address(lib)
        if not obj:
            return False  # no VAD constructed yet -> state is still the pristine zeros
        words = struct.unpack(f"<{_SCAN_BYTES // 8}Q", _safe_read(obj, _SCAN_BYTES))
        ctx = _vector_at(words, *_LAYOUT_CONTEXT)
        _vector_at(words, *_LAYOUT_INPUT)
        state = _vector_at(words, *_LAYOUT_STATE)
        targets = [(state, _LAYOUT_STATE[1] * 4), (ctx, _LAYOUT_CONTEXT[1] * 4)]
        saved = [(addr, _safe_read(addr, size)) for addr, size in targets]  # both mapped + readable
        for addr, orig in saved:  # both writable, proven before anything changes (writes the bytes already there)
            _safe_write(addr, orig)
        try:
            for addr, orig in saved:
                _safe_write(addr, bytes(len(orig)))
        except Exception:
            failed = 0
            for addr, orig in saved:  # roll back whatever was zeroed: never one zeroed and one stale
                for _attempt in range(3):  # both targets were proven writable, so a retry is cheap insurance
                    try:
                        _safe_write(addr, orig)
                        break
                    except Exception:
                        pass
                else:
                    failed += 1
            if failed:  # cannot be made impossible (the memory itself went unwritable): say so loudly, not silently
                logger.error("Moonshine VAD state rollback FAILED for %d of %d targets - Silero state may be "
                             "partially reset", failed, len(saved))
            raise
        return True
    except Exception as exc:  # absent symbol, other build, unreadable object -> fix lost, never a crash
        if not _warned:
            _warned = True
            logger.warning("Moonshine VAD state reset unavailable on the %s STT path (%s) - STT keeps its carried "
                           "VAD state", path, exc)
        return False


def transcriber_lib(transcriber: Any) -> Optional[Any]:
    """The loaded libmoonshine CDLL behind a ``moonshine_voice`` Transcriber (None for fakes/other builds)."""
    return getattr(transcriber, "_lib", None)
