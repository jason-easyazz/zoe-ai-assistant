"""Spacers: the short spoken lines that fill a wait ("Let me check.", "One sec."). One policy for every mechanism that speaks one.

Three mechanisms speak a spacer on the voice path, each from a different layer:

* the TIMER filler  (``routers/voice_tts.py`` ``voice_turn_stream``, ``ZOE_VOICE_FILLER_ENABLED``): the first audio is late.
* the TOOL ACK      (``voice_first_sound.dispatch_ack``, ``ZOE_FIRST_SOUND_TOOL_ACK``): a tool-class turn is dispatched to the brain.
* the TOOL FILLER   (``routers/voice_tts.py`` ``_voice_tool_filler``, ``ZOE_VOICE_TOOL_FILLER``): the brain announced a tool call.

Live 2026-10-12 they fired on 12 of 18 brain turns: sometimes two on one turn, the same line three times running, "Hmm, let me look"
after a person said a parent was in hospital, "Let me check" on a philosophical question, "Let me think" on a stored-name lookup.
What this module decides, once, for all three:

* ONE per turn. ``SpacerTurn`` is shared between the layers (the outer stream and ``voice_command`` run as two tasks); a mechanism
  ``claim``s the turn before it synthesizes, so the second one finds it taken.
* NEVER the same line twice in a row on a panel (``pick`` keeps the last lines per panel, across mechanisms).
* A WIDE pool in the lexicon (``lexicons_data/<lang>.json`` "spacer"), and a family that fits what the turn is doing: "let me check"
  only for a lookup; a recall turn says "let me think"; an action says "one sec".
* SILENCE when a spacer would be wrong: a router ``chat`` turn gets no TIMER filler (it is a conversation, not a lookup); a turn
  that discloses something emotional or personal gets none from any mechanism (the existing restraint classes + the continuity
  shape, no word list here); a recall/identity question the seam answers from the packet or the account gets no TOOL ACK; and no
  TIMER filler when the first token is expected inside ``ZOE_VOICE_FILLER_EXPECT_MIN_S`` (1.0 s).

``SpacerTurn.expect_first_token(seconds)`` is the hook for a measured time-to-first-token or a cache hit at dispatch; today only
the fast-lane heuristic (``fast_lane``) sets it. No word of any language lives here. NEVER raises into a turn.
"""
from __future__ import annotations

import logging
import os
import random
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

import lexicons
from typed_env import env_float

logger = logging.getLogger(__name__)

#: the router domain -> the family of line that fits it. ``chat`` is absent on purpose (no spacer is chosen for a conversation).
#: A domain with no entry (the router is off, an unknown label) takes the ``generic`` family.
DOMAIN_FAMILY: dict[str, str] = {
    "calendar": "lookup", "lists": "lookup", "reminders": "lookup", "weather": "lookup", "time": "lookup",
    "people": "lookup", "notes": "lookup", "journal": "lookup",
    "memory": "recall",
    "music": "action", "smart_home": "action", "timers": "action",
}
#: the domains whose DISPATCH-time acknowledgement exists at all (the blueprint's control: a chat turn gets none).
TOOL_ACK_DOMAINS: tuple[str, ...] = tuple(DOMAIN_FAMILY)
#: domains where a fast lane (an identity question, a recall-floor question) answers from the account / the packet.
FAST_LANE_DOMAINS = frozenset({"memory", "people"})
#: restraint classes that mark a turn as an emotional or personal disclosure (``restraint.classify``).
DISCLOSURE_CLASSES = frozenset({"affect", "health", "grief", "family_conflict"})
#: the first-token estimate the fast-lane heuristic hands the timer filler (seconds); only its being under the minimum matters.
FAST_LANE_TTFT_S = 0.5
#: how many of a panel's last lines ``pick`` avoids (it falls back to fewer when the pool is too small to avoid them all).
RECENT_PER_PANEL = 2
_MAX_PANELS = 128

_recent: "OrderedDict[str, list[str]]" = OrderedDict()
_rng = random.Random()


def expect_min_s() -> float:
    """``ZOE_VOICE_FILLER_EXPECT_MIN_S`` (default 1.0): a first token expected sooner than this needs no timer filler."""
    return max(0.0, env_float("ZOE_VOICE_FILLER_EXPECT_MIN_S", 1.0))


# ── lexicon pools ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _section(lang: str) -> dict:
    sec = lexicons.load(lang).get("spacer")
    if sec:
        return sec
    return lexicons.load("en").get("spacer") or {}   # a language with no pool speaks the default one rather than nothing


def family_of(domain: str) -> str:
    return DOMAIN_FAMILY.get((domain or "").strip().lower(), "generic")


