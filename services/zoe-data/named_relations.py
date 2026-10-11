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
  "Mika is Dana's son": a = the child, b = the parent). The SPEAKER has no node in that graph
  (and gets none: see apply_named_relations), so a list the speaker owns ("my kids are ...",
  "I have two kids, ...") is kept as a user-stated fact with every name, AND each listed
  person gets a partial ``people`` row owned by the account (``people.user_id``) whose
  ``relationship`` column states the role to the speaker ("child", "sister", "parent") - that
  row is the link to the owner, and a later "Mika's birthday is ..." lands on it;
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

def _letter_class(pred) -> str:
    """A regex class of the Latin / Greek / Cyrillic letters satisfying pred (str.isupper ...)."""
    spans = [(0x41, 0x530), (0x1E00, 0x1F00)]
    return "[" + "".join(chr(c) for lo, hi in spans for c in range(lo, hi) if pred(chr(c))) + "]"


_UP = _letter_class(str.isupper)
_LOW = _letter_class(str.islower)
_LETTER = r"[^\W\d_]"

# One WHOLE name token: an initial capital (O'Brien / D'Angelo allow a second after an
# apostrophe), at least one lower-case letter (no SHOUTED words), any letters after that
# (McDonald, Zoë), up to two hyphenated parts (Anne-Marie). The token is anchored at both
# ends: it may not start inside a word / after an apostrophe or hyphen, and may not stop in the
# middle of one - "McDonald" is read as McDonald, never as the suffix "Donald"; a token that
# cannot be read whole (Ana2, Anne-marie) matches nothing, so no partial name is ever stored.
# The only thing allowed to follow it directly is a possessive 's.
_PART = rf"{_UP}(?=[^\W\d_]*{_LOW}){_LETTER}{{1,%d}}"
_TOKEN = (
    rf"(?<![\w'’-]){_UP}(?:['’]{_UP})?(?=[^\W\d_]*{_LOW}){_LETTER}{{1,30}}"
    rf"(?:-{_PART % 20}){{0,2}}"
    rf"(?!\w|-\w|['’](?!s(?!\w))\w)"
)
# the two-word shape joins on a SPACE only: a line break ends a name ("Mika\nBiscuit" = two kids)
_MEMBER = rf"(?-i:{_TOKEN}(?: {_TOKEN})?)"
_SEP = r"(?:\s*,\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*|\s*\n\s*)"
_LIST = rf"{_MEMBER}(?:{_SEP}{_MEMBER})*"
_OWNER = rf"(?-i:{_TOKEN}(?: {_TOKEN})?)"

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


# "do I have two kids, Mika and Sam" / "if I have two kids, ..." / "whether we have ...": the
# speaker is ASKING or supposing, not stating - nothing to keep.
_NOT_ASSERTED_LEAD_RE = re.compile(
    r"\b(?:do|did|does|if|whether|wish|suppose|imagine|what\s+if|unless)\s*,?\s*$", re.IGNORECASE)

# The role a listed person has to the SPEAKER, as the people.relationship column states it.
_ROLE_OF_NOUN = {
    "kid": "child", "kids": "child", "child": "child", "children": "child",
    "son": "son", "sons": "son", "daughter": "daughter", "daughters": "daughter",
    "sibling": "sibling", "siblings": "sibling", "brother": "brother", "brothers": "brother",
    "sister": "sister", "sisters": "sister", "parent": "parent", "parents": "parent",
}


def _asks_not_states(text: str, start: int, end: int) -> bool:
    """True when the sentence holding text[start:end] is a question or a supposition."""
    if _NOT_ASSERTED_LEAD_RE.search(text[:start]):
        return True
    tail = re.search(r"[.!?\n]", text[end:])
    return bool(tail and tail.group(0) == "?")


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
    for part in re.split(r"\s*,\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*|\s*\n\s*", raw.strip()):
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


# a lower-case surname particle directly before an owner ("van der Berg", "de la Cruz"): the
# owner token read here is only the tail of the name
_PARTICLES = frozenset("van von de der den di da del della la le du dos das bin al el ten ter".split())
_LIST_CONTINUES = re.compile(r"(?:\s*,\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*|\s*\n\s*)(\S+)", re.IGNORECASE)


