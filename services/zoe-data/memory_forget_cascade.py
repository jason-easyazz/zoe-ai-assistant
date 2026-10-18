"""The forget cascade (memory fidelity audit P2.2): "forget Dana" also clears what was DERIVED from Dana.

Forgetting archived the palace rows and nothing else, so the user portrait, the user-model card, open
loops, proactive candidates and the ``people`` row all kept naming the person (audit 3.7). This runs
right after the sweep, with the entity name the person just spoke in hand (it is matched here and never
stored anywhere new):

  * ``user_portraits``      - the row is deleted when its text names the entity (derived text; the next
                              dreaming pass writes a fresh one from rows that no longer carry the name)
  * ``user_model_cards``    - deleted likewise; ``load_card_block`` rebuilds from the live rows
  * ``open_loops``          - an unresolved loop naming the entity is RESOLVED (archived, history kept)
  * ``proactive_candidates``- deleted (derived, rebuilt nightly from loops / moments / events)
  * ``people``              - the row is SOFT-deleted (``deleted = 1``, the row and its history are kept),
                              and the person's current relationship edges are CLOSED (``valid_to``)
  * ``contacts``            - the contact model (migration 0053, ``contacts_store.forget_entity``): the person's contact fields,
                              source-contact links, synced dates and merge reviews are DELETED, any other field or review that
                              names them too, and their synced source cards become content-free tombstones the next sync skips
  * ``voiceprints``         - the voiceprint the member's Omi tagging taught Zoe for that person (``learned_voiceprints``,
                              owner decision 2026-10-14) is DELETED (``contacts_store.forget_voiceprints``)
  * ``text_logs``           - the local JSONL logs that keep utterance text (``local_text_logs``: the router shadow log, the intent-miss
                              log): a record whose text names the entity is DROPPED, hash included
  * ``event_attendees``     - on the member's OWN events (alembic 0052): an attendee linked to the forgotten person,
                              or whose name / email names the entity, loses the link AND the name and email (the row
                              stays, anonymous, so the event keeps its shape); the desktop calendar's legacy
                              ``events.metadata.attendees`` entries are dropped the same way. Undo does not relink.

Every step is independent: a missing table or a DB blip in one never blocks the others. A step that fails is retried
once, and if it still fails it is RECORDED (``CascadeResult.failed``) and logged at ERROR - never swallowed: the caller
(``memory_forget_entity``) then does not say "forgotten" (forgotten means forever; a forget that silently leaves derived
text behind is a P1). The one exception is a process that has NEVER had a database pool (a lab, a bare unit test, a script -
``db_pool.pool_ever_initialised``): there is no store to clear, so that is not a failure. A process that had a pool and lost it
(or whose pool is exhausted) is a failure. Counts only are logged, never the name.

DURABLE RETRY (no name stored): a cascade that ends incomplete leaves a marker in the forget ledger for each step that failed
(``memory_forgotten.mark_pending``: the salted hash of a fixed step label - not the name, not a hash of the name, not a regex).
The next healthy pass (``retry_pending``: the nightly digest's pre-read, and the next forget by that member) re-runs ONLY the
failed steps against the ledger's own hashes (``memory_forgotten.LedgerPattern``), which can find a forgotten word in a derived
text without the word ever having been stored, then deletes the markers. So the member never has to say the forget again, and the
name still exists nowhere but in the turn that spoke it. Without the ledger secret there is no marker and no retry: saying it again
is the only redo (the caller's answer says so).

UNDO: the people soft-delete and the edge closes are recorded in-process for ``UNDO_WINDOW_S`` so
"keep the contact" (``restore_contacts``) can put exactly those rows back. It restores the contact, not the
forgotten memories, and not past a restart (the People panel can re-add the contact after that).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

UNDO_WINDOW_S = 900.0
RETRY_DELAY_S = 0.25   # one retry of a failed step after this pause (a pool blip); tests set 0

#: The cascade's fixed step labels (what ``CascadeResult.failed`` holds and what a pending marker is keyed by). ``database`` is the
#: connection itself: when it is pending every DB step is.
DB_STEPS = frozenset(("portrait", "card", "open_loops", "proactive_candidates", "people", "edges", "contacts", "voiceprints",
                      "calendar_attendees"))
DB_LABELS = DB_STEPS.union(("database",))
ALL_LABELS = ("database", "portrait", "card", "open_loops", "proactive_candidates", "people", "edges", "contacts", "voiceprints",
              "calendar_attendees", "restraint_mutes", "commitments", "omi_recordings", "omi_day_review", "text_logs")

# {user_id: (recorded_at_monotonic, [person ids], closed_at_iso)}
_last_cascade: dict[str, tuple[float, list[str], str]] = {}


@dataclass
class CascadeResult:
    portrait: int = 0
    card: int = 0
    loops: int = 0
    candidates: int = 0
    people: int = 0
    edges: int = 0
    contacts: int = 0       # contact-model rows removed / tombstoned (0053)
    voiceprints: int = 0    # learned voiceprints erased
    attendees: int = 0
    person_ids: list[str] = field(default_factory=list)
    closed_at: str = ""
    failed: list[str] = field(default_factory=list)   # labels of the stores that could NOT be cleared (after a retry)

    @property
    def complete(self) -> bool:
        """Every derived store was reached. False = the forget is NOT confirmed."""
        return not self.failed

    @property
    def summary_cleared(self) -> int:
        return self.portrait + self.card + self.loops + self.candidates

    @property
    def touched(self) -> bool:
        return bool(self.summary_cleared or self.people or self.contacts or self.voiceprints or self.attendees)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _name_re(name: str) -> "re.Pattern[str]":
    # the same whole-word pattern the ledger's tokenisation implies (separator-blind between the words of a name),
    # so a confirmed split alias ("Mari sol") clears "Mari-sol" in a contact or a summary too
    from memory_forgotten import name_pattern
    return name_pattern(name)


def _names(rx: "re.Pattern[str]", *texts: Any) -> bool:
    return any(t and rx.search(str(t)) for t in texts)


async def _rows(db, sql: str, params: tuple) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


def _store_missing(exc: BaseException) -> bool:
    """True when ``exc`` only says there is nothing of that store to reach, so nothing derived can be left behind: the table does
    not exist (Postgres ``UndefinedTableError`` / SQLite "no such table"), or this process has never had a database pool at all
    (a lab, a bare unit test). Anything else - a lost or exhausted pool, a failed query - is a real failure."""
    if type(exc).__name__ == "UndefinedTableError" or "no such table" in str(exc).lower():
        return True
    if not isinstance(exc, RuntimeError):
        return False
    try:
        from db_pool import pool_ever_initialised  # type: ignore[import]
        return not pool_ever_initialised()
    except Exception:  # noqa: BLE001 - cannot tell: treat as a failure, the safe side
        return False


async def _step(label: str, fn, res: Optional[CascadeResult] = None) -> int:
    """Run one store's clear; retry once; on a second failure record ``label`` in ``res.failed`` (ERROR) and return 0."""
    exc: Optional[BaseException] = None
    for attempt in (1, 2):
        try:
            return int(await fn() or 0)
        except Exception as e:  # noqa: BLE001 - one store failing must not stop the others
            exc = e
            if _store_missing(e):
                logger.info("memory_forget_cascade: %s skipped - nothing to reach (no database in this process, or no such table)", label)
                return 0
            if attempt == 1 and RETRY_DELAY_S > 0:
                await asyncio.sleep(RETRY_DELAY_S)
    logger.error("memory_forget_cascade: %s NOT cleared (%s)", label, type(exc).__name__)
    if res is not None:
        res.failed.append(label)
    return 0