def _env_override() -> list[str]:
    """``ZOE_VOICE_FILLER_PHRASES`` (``a|b|c``): an operator pin for the TIMER pool. Empty/unset = the lexicon's."""
    return [p.strip() for p in os.environ.get("ZOE_VOICE_FILLER_PHRASES", "").split("|") if p.strip()]


def timer_pool(domain: str, lang: str = "en") -> list[str]:
    """The lines the timer filler may say for ``domain``: the env pin when set, else the family's lines plus the domain's own
    acknowledgement lines (a calendar wait may say "Checking your calendar.")."""
    pinned = _env_override()
    if pinned:
        return pinned
    sec = _section(lang)
    pool = list((sec.get("families") or {}).get(family_of(domain)) or [])
    pool += [p for p in (sec.get("tool_ack") or {}).get((domain or "").strip().lower()) or [] if p not in pool]
    return pool or list((sec.get("families") or {}).get("generic") or [])


def tool_ack_pool(domain: str, lang: str = "en") -> list[str]:
    """The lines the dispatch-time acknowledgement may say for ``domain``: its own pool, else its family's. Empty for ``chat``
    and for a domain the router does not name (no acknowledgement)."""
    dom = (domain or "").strip().lower()
    if dom not in DOMAIN_FAMILY:
        return []
    sec = _section(lang)
    return list((sec.get("tool_ack") or {}).get(dom) or (sec.get("families") or {}).get(family_of(dom)) or [])


# ── no repeats ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def pick(pool: list[str], panel_key: str, *, rng: Optional[random.Random] = None) -> Optional[str]:
    """One line from ``pool`` that is not one of the panel's last ``RECENT_PER_PANEL`` lines (fewer when the pool is too small to
    avoid them all; never the very last one unless it is the only line there is). Remembers the choice. ``None`` for an empty pool."""
    lines = [p for p in pool if p and p.strip()]
    if not lines:
        return None
    key = (panel_key or "-")[:80]
    recent = _recent.get(key, [])
    chosen: Optional[str] = None
    for avoid in range(min(RECENT_PER_PANEL, len(recent)), -1, -1):
        banned = set(recent[len(recent) - avoid:]) if avoid else set()
        options = [p for p in lines if p not in banned]
        if options:
            chosen = (rng or _rng).choice(options)
            break
    chosen = chosen or lines[0]
    _recent[key] = (recent + [chosen])[-RECENT_PER_PANEL:]
    _recent.move_to_end(key)
    while len(_recent) > _MAX_PANELS:
        _recent.popitem(last=False)
    return chosen


def last_line(panel_key: str) -> Optional[str]:
    recent = _recent.get((panel_key or "-")[:80]) or []
    return recent[-1] if recent else None


def reset() -> None:
    _recent.clear()


