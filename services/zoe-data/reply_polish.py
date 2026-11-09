"""Two last-mile reply rules - flag ``ZOE_REPLY_POLISH=off|shadow|on`` (default ``on``).

1. **Plumbing words never reach the person.** QA 2026-11-08, "if I said 'turn on the kitchen light' what would you do?" -> "I'd activate
   the home ability and then use the home tool to turn on the kitchen light." The doctrine (labs/flue-zoe-brain-2x src/doctrines.ts) now
   says never to name tools, abilities or groups; this is the deterministic backstop. ``scrub`` removes "activate the <group> ability",
   "use the <group|tool> tool" and humanises a raw tool identifier (``show_calendar`` -> "show calendar"). A sentence the scrub empties is
   dropped.
2. **Not every reply ends on a question.** "Nearly every reply ends on a question" (QA round 4: about 80 percent of the feelings turns).
   When the PREVIOUS reply of this conversation already ended on a question, a generic closing question ("How are you feeling?", "Is there
   anything else?", "Does that make sense?", from ``lexicons_data/<lang>.json`` "mirror" generic_closers + "question_cap" closers) at the end of
   this one is dropped, provided a sentence of substance comes before it. A specific question ("Which list should I put bolts on?") is never
   a closer, and a reply that is only a closer is kept whole.

3. **On a feeling's turn the breaker is strict, and a stock line is not an answer** (QA round 7, guest g1 / g4). When the previous reply ended
   on a question and THIS turn is emotional (``emotional_turn_guard.is_emotional_turn``) and the person asked nothing, ANY trailing question
   is dropped (not only a generic closer), provided a sentence of substance comes before it: a person who is struggling is never asked on
   three turns running. A stock sentence ("I'm here to listen to whatever is on your mind", "feel free to share") is dropped when anything
   else is said; when it is the whole reply it is replaced by one short, true statement.

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
    r"\b(?:activate|activating|unlock|unlocking|enable|enabling|access|accessing|open|opening|load|loading|start|starting|invoke|invoking)\s+(?:the\s+)?" + _G + r"\s+(?:abilit(?:y|ies)|group|tools?)\b"
    r"(?:\s*,)?(?:\s+and)?(?:\s+then)?\s*", re.IGNORECASE)
_USE_RX = re.compile(
    r"\b(?:use|using|call|calling|run|running|trigger|triggering|invoke|invoking|access|accessing|need|needs)\s+(?:the\s+)?(?:" + _G + r"|"
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


#: a question that names a thing, a day or a number is a contextual follow-up, never a generic closer
#: ("How are you feeling about surgery?", "Does that work for you on Friday?")
_SPECIFIC_RX = re.compile(
    r"\d|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b|\b(?:about|on|for|with|at|over|regarding|around)\s+(?!it\b|that\b|this\b|all\b|things\b|everything\b|"
    r"now\b|today\b|tonight\b|you\b|me\b|us\b|the\s+(?:moment|day|rest)\b)\w", re.IGNORECASE)


def is_closing_question(sentence: str, lex) -> bool:
    import mirror_guard

    if lex is None or not mirror_guard.is_generic_closer(sentence, lex):
        return False
    return not (getattr(lex, "lang", "en") == "en" and _SPECIFIC_RX.search(sentence or ""))


#: (user id, session id) -> did the last reply end on a question? A bounded LRU; a dict write, never I/O.
_ended_q: "OrderedDict[tuple, bool]" = OrderedDict()


def ended_on_question(user_id: str, session_id: str) -> bool:
    return _ended_q.get(((user_id or "").strip(), session_id or ""), False)


def note_reply(user_id: str, session_id: str, text: str) -> None:
    key = ((user_id or "").strip(), session_id or "")
    _ended_q.pop(key, None)
    _ended_q[key] = (text or "").rstrip().endswith("?")
    import write_intent_gate

    write_intent_gate.note_reply(user_id, text)
    while len(_ended_q) > _MAX_SESSIONS:
        _ended_q.popitem(last=False)


def reset_state() -> None:
    _ended_q.clear()
    _feeling_thread.clear()


# -- a feeling's turn: the strict breaker and the stock line -------------------------------------------------

#: the sentence is a stock "I'm here to listen" line, not something said about what the person told Zoe
_STOCK_RX = re.compile(
    r"^\W*(?:i['\u2019]?m|i\s+am)\s+(?:always\s+|right\s+|just\s+)?(?:here|all\s+ears|listening)\b(?:\s+(?:to\s+listen|for\s+you|if\s+you|whenever|when\s+you|and\s+listening))?"
    r"|^\W*(?:please\s+)?feel\s+free\s+to\s+(?:share|talk|tell|open\s+up)\b|^\W*i['\u2019]?m\s+here\s+to\s+(?:listen|hear)\b"
    , re.IGNORECASE)
#: said only when a stock line was the WHOLE reply: a statement, no question, nothing invented
STOCK_REPLACEMENT = "That sounds like a lot to carry. You don't have to sort it all out at once."
_ASKS_RX = re.compile(
    r"\?|^\W*(?:(?:hey|ok|okay|so|and|um|zoe)\s+)*(?:what|why|how|who|where|when|which|can\s+you|could\s+you|do\s+you|are\s+you|is\s+it|"
    r"should\s+i|would\s+you|will\s+you|any\s+ideas)\b", re.IGNORECASE)


_FEEL_FREE_TAIL_RX = re.compile(
    r"^(?:\s+(?:(?:to\s+)?(?:me|us)|with\s+(?:me|us)|more|anytime|any\s*time|whenever(?:\s+you(?:'re|\s+are)?\s+ready|\s+you\s+like)?|"
    r"about\s+(?:it|that|anything|whatever|how\s+you\s+feel)(?:\s+(?:you\s+(?:like|want)|on\s+your\s+mind))?|"
    r"if\s+you\s+(?:want|like|need)(?:\s+to)?|whatever(?:\s+is)?(?:\s+on\s+your\s+mind)?))*[\s.!]*$", re.IGNORECASE)


def is_stock_line(sentence: str) -> bool:
    """A sentence that is ONLY a stock listening line: one with a second clause ("I'm here to listen, and that sounds hard") says something."""
    m = _STOCK_RX.match(sentence or "")
    if not m or re.search(r"[,;]|\b(?:and|but|that|because)\b", (sentence or "")[m.end():], re.IGNORECASE):
        return False
    if re.match(r"^\W*(?:please\s+)?feel\s+free\b", sentence or "", re.IGNORECASE):
        # an invitation to talk TO ZOE is stock; "feel free to tell your boss you need a day off" is advice and is kept
        return bool(_FEEL_FREE_TAIL_RX.match((sentence or "")[m.end():]))
    return True


def person_asked(message: str) -> bool:
    """The person's own turn asks Zoe something (a ``?`` or a question opener): she may answer with a question back."""
    return bool(_ASKS_RX.search((message or "").strip()))


