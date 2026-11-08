"""Nothing unrelated rides on an emotional turn - flag ``ZOE_EMOTIONAL_TURN_GUARD=off|shadow|on`` (default ``off``).

THE MISS (QA 2026-11-05, demo household): "honestly I don't know anyone here yet ... it's kinda lonely" got "I'm here to listen if you
want to talk more about that. As for the weather, it's 25 degrees and clear sky ..." - the 4B re-read a weather result from earlier in
the session and tacked it on. On other turns a contact offer ("Would you like me to add Sam as a contact?") was voiced after a
personal share. The prompt side of the fix is the warmth doctrine (labs/flue-zoe-brain-2x src/doctrines.ts ``WARMTH_DOCTRINE``: "keep
that reply about them: nothing unrelated added"). This module is the deterministic backstop, in two places:

1. **Injection** (``holds_extras``): on an emotional turn the Flue seam does not add the pending contact-offer directive and does not
   prepare the first-turn day brief (weather + calendar), exactly as it already defers them on a continuity turn; the offer-aging
   tick skips the turn too (``latent_intent_detector``), so a run of emotional turns never expires an offer it hid. The proactive
   ``[RAISE ...]`` is NOT held: it carries the person's own open threads, which is the follow-up the doctrine asks for.
2. **The reply** (``trim`` / ``filter_stream``): on an emotional turn, a sentence AFTER the first that carries an info kind the user
   did not ask for (``weather``, ``contact_offer``, an ``aside`` that opens "By the way" / "As for the ...") is dropped. The first
   sentence is never touched (it is the acknowledgement) and is released as it streams, so the first sound is not delayed; later
   sentences are held only until each one ends. Asked-for info is kept: "I'm so stressed, will it rain for the wedding?" keeps its
   forecast. A non-emotional turn is never held at all.

WHAT IS AN EMOTIONAL TURN (``is_emotional_turn``, the user's own words only): the distress lexicon scores it (any tier; the hand-off
tier never reaches the brain anyway), OR ``memory_gate.extract_affect`` finds a first-person feeling, OR a ``cues`` phrase from
``lexicons_data/<lang>.json`` "emotional_turn" is in it ("kinda lonely", "feel like a terrible daughter"). Every word is DATA; a
language with no entry gets only the language-neutral parts. A false positive costs one deferred offer or one dropped weather
sentence, never the reply.

MODES. ``off`` (default): nothing runs, the stream and the wire bytes are exactly today's. ``shadow``: the stream is byte-identical
and nothing is held back; one label-only line ``EMOTIONAL_TURN_GUARD mode=shadow ... dropped=<n> kinds=<k>`` (never text) says what
``on`` would have done. ``on``: both places above act. VOICE-PATH: replay-gated before the flag flips.

Stdlib only (the lexicon / memory_gate / distress imports are lazy). Never raises into a turn.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import AsyncIterator, Optional

from narration_filter import _next_end  # the repo's one sentence splitter (abbreviations and initials do not end a sentence)
from typed_env import env_str

logger = logging.getLogger(__name__)

FLAG = "ZOE_EMOTIONAL_TURN_GUARD"
MODES = ("off", "shadow", "on")
LEXICON_KEY = "emotional_turn"
FALLBACK_LANG = "en"
#: a pasted essay is not scanned for cues (the regexes stay cheap on the voice path)
MAX_MESSAGE_CHARS = 600
_SENTINELS = ("__TOOL__:", "__THINKING__:")


def mode() -> str:
    """``off`` (default; unset or unknown) | ``shadow`` | ``on``. Read per call, so a flip needs no restart."""
    raw = env_str("ZOE_EMOTIONAL_TURN_GUARD", "off").lower()  # literal on purpose: tools/audit/flag_inventory.py greps it
    if raw in ("on", "1", "true", "yes", "enforce"):
        return "on"
    if raw == "shadow":
        return "shadow"
    return "off"


# -- the words ---------------------------------------------------------------------------------------------

def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").replace("’", "'").replace("‘", "'").lower()
    return " ".join(t.split())


def _phrase_rx(items) -> Optional["re.Pattern[str]"]:
    parts = [r"\s+".join(re.escape(w) for w in _fold(str(it)).split()) for it in items or [] if str(it).strip()]
    if not parts:
        return None
    return re.compile(r"(?<![\w'])(?:" + "|".join(sorted(parts, key=len, reverse=True)) + r")(?![\w'])")


def _lead_rx(items) -> Optional["re.Pattern[str]"]:
    parts = [r"[\s,]+".join(re.escape(w) for w in _fold(str(it)).replace(",", " ").split()) for it in items or [] if str(it).strip()]
    if not parts:
        return None
    return re.compile(r"^\W*(?:" + "|".join(sorted(parts, key=len, reverse=True)) + r")(?![\w'])")


@dataclass(frozen=True)
class Lexicon:
    lang: str
    cues: Optional["re.Pattern[str]"]
    info: dict = field(default_factory=dict)      # kind -> [compiled] (anywhere in the sentence)
    leads: dict = field(default_factory=dict)     # kind -> compiled (sentence start only)
    asks: dict = field(default_factory=dict)      # kind -> compiled (the user's words ask for it)


@lru_cache(maxsize=None)
def lexicon(lang: str) -> Optional[Lexicon]:
    """The emotional-turn lexicon of ``lang``, or None when that language has none."""
    import lexicons

    entry = (lexicons.load(lang) or {}).get(LEXICON_KEY)
    if not isinstance(entry, dict):
        return None
    info: dict = {}
    for kind, items in (entry.get("info") or {}).items():
        rx = _phrase_rx(items)
        if rx is not None:
            info.setdefault(kind, []).append(rx)
    for kind, pats in (entry.get("info_patterns") or {}).items():
        for p in pats or []:
            try:
                info.setdefault(kind, []).append(re.compile(str(p)))
            except re.error:
                logger.warning("EMOTIONAL_TURN_GUARD bad pattern in %s/%s; skipped", lang, kind)
    leads = {k: rx for k, items in (entry.get("leads") or {}).items() if (rx := _lead_rx(items)) is not None}
    asks = {k: rx for k, items in (entry.get("asks") or {}).items() if (rx := _phrase_rx(items)) is not None}
    return Lexicon(lang=(lang or "").lower(), cues=_phrase_rx(entry.get("cues")), info=info, leads=leads, asks=asks)


def _lang_of(message: str) -> str:
    import lexicons

    return lexicons.detect(message or "") or FALLBACK_LANG


# -- the turn ----------------------------------------------------------------------------------------------

def is_emotional_turn(message: str) -> bool:
    """Does the user's own turn carry a feeling (theirs, or a hard thing in their life)? Pure apart from the lexicon reads."""
    text = (message or "").strip()
    if not text:
        return False
    text = text[:MAX_MESSAGE_CHARS]
    try:
        import distress_handoff

        if distress_handoff.detect(text).tier != "none":
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from memory_gate import extract_affect

        if extract_affect(text)[0]:
            return True
    except Exception:  # noqa: BLE001
        pass
    lex = lexicon(_lang_of(text))
    return bool(lex and lex.cues and lex.cues.search(_fold(text)))


