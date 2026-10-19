"""Approve a pendant day-review candidate: the ONLY module that writes what the day review proposed (``omi_day_review.py``).

Nothing the pendant heard reaches a list, the calendar, a reminder, a contact or memory except through ``approve`` here, called by
the owner's own approval (``routers/omi_day_review.py``). Every write goes through the EXISTING writer of its store, with provenance:

  calendar_event  ``calendar_service.create_event_record``, personal, ``metadata`` = {"source": "pendant", "candidate_id", "day",
                  "recording_ids", "approved_via"}
  list_item       ``list_service.add_item_to_list`` (the household list of its type, as a spoken add); an item already open on that
                  list is linked, not added twice
  reminder        ``reminder_service.create_reminder_record`` (role check, notification, scheduling); the description says it came
                  from the pendant on that day
  follow_up       an OPEN LOOP (``open_loops``, owner decision D5 2026-10-18): no alarm, ``context='pendant:<candidate>'`` keeps it
                  out of the proactive engine; read back by "what's on my plate?" (``panel_review_cards``)
  person          ``contacts_store.ensure_person``: the owner's live person of that name, or a new PERSONAL row in their own layer
  owner_fact      ``MemoryService.ingest(source=origin="omi_review")`` - class ``user_confirmed`` (the approval is the owner
                  confirming their own words), ``source_excerpt`` = the quoted span, ``turn_ref`` = the recording, metadata
                  ``provenance=pendant``. ADD only: never an edit or a supersede. A write that returns nothing is a failure.
  person_fact     a fact about one of the owner's OWN people (``payload.person_id``): ``MemoryService.ingest`` of "<Name>: <fact>",
                  ``entity_type='person'`` / ``entity_id`` = that people row, class ``user_confirmed`` (the approval), personal
                  (never family-visible), ``turn_ref`` = the segment, metadata ``stated_by`` (``stated_by_subject`` - the person
                  said it on a voice the owner tagged - or ``owner_stated``), the candidate, day, recording and segment ids. ADD only.
                  It contradicts any approved row about that person (``omi_day_review.facts_conflict``, whoever said either) ->
                  ``held_conflict``, never written beside it; the same fact already there -> linked (``known:memory:<id>``), not
                  written twice.
  person_relation the person's relationship to the OWNER (``people.relationship``) on the owner's PERSONAL people row only, and only
                  when it is empty; a different one already there -> ``held_conflict``; written_ref ``people:<id>#relationship``.

``unwrite`` erases what an approved person_fact / person_relation wrote: forgetting the person, the name or the recording it came
from (``omi_day_review.forget_people`` / ``erase_entity`` / ``forget_recording``) reaches it. A candidate deleted by a forget
while its approval was writing is un-written at once (the approval answers 410).

The candidate row is the provenance index: ``written_ref`` (``events:<id>``, ``list_items:<id>``, ``reminders:<id>``,
``people:<id>``, ``memory:<id>``), ``decided_at``, ``decided_via``. A failed write puts the candidate back to ``pending`` with
``write_error`` (nothing half-written is reported as written); ``approving`` is a short claim so two taps cannot write twice, and a
claim left stale by a crash is re-taken after ``STALE_CLAIM_S`` with an existence check first.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import omi_day_review as dr
import user_erase_gate as erase_gate

logger = logging.getLogger(__name__)

STALE_CLAIM_S = 600
EDITABLE = {"calendar_event": ("title", "date", "time"), "list_item": ("item", "list"), "reminder": ("title", "date", "time"),
            "follow_up": ("title",), "person": ("name", "relation"), "owner_fact": ("fact",), "person_fact": ("fact",),
            # a relationship is NOT editable: the candidate is the quoted words, and an edited one would carry provenance its quote
            # does not support (dismiss it, and set the relationship on the person instead)
            "person_relation": ()}
VIAS = ("voice", "panel_touch", "desktop", "phone")
#: ``open_loops.context`` of a loop the proactive engine must never raise (a pendant follow-up, D5)
QUIET_LOOP_PREFIX = "pendant:"


class ApplyError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prov(c: dict, via: str) -> dict:
    return {"source": dr.PROVENANCE, "writer": dr.WRITER, "candidate_id": c["id"], "day": c["day_local"],
            "recording_ids": c.get("recording_ids") or [], "approved_via": via}


def apply_edits(kind: str, payload: dict, fields: Optional[dict]) -> dict:
    """The owner's edits, limited to the kind's editable fields, re-validated like the analysis would have. 422 on a bad value."""
    out = dict(payload)
    for k, v in (fields or {}).items():
        if k not in EDITABLE.get(kind, ()):
            raise ApplyError(f"field_not_editable:{k}", 422)
        v = dr.clean(v, 160)
        if k in ("title", "item", "name", "fact") and not v:
            raise ApplyError(f"empty_field:{k}", 422)
        if k == "date" and v:
            try:
                from reminder_service import normalize_due_date
                v = normalize_due_date(v) or ""
            except Exception:  # noqa: BLE001 - HTTPException 422 from the parser
                raise ApplyError("bad_date", 422)
        elif k == "time" and v:
            v = dr.hhmm(v) or ""
            if not v:
                raise ApplyError("bad_time", 422)
        elif k == "list" and v not in ("shopping", "tasks"):
            raise ApplyError("bad_list", 422)
        out[k] = v
        if kind in dr.PERSON_KINDS and v != payload.get(k):
            out["edited_by_owner"] = True      # no longer the exact words: the row says the owner changed it
    return out


# --- the writers (module attributes so a test can stand in for a store) --------------------------------------------------
async def _role(db, user_id: str) -> str:
    try:
        cur = await db.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        return (row["role"] if row else None) or "user"
    except Exception:  # noqa: BLE001
        return "user"


async def write_calendar_event(user_id: str, c: dict, via: str) -> str:
    p = c["payload"]
    if not p.get("date"):
        raise ApplyError("needs_date", 422)
    from calendar_service import create_event_record
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        cur = await db.execute("SELECT id FROM events WHERE user_id = ? AND deleted = 0 AND metadata LIKE ?", (user_id, f'%"{c["id"]}"%'))
        row = await cur.fetchone()
        if row:                                   # a retried approval after a crash: the event is already there
            return f"events:{row['id']}"
        rec = await create_event_record(db, user_id=user_id, title=p["title"], start_date=p["date"], start_time=p.get("time") or None,
                                        all_day=not p.get("time"), visibility="personal", metadata=json.dumps(_prov(c, via)))
        await db.commit()
    return f"events:{rec['id']}"


async def _list_add(user_id: str, list_type: str, text: str) -> str:
    from db_compat import get_compat_db
    from list_service import add_item_to_list, default_visibility, find_list_id

    name = list_type.capitalize()
    async with get_compat_db() as db:
        lid = await find_list_id(db, user_id=user_id, list_type=list_type, name=name)
        if lid:
            cur = await db.execute("SELECT id FROM list_items WHERE list_id = ? AND lower(text) = lower(?) AND deleted = 0 "
                                   "AND (completed = 0 OR completed IS NULL) LIMIT 1", (lid, text))
            row = await cur.fetchone()
            if row:
                return f"list_items:{row['id']}"
        out = await add_item_to_list(db, user_id=user_id, list_type=list_type, list_name=name, text=text,
                                     new_list_visibility=default_visibility(list_type), dedup_window_s=0)
        await db.commit()
    return f"list_items:{out['item_id']}"


async def write_list_item(user_id: str, c: dict, via: str) -> str:
    return await _list_add(user_id, c["payload"].get("list") or "shopping", c["payload"]["item"])


async def write_follow_up(user_id: str, c: dict, via: str) -> str:
    """Owner decision D5 (2026-10-18): an OPEN LOOP - no alarm, never raised by the proactive engine (``context`` marks it
    ``pendant:<candidate>``; ``panel_review_cards`` reads it back on request). A retry finds the row it already wrote."""
    from db_compat import get_compat_db

    ref = f"{QUIET_LOOP_PREFIX}{c['id']}"
    async with get_compat_db() as db:
        for _ in range(2):
            cur = await db.execute("SELECT id FROM open_loops WHERE user_id = ? AND context = ?", (user_id, ref))
            row = await cur.fetchone()
            if row:
                return f"open_loops:{row['id']}"
            await db.execute("INSERT INTO open_loops (user_id, loop_text, context, emotional_weight) VALUES (?, ?, ?, 1)",
                             (user_id, str(c["payload"]["title"])[:300], ref))
            await db.commit()
    raise ApplyError("not_stored")


async def write_reminder(user_id: str, c: dict, via: str) -> str:
    from db_compat import get_compat_db
    from models import ReminderCreate
    from reminder_service import create_reminder_record

    p = c["payload"]
    async with get_compat_db() as db:
        cur = await db.execute("SELECT id FROM reminders WHERE user_id = ? AND deleted = 0 AND title = ? AND description LIKE ?",
                               (user_id, p["title"], f"%{c['id'][:8]}%"))
        row = await cur.fetchone()
        if row:
            return f"reminders:{row['id']}"
        made = await create_reminder_record(
            ReminderCreate(title=p["title"], description=f"From your pendant on {c['day_local']} (you approved it; ref {c['id'][:8]}).",
                           due_date=p.get("date") or None, due_time=p.get("time") or None),
            user={"user_id": user_id, "role": await _role(db, user_id)}, db=db)
        await db.commit()
    rid = str(made.get("id") or "")
    if not rid:
        raise ApplyError("not_stored")
    return f"reminders:{rid}"


async def write_person(user_id: str, c: dict, via: str) -> str:
    import contacts_store
    from db_compat import get_compat_db

    p = c["payload"]
    async with get_compat_db() as db:
        pid, _created = await contacts_store.ensure_person(db, user_id, p["name"], relationship=p.get("relation") or None)
        await db.commit()
    return f"people:{pid}"


async def write_owner_fact(user_id: str, c: dict, via: str) -> str:
    import memory_authority as auth
    from memory_service import get_memory_service

    ev = (c.get("evidence") or [{}])[0]
    svc = get_memory_service()
    # Trust order: user-stated > confirmed > inferred. A pendant fact is confirmed by THIS approval, but it never stands against a
    # row the owner told Zoe in their own words: a contradiction with any approved row at or above user_stated is HELD (the
    # owner settles it), never written beside it. The scan is the authority wall's own (``_protected_conflict``), asked at the rank
    # just below user_stated so every user-stated / confirmed / operator row counts.
    clash = await svc._protected_conflict(user_id, c["payload"]["fact"], auth.DERIVED_RANK, strict=True)   # unreadable = no write
    if clash is not None:
        raise ApplyError("held_conflict")
    ref = await svc.ingest(
        c["payload"]["fact"], user_id=user_id, source=dr.WRITER, origin=dr.WRITER, memory_type="fact",
        confidence=0.9, source_excerpt=ev.get("quote") or None, anchor_text=ev.get("quote") or None,
        turn_ref=f"omi_recording:{ev.get('recording_id', '')}", authority="user_confirmed",
        metadata={"provenance": dr.PROVENANCE, **_prov(c, via)})
    if ref is None:                                # the forget shield, an opt-out, a dedup: NOT written, and the owner is told
        raise ApplyError("not_stored")
    return f"memory:{ref.id}"


async def _own_person(user_id: str, pid: str) -> dict:
    """The owner's OWN live people row the candidate is about; gone (deleted, forgotten, another member's) = 409, nothing written."""
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        cur = await db.execute("SELECT id, name, birthday, relationship, visibility FROM people WHERE id = ? AND user_id = ? "
                               "AND (deleted = 0 OR deleted IS NULL)", (pid, user_id))
        row = await cur.fetchone()
    if not row or not str(row["name"] or "").strip():
        raise ApplyError("person_gone")
    return {k: row[k] for k in ("id", "name", "birthday", "relationship", "visibility")}


async def write_person_fact(user_id: str, c: dict, via: str) -> str:
    """A fact about one of the owner's people, into the owner's memory linked to that person. Trust order (owner 2026-10-18):
    the owner's own words > confirmed > inferred, and a fact the person said about themself (``stated_by_subject``) and one the owner
    said about them (``owner_stated``) never silently overwrite each other: this is ADD only, and a contradiction with ANY approved
    row about the person - whoever said it, whatever wrote it - is held for the owner (``held_conflict``)."""
    from memory_service import get_memory_service

    p = c["payload"]
    person = await _own_person(user_id, str(p.get("person_id") or ""))
    if dr.may_be_child(person):
        raise ApplyError("child")              # a (possible) child is a label only, whatever the analysis saw
    name, fact = str(person["name"]).strip(), str(p["fact"]).strip()
    svc = get_memory_service()
    # every row already linked to the person, read STRICTLY (``list_by_entity`` answers [] on a failed read, and an unreadable index
    # must write nothing rather than skip the conflict check): approved rows only, as recall would serve them
    rows = await svc._run_sync(svc._entity_rows_sync, [person["id"]], user_id)
    mine = next((r for r in rows if _written_by(r, c["id"])), None)
    if mine is not None:
        return f"memory:{mine.id}"             # a retry after a crash between the write and ``written_ref``: it is THIS approval's row
    have = [r for r in rows if str(r.metadata.get("status") or "approved") == "approved"]
    if any(dr.facts_conflict(fact, r.text, name) for r in have):
        raise ApplyError("held_conflict")
    same = next((r for r in have if dr.same_fact(fact, r.text, name)), None)
    if same is not None:
        return f"known:memory:{same.id}"       # already known: linked, not written twice (and never erased on this one's behalf)
    ev = (c.get("evidence") or [{}])[0]
    segs = [str(s) for s in (p.get("segment_ids") or []) if s]
    ref = await svc.ingest(
        f"{name}: {fact}", user_id=user_id, source=dr.WRITER, origin=dr.WRITER, memory_type="fact", confidence=0.9,
        source_excerpt=ev.get("quote") or None, anchor_text=ev.get("quote") or None,
        turn_ref=dr.turn_ref_for(segs, str(ev.get("recording_id") or "")), authority="user_confirmed",
        entity_type="person", entity_id=person["id"], tags=["person", "pendant"],
        metadata={"provenance": dr.PROVENANCE, **_prov(c, via), "stated_by": p.get("stated_by") or dr.OWNER_STATED,
                  "person_id": person["id"], "segment_ids": segs, "edited_by_owner": bool(p.get("edited_by_owner"))})
    if ref is None:                            # the forget shield, an opt-out, a dedup: NOT written, and the owner is told
        raise ApplyError("not_stored")
    return f"memory:{ref.id}"


async def write_person_relation(user_id: str, c: dict, via: str) -> str:
    """The person's relationship to the owner, on the owner's PERSONAL people row, only where none is set. A family-visible row is
    refused (``shared_person``): writing there would tell every member what the owner's pendant heard. A different relationship
    already set is held for the owner (``held_conflict``), never overwritten."""
    from db_compat import get_compat_db

    p = c["payload"]
    person = await _own_person(user_id, str(p.get("person_id") or ""))
    if str(person.get("visibility") or "") != "personal":
        raise ApplyError("shared_person")
    rel = dr.clean(p.get("relation") or "", 60)
    if dr.may_be_child(person) or dr.relation_names_child(rel):
        raise ApplyError("child")              # nothing about a (possible) child, whatever the analysis or an edit says
    have = str(person.get("relationship") or "").strip()
    ref = f"people:{person['id']}#relationship"
    if have:
        if dr._norm(have) == dr._norm(rel):
            # THIS approval's own write when a crash left its intent recorded (below); otherwise someone else's, only linked
            return ref if c.get("written_ref") == ref else "known:" + ref
        raise ApplyError("held_conflict")
    async with get_compat_db() as db:
        # the intent FIRST: a crash between the people update and the final ``written_ref`` must leave a pointer a forget can follow
        await db.execute("UPDATE omi_day_candidates SET written_ref = ? WHERE id = ? AND user_id = ? AND status = 'approving'",
                         (ref, c["id"], user_id))
        cur = await db.execute("UPDATE people SET relationship = ? WHERE id = ? AND user_id = ? AND relationship IS NULL "
                               "AND (deleted = 0 OR deleted IS NULL)", (rel, person["id"], user_id))
        await db.commit()
    if not getattr(cur, "rowcount", 0):
        raise ApplyError("held_conflict")      # set (or removed) between the read and the write: the owner looks again
    return ref


#: what the owner can do with a ``held_conflict`` candidate (the review surface draws one button per option)
KEEP_EXISTING, EDIT, RETRY = "keep_existing", "edit", "retry"


async def conflict_detail(user_id: str, c: dict) -> Optional[dict]:
    """What a pending ``held_conflict`` candidate disagrees with, read NOW for the owner's review surface. READ-ONLY, never raises.

    ``existing`` is the owner's OWN approved row it contradicts (the memory text, or the people row's relationship), found by the
    same test the writer ran (``facts_conflict`` / ``_protected_conflict``); None when it cannot be read or is no longer there.
    ``options``: ``keep_existing`` (dismiss this one: approval is ADD only, nothing ever replaces the row the owner has), ``edit``
    (the kind allows it; the edit is held to the quote again), ``retry`` (nothing disagrees any more: approving again may write)."""
    if not isinstance(c, dict) or c.get("status") != "pending" or c.get("write_error") != "held_conflict":
        return None
    kind, p = c.get("kind"), c.get("payload") or {}
    options = [KEEP_EXISTING] + ([EDIT] if EDITABLE.get(kind) else [])
    existing: Optional[str] = None
    try:
        if kind == "person_fact":
            from memory_service import get_memory_service
            person = await _own_person(user_id, str(p.get("person_id") or ""))
            name, fact = str(person["name"]).strip(), str(p.get("fact") or "").strip()
            svc = get_memory_service()
            rows = await svc._run_sync(svc._entity_rows_sync, [person["id"]], user_id)
            existing = next((r.text for r in rows if str(r.metadata.get("status") or "approved") == "approved"
                             and dr.facts_conflict(fact, r.text, name)), None)
        elif kind == "person_relation":
            person = await _own_person(user_id, str(p.get("person_id") or ""))
            have = str(person.get("relationship") or "").strip()
            existing = have if have and dr._norm(have) != dr._norm(dr.clean(p.get("relation") or "", 60)) else None
        elif kind == "owner_fact":
            import memory_authority as auth
            from memory_service import get_memory_service
            clash = await get_memory_service()._protected_conflict(user_id, str(p.get("fact") or ""), auth.DERIVED_RANK, strict=True)
            existing = clash[0].text if clash is not None else None
        else:
            return {"existing": None, "options": options}
    except Exception as exc:  # noqa: BLE001 - unreadable: the owner still sees the hold and can keep what they had
        logger.debug("omi_review_apply: conflict detail unavailable (%s)", type(exc).__name__)
        return {"existing": None, "options": options}
    if existing is None:
        return {"existing": None, "options": [RETRY] + options}
    return {"existing": dr.clean(existing, 240), "options": options}


def _written_by(row: Any, candidate_id: str) -> bool:
    """Was this memory row written by the approval of ``candidate_id``? (``ingest`` keeps an extra metadata key as ``candidate_<key>``.)"""
    md = getattr(row, "metadata", None) or {}
    return bool(candidate_id) and str(md.get("candidate_candidate_id") or "") == str(candidate_id) \
        and str(md.get("origin") or "") == dr.WRITER


async def unwrite(user_id: str, rows: list) -> int:
    """Erase what ``person_fact`` / ``person_relation`` candidates wrote (forget: the person, the name, the recording). A memory row
    is HARD-erased (``MemoryService.erase_rows``: drawers, audit trail, the physical residue); a relationship goes back to empty only
    while it still holds the value this approval set. An ``approving`` candidate a crash left without ``written_ref`` is covered too:
    its memory row is found by the candidate id it carries, its relationship by the intent recorded before the update. A ``known:``
    link wrote nothing and erases nothing. RAISES on a failure, so the caller deletes nothing and is retried. Returns rows erased."""
    mem: list[str] = []
    rels: list[tuple[str, str]] = []
    orphans: dict[str, set] = {}           # person id -> candidate ids whose write may have landed without a ``written_ref``
    for r in rows:
        ref = str(r.get("written_ref") or "")
        if r.get("kind") == "person_fact" and ref.startswith("memory:"):
            mem.append(ref.split(":", 1)[1])
        elif r.get("kind") == "person_fact" and not ref and r.get("status") == "approving" and r.get("id"):
            orphans.setdefault(str((r.get("payload") or {}).get("person_id") or ""), set()).add(str(r["id"]))
        elif r.get("kind") == "person_relation" and ref.startswith("people:"):
            rels.append((ref.split(":", 1)[1].split("#", 1)[0], str((r.get("payload") or {}).get("relation") or "")))
    n = 0
    if mem or orphans:
        from memory_service import get_memory_service
        svc = get_memory_service()
        for pid, cids in orphans.items():      # read strictly: an unreadable index raises, and the forget is retried
            if pid:
                mem += [m.id for m in await svc._run_sync(svc._entity_rows_sync, [pid], user_id)
                        if any(_written_by(m, cid) for cid in cids)]
        if mem:
            out = await svc.erase_rows(user_id, mem, actor=user_id, reason="forgotten by request (pendant review)")
            n += int(out.get("rows_removed") or 0)
    if rels:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            for pid, rel in rels:
                cur = await db.execute("UPDATE people SET relationship = NULL WHERE id = ? AND user_id = ? AND relationship = ?",
                                       (pid, user_id, dr.clean(rel, 60)))
                n += int(getattr(cur, "rowcount", 0) or 0)
            await db.commit()
    if mem or rels:
        logger.info("OMI_REVIEW_UNWRITE user=%s memory=%d relationships=%d erased=%d", user_id, len(mem), len(rels), n)
    return n


WRITERS = {"calendar_event": "write_calendar_event", "list_item": "write_list_item", "reminder": "write_reminder",
           "follow_up": "write_follow_up", "person": "write_person", "owner_fact": "write_owner_fact",
           "person_fact": "write_person_fact", "person_relation": "write_person_relation"}


# --- the floors re-checked at write time (gap audit P0-3) ------------------------------------------------------------------
def _candidate_text(c: dict) -> str:
    return " ".join([json.dumps(c.get("payload") or {})] + [str(e.get("quote") or "") for e in (c.get("evidence") or [])])


async def _floors(user_id: str, c: dict) -> None:
    """Raise when the candidate may not be written NOW, whatever the analysis saw: a name forgotten since (forgotten means forever:
    the candidate is deleted), or distress content (never to a memory writer). The forget ledger fails CLOSED here: an unreadable
    ledger refuses the write rather than risk bringing a forgotten name back."""
    text = _candidate_text(c)
    if dr._distress(text):
        raise ApplyError("distress", 409)
    # restraint again at WRITE: the owner's edit at approval can bring in what the analysis never saw (cross-review B1, #2091)
    kind = c.get("kind")
    # a person_fact is held to the strictest set (a family word in it is another member's business); a relationship is a family word
    sensitive = dr._SENSITIVE_FACT | {"other_member"} if kind == "person_fact" else (
        dr._SENSITIVE_FACT if kind == "owner_fact" else dr._SENSITIVE_ALL)
    if dr._restraint(text) & sensitive:
        raise ApplyError("sensitive", 409)
    if kind == "person_fact" and dr._PERSON_SENSITIVE.search(text):
        raise ApplyError("sensitive", 409)       # D11 about another person, re-checked: the owner's edit may have added it
    try:
        import memory_forgotten as mf
        hit = bool(await mf.spans(user_id, text))
    except Exception:  # noqa: BLE001
        raise ApplyError("forget_ledger_unavailable", 503)
    if hit:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            await db.execute("DELETE FROM omi_day_candidates WHERE id = ? AND user_id = ?", (c["id"], user_id))
            await db.commit()
        raise ApplyError("forgotten", 410)


# --- approve ---------------------------------------------------------------------------------------------------------------
async def approve(user_id: str, cid: str, *, fields: Optional[dict] = None, via: str = "desktop") -> dict:
    """The owner approved candidate ``cid`` (optionally with edits). Returns the candidate as stored afterwards. Raises ``ApplyError``
    (404 not found / 409 already decided / 422 bad edit / 409 write failed)."""
    from db_compat import get_compat_db

    via = via if via in VIAS else "desktop"
    now = _now()
    gen = erase_gate.generation(user_id)
    async with get_compat_db() as db:
        cur = await db.execute("SELECT * FROM omi_day_candidates WHERE id = ? AND user_id = ?", (cid, user_id))
        row = await cur.fetchone()
        if not row:
            raise ApplyError("not_found", 404)
        c = dr.public(row)
        if c["status"] == "approved":
            return c
        if c["status"] == "dismissed":
            raise ApplyError("already_dismissed")
        payload = apply_edits(c["kind"], c["payload"], fields)
        if c["kind"] == "person_fact" and fields:
            # an edit must still rest on the words it cites: the stored row keeps the quote and who said it as provenance
            why = dr.fact_holds(str(payload.get("fact") or ""), str((c.get("evidence") or [{}])[0].get("quote") or ""),
                                str(payload.get("name") or ""))
            if why:
                raise ApplyError(f"edit_{why}", 422)
        stale = dr._iso(now - timedelta(seconds=STALE_CLAIM_S))
        cur = await db.execute(
            "UPDATE omi_day_candidates SET status = 'approving', payload = ?, decided_at = ?, decided_via = ?, write_error = NULL "
            "WHERE id = ? AND user_id = ? AND (status = 'pending' OR (status = 'approving' AND decided_at < ?))",
            (json.dumps(payload), dr._iso(now), via, cid, user_id, stale))
        await db.commit()
        if not getattr(cur, "rowcount", 0):
            raise ApplyError("in_progress")
    c["payload"] = payload
    try:
        # the member's erase gate (the commitments pattern): an erase that ran since this approval began wins, and one that starts
        # now waits for this write and then removes it
        async with erase_gate.writing(user_id, gen) as ok:
            if not ok:
                raise ApplyError("erased", 410)
            await _floors(user_id, c)
            ref = await globals()[WRITERS[c["kind"]]](user_id, c, via)
    except Exception as exc:  # noqa: BLE001 - every failure leaves the candidate pending and says why
        code = exc.code if isinstance(exc, ApplyError) else f"write_failed:{type(exc).__name__}"
        if code in ("forgotten", "erased"):        # already deleted: nothing to put back
            logger.info("OMI_REVIEW_APPLY user=%s kind=%s result=forgotten", user_id, c["kind"])
            raise
        async with get_compat_db() as db:
            await db.execute("UPDATE omi_day_candidates SET status = 'pending', write_error = ? WHERE id = ? AND user_id = ?",
                             (code[:80], cid, user_id))
            if c["kind"] in dr.PERSON_KINDS:        # a recorded intent that did not land is no write: a forget must not follow it
                await db.execute("UPDATE omi_day_candidates SET written_ref = NULL WHERE id = ? AND user_id = ?", (cid, user_id))
            await db.commit()
        logger.info("OMI_REVIEW_APPLY user=%s kind=%s result=failed code=%s", user_id, c["kind"], code)
        raise ApplyError(code, exc.status if isinstance(exc, ApplyError) else 409)
    async with get_compat_db() as db:
        cur = await db.execute("UPDATE omi_day_candidates SET status = 'approved', written_ref = ?, decided_at = ? WHERE id = ? "
                               "AND user_id = ?", (ref, dr._iso(_now()), cid, user_id))
        await db.commit()
    if c["kind"] in dr.PERSON_KINDS and not getattr(cur, "rowcount", 1):
        # a forget (the person, the name, the recording) deleted the candidate while this write ran: what it wrote goes too
        await unwrite(user_id, [{"kind": c["kind"], "written_ref": ref, "payload": c["payload"]}])
        logger.info("OMI_REVIEW_APPLY user=%s kind=%s result=forgotten_during_write", user_id, c["kind"])
        raise ApplyError("forgotten", 410)
    logger.info("OMI_REVIEW_APPLY user=%s kind=%s result=written", user_id, c["kind"])
    return await dr.get_candidate(user_id, cid) or c
