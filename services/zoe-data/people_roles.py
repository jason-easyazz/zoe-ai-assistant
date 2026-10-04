"""Roles are STATED, never guessed: gender and family role never come from a name.

A pasted list of names with no stated roles must not come back with a wife, a husband or
children assigned by the 4B model from first names. A name says nothing about whether
someone is a wife, a husband or a daughter. Rules enforced here, at the source every extractor shares:

* ``PROMPT_RULES`` — the line every extraction prompt carries (person extractor,
  turn/nightly digest, contact-offer detector).
* ``named_role_claim_unsupported`` — a deterministic backstop for a 4B model that
  ignores the prompt: a fact that makes NAME the holder of a role ("Casey is the wife",
  "Casey: wife of Jordan") is kept only when the user's own text puts that name and
  that role together — the same line, and close together when the line is a name list.
  Intro words ("a partner and two children") on one line and names on the next support
  nothing: that is exactly the guess being refused.
* ``is_unlabelled_roster`` / ``roster_reply`` — the neutral restatement + the one short
  question ("Who's who?") for a pasted list of people whose roles are not stated.

Pets are not children: ``PET_WORDS`` / ``PROMPT_RULES`` keep "Biscuit is their dog" from
ever being counted as a kid.
"""
from __future__ import annotations

import re
from typing import Optional

PET_WORDS = (
    "dog", "puppy", "pup", "cat", "kitten", "bird", "parrot", "budgie", "rabbit", "bunny",
    "hamster", "guinea pig", "fish", "horse", "pony", "turtle", "tortoise", "snake",
    "lizard", "ferret", "pet",
)
_PET_ALT = "|".join(sorted((re.escape(w) for w in PET_WORDS), key=len, reverse=True))

PROMPT_RULES = (
    "Never infer a person's gender, family role or relationship from their NAME or from "
    "where they sit in a list. A role (wife, husband, son, daughter, girls, boys, friend) "
    "counts only if the user said it about THAT person. If the user lists people without "
    "saying who is who, record no role for them. A pet (dog, cat...) is never a child, "
    "kid, son or daughter."
)

_NAME = r"[A-Z][a-z]{1,30}(?:\s[A-Z][a-z]{1,20})?"
_ROLE_ALT = (
    r"wife|husband|partner|girlfriend|boyfriend|fianc[eé]e?|spouse"
    r"|son|daughter|kid|children|child|girl|boy|baby"
    r"|friend|mate|buddy|bestie"
    r"|brother|sister|mum|mom|mother|dad|father|grandma|grandmother|grandpa"
    r"|grandfather|aunt|uncle|niece|nephew|cousin|parent|sibling|grandparent"
    r"|colleague|coworker|boss|neighbou?r"
)
_ROLE_RE = re.compile(rf"\b({_ROLE_ALT})s?\b", re.IGNORECASE)

# NAME is the holder of the role in the fact: "Casey is the wife", "Casey is Tom's
# wife", "Casey: wife of Tom", "Casey (wife)". "Tom's wife" alone names the OWNER, not
# a holder, so it is deliberately not a claim here.
_CLAIM_RES = (
    re.compile(
        rf"\b(?P<n>{_NAME})\s+(?:is|was|are|were)\s+(?:(?:the|a|an|his|her|their|my|our|user's|"
        rf"{_NAME}'s)\s+)*(?:\w+\s+)?(?P<r>{_ROLE_ALT})s?\b"
    ),
    re.compile(rf"^\s*(?P<n>{_NAME})\s*:\s*(?:\w+\s+){{0,2}}(?P<r>{_ROLE_ALT})s?\b"),
    re.compile(rf"\b(?P<n>{_NAME})\s*\(\s*(?:\w+\s+)?(?P<r>{_ROLE_ALT})s?\s*\)"),
)


def role_claims(fact: str) -> list[tuple[str, str]]:
    """``[(name, role)]`` the fact asserts: NAME holds ROLE."""
    out: list[tuple[str, str]] = []
    for rx in _CLAIM_RES:
        for m in rx.finditer(fact or ""):
            out.append((m.group("n"), m.group("r").lower()))
    return out


def _variants(role: str) -> frozenset[str]:
    from memory_quality import _role_variants

    return _role_variants(role)


_CAP_TOKEN = re.compile(r"\b[A-Z][a-z]{1,30}\b")
_NEAR = 4  # tokens between a name and its role inside a name list


