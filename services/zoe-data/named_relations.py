"""Named people listed after a relationship noun are KEPT, never summarised to a count.

"My friend Dana Whitfield has two kids, Mika and Biscuit." used to leave the store with
"Dana Whitfield has two kids" and no children's names: nothing deterministic read the list
after the noun (person_extractor._REL_RE only knows "A is B's <role>" / "A and B are
<role>s"), the per-turn LLM passes are asked for a "concise fact" (the person LLM invents
"mother of Mika and Biscuit" and people_roles then drops it; the digest may emit one
fact per child or only the count), and the role guard used to refuse a listed name as an
"unstated role" (people_roles.role_assignment_supported). This module is the
deterministic net under all of them. It reads the user's own sentence and returns every
"<owner> has <count> <noun>, <Name> and <Name>" / "<owner>'s <noun> are <Name>..." /
"my <noun> are <Name>..." it contains:

* the stored fact keeps the names, in the shape correction_apply already rewrites
  ("Dana Whitfield has two kids, Mika and Biscuit.") - so "Biscuit is their dog" later
  takes Biscuit OUT of the list and the count;
* children / siblings / parents of a NAMED owner also become people rows linked to the
  owner in the Postgres people graph (people + person_relationships, edge direction as
  "Mika is Dana's son": a = the child, b = the parent). The speaker has no node in that
  graph, so a list the speaker owns ("my kids are ...", "I have two kids, ...") is kept
  as a fact only;
* pets are facts only: a pet is never minted as a person (person_extractor._ROLE_TO_TYPE).

Roles here are STATED, not guessed: the names must follow the noun in the same sentence
(people_roles: an intro line plus a name list on other lines assigns nothing).
Pure extraction (extract_named_relations) has no I/O; apply_named_relations is the
only part that touches the database / memory store and is called from
person_extractor.process_text. Never raises into the turn.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

MAX_NAMES = 8

# -- vocabulary ---------------------------------------------------------------

_GROUP_NOUNS = dict(
    child=r"kids?|children|child|sons?|daughters?",
    sibling=r"siblings?|brothers?|sisters?",
    parent=r"parents?",
)


def _pet_alt() -> str:
    from people_roles import PET_WORDS

    words = sorted((re.escape(w) for w in PET_WORDS if w != "pet"), key=len, reverse=True)
    return r"pets?|puppies|(?:" + "|".join(words) + r")(?:s|es)?"


def _noun_alt() -> str:
    return "|".join(f"(?:{alt})" for alt in [*_GROUP_NOUNS.values(), _pet_alt()])


_COUNT_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_NUM_WORD = dict((i + 1, w) for i, w in enumerate(_COUNT_WORDS))
_COUNT_RE = r"(?:(?P<count>a|an|\d{1,2}|" + "|".join(_COUNT_WORDS) + r")\s+)?"

# Capitalised words that follow a relationship noun but are not a person's name
# ("Dana's parents are Italian", "my kids are Awesome").
_NOT_A_NAME = frozenset(
    "italian greek indian chinese japanese korean australian american british english french "
    "german irish scottish welsh spanish polish dutch russian vietnamese lebanese turkish "
    "catholic christian jewish muslim buddhist hindu atheist aussie kiwi maori canadian "
    "awesome great fine good well happy sad sick healthy tall little big busy young old "
    "twins both all none some many few several".split()
)

# -- patterns -----------------------------------------------------------------

_TOKEN = r"[A-Z][a-z]{1,30}(?:-[A-Z][a-z]{1,20})?"
_MEMBER = rf"(?-i:{_TOKEN}(?:\s{_TOKEN})?)"
_SEP = r"(?:\s*,\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*)"
_LIST = rf"{_MEMBER}(?:{_SEP}{_MEMBER})*"
_OWNER = rf"(?-i:{_TOKEN}(?:\s{_TOKEN})?)"

_PUNCT_SEP = r"(?:\s+(?:named|called)\s+|\s*[:,]\s*|\s+[-–—]\s+)"
_COPULA_SEP = r"(?:\s*[:,]\s*|\s+(?:are|is)\s+(?:named\s+|called\s+)?|\s+(?:named|called)\s+)"


def _compile(pattern: str) -> "re.Pattern[str]":
    return re.compile(pattern.replace("NOUN", _noun_alt()), re.IGNORECASE)


# "<Owner> has two kids, Mika and Biscuit" / "<Owner>, who has a son called Mika"
_HAS_RE = _compile(
    rf"(?P<owner>{_OWNER})(?:\s*,\s*who|\s+who)?(?:['’]s\s+got|\s+(?:has|had|have)(?:\s+got)?)\s+"
    rf"{_COUNT_RE}(?P<noun>NOUN)(?:{_PUNCT_SEP})(?P<names>{_LIST})"
)
# "<Owner>'s kids are Mika and Biscuit" / "<Owner>'s two kids: Mika, Biscuit" / "<Owner>'s son is Mika"
_POSS_RE = _compile(
    rf"(?P<owner>{_OWNER})['’]s\s+{_COUNT_RE}(?P<noun>NOUN)(?:{_COPULA_SEP})(?P<names>{_LIST})"
)
# "I have two kids, Mika and Biscuit" / "we've got two dogs named Rex and Fido"
_I_HAVE_RE = _compile(
    rf"\b(?:i|we)(?:['’]ve|\s+have|\s+had)(?:\s+got)?\s+{_COUNT_RE}(?P<noun>NOUN)"
    rf"(?:{_PUNCT_SEP})(?P<names>{_LIST})"
)
# "my kids are Mika and Biscuit" / "my two sisters, Ana and Bea"
_MY_RE = _compile(
    rf"\b(?:my|our)\s+{_COUNT_RE}(?P<noun>NOUN)(?:{_COPULA_SEP})(?P<names>{_LIST})"
)


@dataclass(frozen=True)
class NamedRelation:
    owner: Optional[str]          # None = the speaker
    group: str                    # child | sibling | parent | pet
    noun: str                     # as typed, lower-cased ("kids", "son", "dogs")
    count: str                    # as typed ("two", "a") or ""
    names: tuple
    copula: bool = False          # matched an "are/is" shape (no "named/called/,/:" separator)

    @property
    def plural(self) -> bool:
        n = self.noun
        return n in ("kids", "children", "siblings", "pets", "puppies", "fish") or (
            n.endswith("s") and n not in ("bus",))

    def fact(self) -> str:
        """The stored sentence: the user's own count and noun, every name kept."""
        who = self.owner or "User"
        listing = _join(self.names)
        if self.group == "parent":
            verb = "are" if len(self.names) > 1 else "is"
            return f"{who}'s {self.noun} {verb} {listing}."
        count = self.count
        if not count and len(self.names) >= 2:
            count = _NUM_WORD.get(len(self.names), str(len(self.names)))
        if not count and not self.plural:
            count = "a"
        if not count:
            return f"{who}'s {self.noun} include {listing}."
        return f"{who} has {count} {self.noun}, {listing}."