def asked_kinds(message: str, lex: Lexicon) -> frozenset:
    """The info kinds the user's own words ask about (those are kept in the reply)."""
    low = _fold(message)
    return frozenset(k for k, rx in lex.asks.items() if rx.search(low))


def info_kind(sentence: str, lex: Lexicon) -> str:
    """The info kind ONE reply sentence carries ("weather", "contact_offer", "aside", ...), or "". Pure."""
    low = _fold(sentence)
    if not low:
        return ""
    for kind, rxs in lex.info.items():
        if any(rx.search(low) for rx in rxs):
            return kind
    for kind, rx in lex.leads.items():
        if rx.search(low):
            return kind
    return ""


def holds_extras(message: str, user_id: str = "", *, log: bool = True) -> bool:
    """True when the seam must NOT add the contact-offer directive / the day brief to this turn (mode ``on`` and an emotional turn).
    ``shadow`` logs what ``on`` would hold (once per turn: the offer ager asks with ``log=False``) and returns False. Never raises."""
    try:
        m = mode()
        if m == "off" or not is_emotional_turn(message):
            return False
        if m == "shadow":
            if log:
                logger.info("EMOTIONAL_TURN_GUARD mode=shadow surface=extras held=1 user=%s", (user_id or "").strip())
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.warning("EMOTIONAL_TURN_GUARD extras check failed; nothing held", exc_info=True)
        return False


# -- the reply ---------------------------------------------------------------------------------------------

