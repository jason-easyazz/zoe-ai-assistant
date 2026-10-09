"""Strip a narrated lookup from the front of a reply — flag-dark ``ZOE_STRIP_NARRATION``.

Live 2026-10-04: "Who am I" was answered "I'll check what I've got on file about
you." followed by the actual answer. The 4B brain announces the recall_memory
call in text BEFORE making it, and both the announcement and the answer reach the
user. A human assistant just answers. The source rules (the recall block's
"answer directly" line, ``zoe_flue_client._recall_block_open``) ask the model not
to; this is the deterministic backstop: drop a LEADING sentence that only
announces a lookup when a real answer follows it.

Kept (never stripped):
  * a reply that is ONLY the narration sentence (nothing follows — dropping it
    would leave an empty reply);
  * a sentence that carries a deferral / promise ("…and get back to you", "I'll
    check with you tomorrow", "let you know", "once it's done"): that is a real
    commitment, not narration of a lookup whose result is about to be given;
  * anything not at the very start of the reply, and anything that is not one of
    the lookup-announcing shapes below.

At most two leading narration sentences are dropped. Stdlib only. The stream
form buffers just far enough to see the first sentence, passes tool/thinking
sentinels straight through, and never reorders or loses text.
"""
from __future__ import annotations

import os
import re
from typing import AsyncIterator, Optional

ENV_FLAG = "ZOE_STRIP_NARRATION"
_MAX_DROPPED = 2
_MAX_SENTENCE_CHARS = 140  # a longer first "sentence" is content, not an announcement
_PROBE_LIMIT = 200  # decide by here even if no sentence end has arrived


def enabled() -> bool:
    """``ZOE_STRIP_NARRATION`` — default OFF. Per-call env read."""
    return (os.environ.get("ZOE_STRIP_NARRATION") or "").strip().lower() in {"1", "true", "yes", "on"}


_FILLER = r"(?:(?:ok(?:ay)?|sure|alright|right|hmm+|um+|well|so|yes|yeah|of\s+course|certainly|absolutely)[\s,.!…-]+)?"
_LEAD = (
    r"(?:i(?:['’]ll|\s+will|['’]m\s+going\s+to|\s+am\s+going\s+to|\s+shall|['’]ll\s+just|\s+will\s+just|"
    r"['’]m\s+just\s+going\s+to)"
    r"|let\s+me(?:\s+just)?|allow\s+me\s+to|let['’]s|"
    r"(?:one|just\s+a)\s+(?:moment|sec(?:ond)?|tick)[\s,.!…-]+(?:while\s+i|as\s+i|i['’]ll|let\s+me)|"
    r"give\s+me\s+(?:a\s+)?(?:moment|sec(?:ond)?)\s+to)\s+"
)
_ADV = r"(?:(?:quickly|just|first|now|also|go\s+ahead\s+and|try\s+to)\s+)*"
_VERB = (
    r"(?:check(?!\s+(?:with|in\s+with)\s+you\b)|have\s+a\s+look|take\s+a\s+look|"
    r"look(?!\s+(?:forward|like|good|great|out|after|away|down)\b)|pull\s+up|bring\s+up|"
    r"search(?:\s+for)?|dig(?:\s+through|\s+up)?|go\s+through|go\s+and\s+(?:check|look)|"
    r"see\s+(?:what|if|whether|how|which|who|where|when)|find\s+out|recall|"
    r"fetch|grab|pull)"
)
_NARRATION_RE = re.compile(
    r"^\W*" + _FILLER + r"(?:"
    + _LEAD + _ADV + _VERB
    + r"|(?:checking|looking|searching|digging|pulling\s+up|having\s+a\s+look|taking\s+a\s+look)\b"
    + r"|(?:one|just\s+a)\s+(?:moment|sec(?:ond)?|tick)\b[\s,.!…-]*$"
    + r"|(?:hold\s+on|bear\s+with\s+me)\b"
    + r")",
    re.IGNORECASE,
)
# A promise / deferral — the sentence commits to a later action or message.
_DEFERRAL_RE = re.compile(
    r"\b(?:get\s+back\s+to\s+you|come\s+back\s+to\s+you|let\s+you\s+know|tell\s+you\s+(?:when|once|later)|"
    r"message\s+you|text\s+you|ping\s+you|remind\s+you|follow\s+up|report\s+back|circle\s+back|"
    r"in\s+(?:a\s+)?(?:bit|while|moment|minute|minutes|hour|hours)|later|tomorrow|tonight|"
    r"when\s+(?:it['’]s|i['’]ve)|once\s+(?:it['’]s|i['’]ve|i\s+have)|after\s+(?:that|lunch|dinner)|"
    r"with\s+you)\b",
    re.IGNORECASE,
)
# End of the first sentence: terminator(s) + closing quote/bracket, then whitespace.
_SENT_END_RE = re.compile(r"[.!?…]+[\"')\]]*(?=\s)|\n")


