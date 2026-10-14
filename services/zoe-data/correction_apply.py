"""A correction reaches the stored record, and the reply says what changed.

Before this, a correction such as "that date is wrong" was answered with an apology and
nothing stored changed, and a statement such as "X is their dog" left X recorded as a child.
The correction cue (``memory_supersede`` "correction", ``memory_extractor._CORRECTION_RES``)
only ever worked on the NEXT fact write, never on the rows already stored.

This is a deterministic tier ahead of the brain (``fast_tiers.resolve``, so web chat, the
Telegram lane and LiveKit all get it). It acts ONLY when it finds the stored record to
fix; with nothing to change it returns None and the brain answers as before — a
correction is never claimed without evidence.

* **Date correction** ("you've got the date wrong", "we're in Australia"): find the most
  recent of the user's own messages carrying an ambiguous numeric date, re-read it
  day-first (``date_locale``), and rewrite every stored fact that still holds the raw
  digits or the month-first rendering a model made of them — ``MemoryService.review(edit)``
  supersedes, nothing is deleted — then the structured ``person_important_dates`` row.
* **Name / value swap** ("that's wrong, my sister is Marisol not Marisa"): the stored note that holds the wrong value is rewritten
  with the right one, the contact carrying the wrong name is renamed, and the reply reads the new fact back
  ("Got it, your sister is Marisol."). Evidence only - see ``maybe_swap``.
* **Pet correction** ("Biscuit is their dog"): the person's relationship becomes ``pet <kind>``,
  their parent/sibling edges become the new ``pet`` edge type, stored facts that call them a
  child are rewritten, and one explicit "is a pet dog, not a child" fact is stored so a
  summary that enumerates children no longer lists them.

Flag-dark: ``ZOE_CORRECTION_APPLY`` (default OFF, read per call). Off, ``fast_tiers`` never
reaches this module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_CORRECTION_APPLY"
SOURCE = "conversation_correction"
SCAN_LIMIT = 2000
RECENT_TURNS = 6
MAX_WORDS = 40  # a correction is a short sentence; a paragraph that mentions "wrong" is not one


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_CORRECTION_APPLY", False)


@dataclass
class CorrectionResult:
    kind: str                 # "date" | "pet" | "row" | "swap"
    reply: str
    changed: list[str] = field(default_factory=list)  # the corrected texts


# ── cues ─────────────────────────────────────────────────────────────────────

_DATE_WORD = r"(?:dates?|birthdays?|dob|day|days|month|months|born|year)"
_WRONG = (r"(?:wrong|incorrect|mistaken|mistake|not right|backwards?|back to front|swapped|"
          r"mixed up|the wrong way(?: (?:round|around))?|the other way (?:round|around)|"
          r"american|us style|month first)")
_DATE_CUE_RES = (
    re.compile(rf"\b{_DATE_WORD}\b[^.!?]{{0,60}}\b{_WRONG}\b", re.IGNORECASE),
    re.compile(rf"\b{_WRONG}\b[^.!?]{{0,60}}\b{_DATE_WORD}\b", re.IGNORECASE),
    re.compile(r"\b(?:day[- ]first|dd/mm|d/m/y|australian (?:date|format))\b", re.IGNORECASE),
    re.compile(r"\b(?:we(?:'re| are)|i(?:'m| am)) in australia\b[^.!?]{0,60}\b(?:date|day|month)\b",
               re.IGNORECASE),
)


_US_ORDER_RE = re.compile(r"\b(?:american|us[- ]style|us format|u\.s\.|month[- ]first|mm/dd)\b", re.IGNORECASE)


def correction_order(text: str) -> str:
    """``mdy`` when the user's correction itself asks for US/month-first reading ("I use US
    style dates"), else ``dmy`` (the household default). Those cues state a PREFERENCE, so they
    set the order for this correction instead of undoing it."""
    return "mdy" if _US_ORDER_RE.search(text or "") else "dmy"


def is_date_correction(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t.split()) > MAX_WORDS:
        return False
    return any(rx.search(t) for rx in _DATE_CUE_RES)


_PET = (r"(?:dog|puppy|pup|cat|kitten|bird|parrot|budgie|rabbit|bunny|hamster|guinea pig|"
        r"fish|horse|pony|pet)")
_PET_RE = re.compile(
    r"^\s*(?:(?:no|nope|actually|sorry|wait|oh|hang on)[,.\s]+)*"
    r"(?P<n>[A-Za-z][A-Za-z'\-]{1,30}(?:\s[A-Z][a-z]{1,20})?)\s+(?:is|was)\s+(?:actually\s+|just\s+)?"
    r"(?:their|his|her|my|our|the|a|an|[A-Z][a-z]+'s)\s+(?:\w+\s+)?(?P<k>" + _PET + r")\b[.!?\s]*$",
    re.IGNORECASE,
)


def pet_statement(text: str) -> Optional[tuple[str, str]]:
    """``(name, pet kind)`` for "Biscuit is their dog" / "No, Biscuit is the dog", else None."""
    t = (text or "").strip()
    if not t or len(t.split()) > 14:
        return None
    m = _PET_RE.match(t)
    if not m:
        return None
    name = m.group("n").strip()
    try:
        from person_extractor import _looks_like_person_name

        if not _looks_like_person_name(name):
            return None
    except Exception:  # noqa: BLE001
        return None
    return name[:1].upper() + name[1:], m.group("k").lower()


def is_correction_turn(text: str) -> bool:
    """Cheap pre-check so a turn that is neither shape never touches the store."""
    return is_date_correction(text) or pet_statement(text) is not None


# ── driver helpers (asyncpg ``$n`` or sqlite ``?`` — the codebase's try-both idiom) ──────

async def _exec(db, dollar_sql: str, qmark_sql: str, args: tuple) -> bool:
    for sql, dollar in ((dollar_sql, True), (qmark_sql, False)):
        try:
            if dollar:
                await db.execute(sql, *args)
            else:
                await db.execute(sql, args)
            return True
        except Exception:  # noqa: BLE001 — try the other placeholder style
            continue
    return False


async def _fetch(db, dollar_sql: str, qmark_sql: str, args: tuple,
                 qmark_args: Optional[tuple] = None) -> list:
    for sql, dollar in ((dollar_sql, True), (qmark_sql, False)):
        try:
            qargs = args if qmark_args is None else qmark_args
            cur = await (db.execute(sql, *args) if dollar else db.execute(sql, qargs))
            return list(await cur.fetchall())
        except Exception:  # noqa: BLE001
            continue
    return []


async def _commit(db) -> None:
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        pass


# ── date correction ──────────────────────────────────────────────────────────

def _caps(text: str) -> set[str]:
    return set(re.findall(r"\b[A-Z][a-z]{2,}\b", text or ""))


async def _recent_user_messages(user_id: str, session_id: str, current: str) -> list[str]:
    msgs: list[str] = []
    try:
        from memory_extractor import _load_recent_user_messages, recall_prev_user_turn

        msgs = await _load_recent_user_messages(user_id, session_id, current, limit=RECENT_TURNS)
        if not msgs:
            prev = recall_prev_user_turn(user_id, session_id)
            if prev and prev.strip() != (current or "").strip():
                msgs = [prev]
    except Exception as exc:  # noqa: BLE001
        logger.debug("correction_apply: history lookup failed (%s)", type(exc).__name__)
    return msgs


async def _fix_structured_date(db, user_id: str, old_mem_id: str, new_mem_id: str, d) -> None:
    """Point the person's important-date row at the corrected day/month/year."""
    if db is None:
        return
    await _exec(
        db,
        "UPDATE person_important_dates SET month=$1, day=$2, year=$3, mem_id=$4 "
        "WHERE user_id=$5 AND mem_id=$6",
        "UPDATE person_important_dates SET month=?, day=?, year=?, mem_id=? "
        "WHERE user_id=? AND mem_id=?",
        (d.month, d.day, d.year, new_mem_id, user_id, old_mem_id),
    )
    await _commit(db)


