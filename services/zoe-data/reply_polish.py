"""Two last-mile reply rules - flag ``ZOE_REPLY_POLISH=off|shadow|on`` (default ``on``).

1. **Plumbing words never reach the person.** QA 2026-11-08, "if I said 'turn on the kitchen light' what would you do?" -> "I'd activate
   the home ability and then use the home tool to turn on the kitchen light." The doctrine (labs/flue-zoe-brain-2x src/doctrines.ts) now
   says never to name tools, abilities or groups; this is the deterministic backstop. ``scrub`` removes "activate the <group> ability",
   "use the <group|tool> tool" and humanises a raw tool identifier (``show_calendar`` -> "show calendar"). A sentence the scrub empties is
   dropped; a reply is never emptied (the original is kept when everything would go).
2. **Not every reply ends on a question.** "Nearly every reply ends on a question" (QA round 4: about 80 percent of the feelings turns).
   When the PREVIOUS reply of this conversation already ended on a question, a generic closing question ("How are you feeling?", "Is there
   anything else?", "Does that make sense?", from ``lexicons_data/<lang>.json`` "mirror" generic_closers + "question_cap" closers) at the end of
   this one is dropped, provided a sentence of substance comes before it. A specific question ("Which list should I put bolts on?") is never
   a closer, and a reply that is only a closer is kept whole.

Streaming. Text is released a sentence at a time (the repo's one splitter, ``narration_filter._next_end``); only a completed question
sentence that WOULD be dropped is held back, until the next sentence (release it) or the end of the reply (drop it). Tool / thinking
sentinels pass untouched and at once. ``PolishStream.feed/finish`` equals ``polish`` of the whole reply for every way of cutting it
into deltas (tests).

The words are data (the lexicon) and English-only where they are code (the plumbing names): a language with no lexicon gets no cap.
``shadow``: the stream is byte-identical and one label-only line says what ``on`` would have done (never reply text). Stdlib only. Never
raises into the stream.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from collections import OrderedDict
from typing import AsyncIterator, Optional

from narration_filter import _next_end  # the repo's one sentence splitter
from typed_env import env_str

logger = logging.getLogger(__name__)

FLAG = "ZOE_REPLY_POLISH"
_SENTINELS = ("__TOOL__:", "__THINKING__:")
_MAX_SESSIONS = 512


def mode() -> str:
    """``on`` (default; unset or unknown) | ``shadow`` | ``off``. Read per call."""
    raw = env_str("ZOE_REPLY_POLISH").lower()   # literal on purpose: tools/audit/flag_inventory.py greps it
    if raw in ("0", "false", "no", "off", "disabled"):
        return "off"
    if raw == "shadow":
        return "shadow"
    return "on"


# -- plumbing words ----------------------------------------------------------------------------------------

#: the ability groups the brain's activator names (labs/flue-zoe-brain-2x src/tools/tool-groups.ts TOOL_GROUPS)
GROUPS = ("weather", "lists", "list", "timers", "timer", "alarms", "alarm", "reminders", "reminder", "calendar", "notes", "note",
          "journal", "people", "media", "home", "memory")
#: every tool name the brain can call (pinned against zoe-tools.ts by tests/test_reply_polish.py)
TOOL_NAMES = (
    "activate_abilities", "get_time", "recall_memory", "get_weather", "shopping_list_add", "add_to_list", "list_remove", "show_list",
    "set_timer", "alarm", "list_reminders", "add_reminder", "show_calendar", "add_calendar_event", "create_note", "note_search",
    "journal", "people", "media", "home", "remember_fact", "remember_emotional_moment", "memory_retire", "web_search",
)
_G = r"(?:" + "|".join(GROUPS) + r")"
_ACTIVATE_RX = re.compile(
    r"\b(?:activate|activating|unlock|unlocking|enable|enabling)\s+(?:the\s+)?" + _G + r"\s+(?:abilit(?:y|ies)|group|tools?)\b"
    r"(?:\s*,)?(?:\s+and)?(?:\s+then)?\s*", re.IGNORECASE)
_USE_RX = re.compile(
    r"\b(?:use|using|call|calling|run|running|trigger|triggering)\s+(?:the\s+)?(?:" + _G + r"|"
    + "|".join(n.replace("_", r"[\s_]") for n in TOOL_NAMES if "_" in n) + r")\s+(?:tool|function|abilit(?:y|ies))\b(?:\s+to\b)?\s*",
    re.IGNORECASE)
_IDENT_RX = re.compile(r"\b(" + "|".join(n for n in TOOL_NAMES if "_" in n) + r")\b")


def scrub(sentence: str) -> str:
    """The sentence without tool / ability / group names. May return '' (the sentence was only plumbing). Pure."""
    s = _ACTIVATE_RX.sub("", sentence)
    s = _USE_RX.sub("", s)
    s = _IDENT_RX.sub(lambda m: m.group(1).replace("_", " "), s)
    if s == sentence:
        return sentence
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\b(I'd|I'll|I would|I will|I can|I could|I'd then)\s+(and|then|to)\s+", r"\1 ", s)
    if len(re.findall(r"[A-Za-z']+", s)) < 3:
        return ""
    return s[:1].upper() + s[1:] if s[:1].islower() and sentence[:1].isupper() else s


# -- the closing question ----------------------------------------------------------------------------------

def _closer_lexicon(lang: str):
    """The mirror lexicon with the question-cap closers added, or None for a language with no lexicon (no cap)."""
    import lexicons
    import mirror_guard

    lex = mirror_guard.lexicon(lang)
    if lex is None:
        return None
    extra = mirror_guard._phrases(((lexicons.load(lang) or {}).get("question_cap") or {}).get("closers"))
    return dataclasses.replace(lex, closers=lex.closers + extra) if extra else lex


_LEX_CACHE: dict = {}


def closer_lexicon_for(message: str):
    import lexicons

    lang = lexicons.detect(message or "") or "en"
    if lang not in _LEX_CACHE:
        _LEX_CACHE[lang] = _closer_lexicon(lang)
    return _LEX_CACHE[lang]


def is_closing_question(sentence: str, lex) -> bool:
    import mirror_guard

    return lex is not None and mirror_guard.is_generic_closer(sentence, lex)


#: (user id, session id) -> did the last reply end on a question? A bounded LRU; a dict write, never I/O.
_ended_q: "OrderedDict[tuple, bool]" = OrderedDict()


def ended_on_question(user_id: str, session_id: str) -> bool:
    return _ended_q.get(((user_id or "").strip(), session_id or ""), False)


def note_reply(user_id: str, session_id: str, text: str) -> None:
    key = ((user_id or "").strip(), session_id or "")
    _ended_q.pop(key, None)
    _ended_q[key] = (text or "").rstrip().endswith("?")
    while len(_ended_q) > _MAX_SESSIONS:
        _ended_q.popitem(last=False)


def reset_state() -> None:
    _ended_q.clear()


# -- the stream --------------------------------------------------------------------------------------------

class PolishStream:
    """Incremental :func:`polish`. ``feed(delta)`` returns the text safe to emit now; ``finish()`` what is held."""

    def __init__(self, lex, cap: bool, scrub_names: bool = True) -> None:
        self.lex, self.cap, self.scrub_names = lex, bool(cap and lex is not None), scrub_names
        self._buf = ""
        self._held = ""
        self._substance = False        # a released sentence that is not a bare closer
        self._out: list = []           # everything released (the caller reads the tail for the next turn's cap)
        self.scrubbed = 0
        self.dropped = 0

    def _release(self, text: str) -> str:
        if text:
            self._out.append(text)
        return text

    def _sentence(self, sent: str) -> str:
        out = ""
        if self._held:                  # a question that is not the last sentence after all
            out += self._release(self._held)
            self._held = ""
        text = sent
        if self.scrub_names:
            body = sent.strip()
            lead = sent[: len(sent) - len(sent.lstrip())]
            tail = sent[len(sent.rstrip()):]
            clean = scrub(body)
            if clean != body:
                self.scrubbed += 1
                if clean:
                    text = lead + clean + tail
                elif self._out:
                    return out        # only plumbing, and something was already said: dropped
                # else: it would leave the reply empty - the original stays
        if self.cap and self._substance and text.rstrip().endswith("?") and is_closing_question(text, self.lex):
            self._held = text
            return out
        if not is_closing_question(text, self.lex):
            self._substance = True
        return out + self._release(text)

    def feed(self, delta: str) -> str:
        self._buf += delta or ""
        out = ""
        while True:
            end = _next_end(self._buf)
            if end < 0:
                return out
            sent, self._buf = self._buf[:end], self._buf[end:]
            out += self._sentence(sent)

    def finish(self) -> str:
        out = ""
        if self._buf:
            sent, self._buf = self._buf, ""
            out += self._sentence(sent)
        if self._held:
            self.dropped += 1
            self._held = ""
        return out

    def released(self) -> str:
        return "".join(self._out)


def polish(text: str, message: str = "", previous_ended_on_question: bool = False) -> str:
    """The whole reply, polished. Pure; the batch form of :class:`PolishStream`."""
    p = PolishStream(closer_lexicon_for(message), previous_ended_on_question)
    out = p.feed(text) + p.finish()
    return out if out.strip() else (text or "")


async def filter_stream(deltas: AsyncIterator[str], message: str, user_id: str = "", session_id: str = "") -> AsyncIterator[str]:
    """Wrap a brain delta stream. Mode ``off``: passed through untouched. ``aclose`` is forwarded so an early consumer exit closes the
    inner turn deterministically. A failure inside the polish passes the rest of the stream through as it came."""
    m = mode()
    stream: Optional[PolishStream] = None
    shadow_text: list = []
    if m != "off":
        try:
            stream = PolishStream(closer_lexicon_for(message), ended_on_question(user_id, session_id))
        except Exception:  # noqa: BLE001 - a polish is optional; the turn is not
            logger.warning("REPLY_POLISH setup failed; passing through", exc_info=True)
    emitted = False
    try:
        async for delta in deltas:
            if stream is None or delta.startswith(_SENTINELS):
                yield delta
                continue
            try:
                out = stream.feed(delta)
            except Exception:  # noqa: BLE001
                logger.warning("REPLY_POLISH failed; passing through", exc_info=True)
                out, stream = (stream._buf + stream._held + delta), None
            if m == "shadow":
                shadow_text.append(delta)
                yield delta
            elif out:
                emitted = True
                yield out
            elif stream is None:
                yield delta
        if stream is not None:
            try:
                tail = stream.finish()
            except Exception:  # noqa: BLE001
                logger.warning("REPLY_POLISH finish failed", exc_info=True)
                tail = stream._buf + stream._held
            if m != "shadow" and tail:
                emitted = True
                yield tail
            if m != "shadow" and not emitted and (stream.scrubbed or stream.dropped):
                logger.info("REPLY_POLISH emptied; the reply was only plumbing or a closer")
            final = "".join(shadow_text) if m == "shadow" else stream.released()
            try:
                note_reply(user_id, session_id, stream.released() if m != "shadow" else "".join(shadow_text))
            except Exception:  # noqa: BLE001
                pass
            if stream.scrubbed or stream.dropped:
                logger.info("REPLY_POLISH mode=%s scrubbed=%d dropped_closer=%d chars=%d", m, stream.scrubbed, stream.dropped, len(final))
    finally:
        aclose = getattr(deltas, "aclose", None)
        if aclose is not None:
            await aclose()  # type: ignore[misc]