# Where the announcement CLAUSE ends inside one sentence and the answer begins:
# "Let me look: you have 3 events." / "Checking — you have 3 events." / "Let me
# check, you have 3 events." (a comma counts only before an answer-shaped word, so
# "Let me check my notes, then tell you." stays one announcement). A colon inside a
# clock time ("3:30") is not a boundary.
_BOUNDARY_RE = re.compile(
    r"\s*(?::(?!\d)|[—–]|\s-\s|,(?=\s+(?:you|your|you['’]re|you['’]ve|i|i['’]ve|it|it['’]s|"
    r"there|here|that|no|yes|nothing|nobody|none)\b))\s*"
)


def split_narration(sentence: str) -> Optional[str]:
    """Split ONE sentence into announcement + answer.

    * ``None``  — not a lookup announcement (or it carries a promise): leave it.
    * ``""``    — the whole sentence is the announcement ("I'll check what I've got
      on file about you."): drop it, if an answer follows.
    * non-empty — the announcement clause plus an answer in the SAME sentence
      ("Let me look: you have 3 events today."): drop only the clause, keep this
      (first letter capitalised).
    The promise test looks at the announcement clause only, so an answer that
    mentions "tomorrow" does not protect the announcement in front of it. Pure."""
    s = (sentence or "").strip()
    if not s:
        return None
    m = _NARRATION_RE.match(s)
    if not m:
        return None
    b = _BOUNDARY_RE.search(s, m.end())
    head = s[: b.start()] if b else s
    rest = s[b.end():].strip() if b else ""
    if len(head) > _MAX_SENTENCE_CHARS or _DEFERRAL_RE.search(head):
        return None
    if not rest:
        return ""
    return rest[0].upper() + rest[1:]


def is_narration(sentence: str) -> bool:
    """True for ONE sentence that only announces a lookup ("I'll check what I've
    got on file about you.") and carries no promise and no inline answer. Pure."""
    return split_narration(sentence) == ""


_FILLER_ONLY_RE = re.compile(
    r"^\W*(?:ok(?:ay)?|sure|alright|right|hmm+|um+|well|so|yes|yeah|of\s+course|certainly|absolutely)\W*$",
    re.IGNORECASE,
)


# A "." after one of these is not a sentence end ("Dr. Patel's number", "e.g. the
# dentist"). "no" counts only before a digit ("No. 5") — "No. You have nothing." is
# a sentence. A single capital letter ("J. Smith") is an initial.
_ABBREVIATIONS = frozenset({
    "dr", "mr", "mrs", "ms", "mx", "st", "prof", "sr", "jr", "vs", "etc", "approx", "mt", "ave",
    "rd", "blvd", "dept", "est", "inc", "ltd", "co", "fig", "gen", "col", "capt", "sgt", "rev", "hon",
    "e.g", "i.e", "a.m", "p.m", "u.s", "u.k",
})
_WORD_BEFORE_RE = re.compile(r"([A-Za-z]+(?:\.[A-Za-z]+)*)$")


def _is_abbreviation_dot(text: str, m: "re.Match[str]") -> bool:
    if m.group(0) != ".":
        return False
    wm = _WORD_BEFORE_RE.search(text[: m.start()])
    if not wm:
        return False
    word = wm.group(1)
    low = word.lower()
    if low in _ABBREVIATIONS or (len(word) == 1 and word.isupper()):
        return True
    return low == "no" and bool(re.match(r"\s*\d", text[m.end():]))


def _next_end(text: str, start: int = 0) -> int:
    """Index just past the first real sentence end at or after ``start``, or -1."""
    for m in _SENT_END_RE.finditer(text, start):
        if not _is_abbreviation_dot(text, m):
            return m.end()
    return -1


