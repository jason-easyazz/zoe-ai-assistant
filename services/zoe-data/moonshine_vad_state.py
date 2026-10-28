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

HOW. The Silero object's address is the exported data symbol ``_ZN21VoiceActivityDetector10silero_vadE``. We read
the first bytes of the object, find the two ``std::vector<float>`` members by their exact shape (begin/end/cap
pointers spanning 256 floats for ``_state`` and 64 floats for ``_context``) and zero them -- the same values a fresh
process starts with. Nothing is written unless BOTH vectors are found exactly once; anything unexpected (other
library build, symbol absent, no VAD created yet) is a harmless no-op that returns False, so a library bump can at
worst lose the fix, never corrupt memory. Callers must hold the Moonshine infer lock.
"""
from __future__ import annotations

import ctypes
import logging
import struct
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SYMBOL = "_ZN21VoiceActivityDetector10silero_vadE"
_SCAN_BYTES = 216  # SileroVad members before the first non-vector tail; sizeof(SileroVad) is larger, so reads stay in-object
_STATE_FLOATS = 2 * 1 * 128  # silero-vad.h ``size_state``
_CONTEXT_FLOATS = 64  # silero-vad.h ``context_samples`` at 16 kHz

_warned = False


def _vector_spans(obj_addr: int) -> list[tuple[int, int]]:
    """(begin_address, byte_length) of every plausible ``std::vector<float>`` (begin, end, cap) triple in the head of
    the object. Pure read of in-object bytes."""
    raw = ctypes.string_at(obj_addr, _SCAN_BYTES)
    words = struct.unpack(f"<{len(raw) // 8}Q", raw)
    spans = []
    for i in range(len(words) - 2):
        begin, end, cap = words[i], words[i + 1], words[i + 2]
        if begin and begin % 4 == 0 and begin <= end <= cap:
            spans.append((begin, end - begin))
    return spans


def _object_address(lib: Any) -> int:
    return ctypes.c_void_p.in_dll(lib, _SYMBOL).value or 0


def reset_silero_state(lib: Any) -> bool:
    """Zero the Silero ``_state`` + ``_context`` inside the already-loaded libmoonshine ``lib`` (ctypes CDLL).

    True when both were cleared; False (and nothing written) when there is no Silero yet or the layout is not the one
    validated. Never raises."""
    global _warned
    try:
        obj = _object_address(lib)
        if not obj:
            return False  # no VAD constructed yet -> state is still the pristine zeros
        spans = _vector_spans(obj)
        states = [a for a, n in spans if n == _STATE_FLOATS * 4]
        ctxs = [a for a, n in spans if n == _CONTEXT_FLOATS * 4]
        if len(states) != 1 or len(ctxs) != 1:
            raise RuntimeError(f"unexpected Silero layout: state matches={len(states)} context matches={len(ctxs)}")
        ctypes.memset(states[0], 0, _STATE_FLOATS * 4)
        ctypes.memset(ctxs[0], 0, _CONTEXT_FLOATS * 4)
        return True
    except Exception as exc:  # absent symbol, other build, unreadable object -> fix lost, never a crash
        if not _warned:
            _warned = True
            logger.warning("Moonshine VAD state reset unavailable (%s) - batch STT keeps its carried VAD state", exc)
        return False


def transcriber_lib(transcriber: Any) -> Optional[Any]:
    """The loaded libmoonshine CDLL behind a ``moonshine_voice`` Transcriber (None for fakes/other builds)."""
    return getattr(transcriber, "_lib", None)
