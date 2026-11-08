"""fact_subject - a fact the 4B digest filed under "the user" that the turn says about SOMEONE ELSE.

Why (QA round 5, 2026-11-08): the post-turn digest sees one user turn and writes "User ..." by default. "dad rang today. sober
for once. weird" became "The user is sober for once."; "priya no beef. arjun hates onion" became "The user does not eat beef." and
"The user does not eat onion for Arjun." A wrong fact is worse than a missing one, so the rule is: a fact whose subject is the
USER must be backed by a clause where the user speaks about themself (I / my / me / we / our); otherwise it is

* RE-ATTACHED when the turn names exactly one other person ("... for Arjun" -> "Arjun does not eat onion."; the turn's one
  relation word -> "User's dad is sober for once."), or
* DROPPED when the subject is uncertain (several people, or a multi-clause turn with no first person at all).

A single elliptical clause with nobody else in it ("allergic to nuts") is the user's own and is kept. Pure; the digest calls it.
"""
from __future__ import annotations

import re
from typing import Optional

_USER_SUBJECT_RE = re.compile(r"^\s*(?:the\s+)?user\b(?!['’]s\b)\s*(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|im|i've|ive|i'd|i'll|me|my|mine|myself|we|we're|we've|our|ours|us)\b", re.IGNORECASE)
_FOR_NAME_RE = re.compile(r"^(?P<pred>.+?)\s+for\s+(?P<name>[A-Z][a-z]{1,24})\W*$")
_RELATIONS = ("dad", "mum", "mom", "mother", "father", "wife", "husband", "partner", "son", "daughter", "brother", "sister", "boss",
              "boyfriend", "girlfriend", "nan", "nana", "grandma", "grandpa", "grandmother", "grandfather", "aunt", "uncle", "cousin",
              "friend", "mate", "neighbour", "neighbor", "colleague", "kid", "kids", "child", "baby")
_REL_RE = re.compile(r"\b(" + "|".join(_RELATIONS) + r")\b", re.IGNORECASE)
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;\n]+|,|\b(?:and|but|because|so)\b", re.IGNORECASE)
_STOP = frozenset({"the", "a", "an", "is", "are", "was", "were", "to", "of", "for", "in", "on", "at", "and", "or", "user", "not",
                   "does", "do", "doesn't", "don't", "has", "have", "had", "with", "that", "this", "it", "its", "be", "been"})


def _stem(tok: str) -> str:
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok


def _tokens(text: str) -> set:
    return {_stem(t) for t in re.findall(r"[a-z0-9']+", (text or "").lower()) if t not in _STOP and len(t) > 1}


def _clauses(message: str) -> list:
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(message or "") if c and c.strip()]


def _support(fact_rest: str, clauses: list) -> Optional[str]:
    """The clause of the turn the fact paraphrases (most shared content words), or None when none shares any."""
    want = _tokens(fact_rest)
    best, best_n = None, 0
    for c in clauses:
        n = len(want & _tokens(c))
        if n > best_n:
            best, best_n = c, n
    return best


def _names(message: str) -> set:
    """Capitalised words that are not a sentence's first word (a lowercase STT name is covered by the clause rules instead)."""
    out = set()
    for sentence in re.split(r"[.!?]+\s*", message or ""):
        words = sentence.split()
        for w in words[1:]:
            w = w.strip(",;:()\"'")
            if re.fullmatch(r"[A-Z][a-z]{2,24}", w) and w.lower() not in _RELATIONS:
                out.add(w)
    return out


def attribute(fact: str, user_message: str) -> tuple:
    """``("keep", fact)`` | ``("rewrite", new_fact)`` | ``("drop", reason)`` for one digest fact against the turn it came from."""
    m = _USER_SUBJECT_RE.match(fact or "")
    if not m or not (user_message or "").strip():
        return "keep", fact
    rest = m.group("rest").strip()
    clauses = _clauses(user_message)
    support = _support(rest, clauses)
    if (support is not None and _FIRST_PERSON_RE.search(support)) or (support is None and _FIRST_PERSON_RE.search(user_message)):
        return "keep", fact
    # no clause backs "the user" with the user's own voice: who is the turn about?
    fm = _FOR_NAME_RE.match(rest)
    if fm and re.search(rf"\b{re.escape(fm.group('name'))}\b", user_message, re.IGNORECASE):
        return "rewrite", f"{fm.group('name')} {fm.group('pred').strip()}."
    rels = {r.lower() for r in _REL_RE.findall(user_message)}
    names = _names(user_message)
    if len(rels) == 1 and not names and rest:
        rel = next(iter(rels))
        return "rewrite", f"User's {rel} {rest}"
    if rels or names or len(clauses) >= 2:
        return "drop", "other_subject"
    return "keep", fact
