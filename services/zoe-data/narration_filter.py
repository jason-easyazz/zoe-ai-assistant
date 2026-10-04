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


def is_narration(sentence: str) -> bool:
    """True for ONE sentence that only announces a lookup ("I'll check what I've
    got on file about you.") and carries no promise. Pure."""
    s = (sentence or "").strip()
    if not s or len(s) > _MAX_SENTENCE_CHARS:
        return False
    if _DEFERRAL_RE.search(s):
        return False
    return bool(_NARRATION_RE.match(s))


_FILLER_ONLY_RE = re.compile(
    r"^\W*(?:ok(?:ay)?|sure|alright|right|hmm+|um+|well|so|yes|yeah|of\s+course|certainly|absolutely)\W*$",
    re.IGNORECASE,
)


def _first_sentence_end(buf: str) -> int:
    """Index just past the first sentence of ``buf`` (terminator followed by
    whitespace, or a newline), or -1 when the sentence is not complete yet. A
    bare interjection sentence ("Sure!", "Okay.") is joined to the sentence
    after it, so "Sure! Let me check." is judged as one announcement."""
    m = _SENT_END_RE.search(buf)
    if not m:
        return -1
    end = m.end()
    if _FILLER_ONLY_RE.match(buf[:end]):
        rest = buf[end:]
        lead = len(rest) - len(rest.lstrip())
        m2 = _SENT_END_RE.search(rest[lead:])
        return end + lead + m2.end() if m2 else -1
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
            break
        sentence, after = stripped[:end], stripped[end:]
        if not is_narration(sentence) or not after.strip():
            break
        rest = after.lstrip()
        dropped += 1
    return rest if dropped else (text or "")


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
            if end < 0:
                if final or len(stripped) > _PROBE_LIMIT:
                    out += "".join(self._held) + self._buf
                    self._held, self._buf, self._passing = [], "", True
                break
            sentence, after = stripped[:end], stripped[end:]
            if self._dropped + len(self._held) < _MAX_DROPPED and is_narration(sentence):
                if after.strip():
                    self._dropped += 1
                    self._held = []  # an answer follows: the announcements go
                    self._buf = after.lstrip()
                    continue
                # nothing follows YET: hold it (emitted at finish, or dropped
                # when the answer arrives)
                self._held.append(self._buf)
                self._buf = ""
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
