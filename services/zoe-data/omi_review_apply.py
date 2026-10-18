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
            "follow_up": ("title",), "person": ("name", "relation"), "owner_fact": ("fact",)}
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


WRITERS = {"calendar_event": "write_calendar_event", "list_item": "write_list_item", "reminder": "write_reminder",
           "follow_up": "write_follow_up", "person": "write_person", "owner_fact": "write_owner_fact"}


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
    sensitive = dr._SENSITIVE_FACT if c.get("kind") == "owner_fact" else dr._SENSITIVE_ALL
    if dr._restraint(text) & sensitive:
        raise ApplyError("sensitive", 409)
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
            await db.commit()
        logger.info("OMI_REVIEW_APPLY user=%s kind=%s result=failed code=%s", user_id, c["kind"], code)
        raise ApplyError(code, exc.status if isinstance(exc, ApplyError) else 409)
    async with get_compat_db() as db:
        await db.execute("UPDATE omi_day_candidates SET status = 'approved', written_ref = ?, decided_at = ? WHERE id = ? AND user_id = ?",
                         (ref, dr._iso(_now()), cid, user_id))
        await db.commit()
    logger.info("OMI_REVIEW_APPLY user=%s kind=%s result=written", user_id, c["kind"])
    return await dr.get_candidate(user_id, cid) or c