def _join(names) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _group_of(noun: str) -> str:
    n = noun.lower()
    for group, alt in _GROUP_NOUNS.items():
        if re.fullmatch(alt, n):
            return group
    return "pet"


def _split_names(raw: str) -> list:
    from person_extractor import _NON_NAME_TOKENS, _looks_like_person_name

    out: list = []
    for part in re.split(r"\s*,\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*", raw.strip()):
        tokens = part.split()
        # a trailing capitalised stop word ("Mika Friday") is not part of the name
        while len(tokens) > 1 and tokens[-1].lower() in _NON_NAME_TOKENS:
            tokens.pop()
        name = " ".join(tokens)
        if (name and name.lower() not in _NOT_A_NAME and tokens[0].lower() not in _NOT_A_NAME
                and _looks_like_person_name(name) and name not in out):
            out.append(name)
    return out[:MAX_NAMES]


def _clean_owner(raw: str) -> Optional[str]:
    """Then Dana -> Dana: drop leading sentence-openers; None if nothing name-like is left."""
    from person_extractor import _looks_like_person_name

    tokens = raw.split()
    while tokens and not _looks_like_person_name(tokens[0]):
        tokens.pop(0)
    return " ".join(tokens) if tokens else None


def extract_named_relations(text: str) -> list:
    """Every "<owner> <noun> <Names>" list the sentence states. Pure, no I/O."""
    text = " ".join((text or "").split())
    if not text:
        return []
    out: list = []
    seen: set = set()
    for rx, speaker in ((_HAS_RE, False), (_POSS_RE, False), (_I_HAVE_RE, True), (_MY_RE, True)):
        for m in rx.finditer(text):
            noun = m.group("noun").lower()
            group = _group_of(noun)
            owner = None
            if not speaker:
                owner = _clean_owner(m.group("owner"))
                if not owner:
                    continue  # "Her kids are ..." - a pronoun owner needs an antecedent
            names = _split_names(m.group("names"))
            if owner:
                names = [n for n in names if n.lower() != owner.lower()]
            if not names:
                continue
            count = (m.group("count") or "").lower()
            copula = rx in (_POSS_RE, _MY_RE) and bool(
                re.search(r"\b(?:are|is)\b", text[m.end("noun"):m.start("names")], re.IGNORECASE))
            rel = NamedRelation(owner, group, noun, count, tuple(names), copula)
            # "Dana's parents are Italian": a plural noun with ONE name and no count in an
            # "are" shape is not a list of people.
            if rel.copula and rel.plural and len(rel.names) < 2 and not rel.count:
                continue
            # The speaker's OWN single-name shapes ("my son is Mika", "my dog is Rex") belong to
            # memory_extractor's templates; only lists / plural nouns are taken here.
            if speaker and group == "pet" and not rel.plural:
                continue
            if speaker and not rel.plural and len(rel.names) < 2:
                continue
            key = (rel.owner, rel.group, rel.names)
            if key in seen:
                continue
            seen.add(key)
            out.append(rel)
    return out


