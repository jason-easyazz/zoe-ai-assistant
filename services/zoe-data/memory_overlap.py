"""Token-level comparison of one fact against stored facts - the dedup and richness rules.

Why this module exists (docs/research/brain-extraction-stack-best-practice-2026-10-05.md, L3/L4): the
digests skipped a fact as a "duplicate" when more than 70 percent of its words were SUBSTRINGS of the
stored-facts blob. "Dana has two kids Mika and Biscuit" scored 0.71 against a stored "User's friend Dana
has two kids", and "User's friend Dana has two kids Mika and Biscuit" scored 0.78: the richer fact, and
exactly the new names, were dropped. Substring matching also counts "a" as shared with every blob.

Rules, all word-boundary and normalised (lower-case, possessive stripped, punctuation ignored):

* A fact that carries a NEW named entity, number or date that the stored fact does not contain is never
  a duplicate of it, whatever the word overlap.
* A fact that strictly EXTENDS a stored fact (every stored word is in it, plus new markers) is reported
  as ``extends`` so the writer supersedes the stored row (history kept) instead of skipping.
* ``richness`` (the count of distinct entity / number / date tokens) is what the weekly merge uses to
  pick the survivor.

Stdlib only; pure functions.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")
_POSSESSIVE_RE = re.compile(r"['’]s\b")
_CAP_RE = re.compile(r"\b[A-Z][A-Za-z]+\b")

# Capitalised words that are NOT named entities (sentence starts, the owner anchor, pronouns).
_CAP_STOP = frozenset(
    "user users the a an i my me we our you your he she they it his her their its this that these those "
    "there here and or but so if then is are was were has have had do does did not no yes zoe".split()
)
_NUMBER_WORDS = frozenset(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred "
    "thousand million dozen first second third fourth fifth".split()
)
_DATE_WORDS = frozenset(
    "january february march april june july august september october november december "
    "monday tuesday wednesday thursday friday saturday sunday".split()
)
# Words too common to decide whether one fact is "contained" in another.
_FILLER = frozenset("a an the of to in on at is are was were and or".split())

DUP_OVERLAP = 0.7


def tokens(text: str) -> list[str]:
    """Normalised word-boundary tokens (order kept)."""
    return _WORD_RE.findall(_POSSESSIVE_RE.sub("", (text or "").lower()))


def markers(text: str) -> set[str]:
    """The distinct entity / number / date tokens of ``text`` (lower-case).

    Entities are capitalised words (minus ``_CAP_STOP``); numbers are digit tokens and number words;
    dates are month and weekday names (a lower-case ``may`` is a verb and is not counted)."""
    out = {w.lower() for w in _CAP_RE.findall(_POSSESSIVE_RE.sub("", text or ""))} - _CAP_STOP
    for t in tokens(text):
        if t in _NUMBER_WORDS or t in _DATE_WORDS or any(ch.isdigit() for ch in t):
            out.add(t)
    return out


def richness(text: str) -> int:
    return len(markers(text))


def novel_markers(fact: str, stored: str) -> set[str]:
    """Entity / number / date tokens the fact holds that the stored text does not contain at all."""
    return markers(fact) - set(tokens(stored))


def overlap(fact: str, stored: str) -> float:
    """Fraction of the fact's distinct tokens present (as whole words) in ``stored``."""
    ft = set(tokens(fact))
    return len(ft & set(tokens(stored))) / max(len(ft), 1)


def strictly_extends(fact: str, stored: str) -> bool:
    """True when ``fact`` holds every content word of ``stored`` AND at least one new marker."""
    st = {t for t in tokens(stored) if t not in _FILLER}
    ft = set(tokens(fact))
    return bool(st) and st <= ft and bool(novel_markers(fact, stored))


def stored_lines(blob: str) -> list[str]:
    """The stored facts of a ``_mempalace_load_user_facts`` blob, one per line, without bullets,
    age prefixes ("[today]") or section headers."""
    out = []
    for raw in (blob or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s*", "", line)
        line = re.sub(r"^\[[^\]]{1,24}\]\s*", "", line)
        if line:
            out.append(line)
    return out


def dedup_verdict(fact: str, blob: str, *, threshold: float = DUP_OVERLAP) -> tuple[str, str]:
    """``(verdict, stored_line)`` of ``fact`` against the stored-facts blob.

    * ``duplicate``: a stored line holds more than ``threshold`` of the fact's words and nothing in the
      fact is new (no new name, number or date);
    * ``extends``: a stored line is fully contained in the fact, which adds new markers: supersede it;
    * ``novel``: anything else (write it).
    Each stored line is compared on its own: a fact is never "covered" by words scattered across
    unrelated stored facts."""
    extended = ""
    for line in stored_lines(blob):
        if overlap(fact, line) > threshold and not novel_markers(fact, line):
            return "duplicate", line
        if not extended and strictly_extends(fact, line):
            extended = line
    if extended:
        return "extends", extended
    return "novel", ""