async def _clear(user_id: str, rx, mutes, res: CascadeResult, *, db=None, only: Optional[frozenset] = None,
                 recover_ids: bool = False) -> None:
    """One pass over the derived stores (or just the labels in ``only``). ``rx`` is a ``re.Pattern`` for the name or a
    ``memory_forgotten.LedgerPattern`` (the retry: no name); ``mutes`` is the restraint-mutes eraser for the same."""
    def want(label: str) -> bool:
        return only is None or label in only

    db_only = None if (only is None or "database" in only) else frozenset(only.intersection(DB_STEPS))

    async def database() -> int:
        if db is not None:
            await _run(db, user_id, rx, res, only=db_only, recover_ids=recover_ids)
        else:
            from db_pool import get_db_ctx  # type: ignore[import]
            async with get_db_ctx() as conn:
                await _run(conn, user_id, rx, res, only=db_only, recover_ids=recover_ids)
        return 0

    # _run records each store's own failure; this label is the connection itself (pool lost / exhausted / never there)
    if only is None or "database" in only or db_only:
        await _step("database", database, res)
    # the stores reached through their own modules: restraint mutes, commitments, pendant transcripts. strict=True so a failure
    # raises to the cascade (their default is swallow-and-log, which is what hid this).
    async def commitments_() -> int:
        import commitments

        return await commitments.erase_entity(user_id, rx, strict=True)

    async def omi() -> int:
        import omi_recordings

        return await omi_recordings.erase_entity(user_id, rx, strict=True)

    async def omi_review() -> int:
        import omi_day_review

        return await omi_day_review.erase_entity(user_id, rx, strict=True)

    async def text_logs() -> int:
        import local_text_logs

        # the local JSONL logs that keep utterance text (router shadow log, intent misses): a record naming the entity is dropped
        return await asyncio.to_thread(local_text_logs.erase_entity, rx)

    if want("restraint_mutes"):
        await _step("restraint_mutes", mutes, res)
    if want("commitments"):
        await _step("commitments", commitments_, res)
    if want("omi_recordings"):
        await _step("omi_recordings", omi, res)
    if want("omi_day_review"):
        await _step("omi_day_review", omi_review, res)
    if want("text_logs"):
        await _step("text_logs", text_logs, res)


