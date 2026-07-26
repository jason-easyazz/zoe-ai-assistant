"""W11 — expressive delivery: decide HOW a reply is spoken, not what it says.

Kokoro renders every reply with one fixed delivery today. Samantha's voice acts:
late-night consolation is slower and softer than a timer confirmation. The rock
does not change — the sidecar already takes a per-request ``speed`` and the reply
is already sentence-split; this module is the deterministic mapper that decides
when to use it.

WHY IT IS DELIBERATELY CONSERVATIVE
-----------------------------------
The Kokoro sidecar's phrase cache **only hits at speed == 1.0** (see
``scripts/setup/kokoro_sidecar.py``: keys are ``voice|text``, entries stored and
served only at unit speed, ``_CACHE_MAX_TEXT_LEN = 240``). A cache hit is ~2ms; a
cold synthesis is 1-2.5s. So every utterance this module slows down is an
utterance that STOPS being cacheable — a ~1000x cost on the hot path if applied
carelessly.

Two rules follow, and they are the whole safety story:

  1. **Neutral is None, not 1.0.** Returning ``None`` means "send the request
     exactly as before" — no ``speed`` key at all — so the flag-off and
     no-profile paths are byte-identical to today's payload. A literal ``1.0``
     would still be a behaviour change to reason about; ``None`` is not.
  2. **Short utterances are never touched.** Confirmations, acks and tool
     fillers ("Okay.", "Done.", "Let me check your calendar.") are exactly the
     high-frequency entries the phrase cache exists for, and exactly the ones
     that gain nothing from expressive delivery. Anything at or under
     ``ZOE_EXPRESSIVE_MIN_CHARS`` is left alone.

W4 (prosody sensing) is BLOCKED behind the W3 RAM gate, so there is no
valence/arousal signal to read. This mapper uses only what is already free:
wall-clock hour and the reply's own text. When W4 lands it becomes one more
input here rather than a rewrite.

NON-GOALS (from the plan): synthetic laughter and singing. The model cannot do
them; faking them badly is worse than not doing them.
"""
from __future__ import annotations

import os
import re
import zoneinfo
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Shared typed env helpers, NOT hand-rolled ones: they are the repo's single
# mechanic for flag reads, and `tools/audit/flag_inventory.py` only discovers
# flags passed as literals to these functions (or os.environ.get/getenv). A
# local `_env_flag` wrapper would work at runtime and be INVISIBLE to the
# generated inventory — a flag nobody can find is a flag nobody can turn off.
from typed_env import env_bool, env_int

# Household timezone, resolved the same way proactive/engine.py does for ITS quiet
# hours. `datetime.now()` alone reads the HOST clock, and this box runs UTC while
# the household is Australia/Perth — so a naive now() would soften delivery in the
# middle of the afternoon and leave real 2am replies neutral. Quiet hours are a
# fact about the house, not about the server.
_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))

# Profiles are speed multipliers for the Kokoro sidecar. Kept few and boring on
# purpose: every one of them costs a phrase-cache miss, so each has to earn it.
SPEED_GENTLE = 0.92   # late night — quieter house, slower delivery
SPEED_WARM = 0.94     # consolation / empathy content, any hour

# Consolation lexicon. Deliberately explicit and narrow rather than a sentiment
# model: this must NOT fire on ordinary replies, and a false positive costs a
# cache miss plus a reply that sounds oddly funereal about the weather.
_WARM_RE = re.compile(
    r"\b("
    r"i'?m sorry|sorry to hear|that sounds (hard|tough|awful|rough)|"
    r"take your time|no rush|i'?m here|are you o\.?k(ay)?|"
    r"that must (be|have been)|hope you'?re o\.?k(ay)?"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Delivery:
    """A resolved delivery decision. ``speed is None`` means "unchanged"."""
    speed: Optional[float]
    profile: str


NEUTRAL = Delivery(speed=None, profile="neutral")


def expressive_enabled() -> bool:
    """ZOE_EXPRESSIVE_TTS — default OFF (plan: every behaviour ships flag-gated)."""
    return env_bool("ZOE_EXPRESSIVE_TTS", default=False)


def min_chars() -> int:
    """Utterances at or below this length keep the cacheable neutral delivery."""
    return env_int("ZOE_EXPRESSIVE_MIN_CHARS", 60)


def _quiet_hours(hour: int) -> bool:
    """Late-night window, inclusive start / exclusive end, wrapping midnight."""
    start = env_int("ZOE_EXPRESSIVE_GENTLE_FROM_H", 22)
    end = env_int("ZOE_EXPRESSIVE_GENTLE_TO_H", 7)
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def resolve(text: str, *, hour: Optional[int] = None) -> Delivery:
    """Decide how ``text`` should be spoken.

    Returns :data:`NEUTRAL` (``speed=None``) unless there is a positive reason to
    do otherwise — the caller must then send the synthesis request unchanged.
    """
    if not expressive_enabled():
        return NEUTRAL
    body = (text or "").strip()
    # Guard 1: short utterances are the phrase cache's bread and butter.
    if len(body) <= min_chars():
        return NEUTRAL

    if hour is None:
        hour = datetime.now(_ZOE_TZ).hour

    candidates: list[Delivery] = []
    if _WARM_RE.search(body):
        candidates.append(Delivery(speed=SPEED_WARM, profile="warm"))
    if _quiet_hours(int(hour)):
        candidates.append(Delivery(speed=SPEED_GENTLE, profile="gentle"))
    if not candidates:
        return NEUTRAL
    # Both can apply (a 2am consolation). Take the slowest — the gentler reading
    # is always the safe one when two reasons to soften agree.
    return min(candidates, key=lambda d: d.speed if d.speed is not None else 1.0)
