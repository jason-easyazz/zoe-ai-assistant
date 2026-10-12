"""Drop a reflective-listening opener from the front of a reply - flag ``ZOE_MIRROR_GUARD=off|shadow|on`` (default ``shadow``).

THE FAILURE (owner, 2026-10-12): "We really need to teach you how to talk to people ... it feels more natural rather than you just
regurgitating what I'm doing." In one live evening 6 of 14 conversational brain replies opened "It sounds like ..." / "That sounds
like ...", and most ended on a generic question ("How are you holding up?"). The 4B has one empathy move - restate the feeling the
person just stated - and the soul, the voice doctrine and the emotional doctrines all say "warm" and "acknowledge first" without ever
saying what a reply is FOR. The prompt side of the fix is the manner block's text (``manner_block.py``, ``lexicons_data/<lang>.json``
"manner"). This module is the deterministic backstop for the replies the prompt does not reach.

WHAT IT DOES (``trim`` is the pure form, ``MirrorStream`` the incremental one; the two are proven equal in tests for every way of
cutting a reply into deltas):

* the reply's FIRST sentence is a *mirror sentence* when, after any filler ("oh", "wow"), it starts with one of the language's mirror
  formulas ("it sounds like", "i hear that", "that must be", ...), is at most ``MAX_MIRROR_WORDS`` long, and is not a positive
  reaction ("sounds like a great plan");
* up to ``MAX_DROPPED`` leading mirror sentences are dropped, but ONLY when something of substance follows - a sentence that is
  neither another mirror sentence nor a generic closer ("How are you feeling?"). A reply that is only a mirror, or a mirror plus a
  generic question, is returned UNCHANGED: trimming it would leave a reply that is worse (a bare generic question) or empty;
* no second brain call. Regenerating with a nudge costs a full time-to-first-token again (6.6 s cold, 0.9 s warm in the 2026-10-12
  voice log); the trim costs nothing, so it is the only repair the voice path gets.

THE WORDS ARE DATA (``lexicons_data/<lang>.json`` "mirror": fillers, openers, intensifiers, positive_exempt, generic_closers). English
and Spanish exist. A language with no entry gets NO guard (inert, it is never guessed from English). The language is the one of the
user's own words (``lexicons.detect``).

MODES. ``off``: nothing wraps the stream. ``shadow`` (the default): the stream passes through byte-identical and one label-only log
line says what ``on`` would have done (no reply text is ever logged). ``on``: the trim above. It is not adults-only: it removes a
formula, it adds no behaviour, and the distress hand-off answers before the brain (and so before this filter) in every mode.

Stdlib only (the lexicon loader aside). Never raises into the stream.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import AsyncIterator, Optional

from narration_filter import _next_end  # the repo's one sentence splitter (abbreviations and initials do not end a sentence)
from typed_env import env_str

logger = logging.getLogger(__name__)

FLAG = "ZOE_MIRROR_GUARD"
LEXICON_KEY = "mirror"
MAX_DROPPED = 2
#: a "mirror sentence" longer than this is carrying content of its own; it is left alone
MAX_MIRROR_WORDS = 30
#: the part of a mirror sentence after a boundary ("..., but a walk helps") must run this many words to be kept as the reply's start
MIN_TAIL_WORDS = 3
#: a generic closer may run on this many words past its formula and still be generic ("how are you feeling right now")
CLOSER_SLACK_WORDS = 2
#: undecided after this many buffered characters => release the reply untouched (the guard never holds a reply hostage)
PROBE_LIMIT = 420
_SENTINELS = ("__TOOL__:", "__THINKING__:")


def mode() -> str:
    """``shadow`` (default; unset or unknown) | ``on`` | ``off``. Read per call, so a flip needs no restart."""
    raw = env_str("ZOE_MIRROR_GUARD").lower()   # literal on purpose: tools/audit/flag_inventory.py greps it
    if raw in ("0", "false", "no", "off", "disabled"):
        return "off"
    if raw in ("1", "true", "yes", "on", "enforce"):
        return "on"
    return "shadow"


# -- the words -------------------------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def norm_words(text: str) -> list[str]:
    """Lower-cased, accent-stripped words (apostrophes kept inside a word: ``it's``)."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower().replace("’", "'").replace("‘", "'")
    return _WORD_RE.findall(t)


@dataclass(frozen=True)
class Lexicon:
    lang: str
    fillers: frozenset
    openers: tuple
    intensifiers: frozenset
    positive: tuple
    closers: tuple
    contrast: Optional["re.Pattern[str]"]


def _phrases(items) -> tuple:
    out = []
    for it in items or []:
        w = tuple(norm_words(str(it)))
        if w:
            out.append(w)
    return tuple(out)


def _contrast_rx(items) -> Optional["re.Pattern[str]"]:
    """The lexicon's contrast words ("but", "however") as one whole-word pattern over the RAW sentence, or None."""
    parts = [r"\s+".join(re.escape(w) for w in str(it).split()) for it in items or [] if str(it).strip()]
    if not parts:
        return None
    return re.compile(r"(?<![^\W_])(?:" + "|".join(parts) + r")(?![^\W_])", re.IGNORECASE)


@lru_cache(maxsize=None)
def lexicon(lang: str) -> Optional[Lexicon]:
    """The mirror lexicon of ``lang``, or None when that language has none (the guard is then inert)."""
    import lexicons

    entry = (lexicons.load(lang) or {}).get(LEXICON_KEY)
    if not isinstance(entry, dict) or not entry.get("openers"):
        return None
    return Lexicon(
        lang=(lang or "").lower(),
        fillers=frozenset(w for p in _phrases(entry.get("fillers")) for w in p),
        openers=_phrases(entry.get("openers")),
        intensifiers=frozenset(w for p in _phrases(entry.get("intensifiers")) for w in p),
        positive=_phrases(entry.get("positive_exempt")),
        closers=_phrases(entry.get("generic_closers")),
        contrast=_contrast_rx(entry.get("contrast")),
    )


def lexicon_for_message(message: str) -> Optional[Lexicon]:
    import lexicons

    return lexicon(lexicons.detect(message or "") or "en")


# -- classification of one sentence ----------------------------------------------------------------------

def _starts_with(words: list, phrases: tuple, at: int = 0) -> Optional[tuple]:
    for p in phrases:
        if tuple(words[at:at + len(p)]) == p:
            return p
    return None


def _match(sentence: str, lex: Lexicon) -> Optional[tuple]:
    """``(formula, words consumed by the fillers and the formula)`` when ONE sentence is a mirror sentence, else None."""
    words = norm_words(sentence)
    if not words or len(words) > MAX_MIRROR_WORDS:
        return None
    skip = 0
    while True:
        op = _starts_with(words, lex.openers, skip)
        if op is not None:
            after = [w for w in words[skip + len(op):][:5] if w not in lex.intensifiers]   # "a really lovely idea" = "a lovely idea"
            if _starts_with(after, lex.positive) is not None:
                return None
            return op, skip + len(op)
        if skip < len(words) and words[skip] in lex.fillers:
            skip += 1
            continue
        return None


def mirror_opener(sentence: str, lex: Lexicon) -> Optional[tuple]:
    """The mirror formula ONE sentence opens with (after any fillers), or None. A positive reaction ("sounds like a great plan") and a
    sentence longer than ``MAX_MIRROR_WORDS`` are not mirrors. Pure."""
    m = _match(sentence, lex)
    return m[0] if m else None


_RAW_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_PUNCT_BOUNDARY_RE = re.compile(r":(?!\d)|;|—|–|\s-\s")


def mirror_tail(sentence: str, lex: Lexicon) -> str:
    """For a mirror sentence that carries more than the mirror ("I hear you: say it slower." / "That sounds hard, but a walk helps."):
    the part after its first boundary (a colon, semicolon, dash, or one of the lexicon's contrast words) after the formula,
    capitalised - when it has at least ``MIN_TAIL_WORDS`` words and is not itself a mirror sentence. "" for a pure mirror sentence."""
    m = _match(sentence, lex)
    if m is None:
        return ""
    raw = list(_RAW_WORD_RE.finditer(sentence))
    aligned = sum(len(norm_words(r.group())) for r in raw) == len(norm_words(sentence)) and len(raw) >= m[1]
    start = raw[m[1] - 1].end() if aligned else 0
    ends = [bm.end() for bm in (_PUNCT_BOUNDARY_RE.search(sentence, start), lex.contrast.search(sentence, start) if lex.contrast else None) if bm]
    if not ends:
        return ""
    tail = sentence[min(ends):].lstrip(" ,;:-—–")
    if len(norm_words(tail)) < MIN_TAIL_WORDS or _match(tail, lex) is not None:
        return ""
    return _capitalise(tail)


def is_mirror_sentence(sentence: str, lex: Lexicon) -> bool:
    return mirror_opener(sentence, lex) is not None


def is_generic_closer(sentence: str, lex: Lexicon) -> bool:
    """A question that asks nothing in particular ("How are you holding up?", "Want to talk about it?"). Pure."""
    s = (sentence or "").strip()
    if not s.endswith("?"):
        return False
    words = norm_words(s)
    skip = 0
    while skip <= len(words):
        p = _starts_with(words, lex.closers, skip)
        # "How are you feeling about the interview?" is specific; "How are you feeling right now?" is not
        if p is not None and len(words) - skip <= len(p) + CLOSER_SLACK_WORDS:
            return True
        if skip < len(words) and words[skip] in lex.fillers:
            skip += 1
            continue
        return False
    return False


def could_open(buf: str, lex: Lexicon) -> bool:
    """False only when NO continuation of ``buf`` can make its first sentence a mirror sentence (so the stream may release it now);
    True while it still could - an unfinished last word counts if it starts any word the formulas want."""
    words = norm_words(buf)
    growing = bool(re.search(r"[A-Za-z0-9'’]$", buf))
    n = len(words)
    i = 0
    while i < n and not (i == n - 1 and growing) and words[i] in lex.fillers:
        i += 1
        # a filler may ALSO be the start of an opener ("so you're saying"): both readings are tried below
    for skip in range(0, i + 1):
        rest = words[skip:]
        if not rest:
            return True
        for op in lex.openers:
            if len(rest) > len(op):
                if tuple(rest[:len(op)]) == op:
                    return True
                continue
            if all(rest[k] == op[k] for k in range(len(rest) - 1)) and (
                    op[len(rest) - 1] == rest[-1] or (growing and op[len(rest) - 1].startswith(rest[-1]))):
                return True
    last = words[-1] if words else ""
    return bool(growing and last and any(f.startswith(last) for f in lex.fillers) and i == n - 1)


# -- the decision ----------------------------------------------------------------------------------------

def _capitalise(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
        if ch.isdigit():
            break
    return text


@dataclass(frozen=True)
class Decision:
    """``text``: what to emit. ``opener``: the reply opened with a mirror formula. ``trimmed``: ``text`` differs from the reply."""
    text: str
    opener: bool
    trimmed: bool


def resolve(buf: str, lex: Lexicon, final: bool) -> Optional[Decision]:
    """The decision for the reply so far, or None while more text is needed. ``final``: the reply is complete, always decides.
    Pure; ``trim`` and the stream form both go through here."""
    s = buf.lstrip()
    if not s:
        return Decision(buf, False, False) if final else None
    pos = drops = 0
    while True:
        seg = s[pos:].lstrip()
        offset = len(s) - len(seg)
        if not seg:
            return Decision(buf, drops > 0, False) if final else None   # nothing after the mirror yet / ever: keep the reply
        end = _next_end(seg)
        if end < 0:
            if not final:
                if drops == 0 and not could_open(seg, lex):
                    return Decision(buf, False, False)
                if len(s) > PROBE_LIMIT:
                    return Decision(buf, drops > 0, False)
                return None
            end = len(seg)
        sentence = seg[:end]
        if drops < MAX_DROPPED and is_mirror_sentence(sentence, lex):
            tail = mirror_tail(sentence, lex)
            if tail:   # "I hear you: say it slower." - the sentence carries its own substance; keep it, drop the formula's clause
                return Decision(_capitalise(tail + seg[end:]), True, True)
            drops += 1
            pos = offset + end
            continue
        if drops == 0:
            return Decision(buf, False, False)
        if is_generic_closer(sentence, lex):
            return Decision(buf, True, False)      # only a generic question follows: trimming would leave something worse
        return Decision(_capitalise(s[offset:]), True, True)


def trim(text: str, lang: str = "en") -> str:
    """The reply with a leading mirror sentence dropped when something of substance follows it; unchanged otherwise. Pure."""
    lex = lexicon(lang)
    if lex is None or not (text or "").strip():
        return text or ""
    d = resolve(text, lex, final=True)
    return d.text if d else (text or "")


def opens_with_mirror(text: str, lang: str = "en") -> bool:
    """Does the reply's first sentence open with a mirror formula (whatever follows it)? The metric the A/B counts. Pure."""
    lex = lexicon(lang)
    if lex is None:
        return False
    s = (text or "").lstrip()
    if not s:
        return False
    end = _next_end(s)
    return is_mirror_sentence(s[: end if end >= 0 else len(s)], lex)


# -- the stream ------------------------------------------------------------------------------------------

class MirrorStream:
    """Incremental :func:`trim`. ``feed(delta)`` returns the text safe to emit now ("" while the first sentences are still arriving);
    ``finish()`` returns whatever is held. The concatenation of every return equals ``trim`` of the concatenated input, for every way
    of cutting the reply into deltas (tests). ``shadow=True`` holds nothing and changes nothing: it only records the verdict."""

    def __init__(self, lex: Lexicon, shadow: bool = False) -> None:
        self.lex, self.shadow = lex, shadow
        self._buf = ""
        self._passing = False
        self.decision: Optional[Decision] = None

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        if self.shadow:
            if self.decision is None:
                self._buf += delta
                self.decision = resolve(self._buf, self.lex, final=False)
            return delta
        if self._passing:
            return delta
        self._buf += delta
        d = resolve(self._buf, self.lex, final=False)
        if d is None:
            return ""
        self.decision, self._passing, self._buf = d, True, ""
        return d.text

    def finish(self) -> str:
        if self.shadow:
            if self.decision is None and self._buf:
                self.decision = resolve(self._buf, self.lex, final=True)
            return ""
        if self._passing:
            return ""
        d = resolve(self._buf, self.lex, final=True)
        self.decision, self._passing, self._buf = d, True, ""
        return d.text if d else ""


async def filter_stream(deltas: AsyncIterator[str], message: str) -> AsyncIterator[str]:
    """Wrap a brain delta stream: text deltas pass through a :class:`MirrorStream`; tool / thinking sentinels pass untouched and at
    once. Mode ``off``, or a language with no mirror lexicon: the stream is passed through untouched. ``aclose`` is forwarded so an
    early consumer exit closes the inner turn deterministically."""
    m = mode()
    try:
        lex = lexicon_for_message(message) if m != "off" else None
    except Exception:  # noqa: BLE001 - a guard is optional; the turn is not
        logger.warning("MIRROR_GUARD lexicon failed; passing through", exc_info=True)
        lex = None
    stream = MirrorStream(lex, shadow=(m == "shadow")) if lex is not None else None
    try:
        async for delta in deltas:
            if stream is None or delta.startswith(_SENTINELS):
                yield delta
                continue
            try:
                out = stream.feed(delta)
            except Exception:  # noqa: BLE001
                logger.warning("MIRROR_GUARD failed; passing through", exc_info=True)
                out = delta if stream.shadow else stream._buf + delta   # whatever it was holding goes out too
                stream = None
            if out:
                yield out
        if stream is not None:
            try:
                tail = stream.finish()
            except Exception:  # noqa: BLE001
                logger.warning("MIRROR_GUARD finish failed", exc_info=True)
                tail = stream._buf if not stream.shadow else ""
            if tail:
                yield tail
            _log(m, stream)
    finally:
        aclose = getattr(deltas, "aclose", None)
        if aclose is not None:
            await aclose()  # type: ignore[misc]


def _log(m: str, stream: MirrorStream) -> None:
    d = stream.decision
    if d is None:
        return
    logger.info("MIRROR_GUARD mode=%s lang=%s opener=%d would_trim=%d", m, stream.lex.lang, int(d.opener), int(d.trimmed))
