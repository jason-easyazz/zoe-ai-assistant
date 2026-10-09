"""Role claims are supported ONLY by ID TRIPLES (ZOE_STRUCTURAL_CLAIMS, the role guard's evidence step).

``role_guess_guard`` used to answer "does the evidence put this person in this role, for this owner?" by parsing English prose
(the recall packet, the owner's turn): every review round of #1912 found one more phrasing (ownerless rows, a denial in a
packet row, a lowercase namesake, a nested possessive). A role is a RELATION between two people; it belongs in rows with ids:

    (person_id, kin_code, owner_id)        owner_id is the account owner ("user") or another people.id

The triples come from the people graph, never from prose:

* ``person_relationships`` - the CURRENT edges (``valid_to IS NULL``) between two ``people`` rows, both directions
  (``rel_a_to_b`` is what b is to a). An edge stamped ``inferred`` (a model's guess) does not count.
* ``people.relationship`` - the owner-scoped people row's role to the account owner (the speaker has no node of their own in
  the graph, so this column is their edge: ``owner_id`` = the account).

A claim "Anika Reyes is your mother" is allowed iff the triple (anika_id, mother, user) - or a more specific one that implies it
(``mother`` implies ``parent``; ``spouse`` does NOT imply ``wife``: a gender-neutral edge cannot license a gendered claim) - is
in the set. Namesakes, case, nested owners ("your brother's wife"), pronouns and negated rows are set membership:
a denial is the ABSENCE of a triple, an ownerless row cannot exist, a different person with the same first name has a
different id. The only model-shaped step left is READING the reply into a claim (the reader in ``role_guess_guard``), and it
fails closed: a claim it cannot map to a person id is the neutral phrasing.

Pure functions plus one bounded read (``fetch``). Stdlib only; never raises into a turn.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import lexicons as lex

logger = logging.getLogger(__name__)

USER = "user"            # the owner id of "your ..." - the account owner
MAX_PEOPLE = 500
FETCH_TIMEOUT_S = 0.4

#: a more specific kin code implies the general one (a wife IS a spouse); never the reverse
IMPLIES = {
    "wife": {"spouse", "partner"}, "husband": {"spouse", "partner"}, "spouse": {"partner"},
    "girlfriend": {"partner"}, "boyfriend": {"partner"}, "fiance": {"partner"},
    "mother": {"parent"}, "father": {"parent"}, "stepmother": {"parent"}, "stepfather": {"parent"},
    "mother_in_law": {"parent_in_law"}, "father_in_law": {"parent_in_law"},
    "son": {"child"}, "daughter": {"child"}, "stepson": {"child"}, "stepdaughter": {"child"},
    "sister": {"sibling"}, "brother": {"sibling"}, "sister_in_law": {"sibling_in_law"}, "brother_in_law": {"sibling_in_law"},
    "grandmother": {"grandparent"}, "grandfather": {"grandparent"},
}
#: person_relationships.rel_type -> (what b is to a, what a is to b) as kin codes ("" = no kin code to be had)
EDGE_KIN = {
    "partner": ("partner", "partner"), "spouse": ("spouse", "spouse"), "ex": ("", ""),
    "parent": ("parent", "child"), "sibling": ("sibling", "sibling"), "grandparent": ("grandparent", "grandchild"),
    "aunt_uncle": ("", ""), "cousin": ("cousin", "cousin"), "in_law": ("", ""),
    "friend": ("friend", "friend"), "best_friend": ("friend", "friend"), "met_through": ("", ""),
    "colleague": ("colleague", "colleague"), "boss": ("boss", ""), "mentor": ("", ""), "client": ("", ""), "pet": ("", ""),
}


def implies(have: str, want: str) -> bool:
    return have == want or want in IMPLIES.get(have, ())


@dataclass(frozen=True)
class Triple:
    person_id: str
    kin: str
    owner: str          # USER or a people.id


def _tokens(name: str) -> tuple:
    return tuple(t for t in re.findall(r"[\w-]+", (name or "").casefold()) if t)


@dataclass
class TripleSet:
    """The id triples of one user's people graph, and the names that let a reply's handle be resolved to an id."""

    triples: list = field(default_factory=list)
    names: dict = field(default_factory=dict)          # people.id -> display name
    complete: bool = True                               # False = the read failed: nothing is supported (fail closed)

    def ids_for(self, tokens: tuple) -> set:
        """Every people.id a handle names: the COMPLETE name, or a bare given name only when it is unique among this user's
        people (an ambiguous given name names nobody)."""
        if not tokens:
            return set()
        full = {i for i, n in self.names.items() if _tokens(n) == tokens}
        if full:
            return full
        if len(tokens) == 1:
            firsts = {i for i, n in self.names.items() if _tokens(n)[:1] == tokens}
            return firsts if len(firsts) == 1 else set()
        return set()

    def has_role_for(self, person_id: str, owner: str = USER) -> bool:
        """Does ANY triple tie ``person_id`` to ``owner`` (the "relationship not stated" marker)?"""
        return any(t.person_id == person_id and t.owner == owner and not lex.loose_code(t.kin) for t in self.triples)

    def resolve_chain(self, chain: tuple) -> set:
        """The people reached from the user along ``chain`` (user's mum's sister -> ("mother", "sister"))."""
        owners = {USER}
        for word in chain:
            code = lex.kin_code(word)
            if not code:
                return set()
            owners = {t.person_id for t in self.triples if implies(t.kin, code) and t.owner in owners}
            if not owners:
                return set()
        return owners

    def supports(self, person_id: str, kin_word: str, owner: Any = USER) -> bool:
        """Is ``person_id`` the ``kin_word`` of ``owner`` per the triples? ``owner``: ``None`` (the claim names none: any stated
        holder will do), ``"user"``, ``("name", tokens)``, ``("pron", word)`` (someone other than the user) or
        ``("rel", chain)``. An unknown person, an unmappable word or a failed read supports nothing."""
        if not self.complete or not person_id:
            return False
        code = lex.kin_code(kin_word)
        if not code:
            return False
        mine = [t for t in self.triples if t.person_id == person_id and implies(t.kin, code)]
        if not mine:
            return False
        if owner is None:
            return True
        if owner == USER or owner == "user":
            return any(t.owner == USER for t in mine)
        kind = owner[0]
        if kind == "pron":
            return any(t.owner != USER for t in mine)
        if kind == "name":
            ids = self.ids_for(tuple(owner[1]))
            return bool(ids) and any(t.owner in ids for t in mine)
        if kind == "rel":
            reached = self.resolve_chain(tuple(owner[1]))
            return bool(reached) and any(t.owner in reached for t in mine)
        return False


def triples_from_rows(people_rows: Iterable[Any], edge_rows: Iterable[Any]) -> TripleSet:
    """Build the set from raw rows. ``people_rows``: ``(id, name, relationship)``. ``edge_rows``: ``(person_a_id, person_b_id,
    rel_type, authority)`` of CURRENT edges. Pure."""
    ts = TripleSet()
    for pid, name, rel in people_rows:
        pid = str(pid)
        if name:
            ts.names[pid] = str(name)
        code = lex.kin_code(str(rel or "").strip()) if rel else None
        if code:
            ts.triples.append(Triple(pid, code, USER))
    for a, b, rel_type, authority in edge_rows:
        if str(authority or "").strip().lower() == "inferred":
            continue
        if str(a) not in ts.names or str(b) not in ts.names:
            continue          # an edge to a deleted (or unread) person is stale: it states nothing
        b_to_a, a_to_b = EDGE_KIN.get(str(rel_type or ""), ("", ""))
        if b_to_a:
            ts.triples.append(Triple(str(b), b_to_a, str(a)))      # b is a's <b_to_a>
        if a_to_b:
            ts.triples.append(Triple(str(a), a_to_b, str(b)))
    return ts


async def _read(user_id: str) -> TripleSet:
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        async with db.execute(
                "SELECT id, name, relationship FROM people WHERE user_id = ? AND deleted = 0 ORDER BY name LIMIT ?",
                (user_id, MAX_PEOPLE)) as cur:
            people = await cur.fetchall()
        try:
            async with db.execute(
                    "SELECT person_a_id, person_b_id, rel_type, authority FROM person_relationships "
                    "WHERE user_id = ? AND valid_to IS NULL", (user_id,)) as cur:
                edges = await cur.fetchall()
        except Exception:  # noqa: BLE001 - a schema without the authority column
            async with db.execute(
                    "SELECT person_a_id, person_b_id, rel_type, '' FROM person_relationships "
                    "WHERE user_id = ? AND valid_to IS NULL", (user_id,)) as cur:
                edges = await cur.fetchall()
    return triples_from_rows([(r[0], r[1], r[2]) for r in people], [(r[0], r[1], r[2], r[3]) for r in edges])


async def fetch(user_id: str, *, timeout: float = FETCH_TIMEOUT_S) -> TripleSet:
    """This user's triples, one bounded read. On ANY failure an EMPTY, ``complete=False`` set: the guard then supports
    nothing (the neutral phrasing) - never a guess. Never raises."""
    try:
        return await asyncio.wait_for(_read(user_id), timeout)
    except Exception as exc:  # noqa: BLE001 - incl. the timeout
        logger.warning("role triples unavailable (%s) - role claims fail closed this turn", type(exc).__name__)
        return TripleSet(complete=False)