async def apply_date_correction(
    text: str, user_id: str, recent_messages: list[str], *, svc=None, db=None,
    session_id: Optional[str] = None,
) -> Optional[CorrectionResult]:
    from date_locale import find_numeric_dates, misread_pattern, render_date

    if not is_date_correction(text):
        return None
    order = correction_order(text)
    dayfirst = None if order == "dmy" else False
    # The most recent user message that carries an AMBIGUOUS numeric date: the one being
    # corrected. Older messages are left alone (blast radius = one message).
    source_msg, found = "", []
    for msg in recent_messages:
        f = [x for x in find_numeric_dates(msg, dayfirst=dayfirst) if x.date.ambiguous]
        if f:
            source_msg, found = msg, f
            break
    if not found:
        return None
    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()
    rows = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
    names = _caps(source_msg)
    changed: list[str] = []
    done: set[str] = set()
    for f in found:
        right = render_date(f.date.day, f.date.month, f.date.year)
        wrong_rx = misread_pattern(f.date)
        for row in rows:
            if row.id in done:
                continue
            old = row.text or ""
            new = None
            if f.token in old:  # the raw digits, stored as typed
                new = old.replace(f.token, right)
            elif (wrong_rx is not None and f.date.year and (_caps(old) & names)):
                # A month-first rendering a model made of the digits ("July 8th, 1991"): only
                # when it carries the SAME year (a date stated in words with another year, or
                # none, is a different fact and is never touched), on a row about the same
                # person/thing as the corrected message. The year is never added to a row
                # that did not have it.
                m = wrong_rx.search(old)
                if m and str(f.date.year) in m.group(0):
                    new = old[: m.start()] + right + old[m.end():]
            if not new or new == old:
                continue
            try:
                ref = await svc.review(
                    row.id, decision="edit", edits=new, actor=SOURCE,
                    note=f"date correction ({order} order)",
                    source_excerpt=" ".join((text or "").split()),
                    session_id=session_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("correction_apply: date edit failed (%s)", type(exc).__name__)
                continue
            if ref is None:
                continue
            done.add(row.id)
            changed.append(new)
            await _fix_structured_date(db, user_id, row.id, getattr(ref, "id", row.id), f.date)
    if not changed:
        return None
    # Say what changed, in the stored words: prefer the sentence form ("Jordan's birthday is
    # 7 August 1991") over a compact "Jordan: 7 August 1991".
    uniq = list(dict.fromkeys(c.strip().rstrip(".") for c in changed))
    uniq.sort(key=lambda c: ("birthday" not in c.lower(), len(c)))
    shown = "; ".join(uniq[:2])
    how = ("I'd read it day-first — dates are month first for you, so that's what I've stored."
           if order == "mdy" else
           "I'd read it month-first — dates are day first here, so that's what I've stored.")
    reply = f"Fixed: {shown}. {how}"
    logger.info("CORRECTION_APPLIED kind=date user=%s rows=%d", user_id, len(changed))
    return CorrectionResult("date", reply, uniq)


# ── pet correction ───────────────────────────────────────────────────────────

_CHILD_WORD = re.compile(r"\b(?:child|children|kids?|sons?|daughters?|babys?|babies)\b", re.IGNORECASE)
_PET_NOUN = (r"(?:dog|puppy|pup|cat|kitten|bird|parrot|budgie|rabbit|bunny|hamster|guinea pig|"
             r"fish|horse|pony|pet)")


def _claims_child(text: str, name: str) -> bool:
    """Does ``text`` say NAME is one of someone's children? Same clause only: a row that
    mentions kids and, separately, "a dog named NAME" is not such a claim, and neither is a
    row where the child words never reach NAME."""
    n = re.escape(name)
    if re.search(rf"\b{_PET_NOUN}\s+(?:named\s+|called\s+)?{n}\b", text, re.IGNORECASE):
        return False  # already identified as a pet in this very text
    for sent in re.split(r"[.;\n]", text):
        if not re.search(rf"\b{n}\b", sent, re.IGNORECASE):
            continue
        if re.search(rf"\b(?:children|kids|child|kid|sons?|daughters?)\b[^.;\n]*?"
                     rf"(?:\bare\b|\bis\b|:|,|\binclude[s]?\b|\bnamed\b|\bcalled\b)[^.;\n]*\b{n}\b",
                     sent, re.IGNORECASE):
            return True
        if re.search(rf"\b{n}(?:\s+[A-Z][a-z]+)?\b[^.;\n]*?\b(?:is|was)\b[^.;\n]*?"
                     r"\b(?:child|kid|son|daughter)\b", sent, re.IGNORECASE):
            return True
    return False


_NUM_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10}
_WORD_OF = {1: "one", **{v: k for k, v in _NUM_WORDS.items()}}


def _decrement_count(sent: str) -> str:
    """"two kids" -> "one kid" once the pet is taken out of the list."""
    def sub(m: "re.Match[str]") -> str:
        raw = m.group(1).lower()
        n = _NUM_WORDS.get(raw) or int(raw)
        left = n - 1
        word = m.group(2).lower()
        if left == 1:
            word = {"kids": "kid", "children": "child"}.get(word, word)
        label = _WORD_OF.get(left, str(left)) if raw.isalpha() else str(left)
        return f"{label} {word}"

    return re.sub(r"\b(two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(kids|children)\b", sub,
                  sent, count=1, flags=re.IGNORECASE)


