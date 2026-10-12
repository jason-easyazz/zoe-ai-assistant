"""What a finished panel utterance LOOKS like, decided from its transcript.

Two questions the panel daemon cannot answer itself, because it has no transcript at
endpoint time (STT runs on the Jetson, and asking it per turn would add a Moonshine
pass to every fast turn):

1. **Did the speaker stop mid-clause?** A transcript ending in a continuation word
   ("...we could walk down to the market and") was cut by the endpointer's pause, not by the
   end of the thought. ``ends_incomplete`` says so; ``gate_frame`` then answers the turn
   with an ``incomplete`` done frame INSTEAD of running the brain, and the daemon listens
   a short extra window and re-sends one merged clip (``continued``) — one turn, never
   two. The hold happens before any brain work, so nothing is written twice.

2. **Was a follow-up-window utterance meant for Zoe at all?** Follow-up (5 s) and
   conversation (12 s) windows take speech with no wake word, so the owner talking to
   someone else is accepted as a command. ``addressedness`` is a deliberately
   conservative rule over the transcript; ``gate_frame`` answers a side-speech turn with
   a ``skipped`` done frame (silence, no brain, no memory write). Behind
   ``ZOE_FOLLOWUP_ADDRESSED=off|on`` (default ``off``; ``strict`` is a third, harder
   level): this is a product behaviour, so it ships dark and the owner decides.

Pure and dependency-free (stdlib only). All language data lives in ``_LANGS`` so a new
language is data, not code. Both rules apply the union of the configured languages: the
panel's STT does not report a language, and the words involved do not collide (the
single-letter Spanish conjunctions never end an English transcript).

Daemon half: ``scripts/setup/zoe_voice_daemon.py`` (``_turn_with_monitor``). Wire fields
the daemon adds to a /turn_stream payload (all optional, ignored by an old server):
``window`` ("followup" | "conversation"; absent on a wake turn and on a barge-in seed),
``prev_reply_question`` (Zoe's previous reply ended in a question), ``can_continue`` (the
daemon will honour an ``incomplete`` frame) and ``continued`` (this clip is already the
merged retry — never hold it again).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)

FOLLOWUP_FLAG = "ZOE_FOLLOWUP_ADDRESSED"
CONTINUATION_FLAG = "ZOE_VOICE_CONTINUATION_HOLD"

WINDOWS = ("followup", "conversation")

# A transcript shorter than this never holds: "and?" / "so" / "Yes and" are answers,
# not a thought cut in half.
_MIN_HOLD_WORDS = 3
# A weak continuation word ("that", "which", "que") also ends complete utterances ("what
# was that"), so it only counts in a long, non-question utterance.
_MIN_WEAK_HOLD_WORDS = 6
# Up to this many words in a follow-up window are always answered: "yes please",
# "the second one" — too short to judge and cheap to answer.
_SHORT_FOLLOWUP_WORDS = 3


@dataclass(frozen=True)
class _Lang:
    # Words that, as the LAST word, mean the clause is unfinished.
    strong_continuers: frozenset
    # Words that sometimes end a complete utterance too; held only in a long non-question.
    weak_continuers: frozenset
    # First words of a question (wh-words and subject-aux inversion).
    interrogatives: frozenset
    # First words of a command (imperative verbs).
    imperatives: frozenset
    # First words of an acknowledgement / answer / closing aimed at the last reply.
    acknowledgements: frozenset
    # Words to skip before judging the first word ("and what time", "okay play ...").
    lead_ins: frozenset
    # Capitalised words that follow a comma but are NOT a person addressed.
    not_names: frozenset


def _fs(*words: str) -> frozenset:
    return frozenset(words)


_LANGS: Mapping[str, _Lang] = {
    "en": _Lang(
        strong_continuers=_fs(
            "and", "but", "because", "cause", "cos", "or", "if", "unless", "although",
            "whether", "until", "plus", "um", "uh", "er", "erm",
        ),
        weak_continuers=_fs("that", "which"),
        interrogatives=_fs(
            "what", "whats", "what's", "when", "whens", "where", "wheres", "where's",
            "who", "whos", "who's", "whom", "whose", "why", "how", "hows", "how's",
            "which", "can", "could", "will", "would", "do", "does", "did", "is", "are",
            "am", "was", "were", "should", "shall", "may", "might", "have", "has",
            "isnt", "isn't", "arent", "aren't", "dont", "don't", "cant", "can't",
        ),
        imperatives=_fs(
            "play", "pause", "resume", "stop", "skip", "next", "previous", "turn",
            "switch", "set", "add", "remove", "delete", "remind", "tell", "show", "open",
            "close", "call", "text", "send", "read", "find", "search", "look", "give",
            "make", "put", "take", "start", "cancel", "snooze", "mute", "unmute", "lower",
            "raise", "increase", "decrease", "dim", "brighten", "change", "check", "list",
            "repeat", "say", "explain", "help", "let", "let's", "lets", "order", "buy",
            "schedule", "book", "save", "note", "remember", "forget", "ask", "bring",
            "get", "go", "stay", "wait", "hold", "keep", "try", "use", "run", "shut",
            "lock", "unlock", "queue", "shuffle", "dont", "don't",
        ),
        acknowledgements=_fs(
            "yes", "yeah", "yep", "yup", "no", "nope", "nah", "okay", "ok", "sure",
            "thanks", "thank", "cheers", "please", "great", "perfect", "nice", "cool",
            "awesome", "never", "nevermind", "louder", "quieter", "again", "more", "less",
            "sorry", "correct", "right", "exactly",
        ),
        lead_ins=_fs(
            "and", "but", "so", "also", "now", "then", "well", "hey", "hi", "hello",
            "ok", "okay", "please", "actually", "just", "oh", "um", "uh",
        ),
        not_names=_fs(
            "i", "the", "a", "an", "thanks", "thank", "please", "sorry", "okay", "ok",
            "yes", "no", "sir", "maam", "madam", "monday", "tuesday", "wednesday",
            "thursday", "friday", "saturday", "sunday", "january", "february", "march",
            "april", "may", "june", "july", "august", "september", "october", "november",
            "december", "english", "spanish", "again", "tonight", "tomorrow", "today",
            "now", "then", "later", "first", "next", "last", "finally",
        ),
    ),
    "es": _Lang(
        strong_continuers=_fs(
            "y", "e", "o", "u", "ni", "pero", "porque", "aunque", "sino", "pues", "mientras",
        ),
        weak_continuers=_fs("que", "si", "como", "cuando", "donde", "cual"),
        interrogatives=_fs(
            "qué", "quién", "quien", "quiénes", "cuándo", "dónde", "cómo", "cuál",
            "cuáles", "cuánto", "cuántos", "cuántas", "puedes", "puedo", "podrías",
            "podrias", "podemos", "sabes", "hay", "está", "tienes", "tiene", "quieres",
            "debo",
        ),
        imperatives=_fs(
            "pon", "ponme", "pone", "apaga", "apagar", "enciende", "encender", "sube",
            "baja", "pausa", "para", "detén", "deten", "siguiente", "anterior",
            "recuérdame", "recuerdame", "dime", "muestra", "abre", "cierra", "llama",
            "envía", "envia", "busca", "añade", "anade", "agrega", "quita", "elimina",
            "cambia", "repite", "ayuda", "ayúdame", "ayudame", "cancela", "reproduce",
            "toca", "lee", "dame", "haz", "empieza", "continúa", "continua", "espera",
        ),
        acknowledgements=_fs(
            "sí", "si", "no", "vale", "claro", "gracias", "perfecto", "genial", "bueno",
            "bien", "exacto", "correcto", "porfa", "perdón", "perdon",
        ),
        lead_ins=_fs("y", "pero", "entonces", "bueno", "oye", "hola", "pues", "ahora"),
        not_names=_fs(
            "yo", "gracias", "perdón", "perdon", "sí", "no", "lunes", "martes",
            "miércoles", "miercoles", "jueves", "viernes", "sábado", "sabado", "domingo",
            "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
            "septiembre", "octubre", "noviembre", "diciembre", "mañana", "manana", "hoy",
            "ahora", "luego", "inglés", "ingles", "español", "espanol",
        ),
    ),
}

# Zoe's own name, as the STT writes it. Naming her is always addressing her.
_ZOE_NAME = re.compile(r"\bzo[eë]y?\b", re.IGNORECASE)

# ", Name" closing a clause — the shape of a vocative ("..., Name." / "..., Name?" / ", Name"
# at the very end). It is almost always a person, but "weather in Lisbon, Portugal?" has
# the same shape, so a name never decides alone — see addressedness().
_VOCATIVE = re.compile(r",\s+([A-ZÁÉÍÓÚÑ][A-Za-zÀ-ÿ'’-]+)\s*(?:[.!?…]|$)")
# "Hey Name ..." / "Hola Name ..." — a greeting aimed at someone.
_GREETED = re.compile(r"^\s*(?:hey|hi|hello|hola|oye)[,\s]+([A-ZÁÉÍÓÚÑ][A-Za-zÀ-ÿ'’-]+)\b")

_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_TRAILING_JUNK = " \t\r\n.,;:…-–—\"'”’)"


def _langs(languages: tuple[str, ...] | None) -> list[_Lang]:
    return [_LANGS[k] for k in (languages or tuple(_LANGS)) if k in _LANGS]


def _words(text: str) -> list[str]:
    return [w.replace("’", "'").lower() for w in _WORD.findall(text or "")]


def _union(attr: str, langs: list[_Lang]) -> frozenset:
    out: frozenset = frozenset()
    for lang in langs:
        out |= getattr(lang, attr)
    return out


def _first_content_word(words: list[str], lead_ins: frozenset) -> str:
    """The first word that is not a lead-in ("and what time" -> "what"); at most two skipped."""
    for skipped, word in enumerate(words):
        if word in lead_ins and skipped < 2 and skipped + 1 < len(words):
            continue
        return word
    return ""


def ends_incomplete(text: str, languages: tuple[str, ...] | None = None) -> bool:
    """True when the transcript stops on a word that means the clause is unfinished.

    Conservative: a ``?`` or ``!`` ending is complete; fewer than three words is an answer;
    a weak word ("that", "which", "que") only counts in a long statement that does not open
    like a question ("what was that" is complete).
    """
    s = (text or "").strip()
    if not s or s[-1] in "?!":
        return False
    words = _words(s.rstrip(_TRAILING_JUNK))
    if len(words) < _MIN_HOLD_WORDS:
        return False
    langs = _langs(languages)
    last = words[-1]
    if last in _union("strong_continuers", langs):
        return True
    if last in _union("weak_continuers", langs) and len(words) >= _MIN_WEAK_HOLD_WORDS:
        first = _first_content_word(words, _union("lead_ins", langs))
        return first not in _union("interrogatives", langs)
    return False


@dataclass(frozen=True)
class Verdict:
    addressed: bool
    reason: str


def _names_other_person(text: str, langs: list[_Lang]) -> bool:
    not_names = _union("not_names", langs)
    for rx in (_VOCATIVE, _GREETED):
        for m in rx.finditer(text or ""):
            candidate = m.group(1).replace("’", "'").lower()
            if candidate not in not_names and not _ZOE_NAME.fullmatch(candidate):
                return True
    return False


def _has_shape(text: str, words: list[str], langs: list[_Lang]) -> str:
    """The reason a question / command / acknowledgement shape was found, else ''."""
    stripped = (text or "").strip()
    if stripped.endswith(("?", "？")) or stripped.startswith("¿"):
        return "question"
    first = _first_content_word(words, _union("lead_ins", langs))
    if first in _union("interrogatives", langs):
        return "question"
    if first in _union("imperatives", langs):
        return "command"
    if first in _union("acknowledgements", langs):
        return "acknowledgement"
    return ""


def addressedness(text: str, window: str | None, *, prev_reply_question: bool = False,
                  strict: bool = False, languages: tuple[str, ...] | None = None) -> Verdict:
    """Was this utterance meant for Zoe? Only a window turn is ever judged.

    Default (``on``) acts on POSITIVE evidence only: the utterance names another person as
    the addressee (a vocative: ", Name." / "Hey Name") and has no question / command shape
    and does not name Zoe. A plain statement with no such evidence is let through — in an
    open follow-up the owner often keeps talking TO Zoe in statements.
    ``strict`` additionally requires a 5 s ``followup`` utterance to look like a question,
    command or acknowledgement, to answer a question Zoe just asked, or to be a few words
    long; a plain statement is then dropped even with no vocative. It is more protective
    against side speech and will also drop a real statement aimed at Zoe.
    Any ``window`` other than followup / conversation (wake word, barge-in seed, none) is
    addressed by definition.
    """
    if window not in WINDOWS:
        return Verdict(True, "wake")
    langs = _langs(languages)
    words = _words(text)
    if not words:
        return Verdict(True, "empty")
    if _ZOE_NAME.search(text):
        return Verdict(True, "name")
    shape = _has_shape(text, words, langs)
    vocative = _names_other_person(text, langs)
    if vocative and not shape:
        return Verdict(False, "vocative")
    if strict and window == "followup":
        if prev_reply_question:
            return Verdict(True, "answer")
        if shape:
            return Verdict(True, shape)
        if len(words) <= _SHORT_FOLLOWUP_WORDS:
            return Verdict(True, "short")
        return Verdict(False, "no_shape")
    return Verdict(True, window)


def followup_addressed_mode() -> str:
    """``ZOE_FOLLOWUP_ADDRESSED``: ``off`` (default) | ``on`` | ``strict``, read per call.
    Anything unrecognised is ``off`` — a typo never starts dropping speech."""
    raw = os.environ.get("ZOE_FOLLOWUP_ADDRESSED", "off").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "on"
    if raw == "strict":
        return "strict"
    return "off"


def continuation_hold_enabled() -> bool:
    """Server kill switch for the trailing-conjunction hold (default on). The daemon only
    asks for a hold at all when its own ``ZOE_CONTINUE_WINDOW_S`` is above zero."""
    raw = os.environ.get("ZOE_VOICE_CONTINUATION_HOLD", "on").strip().lower()
    return raw not in ("0", "false", "no", "off")


def gate_frame(payload: Mapping[str, Any] | None, transcript: str, *,
               speculative: bool = False) -> dict | None:
    """The done frame that answers this turn WITHOUT running the brain, or None to proceed.

    ``skipped`` (not addressed) wins over ``incomplete``: a side-speech fragment is dropped,
    not held. A speculative turn is never gated (its own protocol owns the verdict).
    """
    p = payload or {}
    text = (transcript or "").strip()
    if speculative or not text:
        return None
    mode = followup_addressed_mode()
    if mode != "off":
        verdict = addressedness(text, p.get("window"), strict=(mode == "strict"),
                                prev_reply_question=bool(p.get("prev_reply_question")))
        if not verdict.addressed:
            return {"done": True, "reply": "", "skipped": "not_addressed", "reason": verdict.reason}
    if (p.get("can_continue") and not p.get("continued")
            and continuation_hold_enabled() and ends_incomplete(text)):
        return {"done": True, "reply": "", "incomplete": True}
    return None
