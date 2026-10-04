"""Conversation-shaped contacts: name/relation parsing, query classification,
duplicate detection and reply wording for the people_* intents.

Pure helpers (stdlib only, no DB / no network at import) shared by the handlers
in ``intent_router`` (``_execute_people_create_direct`` /
``_execute_people_search_direct``), the pending-offer state machine and the
``scripts/maintenance/contacts_dedupe.py`` report, so the live path and the
report agree on what "the same person" means.

Two behaviour groups, split by risk:

* UNFLAGGED bug fixes (they replace a plainly wrong answer): never search for a
  non-name ("in my contacts"), strip "my brother" out of a saved name and map it
  to the relationship field.
* FLAG-DARK behaviour (default OFF, per-call env reads):
  ``ZOE_CONTACTS_CONVERSATIONAL`` - sentence-shaped replies, de-duplicated
  lookups, merge-on-save; ``ZOE_CONTACT_OFFER_BATCH`` - one enumerated
  "add A, B and C?" question and a yes that accepts the whole set.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable, Optional

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def conversational_enabled() -> bool:
    """ZOE_CONTACTS_CONVERSATIONAL - default OFF (per-call read)."""
    return os.environ.get("ZOE_CONTACTS_CONVERSATIONAL", "").strip().lower() in _TRUTHY


def offer_batch_enabled() -> bool:
    """ZOE_CONTACT_OFFER_BATCH - default OFF (per-call read)."""
    return os.environ.get("ZOE_CONTACT_OFFER_BATCH", "").strip().lower() in _TRUTHY


# ── Relation vocabulary ──────────────────────────────────────────────────────

_REL_CANON: dict[str, str] = {
    "brother": "brother", "bro": "brother", "sister": "sister", "sis": "sister",
    "mum": "mother", "mom": "mother", "mother": "mother", "mummy": "mother",
    "mommy": "mother", "dad": "father", "father": "father", "daddy": "father",
    "son": "son", "daughter": "daughter", "wife": "wife", "husband": "husband",
    "partner": "partner", "spouse": "spouse", "girlfriend": "girlfriend",
    "boyfriend": "boyfriend", "fiance": "fiance", "fiancee": "fiancee",
    "niece": "niece", "nephew": "nephew", "cousin": "cousin",
    "aunt": "aunt", "auntie": "aunt", "aunty": "aunt", "uncle": "uncle",
    "grandmother": "grandmother", "grandma": "grandmother", "nan": "grandmother",
    "nana": "grandmother", "gran": "grandmother", "grandfather": "grandfather",
    "grandpa": "grandfather", "pop": "grandfather", "grandson": "grandson",
    "granddaughter": "granddaughter",
    "friend": "friend", "mate": "friend", "buddy": "friend", "pal": "friend",
    "best friend": "best friend", "bestie": "best friend",
    "boss": "boss", "manager": "manager", "colleague": "colleague",
    "coworker": "colleague", "co-worker": "colleague", "client": "client",
    "neighbour": "neighbor", "neighbor": "neighbor", "flatmate": "flatmate",
    "roommate": "flatmate", "landlord": "landlord", "mentor": "mentor",
    "doctor": "doctor", "dentist": "dentist", "teacher": "teacher",
    "therapist": "therapist", "plumber": "plumber", "electrician": "electrician",
}
# Longest-first so "best friend" wins over "friend" in the alternation.
_REL_ALT = "|".join(re.escape(k) for k in sorted(_REL_CANON, key=len, reverse=True))
_REL_MOD = r"(?:(?:step|half|ex|late)[\s-]?)?"
_REL_PHRASE = rf"{_REL_MOD}(?:{_REL_ALT})(?:[\s-]in[\s-]law)?"

_LEAD_REL_RE = re.compile(
    rf"^(?:(?:this\s+is|it'?s|that'?s)\s+)?(?:my|our)\s+({_REL_PHRASE})\s+(.+)$",
    re.IGNORECASE,
)
_TRAIL_REL_RE = re.compile(
    rf"^(.+?)[\s,]+(?:who(?:'s|\s+is)\s+)?(?:is\s+)?(?:my|our)\s+({_REL_PHRASE})$",
    re.IGNORECASE,
)
_PAREN_REL_RE = re.compile(
    rf"^(.+?)\s*\(\s*(?:my\s+)?({_REL_PHRASE})\s*\)$", re.IGNORECASE,
)
_ONLY_REL_RE = re.compile(rf"^(?:my|our)\s+({_REL_PHRASE})$", re.IGNORECASE)
_BAD_NAME_WORDS = frozenset({
    "who", "that", "and", "or", "is", "was", "the", "a", "an", "to", "for", "in",
    "on", "at", "with", "from", "he", "she", "they", "it", "his", "her", "their",
})


def canon_relation(phrase: str) -> str:
    """'Step-Brother' -> 'step brother', 'mum' -> 'mother', 'mate' -> 'friend'."""
    p = re.sub(r"[\s-]+", " ", (phrase or "").strip().lower())
    in_law = ""
    m = re.match(r"^(.*?)\s*in law$", p)
    if m and m.group(1):
        p, in_law = m.group(1).strip(), "-in-law"
    mod = ""
    m = re.match(r"^(step|half|ex|late)\s+(.+)$", p)
    if m:
        mod, p = m.group(1), m.group(2)
    base = _REL_CANON.get(p) or _REL_CANON.get(p.replace(" ", "-")) or p
    return (f"{mod} " if mod else "") + base + in_law


def _tidy_name(name: str) -> str:
    n = re.sub(r"\s+", " ", name or "").strip(" ,.;:!?")
    if n and n == n.lower():  # normalised/STT lower-case input -> Title Case
        n = " ".join(w[:1].upper() + w[1:] for w in n.split(" "))
    return n


def _plausible_name(rest: str) -> bool:
    words = (rest or "").split()
    if not words or len(words) > 4:
        return False
    if any(c.isdigit() for c in rest):
        return False
    return words[0].lower() not in _BAD_NAME_WORDS


def split_relation_from_name(raw: str) -> tuple[str, Optional[str]]:
    """Separate a relation phrase from a person name.

    "my brother Kyle" -> ("Kyle", "brother"); "Kyle, my mate" -> ("Kyle",
    "friend"); "Kyle (my boss)" -> ("Kyle", "boss"). Anything that is not
    clearly "<my relation> <name>" is returned unchanged with relation None, so
    a real name that merely contains a relation word ("Pop Tarr" has no "my")
    is never mangled.
    """
    s = re.sub(r"\s+", " ", raw or "").strip(" ,.;")
    for rx, name_group, rel_group in (
        (_LEAD_REL_RE, 2, 1), (_TRAIL_REL_RE, 1, 2), (_PAREN_REL_RE, 1, 2),
    ):
        m = rx.match(s)
        if m and _plausible_name(m.group(name_group)):
            return _tidy_name(m.group(name_group)), canon_relation(m.group(rel_group))
    return (_tidy_name(s) if s else s), None


WORK_RELATIONS = frozenset({"boss", "manager", "colleague", "client"})
_TAIL_REL_RE = re.compile(
    rf"(?:\bas|\bshe'?s|\bhe'?s|\bthey'?re|\bwho\s+is|\bwho'?s)\s+(?:(?:a|an|my|our)\s+)?({_REL_PHRASE})\b",
    re.IGNORECASE,
)


def relation_from_tail(raw: str) -> Optional[str]:
    """'kyle as my brother' / 'kyle, he's my brother' -> 'brother'."""
    m = _TAIL_REL_RE.search(raw or "")
    return canon_relation(m.group(1)) if m else None


