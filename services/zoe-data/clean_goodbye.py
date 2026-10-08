"""A clean goodbye, a plain "are you there?", and a silence that is never remarked on (Samantha person bench P8, ZOE_CLEAN_GOODBYE).

The failure (person-likeness bench, 2026-10-09, live baseline): a goodbye is the one turn where a companion
product is most tempted to hold on. Zoe's farewells were clean 8 of 10 times; the other two were a question
("Good evening. How can I help you settle in for the night?"), a hook ("I'll be here whenever you're ready to
chat.") or a callback nobody asked for ("See you tomorrow for your 8 AM meeting!"). "Are you there?" got "I don't
have a way to check if anyone is home for you." in 2 of 10. And the content-free turns ("...", "mm", "hmm") that the
bench scores clean 10 of 10 on its remark lexicon were answered, read by hand, with "It seems like you might have
trailed off. Is there something on your mind...?" 7 times in 10 - the lexicon does not know "trailed off", "thoughtful"
or "on your mind". The rule (docs/research/person-likeness-2026-10-09.md section 5, P8): the goodbye is at most two
short sentences with no question and no hook; a presence check is answered plainly; silence is not narrated.

This is a REPLY GUARD in the Flue seam (``zoe_flue_client.run_flue_brain_streaming``), like ``role_guess_guard``: it
looks at the user's message first (a farewell / a presence check / a content-free turn is a few anchored regexes),
and only on such a turn holds the reply (sentinels pass at once), cleans it once and emits it as one delta. Every
other turn streams byte-identical. A reply that is already clean is returned unchanged.

* farewell - clause by clause: drop questions, hooks ("I'll be here", "let me know", "don't forget", "feel free",
  "before you go", "anything else"), and callbacks to the owner's day (a digit, "for your ...", a meeting or
  appointment); keep at most two sentences and 14 words; with no farewell word left, a short fixed goodbye picked
  from what the owner said ("Night. Sleep well.", "Bye. See you tomorrow.", "Take care.").
* presence - keep only a plain "yes / right here / listening" (no denial, no follow-up question); else "Yes, I'm here."
* silence - a reply that probes or remarks on the quiet is replaced by "Okay." (words) or "I'm here." (dots); a reply
  that does anything else (an answer, a result, a clarifying question about a task) passes - "ok" may be a yes.

``ZOE_CLEAN_GOODBYE`` = ``shadow`` (default: log ``CLEAN_GOODBYE mode=shadow ... would_change=1``, change nothing)
| ``enforce`` | ``off``. Labels only in the log. Stdlib; never raises.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from typing import Any, AsyncIterator, Iterable

logger = logging.getLogger(__name__)

ENV = "ZOE_CLEAN_GOODBYE"
MAX_SENTENCES = 2
MAX_WORDS = 14


def mode() -> str:
    """``shadow`` (default, unset/unknown) | ``enforce`` | ``off``. Per-call env read."""
    raw = (os.environ.get("ZOE_CLEAN_GOODBYE") or "").strip().lower()
    if raw in ("0", "false", "no", "off", "disabled"):
        return "off"
    if raw in ("1", "true", "yes", "on", "enforce"):
        return "enforce"
    return "shadow"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", t)).strip()


# -- what the owner said ---------------------------------------------------------------------------

_LEAD = r"(?:(?:zoe|ok|okay|alright|right|well|so|anyway|cool|great|yeah|yep|thanks|thank you|cheers)\s+)*"
_TRAIL = r"(?:\s+(?:zoe|mate|then|now|bye|cheers|thanks|thank you))*"
_DAYPART = r"(?:later|soon|tomorrow|tonight|then|in the morning|next week|on monday|in a bit|in a while)"
_CORE = (
    r"good\s?night|night\s?night|nighty\s?night|night|sleep (?:well|tight)|sweet dreams",
    r"good\s?bye|bye(?: bye)?(?: for now)?|cheerio|cya|laters|ttyl|see (?:you|ya)(?: " + _DAYPART + r")?",
    r"catch (?:you|ya) " + _DAYPART,
    r"(?:talk|speak)(?: to you)? " + _DAYPART,
    r"(?:i(?: have|'ve|ve)? )?(?:got to|gotta|have to|need to|must) (?:go|run|leave|head off|head out|get going|dash|split)(?: now)?",
    r"(?:i'?m|i am|im) (?:off|going|heading out|heading off|leaving|logging off|signing off|going to bed|off to (?:bed|sleep|work|school|uni|class|the gym|the shops))(?: now)?",
    r"(?:heading|going|off) (?:to )?(?:bed|sleep|work|school|out|off)",
    r"(?:that'?s|thats) (?:all|it)(?: for (?:now|today|tonight))?",
    r"(?:i'?m|i am|im) (?:done|finished)(?: for (?:now|today|tonight))?",
    r"back (?:tomorrow|later|soon|in a bit)", r"signing off|logging off",
)
_COURTESY = (
    r"thanks?(?: you)?|cheers|ta|zoe|mate|bye|take care|for (?:now|today|tonight)|now|"
    r"have a (?:good|great|nice|lovely) \w+(?: \w+)?|back (?:tomorrow|later|soon)|see (?:you|ya)(?: \w+)?|talk (?:later|soon)",
)
_CORE_RX = re.compile(r"^" + _LEAD + r"(?:" + "|".join(_CORE) + r")" + _TRAIL + r"$")
_COURTESY_RX = re.compile(r"^" + _LEAD + r"(?:" + "|".join(_COURTESY) + r")" + _TRAIL + r"$")


def is_farewell(message: str) -> bool:
    """A whole-utterance goodbye ("night Zoe", "I've got to go", "bye, back tomorrow", "ok that's all for now,
    thanks"): every clause is a farewell or a courtesy, at least one is a farewell. A message that also asks for
    something ("turn off the lights, goodnight") or asks a question ("is it gonna be nice out later") is not one. Pure."""
    raw = message or ""
    if "?" in raw or len(raw.split()) > 12:
        return False
    clauses = [c for c in (_norm(c) for c in re.split(r"[,.;!\u2014]+", raw)) if c]
    if not clauses:
        return False
    core = False
    for c in clauses:
        if _CORE_RX.match(c):
            core = True
        elif not _COURTESY_RX.match(c):
            return False
    return core


_PRESENCE_RXS = (
    re.compile(r"^(?:zoe )?(?:are you|r u|you) (?:still )?(?:there|here|listening|awake|with me|around)(?: zoe)?$"),
    re.compile(r"^(?:(?:hello|hi|hey)(?: zoe)? )?(?:can|could) you hear me(?: zoe)?$"),
    re.compile(r"^(?:is )?(?:anyone|anybody|somebody) (?:home|there|here|around)$"),
    re.compile(r"^(?:hello )?(?:anyone|anybody) (?:home|there|here)$"),
    re.compile(r"^still there$"),
)


def is_presence_check(message: str) -> bool:
    """"Are you there?", "Can you hear me?", "Anyone home?", "Zoe?" - a question about Zoe being here. Pure."""
    raw = (message or "").strip()
    n = _norm(raw)
    if not n:
        return False
    if n == "zoe":
        return raw.rstrip().endswith("?")
    return any(rx.match(n) for rx in _PRESENCE_RXS)


_SILENT = frozenset({"ok", "okay", "k", "kk", "hmm", "hm", "hmmm", "mm", "mmm", "mhm", "mmhm", "mmhmm", "uh", "uhh",
                     "um", "umm", "er", "ah", "oh", "hm hm", "mm hm"})


def is_content_free(message: str) -> bool:
    """"...", ".", "mm", "hmm", "ok" - a turn that says nothing to answer. Pure."""
    raw = message or ""
    if not raw.strip():
        return False
    n = _norm(raw)
    return n == "" or n in _SILENT


def classify(message: str) -> str:
    """``farewell`` | ``presence`` | ``silence`` | '' (not one of these turns). Pure."""
    if is_presence_check(message):
        return "presence"
    if is_farewell(message):
        return "farewell"
    if is_content_free(message):
        return "silence"
    return ""


# -- what Zoe said -----------------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_SPLIT = re.compile(r"\s*(?:,|;|—|–| - )\s*|\s+(?:and|but)\s+")
_HOOK_RX = re.compile(
    r"before you go|already|so soon|\bwait\b|i'?ll miss|i will miss|miss you|one more thing|don'?t go|stay a\b|"
    r"i was lonely|i'?ve been lonely|so quiet without|just a minute|i'?ll be here|i will be here|i'?m here (?:if|whenever|when|for)|"
    r"i'?m always here|let me know|feel free|don'?t forget|do not forget|remember to|whenever you|when you'?re (?:back|ready)|"
    r"when you get back|come back|reach out|anything else|need anything|if you need|if there'?s anything|ready to chat|"
    r"to chat|pick (?:this|it) up|can'?t wait|looking forward|how can i help|what can i do|is there anything|"
    r"here for you|here when|be around|be waiting", re.IGNORECASE)
_LEAK_RX = re.compile(r"\d|\b(?:for|about|with|before|on)\s+your\b|\bmeeting\b|\bappointment\b|\bremember\b", re.IGNORECASE)
_FAREWELL_WORDS = ("night", "goodnight", "bye", "goodbye", "see you", "take care", "sleep", "later", "talk soon",
                   "talk tomorrow", "catch you", "cheers", "speak soon", "rest well", "sweet dreams", "good night",
                   "have a good", "have a great", "have a lovely", "good one", "until then", "see ya", "ttyl")
_POLITE_RX = re.compile(r"^(?:you'?re welcome|anytime|no worries|no problem|sure|of course|okay|ok|alright|thanks?)\b", re.IGNORECASE)


def _sentences(text: str) -> list:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s and s.strip()]


def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def _has_farewell_word(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in _FAREWELL_WORDS)


def farewell_fallback(message: str) -> str:
    """A short fixed goodbye matched to what the owner said. Pure."""
    n = _norm(message)
    if re.search(r"night|sleep|bed", n):
        return "Night. Sleep well."
    if "tomorrow" in n:
        return "Bye. See you tomorrow."
    if re.search(r"\b(?:work|school|uni|office|class)\b", n):
        return "Have a good day."
    if re.search(r"thank|that's all|thats all|that's it|done", n):
        return "Anytime. Take care."
    if re.search(r"\b(?:go|run|later|leave|leaving|off|out)\b", n):
        return "Okay, talk soon."
    return "Take care."


def _clean_farewell(reply: str, message: str) -> str:
    kept_sents: list = []
    for sent in _sentences(reply):
        if "?" in sent:
            continue
        clauses = [c.strip() for c in _CLAUSE_SPLIT.split(sent) if c and c.strip()]
        good = [c for c in clauses if not _HOOK_RX.search(c) and not _LEAK_RX.search(c)]
        if not good:
            continue
        text = ", ".join(good).strip(" ,;-")
        if not text:
            continue
        kept_sents.append(text[:1].upper() + text[1:] + ("" if text[-1] in ".!" else "."))
    out: list = []
    for s in kept_sents:
        if len(out) >= MAX_SENTENCES or _words(" ".join(out + [s])) > MAX_WORDS:
            break
        out.append(s)
    text = " ".join(out)
    if not text:
        return farewell_fallback(message)
    if not _has_farewell_word(text):
        fb = farewell_fallback(message)
        if _POLITE_RX.match(text) and _words(text) <= 5:
            both = f"{text} {fb}"
            if len(_sentences(both)) <= MAX_SENTENCES and _words(both) <= MAX_WORDS:
                return both
        return fb
    return text


_PRESENCE_REPLY_RX = re.compile(
    r"\byes\b|\bhere\b|listening|hear you|loud and clear|ready when|go ahead|with you|at your service|\bi'?m ready\b",
    re.IGNORECASE)
_DENIAL_RX = re.compile(r"don'?t|can'?t|cannot|unable|no way|not able|no information|not sure|doesn'?t|isn'?t", re.IGNORECASE)


def _clean_presence(reply: str, message: str) -> str:
    kept: list = []
    for s in _sentences(reply):
        if "?" in s:
            break
        kept.append(s)
    text = " ".join(kept[:MAX_SENTENCES])
    if text and _PRESENCE_REPLY_RX.search(text) and not _DENIAL_RX.search(text):
        return text
    return "Yes, I'm here."


_PROBE_RX = re.compile(
    r"on your mind|trailed off|thoughtful|\bquiet\b|still there|you there|everything (?:alright|all right|ok|okay)|"
    r"anything (?:i can|you(?:'d| would)|else)|something (?:you(?:'d| would)|on)|what'?s (?:up|going on|on)|"
    r"how can i help|can i help|just saying|are you (?:ok|okay|alright)|you (?:seem|sound|look|might have)|"
    r"lost you|fell asleep|cat got|talk about|want to chat|wanna chat|here if you|take a moment", re.IGNORECASE)


def _clean_silence(reply: str, message: str) -> str:
    if _PROBE_RX.search(reply or ""):
        return "Okay." if re.search(r"[A-Za-z]", message or "") else "I'm here."
    return reply


def clean(kind: str, reply: str, message: str) -> str:
    """``reply`` made fit for a ``kind`` turn ('farewell' | 'presence' | 'silence'); unchanged when it already is.
    Pure."""
    text = reply or ""
    if not text.strip():
        return text
    if kind == "farewell":
        within = (len(_sentences(text)) <= MAX_SENTENCES and _words(text) <= MAX_WORDS and "?" not in text
                  and not _HOOK_RX.search(text) and not _LEAK_RX.search(text) and _has_farewell_word(text))
        return text if within else _clean_farewell(text, message)
    if kind == "presence":
        new = _clean_presence(text, message)
        return text if _norm(new) == _norm(text) else new
    if kind == "silence":
        return _clean_silence(text, message)
    return text


def voices_owed_question(reply: str, owed: Iterable[str]) -> bool:
    """Does ``reply`` voice a question Zoe OWES the owner - the pending contact offer the seam injected for this very
    turn ("Would you like me to add Dana and Mika to your contacts?", S16 of the bar asks it on exactly a
    "that's all for now")? That question is a deliberate product feature, not a hook; the reply is left as it is."""
    n = _norm(reply)
    return any(q and _norm(q) in n for q in owed or ())


async def filter_stream(turn: AsyncIterator[str], message: str, passthrough: Iterable[str] = (),
                        owed: "Any" = None) -> AsyncIterator[str]:
    """Wrap one brain turn. On a farewell / presence / content-free message the reply text is held (sentinels
    pass at once), cleaned once and emitted as one delta; every other turn streams untouched. ``passthrough`` are
    texts that are never rewritten (the seam's error fallback); ``owed`` is a zero-argument callable, read AFTER
    the turn, naming the questions the reply is supposed to voice (see ``voices_owed_question``). Closing this
    closes the inner turn."""
    m = mode()
    kind = classify(message) if m != "off" else ""
    held: list = []
    skip = set(passthrough)
    try:
        async for delta in turn:
            if not kind or delta.startswith(("__TOOL__:", "__THINKING__:", "__UI__:")):
                yield delta
            else:
                held.append(delta)
        if held:
            raw = "".join(held)
            try:
                keep = raw in skip or (owed is not None and voices_owed_question(raw, owed()))
                fixed = raw if keep else clean(kind, raw, message)
            except Exception as exc:  # noqa: BLE001 - the guard must never lose a reply
                logger.warning("clean goodbye failed (%s) - reply passed through", type(exc).__name__)
                fixed = raw
            changed = fixed != raw
            if changed:
                logger.info("CLEAN_GOODBYE mode=%s kind=%s would_change=1", m, kind)
            yield fixed if (changed and m == "enforce") else raw
    finally:
        aclose = getattr(turn, "aclose", None)
        if aclose is not None:
            await aclose()