# ── the turn ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def is_disclosure(text: str, user_id: str = "") -> bool:
    """True when the turn discloses something emotional or personal: a restraint class (affect / health / grief / family
    conflict, ``restraint.classify``) or the continuity shape of a first-person feeling (``zoe_flue_client.is_continuity_turn``).
    Existing signals only; fails to False."""
    if not (text or "").strip():
        return False
    try:
        import restraint

        if DISCLOSURE_CLASSES.intersection(restraint.classify(text)):
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("voice_spacer: restraint signal unavailable: %s", type(exc).__name__)
    try:
        import zoe_flue_client

        # the shape of a first-person feeling does not depend on WHO says it; a non-guest placeholder keeps the helper's
        # guest short-circuit from hiding it
        return bool(zoe_flue_client.is_continuity_turn(text, user_id if user_id and user_id not in ("guest", "voice-guest") else "household"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("voice_spacer: continuity signal unavailable: %s", type(exc).__name__)
    return False


def fast_lane(text: str, domain: str) -> bool:
    """True when this is a memory/people-domain turn the stack answers from the ACCOUNT or the recall packet (an own-identity
    question, a recall-floor question): its first token comes quickly, so a spacer in front of it is noise."""
    if (domain or "").strip().lower() not in FAST_LANE_DOMAINS:
        return False
    try:
        import identity_facts

        if identity_facts.identity_question_kind(text):
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("voice_spacer: identity signal unavailable: %s", type(exc).__name__)
    try:
        import zoe_flue_client

        return bool(zoe_flue_client._recall_floor_shape(text))
    except Exception as exc:  # noqa: BLE001
        logger.debug("voice_spacer: recall-floor signal unavailable: %s", type(exc).__name__)
    return False


@dataclass
class SpacerTurn:
    """The spacer state of ONE voice turn, shared by the layers that may speak one (the outer stream and ``voice_command``)."""

    panel_key: str = "-"
    text: str = ""
    user_id: str = ""
    domain: str = ""
    tool: str = ""
    spoken: Optional[str] = None            # which mechanism holds the turn's one spacer ("timer" | "tool_ack" | "tool_filler")
    fast: bool = False
    expected_ttft_s: Optional[float] = None
    _disclosure: Optional[bool] = None

    # ── what the router said ──
    def note_router(self, decision: Optional[dict]) -> None:
        """Record the router's domain/tool for this turn (``semantic_router.route`` shape) and the fast-lane verdict."""
        try:
            if not isinstance(decision, dict):
                return
            self.domain = str(decision.get("routed") or "").strip().lower()
            self.tool = str((decision.get("two_stage") or {}).get("tool") or "")
            self.fast = fast_lane(self.text, self.domain)
            if self.fast:
                self.expect_first_token(FAST_LANE_TTFT_S)
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice_spacer: note_router failed: %s", type(exc).__name__)

    def expect_first_token(self, seconds: float) -> None:
        """The hook for a measured TTFT or a cache hit known at dispatch: a turn whose first token is expected sooner than
        ``ZOE_VOICE_FILLER_EXPECT_MIN_S`` gets no timer filler."""
        self.expected_ttft_s = float(seconds) if self.expected_ttft_s is None else min(self.expected_ttft_s, float(seconds))

    @property
    def disclosure(self) -> bool:
        if self._disclosure is None:
            self._disclosure = is_disclosure(self.text, self.user_id)
        return self._disclosure

    @property
    def lang(self) -> str:
        try:
            return lexicons.detect(self.text)
        except Exception:  # noqa: BLE001
            return "en"

    # ── the one-per-turn rule ──
    def veto(self, source: str) -> Optional[str]:
        """Why ``source`` ("timer" | "tool_ack" | "tool_filler") must stay silent this turn, or ``None`` when it may speak."""
        if self.spoken:
            return f"already_spoken:{self.spoken}"
        if self.disclosure:
            return "disclosure"
        if source == "timer":
            if self.domain == "chat":
                return "chat_domain"
            if self.expected_ttft_s is not None and self.expected_ttft_s < expect_min_s():
                return "fast_first_token"
        if source == "tool_ack" and self.fast:
            return "fast_lane"
        return None

    def claim(self, source: str) -> bool:
        """Take the turn's one spacer for ``source`` (synchronous, so the two layers cannot both pass the check). False = taken."""
        if self.spoken:
            return False
        self.spoken = source
        return True

    def release(self, source: str) -> None:
        """Give the spacer back when nothing was actually spoken (the synthesis failed)."""
        if self.spoken == source:
            self.spoken = None

    # ── the lines ──
    def timer_line(self) -> Optional[str]:
        """The timer filler's line for this turn, or ``None`` when it must stay silent. Claims the turn."""
        reason = self.veto("timer")
        if reason:
            logger.info("SPACER skip source=timer reason=%s domain=%s", reason, self.domain or "-")
            return None
        return self._claim_and_pick("timer", lambda: timer_pool(self.domain, self.lang))

    def tool_ack_line(self) -> Optional[str]:
        """The dispatch-time acknowledgement for this turn, or ``None``. Claims the turn."""
        reason = self.veto("tool_ack")
        if reason:
            if self.domain in DOMAIN_FAMILY:
                logger.info("SPACER skip source=tool_ack reason=%s domain=%s", reason, self.domain)
            return None
        return self._claim_and_pick("tool_ack", lambda: tool_ack_pool(self.domain, self.lang))

    def _claim_and_pick(self, source: str, pool) -> Optional[str]:
        # claim BEFORE picking: the claim is synchronous, so of two layers racing for the turn exactly one gets a line, and a
        # line is only remembered (against repeats) once it is going to be spoken
        if not self.claim(source):
            return None
        line = pick(pool(), self.panel_key)
        if line is None:
            self.release(source)
        return line

    def tool_filler_line(self, templated: str) -> Optional[str]:
        """The line for a brain tool-call sentinel: ``templated`` (the tool-specific line) unless it repeats the panel's last
        line, then a family line. Claims the turn; ``None`` when another spacer holds it or the turn is a disclosure."""
        reason = self.veto("tool_filler")
        if reason:
            logger.info("SPACER skip source=tool_filler reason=%s domain=%s", reason, self.domain or "-")
            return None
        if not self.claim("tool_filler"):
            return None
        if templated and templated != last_line(self.panel_key):
            return pick([templated], self.panel_key)           # remembers it
        line = pick(timer_pool(self.domain, self.lang), self.panel_key) or templated
        if not line:
            self.release("tool_filler")
        return line or None