def bare_relation_phrase(raw: str) -> Optional[str]:
    """'my boss' (no actual name) -> 'boss'; anything else -> None."""
    m = _ONLY_REL_RE.match(re.sub(r"\s+", " ", raw or "").strip(" ,.;"))
    return canon_relation(m.group(1)) if m else None


_GENERIC_RELS = frozenset({"", "friend", "contact", "acquaintance"})


def merge_relationship(slot_rel: Optional[str], phrase_rel: Optional[str]) -> Optional[str]:
    """The relation parsed out of the name beats an empty/generic slot value
    (the brain tool defaults to 'friend'); a specific slot value is kept."""
    slot = (slot_rel or "").strip()
    if phrase_rel and slot.lower() in _GENERIC_RELS:
        return phrase_rel
    return slot or phrase_rel or None


# ── Query classification (class 1: never search for a non-name) ───────────────

_SCAFFOLD = frozenset({
    "in", "on", "at", "my", "our", "the", "a", "an", "of", "to", "for", "from",
    "with", "about", "all", "every", "everyone", "everybody", "anyone", "people",
    "person", "contacts", "contact", "list", "lists", "saved", "stored", "have",
    "got", "i", "do", "you", "me", "who", "whos", "who's", "what", "whats",
    "what's", "is", "are", "show", "tell", "give", "it", "there",
})
_CLAUSE_WORDS = frozenset({
    "today", "tonight", "tomorrow", "yesterday", "weekend", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "and", "or",
    "going", "coming", "flying", "arriving", "visiting", "staying", "driving",
    "in", "on", "at", "from", "to", "with", "for", "about", "when", "where",
    "why", "how", "what", "that", "this", "there", "here",
})
_BOOK_RE = re.compile(r"\b(?:address\s*book|phone\s*book|contact\s*list)\b", re.IGNORECASE)
_QUERY_PUNCT_RE = re.compile(r"[\s?.!,…]+$")