async def cascade_forget(user_id: str, name: str, *, db=None) -> CascadeResult:
    """Clear everything derived from ``name`` for ``user_id`` and record the contact undo. Never raises; a store it could not
    clear is in ``CascadeResult.failed`` (the caller must not report the forget as complete then) AND left as a pending marker in the
    forget ledger (no name) so ``retry_pending`` finishes it on the next healthy pass. A complete cascade also finishes whatever an
    EARLIER forget left pending for this member."""
    res = CascadeResult()
    if not user_id or not (name or "").strip():
        return res
    rx = _name_re(name)

    async def mutes() -> int:
        import restraint

        return await restraint.erase_entity(user_id, name, strict=True)

    await _clear(user_id, rx, mutes, res, db=db)
    if res.failed:
        logger.error("FORGET_CASCADE_INCOMPLETE user=%s steps=%s - the forget is not confirmed", user_id, ",".join(res.failed))
        await _mark_pending(user_id, res.failed)
    logger.info("memory_forget_cascade: user=%s portrait=%d card=%d loops=%d candidates=%d people=%d edges=%d contacts=%d "
                "voiceprints=%d attendees=%d", user_id, res.portrait, res.card, res.loops, res.candidates, res.people, res.edges,
                res.contacts, res.voiceprints, res.attendees)
    if res.complete:
        await retry_pending(user_id, db=db)         # an earlier forget's unfinished steps, now that the database answers
    return res


async def _mark_pending(user_id: str, labels) -> bool:
    """Leave the durable retry marker for ``labels`` (never the name). Never raises."""
    try:
        import memory_forgotten
        ok = await memory_forgotten.mark_pending(user_id, labels)
    except Exception as exc:  # noqa: BLE001
        logger.error("FORGET_CASCADE_RETRY_NOT_RECORDED user=%s (%s)", user_id, type(exc).__name__)
        return False
    if not ok:
        logger.error("FORGET_CASCADE_RETRY_NOT_RECORDED user=%s - no durable marker (ledger off or its table unreachable); "
                     "only the member saying the forget again will redo it", user_id)
    return ok


async def retry_pending(user_id: str, *, db=None) -> Optional[CascadeResult]:
    """Finish the cascade steps an earlier forget left pending for ``user_id``, from the forget ledger's hashes alone (no name was
    ever stored). Re-runs only the steps that have a marker, deletes the markers of the ones that succeeded, leaves (and adds) the
    markers of the ones that did not. None when nothing is pending. Idempotent and safe to call from any healthy pass; never raises.

    Does NOT record a contact undo and never touches contacts of a step that was not pending: a member who said "keep the
    contact" after a COMPLETE forget keeps it."""
    try:
        import memory_forgotten
        labels = await memory_forgotten.pending_labels(user_id, ALL_LABELS)
        if not labels:
            return None
        rx = await memory_forgotten.ledger_pattern(user_id)
        if rx is None:       # nothing forgotten any more (every entity was explicitly re-taught): there is nothing left to clear
            await memory_forgotten.clear_pending(user_id, labels)
            return CascadeResult()
        res = CascadeResult()

        async def mutes() -> int:
            import restraint

            return await restraint.erase_matching(user_id, lambda topic_key: bool(rx.search(topic_key)), strict=True)

        run = set(labels)
        if "people" in run:
            run.update(("edges", "calendar_attendees"))             # edges depends on people: never trust the marker set to carry both (an older build wrote one)
        await _clear(user_id, rx, mutes, res, db=db, only=frozenset(run), recover_ids=True)
        failed = set(res.failed)
        db_failed = bool(failed.intersection(DB_LABELS))
        done = []
        for label in labels:
            if label == "database":
                ok = not db_failed
            elif label in DB_STEPS:
                ok = label not in failed and "database" not in failed
            else:
                ok = label not in failed
            if ok:
                done.append(label)
        if done:
            await memory_forgotten.clear_pending(user_id, done)
        if failed:
            await _mark_pending(user_id, sorted(failed))
        logger.info("FORGET_CASCADE_RETRY user=%s pending=%s cleared=%s still_pending=%s rows=%d", user_id, ",".join(labels),
                    ",".join(done), ",".join(sorted(failed)), res.summary_cleared + res.people)
        return res
    except Exception as exc:  # noqa: BLE001 - a retry pass never breaks the pass that called it
        logger.error("FORGET_CASCADE_RETRY failed user=%s (%s)", user_id, type(exc).__name__)
        return None


