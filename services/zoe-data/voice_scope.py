"""Voice utterance scope classifier: `public` vs `user_scoped`.

Pure, synchronous helper used by the voice path to decide whether a turn is
safe to answer as "guest" (weather, time, lights, shared lists, shared calendar
reads, generic chat) or whether it requires a confirmed user identity (journal,
memory, finances, personal reminders, private lists, personal writes).

Design goals:
    * **No side effects** - pure function, easy to unit-test.
    * **Fail public** - ambiguous utterances default to the less-privileged
      answer so the guest UX goal stays intact.
    * **Intent-aware if available** - if the intent router already gave us
      an ``Intent``, we trust its name/slots; otherwise we fall back to
      cheap regex heuristics over the raw text.
    * **No imports from the chat stack** - this module must be trivially
      importable inside hot paths without dragging the full LLM graph.

Wiring into ``voice_command`` / ``voice_turn`` happens in Pass 3 (B3/B4/B7).
Pass 1 only ships the classifier + its tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from guest_policy import PUBLIC_HOUSEHOLD_INTENTS, USER_SCOPED_INTENTS

_PUBLIC_INTENTS: frozenset = PUBLIC_HOUSEHOLD_INTENTS
_USER_SCOPED_INTENTS: frozenset = USER_SCOPED_INTENTS

_SHARED_LIST_TYPES: frozenset = frozenset({
    "shopping", "groceries", "grocery", "household", "family", "pantry",
})

_HA_CONTROL_RE = re.compile(
    r"\b("
    r"turn (on|off|up|down)|"
    r"set (the |my )?thermostat|"
    r"dim|brighten|brightness|"
    r"(play|pause|resume|skip|stop|start|volume|mute|unmute|louder|quieter)"
    r")\b",
    re.IGNORECASE,
)

_MUSIC_USER_RE = re.compile(
    r"\b(my (playlist|mix|liked|tracks|library|history|favorites?|favourites?|queue|daily)"
    r"|resume where i left off)\b",
    re.IGNORECASE,
)

_POSSESSIVE_RE = re.compile(
    r"\b("
    r"my(?! (house|home|family|kitchen|lounge|living room|bedroom|bathroom|garage|garden))|"
    r"mine\b|"
    r"for me\b|"
    r"remind me\b|"
    r"remember (that|me)\b|"
    r"what did i\b|"
    r"what do i\b|"
    r"did i\b|"
    r"have i\b|"
    r"am i\b|"
    r"i need to\b|"
    r"i want to\b|"
    r"i should\b"
    r")",
    re.IGNORECASE,
)

_CLEAR_PUBLIC_RE = re.compile(
    r"\b("
    r"what(?:'s| is) the (time|date|weather|temperature|forecast)|"
    r"what time|what day|what date|"
    r"tell me a joke|"
    r"how are you\b|"
    r"good (morning|afternoon|evening|night)|"
    r"hello|hi( there)?|hey (zoe|there)|"
    r"thank you|thanks\b"
    r")",
    re.IGNORECASE,
)

_PLAY_PREFIX_RE = re.compile(r"^\s*play\b", re.IGNORECASE)


@dataclass(frozen=True)
class ScopeDecision:
    """Result of classifying an utterance."""
    scope: str                  # "public" | "user_scoped"
    reason: str                 # short tag used by metrics/logs
    intent_name: Optional[str]  # echoes intent.name when available


def classify(
    text: str,
    intent: Optional[Any] = None,
    *,
    default_when_ambiguous: str = "public",
) -> ScopeDecision:
    """Return a ``ScopeDecision`` for the given utterance.

    Args:
        text: Raw transcribed utterance (any case; ws is trimmed).
        intent: Optional intent_router.Intent instance. We only read
            ``intent.name`` and ``intent.slots`` so any duck-typed object
            with those attributes works. That keeps this module free of
            a hard import on the chat stack.
        default_when_ambiguous: What to return when no rule matches.
            Default ``"public"`` matches the "fail-public" design goal.

    The function is **pure** - no IO, no logging, no network calls.
    """
    t = (text or "").strip().lower()

    intent_name = getattr(intent, "name", None) if intent is not None else None
    if intent_name:
        if intent_name in _PUBLIC_INTENTS:
            return ScopeDecision("public", f"intent:{intent_name}", intent_name)

        if intent_name in ("list_add", "list_show", "list_remove", "list_update"):
            slots = getattr(intent, "slots", None) or {}
            lt = str(slots.get("list_type") or "").lower().strip()
            # Guard against intent_router mis-classifying "add a reminder" as list_add:shopping.
            # If the text itself mentions "reminder" or "remind me", treat as user_scoped.
            if re.search(r"\b(reminder|remind me)\b", t, re.IGNORECASE):
                return ScopeDecision("user_scoped", f"intent:{intent_name}:reminder_text", intent_name)
            if any(tag in lt for tag in _SHARED_LIST_TYPES):
                return ScopeDecision("public", f"intent:{intent_name}:shared_list", intent_name)
            if "my " in t or _POSSESSIVE_RE.search(t):
                return ScopeDecision("user_scoped", f"intent:{intent_name}:personal_list", intent_name)
            return ScopeDecision(default_when_ambiguous, f"intent:{intent_name}:ambiguous", intent_name)

        if intent_name.startswith("music_"):
            if _MUSIC_USER_RE.search(t):
                return ScopeDecision("user_scoped", f"intent:{intent_name}:my_library", intent_name)
            return ScopeDecision("public", f"intent:{intent_name}:generic", intent_name)

        if intent_name.startswith(("ha_", "light_", "media_", "climate_")):
            return ScopeDecision("public", f"intent:{intent_name}:ha", intent_name)

        if intent_name in _USER_SCOPED_INTENTS:
            return ScopeDecision("user_scoped", f"intent:{intent_name}", intent_name)

    if not t:
        return ScopeDecision(default_when_ambiguous, "empty_text", intent_name)

    if _CLEAR_PUBLIC_RE.search(t):
        return ScopeDecision("public", "text:clear_public", intent_name)

    # "my library" / "resume where i left off" must win before the HA
    # regex, otherwise "play"/"resume" would be tagged as public media
    # control even though the user clearly asked for personal content.
    if _MUSIC_USER_RE.search(t):
        return ScopeDecision("user_scoped", "text:my_music", intent_name)

    if _HA_CONTROL_RE.search(t):
        return ScopeDecision("public", "text:ha_control", intent_name)

    if _PLAY_PREFIX_RE.match(t):
        return ScopeDecision("public", "text:play_generic", intent_name)

    if _POSSESSIVE_RE.search(t):
        return ScopeDecision("user_scoped", "text:possessive", intent_name)

    return ScopeDecision(default_when_ambiguous, "text:fallback", intent_name)


__all__ = ["classify", "ScopeDecision"]