def classify_contacts_query(query: str) -> tuple[str, str]:
    """('list', '') | ('rel', <relation>) | ('name', <cleaned name>) | ('none', '').

    'none' = the residue is a clause or time phrase ("on thursday"), which is
    never searched as a name.

    The brain tool and the regex lane both hand the handler whatever trailed
    "who is": "in my contacts", "on my contacts", "my brother", "Caitlin in my
    contacts". Only a residue that can be a name is searched as one.
    """
    q = _BOOK_RE.sub("contacts", _QUERY_PUNCT_RE.sub("", (query or "").strip()))
    words = q.split()
    lower = [w.lower() for w in words]
    lead = 0
    while lead < len(words) and lower[lead] in _SCAFFOLD:
        lead += 1
    if lead == len(words):
        return "list", ""
    end = len(words)
    while end > lead and lower[end - 1] in _SCAFFOLD:
        end -= 1
    core = words[lead:end]
    phrase = " ".join(core)
    if len(core) > 4 or "," in phrase or _CLAUSE_WORDS & {w.lower() for w in core}:
        return "none", ""  # a clause / time phrase, never a person's name
    if lead and lower[lead - 1] in ("my", "our", "the") and \
            re.fullmatch(_REL_PHRASE, phrase, re.IGNORECASE):
        return "rel", canon_relation(phrase)
    return "name", phrase


# ── Duplicate detection (class 4b) ───────────────────────────────────────────


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"\s+", (name or "").strip().casefold()) if t]


def _rel_norm(rel: Optional[str]) -> str:
    r = (rel or "").strip().casefold()
    return canon_relation(r) if r else ""


def relations_compatible(a: Optional[str], b: Optional[str]) -> bool:
    ra, rb = _rel_norm(a), _rel_norm(b)
    return not ra or not rb or ra == rb


def duplicate_kind(a_name: str, a_rel: Optional[str],
                   b_name: str, b_rel: Optional[str]) -> Optional[str]:
    """How record A relates to record B when they look like the same person.

    'same'    - identical names, compatible relation
    'a_stub'  - A is a first-name-only record of B's fuller name
    'b_stub'  - B is a first-name-only record of A's fuller name
    None      - different people (different first name, different last name, or
                clashing relations).
    """
    ta, tb = _tokens(a_name), _tokens(b_name)
    if not ta or not tb or ta[0] != tb[0] or not relations_compatible(a_rel, b_rel):
        return None
    if ta == tb:
        return "same"
    if len(ta) == 1 and len(tb) >= 2:
        return "a_stub"
    if len(tb) == 1 and len(ta) >= 2:
        return "b_stub"
    return None


def collapse_duplicates(rows: Iterable[dict]) -> list[dict]:
    """One row per person: a first-name-only row folds into the fuller record
    with the same first name and a compatible relation (blank contact fields on
    the kept row are filled from the folded one). Order is preserved."""
    rows = [dict(r) for r in rows]
    drop: set[int] = set()
    for i, a in enumerate(rows):
        for j, b in enumerate(rows):
            if i == j or j in drop or i in drop:
                continue
            kind = duplicate_kind(a.get("name", ""), a.get("relationship"),
                                  b.get("name", ""), b.get("relationship"))
            if kind == "a_stub" or (kind == "same" and j < i):
                keep, gone = b, a
                drop.add(i)
            elif kind == "b_stub":
                keep, gone = a, b
                drop.add(j)
            else:
                continue
            for k in ("relationship", "birthday", "phone", "email", "notes"):
                if not keep.get(k) and gone.get(k):
                    keep[k] = gone[k]
            # Remember which ids folded in, so facts linked to the stub still show.
            keep.setdefault("_merged_ids", []).extend(
                [gone.get("id"), *gone.get("_merged_ids", [])])
    return [r for n, r in enumerate(rows) if n not in drop]


