"""kin_pet_guard - a child the 4B digest filed as a pet (QA round 6a, 2026-11-08).

"kids Ava (8) and Ben (4), and a fat cat called Mo" came back as "User's pet Ava is obsessed with horses": the one-turn extractor
types a name by its neighbours, and a list of kids that ends beside an animal bleeds the animal's type onto the kids. The user's
own words settle it, so this is a deterministic check on the model's output, not a prompt change:

* a name that FOLLOWS a child cue (kids / sons / daughters ...) in a list is a CHILD;
* a name that follows "<animal> called / named" is a PET.

A fact filed as a pet about a named child becomes a child fact, and the reverse. A fact naming neither, or both, is left alone.
Pure; the digest calls ``repair``. The words are data (``lexicons_data/en.json`` child_cue_words / pet_words / name_list_stop_words).
"""
from __future__ import annotations

import re
from typing import Optional

import lexicons as _lexicons

_EN = _lexicons.load("en")
_CHILD_ALT = "|".join(sorted((re.escape(w) for w in _EN.get("child_cue_words", [])), key=len, reverse=True))
_PET_ALT = "|".join(sorted((re.escape(w) for w in _EN.get("pet_words", []) if w != "pet"), key=len, reverse=True))
_STOP = frozenset(w.lower() for w in _EN.get("name_list_stop_words", []))
_MAX_NAMES = 8

_CHILD_CUE_RE = (re.compile(rf"\b(?:{_CHILD_ALT})\b[\s:,-]*(?:(?:are|is|named|called)\b[\s:,-]*)*", re.IGNORECASE)
                 if _CHILD_ALT else None)
_ITEM_RE = re.compile(r"\s*([A-Za-z][A-Za-z'’-]{1,24})(?:\s*\((?:aged\s*)?\d{1,2}\))?(?:\s+(?:aged|age)\s+\d{1,2})?")
_SEP_RE = re.compile(r"\s*(?:,\s*(?:and\s+)?|&\s*|\band\s+)", re.IGNORECASE)
_PET_NAME_RE = (re.compile(rf"\b(?:{_PET_ALT}|pet)s?\s+(?:called|named)\s+([A-Za-z][A-Za-z'’-]{{1,24}})", re.IGNORECASE)
                if _PET_ALT else None)
_PET_FACT_RE = re.compile(r"\bpets?\b", re.IGNORECASE)
_CHILD_FACT_RE = re.compile(rf"\b(?:{_CHILD_ALT})\b", re.IGNORECASE) if _CHILD_ALT else None


def child_names(message: str) -> set:
    """Lower-cased names listed straight after a child cue ("kids Ava (8) and Ben (4)"), never an article or filler word."""
    out: set = set()
    if not _CHILD_CUE_RE:
        return out
    text = message or ""
    for cue in _CHILD_CUE_RE.finditer(text):
        pos = cue.end()
        for _ in range(_MAX_NAMES):
            m = _ITEM_RE.match(text, pos)
            if not m or m.group(1).lower() in _STOP:
                break
            out.add(m.group(1).lower())
            sep = _SEP_RE.match(text, m.end())
            if not sep:
                break
            pos = sep.end()
    return out


def pet_names(message: str) -> set:
    """Lower-cased names that follow "<animal> called / named" ("a fat cat called Mo")."""
    if not _PET_NAME_RE:
        return set()
    return {m.group(1).lower() for m in _PET_NAME_RE.finditer(message or "") if m.group(1).lower() not in _STOP}


def _mentions(fact: str, names: set) -> set:
    toks = {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z'’-]*", fact or "")}
    toks |= {t[:-2] for t in toks if t.endswith(("'s", "’s"))}
    return toks & names


def repair(fact: str, fact_type: str, user_message: str) -> Optional[tuple]:
    """``(fact, type)`` re-typed when the turn's own words say the fact's named subject is a child filed as a pet (or a pet filed
    as a child); None when nothing needs changing."""
    kids, pets = child_names(user_message), pet_names(user_message)
    kids -= pets            # one word cannot be both: leave the ambiguous alone
    if not kids and not pets:
        return None
    as_kid, as_pet = _mentions(fact, kids), _mentions(fact, pets)
    if as_kid and not as_pet and (_PET_FACT_RE.search(fact) or fact_type == "pet"):
        return _PET_FACT_RE.sub("child", fact, count=1), "relationship"
    if as_pet and not as_kid and _CHILD_FACT_RE and _CHILD_FACT_RE.search(fact):
        return _CHILD_FACT_RE.sub("pet", fact, count=1), "pet"
    return None