def _first_sentence_end(buf: str) -> int:
    """Index just past the first sentence of ``buf`` (terminator followed by
    whitespace, or a newline; abbreviations and initials do not end one), or -1
    when the sentence is not complete yet. A bare interjection sentence ("Sure!",
    "Okay.") is joined to the sentence after it, so "Sure! Let me check." is
    judged as one announcement."""
    end = _next_end(buf)
    if end < 0:
        return -1
    if _FILLER_ONLY_RE.match(buf[:end]):
        rest = buf[end:]
        lead = len(rest) - len(rest.lstrip())
        e2 = _next_end(rest[lead:])
        return end + lead + e2 if e2 >= 0 else -1
    return end


def strip_leading_narration(text: str) -> str:
    """The reply with up to two leading lookup-announcing sentences dropped.
    Unchanged when nothing follows them, when a sentence carries a promise, or
    when the reply does not start with an announcement. Pure."""
    rest = text or ""
    dropped = 0
    while dropped < _MAX_DROPPED:
        stripped = rest.lstrip()
        end = _first_sentence_end(stripped)
        if end < 0:
            if not stripped:
                break
            end = len(stripped)  # the reply's last (unterminated) sentence
        sentence, after = stripped[:end], stripped[end:]
        remainder = split_narration(sentence)
        if remainder is None:
            break
        if remainder:  # the answer is in the same sentence: drop the clause only
            rest = remainder + after
            dropped += 1
            break
        if not after.strip():
            break
        rest = after.lstrip()
        dropped += 1
    return rest if dropped else (text or "")


# ── Early release (ZOE_FIRST_SOUND_NARRATION_EARLY, default OFF) ─────────────────
# The stripper buffers the first sentence (up to _PROBE_LIMIT chars) to judge it, which holds EVERY reply's
# first words until that sentence closes: measured 2026-10-09 on the live path, the first delta reached the
# stream loop ~0.9 s (median) after the sidecar sent it. A sentence can only be an announcement if it STARTS
# like one, so once the buffered prefix cannot grow into a match of _NARRATION_RE it is released at once:
# same text, same order, same strips (tests/test_first_sound_narration_early.py proves it), earlier.
_FILLERS = frozenset({"ok", "okay", "sure", "alright", "right", "well", "so", "yes", "yeah", "of", "course",
                      "certainly", "absolutely"})
_GERUNDS = frozenset({"checking", "looking", "searching", "digging", "pulling", "having", "taking"})
_ADV = frozenset({"quickly", "just", "first", "now", "also", "try"})
_VERBS = frozenset({"check", "have", "take", "look", "pull", "bring", "search", "dig", "go", "see", "find",
                    "recall", "fetch", "grab"})
# Word sequences that open an announcement; a "+" lead is then followed by adverbs and a lookup verb.
_LEADS = ((("i'll",), True), (("i", "will"), True), (("i", "shall"), True), (("let", "me"), True),
          (("let's",), True), (("i'm", "going"), False), (("i", "am", "going"), False), (("allow", "me"), False),
          (("give", "me"), False), (("hold", "on"), False), (("bear", "with"), False), (("one", "moment"), False),
          (("one", "second"), False), (("one", "sec"), False), (("one", "tick"), False), (("just", "a"), False))
_STARTERS = tuple(_FILLERS | _GERUNDS | {"hmm", "um"} | {w for lead, _ in _LEADS for w in lead[:1]})


def early_release_enabled() -> bool:
    """ZOE_FIRST_SOUND_NARRATION_EARLY; unset, it follows ZOE_FIRST_SOUND_CLAUSE. Default OFF: the text is identical
    either way, but the voice loop's long-opening rules (clause break at 60+ characters, 90-character soft cap) then see
    a first sentence they never could before it closed, which changes the audio on those turns."""
    raw = os.environ.get("ZOE_FIRST_SOUND_NARRATION_EARLY")
    if raw is None or not raw.strip():
        raw = os.environ.get("ZOE_FIRST_SOUND_CLAUSE") or "0"
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def may_become_narration(buf: str) -> bool:
    """False only when NO continuation of ``buf`` can make :data:`_NARRATION_RE` match it; True (keep
    holding) whenever it still could. Conservative: any number of leading fillers is allowed (the regex
    allows one) and an unfinished last word is viable if it starts any word the pattern could want."""
    rest = buf[re.match(r"\W*", buf).end():]
    if not rest:
        return True
    if not re.match(r"[A-Za-z]", rest):
        return False   # a digit or other character: the pattern needs a letter here
    words = [w.lower().replace("\u2019", "'") for w in re.findall(r"[A-Za-z'\u2019]+", rest)]
    growing = bool(re.search(r"[A-Za-z'\u2019]$", buf))   # the last word may still be growing
    n, i = len(words), 0

    def starts(k: int, options) -> bool:   # words[k] is the unfinished last word and could become an option
        return k == n - 1 and growing and any(o.startswith(words[k]) for o in options)

    while i < n and not starts(i, _STARTERS) and (words[i] in _FILLERS or re.match(r"(hm+|um+)$", words[i])):
        i += 1   # skip filler words
    if i >= n or starts(i, _STARTERS) or words[i] in _GERUNDS:
        return True
    tail = words[i:]
    for lead, verb_follows in _LEADS:
        if not all(lead[k] == w or (k == len(tail) - 1 and growing and lead[k].startswith(w))
                   for k, w in enumerate(tail[:len(lead)])):
            continue
        if len(tail) <= len(lead) or not verb_follows:
            return True   # still inside the lead, or a lead that needs no verb after it
        j = i + len(lead)   # after the lead: adverbs, then a lookup verb
        while j < n and not starts(j, _ADV | _VERBS) and words[j] in _ADV:
            j += 1
        return j >= n or starts(j, _ADV | _VERBS) or words[j] in _VERBS
    return False