def _strip_name(text: str, name: str) -> str:
    """Remove NAME (+ an optional surname and its comma/and) from an enumeration."""
    nm = rf"{re.escape(name)}(?:\s+[A-Z][a-z]+)?"
    out = re.sub(rf",?\s*(?:and\s+)?{nm}(?=\s*(?:,|and\b|\.|$))", "", text, count=1)
    out = re.sub(r"(\b\w+)\s+,\s*", r"\1 ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _rewrite_child_row(text: str, name: str, pet_text: str) -> str:
    """Edit ONLY the clause about NAME; every other sentence of the row is kept.
    "NAME is a child of X" becomes the pet fact; an enumeration loses NAME and its count."""
    n = re.escape(name)
    sents = re.split(r"(?<=[.;])\s*|\n", text)
    out = []
    for sent in sents:
        if sent and _claims_child(sent, name):
            if re.search(rf"\b{n}(?:\s+[A-Z][a-z]+)?\b[^.;\n]*?\b(?:is|was)\b[^.;\n]*?"
                         r"\b(?:child|kid|son|daughter)\b", sent, re.IGNORECASE):
                out.append(pet_text)  # "NAME is a child of X": the whole claim is the error
                continue
            stripped = _decrement_count(_strip_name(sent, name))
            out.append(stripped if stripped != sent else f"{sent.rstrip('.')} (not {name}, a pet).")
        elif sent:
            out.append(sent)
    return " ".join(out).strip()


async def _people_rows(db, user_id: str, name: str) -> list[dict]:
    rows = await _fetch(
        db,
        "SELECT id, name, relationship, email, phone, birthday, is_partial FROM people "
        "WHERE user_id=$1 AND deleted=0 AND (lower(name)=lower($2) OR lower(name) LIKE lower($3))",
        "SELECT id, name, relationship, email, phone, birthday, is_partial FROM people "
        "WHERE user_id=? AND deleted=0 AND (lower(name)=lower(?) OR lower(name) LIKE lower(?))",
        (user_id, name, f"{name} %"),
    )
    return [{"id": r[0], "name": r[1], "rel": r[2] or "", "email": r[3], "phone": r[4],
             "birthday": r[5], "partial": bool(r[6])} for r in rows]


def _pet_candidate(p: dict) -> bool:
    """A row that can safely become a pet: no human data (email / phone / birthday) and it is
    child-like ("friend's child"), a bare partial stub, or already a pet."""
    if p["email"] or p["phone"] or p["birthday"]:
        return False
    rel = p["rel"].strip().lower()
    return p["partial"] or rel.startswith("pet") or bool(_CHILD_WORD.search(rel))


async def _current_edges(db, user_id: str, person_id: str) -> list[tuple[str, str, str]]:
    rows = await _fetch(
        db,
        "SELECT id, person_a_id, person_b_id FROM person_relationships WHERE user_id=$1 "
        "AND valid_to IS NULL AND rel_type <> 'pet' AND (person_a_id=$2 OR person_b_id=$2)",
        "SELECT id, person_a_id, person_b_id FROM person_relationships WHERE user_id=? "
        "AND valid_to IS NULL AND rel_type <> 'pet' AND (person_a_id=? OR person_b_id=?)",
        (user_id, person_id),
        (user_id, person_id, person_id),
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def _retype_edge_as_pet(db, user_id: str, edge_id: str, a: str, b: str, person_id: str, owner: str, *,
                              evidence=None) -> bool:
    """One wrong parent / sibling / ... edge becomes the ``pet`` edge (pet = person_a, owner = person_b) - by CLOSING it
    (``close_reason='corrected_pet'``) and opening the pet edge, in ONE transaction under the pair's advisory lock
    (``people_graph``: invalidate, never rewrite in place). When the (pet, owner) pair already has a current edge that is
    not this one, the wrong edge is just closed (``corrected_pet_duplicate``) - it never collides on the unique index.
    False when nothing changed (the edge was already gone) or the change could not be made whole (rolled back)."""
    import uuid

    import people_graph as pg

    cols = await pg.edge_columns(db)
    now = pg.now_iso()
    new_id = str(uuid.uuid4())
    try:
        async with pg.edge_transaction(db, user_id, a, b):
            # the replacement keeps what was written on the edge it replaces (notes were kept when this changed the type
            # in place; closing + reopening must not make them vanish from the current relationship)
            spec = pg.EdgeSpec("pet", "Pet owner", "Pet", "pet", notes=await pg.edge_notes(db, user_id, edge_id),
                               authority="user_stated", origin=SOURCE, evidence=evidence)
            clash = await pg.current_edge(db, user_id, person_id, owner)
            if clash is not None and clash[0] != edge_id:
                return await pg.close_edge(db, user_id, edge_id, reason="corrected_pet_duplicate", now=now,
                                           superseded_by=str(clash[0]), cols=cols)
            if not await pg.close_edge(db, user_id, edge_id, reason="corrected_pet", now=now, superseded_by=new_id,
                                       cols=cols):
                return False
            if not await pg.insert_edge(db, user_id=user_id, edge_id=new_id, person_a_id=person_id, person_b_id=owner,
                                        spec=spec, now=now, cols=cols, ignore_conflict=False):
                raise pg.EdgeWriteError("the pet edge did not land")
            return True
    except Exception as exc:  # noqa: BLE001 - rolled back: the edge is exactly as it was
        logger.warning("correction_apply: pet edge change not applied (%s)", type(exc).__name__)
        return False


async def _make_pet(db, user_id: str, person_id: str, current_rel: str, kind: str, evidence=None) -> int:
    """Relationship -> ``pet <kind>``; parent/sibling/... edges -> the ``pet`` edge type
    (pet = person_a, owner = person_b; labels as ``RELATIONSHIP_TYPES['pet']``). The old edges are closed, not
    rewritten (see ``_retype_edge_as_pet``).
    Returns how many things changed (0 when the record already says pet)."""
    changed = 0
    rel = "pet" if kind == "pet" else f"pet {kind}"
    now = datetime.utcnow().isoformat() + "Z"
    if current_rel.strip().lower() != rel and await _exec(
        db,
        "UPDATE people SET relationship=$1, updated_at=$2 WHERE id=$3 AND user_id=$4",
        "UPDATE people SET relationship=?, updated_at=? WHERE id=? AND user_id=?",
        (rel, now, person_id, user_id),
    ):
        changed += 1
    for edge_id, a, b in await _current_edges(db, user_id, person_id):
        owner = b if a == person_id else a
        if await _retype_edge_as_pet(db, user_id, edge_id, a, b, person_id, owner, evidence=evidence):
            changed += 1
    await _commit(db)
    return changed


def _pet_evidence(user_id: str, text: str):
    """The pet edge's evidence pointer: this turn, the statement inside it, the correcting writer's rank."""
    import people_graph as pg

    try:
        import memory_authority as auth

        rank = int(auth.resolve_write(SOURCE, text, anchor_text=text).rank)
    except Exception:  # noqa: BLE001
        rank = pg.rank_for_authority("user_stated")
    return pg.evidence_for(user_id, text, " ".join((text or "").split()), rank=rank)


async def apply_pet_correction(
    text: str, user_id: str, *, svc=None, db=None, session_id: Optional[str] = None,
) -> Optional[CorrectionResult]:
    stmt = pet_statement(text)
    if not stmt:
        return None
    name, kind = stmt
    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()
    people = await _people_rows(db, user_id, name) if db is not None else []
    if len(people) > 1:
        # Two people answer to this name: guessing would turn the wrong one into a pet.
        return CorrectionResult(
            "pet_ask",
            f"I know more than one {name} — which one is the {kind}? Tell me their full name "
            "and I'll fix it.", [])
    if people and not _pet_candidate(people[0]):
        return None  # a person with human data (or an adult relationship) is never made a pet
    full = people[0]["name"] if people else name
    name_rx = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    rows = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
    child_rows = [r for r in rows if _claims_child(r.text or "", name)
                  and "not a child" not in (r.text or "").lower()]
    if not people and not child_rows:
        return None  # nothing stored about this name: no claim, the brain answers
    pet_text = (f"{full} is a pet {kind}, not a child." if kind != "pet"
                else f"{full} is a pet, not a child.")
    changed = 0
    excerpt = " ".join((text or "").split())
    evidence = _pet_evidence(user_id, text)
    for p in people:
        changed += await _make_pet(db, user_id, p["id"], p["rel"], kind, evidence)
    for r in child_rows:
        new = _rewrite_child_row(r.text, name, pet_text)
        if not new or new == r.text:
            continue
        try:
            ref = await svc.review(r.id, decision="edit", edits=new, actor=SOURCE,
                                   note="pet is not a child (correction)", source_excerpt=excerpt,
                                   session_id=session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("correction_apply: pet edit failed (%s)", type(exc).__name__)
            continue
        if ref is not None:
            changed += 1
    # Only now (after the edits) is it known whether a pet fact already exists: an edit above
    # may have just written it, and ingesting a second copy would duplicate it.
    after = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
    has_pet_fact = any(name_rx.search(r.text or "") and "not a child" in (r.text or "").lower()
                       for r in after)
    if not has_pet_fact:
        entity_id = people[0]["id"] if people else f"slug:{name.lower().replace(' ', '_')}"
        try:
            ref = await svc.ingest(
                pet_text, user_id=user_id, source=SOURCE, memory_type="person",
                status="approved", tags=["person", "pet", name.lower()],
                entity_type="person" if people else "person_pending", entity_id=entity_id,
                metadata={"pattern_type": "pet"}, source_excerpt=excerpt,
            )
            changed += 1 if ref is not None else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("correction_apply: pet fact ingest failed (%s)", type(exc).__name__)
    if not changed:
        return None
    word = "pet" if kind == "pet" else f"pet {kind}"
    reply = (f"Fixed: {full} is a {word}, not one of the children. "
             "I've updated their record and the family list.")
    logger.info("CORRECTION_APPLIED kind=pet user=%s changed=%d", user_id, changed)
    return CorrectionResult("pet", reply, [pet_text])


# ── one named row (BM5: "why did you say that?" -> "that's wrong, it's X") ────

async def apply_row_correction(
    user_id: str, row_id: str, new_text: str, *, utterance: str = "", session_id: Optional[str] = None, svc=None,
) -> Optional[CorrectionResult]:
    """Edit ONE stored row - the one ``provenance_answers`` just named to the owner - to ``new_text`` (the owner's own correction,
    already decided by ``provenance_answers.fix_text``). The same path as the date and pet corrections: ``MemoryService.review(edit)``
    supersedes (nothing is deleted), under the user-class writer ``conversation_correction`` with the correcting utterance as
    evidence. None (the caller says so) when the correction path is off, the row is gone / not approved / not this user's, the text
    does not change, or the edit is refused. Never raises."""
    if not enabled() or _is_unidentified(user_id) or not (new_text or "").strip():
        return None
    try:
        try:
            import user_prefs

            if await user_prefs.is_memory_opted_out(user_id):
                return None
        except Exception:  # noqa: BLE001
            pass
        if svc is None:
            from memory_service import get_memory_service

            svc = get_memory_service()
        cur = await svc.get(row_id)
        if cur is None:
            return None
        meta = cur.metadata or {}
        owner = str(meta.get("user_id") or meta.get("wing") or "").strip().lower()
        if owner != user_id.strip().lower() or str(meta.get("status") or "approved").lower() != "approved":
            return None
        new = " ".join(new_text.split())
        if new == " ".join((cur.text or "").split()):
            return None
        ref = await svc.review(
            row_id, decision="edit", edits=new, actor=SOURCE, note="row correction (owner named the row)",
            source_excerpt=" ".join((utterance or "").split()), session_id=session_id,
        )
        if ref is None:
            return None
        logger.info("CORRECTION_APPLIED kind=row user=%s", user_id)
        return CorrectionResult("row", f"Fixed - I now have: \"{new}\". The old note is gone.", [new])
    except Exception as exc:  # noqa: BLE001 — a correction never breaks the turn
        logger.warning("correction_apply row correction failed user=%s: %s", user_id, type(exc).__name__)
        return None


# ── name / value swap ("my sister is Marisol, not Marisa") ──────────────────

_REL = (r"(?:sister|brother|mum|mom|mother|dad|father|wife|husband|partner|son|daughter|boyfriend|girlfriend|fianc[ée]e?|"
        r"nan|nana|grandma|grandmother|grandpa|grandfather|aunt|auntie|uncle|cousin|niece|nephew|friend|boss|"
        r"doctor|dentist|teacher|neighbou?r|dog|cat)")
_VAL = r"[A-Za-z][A-Za-z'’\-]{1,24}(?:\s+[A-Za-z][A-Za-z'’\-]{1,24})?"
_SWAP_LEAD = (r"^\W*(?P<lead>(?:(?:that(?:['’]s| is| was)\s+(?:wrong|incorrect|not\s+(?:right|correct))|you(?:['’]ve| have)\s+(?:got|got that)\s+"
              r"(?:it|that)\s+wrong|no|nope|actually|sorry|wait|oh|hang\s+on|zoe|hey|um+)[,.;:!\-\s]+)*)")
_SWAP_RES = (
    # "my sister is (called) Marisol, not Marisa"
    re.compile(_SWAP_LEAD + rf"(?:my|our|his|her|their)\s+(?P<r>{_REL})(?:['’]s\s+name)?\s+(?:is|was)\s+(?:called\s+|named\s+|actually\s+)?"
               rf"(?P<n>{_VAL})[,;]?\s+(?:and\s+)?(?:and\s+)?not\s+(?P<o>{_VAL})\W*$", re.IGNORECASE),
    # "my sister is not Marisa, she's Marisol" / "... it's Marisol"
    re.compile(_SWAP_LEAD + rf"(?:my|our|his|her|their)\s+(?P<r>{_REL})(?:['’]s\s+name)?\s+(?:is|was)\s+not\s+(?P<o>{_VAL})[,;]?\s+"
               rf"(?:(?:she|he|they|it)(?:['’]s|\s+is)|it(?:['’]s|\s+is)|but)\s+(?P<n>{_VAL})\W*$", re.IGNORECASE),
    # "it's Marisol, not Marisa" / "No, Marisol, not Marisa"
    # ("it's actually Marisol, not Marisa": the filler adverb is not part of the value - it used to be read as "actually Marisol")
    re.compile(_SWAP_LEAD + rf"(?:it(?:['’]s|\s+is|\s+was)\s+)?(?:(?:actually|really)\s+)?(?P<n>{_VAL})[,;]?\s+not\s+(?P<o>{_VAL})\W*$",
               re.IGNORECASE),
    # "not Marisa, Marisol"
    re.compile(_SWAP_LEAD + rf"not\s+(?P<o>{_VAL})[,;]?\s+(?:it(?:['’]s|\s+is)\s+)?(?P<n>{_VAL})\W*$", re.IGNORECASE),
)
_SWAP_STOP = frozenset({"wrong", "right", "correct", "incorrect", "that", "this", "it", "so", "just", "yet", "very", "really",
                        "the", "a", "an", "my", "your", "our", "his", "her", "their"})
SWAP_MAX_WORDS = 18
SWAP_MAX_ROWS = 3        # the twin rows one utterance leaves behind; more than that is not one fact


def swap_statement(text: str) -> Optional[tuple[str, str, str]]:
    """``(relation, new, old)`` for "that's wrong, my sister is Marisol not Marisa" (relation may be ""), else None. Pure.

    Both values must look like names (letters, one or two words) and differ; "it's not the dog, it's the cat" is not a swap."""
    t = " ".join((text or "").split())
    if not t or len(t.split()) > SWAP_MAX_WORDS:
        return None
    for i, rx in enumerate(_SWAP_RES):
        m = rx.match(t)
        if not m:
            continue
        # the two bare shapes ("Marisol, not Marisa") are a correction only when the person says so ("no," / "that's wrong,") or
        # opens with "it's": "tea not coffee" alone is an answer, not a correction
        if i >= 2 and not (m.group("lead") or "").strip() and not re.match(r"^\W*it(?:['’]s|\s+is|\s+was)\b", t, re.IGNORECASE):
            continue
        new, old = m.group("n").strip(" ,.;"), m.group("o").strip(" ,.;")
        rel = (m.groupdict().get("r") or "").strip().lower()
        if not new or not old or new.lower() == old.lower():
            continue
        if any(w.lower() in _SWAP_STOP for w in (new + " " + old).split()):
            continue
        return rel, new, old
    return None


def _title(value: str, like: str) -> str:
    """``value`` capitalised the way the old value was written (STT often lowercases a name)."""
    return " ".join(w[:1].upper() + w[1:] for w in value.split()) if like[:1].isupper() or value.islower() else value


def _word_re(value: str):
    return re.compile(r"(?<![A-Za-z0-9'’])" + re.escape(value) + r"(?![A-Za-z0-9'’])", re.IGNORECASE)


def _is_note_about(rel: str, old: str, note: str) -> bool:
    """True when ``note`` states ``old`` AS the owner's ``rel`` - the relation is the SUBJECT of the note ("my sister is Marisa",
    "User's sister Marisa", "sister: Marisa"), not a modifier or another person's relative. "My sister's friend is Marisa" (a possessive
    chain), "Dana's sister is Marisa" (a third person's sister) and "Marisa is a coworker" (no relation) are all someone else.
    Prefers the stored claim row (subject / predicate / object, ``structural_claims``) when the note carries one; else reads the
    note's own opening, anchored at its start. Pure."""
    if not rel or not old:
        return False
    try:
        import structural_claims as sc

        claim = sc.claim_from_metadata(getattr(note, "metadata", None)) if not isinstance(note, str) else None
    except Exception:  # noqa: BLE001
        claim = None
    body = note if isinstance(note, str) else (getattr(note, "text", "") or "")
    if claim is not None and getattr(claim, "obj", ""):
        pred = str(getattr(claim, "pred", "") or "").lower()
        if rel in pred.replace("_", " ") and _word_re(old).search(str(claim.obj)):
            return True
    rel_p = re.escape(rel)
    pattern = (rf"^\W*(?:(?:remember\s+that|note\s+that)\s+)?(?:(?:my|our|the\s+user['’]s|user['’]s)\s+)?{rel_p}(?!['’]s\b)"
               rf"\s*(?::|-|,)?\s*(?:(?:is|was|are)\s+)?(?:(?:called|named)\s+)?")
    m = re.match(pattern, body, re.IGNORECASE)
    return bool(m) and bool(re.match(rf"{re.escape(old)}(?![A-Za-z0-9'’])", body[m.end():], re.IGNORECASE))


def _read_back(rel: str, new: str, old: str, text: str) -> str:
    """One spoken sentence that says what is now held: "Got it, your sister is Marisol." (a plain restatement of the new fact)."""
    if rel:
        return f"Got it, your {rel} is {new}."
    flipped = re.sub(r"\b(?:my|our)\b", "your", text.strip().rstrip(".!"), flags=re.IGNORECASE)
    flipped = re.sub(r"\bi am\b", "you are", flipped, flags=re.IGNORECASE)
    flipped = re.sub(r"\bi['’]m\b", "you're", flipped, flags=re.IGNORECASE)
    return f"Got it, {flipped[:1].lower() + flipped[1:]}." if flipped else f"Got it, {new}, not {old}."


async def _rename_contact(user_id: str, rel: str, old: str, new: str) -> str:
    """Rename the ONE contact called ``old`` (and, when the owner named a relation, related that way) to ``new``.

    Returns ``"renamed"``, ``"none"`` (no such contact, or more than one - a person is never renamed on a guess) or ``"failed"``
    (the contact update or its memory mirror did not hold: the note and the contact now disagree). A failure is logged at WARNING
    with the account and the contact's id, never a name."""
    pid = ""
    try:
        from datetime import datetime

        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            cur = await db.execute(
                "SELECT id, relationship FROM people WHERE user_id = ? AND deleted = 0 AND lower(name) = lower(?)", (user_id, old))
            rows = [r for r in await cur.fetchall()]
            if rel:
                rows = [r for r in rows if rel in str(r["relationship"] or "").lower()]
            if len(rows) != 1:
                return "none"
            pid = str(rows[0]["id"])
            await db.execute("UPDATE people SET name = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                             (new, datetime.utcnow().isoformat() + "Z", pid, user_id))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("correction_apply: contact rename FAILED user=%s person=%s (%s): the note changed, the contact did not",
                       user_id, pid or "-", type(exc).__name__)
        return "failed"
    try:
        import contacts_conversation as _cc

        ok = await _cc.refresh_person_mirror(user_id, pid, new, str(rows[0]["relationship"] or "") or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("correction_apply: contact mirror refresh FAILED user=%s person=%s (%s)", user_id, pid, type(exc).__name__)
        return "failed"
    if ok is False:
        logger.warning("correction_apply: contact mirror refresh FAILED user=%s person=%s", user_id, pid)
        return "failed"
    return "renamed"


async def maybe_swap(text: str, user_id: str, session_id: str, *, svc=None) -> Optional[CorrectionResult]:
    """"that's wrong, my sister is Marisol not Marisa": rewrite the stored note(s) holding the wrong value, then read the new fact
    back. Evidence only: it acts when the OWNER'S approved notes hold the old value as a whole word (with the relation word too,
    when one was said) in at most ``SWAP_MAX_ROWS`` rows - with the relation unsaid, in exactly one - and returns None otherwise, so
    the brain answers as before and a correction is never claimed without a stored fact to change. Never raises."""
    if not enabled() or _is_unidentified(user_id):
        return None
    sw = swap_statement(text)
    if sw is None:
        return None
    rel, new, old = sw
    try:
        try:
            import user_prefs

            if await user_prefs.is_memory_opted_out(user_id):
                return None
        except Exception:  # noqa: BLE001
            pass
        if svc is None:
            from memory_service import get_memory_service

            svc = get_memory_service()
        uid = user_id.strip().lower()
        old_re = _word_re(old)
        hits = []
        for r in await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT):
            meta = r.metadata or {}
            if str(meta.get("user_id") or meta.get("wing") or "").strip().lower() != uid:
                continue
            body = r.text or ""
            if not old_re.search(body):
                continue
            # with a relation said, only a note that states the old value AS that relation (the relation is the note's subject) is the
            # fact being corrected: "my sister's friend is Marisa" and "Dana's sister is Marisa" are other people
            if rel and not _is_note_about(rel, old, r):
                continue
            hits.append(r)
        if not hits or len(hits) > (SWAP_MAX_ROWS if rel else 1):
            return None
        if len(hits) > 1:
            # several rows: only the twin rows ONE utterance left behind (same saved turn / excerpt) are one fact; anything else is a guess
            from provenance_answers import twins_of

            twin_ids = {t.id for t in await twins_of(user_id, hits[0], svc=svc)} | {hits[0].id}
            if any(h.id not in twin_ids for h in hits):
                return None
        new_val = _title(new, old)
        from provenance_answers import _speculation_barrier

        await _speculation_barrier()
        changed: list[str] = []
        for r in hits:
            res = await apply_row_correction(user_id, r.id, old_re.sub(new_val, r.text, count=1), utterance=text,
                                             session_id=session_id, svc=svc)
            if res is not None:
                changed.extend(res.changed)
        if not changed:
            return None
        renamed = await _rename_contact(user_id, rel, old, new_val)
        logger.info("CORRECTION_APPLIED kind=swap user=%s rows=%d contact=%s", user_id, len(changed), renamed)
        reply = _read_back(rel, new_val, old, text)
        if renamed == "failed":      # never claim a fix that did not fully hold
            reply += " I fixed my note, but I couldn't update your contact."
        return CorrectionResult("swap", reply, changed)
    except Exception as exc:  # noqa: BLE001 — a correction never breaks the turn
        logger.warning("correction_apply swap failed user=%s: %s", user_id, type(exc).__name__)
        return None


# ── same-session correction ("it's actually a bike shop, not a pike shop") ───────────────────────
#
# A misheard word is corrected by the SAME person a turn or two later. The name swap above only fixes a note that is already
# stored and name-shaped; this lane covers the other two halves of that class, bounded by the strongest evidence there is - the
# same identity, the same session, a few minutes:
#
# * the misheard fact is ALREADY stored (``maybe_fix_recent``): the owner's approved rows written in THIS session within
#   ``RECENT_WINDOW_S`` that hold the old phrase are rewritten through ``apply_row_correction`` (review edit = supersede, the
#   correcting utterance as evidence, nothing deleted);
# * the misheard fact is NOT stored yet (``correct_fact``): the post-turn digest of the misheard turn waits for the brain's one
#   slot and can land minutes AFTER the correction (seen live: the correction at :03, the wrong row at :25). The correction is
#   kept per (identity, session) with the owner's earlier turns of that session; the digest of one of THOSE turns rewrites the
#   old phrase before the write, the correcting words joining the evidence.
#
# A different identity never matches: the registry, the history read and the row filter are all keyed on the turn's own
# member id, and a guest / unidentified turn neither registers nor reads (it writes no durable memory at all).

RECENT_WINDOW_S = 30 * 60
_MIN_CONTAIN_WORDS = 4
# a correction refers to what the member JUST said: only the last two of their turns in the session are ones it may correct
FIX_TURNS = 2
_PENDING_MAX_KEYS = 256
_PHRASE = r"[A-Za-z0-9][A-Za-z0-9'’\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'’\-]*){0,3}"
_MARK = (r"(?:no|nope|actually|sorry|wait|oh|hang\s+on|correction|you\s+misheard(?:\s+me)?|i\s+said|i\s+meant|"
         r"that(?:['’]s|\s+is|\s+was)\s+(?:wrong|incorrect|not\s+(?:right|correct)))")
_LEAD = rf"^\W*(?P<lead>(?:{_MARK}[,.;:!\-\s]+)*)"
_PAIR_RES = (
    # "it's actually a bike shop, not a pike shop" / "no, a bike shop not a pike shop" / "I said Perth, not Sydney"
    re.compile(_LEAD + rf"(?:(?:it|that|this)(?:['’]s|\s+is|\s+was)\s+)?(?P<act>(?:actually|really)\s+)?(?P<n>{_PHRASE})[,;]?\s+"
               rf"(?:and\s+)?not\s+(?P<o>{_PHRASE})\W*$", re.IGNORECASE),
    # "no, not a pike shop, a bike shop"
    re.compile(_LEAD + rf"not\s+(?P<o>{_PHRASE})[,;]?\s+(?:(?:it|that)(?:['’]s|\s+is)\s+)?(?P<act>(?:actually|really)\s+)?"
               rf"(?P<n>{_PHRASE})\W*$", re.IGNORECASE),
)
_FILLER_RE = re.compile(r"^(?:(?:a|an|the|my|our|your|actually|really|just)\s+)+", re.IGNORECASE)
# a one-word "value" that is really the rest of an everyday sentence ("it's fine, not bad", "not sure") is never a correction
_PAIR_STOP = frozenset({"it", "that", "this", "so", "sure", "right", "wrong", "correct", "incorrect", "really", "yet", "bad", "good",
                        "much", "quite", "too", "very", "just", "only", "even", "me", "you", "now", "then", "there", "here",
                        "fine", "okay", "ok", "yes", "no"})
# a value never opens on a pronoun / contraction or on the words "not" negates in everyday speech ("not sure yet", "not really")
_PAIR_BAD_HEAD = frozenset({"it", "it's", "that", "that's", "this", "he", "he's", "she", "she's", "they", "they're", "we", "we're",
                            "i", "i'm", "you", "you're", "there", "there's", "sure", "really", "yet", "quite", "too", "so", "very",
                            "just", "only", "even", "much", "that'll", "gonna", "going"})
# a VALUE never carries a verb / auxiliary: "the meeting is tomorrow" is a clause, so "the meeting is tomorrow, not today" is not
# a value swap (codex review, #2058 - it rewrote "User is free today" to "User is free meeting is tomorrow")
_CLAUSE_WORDS = frozenset({"is", "isn't", "are", "aren't", "was", "wasn't", "were", "weren't", "be", "been", "being", "am", "will",
                           "won't", "would", "wouldn't", "has", "hasn't", "have", "haven't", "had", "do", "does", "did", "don't",
                           "doesn't", "didn't", "can", "can't", "could", "should", "shall", "may", "might", "must", "want", "wants",
                           "need", "needs", "said", "say", "says", "think", "know", "like", "likes", "not", "to", "i'd", "i'll"})
_TIME_RE = re.compile(r"^(?:today|tonight|tomorrow|yesterday|morning|afternoon|evening|night|noon|midnight|weekend|week|month|year|"
                      r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|"
                      r"january|february|march|april|may|june|july|august|september|october|november|december|"
                      r"am|pm|o'clock|\d[\d:.]*(?:am|pm|st|nd|rd|th)?)$")


def _is_time(words: "list[str]") -> bool:
    return any(_TIME_RE.match(w) for w in words)


def same_shape(new: str, old: str) -> bool:
    """The two halves of a value swap are the same KIND of thing: the same number of words (at most three), no verb or
    auxiliary in either (a clause is never a value), both a time or neither, and a multi-word pair keeps one word in place
    ("bike shop" / "pike shop", "New York" / "New Jersey") - a misheard or misspoken value, not two unrelated phrases. Pure."""
    a = [w.replace("’", "'") for w in new.lower().split()]
    b = [w.replace("’", "'") for w in old.lower().split()]
    if not a or len(a) != len(b) or len(a) > 3:
        return False
    if any(w in _CLAUSE_WORDS or w.endswith("'s") for w in a + b):
        return False
    if _is_time(a) != _is_time(b):
        return False
    return len(a) == 1 or any(x == y for x, y in zip(a, b))


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _bare(value: str) -> str:
    return _FILLER_RE.sub("", " ".join((value or "").strip(" ,.;").split()))


def correction_pair(text: str) -> Optional[tuple[str, str]]:
    """``(new, old)`` for an explicit correction of a just-said value - "it's actually a bike shop, not a pike shop",
    "no, Perth not Sydney", "I said Perth, not Sydney", "not a pike shop, a bike shop" - else None. Pure.

    The correction must SAY it is one: a lead cue ("no", "actually", "sorry", "that's wrong", "I said/meant", ...) or "actually"
    before the new value. "Tea, not coffee" (an answer), "it's fine, not bad" (an everyday sentence) are not corrections.
    Leading articles / possessives are dropped from both values, so they are matched as the phrase itself. Each sentence of a
    multi-sentence turn is read on its own; the first correction wins."""
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())):
        if not sentence or len(sentence.split()) > SWAP_MAX_WORDS:
            continue
        for rx in _PAIR_RES:
            m = rx.match(sentence)
            if not m:
                continue
            if not (m.group("lead") or "").strip() and not (m.group("act") or "").strip():
                continue
            new, old = _bare(m.group("n")), _bare(m.group("o"))
            if not new or not old or new.lower() == old.lower():
                continue
            if any(len(v.split()) == 1 and v.lower() in _PAIR_STOP for v in (new, old)):
                continue
            if any(v.split()[0].lower().replace("’", "'") in _PAIR_BAD_HEAD for v in (new, old)):
                continue          # "actually it's not sure yet": a clause, not a value
            if not same_shape(new, old):
                continue          # "actually, the meeting is tomorrow, not today": a clause against a token
            return new, old
    return None


@dataclass
class _PendingCorrection:
    new: str
    old: str
    utterance: str
    earlier: tuple[str, ...]          # the owner's earlier user turns of the session, normalised (the turns it may correct)
    at: float


_PENDING: "dict[tuple[str, str], list[_PendingCorrection]]" = {}


def _pending_key(user_id: str, session_id: str) -> tuple[str, str]:
    return (user_id or "").strip().lower(), (session_id or "").strip()


def _live_pending(user_id: str, session_id: str, now: Optional[float] = None) -> list[_PendingCorrection]:
    import time

    key = _pending_key(user_id, session_id)
    now = time.time() if now is None else now
    keep = [p for p in _PENDING.get(key, []) if now - p.at <= RECENT_WINDOW_S]
    if keep:
        _PENDING[key] = keep
    else:
        _PENDING.pop(key, None)
    return keep


def note_correction(user_id: str, session_id: str, new: str, old: str, utterance: str, earlier: "list[str]") -> None:
    """Remember a member's correction for the rest of the window (in process; a restart forgets it - the window is minutes)."""
    import time

    if not user_id or not session_id or _is_unidentified(user_id):
        return
    key = _pending_key(user_id, session_id)
    entries = _live_pending(user_id, session_id)
    recent = [e for e in earlier if (e or "").strip()][:FIX_TURNS]      # newest first: the turns this correction follows
    entries.append(_PendingCorrection(new, old, " ".join((utterance or "").split()),
                                      tuple(_norm(e) for e in recent), time.time()))
    _PENDING[key] = entries[-8:]
    while len(_PENDING) > _PENDING_MAX_KEYS:
        _PENDING.pop(next(iter(_PENDING)))


def _phrase_re(value: str):
    return re.compile(r"(?<![A-Za-z0-9'’])" + re.escape(value).replace(r"\ ", r"\s+") + r"(?![A-Za-z0-9'’])", re.IGNORECASE)


def _swap_phrase(text: str, old: str, new: str) -> str:
    rx = _phrase_re(old)
    m = rx.search(text or "")
    if not m:
        return text
    found = m.group(0)
    value = new[:1].upper() + new[1:] if found[:1].isupper() and not new[:1].isupper() else new
    return text[:m.start()] + value + text[m.end():]


def correct_fact(user_id: str, session_id: Optional[str], fact: str, turn_text: str) -> Optional[FactCorrection]:
    """``FactCorrection(corrected_fact, correcting_utterance, old, new)`` when THIS member corrected, later in the SAME session, a value the fact holds
    and ``turn_text`` (the user turn the fact was mined from) is one of the turns that correction followed; else None. Pure
    apart from the in-process registry. Newest correction wins."""
    if not user_id or not session_id or _is_unidentified(user_id) or not fact or not turn_text:
        return None
    for p in reversed(_live_pending(user_id, session_id)):
        if not _derives_from(turn_text, p.earlier):
            continue
        rx = _phrase_re(p.old)
        if rx.search(fact) and rx.search(turn_text):
            return FactCorrection(_swap_phrase(fact, p.old, p.new), p.utterance, p.old, p.new)
    return None


def global_swap_allowed(text: str) -> bool:
    """May the store-wide name swap (``maybe_swap``) act on ``text``? Only when it names a RELATION ("my sister is Marisol not
    Marisa": the relation pins the row) or is a swap the same-session path does not read (no correction cue). A generic
    "actually Perth, not Sydney" belongs to the session-bounded path only - globally it would rewrite whichever older,
    unrelated row happens to hold "Sydney". Pure."""
    sw = swap_statement(text)
    if sw is None:
        return False
    return bool(sw[0]) or correction_pair(text) is None


class FactCorrection(NamedTuple):
    fact: str           # the fact with the old value replaced
    utterance: str      # the member's correcting words (evidence)
    old: str
    new: str


def correct_claim(claim, fc: "FactCorrection"):
    """The extractor's claim row for a fact ``correct_fact`` rewrote, rebuilt for the CORRECTED value: ``obj`` with the old
    value replaced and ``quote`` = the member's correcting words (their verbatim support for the new value). None (drop the
    claim, the write is judged on its lexical floor) when the claim's value does not hold the old value. Pure."""
    if claim is None:
        return None
    obj = str(getattr(claim, "obj", "") or "")
    if not _phrase_re(fc.old).search(obj):
        return None
    try:
        import dataclasses

        return dataclasses.replace(claim, obj=_swap_phrase(obj, fc.old, fc.new), quote=fc.utterance)
    except Exception:  # noqa: BLE001 - an unexpected claim shape is dropped, never stored stale
        return None


def _derives_from(turn_text: str, earlier: "tuple[str, ...]") -> bool:
    """``turn_text`` is one of the (normalised) turns a correction followed - the same turn, give or take what the lane trimmed
    (own_words) or appended. A SHORT turn ("ok") is never matched by containment: it would make any later turn that happens
    to contain it look earlier."""
    turn = _norm(turn_text)
    if not turn:
        return False
    return any(turn == e or (min(len(turn.split()), len(e.split())) >= _MIN_CONTAIN_WORDS and (turn in e or e in turn))
               for e in earlier if e)


def _is_unidentified(user_id: Optional[str]) -> bool:
    try:
        from user_filters import is_unidentified

        return is_unidentified(user_id)
    except Exception:  # noqa: BLE001
        return (user_id or "").strip().lower() in ("guest", "anonymous", "voice-guest", "voice-daemon", "")


def _row_session(meta: dict) -> str:
    return str(meta.get("session_id") or "").strip()


async def maybe_fix_recent(text: str, user_id: str, session_id: str, *, svc=None,
                           now: Optional[float] = None) -> Optional[CorrectionResult]:
    """Explicit same-session correction: rewrite the owner's rows of THIS session (last ``RECENT_WINDOW_S``) that hold the old
    phrase, and remember the correction for a digest of an earlier turn that has not landed yet (``correct_fact``). Returns the
    result only when a stored row changed (a correction is never claimed without one); None otherwise. Never raises."""
    if not enabled() or not session_id or _is_unidentified(user_id):
        return None
    pair = correction_pair(text)
    if pair is None:
        return None
    new, old = pair
    try:
        try:
            import user_prefs

            if await user_prefs.is_memory_opted_out(user_id):
                return None
        except Exception:  # noqa: BLE001
            pass
        earlier = await _recent_user_messages(user_id, session_id, text)
        note_correction(user_id, session_id, new, old, text, earlier)
        just_said = tuple(_norm(e) for e in earlier if (e or "").strip())[:FIX_TURNS]
        if svc is None:
            from memory_service import get_memory_service

            svc = get_memory_service()
        import time

        now = time.time() if now is None else now
        uid = user_id.strip().lower()
        old_re = _phrase_re(old)
        hits = []
        for r in await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT):
            meta = r.metadata or {}
            if str(meta.get("user_id") or meta.get("wing") or "").strip().lower() != uid or _row_session(meta) != session_id:
                continue
            try:
                added = float(meta.get("added_ts") or 0.0)
            except (TypeError, ValueError):
                added = 0.0
            if now - added > RECENT_WINDOW_S or not old_re.search(r.text or ""):
                continue
            # the row must DERIVE from the value being corrected: its evidence (the member's own words it was mined from) is one
            # of the turns this correction follows AND states the old value - never a blind swap of a word some other fact uses
            excerpt = str(meta.get("source_excerpt") or "")
            if not old_re.search(excerpt) or not _derives_from(excerpt, just_said):
                continue
            hits.append(r)
        if not hits or len(hits) > SWAP_MAX_ROWS:
            return None
        from provenance_answers import _speculation_barrier

        await _speculation_barrier()
        changed: list[str] = []
        for r in hits:
            res = await apply_row_correction(user_id, r.id, _swap_phrase(r.text, old, new), utterance=text,
                                             session_id=session_id, svc=svc)
            if res is not None:
                changed.extend(res.changed)
        if not changed:
            return None
        logger.info("CORRECTION_APPLIED kind=recent user=%s rows=%d", user_id, len(changed))
        return CorrectionResult("recent", f"Got it - {new}, not {old}. I've fixed my note.", changed)
    except Exception as exc:  # noqa: BLE001 — a correction never breaks the turn
        logger.warning("correction_apply recent fix failed user=%s: %s", user_id, type(exc).__name__)
        return None