def _name_hits(line: str, name: str) -> list[int]:
    tokens = re.findall(r"[A-Za-z']+", line.lower())
    first = name.split()[0].lower()
    return [i for i, t in enumerate(tokens) if t == first]


def role_assignment_supported(name: str, role: str, source_text: str) -> bool:
    """Did the user's own text put ``name`` and ``role`` together?

    Same LINE; and when the line is a list of names (3+ capitalised words) the two must
    also sit within a few words of each other, so "a partner and two children: Ann, Bo, Cy"
    assigns nothing."""
    variants = {v.lower() for v in _variants(role.lower())}
    for line in (source_text or "").splitlines():
        tokens = re.findall(r"[A-Za-z']+", line.lower())
        hits = _name_hits(line, name)
        if not hits:
            continue
        roles = [i for i, t in enumerate(tokens)
                 if t in variants or (t.endswith("s") and t[:-1] in variants)]
        if not roles:
            continue
        if len(_CAP_TOKEN.findall(line)) < 3:
            return True
        if any(abs(h - r) <= _NEAR for h in hits for r in roles):
            return True
    return False


def named_role_claim_unsupported(fact: str, source_text: str) -> bool:
    """True when ``fact`` makes a NAME the holder of a role that ``source_text`` never
    says about that name. Facts with no named-role claim pass untouched."""
    for name, role in role_claims(fact):
        if not role_assignment_supported(name, role, source_text):
            return True
    return False


_LOOSE_ROLES = frozenset({"friend", "mate", "buddy", "bestie", "colleague", "coworker", "boss",
                          "neighbour", "neighbor"})


def value_role_unsupported(name: str, value: str, source_text: str, *,
                           family_only: bool = False) -> bool:
    """For the person-extractor LLM's ``(name, value)`` pair ("Casey Smith", "wife of
    Jordan Smith"): True when the value gives ``name`` a role the text never ties to
    that name. Owner-anchored roles ("user's friend") are checked like any other: the
    user must have said it about this person. ``family_only`` skips the loose social labels
    (friend, colleague...) for callers where those are a harmless summary, not a guess."""
    roles = {m.group(1).lower() for m in _ROLE_RE.finditer(value or "")}
    if not roles:
        return False
    # "great with kids" / "works with the boss" describe something else: only a role in
    # head position (the value starts with it, after an optional article/adjective) is a
    # claim that THIS person holds it.
    head = re.match(
        rf"^(?:(?:a|an|the|his|her|their|my|our|user's)\s+)?(?:\w+['\u2019]s\s+)?(?:\w+\s+){{0,1}}"
        rf"({_ROLE_ALT})s?\b",
        (value or "").strip(), re.IGNORECASE)
    if not head:
        return False
    if family_only and head.group(1).lower() in _LOOSE_ROLES:
        return False  # "friend" from "had lunch with Sarah" is a loose label, not a gender/family guess
    return not role_assignment_supported(name, head.group(1).lower(), source_text)


# ── Pasted list of people, no roles ───────────────────────────────────────────
_ROSTER_LINE = re.compile(
    rf"^\s*(?P<n>{_NAME}(?:\s[A-Z][a-z]{{1,20}})?)\s*[-–—:]\s*(?P<rest>\S.*)$"
)


def roster_entries(text: str) -> list[tuple[str, str]]:
    """``[(name, rest-of-line)]`` for each ``Name - detail`` line of a pasted list."""
    out = []
    for line in (text or "").splitlines():
        m = _ROSTER_LINE.match(line)
        if m:
            out.append((m.group("n"), m.group("rest").strip()))
    return out


def is_unlabelled_roster(text: str) -> bool:
    """3+ ``Name - detail`` lines and NO name tied to a role anywhere in the message."""
    entries = roster_entries(text)
    if len(entries) < 3:
        return False
    for name, _rest in entries:
        for role in {m.group(1).lower() for m in _ROLE_RE.finditer(text)}:
            if role_assignment_supported(name, role, text):
                return False
    return True


def roster_reply(text: str) -> str:
    """Neutral restatement (names + dates, no roles) and the ONE short question."""
    from date_locale import normalize_numeric_dates

    parts = []
    for name, rest in roster_entries(text):
        rest = normalize_numeric_dates(rest).strip(" .")
        parts.append(f"{name} ({rest})" if rest else name)
    listing = "; ".join(parts)
    return (f"Here's what I've got: {listing}. "
            "I haven't guessed who's who \u2014 which one is your friend?")


def pet_kind(word: str) -> Optional[str]:
    w = (word or "").strip().lower()
    return w if w in PET_WORDS else None
