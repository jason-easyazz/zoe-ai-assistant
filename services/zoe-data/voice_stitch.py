"""Segment-stitched TTS for high-frequency templated voice replies.

TTS (Kokoro) renders at ~1x realtime, so a fresh "It's 14 degrees and clear in
Geraldton" costs ~2s — and it misses the sidecar's exact-text cache on every new
value (temp / condition / minute). This module renders such replies by
concatenating a small set of **cached, finite-vocabulary phrase segments** instead
of synthesizing the whole novel sentence:

    weather → ["It's fourteen degrees", "Clear", "in Geraldton"]
    time    → ["It's seven", "forty two", "PM"]

Each segment is a natural phrase (good prosody within a segment) drawn from a
bounded vocabulary (0–100, weather conditions, hours, minutes, am/pm), so after a
one-time prewarm every segment is a sidecar cache hit (~2ms) and the reply is
assembled by joining WAV PCM — no synthesis on the turn. Flag-gated
(``ZOE_VOICE_STITCH_ENABLED``, default OFF); any gap in the vocabulary or a synth
failure returns None so the caller falls back to normal full-sentence TTS.

WAV assumption: Kokoro emits mono 16-bit PCM at 24 kHz. :func:`concat_wavs`
verifies each segment matches before joining and refuses to stitch a mismatch.
"""
from __future__ import annotations

import io
import logging
import os
import re
import wave
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Silence inserted between segments so the phrases don't butt together — natural
# micro-pause, not a full sentence gap.
_GAP_MS = 70

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]

# Weather condition phrases we cache/stitch. Anything outside this set → fall back
# to full-sentence TTS (never guess a spoken form we didn't prewarm).
WEATHER_CONDITIONS = [
    "clear", "sunny", "mostly clear", "partly cloudy", "cloudy", "overcast",
    "light rain", "rain", "showers", "drizzle", "thunderstorms", "fog",
    "mist", "windy", "humid", "hot", "cold",
]

# Same var the weather router + MCP use for the home city, so the stitched
# "in {city}" segment matches the spoken reply's city and shares its cache entry.
_CITY = os.environ.get("ZOE_LOCATION_CITY", "Geraldton")

# Canonical Kokoro WAV format. Segments must match this (not just each other) or
# concat refuses — a consistently-wrong rate would otherwise play at wrong speed.
_WAV_CHANNELS, _WAV_SAMPWIDTH, _WAV_RATE = 1, 2, 24000