class NarrationStripper:
    """Incremental form of :func:`strip_leading_narration` for a delta stream.

    ``feed(delta)`` returns the text safe to emit now ("" while the first
    sentence is still arriving); ``finish()`` returns whatever is still held.
    The concatenation of every return equals ``strip_leading_narration`` of the
    concatenated input.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._held: list[str] = []  # narration sentences waiting for an answer
        self._dropped = 0
        self._passing = False

    def _resolve(self, final: bool) -> str:
        out = ""
        while not self._passing:
            stripped = self._buf.lstrip()
            end = _first_sentence_end(stripped)
            if end < 0 and final and stripped:
                end = len(stripped)  # the reply's last (unterminated) sentence
            if end < 0:
                if final or len(stripped) > _PROBE_LIMIT:
                    out += "".join(self._held) + self._buf
                    self._held, self._buf, self._passing = [], "", True
                break
            sentence, after = stripped[:end], stripped[end:]
            remainder = (split_narration(sentence)
                         if self._dropped + len(self._held) < _MAX_DROPPED else None)
            if remainder:  # announcement clause + answer in one sentence: keep the answer
                out += remainder + after
                self._held, self._buf, self._passing = [], "", True
                break
            if remainder == "":
                if after.strip():
                    self._dropped += 1
                    self._held = []  # an answer follows: the announcements go
                    self._buf = after.lstrip()
                    continue
                # nothing follows YET: hold it (emitted at finish, or dropped
                # when the answer arrives)
                self._held.append(self._buf)
                self._buf = ""
                if final:  # the stream ended on the announcement: it IS the reply
                    out += "".join(self._held)
                    self._held, self._passing = [], True
                break
            out += "".join(self._held) + self._buf
            self._held, self._buf, self._passing = [], "", True
        return out

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        if self._passing:
            return delta
        self._buf += delta
        if self._held and self._buf.strip():
            # an answer has started arriving after the held announcement(s)
            self._dropped += len(self._held)
            self._held = []
            self._buf = self._buf.lstrip()
        if (not self._held and self._buf.strip() and early_release_enabled()
                and not may_become_narration(self._buf)):
            # cannot be an announcement: release now (same bytes the buffered form would emit later)
            out, self._buf, self._passing = self._buf, "", True
            return out
        return self._resolve(final=False)

    def finish(self) -> str:
        if self._passing:
            return ""
        return self._resolve(final=True)


async def filter_stream(
    deltas: AsyncIterator[str],
    sentinel_prefixes: tuple[str, ...] = ("__TOOL__:", "__THINKING__:"),
) -> AsyncIterator[str]:
    """Wrap a brain delta stream: text deltas pass through a
    :class:`NarrationStripper`; tool / thinking sentinels pass through untouched
    and immediately. ``aclose`` is forwarded so an early consumer exit closes
    the inner turn deterministically."""
    stripper = NarrationStripper()
    try:
        async for delta in deltas:
            if delta.startswith(sentinel_prefixes):
                yield delta
                continue
            out = stripper.feed(delta)
            if out:
                yield out
        tail = stripper.finish()
        if tail:
            yield tail
    finally:
        aclose: Optional[object] = getattr(deltas, "aclose", None)
        if aclose is not None:
            await aclose()  # type: ignore[misc]