#: (user id, session id) -> how many MORE turns still belong to a feeling just shared ("yeah" after "i snapped at my youngest and i feel
#: horrible" is part of that moment, though its own words carry no feeling). A bounded LRU; a dict write, never I/O.
_THREAD_TURNS = 2
_feeling_thread: "OrderedDict[tuple, int]" = OrderedDict()


def feeling_turn_policy(message: str, user_id: str, session_id: str) -> tuple:
    """``(strict_cap, stock)`` for this turn: both False off a feeling's turn (an emotional turn, or the short turns right after one).
    ``strict_cap`` also needs the last reply to have ended on a question and the person to have asked nothing. Never raises."""
    try:
        import emotional_turn_guard

        key = ((user_id or "").strip(), session_id or "")
        if emotional_turn_guard.is_emotional_turn(message):
            _feeling_thread.pop(key, None)
            _feeling_thread[key] = _THREAD_TURNS
        elif _feeling_thread.get(key, 0) > 0 and len((message or "").split()) <= 8 and not person_asked(message):
            _feeling_thread[key] -= 1
        else:
            _feeling_thread.pop(key, None)
            return False, False
        while len(_feeling_thread) > _MAX_SESSIONS:
            _feeling_thread.popitem(last=False)
        return bool(ended_on_question(user_id, session_id) and not person_asked(message)), True
    except Exception:  # noqa: BLE001 - optional polish
        return False, False


# -- the stream --------------------------------------------------------------------------------------------