# ── entry point ──────────────────────────────────────────────────────────────

async def maybe_apply(
    text: str, user_id: str, session_id: str, *, recent_messages: Optional[list[str]] = None,
    svc=None, db=None,
) -> Optional[CorrectionResult]:
    """Apply a spoken/typed correction to the stored record, or None (the brain answers).

    Never raises: any failure is logged and the turn goes on to the brain."""
    if not enabled() or _is_unidentified(user_id):
        return None
    if not is_correction_turn(text):
        return None
    opened = False
    try:
        try:
            import user_prefs

            if await user_prefs.is_memory_opted_out(user_id):
                return None
        except Exception:  # noqa: BLE001
            pass
        if db is None:
            from person_extractor import _ensure_db

            db, opened = await _ensure_db(None)
        if is_date_correction(text):
            if recent_messages is None:
                recent_messages = await _recent_user_messages(user_id, session_id, text)
            res = await apply_date_correction(text, user_id, recent_messages, svc=svc, db=db,
                                              session_id=session_id)
            if res:
                return res
        return await apply_pet_correction(text, user_id, svc=svc, db=db, session_id=session_id)
    except Exception as exc:  # noqa: BLE001 — a correction never breaks the turn
        logger.warning("correction_apply failed user=%s: %s", user_id, type(exc).__name__)
        return None
    finally:
        if opened and db is not None:
            try:
                await db.close()
            except Exception:  # noqa: BLE001
                pass
