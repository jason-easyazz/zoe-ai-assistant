"""member_owned_forget - a member delete removes EVERYTHING of the member (owner decision 2026-11-15: "deleting a member should delete
everything").

``MemoryService.delete_user`` already erased the palace, the derived stores (``member_derived_forget``) and every store with its own
eraser. This module erases the rest: the transcript, what the member wrote (notes, journal, reminders, transactions), their queued and
scheduled sends, their ambient recordings, their own contacts and their entries on shared ones, their personal events, and the
smaller per-member tables. The per-store rule, in plain words, is in docs/knowledge/forget-reach.md ("What a member delete removes").

THE SHARED-OBJECT RULE. A row the member wrote alone (a note, a reminder, a transaction, a journal entry) goes, whatever its
visibility. A household-SHARED container (a family list, a family event, a family-visible contact) is NOT deleted: only the entries
inside it that record the member as their author go, and the member's own rows elsewhere. Where a row does not record its author
(``list_items``, ``people_field_values``) nothing is guessed: it stays, and forget-reach lists it as an authorship gap with the column
that would be needed. A shared chat session never keeps the member as its owner, and its AG-UI runs stay only when shown to be another
member's (``_hand_over``, ``_shared_runs``).

Fail closed like the other erasers: a store that cannot be cleared RAISES (the caller retries the whole delete); a table that is not
migrated, or a process that never had a database pool, holds nothing. Scheduled sends are consumed BEFORE their rows go (a job whose row
is gone claims nothing and never fires) and their scheduler jobs cancelled where this process runs the scheduler. Takes no erase gate:
it runs before the memory lock, like ``member_derived_forget``. Logs counts only.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: single-author tables: every row whose ``user_id`` is the member goes (children before parents where they reference each other)
OWNED = ("chat_feedback", "background_tasks", "ui_actions", "notes", "journal_entries", "reminders", "transactions",
         "voice_announcements", "openclaw_approvals", "openclaw_run_state", "memory_consolidation_state",
         "person_activities", "person_bucket_list", "person_gift_ideas", "person_important_dates", "person_relationships",
         "event_attendees",
         # the member's DEVICE LINKS: their browsers' web-push endpoints (a later send finds no endpoint), and every panel row the
         # member is bound to (ui_panel_sessions: one row per panel, user_id = who it is signed in / verified as; the voice path trusts
         # it for 15 min, and its ui_context / chat_session_id are the member's). A guest / kiosk row names no member and stays.
         "push_subscriptions", "ui_panel_sessions")
#: tables whose ``person_id`` is a plain ``REFERENCES people(id)`` (alembic 0006, no ON DELETE): another member's row pointing at one of the
#: member's private contacts keeps the row and loses the reference
NULLED = ("notes", "journal_entries")
_CHUNK = 200
_GUESTS = ("guest", "kiosk", "unknown", "")


def _missing_table(exc: BaseException) -> bool:
    s = str(exc)
    return "no such table" in s or ("does not exist" in s and "relation" in s)


def _no_pool(exc: BaseException) -> bool:
    if isinstance(exc, RuntimeError) and re.match(r"(?:db[_ ])?pool not initialised", str(exc)):
        from db_pool import pool_ever_initialised

        return not pool_ever_initialised()
    return False


def _db():
    from db_compat import get_compat_db
    return get_compat_db()


@contextlib.asynccontextmanager
async def _nothing():
    yield


def _tx_of(db):
    """The connection's own transaction factory (the asyncpg pool connection, through ``AsyncpgCompat``), or None (aiosqlite: one
    implicit transaction until ``commit``)."""
    tx = getattr(db, "transaction", None)
    return tx if callable(tx) else None


@contextlib.asynccontextmanager
async def _atomic(db):
    """ONE transaction for every erase step: a failure anywhere rolls ALL of them back, so the retry still finds every link (a run-state
    row, a session owner, a turn's metadata) that leads it to the rows it has to erase. Postgres: a real transaction (each statement
    of ``_Run`` then runs in its own savepoint, so a not-migrated table does not abort it). aiosqlite: the implicit transaction, rolled
    back here on failure."""
    tx = _tx_of(db)
    if tx is not None:
        async with tx():
            yield
        return
    try:
        yield
    except BaseException:
        await db.rollback()
        raise


class _Run:
    def __init__(self, db):
        self.db, self.removed, self.order = db, 0, []      # ``order``: the tables DELETEd from, in the order the statements ran
        self.savepoints = False                              # inside ``_atomic`` on a connection with transactions: True

    def _sp(self):
        tx = _tx_of(self.db) if self.savepoints else None
        return tx() if tx is not None else _nothing()

    async def rows(self, sql: str, params: tuple) -> list:
        try:
            async with self._sp():
                cur = await self.db.execute(sql, params)
                return list(await cur.fetchall())
        except Exception as exc:  # noqa: BLE001
            if _missing_table(exc):
                return []
            raise

    async def run(self, sql: str, params: tuple) -> int:
        m = re.match(r"\s*DELETE FROM (\w+)", sql)
        if m:
            self.order.append(m.group(1))
        try:
            async with self._sp():
                cur = await self.db.execute(sql, params)
        except Exception as exc:  # noqa: BLE001
            if _missing_table(exc):
                return 0
            raise
        n = int(getattr(cur, "rowcount", 0) or 0)
        self.removed += max(n, 0)
        return n

    async def by_ids(self, sql_prefix: str, ids: list) -> None:
        """``DELETE ... WHERE <col> IN (ids)`` in chunks (``sql_prefix`` ends with the column name)."""
        for at in range(0, len(ids), _CHUNK):
            part = ids[at:at + _CHUNK]
            await self.run(f"{sql_prefix} IN ({', '.join('?' * len(part))})", tuple(part))


def _turn_owner(metadata: Any, session_owner: Any) -> str:
    """The owner of one chat turn: the per-turn ``metadata.user_id`` when it names one, else the session owner (the ownership rule of
    ``user_filters.message_owner_expr``, in Python so it is the same on every database)."""
    meta = metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta.strip().startswith("{") else {}
        except ValueError:
            meta = {}
    uid = str((meta or {}).get("user_id") or "").strip() if isinstance(meta, dict) else ""
    return uid or str(session_owner or "").strip()


#: candidate turns of a member: the sessions they own, and every row whose metadata TEXT contains the bare id (a prefilter that cannot miss,
#: whatever the JSON spacing or key order: a member id is ASCII, and its JSON-escaped form is matched too); ``_turn_owner`` then decides
#: each row. ``metadata`` is a TEXT column (alembic 0001); the CAST keeps the comparison text on any database. No literal question marks
#: beyond the binds (the asyncpg positional-compat layer counts them).
TRANSCRIPT_SQL = ("SELECT cm.id, cm.session_id, cm.metadata, cs.user_id FROM chat_messages cm "
                  "LEFT JOIN chat_sessions cs ON cs.id = cm.session_id "
                  "WHERE cs.user_id = ? OR CAST(cm.metadata AS TEXT) LIKE ? OR CAST(cm.metadata AS TEXT) LIKE ?")


def _id_patterns(user_id: str) -> tuple:
    escaped = json.dumps(str(user_id))[1:-1]
    return (f"%{user_id}%", f"%{escaped}%")


#: how long a member delete waits for a send that is mid-delivery (claimed, not yet fired) before it fails closed (and is retried)
INFLIGHT_WAIT_S = 30.0
_POLL_S = 0.1


async def _await_inflight_fires(r: _Run, user_id: str) -> None:
    """Wait until none of the member's scheduled sends is mid-delivery. A fire CLAIMS its row (``claimed_at``) before its erase check and
    holds the claim until it has delivered (``fired = 1``) or released it; the erase mark is already written, so any fire that checks
    from now on refuses, and so does the last step of its send (``erase_watermark.send_refused``). Waiting here closes the last window: a
    fire that checked BEFORE the mark delivers before this delete can finish, never after it.

    A claim of ANY age counts as live (a slow delivery can outlast the stuck-claim timeout) unless it is PROVEN dead
    (``reminders.claim_is_dead``: this process runs the scheduler and is not delivering that row - a claim records no owner, so only the
    process that fires reminders can prove one dead). Unproven claims are waited for at most ``INFLIGHT_WAIT_S``, then the delete FAILS
    closed and is retried; a crashed claim seen from a process without the scheduler keeps failing the delete until the stuck-claim
    recovery re-fires its row (which then refuses at the erase check and consumes it)."""
    import asyncio
    import time

    from proactive.triggers import reminders

    deadline = time.monotonic() + INFLIGHT_WAIT_S
    while True:
        rows = await r.rows("SELECT id FROM proactive_scheduled WHERE user_id = ? AND fired = 0 AND claimed_at IS NOT NULL", (user_id,))
        if not [x for x in rows if not reminders.claim_is_dead(str(x[0]))]:
            return
        if time.monotonic() > deadline:
            raise RuntimeError("member_owned_forget: a scheduled send is still claimed and not proven dead")
        await asyncio.sleep(_POLL_S)


async def _scheduled_sends(r: _Run, user_id: str) -> None:
    """The member's scheduled reminder sends: consumed first (a job that fires finds no claimable row), jobs cancelled where this
    process runs the scheduler, then the rows deleted."""
    await _await_inflight_fires(r, user_id)
    jobs = [str(x[0]) for x in await r.rows("SELECT apscheduler_job_id FROM proactive_scheduled WHERE user_id = ?", (user_id,)) if x[0]]
    await r.run("UPDATE proactive_scheduled SET fired = 1 WHERE user_id = ?", (user_id,))
    await r.db.commit()
    # A fire that ALREADY claimed a row is not stopped by fired=1; it is stopped at delivery time: _fire_reminder refuses a send whose
    # member has an erase mark at or after the row (written before this step). Cancel errors are NOT swallowed (fail closed: the
    # delete is retried); only "this process runs no scheduler" is not an error - the jobstore is shared, and the consumed row cannot fire.
    from proactive.scheduler import cancel_job
    for job in jobs:
        try:
            cancel_job(job)
        except RuntimeError as exc:
            if "not started" not in str(exc):
                raise
    await r.run("DELETE FROM proactive_scheduled WHERE user_id = ?", (user_id,))


async def _transcript(r: _Run, user_id: str) -> None:
    """The member's chat turns (any session, by the per-turn owner rule); then the sessions they own that are now empty (with their
    AG-UI runs); a session they own that still holds someone else's turns keeps those turns, loses its title (the member's words) and
    is handed to a remaining participant (``_hand_over``); and in every surviving session they owned or spoke in, the AG-UI runs not
    shown to be another member's (``_shared_runs``). No surviving chat row names the member."""
    # FIRST, while the evidence exists: every AG-UI run the member STARTED, in ANY session (another member's too, even one holding no turn
    # of theirs). ``openclaw_run_state`` is the only link from a run to its starter, and it is deleted later with the OWNED rows.
    started = sorted({str(x[0]) for x in await r.rows("SELECT id FROM openclaw_run_state WHERE user_id = ?", (user_id,)) if x[0]})
    await r.by_ids("DELETE FROM chat_ag_ui_runs WHERE run_id", started)
    rows = await r.rows(TRANSCRIPT_SQL, (user_id, *_id_patterns(user_id)))
    mine = [str(x[0]) for x in rows if _turn_owner(x[2], x[3]) == user_id]
    touched = sorted({str(x[1]) for x in rows if _turn_owner(x[2], x[3]) == user_id and x[1]})
    await r.by_ids("DELETE FROM chat_messages WHERE id", mine)
    owned = [str(x[0]) for x in await r.rows("SELECT id FROM chat_sessions WHERE user_id = ?", (user_id,))]
    candidates = sorted(set(owned) | set(touched))
    empty, shared = [], []
    for at in range(0, len(candidates), _CHUNK):
        part = candidates[at:at + _CHUNK]
        marks = ", ".join("?" * len(part))
        left = {str(x[0]) for x in await r.rows(f"SELECT DISTINCT session_id FROM chat_messages WHERE session_id IN ({marks})", tuple(part))}
        for sid in part:
            (shared if sid in left else empty).append(sid)
    # an emptied session goes when the member owns it or nobody does (a guest / kiosk session holding only their turns)
    sess_owner = {}
    for at in range(0, len(empty), _CHUNK):
        part = empty[at:at + _CHUNK]
        for x in await r.rows(f"SELECT id, user_id FROM chat_sessions WHERE id IN ({', '.join('?' * len(part))})", tuple(part)):
            sess_owner[str(x[0])] = str(x[1] or "")
    gone = [s for s in empty if sess_owner.get(s, "") in (user_id, *_GUESTS) or s not in sess_owner]
    # a shared session the member owned is handed to a remaining participant, or goes when none is left (``_hand_over``); then the runs
    # of every surviving session they owned or spoke in; then the gone sessions (children before parents: the FK-order test)
    orphans = [sid for sid in shared if sid in set(owned) and not await _hand_over(r, user_id, sid)]
    await r.by_ids("DELETE FROM chat_messages WHERE session_id", orphans)
    await _shared_runs(r, user_id, [sid for sid in shared if sid not in set(orphans)])
    await r.by_ids("DELETE FROM chat_ag_ui_runs WHERE session_id", gone + orphans)
    await r.by_ids("DELETE FROM chat_sessions WHERE id", gone + orphans)