def find_duplicate_groups(rows: Iterable[dict]) -> list[list[dict]]:
    """Groups of >=2 likely-duplicate records (by user), for the dry-run report.
    Never decides anything: a person reads the report."""
    rows = [dict(r) for r in rows]
    parent = list(range(len(rows)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            if a.get("user_id") != b.get("user_id"):
                continue
            if duplicate_kind(a.get("name", ""), a.get("relationship"),
                              b.get("name", ""), b.get("relationship")):
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    return [g for g in groups.values() if len(g) > 1]


# ── Reply wording (flag: ZOE_CONTACTS_CONVERSATIONAL) ────────────────────────

LIST_NAME_CAP = 12


def _plural(label: str) -> str:
    if label.endswith(("s", "x", "ch", "sh")):
        return label + "es"
    if label.endswith("y") and label[-2:-1] not in "aeiou":
        return label[:-1] + "ies"
    return label + "s"


def _join_and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


join_and = _join_and


def format_created(name: str, relationship: Optional[str]) -> str:
    rel = (relationship or "").strip()
    return f"Added {name}, your {rel}." if rel else f"Added {name} to your contacts."


def format_contact_list(rows: list[dict], cap: int = LIST_NAME_CAP) -> str:
    """'You have 7 contacts. Friends: A and B. Brother: C. ... And 2 more.'"""
    people = collapse_duplicates(rows)
    total = len(people)
    if not total:
        return "You don't have any contacts saved yet."
    shown = people[:cap]
    groups: dict[str, list[str]] = {}
    for p in shown:
        label = _rel_norm(p.get("relationship")) or "other"
        groups.setdefault(label, []).append(str(p.get("name") or "?"))
    order = sorted(groups, key=lambda k: (k == "other", -len(groups[k]), k))
    parts = []
    for label in order:
        names = groups[label]
        head = (_plural(label) if len(names) > 1 else label).capitalize()
        if label == "other":
            head = "Others"
        parts.append(f"{head}: {_join_and(names)}.")
    noun = "contact" if total == 1 else "contacts"
    out = f"You have {total} {noun}. " + " ".join(parts)
    if total > len(shown):
        out += f" And {total - len(shown)} more."
    return out


def _first_name(name: str) -> str:
    return (name or "").split()[0] if (name or "").split() else ""


def fact_clause(text: str, name: str, pattern_type: str = "") -> str:
    """One stored fact as a clause that follows "You've told me ...". Compact
    'Name: value' rows are re-voiced ("Name: March 15" + birthday -> "Name's
    birthday is March 15"); a sentence that already names the person is kept."""
    t = re.sub(r"\s+", " ", text or "").strip().rstrip(".")
    first = _first_name(name)
    m = re.match(r"^([A-Za-z][\w' -]{0,40}):\s*(.+)$", t)
    if m:
        who, value = m.group(1).strip(), m.group(2).strip()
        if first and who.lower().startswith(first.lower()):
            kind = (pattern_type or "").strip().lower()
            if kind == "birthday":
                return f"{first}'s birthday is {value}"
            return f"{first}: {value}"
    return t


def format_lookup(people: list[dict], query: str,
                  facts: Optional[dict[str, list[str]]] = None) -> str:
    """Sentence-shaped answer for a name lookup (rows already de-duplicated)."""
    facts = facts or {}
    if not people:
        q = (query or "").strip()
        return f'I don\'t have anyone called "{q}" in your contacts.' if q else \
            "You don't have any contacts saved yet."

    def one(p: dict) -> str:
        name = str(p.get("name") or "?")
        rel = (p.get("relationship") or "").strip()
        return f"{name} is your {rel}." if rel else f"{name} is in your contacts."

    if len(people) == 1:
        p = people[0]
        s = one(p)
        fs = facts.get(str(p.get("id")), [])[:2]
        if fs:
            s += " You've told me " + ", and ".join(fs) + "."
        return s
    bits = []
    for p in people[:6]:
        rel = (p.get("relationship") or "").strip()
        bits.append(f"{p.get('name') or '?'}, your {rel}" if rel else str(p.get("name") or "?"))
    more = len(people) - 6
    tail = f", and {more} more" if more > 0 else ""
    return (f"I have {len(people)} people matching \"{(query or '').strip()}\": "
            f"{_join_and(bits)}{tail}.")


# ── Pending contact offers (class 3) ─────────────────────────────────────────

OFFER_BATCH_MAX = 5


def offer_question(offers: list[dict], safe=lambda v: v) -> str:
    """The ONE question the brain voices for the whole pending set.

    A single offer keeps the legacy wording byte-for-byte; several become
    "Would you like me to add A (your niece), B and C to your contacts?" so one
    yes can answer all of them.
    """
    parts: list[tuple[str, str]] = []
    for o in offers[:OFFER_BATCH_MAX]:
        name = safe(str(o.get("name") or ""))
        if not name:
            continue
        parts.append((name, safe(str(o.get("relationship") or ""))))
    if not parts:
        return ""
    if len(parts) == 1:
        n, r = parts[0]
        return f"Would you like me to add {n}{f' (your {r})' if r else ''} as a contact?"
    items = [f"{n} (your {r})" if r else n for n, r in parts]
    return f"Would you like me to add {_join_and(items)} to your contacts?"


_ALL_WORDS = frozenset({
    "all", "everyone", "everybody", "both", "whole", "family", "entire", "lot",
    "rest", "kids", "children", "everything",
})
_ONLY_WORDS = frozenset({"just", "only"})
_SINGULAR_PRONOUNS = frozenset({"her", "him", "it"})
BATCH_FIRST_EXTRA = frozenset({"all", "everyone", "everybody", "both", "just", "only"})
BATCH_FILLER_EXTRA = _ALL_WORDS | _ONLY_WORDS | frozenset({
    "his", "their", "hers", "them", "those", "these", "of", "and",
})


def batch_reply_scope(tokens: list[str], offers: list[dict],
                      affirm_first: frozenset, decline_first: frozenset,
                      filler: frozenset) -> Optional[tuple[str, list[dict], list[dict]]]:
    """Map a short reply onto the surfaced offer SET.

    Returns (kind, offers_to_act_on, offers_to_dismiss) or None when the reply
    is not a clean answer to the enumerated question (the caller then falls back
    to the legacy one-offer binding or normal routing).

      "yes" / "yes please" / "all of them" / "add his whole family"  -> accept all
      "yes Rodrigo and Jessika"                                      -> accept those
      "just Rodrigo" / "only Rodrigo"                                -> accept him, drop the rest
      "no" / "no thanks"                                             -> dismiss all
    """
    if not tokens or not offers:
        return None
    first = tokens[0]
    name_tokens = [
        {t for t in str(o.get("name") or "").lower().split() if t} for o in offers
    ]
    rest = tokens[1:] if first in (affirm_first | decline_first) else tokens
    matched: set[int] = set()
    for t in rest:
        hits = [i for i, nt in enumerate(name_tokens) if t in nt]
        if len(hits) > 1:
            return None  # one word naming several people (two Caitlins): ambiguous
        matched.update(hits)
    for t in rest:
        if t not in filler and t not in BATCH_FILLER_EXTRA and \
                not any(t in nt for nt in name_tokens):
            return None
    if first in decline_first:
        # "no Rodrigo" is a partial refusal: leave it to the brain.
        return None if matched else ("dismiss", list(offers), [])
    if first not in (affirm_first | BATCH_FIRST_EXTRA):
        return None
    if any(t in ("dont", "don't", "not", "no") for t in rest):
        return ("dismiss", list(offers), [])
    if len(offers) > 1 and not matched and any(t in _SINGULAR_PRONOUNS for t in rest) \
            and not any(t in _ALL_WORDS for t in rest):
        return None  # "yes add her" with several offers: legacy oldest-first binding
    if matched:
        chosen = [o for i, o in enumerate(offers) if i in matched]
        if any(t in _ALL_WORDS for t in rest) and not any(t in _ONLY_WORDS for t in rest):
            return ("accept", list(offers), [])
        rejected = [o for o in offers if o not in chosen] if \
            any(t in _ONLY_WORDS for t in rest) else []
        return ("accept", chosen, rejected)
    return ("accept", list(offers), [])