def enabled() -> bool:
    return os.environ.get("ZOE_VOICE_STITCH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def number_to_words(n: int) -> Optional[str]:
    """Spoken form of an integer 0–100 (the range we cache). None if out of range."""
    if not isinstance(n, int) or n < 0 or n > 100:
        return None
    if n < 20:
        return _ONES[n]
    if n == 100:
        return "one hundred"
    tens, ones = divmod(n, 10)
    return _TENS[tens] if ones == 0 else f"{_TENS[tens]} {_ONES[ones]}"


# ── WAV joining ───────────────────────────────────────────────────────────────

def _silence_pcm(framerate: int, sampwidth: int, channels: int, ms: int) -> bytes:
    return b"\x00" * (int(framerate * ms / 1000) * sampwidth * channels)


def concat_wavs(wavs: list[bytes], *, gap_ms: int = _GAP_MS) -> Optional[bytes]:
    """Join same-format WAV blobs into one, with a short silence between each.

    Returns None (caller falls back) if the list is empty or any blob's format
    differs from the first — we never emit a corrupt/again-clicky stream.
    """
    if not wavs:
        return None
    expected = (_WAV_CHANNELS, _WAV_SAMPWIDTH, _WAV_RATE)
    pcm_parts: list[bytes] = []
    for blob in wavs:
        try:
            with wave.open(io.BytesIO(blob), "rb") as w:
                p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                frames = w.readframes(w.getnframes())
        except Exception as exc:
            logger.debug("voice_stitch: unreadable WAV segment (%s)", exc)
            return None
        # Must match the CANONICAL format, not merely be self-consistent — a
        # uniformly-wrong rate would still concat cleanly but play at wrong speed.
        if p != expected:
            logger.debug("voice_stitch: segment format %s != canonical %s, refusing", p, expected)
            return None
        pcm_parts.append(frames)
    channels, sampwidth, framerate = expected
    gap = _silence_pcm(framerate, sampwidth, channels, gap_ms) if gap_ms > 0 else b""
    body = gap.join(pcm_parts)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(body)
    return out.getvalue()


# ── Segment templates (text only — pure, testable) ────────────────────────────

def weather_segments(temp_c: int, condition: str, city: Optional[str] = None) -> Optional[list[str]]:
    """Ordered phrase segments for a weather reply, or None if the temp is out of range.

    Temperature (0–100) and city are the guaranteed-finite, pre-warmed segments. The
    description is provider-driven free text (WMO-mapped, ~28 values in practice), so
    it is passed through as its own segment rather than gated against a fixed list —
    a novel description just synthesises once and is cached thereafter (`WEATHER_CONDITIONS`
    seeds the pre-warm for the common ones). An empty description is simply omitted.
    """
    if temp_c is None:
        return None
    tw = number_to_words(int(round(temp_c)))
    if tw is None:  # outside the 0–100 vocab
        return None
    city = city or _CITY
    segs = [f"It's {tw} degrees"]
    cond = (condition or "").strip()
    if cond:
        segs.append(cond[:1].upper() + cond[1:])
    segs.append(f"in {city}")
    return segs


def time_segments(hour_24: int, minute: int) -> Optional[list[str]]:
    """Ordered phrase segments for a spoken clock time, or None if out of range."""
    if not (0 <= hour_24 <= 23 and 0 <= minute <= 59):
        return None
    ampm = "AM" if hour_24 < 12 else "PM"
    h12 = hour_24 % 12 or 12
    hw = number_to_words(h12)
    if minute == 0:
        return [f"It's {hw} o'clock", ampm]
    mw = number_to_words(minute)
    # "oh five" reads more naturally than "five" for single-digit minutes.
    if minute < 10:
        mw = f"oh {number_to_words(minute)}"
    return [f"It's {hw}", mw, ampm]


def vocabulary(city: Optional[str] = None) -> list[str]:
    """Every segment string this module can emit — the prewarm set (bounded)."""
    city = city or _CITY
    segs: list[str] = []
    for n in range(0, 101):
        segs.append(f"It's {number_to_words(n)} degrees")
    segs += [c.capitalize() for c in WEATHER_CONDITIONS]
    segs.append(f"in {city}")
    for h in range(1, 13):
        segs.append(f"It's {number_to_words(h)}")
        segs.append(f"It's {number_to_words(h)} o'clock")
    for m in range(1, 60):
        segs.append(f"oh {number_to_words(m)}" if m < 10 else number_to_words(m))
    segs += ["AM", "PM"]
    # de-dup, keep order
    seen: set[str] = set()
    out: list[str] = []
    for s in segs:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ── Stitch driver (async; injects the synth fn so it's testable offline) ──────

async def stitch(
    segments: list[str],
    synth: Callable[[str], Awaitable[Optional[bytes]]],
    *,
    gap_ms: int = _GAP_MS,
) -> Optional[bytes]:
    """Synthesize (cache-backed) each segment and concat. None on any miss/failure."""
    if not segments:
        return None
    wavs: list[bytes] = []
    for seg in segments:
        try:
            wav = await synth(seg)
        except Exception as exc:
            logger.debug("voice_stitch: synth failed for %r (%s)", seg, exc)
            return None
        if not wav:
            return None
        wavs.append(wav)
    return concat_wavs(wavs, gap_ms=gap_ms)


async def prewarm_vocabulary(
    synth: Callable[[str], Awaitable[Optional[bytes]]],
    *,
    city: Optional[str] = None,
    pause_s: float = 0.05,
) -> int:
    """Synthesize every vocabulary segment once so each becomes a sidecar cache hit.

    Runs once at startup (flag-gated by the caller). Paced with a small pause so it
    never thunders the sidecar ahead of a live turn; a per-item failure is skipped,
    not fatal. Returns the number of segments successfully warmed. With the
    persistent cache (see the sidecar), a single prewarm survives restarts.
    """
    import asyncio

    warmed = 0
    for seg in vocabulary(city):
        try:
            if await synth(seg):
                warmed += 1
        except Exception as exc:
            logger.debug("voice_stitch: prewarm skipped %r (%s)", seg, exc)
        if pause_s:
            await asyncio.sleep(pause_s)
    logger.info("voice_stitch: prewarmed %d/%d vocabulary segments", warmed, len(vocabulary(city)))
    return warmed


# ── Reply-text → stitched audio (the live hook) ───────────────────────────────
# Strict, anchored patterns for the exact intent replies we own (both use digits):
#   time    → "It's 7:42 AM."                     (intent_router time_query)
#   weather → "It's 14 degrees and clear in Geraldton"   (intent_router weather)
# Anything else — a decimal temp, a "feels like" clause, a rephrase — simply does
# not match, so stitch_reply returns None and the caller uses normal TTS. It never
# guesses: a mis-parse can only FAIL to match, never emit wrong audio.
_TIME_RE = re.compile(r"^It(?:'s| is) (\d{1,2}):(\d{2}) (AM|PM)\.?$")
_WEATHER_RE = re.compile(
    r"^It's (\d{1,3}) degrees(?: and ([^,]+?))? in ([^,]+?)"
    r"(?:, and it feels like (\d{1,3}) degrees)?\.?$"
)


async def stitch_reply(
    text: str,
    synth: Callable[[str], Awaitable[Optional[bytes]]],
) -> Optional[bytes]:
    """If ``text`` is a known weather/time reply, return stitched audio, else None.

    Fully fallback-safe: disabled flag, no pattern match, an out-of-vocab value, or
    any synth miss → None → caller synthesizes the sentence normally.
    """
    if not enabled():
        return None
    t = (text or "").strip()

    m = _TIME_RE.match(t)
    if m:
        h12, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= h12 <= 12 and 0 <= minute <= 59):
            return None
        hour_24 = (h12 % 12) + (12 if ampm == "PM" else 0)
        segs = time_segments(hour_24, minute)
        return await stitch(segs, synth) if segs else None

    m = _WEATHER_RE.match(t)
    if m:
        temp, desc, city = int(m.group(1)), (m.group(2) or "").strip(), m.group(3).strip()
        segs = weather_segments(temp, desc, city)
        if not segs:
            return None
        feels = m.group(4)
        if feels is not None:
            fw = number_to_words(int(feels))
            if fw is None:
                return None  # out-of-vocab feels-like → full synth
            # Synth-once passthrough (cached thereafter), like the description.
            segs.append(f"and it feels like {fw} degrees")
        return await stitch(segs, synth)

    return None
