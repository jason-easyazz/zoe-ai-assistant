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
# wife", "Casey: wife of Tom", "Casey (wife)", "Tom's wife is Casey", "my wife is Casey",
# "Casey, my wife". "Tom's wife" alone names the OWNER, not a holder, so it is not a claim.
_OWNER = rf"(?:{_NAME}|my|his|her|their|our)"
_CLAIM_RES = (
    re.compile(
        rf"\b(?P<n>{_NAME})\s+(?:is|was|are|were)\s+(?:(?:the|a|an|his|her|their|my|our|user's|"
        rf"{_NAME}'s)\s+)*(?:\w+\s+)?(?P<r>{_ROLE_ALT})s?\b"
    ),
    re.compile(rf"^\s*(?P<n>{_NAME})\s*:\s*(?:\w+\s+){{0,2}}(?P<r>{_ROLE_ALT})s?\b"),
    re.compile(rf"\b(?P<n>{_NAME})\s*\(\s*(?:\w+\s+)?(?P<r>{_ROLE_ALT})s?\s*\)"),
    re.compile(
        rf"\b{_OWNER}(?:'s)?\s+(?:\w+\s+)?(?P<r>{_ROLE_ALT})s?\s+(?:is|was|are|were)\s+"
        rf"(?:named\s+|called\s+)?(?P<n>{_NAME})\b"
    ),
    re.compile(
        rf"\b(?P<n>{_NAME}),?\s+(?:who\s+is\s+)?(?:my|his|her|their|our)\s+(?:\w+\s+)?"
        rf"(?P<r>{_ROLE_ALT})s?\b"
    ),
)


def role_claims(fact: str) -> list[tuple[str, str]]:
    """``[(name, role)]`` the fact asserts: NAME holds ROLE."""
    out: list[tuple[str, str]] = []
    for rx in _CLAIM_RES:
        for m in rx.finditer(fact or ""):
            claim = (m.group("n"), m.group("r").lower())
            if claim not in out:
                out.append(claim)
    return out


def _variants(role: str) -> frozenset[str]:
    from memory_quality import _role_variants

    return _role_variants(role)


_NEAR = 4  # tokens between a name and its role when they sit side by side
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [x for x in _SENT_SPLIT.split(text or "") if x.strip()]


_POSSESSIVES = frozenset({"my", "his", "her", "their", "our"})
_BREAKS = frozenset({"and", "or", "but", ";"})


def _adjacent(raw: list[str], tokens: list[str], i: int, j: int) -> bool:
    """Name at ``i`` and role at ``j`` sit side by side: a few words apart with no list
    separator, another name or conjunction between them ("my friend Jordan", "Casey is the
    wife", "Jordan, my friend"). "wife and Riley" or "Casey, Riley" do not qualify."""
    lo, hi = sorted((i, j))
    between = tokens[lo + 1: hi]
    if hi - lo > _NEAR or any(t in _BREAKS for t in between):
        return False
    if any(w[:1].isupper() and w.lower() not in ("i",) for w in raw[lo + 1: hi]):
        return False  # another name sits between them
    if "," in between:
        return between[0] == "," and (len(between) == 1 or between[1] in _POSSESSIVES) and len(between) <= 3
    return True