def _normalise(text: str) -> str:
    """Collapse runs of spaces / tabs but KEEP line breaks: a roster written one name per line is
    a list ("Mika\nBiscuit" = two children), never one name ("Mika Biscuit")."""
    text = re.sub(r"\r\n?|[\x0b\x0c\x85\u2028\u2029]", "\n", text or "")
    text = re.sub(r"[^\S\n]+", " ", text)
    return re.sub(r" ?\n[\s]*", "\n", text).strip()


def _unreadable_tail(text: str, end: int) -> bool:
    """True when the list is followed by one more capitalised / numbered word that no name token
    could take whole ("Mika and Ana2", "Mika, BISCUIT"): the list we read is a PARTIAL one."""
    m = _LIST_CONTINUES.match(text, end)
    if not m:
        return False
    word = m.group(1).rstrip(".,;:!?)\"'’")
    if len(word) < 2 or not (word[0].isupper() or word[0].isdigit()):
        return False
    if re.fullmatch(r"I['’]\w+", word):
        return False  # "..., I think" / "I'm"
    return re.fullmatch(_TOKEN, word) is None


def _skip_partial(why: str) -> None:
    # no names in the log line: a half-read name is exactly what must not travel anywhere
    logger.info("named_relations: relation skipped, a name could not be read whole (%s)", why)


def extract_named_relations(text: str) -> list:
    """Every "<owner> <noun> <Names>" list the sentence states. Pure, no I/O."""
    text = _normalise(text)
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
                before = text[:m.start("owner")].split()
                if before and before[-1].lower() in _PARTICLES:
                    _skip_partial("owner")
                    continue
            if _unreadable_tail(text, m.end("names")):
                _skip_partial("list")
                continue
            if speaker and _asks_not_states(text, m.start(), m.end("names")):
                continue
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
    """True when name would resolve to a DIFFERENT existing person: by prefix only ("Mika" -> "Mikaela") or to
    more than one ("Tom" with two Toms) - linking an edge would attach the child to the wrong row. An exact name or a
    whole token of one ("Mika" -> "Mika Reyes") is not a clash."""
    import person_extractor as pe

    res = await pe._resolve_person(name, user_id, db)
    return res.ambiguous or (res.person_id is not None and res.tier == "prefix")


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


async def _mint_owned_people(db, user_id: str, rel: NamedRelation) -> int:
    """A speaker-owned list ("I have two kids, Mika and Sam"): a partial ``people`` row per name,
    owned by the account, ``relationship`` = the role to the speaker. NOT a graph edge.

    The people graph has no node for the speaker and this does not add one: a self row named
    after the account would (a) show up in the contact list as a contact, which contact_backfill
    and the contact UI deliberately avoid, (b) be matched by ``_resolve_person_uuid``'s substring
    LIKE for every later name that contains the account's first name (the same collision class
    ``_name_clash`` guards against here), and (c) need the account name, which is identity-walled
    and may be only an id. The owner link is therefore the row's own ``user_id`` plus the
    ``relationship`` column, which is also what the contact UI and the people recall read. A
    listed name that already resolves to someone with a different name is left alone (the fact
    keeps it); an existing row of the same name only gains the role when it has none. Never
    raises; returns the number of rows created."""
    import uuid

    import person_extractor as pe

    role = _ROLE_OF_NOUN.get(rel.noun.lower())
    if not role:
        return 0
    made = 0
    for name in rel.names:
        try:
            res = await pe._resolve_person(name, user_id, db)
            if res.ambiguous:
                continue  # two people answer to this name: a listed name is never a licence to mint a third
            existing = res.person_id
            if existing:
                if await _name_clash(db, user_id, name):
                    continue
                await _set_role_if_blank(db, user_id, existing, role)
                continue
            pid = str(uuid.uuid4())
            try:
                await db.execute(
                    "INSERT INTO people (id, user_id, name, relationship, circle, context, visibility, is_partial) "
                    f"VALUES ({_D}1,{_D}2,{_D}3,{_D}4,'circle','personal','personal',1)",
                    pid, user_id, name, role,
                )
            except Exception:  # noqa: BLE001 - the other placeholder style
                await db.execute(
                    "INSERT INTO people (id, user_id, name, relationship, circle, context, visibility, is_partial) "
                    "VALUES (?,?,?,?,'circle','personal','personal',1)",
                    (pid, user_id, name, role),
                )
            await db.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001 - one bad name never costs the others
            logger.debug("named_relations: owned person not minted (%s)", type(exc).__name__)
    return made


