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


_NOT_A_SUBJECT = frozenset({"no", "yes", "yeah", "nope", "ok", "okay", "also", "and", "but", "so", "well", "just", "still", "really",
                            "actually", "never", "always", "sometimes", "maybe", "oh", "um", "uh", "hey", "zoe", "not", "very", "too",
                            "he", "she", "they", "it", "that", "this", "there"})


def _leading_subject(rest: str, message: str, clauses: list) -> str:
    """The turn's ONE clause opens on a word the fact does not hold, followed by words it does ("Arjun hates onion" -> the fact
    "hates onion"): that first word is the subject. "" when the turn is not that shape."""
    if len(clauses) != 1:
        return ""
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", clauses[0])
    if len(words) < 3:
        return ""
    first = words[0]
    low = first.lower()
    if low in _NOT_A_SUBJECT or low.endswith("ly") or _FIRST_PERSON_RE.fullmatch(low) or _stem(low) in _tokens(rest):
        return ""
    tail = _tokens(" ".join(words[1:]))
    return first if tail and tail <= (_tokens(rest) | _STOP) else ""


_USER_POSS_RE = re.compile(r"^\s*(?:the\s+)?user['’]s\s+(?P<noun>[A-Za-z][A-Za-z\-]{2,24})\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
_NOT_AN_OWNER = frozenset({"the", "his", "her", "hers", "its", "their", "your", "this", "that", "my", "our", "user", "thi", "hi", "it"})


def _possessive_owner(fact: str, user_message: str, known_names=frozenset()) -> tuple:
    """"User's swimming is on Wednesday" from "actually ashas swimming is on wednesday not thursday": the digest dropped the
    possessor (QA round 7, "the row lost its subject"). When the turn says "<name>'s <that noun>" and no first-person word, the row
    belongs to that name. ``(new_fact_or_None, lazy)``: ``lazy`` is True when the possessive had no apostrophe, so "ashas" could be
    Asha's or a name ending in s ("Chris"): it is then read ONLY as a name the member already has (``known_names``, lower-case) -
    never an invented "Chri"."""
    m = _USER_POSS_RE.match(fact or "")
    if not m:
        return None, False
    noun = m.group("noun")
    if noun.lower() in _RELATIONS or _FIRST_PERSON_RE.search(user_message or ""):
        return None, False
    pm = re.search(rf"\b(?P<o>[A-Za-z][A-Za-z\-]{{1,24}}?)(?P<p>['’]s|s)\s+{re.escape(noun)}\b", user_message or "", re.IGNORECASE)
    if not pm:
        return None, False
    stem, lazy = pm.group("o"), not pm.group("p").startswith(("'", "’"))
    owner = stem
    if lazy:
        known = {str(k).lower() for k in (known_names or ())}
        if stem.lower() in known:
            owner = stem
        elif (stem + "s").lower() in known:
            owner = stem + "s"
        else:
            return None, True
    if owner.lower() in _NOT_A_SUBJECT or owner.lower() in _NOT_AN_OWNER or len(owner) < 3:
        return None, False
    return f"{owner[:1].upper()}{owner[1:].lower()}'s {noun}{m.group('rest')}", lazy


def lazy_owner_candidate(fact: str, user_message: str) -> bool:
    """Would ``attribute`` need the member's known names to decide (a lazily typed possessive)? Pure; lets the caller fetch them only then."""
    return _possessive_owner(fact, user_message)[1]


def attribute(fact: str, user_message: str, known_names=frozenset()) -> tuple:
    """``("keep", fact)`` | ``("rewrite", new_fact)`` | ``("drop", reason)`` for one digest fact against the turn it came from."""
    owned = _possessive_owner(fact, user_message, known_names)[0]
    if owned:
        return "rewrite", owned
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
    lead = _leading_subject(rest, user_message, clauses)
    if lead:
        return "rewrite", f"{lead[:1].upper()}{lead[1:]} {rest}"
    if rels or names or len(clauses) >= 2:
        return "drop", "other_subject"
    return "keep", fact
