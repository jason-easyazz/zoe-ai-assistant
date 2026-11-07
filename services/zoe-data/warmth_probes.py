"""Warmth probes - deterministic checks that a reply meets a feeling or a self-disclosure like a friend, not a form.

An offline instrument (nothing live calls it) and a library a persona-drift battery can reuse (the scripts/perf persona-drift probe script,
#2282, scores reply style; these four score the QA "half-baked robot" findings of 2026-11-05/06). Each probe takes ONE exchange
(the user's words, Zoe's reply) and returns ``"pass"``, ``"fail"`` or ``"na"`` (the probe does not apply):

* ``follow_up``   - an emotional or self-disclosing turn gets a SPECIFIC question back: a sentence ending in "?" that is not a
                    generic closer ("How are you holding up?", ``mirror_guard`` "generic_closers"). FAIL: no question, or only a
                    generic one ("Thanks for sharing that with me ... busy household.").
* ``no_extras``   - an emotional turn carries no unrequested weather / contact offer / "by the way" aside after its first sentence
                    (``emotional_turn_guard.dropped_kinds``: the same detector the reply guard uses). FAIL: "... As for the weather,
                    it's 25 degrees".
* ``locale``      - a reply avoids the household's off-locale words (``locale_data/household_locale.json`` "avoid" for the
                    country: "Fahrenheit", "flavored" for AU). ``na`` without a country, and when the user's own words brought
                    the other convention in (an avoid word or an "avoid_asks" pattern: "375F in celsius?").
* ``no_parrot``   - on an emotional / self-disclosing turn, neither of the reply's first ``PARROT_SENTENCES`` sentences is the
                    user's words handed back: FAIL when one shares at least ``PARROT_MIN_SHARED`` content words with the user's
                    turn AND they are at least ``PARROT_MIN_SHARE`` of its own content words ("So you've got Sam, Theo, and
                    Biscuit with you now."). On ANY turn, FAIL on a stock line from ``STOCK_LINES`` ("Thanks for sharing that
                    with me", "Welcome to your new place!").
* ``no_ctx_ack``  - a reply never acknowledges the background context zoe-data adds to a turn as if the user had said it ("I've
                    noted all those details about the household context", "I'll make sure to use metric and Australian English
                    from now on" - QA round 3b). ``na`` when the user's own words are about units, spelling or the language.

The CLI reads a JSONL transcript (``{"in": ..., "out": ...}`` or ``{"user": ..., "reply": ...}`` rows) and prints COUNTS only - no
text, no ids - so it can be run on a private transcript: ``python warmth_probes.py transcript.jsonl --country AU [--after-guard]``.
``--after-guard`` scores every reply after ``emotional_turn_guard.trim`` (what the reply guard would let through).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from typing import Iterable

PROBES = ("follow_up", "no_extras", "locale", "no_parrot", "no_ctx_ack")

# Acknowledging the background context (the household-locale line, the [Today ...] block) as if the user had said it.
_CTX_ACK_RE = re.compile(
    r"\b(?:australian|british|american|new zealand|canadian)\s+english\b|\bhousehold context\b"
    r"|\b(?:noted|got|taken note of)\s+(?:all\s+)?(?:of\s+)?(?:those|these|the)\s+(?:details|conventions|preferences)\b"
    r"|\b(?:use|using|stick (?:to|with))\s+(?:the\s+)?metric\b|\bmetric\b[^.?!]{0,40}\bfrom now on\b",
    re.IGNORECASE)
# The user's own words are about the conventions: naming them back is right.
_CTX_ASK_RE = re.compile(r"\b(?:metric|imperial|celsius|fahrenheit|spell\w*|english|units?|conventions?|locale)\b", re.IGNORECASE)


def no_ctx_ack(message: str, reply: str) -> str:
    if _CTX_ASK_RE.search(message or ""):
        return "na"
    return "fail" if _CTX_ACK_RE.search(reply or "") else "pass"

#: a first sentence sharing at least this many content words with the user's turn, making up at least this share of its own
PARROT_MIN_SHARED = 3
PARROT_MIN_SHARE = 0.6
#: the parrot check reads this many leading sentences ("That's wonderful. So you've got A, B and C now." parrots in the second)
PARROT_SENTENCES = 2

STOCK_LINES = (
    "thanks for sharing", "thank you for sharing", "welcome to your new", "i'm here to listen", "i am here to listen",
    "that's a busy household", "it sounds like you've got a busy", "you've got a busy household",
)

# A turn about the speaker's own life or people (the getting-to-know-you case): a possessive + a person / role / life word, or a
# first-person role statement. English; measurement only.
_SELF_DISCLOSURE_RE = re.compile(
    r"\b(?:my|our)\s+(?:husband|wife|partner|boyfriend|girlfriend|fiance|fiancee|son|daughter|kids?|children|baby|mum|mom|dad|"
    r"mother|father|parents?|brother|sister|family|dog|cat|puppy|job|work|boss|team|new\s+(?:job|place|house|home|flat))\b"
    r"|\bi(?:'m|\s+am)\s+(?:a|an)\s+[a-z]+(?:\s+[a-z]+)?\b"
    r"|\bi\s+work\s+(?:as|at|in|from)\b|\bwe(?:'ve|\s+have|\s+just)?\s+(?:just\s+)?moved\b|\bi\s+have\s+(?:a|an|two|three)\s+"
    r"(?:son|daughter|kids?|children|dog|cat|boy|girl)s?\b",
    re.IGNORECASE)

_STOP = frozenset("""
the and but for with from that this these those have has had you your you're you've are was were been being just really
about what when where which who whom how its it's i'm i've im ive she her him his they them their our ours mine yours
there here then than too very also into onto over under some any all not can could would should will shall may might
must yet now got get gets going gone like lot lots much many more most such only own same so a an of to in on at by
""".split())
_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")


def _fold(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'").lower()


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def content_words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD_RE.findall(_fold(text)) if len(w) >= 3 and w not in _STOP]


def _sentences(text: str) -> list[str]:
    from narration_filter import _next_end

    out, rest = [], (text or "").strip()
    while rest:
        end = _next_end(rest)
        if end < 0:
            out.append(rest.strip())
            break
        out.append(rest[:end].strip())
        rest = rest[end:].lstrip()
    return [s for s in out if s]


_COMMAND_LEAD_RE = re.compile(
    r"^\W*(?:ok(?:ay)?\W+|so\W+|and\W+|oh\W+|please\W+|thanks\W+)*(?:forget|remove|delete|remind|add|set|stop|cancel|don'?t|do\s+not|"
    r"what|who|where|when|why|how|can|could|would|do|does|did|is|are)\b", re.IGNORECASE)


def is_self_disclosure(message: str) -> bool:
    """A sentence that tells Zoe about the speaker's life or people - not a question and not a command ("forget that I'm a ...")."""
    for sentence in _sentences(message):
        if sentence.rstrip().endswith("?") or _COMMAND_LEAD_RE.match(sentence):
            continue
        if _SELF_DISCLOSURE_RE.search(_fold(sentence)):
            return True
    return False


