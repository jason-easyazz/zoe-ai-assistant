"""The named-person recall floor (Samantha bar S21, ZOE_PERSON_RECALL_FLOOR).

A question that NAMES a person the user has told Zoe about ("How many children does
Dana Whitfield have?") is a question about the stored record, whatever its grammar. It has
no my/I, no event verb and no own-fact noun, so none of the recall-floor shapes in
``zoe_flue_client._recall_question_shape`` claimed it: the brain answered "I don't have any
information about Dana Whitfield's children" with the corrected record in the store, and
``recall_memory`` was called in 0 of 3 recorded runs (docs/research/samantha-flags-ab-2026-10-06.md).

This module is the ASYNC half: it resolves which people THIS user knows (their ``people``
rows, and the names of their ``person_pending`` fact entities, which have no row) and asks
the pure matcher in ``memory_gate`` which of them the question names. A hit forces the
for-prompt packet AND the people-graph relational block into the prompt:

* ``zoe_flue_client._recall_context_block`` - the Flue seam (chat, and the voice turn that
  reaches the brain through ``run_flue_brain_streaming(voice_mode=True)``);
* ``routers.voice_tts._voice_relational_lines`` - the voice recall packet, whose vector half
  already always searches but whose relational half is word-gated.

Scope and safety:

* One user only. ``people`` is read with ``user_id = ?`` (NOT the family-visibility union the
  relational block uses), and person-fact entities through ``MemoryService.list_by_entity``,
  which is owner-scoped; a stranger who names the owner's contact matches nothing.
* Exact, whole-name matching (``memory_gate.person_names_in_question``). The substring
  ``person_extractor._resolve_person_uuid`` is not used.
* Bounded: at most ``MAX_KNOWN_PEOPLE`` names are read, ``memory_gate.PERSON_FLOOR_MAX_NAMED``
  are returned, and the whole resolution has ``RESOLVE_TIMEOUT_S`` (a slow store costs the
  floor, never the turn).
* ``ZOE_PERSON_RECALL_FLOOR`` = enforce (default) | shadow | off (``memory_gate.person_floor_mode``).
  ``off`` reads nothing; ``shadow`` resolves and logs what it WOULD force and forces nothing.
  Every resolved hit logs one ``RECALL_FLOOR reason=named_person`` line (counts only, no names).
* Never raises: a failure is "no floor", so the turn proceeds exactly as it did before.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import memory_gate as _gate

logger = logging.getLogger(__name__)

MAX_KNOWN_PEOPLE = 500
RESOLVE_TIMEOUT_S = 1.5
_GUEST_IDS = ("", "guest", "anonymous", "voice-guest")


@dataclass(frozen=True)
class NamedPerson:
    """A person the question names. ``person_id`` is the ``people.id`` ("" for a
    person-fact entity that has no contact row yet)."""

    name: str
    person_id: str = ""
    source: str = "people"  # "people" | "memory"


def focus_ids(people) -> list[str]:
    """The ``people.id`` values the relational block should put first."""
    return [p.person_id for p in people or () if getattr(p, "person_id", "")]


async def _known_people(user_id: str) -> list[tuple[str, str]]:
    """(people.id, name) for THIS user's live people rows, bounded."""
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        async with db.execute(
            "SELECT id, name FROM people WHERE user_id = ? AND deleted = 0 "
            "ORDER BY name LIMIT ?",
            (user_id, MAX_KNOWN_PEOPLE),
        ) as cur:
            rows = await cur.fetchall()
    return [(str(r[0]), str(r[1])) for r in rows if r[1]]


async def _pending_entity_names(user_id: str, candidates: list[str]) -> list[str]:
    """The candidates that are the name of one of this user's approved ``person_pending``
    fact entities (``entity_id`` = ``slug:<name>``): a person Zoe has facts about who is not
    a contact yet. Owner-scoped (``list_by_entity``)."""
    if not candidates:
        return []
    from memory_extractor import _slug_body
    from memory_service import get_memory_service

    by_slug = {"slug:" + _slug_body(c): c for c in candidates}
    rows = await get_memory_service().list_by_entity(user_id, list(by_slug), status="approved")
    seen = {str(r.metadata.get("entity_id") or "") for r in rows}
    return [name for slug, name in by_slug.items() if slug in seen]


async def _resolve(message: str, uid: str, exclude: Optional[Callable[[str], bool]]) -> list[NamedPerson]:
    questions = [q for q in _gate.person_question_sentences(message) if not (exclude and exclude(q))]
    if not questions:
        return []
    known: list[tuple[str, str]] = []
    try:
        known = await _known_people(uid)
    except Exception as exc:  # noqa: BLE001 - no database is "no floor", not a failed turn
        logger.debug("person floor: people read failed (%s)", type(exc).__name__)
    ids = {name: pid for pid, name in reversed(known)}  # first row wins on a duplicate name
    found: list[NamedPerson] = []
    for q in questions:
        for name in _gate.person_names_in_question(q, list(ids)):
            if all(name != f.name for f in found):
                found.append(NamedPerson(name, ids[name], "people"))
    if not found:
        candidates: list[str] = []
        for q in questions:
            candidates.extend(c for c in _gate.person_candidate_names(q) if c not in candidates)
        try:
            for name in await _pending_entity_names(uid, candidates):
                found.append(NamedPerson(name, "", "memory"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("person floor: entity read failed (%s)", type(exc).__name__)
    return found[: _gate.PERSON_FLOOR_MAX_NAMED]


async def resolve_named_people(
    message: str,
    user_id: str,
    *,
    lane: str = "seam",
    exclude: Optional[Callable[[str], bool]] = None,
) -> list[NamedPerson]:
    """The people this user's question names, or [] - the floor's one decision.

    [] when the mode is ``off`` (nothing is read), the id is a guest, no sentence is a
    question, no known person is named, or the mode is ``shadow`` (logged, not forced).
    ``exclude`` drops sentences another floor owns (the seam passes the first-person
    mood statements continuity owns). ``lane`` only labels the log line."""
    mode = _gate.person_floor_mode()
    uid = (user_id or "").strip()
    if mode == "off" or uid.lower() in _GUEST_IDS or not (message or "").strip():
        return []
    try:
        found = await asyncio.wait_for(_resolve(message, uid, exclude), RESOLVE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - incl. the timeout; never fails the turn
        logger.warning("person floor: lookup failed (%s), no floor this turn", type(exc).__name__)
        return []
    if not found:
        return []
    forced = mode == "enforce"
    logger.info("RECALL_FLOOR reason=named_person lane=%s mode=%s user=%s people=%d linked=%d forced=%d",
                lane, mode, uid, len(found), len(focus_ids(found)), int(forced))
    return found if forced else []
