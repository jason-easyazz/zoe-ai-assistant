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
from typing import Optional

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
    kind: str                 # "date" | "pet" | "row"
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
    spec = pg.EdgeSpec("pet", "Pet owner", "Pet", "pet", authority="user_stated", origin=SOURCE, evidence=evidence)
    try:
        async with pg.edge_transaction(db, user_id, a, b):
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
    if not enabled() or not user_id or user_id in ("guest", "voice-daemon", "") or not (new_text or "").strip():
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


# ── entry point ──────────────────────────────────────────────────────────────

async def maybe_apply(
    text: str, user_id: str, session_id: str, *, recent_messages: Optional[list[str]] = None,
    svc=None, db=None,
) -> Optional[CorrectionResult]:
    """Apply a spoken/typed correction to the stored record, or None (the brain answers).

    Never raises: any failure is logged and the turn goes on to the brain."""
    if not enabled() or not user_id or user_id in ("guest", "voice-daemon", ""):
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