# -- write path ---------------------------------------------------------------

def _edge_for(rel: NamedRelation, name: str):
    """(name_a, name_b, rel_type, rel_group) for one listed name, in the direction
    person_extractor._REL_RE uses ("Mika is Dana's son": a = Mika, b = Dana, "parent").
    Pets are never minted as people, and the speaker has no node."""
    if not rel.owner:
        return None
    if rel.group == "child":
        return name, rel.owner, "parent", "family"
    if rel.group == "sibling":
        return name, rel.owner, "sibling", "family"
    if rel.group == "parent":
        return rel.owner, name, "parent", "family"
    return None


_D = chr(36)  # the asyncpg placeholder sigil


async def _name_clash(db, user_id: str, name: str) -> bool:
    """True when name would resolve (substring LIKE) to a DIFFERENT existing person
    ("Mika" -> "Mikaela"): linking an edge would attach the child to the wrong row."""
    import person_extractor as pe

    pid = await pe._resolve_person_uuid(name, user_id, db)
    if not pid:
        return False
    for sql, args, dollar in (
        (f"SELECT name FROM people WHERE id={_D}1 AND user_id={_D}2", (pid, user_id), True),
        ("SELECT name FROM people WHERE id=? AND user_id=?", (pid, user_id), False),
    ):
        try:
            cur = await (db.execute(sql, *args) if dollar else db.execute(sql, args))
            row = await cur.fetchone()
        except Exception:  # noqa: BLE001 - other placeholder style
            continue
        found = str(row[0] or "").strip().lower() if row else ""
        return bool(found) and name.lower() not in [found, *found.split()]
    return False


async def _kept_pet(db, user_id: str, rel: NamedRelation, name: str) -> bool:
    """A child edge must not overwrite an existing current pet edge for the same pair
    (a later "Biscuit is their dog" - correction_apply - is the user's last word)."""
    import person_extractor as pe

    if rel.group != "child" or not rel.owner:
        return False
    a = await pe._resolve_person_uuid(name, user_id, db)
    b = await pe._resolve_person_uuid(rel.owner, user_id, db)
    if not (a and b):
        return False
    cur = await pe._current_edge_for_pair(db, user_id, a, b)
    return bool(cur and cur[1] == "pet")