class PolishStream:
    """Incremental :func:`polish`. ``feed(delta)`` returns the text safe to emit now; ``finish()`` what is held."""

    def __init__(self, lex, cap: bool, scrub_names: bool = True, strict_cap: bool = False, stock: bool = False) -> None:
        self.lex, self.scrub_names = lex, scrub_names
        self.strict_cap, self.stock = bool(strict_cap), bool(stock)
        self.cap = bool((cap and lex is not None) or strict_cap)
        self._buf = ""
        self._held = ""
        self._stock_held = False       # a stock line was seen and not released (dropped, or replaced when it was the whole reply)
        self._substance = False        # a released sentence that is not a bare closer
        self._out: list = []           # everything released (the caller reads the tail for the next turn's cap)
        self.scrubbed = 0
        self.dropped = 0

    def _release(self, text: str) -> str:
        if not self._out:
            text = text.lstrip()      # a dropped first sentence must not leave the next one's leading space
        if text:
            self._out.append(text)
        return text

    def _sentence(self, sent: str) -> str:
        if self._held and not sent.strip():
            return ""               # trailing whitespace is not "text follows": a held closing question stays held (and is dropped)
        text = sent
        if self.scrub_names:
            body = sent.strip()
            lead = sent[: len(sent) - len(sent.lstrip())]
            tail = sent[len(sent.rstrip()):]
            clean = scrub(body)
            if clean != body:
                self.scrubbed += 1
                if not clean:
                    return ""         # only plumbing: dropped (a plumbing name never reaches the person); a held question stays held
                text = lead + clean + tail
        if self._held and self.strict_cap and text.rstrip().endswith("?"):
            self._held += text          # a run of trailing questions stays held TOGETHER (scrubbed first): all dropped at the end, none released one by one
            return ""
        if self.stock and is_stock_line(text):
            self._stock_held = True          # never released: dropped when anything else is said, else replaced at finish
            return ""                        # ... and it is not "text that follows": a held question stays held, so the reply still ends on a statement
        out = ""
        if self._held:                  # a question that is not the last sentence after all: a sentence that SURVIVES follows it
            out += self._release(self._held)
            self._held = ""
        is_q = text.rstrip().endswith("?")
        cap_q = is_closing_question(text, self.lex) or (self.strict_cap and is_q)
        if self.cap and self._substance and is_q and cap_q:
            self._held = text
            return out
        if not cap_q:
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
            try:
                out += self._sentence(sent)
            except Exception:  # noqa: BLE001 - a bad sentence goes out as it came; the rest of the reply is unaffected
                logger.warning("REPLY_POLISH sentence failed; passed through", exc_info=True)
                out += self._release(self._held + sent)
                self._held = ""

    def finish(self) -> str:
        out = ""
        if self._buf:
            sent, self._buf = self._buf, ""
            out += self._sentence(sent)
        if self._held:
            self.dropped += 1
            self._held = ""
        if self._stock_held:
            self.dropped += 1
            self._stock_held = False
            if not self._out and not out:
                out += self._release(STOCK_REPLACEMENT)     # the stock line was ALL there was: say one true thing instead
        return out

    def released(self) -> str:
        return "".join(self._out)


def polish(text: str, message: str = "", previous_ended_on_question: bool = False, *, strict_cap: bool = False, stock: bool = False) -> str:
    """The whole reply, polished. Pure; the batch form of :class:`PolishStream`."""
    p = PolishStream(closer_lexicon_for(message), previous_ended_on_question, strict_cap=strict_cap, stock=stock)
    return p.feed(text) + p.finish()


async def filter_stream(deltas: AsyncIterator[str], message: str, user_id: str = "", session_id: str = "") -> AsyncIterator[str]:
    """Wrap a brain delta stream. Mode ``off``: passed through untouched. ``aclose`` is forwarded so an early consumer exit closes the
    inner turn deterministically. A failure inside the polish passes the rest of the stream through as it came."""
    m = mode()
    stream: Optional[PolishStream] = None
    shadow_text: list = []
    if m != "off":
        try:
            strict, stock = feeling_turn_policy(message, user_id, session_id)
            stream = PolishStream(closer_lexicon_for(message), ended_on_question(user_id, session_id), strict_cap=strict, stock=stock)
        except Exception:  # noqa: BLE001 - a polish is optional; the turn is not
            logger.warning("REPLY_POLISH setup failed; passing through", exc_info=True)
    emitted = False
    off_text: list = []
    try:
        async for delta in deltas:
            if stream is None or delta.startswith(_SENTINELS):
                if m == "off" and not delta.startswith(_SENTINELS):
                    off_text.append(delta)
                yield delta
                continue
            try:
                out = stream.feed(delta)
            except Exception:  # noqa: BLE001
                logger.warning("REPLY_POLISH failed; passing through", exc_info=True)
                out, stream = (stream._held + stream._buf), None
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
        if m == "off":
            # polishing is off, but the write gate still needs to know whether Zoe's reply OFFERED a calendar write ("want me to add
            # those?") so a bare "yes please" is an ask - recorded here, independent of the optional transformation
            try:
                import write_intent_gate

                write_intent_gate.note_reply(user_id, "".join(off_text))
            except Exception:  # noqa: BLE001
                pass
    finally:
        aclose = getattr(deltas, "aclose", None)
        if aclose is not None:
            await aclose()  # type: ignore[misc]