async def _set_role_if_blank(db, user_id: str, person_id: str, role: str) -> None:
    for sql, args in (
        (f"UPDATE people SET relationship={_D}1 WHERE id={_D}2 AND user_id={_D}3 "
         "AND (relationship IS NULL OR relationship='')", (role, person_id, user_id)),
        ("UPDATE people SET relationship=? WHERE id=? AND user_id=? "
         "AND (relationship IS NULL OR relationship='')", (role, person_id, user_id)),
    ):
        try:
            await db.execute(sql, *args) if sql.count(_D) else await db.execute(sql, args)
            await db.commit()
            return
        except Exception:  # noqa: BLE001 - other placeholder style
            continue


def _evidence(pe, user_id: str, source: str, turn_text: str, name: str):
    """The edge's evidence pointer: this turn, where the listed name sits in it, the writer's rank. ``turn_text`` is the
    ORIGINAL turn (never the whitespace-squashed excerpt: offsets and the hash are of what the user typed). Never raises."""
    try:
        import people_graph as pg

        return pg.evidence_for(user_id, turn_text, name, rank=pe._edge_authority_and_rank(source, turn_text)[1])
    except Exception:  # noqa: BLE001 - a missing pointer is a NULL column, never a lost edge
        return None


async def _apply_one(rel: NamedRelation, *, user_id: str, source: str, session_id, db,
                     excerpt: str, turn_text: Optional[str] = None) -> int:
    import person_extractor as pe

    fact = rel.fact()
    if not rel.owner:
        wrote = 1 if await _ingest_user_fact(fact, user_id, source, session_id, excerpt) else 0
        if rel.group != "pet":
            await _mint_owned_people(db, user_id, rel)
        return wrote

    # The fact (names kept) first: it is the record every recall path can read. A refusal by
    # the authority wall (memory_authority, when present) also withholds the graph rows.
    blocked = getattr(pe, "AUTHORITY_BLOCKED", object())
    owner_res = await pe._resolve_person(rel.owner, user_id, db)
    if owner_res.ambiguous:
        # two people answer to the owner's name: the list is held as a pending candidate - not an approved slug-keyed
        # fact, not an edge to a guess (the settled version lands once the user says which one)
        logger.info("PERSON_AMBIGUOUS user=%s tier=%s candidates=%d - named relations held, not guessed",
                    user_id, owner_res.tier, len(owner_res.matches))
        await pe._hold_fact_belief(user_id, rel.owner, fact, origin=source, basis="ambiguous_name",
                                   extra={"ambiguous_name": rel.owner, "fact_type": "named_relations"})
        return 0
    owner_id = owner_res.person_id
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
            await pe._write_relationship(user_id, *edge, db, evidence=_evidence(pe, user_id, source, turn_text if turn_text is not None else excerpt, name))
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
                                session_id=None, db=None, turn_text: Optional[str] = None) -> int:
    """Store every named list in text; returns how many lists landed. Never raises. ``turn_text`` is the turn as spoken
    when ``text`` is only the owner's own words (reported speech / pasted content removed): evidence pointers locate the
    names in the turn, not in the shortened excerpt. Default: ``text`` is the turn."""
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
                                       session_id=session_id, db=db, excerpt=excerpt,
                                       turn_text=turn_text if turn_text is not None else text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("named_relations: write failed for user=%s (%s)", user_id, type(exc).__name__)
    return landed


__all__ = ["NamedRelation", "extract_named_relations", "apply_named_relations"]