async def _ingest_user_fact(fact: str, user_id: str, source: str, session_id, excerpt: str):
    """A list the speaker owns has no node in the people graph: keep it as a fact, through the
    same ADD/UPDATE/SKIP reconciliation every conversational writer shares."""
    from memory_quality import is_storable_fact, reconcile_for_ingest
    from memory_service import get_memory_service

    if not is_storable_fact(fact)[0]:
        return None
    svc = get_memory_service()
    op, target = await reconcile_for_ingest(svc, fact, user_id)
    if op == "skip" and target:
        return target
    if op == "update" and target:
        ref = await svc.review(target, decision="edit", edits=fact, actor=source,
                               note="named relations supersede", source_excerpt=excerpt)
        if ref is not None:
            return ref.id
    ref = await svc.ingest(
        fact, user_id=user_id, source=source, session_id=session_id, memory_type="fact",
        status="approved", tags=["person", "auto_extract", "named_relations"],
        source_excerpt=excerpt,
    )
    return ref.id if ref else None


async def _apply_one(rel: NamedRelation, *, user_id: str, source: str, session_id, db,
                     excerpt: str) -> int:
    import person_extractor as pe

    fact = rel.fact()
    if not rel.owner:
        return 1 if await _ingest_user_fact(fact, user_id, source, session_id, excerpt) else 0

    # The fact (names kept) first: it is the record every recall path can read. A refusal by
    # the authority wall (memory_authority, when present) also withholds the graph rows.
    blocked = getattr(pe, "AUTHORITY_BLOCKED", object())
    owner_id = await pe._resolve_person_uuid(rel.owner, user_id, db)
    mem_id = await pe._ingest_to_mempalace(
        fact, user_id, rel.owner, owner_id, memory_type="person", source=source,
        session_id=session_id, source_excerpt=excerpt,
    )
    if mem_id is not None and mem_id == blocked:
        return 0
    wrote = 1 if mem_id else 0
    for name in rel.names:
        edge = _edge_for(rel, name)
        if edge is None:
            continue
        try:
            if await _name_clash(db, user_id, name) or await _kept_pet(db, user_id, rel, name):
                logger.debug("named_relations: edge for a listed name withheld (clash/pet)")
                continue
            await pe._write_relationship(user_id, *edge, db)
            wrote = 1
        except Exception as exc:  # noqa: BLE001 - one bad name never costs the others
            logger.debug("named_relations: edge write failed (%s)", type(exc).__name__)
    if mem_id and not owner_id and rel.group != "pet":
        # the owner row may have just been minted as an edge stub: link the fact to it
        try:
            new_owner = await pe._resolve_person_uuid(rel.owner, user_id, db)
            if new_owner:
                from memory_service import get_memory_service

                await get_memory_service().relink_entity(user_id, mem_id, "person", new_owner)
        except Exception as exc:  # noqa: BLE001 - the idle link-resolver also repairs this
            logger.debug("named_relations: relink skipped (%s)", type(exc).__name__)
    return wrote


async def apply_named_relations(text: str, *, user_id: str, source: str = "conversation",
                                session_id=None, db=None) -> int:
    """Store every named list in text; returns how many lists landed. Never raises."""
    try:
        rels = extract_named_relations(text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("named_relations: extraction failed (%s)", type(exc).__name__)
        return 0
    if not rels or db is None:
        return 0
    excerpt = " ".join((text or "").split())
    landed = 0
    for rel in rels:
        try:
            landed += await _apply_one(rel, user_id=user_id, source=source,
                                       session_id=session_id, db=db, excerpt=excerpt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("named_relations: write failed for user=%s (%s)", user_id, type(exc).__name__)
    return landed


__all__ = ["NamedRelation", "extract_named_relations", "apply_named_relations"]