async def _run(db, user_id: str, rx, res: CascadeResult, *, only: Optional[frozenset] = None,
               recover_ids: bool = False) -> None:
    """The database stores. ``only`` limits it to those step labels (the retry); ``recover_ids`` lets the edges step find the
    people a previous pass already soft-deleted (their ids were only in that pass memory)."""
    async def portrait() -> int:
        rows = await _rows(db, "SELECT portrait_text FROM user_portraits WHERE user_id = ?", (user_id,))
        if rows and _names(rx, rows[0][0]):
            await db.execute("DELETE FROM user_portraits WHERE user_id = ?", (user_id,))
            await db.commit()
            return 1
        return 0

    async def card() -> int:
        rows = await _rows(db, "SELECT card_text, card_json FROM user_model_cards WHERE user_id = ?", (user_id,))
        if rows and _names(rx, rows[0][0], rows[0][1]):
            await db.execute("DELETE FROM user_model_cards WHERE user_id = ?", (user_id,))
            await db.commit()
            return 1
        return 0

    async def loops() -> int:
        rows = await _rows(
            db, "SELECT id, loop_text, context, follow_up_hint FROM open_loops "
                "WHERE user_id = ? AND resolved IS NOT TRUE", (user_id,))
        n = 0
        for r in rows:
            if _names(rx, r[1], r[2], r[3]):
                await db.execute(
                    "UPDATE open_loops SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (r[0],))
                n += 1
        if n:
            await db.commit()
        return n

    async def candidates() -> int:
        rows = await _rows(db, "SELECT id, text, hint, cue_words FROM proactive_candidates WHERE user_id = ?",
                           (user_id,))
        n = 0
        for r in rows:
            if _names(rx, r[1], r[2], r[3]):
                await db.execute("DELETE FROM proactive_candidates WHERE id = ?", (r[0],))
                n += 1
        if n:
            await db.commit()
        return n

    async def people() -> int:
        rows = await _rows(
            db, "SELECT id, name FROM people WHERE user_id = ? AND (deleted = 0 OR deleted IS NULL)", (user_id,))
        now = _now_iso()
        ids = [str(r[0]) for r in rows if _names(rx, r[1])]
        for pid in ids:
            await db.execute("UPDATE people SET deleted = 1, updated_at = ? WHERE id = ? AND user_id = ?",
                             (now, pid, user_id))
        if ids:
            await db.commit()
            res.person_ids = ids
        return len(ids)

    async def edges() -> int:
        ids = list(res.person_ids)
        if not ids and recover_ids:
            ids = [str(r[0]) for r in await _rows(db, "SELECT id, name FROM people WHERE user_id = ? AND deleted = 1", (user_id,))
                   if _names(rx, r[1])]
        if not ids:
            return 0
        closed_at = _now_iso()
        n = 0
        for pid in ids:
            cur = await db.execute(
                "UPDATE person_relationships SET valid_to = ?, updated_at = ? "
                "WHERE user_id = ? AND valid_to IS NULL AND (person_a_id = ? OR person_b_id = ?)",
                (closed_at, closed_at, user_id, pid, pid))
            n += int(getattr(cur, "rowcount", 0) or 0)
        await db.commit()
        res.closed_at = closed_at
        return n

    async def contacts() -> int:
        # independent of the people step: it finds the person rows itself (live or already soft-deleted), so a people failure
        # never makes it "succeed" over nothing
        import contacts_store

        return await contacts_store.forget_entity(db, user_id, rx)

    async def voiceprints() -> int:
        import contacts_store

        return await contacts_store.forget_voiceprints(db, user_id, rx)

    async def attendees() -> int:
        ids = set(res.person_ids)
        if not ids and recover_ids:
            ids = {str(r[0]) for r in await _rows(db, "SELECT id, name FROM people WHERE user_id = ? AND deleted = 1", (user_id,))
                   if _names(rx, r[1])}
        now = _now_iso()
        n = 0
        for r in await _rows(db, "SELECT id, person_id, display_name, email FROM event_attendees WHERE user_id = ?", (user_id,)):
            if (r[1] is not None and str(r[1]) in ids) or _names(rx, r[2], r[3]):
                await db.execute(
                    "UPDATE event_attendees SET person_id = NULL, person_link_source = NULL, display_name = NULL, "
                    "email = NULL, updated_at = ? WHERE id = ? AND user_id = ?", (now, r[0], user_id))
                n += 1
        # the desktop calendar's legacy attendee list inside events.metadata (JSON text)
        for r in await _rows(db, "SELECT id, metadata FROM events WHERE user_id = ? AND metadata LIKE ?", (user_id, "%attendees%")):
            try:
                meta = json.loads(r[1]) if r[1] else None
            except (TypeError, ValueError):
                continue
            listed = meta.get("attendees") if isinstance(meta, dict) else None
            if not isinstance(listed, list):
                continue
            keep = [a for a in listed if not (isinstance(a, dict) and (
                (a.get("person_id") is not None and str(a.get("person_id")) in ids)
                or _names(rx, a.get("name"), a.get("display_name"), a.get("email"))))]
            if len(keep) != len(listed):
                meta["attendees"] = keep
                await db.execute("UPDATE events SET metadata = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                                 (json.dumps(meta), now, r[0], user_id))
                n += len(listed) - len(keep)
        if n:
            await db.commit()
        return n

    def want(label: str) -> bool:
        return only is None or label in only

    if want("portrait"):
        res.portrait = await _step("portrait", portrait, res)
    if want("card"):
        res.card = await _step("card", card, res)
    if want("open_loops"):
        res.loops = await _step("open_loops", loops, res)
    if want("proactive_candidates"):
        res.candidates = await _step("proactive_candidates", candidates, res)
    if want("people"):
        res.people = await _step("people", people, res)
    if want("edges"):
        res.edges = await _step("edges", edges, res)
    if want("contacts"):
        res.contacts = await _step("contacts", contacts, res)
    if want("voiceprints"):
        res.voiceprints = await _step("voiceprints", voiceprints, res)
    if want("calendar_attendees"):
        res.attendees = await _step("calendar_attendees", attendees, res)
    if "people" in res.failed and "edges" not in res.failed:
        # edges closes the relationships of the people the people step resolved; with that step failed there were no ids, so edges
        # "succeeded" over nothing. It is pending together with its parent, or the contact's open edges would stay open for good.
        res.failed.append("edges")
    if "people" in res.failed and want("calendar_attendees") and "calendar_attendees" not in res.failed:
        # the attendee step unlinks the people ids the people step resolved: same dependency as edges
        res.failed.append("calendar_attendees")
    if res.person_ids and only is None:       # a retry never arms the contact undo
        _last_cascade[user_id] = (time.monotonic(), list(res.person_ids), res.closed_at)


def pending_undo(user_id: str) -> bool:
    rec = _last_cascade.get(user_id)
    return bool(rec and time.monotonic() - rec[0] <= UNDO_WINDOW_S)


async def restore_contacts(user_id: str, *, db=None) -> Optional[int]:
    """Put back the contacts (and their edges) the last forget cascade removed. The number of people restored,
    0 when there is nothing to undo (none, expired, or already restored), None when the database was
    unreachable (nothing changed, the record is kept so the person can try again)."""
    rec = _last_cascade.get(user_id)
    if not rec or time.monotonic() - rec[0] > UNDO_WINDOW_S:
        _last_cascade.pop(user_id, None)
        return 0
    _t, ids, closed_at = rec
    now = _now_iso()

    async def go(conn) -> int:
        n = 0
        for pid in ids:
            await conn.execute("UPDATE people SET deleted = 0, updated_at = ? WHERE id = ? AND user_id = ?",
                               (now, pid, user_id))
            n += 1
            if closed_at:
                try:
                    await conn.execute(
                        "UPDATE person_relationships SET valid_to = NULL, updated_at = ? "
                        "WHERE user_id = ? AND valid_to = ? AND (person_a_id = ? OR person_b_id = ?)",
                        (now, user_id, closed_at, pid, pid))
                except Exception as exc:  # noqa: BLE001 - the contact matters more than its edges
                    logger.warning("memory_forget_cascade: edge restore failed (%s)", type(exc).__name__)
        await conn.commit()
        return n

    try:
        if db is not None:
            restored = await go(db)
        else:
            from db_pool import get_db_ctx  # type: ignore[import]
            async with get_db_ctx() as conn:
                restored = await go(conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_forget_cascade: restore failed (%s)", type(exc).__name__)
        return None
    _last_cascade.pop(user_id, None)
    return restored


def reset_state() -> None:
    _last_cascade.clear()
