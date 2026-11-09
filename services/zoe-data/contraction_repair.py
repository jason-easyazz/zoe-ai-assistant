"""Put a spliced contraction back together - ``ZOE_CONTRACTION_REPAIR`` (default on; ``0|false|no|off`` is the kill switch).

THE GLITCH (QA round 5, 2026-11-08: 6 replies in 297 turns; first measured in ``capped-completions.ts`` ``brainTemperature``):
the brain decodes with MTP speculative drafting, and at the fork "I'm" / "I don't" a draft token is accepted that the target
would not have chosen, so a stray `` don`` lands in front of the real token: ``I'm`` -> ``I don'm``, ``you're`` -> ``you don're``,
``I've`` -> ``I don've``, ``I'd`` -> ``I don'd``. The text is already wrong when it leaves llama-server (nothing in zoe-data or the
sidecar rewrites it - the whole reply path was read for a transform and none exists), so the engine-side lever is the sampling
temperature the sidecar already pins (0.5, which took the rate from ~3.5% to ~0 in the lab) and THIS is the deterministic net for the
turns that still splice: a pronoun, a negative-auxiliary stem, an apostrophe and a SUBJECT-contraction ending (m, re, ve, d, ll) is
never English - negative contractions end in ``t`` - so the stem is dropped. ``don't``, ``can't``, ``I'm`` and every other valid
form are untouched by construction (``tests/test_contraction_repair.py`` holds the negative controls).

Streaming: a delta that ENDS in what could still become the pattern (``I don``, ``I don'``, ``I don'm``) is held until the next delta
decides it, at most one token; every other delta passes at once. ``RepairStream.feed/finish`` equals ``repair`` of the whole reply
for every way of cutting it into deltas. Tool / thinking sentinels pass at once. Stdlib only; never raises into a turn.
"""
from __future__ import annotations

import logging
import re
from typing import AsyncIterator

from typed_env import env_bool

logger = logging.getLogger(__name__)

_SENTINELS = ("__TOOL__:", "__THINKING__:")
_PRON = r"(?:I|you|we|they|he|she|it|that|who|what|there|here|let)"
_STEM = r"(?:don|doesn|didn|can|couldn|won|wouldn|shouldn|isn|aren|wasn|weren|haven|hasn|hadn)"
_APOS = "['’]"
_REPAIR_RE = re.compile(r"\b(" + _PRON + r")\s+" + _STEM + r"(" + _APOS + r"(?:m|re|ve|d|ll))\b", re.IGNORECASE)
_PRON_WORDS = ("I", "you", "we", "they", "he", "she", "it", "that", "who", "what", "there", "here", "let")
_STEM_WORDS = ("don", "doesn", "didn", "can", "couldn", "won", "wouldn", "shouldn", "isn", "aren", "wasn", "weren", "haven", "hasn", "hadn")


def _prefixes(words: tuple) -> str:
    """Regex for every non-empty prefix (the whole word included) of any of ``words``."""
    return "(?:" + "|".join(sorted({re.escape(w[:i]) for w in words for i in range(1, len(w) + 1)}, key=len, reverse=True)) + ")"


# The tail of a buffer that could still grow into the pattern, however the deltas cut it: a started pronoun, a whole pronoun (+ space),
# pronoun + a started stem, pronoun + stem + an apostrophe + a started suffix.
_HOLD_RE = re.compile(
    r"\b(?:" + _prefixes(_PRON_WORDS) + r"$"
    r"|" + _PRON + r"\s+" + r"(?:" + _prefixes(_STEM_WORDS) + r")?$"
    r"|" + _PRON + r"\s+" + _STEM + r"(?:" + _APOS + r"(?:m|re?|ve?|d|ll?)?)?$)", re.IGNORECASE)


def enabled() -> bool:
    return env_bool("ZOE_CONTRACTION_REPAIR", True)  # literal on purpose: tools/audit/flag_inventory.py greps it


def repair(text: str) -> str:
    """``text`` with every spliced contraction restored (``I don'm`` -> ``I'm``). Pure; the same object back when nothing matched."""
    if not text or ("'" not in text and "’" not in text):
        return text
    return _REPAIR_RE.sub(lambda m: m.group(1) + m.group(2), text)


def repair_text(text: str) -> str:
    """The one-shot form for a finished reply: flag-gated, logs a label-only line, never raises."""
    try:
        if not enabled():
            return text
        out = repair(text)
        if out != text:
            logger.warning("CONTRACTION_REPAIRED n=%d", len(_REPAIR_RE.findall(text)))
        return out
    except Exception:  # noqa: BLE001
        return text


class RepairStream:
    """Incremental form of ``repair``. ``feed(delta)`` returns the text safe to release now, ``finish()`` the rest."""

    def __init__(self) -> None:
        self._pending = ""
        self.repaired = 0

    def _release(self, text: str) -> str:
        out, n = _REPAIR_RE.subn(lambda m: m.group(1) + m.group(2), text)
        self.repaired += n
        return out

    def feed(self, delta: str) -> str:
        text = self._pending + delta
        m = _HOLD_RE.search(text)
        if m is None:
            self._pending = ""
            return self._release(text)
        self._pending = text[m.start():]
        return self._release(text[:m.start()])

    def finish(self) -> str:
        text, self._pending = self._pending, ""
        return self._release(text)


async def filter_stream(inner: AsyncIterator[str]) -> AsyncIterator[str]:
    """Repair a reply stream. Flag off: the stream untouched. A mid-stream failure releases what is held, then re-raises."""
    if not enabled():
        async for delta in inner:
            yield delta
        return
    rs = RepairStream()
    try:
        async for delta in inner:
            if delta.startswith(_SENTINELS):
                tail = rs.finish()
                if tail:
                    yield tail
                yield delta
                continue
            out = rs.feed(delta)
            if out:
                yield out
        tail = rs.finish()
        if tail:
            yield tail
    except Exception:
        tail = rs.finish()
        if tail:
            yield tail
        raise
    finally:
        if rs.repaired:
            logger.warning("CONTRACTION_REPAIRED n=%d", rs.repaired)
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            await aclose()