def _mirror_lexicon():
    import mirror_guard

    return mirror_guard.lexicon("en")


# -- the probes ------------------------------------------------------------------------------------------

def follow_up(message: str, reply: str) -> str:
    import emotional_turn_guard

    if not (emotional_turn_guard.is_emotional_turn(message) or is_self_disclosure(message)):
        return "na"
    import mirror_guard

    lex = _mirror_lexicon()
    questions = [s for s in _sentences(reply) if s.rstrip("\"')").endswith("?")]
    specific = [q for q in questions if lex is None or not mirror_guard.is_generic_closer(q, lex)]
    return "pass" if specific else "fail"


def no_extras(message: str, reply: str) -> str:
    import emotional_turn_guard

    if not emotional_turn_guard.is_emotional_turn(message):
        return "na"
    return "fail" if emotional_turn_guard.dropped_kinds(reply, message) else "pass"


def _avoid_rx(words: Iterable[str]):
    parts = [re.escape(_fold(w)) for w in words if str(w).strip()]
    if not parts:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(sorted(parts, key=len, reverse=True)) + r")(?![\w])")


def locale(message: str, reply: str, country: str) -> str:
    import household_locale

    entry = household_locale.conventions(country) or {}
    rx = _avoid_rx(entry.get("avoid") or [])
    if rx is None:
        return "na"
    folded = _fold(message)
    if rx.search(folded) or any(re.search(str(p), folded) for p in entry.get("avoid_asks") or []):
        return "na"   # the user's own words brought the other convention in ("375F in celsius?"): naming it back is right
    return "fail" if rx.search(_fold(reply)) else "pass"


def no_parrot(message: str, reply: str) -> str:
    """Stock lines on any turn; the sentence handed back on a conversational turn (an emotional or self-disclosing one - a task
    confirmation restating the task, "Your timer is set for 12 minutes", is not parroting)."""
    import emotional_turn_guard

    low = _fold(reply)
    if any(s in low for s in STOCK_LINES):
        return "fail"
    if not (emotional_turn_guard.is_emotional_turn(message) or is_self_disclosure(message)):
        return "na"
    theirs = set(content_words(message))
    sents = _sentences(reply)[:PARROT_SENTENCES]
    if not sents or not theirs:
        return "na"
    for sentence in sents:
        mine = content_words(sentence)
        shared = [w for w in mine if w in theirs]
        if mine and len(set(shared)) >= PARROT_MIN_SHARED and len(shared) / len(mine) >= PARROT_MIN_SHARE:
            return "fail"
    return "pass"


def score(message: str, reply: str, country: str = "") -> dict[str, str]:
    """Every probe for one exchange."""
    return {
        "follow_up": follow_up(message, reply),
        "no_extras": no_extras(message, reply),
        "locale": locale(message, reply, country) if country else "na",
        "no_parrot": no_parrot(message, reply),
        "no_ctx_ack": no_ctx_ack(message, reply),
    }


def score_rows(rows: Iterable[tuple[str, str]], country: str = "", after_guard: bool = False) -> dict[str, Counter]:
    """Counts per probe over (message, reply) rows: {"follow_up": Counter(pass=, fail=, na=), ...}. No text is kept."""
    import emotional_turn_guard

    out = {p: Counter() for p in PROBES}
    for message, reply in rows:
        if after_guard:
            reply = emotional_turn_guard.trim(reply, message)
        for p, v in score(message, reply, country).items():
            out[p][v] += 1
    return out


def _rows(path: str) -> list[tuple[str, str]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg, rep = d.get("in", d.get("user")), d.get("out", d.get("reply"))
            if isinstance(msg, str) and isinstance(rep, str) and rep.strip():
                rows.append((msg, rep))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("transcript", help="JSONL with in/out (or user/reply) rows")
    ap.add_argument("--country", default="", help="ISO code for the locale probe (e.g. AU); default: none")
    ap.add_argument("--after-guard", action="store_true", help="score each reply after emotional_turn_guard.trim")
    args = ap.parse_args(argv)
    counts = score_rows(_rows(args.transcript), country=args.country.upper(), after_guard=args.after_guard)
    for p in PROBES:
        c = counts[p]
        applicable = c["pass"] + c["fail"]
        rate = f"{c['pass'] / applicable:.2f}" if applicable else "-"
        print(f"{p:10s} pass={c['pass']:3d} fail={c['fail']:3d} na={c['na']:3d} pass_rate={rate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