async def _hand_over(r: _Run, user_id: str, sid: str) -> bool:
    """A session the member owned that still holds another member's turns must not keep the member as its owner. Every turn left names
    its own owner (a turn without ``metadata.user_id`` was the session owner's, the member's, and is gone), so the session goes to the
    participant who spoke FIRST among the turns that stay, a household member before a guest / kiosk id, and only an id that is a user
    row (the ``chat_sessions.user_id`` foreign key). Its title (the member's words) is reset. False when no such participant is left:
    the caller deletes the session with what it holds (those turns name nobody who is still a user)."""
    turns = await r.rows("SELECT metadata, created_at, id FROM chat_messages WHERE session_id = ?", (sid,))
    owners = {}
    for meta, at, mid in turns:
        o = _turn_owner(meta, "")
        if o and o != user_id:
            key = (o.lower() in _GUESTS, str(at or ""), str(mid))
            owners[o] = min(owners.get(o, key), key)
    users = await _existing_users(r, list(owners))
    ranked = sorted((k, o) for o, k in owners.items() if users is None or o in users)
    if not ranked:
        return False
    await r.run("UPDATE chat_sessions SET user_id = ?, title = 'New Chat' WHERE id = ?", (ranked[0][1], sid))
    return True


async def _existing_users(r: _Run, ids: list):
    """The ids among ``ids`` that are rows of ``users``; None when there is no ``users`` table (a lab store: every id counts)."""
    if not ids:
        return set()
    try:
        async with r._sp():
            cur = await r.db.execute(f"SELECT id FROM users WHERE id IN ({', '.join('?' * len(ids))})", tuple(ids))
            return {str(x[0]) for x in await cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        if _missing_table(exc):
            return None
        raise


async def _shared_runs(r: _Run, user_id: str, sessions: list) -> None:
    """The AG-UI runs of every surviving session the member owned or spoke in. A run records its session and its run id, not a speaker;
    the run's starter is the ``openclaw_run_state`` row of the same id (``chat._record_run_state``: written at the run's start with the
    requesting member). A run is KEPT only when that row names ANOTHER member; a run the member started, or one nobody can be shown to
    have started, goes (its events hold the reply text, derived from the turns it answered). The member's own ``openclaw_run_state``
    rows go afterwards with the ``OWNED`` rows."""
    for at in range(0, len(sessions), _CHUNK):
        part = sessions[at:at + _CHUNK]
        runs = await r.rows(f"SELECT id, run_id FROM chat_ag_ui_runs WHERE session_id IN ({', '.join('?' * len(part))})", tuple(part))
        if not runs:
            continue
        run_ids = sorted({str(x[1]) for x in runs if x[1]})
        starters = {}
        for j in range(0, len(run_ids), _CHUNK):
            chunk = run_ids[j:j + _CHUNK]
            for x in await r.rows(f"SELECT id, user_id FROM openclaw_run_state WHERE id IN ({', '.join('?' * len(chunk))})", tuple(chunk)):
                starters[str(x[0])] = str(x[1] or "").strip()
        gone = [str(x[0]) for x in runs if starters.get(str(x[1] or ""), "") in ("", user_id)]
        await r.by_ids("DELETE FROM chat_ag_ui_runs WHERE id", gone)


async def _lists(r: _Run, user_id: str) -> None:
    """The member's PRIVATE lists and their items go; a family list keeps its items (they record no author); the member's
    assignments on any item are cleared."""
    mine = [str(x[0]) for x in await r.rows("SELECT id FROM lists WHERE user_id = ? AND COALESCE(visibility, 'family') <> 'family'",
                                            (user_id,))]
    await r.by_ids("DELETE FROM list_items WHERE list_id", mine)
    await r.by_ids("DELETE FROM lists WHERE id", mine)
    await r.run("UPDATE list_items SET assigned_to = NULL WHERE assigned_to = ?", (user_id,))


async def _people(r: _Run, user_id: str) -> None:
    """The member's PRIVATE contacts go with every row about them; a family-visible contact stays (other members know them) and
    loses only the member's own entries (the ``OWNED`` person_* rows and relationships, by ``user_id``)."""
    mine = [str(x[0]) for x in await r.rows("SELECT id FROM people WHERE user_id = ? AND COALESCE(visibility, 'family') <> 'family'",
                                            (user_id,))]
    # ANOTHER member's note / journal entry pointing at one of these contacts (a plain REFERENCES people(id), no ON DELETE): the row is
    # theirs, so only the reference goes. The member's own notes and journal are already deleted (OWNED runs first).
    for t in NULLED:
        for at in range(0, len(mine), _CHUNK):
            part = mine[at:at + _CHUNK]
            await r.run(f"UPDATE {t} SET person_id = NULL WHERE person_id IN ({', '.join('?' * len(part))})", tuple(part))
    for t in ("people_field_values", "person_activities", "person_bucket_list", "person_gift_ideas", "person_important_dates"):
        await r.by_ids(f"DELETE FROM {t} WHERE person_id", mine)
    await r.by_ids("DELETE FROM person_relationships WHERE person_a_id", mine)
    await r.by_ids("DELETE FROM person_relationships WHERE person_b_id", mine)
    await r.by_ids("DELETE FROM people WHERE id", mine)


async def _events(r: _Run, user_id: str) -> None:
    """The member's PERSONAL local events go with their attendee rows; a family event stays (a household calendar entry)."""
    mine = [str(x[0]) for x in await r.rows("SELECT id FROM events WHERE user_id = ? AND COALESCE(visibility, 'family') <> 'family'",
                                            (user_id,))]
    await r.by_ids("DELETE FROM event_attendees WHERE event_id", mine)
    await r.by_ids("DELETE FROM events WHERE id", mine)


async def _ambient(r: _Run, user_id: str) -> None:
    """Ambient recordings: rows scoped to the member (``user_id``, every row since migration 0017), and legacy rows whose
    ``speaker_id`` is one of the member's speaker profiles."""
    await r.run("DELETE FROM ambient_memory WHERE user_id = ?", (user_id,))
    profiles = [str(x[0]) for x in await r.rows("SELECT id FROM speaker_profiles WHERE user_id = ?", (user_id,))]
    await r.by_ids("DELETE FROM ambient_memory WHERE speaker_id", profiles)


async def _panel_voices(r: _Run, user_id: str) -> None:
    clusters = [str(x[0]) for x in await r.rows("SELECT id FROM panel_voice_clusters WHERE user_id = ?", (user_id,))]
    await r.by_ids("DELETE FROM panel_voice_samples WHERE cluster_id", clusters)
    await r.by_ids("DELETE FROM panel_voice_clusters WHERE id", clusters)


async def _biometrics(r: _Run, user_id: str) -> None:
    """The member's voice and face embeddings (``speaker_profiles``, with any adapted vector on the same row; ``face_profiles``)."""
    await r.run("DELETE FROM speaker_profiles WHERE user_id = ?", (user_id,))
    await r.run("DELETE FROM face_profiles WHERE user_id = ?", (user_id,))


async def _after_commit(user_id: str) -> None:
    """After the rows are gone: the member's labelled clips in the voice-sample corpus (by the clip's own verdict sidecar, never by a
    name pattern; RAISES on a clip that cannot be removed), and the panels told to drop the voice signature now (voice_enrollment: also
    closes any open voice session of the member; FAIL CLOSED - a queue failure raises; a panel that is offline gets the command when it
    reconnects (24 h) or drops the profile at its next hourly sync, and the server refuses a stale claim anyway: ``_voice_claim_consented``
    reads the store). Face copies on a panel wait for its next sync; the server refuses a stale face corroboration (speaker_adaptation)."""
    import voice_clips

    failure = None
    try:
        voice_clips.erase_member(user_id)
    except Exception as exc:  # noqa: BLE001 - the sessions and the panels below are still reached, then this is raised
        failure = exc
    try:
        await _push_forget_strict(user_id)
    except Exception as exc:  # noqa: BLE001
        failure = failure or exc
    if failure is not None:
        raise failure


async def _push_forget_strict(user_id: str) -> int:
    """Close the member's open voice sessions and queue ``voice_profile_forget`` for every panel holding a device token, both FAIL
    CLOSED: ``delete_user`` does not report success while the member's verified voice session still opens private reads, or a panel keeps
    the voice. Both steps are ATTEMPTED whatever the other did; then the first failure RAISES (the caller retries the whole delete; it is
    idempotent, and the retry re-queues). ``voice_session.revoke_member`` never raises, so its result is checked: a session of the member
    still open afterwards is the failure. No device panel at all is not a failure: there is nothing to purge.
    ``voice_enrollment.push_forget_to_panels`` stays best effort for its own callers (a profile delete that already happened)."""
    failure = None
    try:
        import voice_session

        await voice_session.revoke_member(user_id, "member_deleted")
        if voice_session.member_panels(user_id):
            raise RuntimeError("member_owned_forget: a voice session of the member is still open")
    except Exception as exc:  # noqa: BLE001 - attempted first; the panel purge below still runs, then this is raised
        failure = exc
    queued = 0
    try:
        queued = await _queue_panel_forget(user_id)
    except Exception as exc:  # noqa: BLE001
        failure = failure or exc
    if failure is not None:
        raise failure
    return queued


async def _queue_panel_forget(user_id: str) -> int:
    import voice_announce
    import voice_enrollment

    queued = 0
    async with _db() as db:
        try:
            panels = await voice_enrollment._device_panels(db)
        except Exception as exc:  # noqa: BLE001
            if _missing_table(exc):
                return 0                  # no device_tokens table: no panel was ever paired
            raise
        for panel in panels:
            await voice_announce.enqueue_command(db, user_id=user_id, panel_id=panel, trigger_type="voice_profile_forget", command={})
            queued += 1
        await db.commit()
    return queued


#: the last run's DELETE order (tests read it to check it against the migrations' foreign keys)
last_order: list = []


async def forget_user(user_id: str) -> int:
    """Erase every member-owned row the other erasers do not reach. Returns rows changed. RAISES on any failure but a missing table /
    no pool."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    try:
        async with _db() as db:
            r = _Run(db)
            # CHILD BEFORE PARENT (the migrations' foreign keys; pinned by test_member_delete_everything's FK-order test): the
            # single-author rows first (a note / journal entry / attendee / per-person row may point at a contact or event deleted below)
            await _scheduled_sends(r, uid)          # commits its consume (fired = 1) on its own: a fire must see it at once
            # Every LINK is read before the rows it leads to are deleted, and deleted after them, all in ONE transaction (``_atomic``):
            # run state -> runs (``_transcript``, first), session owner / turn metadata -> sessions and turns, private list -> items,
            # contact -> per-person rows, speaker profile -> legacy recordings, cluster -> samples.
            async with _atomic(db):
                r.savepoints = _tx_of(db) is not None
                # the queued notifications BEFORE the transcript: one claimed right now seeds a chat session, and that seed is either
                # removed by the transcript step below or undone by claim_pending's own post-check (session_utils)
                await r.run("DELETE FROM proactive_pending WHERE user_id = ?", (uid,))
                await _transcript(r, uid)
                for table in OWNED:
                    await r.run(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
                await _lists(r, uid)
                await _events(r, uid)           # its attendee rows reference people (SET NULL): events before contacts
                await _people(r, uid)
                await _ambient(r, uid)          # reads the member's speaker profiles: before they go
                await _panel_voices(r, uid)
                await _biometrics(r, uid)
                await db.commit()
            r.savepoints = False
            last_order[:] = r.order
    except Exception as exc:  # noqa: BLE001
        if _no_pool(exc):
            return 0
        logger.error("member_owned_forget: member-owned stores NOT erased (%s)", type(exc).__name__)
        try:   # the stores failed, but the member's ACCESS is still removed now (voice sessions, the panels' voice copies); the clip
            await _push_forget_strict(uid)   # files wait for the retry, which the raise below makes the caller run
        except Exception:  # noqa: BLE001 - the first failure (the stores) is the one reported
            pass
        raise
    await _after_commit(uid)
    if r.removed:
        logger.info("member_owned_forget: removed or cleared %d row(s)", r.removed)
    return r.removed