def role_assignment_supported(name: str, role: str, source_text: str) -> bool:
    """Did the user's own text put ``name`` and ``role`` together?

    Same SENTENCE (a line break ends one), and linked: the two side by side
    ("my friend Jordan", "Jordan, my friend"), or the name followed by "is ... <role>"
    ("Casey, who I married in Perth on Saturday, is my wife"), or the role followed by
    "name is / called <name>" ("my wife is a nurse at the hospital and her name is Casey").
    An intro line that merely mentions "a partner and two children" followed by names on
    other lines, or a name list after it, assigns nothing."""
    variants = {v.lower() for v in _variants(role.lower())}
    role_alt = "|".join(sorted((re.escape(v) for v in variants), key=len, reverse=True))
    first = re.escape(name.split()[0])
    first_l = name.split()[0].lower()
    for sent in _sentences(source_text):
        raw = re.findall(r"[A-Za-z']+|[,;]", sent)
        tokens = [t.lower() for t in raw]
        hits = [i for i, t in enumerate(tokens) if t == first_l]
        roles = [i for i, t in enumerate(tokens)
                 if t in variants or (t.endswith("s") and t[:-1] in variants)]
        if not hits or not roles:
            continue
        if any(_adjacent(raw, tokens, h, r) for h in hits for r in roles):
            return True
        # name [, who ... ,] is [up to 3 words] role — the verb must follow the name within its
        # own clause (no comma/"and" between), so "Jordan is my friend, Casey is the wife"
        # does not make Jordan a wife.
        if re.search(rf"\b{first}\b(?:\s*,\s*who\b[^.!?\n,]*,)?[\w'\u2019 ]{{0,30}}?\b(?:is|was|are|were)\s+"
                     rf"(?:[\w'\u2019]+\s+){{0,3}}(?:{role_alt})s?\b", sent, re.IGNORECASE):
            return True
        if re.search(rf"\b(?:{role_alt})s?\b[^.!?\n]*?\b(?:name|called|named)\s+(?:is\s+)?{first}\b",
                     sent, re.IGNORECASE):
            return True
        # The role noun followed, in this ONE sentence, by a list that includes the name:
        # "Dana has two kids, Mika and Biscuit" / "Dana's kids are Mika and Biscuit" / "my two
        # sisters, Ana and Bea". The user stated the role for every listed name; a name followed
        # by its own verb ("my friend, Casey is the wife") is a new clause, not a list member.
        listed = (rf"\b(?:{role_alt})s?\b\s*(?:[:,]|\b(?:are|is|named|called)\b)\s*(?:named\s+|called\s+)?"
                  rf"(?-i:(?:[A-Z][\w'\u2019-]*(?:\s[A-Z][\w'\u2019-]*)?\s*(?:,\s*(?:and\s+)?|\s+and\s+|&\s*))*)"
                  rf"{first}\b(?!\s+(?:is|was|are|were|has|have|had)\b)")
        if re.search(listed, sent, re.IGNORECASE):
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


_MONTH_ALT = "|".join(m.lower() for m in
                      ("January", "February", "March", "April", "May", "June", "July", "August",
                       "September", "October", "November", "December")) + "|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec"
_WRITTEN_DATE_RE = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTH_ALT})\b|\b(?:{_MONTH_ALT})\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
    re.IGNORECASE)
_QUANTITY_RE = re.compile(
    r"[\d./\s]+(?:g|kg|ml|l|cups?|tsp|tbsp|x|pcs?|loaf|loaves|dozen|litres?|liters?|packs?|"
    r"tins?|cans?|bottles?|bags?|boxes|box)?\.?", re.IGNORECASE)
_PERSON_WORDS_RE = re.compile(
    r"\b(?:wife|husband|kids?|children|family|friends?|partner|son|daughter|girls|boys|parents?|"
    r"brother|sister|mum|mom|dad)\b", re.IGNORECASE)


def _is_dob(rest: str) -> bool:
    """A date of birth: a numeric date WITH a year, or a written day+month."""
    from date_locale import parse_numeric_date

    nd = parse_numeric_date(rest)
    return bool((nd and nd.year) or _WRITTEN_DATE_RE.search(rest or ""))


def is_unlabelled_roster(text: str) -> bool:
    """A pasted list of PEOPLE with no name tied to a role anywhere in the message.

    Person-like evidence is required — a shopping list or a recipe ("Milk - 2", "Flour -
    200g") is not a roster: entries whose detail is only a quantity are ignored, and the
    rest must be 3+ lines with a date of birth on at least 2 of them, or family words
    (wife, kids, family, friend...) in the message."""
    entries = [(n, r) for n, r in roster_entries(text)
               if _is_dob(r) or not _QUANTITY_RE.fullmatch(r.strip())]
    if len(entries) < 3:
        return False
    dobs = sum(1 for _n, r in entries if _is_dob(r))
    if dobs < 2 and not _PERSON_WORDS_RE.search(text):
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
