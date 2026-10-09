"""First-sound policy for brain turns: what leaves the stream loop first, and when (shared by ``routers/voice_tts.py``,
``scripts/perf/measure_first_sound.py`` and the tests; numbers and the ear-check caveat: docs/knowledge/first-sound-latency-2026-10-09.md).
Both flags default OFF:

* ``ZOE_FIRST_SOUND_CLAUSE``    cut the first spoken unit at the first CLAUSE boundary (`,;:` or a dash, then a space) once it
                                has ``..._CLAUSE_MIN_CHARS`` (24) characters and ``..._CLAUSE_MIN_WORDS`` (4) words; never after a
                                number, in an abbreviation or initial, or inside a quote or bracket. Also switches
                                ``ZOE_FIRST_SOUND_NARRATION_EARLY`` (narration_filter.py), without which the cut cannot fire.
                                Each unit is a standalone Kokoro utterance (a pitch reset): ear-check before enabling.
* ``ZOE_FIRST_SOUND_TOOL_ACK``  on a brain turn the router calls tool-class that the tiers did not fulfil, speak ONE cached line
                                when the brain is dispatched, not when the sidecar's tool-start sentinel finally arrives.
"""
from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator, Optional

from typed_env import env_bool, env_int


def clause_enabled() -> bool:
    return env_bool("ZOE_FIRST_SOUND_CLAUSE", default=False)


def tool_ack_enabled() -> bool:
    return env_bool("ZOE_FIRST_SOUND_TOOL_ACK", default=False)

# ── first-clause extraction ──
# A word whose trailing period/comma belongs to the word, not to the clause or sentence.
_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "ft", "vs", "etc", "e.g",
                            "i.e", "approx", "inc", "ltd", "dept", "a.m", "p.m"})


def _word_before(buffer: str, idx: int) -> str:
    j = idx
    while j > 0 and not buffer[j - 1].isspace():
        j -= 1
    return buffer[j:idx].strip("\"'“”‘’()[]")


def _is_abbreviation(word: str) -> bool:
    w = word.lower().rstrip(".")
    return bool(w) and (w in _ABBREVIATIONS or (len(w) == 1 and w.isalpha()) or ("." in w and len(w) <= 5))


def _balanced(prefix: str) -> bool:
    return (prefix.count('"') % 2 == 0 and prefix.count("“") == prefix.count("”")
            and prefix.count("(") <= prefix.count(")") and prefix.count("[") <= prefix.count("]"))


def extract_first_clause(buffer: str, *, min_chars: Optional[int] = None,
                         min_words: Optional[int] = None) -> tuple[Optional[str], str]:
    """Cut the first CLAUSE out of a streaming buffer, or ``(None, buffer)``.

    A boundary is a clause mark FOLLOWED BY WHITESPACE (so a mark at the end of a still-streaming buffer, or inside
    "8:05" / "12,000" / "3.5", never matches). The text through the mark needs ``min_chars`` and ``min_words``, must
    not end on a word containing a digit ("on the 3rd, 4th"), an abbreviation or an initial, and must have balanced
    quotes and brackets. The clause keeps its mark, so Kokoro keeps the continuation contour."""
    min_chars = max(12, env_int("ZOE_FIRST_SOUND_CLAUSE_MIN_CHARS", default=24)) if min_chars is None else min_chars
    min_words = max(2, env_int("ZOE_FIRST_SOUND_CLAUSE_MIN_WORDS", default=4)) if min_words is None else min_words
    for m in re.finditer(r"([,;:—–])(\s)", buffer):
        prefix = buffer[:m.end(1)].strip()
        if len(prefix) < min_chars or len(prefix.split()) < min_words:
            continue
        word = _word_before(buffer, m.start(1))
        if any(ch.isdigit() for ch in word) or _is_abbreviation(word) or not _balanced(prefix):
            continue
        return prefix, buffer[m.end(2):]
    return None, buffer


# ── the tool-turn acknowledgement ──
# Router domains whose answer needs a tool round before the brain can speak. 'chat' is absent on purpose: a chat
# turn gets no acknowledgement (the blueprint's control: acknowledge-always must fail).
_TOOL_ACK_LINES = {
    "calendar": "Let me check your calendar.", "lists": "Let me check your lists.",
    "reminders": "Let me check your reminders.", "weather": "Let me check the weather.",
    "memory": "Let me think.", "timers": "One sec.", "people": "One sec.", "notes": "One sec.",
    "journal": "One sec.", "music": "One sec.", "smart_home": "One sec.", "time": "One sec.",
}


def dispatch_ack(router_decision: Optional[dict], *, audio_started: bool = False, filler_emitted: bool = False,
                 processing_ack_sent: bool = False) -> Optional[str]:
    """The stream loop's one decision point: the line to speak at brain dispatch, or None (flag off, chat or unknown
    router domain, or sound already started: audio, the tool filler, the cached processing-ack)."""
    if audio_started or filler_emitted or processing_ack_sent or not tool_ack_enabled():
        return None
    if not isinstance(router_decision, dict):
        return None
    return _TOOL_ACK_LINES.get(str(router_decision.get("routed") or "").strip().lower())


# ── start the brain request before the acknowledgement is synthesized ──
class Prefetched:
    """An async iterator over ``stream`` whose FIRST pull starts at construction (needs a running loop), so the
    brain request is in flight while the acknowledgement is synthesized instead of waiting for the first iteration.
    ``close_nowait`` never suspends: it cancels a pending first pull and schedules the stream's ``aclose`` (which
    aborts the sidecar turn, like closing a plain ``async for`` over it)."""

    def __init__(self, stream: AsyncIterator[str]) -> None:
        self._it = stream.__aiter__()
        self._first: "Optional[asyncio.Future]" = asyncio.ensure_future(self._it.__anext__())
        self._closed = False

    def __aiter__(self) -> "Prefetched":
        return self

    async def __anext__(self) -> str:
        if self._first is not None:
            first, self._first = self._first, None
            return await first
        return await self._it.__anext__()

    async def aclose(self) -> None:
        """Awaitable close for a caller that can suspend: cancel a pending first pull, wait for it to unwind, then ``aclose`` the stream
        (so nothing is left running behind a returned call). Best effort - never raises; idempotent with ``close_nowait``."""
        if self._closed:
            return
        self._closed = True
        first, self._first = self._first, None
        if first is not None:
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        aclose = getattr(self._it, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

    def close_nowait(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._first is not None and not self._first.done():
            self._first.cancel()
        aclose = getattr(self._it, "aclose", None)
        if aclose is not None:
            try:
                asyncio.ensure_future(aclose())
            except Exception:  # noqa: BLE001 - closing is best effort
                pass


def prefetch(stream: AsyncIterator[str]) -> Prefetched:
    return Prefetched(stream)