class GuardStream:
    """Incremental :func:`trim`. ``feed(delta)`` returns the text safe to emit now; ``finish()`` the rest. The concatenation of every
    return equals ``trim`` of the whole reply for every way of cutting it into deltas (tests). The FIRST sentence streams straight
    through (it is never dropped); each later sentence is held only until it ends, then released or dropped. ``shadow=True``
    changes nothing and only counts."""

    def __init__(self, lex: Lexicon, asked: frozenset, shadow: bool = False) -> None:
        self.lex, self.asked, self.shadow = lex, asked, shadow
        self._first = ""         # the first sentence so far (already released; kept to find where it ends)
        self._first_done = False
        self._buf = ""           # held: the current later sentence, with its leading whitespace
        self._seen = ""          # shadow: the whole reply, judged at finish
        self.dropped: list[str] = []

    def _judge(self, piece: str) -> str:
        kind = info_kind(piece, self.lex)
        if kind and kind not in self.asked:
            self.dropped.append(kind)
            return ""
        return piece

    def _drain(self, final: bool) -> str:
        out = []
        while self._buf:
            stripped = self._buf.lstrip()
            lead = len(self._buf) - len(stripped)
            if not stripped:
                if final:
                    out.append(self._buf)
                    self._buf = ""
                break
            end = _next_end(stripped)
            if end < 0:
                if final:
                    out.append(self._judge(self._buf))
                    self._buf = ""
                break
            out.append(self._judge(self._buf[:lead + end]))
            self._buf = self._buf[lead + end:]
        return "".join(out)

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        if self.shadow:
            self._seen += delta
            return delta
        if self._first_done:
            self._buf += delta
            return self._drain(final=False)
        released = len(self._first)
        combined = self._first + delta
        end = _next_end(combined)
        if end < 0:
            self._first = combined
            return delta
        cut = max(end, released)
        self._first, self._first_done = "", True
        self._buf = combined[cut:]
        return combined[released:cut] + self._drain(final=False)

    def finish(self) -> str:
        if self.shadow:
            replay = GuardStream(self.lex, self.asked)
            replay.feed(self._seen)
            replay.finish()
            self.dropped = replay.dropped
            return ""
        return self._drain(final=True) if self._first_done else ""


def trim(reply: str, message: str) -> str:
    """The reply with every unrequested info sentence after the first dropped, on an emotional turn; unchanged otherwise. Pure apart
    from the lexicon reads (no flag read: callers decide the mode)."""
    if not (reply or "").strip() or not is_emotional_turn(message):
        return reply or ""
    lex = lexicon(_lang_of(message)) or lexicon(FALLBACK_LANG)
    if lex is None:
        return reply
    g = GuardStream(lex, asked_kinds(message, lex))
    return g.feed(reply) + g.finish()


def dropped_kinds(reply: str, message: str) -> list[str]:
    """What ``trim`` would drop (kinds, in order). The warmth probes count it."""
    if not (reply or "").strip() or not is_emotional_turn(message):
        return []
    lex = lexicon(_lang_of(message)) or lexicon(FALLBACK_LANG)
    if lex is None:
        return []
    g = GuardStream(lex, asked_kinds(message, lex))
    g.feed(reply)
    g.finish()
    return list(g.dropped)


async def filter_stream(deltas: AsyncIterator[str], message: str, user_id: str = "") -> AsyncIterator[str]:
    """Wrap a brain delta stream. Mode ``off`` or a non-emotional turn: passed through untouched (nothing held). Sentinels always pass
    at once. ``aclose`` is forwarded so an early consumer exit closes the inner turn deterministically."""
    m = mode()
    stream: Optional[GuardStream] = None
    try:
        if m != "off" and is_emotional_turn(message):
            lex = lexicon(_lang_of(message)) or lexicon(FALLBACK_LANG)
            if lex is not None:
                stream = GuardStream(lex, asked_kinds(message, lex), shadow=(m == "shadow"))
    except Exception:  # noqa: BLE001 - a guard is optional; the turn is not
        logger.warning("EMOTIONAL_TURN_GUARD setup failed; passing through", exc_info=True)
        stream = None
    try:
        async for delta in deltas:
            if stream is None or delta.startswith(_SENTINELS):
                yield delta
                continue
            try:
                out = stream.feed(delta)
            except Exception:  # noqa: BLE001
                logger.warning("EMOTIONAL_TURN_GUARD failed; passing through", exc_info=True)
                out = delta if stream.shadow else stream._buf
                stream = None
            if out:
                yield out
        if stream is not None:
            try:
                tail = stream.finish()
            except Exception:  # noqa: BLE001
                logger.warning("EMOTIONAL_TURN_GUARD finish failed", exc_info=True)
                tail = "" if stream.shadow else stream._buf
            if tail:
                yield tail
            if stream.dropped or m == "shadow":
                logger.info("EMOTIONAL_TURN_GUARD mode=%s surface=reply dropped=%d kinds=%s user=%s", m, len(stream.dropped),
                            ",".join(stream.dropped) or "-", (user_id or "").strip())
    finally:
        aclose = getattr(deltas, "aclose", None)
        if aclose is not None:
            await aclose()  # type: ignore[misc]
