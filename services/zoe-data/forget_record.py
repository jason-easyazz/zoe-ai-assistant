"""The write-ahead FORGET RECORD: one mechanism for every forget that takes more than one step (PR #2574, owner-approved redesign).

The bug class it ends. A forget is several steps (hide the rows, erase them for real, flag the saved transcript turn, erase the
exact-words entries, the night mind's observations, the conversation notes, the stored user-model card, the value-registry
entries, a topic's pending offers). Each step destroys what the NEXT attempt would have found its work by: a rejected row is no
longer listed, a flagged transcript turn no longer resolves the words, an erased entry no longer names its turn. So when step N
failed, the retry could not rediscover what steps 1..N-1 had removed, and the forget was confirmed (or abandoned) with something
left behind. Review found that shape at a new call site five rounds running; the sites were reordered one by one. This replaces
the reorderings and the per-site carriers (``turn_forget.note_pending_rows``, ``Explained.mids``).

THE CONTRACT
* RESOLVE FIRST. Before anything is destroyed or hidden, the entry point resolves its whole target set to IDS (``Plan``) and
  ``forget`` writes it down as ONE record. Nothing is destroyed until that write succeeded (a failed write = not confirmed, nothing
  done, the request can simply be repeated).
* WORK FROM THE RECORD. Each step (``STEPS``) reads only the record and marks itself done in it. A retry (the same request again:
  the same ``key``) loads the open record and runs only the steps not yet done: no rediscovery. By-id lookups a step makes (the
  rows bound to a saved-turn id, the copies of a transcript turn) need no words and are re-run at step time, so a late copy BOUND
  to the same turn is caught too.
* CONFIRM ONLY WHEN EVERY STEP IS DONE. Then the record is deleted. Steps run in ``STEPS`` order and every remaining step runs even
  after one fails (erase everything reachable, fail closed); the one ordering rule: the physical erase runs only once the hide held.
* IDS AND CODES ONLY, NEVER WORDS. The record holds row / saved-turn / entry / observation / note / registry / offer ids, the
  registry's content turn ids, the entry-point code, the actor's member id and a few counts - never text, an excerpt, a topic or
  a name (``_ids`` refuses anything with whitespace). A step that can only find its work by WORDS (a legacy row with no id, a topic)
  is resolved to the ids those words named AT RESOLUTION TIME. Anything that first appears after that is not this forget's: the
  turn forgets' write wall (``turn_forget.retract`` / ``blocks_write``, installed before resolution) refuses the forgotten turn's
  late capture, the entity ledger shields a forgotten name; anything else is new and needs a new forget (a closed record never
  blocks one).
* IT LIVES WHERE FORGET REACHES. ``app_settings`` rows ``forget_rec:<member>:<hash of the key>`` (the precedent:
  ``reminder_delivery``'s ledger): no migration; the member erase (``MemoryService.delete_user``) deletes the member's records
  (``forget_user``; ``forget_reach`` lists the store).

ATOMIC OR IDEMPOTENT. The steps write OTHER stores (the palace, other tables) through their own connections, so "do the work +
mark done" cannot be one transaction even on Postgres (and ``db_pool.AsyncpgCompat.commit`` is a no-op there: statements
autocommit). So EVERY STEP IS IDEMPOTENT - re-running a done step is harmless (a hidden row is skipped, an erased id erases
nothing) - and a crash between the work and the mark just re-runs it. The record's own writes are single statements (atomic on
Postgres and SQLite) and compare-and-set (``UPDATE ... WHERE value = <what was read>``), so two processes never both advance it.

ABANDONED RECORDS. ``resume_open`` finishes a member's open records; it runs from ``memory_forget_cascade.retry_pending`` (the
nightly digest's pre-read for every member, and every topic / entity forget), so no new background loop. A record NEVER expires:
a forget must not be silently dropped. Each attempt counts ``tries``; past ``STUCK_TRIES`` every attempt logs
``FORGET_RECORD code=stuck`` at ERROR (it is still retried). Bounded: a pass resumes at most ``MAX_RESUME`` records of the member,
each step does work proportional to the record's own ids.

CONCURRENCY. One record per (member, key): two requests for the same target share it (and an in-process lock serialises them); two
forgets of OVERLAPPING targets have two records whose steps are idempotent, so either order ends with both sets gone. A write
racing a forget is the shields' job, unchanged (``turn_forget.blocks_write``, ``memory_forgotten``): "forgotten means forever".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, fields
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

#: every entry point that forgets through a record (``tests/test_forget_record_matrix.py`` drives each; an AST check pins the list
#: to the ``forget(code=...)`` call sites)
ENTRY_POINTS = ("forget_last", "explained_row", "explained_turn", "memory_space_row", "guardian_row", "topic")
#: the steps, in run order (the matrix fails each)
STEPS = ("reject", "erase_rows", "transcript", "exact_words", "night_mind", "notes", "card", "registry", "offers")

PREFIX = "forget_rec:"
MAX_RESUME = 8
STUCK_TRIES = 10

#: per entry point: the review decision, its note, and the physical erase's reason (fixed codes, never household words)
_REVIEW = {
    "forget_last": ("reject", "forget_last", "forgotten by request (last)"),
    "explained_row": ("reject", "forget_explained", "forgotten by request (explained)"),
    "explained_turn": ("reject", "forget_explained", "forgotten by request (explained)"),
    "memory_space_row": ("reject", "forget_memory_space", "forgotten by request (memories screen)"),
    "guardian_row": ("reject", "guardian_forget", "forgotten by a guardian"),
    "topic": ("archive", "forget_topic", "forgotten by request (topic)"),
}
_ID_RE = re.compile(r"^[^\s]{1,200}$")


def _ids(values: Any) -> list:
    """Ids only: a value with whitespace (words) or over 200 chars is refused LOUDLY - the record must never hold text."""
    out = []
    for v in values or ():
        s = str(v)
        if not _ID_RE.match(s):
            raise ValueError("forget_record: not an id")
        if s not in out:
            out.append(s)
    return out


@dataclass
class Plan:
    """What a forget resolved to, before it destroyed anything: ids and codes only."""
    rows: list = field(default_factory=list)          # memory rows to hide and erase
    mids: list = field(default_factory=list)          # saved-turn (chat_messages) ids, every committed copy
    entries: list = field(default_factory=list)       # exact-words entry ids (legacy or a quoted entry: bound ones go by ``mids``)
    obs: list = field(default_factory=list)           # night-mind observation ids a legacy target's words named
    notes: list = field(default_factory=list)         # conversation-note ids a legacy target's words / time named
    reg: list = field(default_factory=list)           # value-registry entry ids (a topic's people)
    reg_turns: list = field(default_factory=list)     # the registry's content turn ids of the forgotten turns
    reg_topic: bool = False                           # topic rule for ``reg_turns`` (a row with no source turn goes too)
    offers: list = field(default_factory=list)        # pending-suggestion ids
    n: dict = field(default_factory=dict)             # counts the reply needs (ints)

    def __post_init__(self) -> None:
        for f in ("rows", "mids", "entries", "obs", "notes", "reg", "offers"):
            setattr(self, f, _ids(getattr(self, f)))
        # a content turn id may be None ("could not be computed": the registry then takes every row of the member)
        self.reg_turns = [None if t is None else _ids([t])[0] for t in (self.reg_turns or ())]
        self.n = {str(k): int(v) for k, v in (self.n or {}).items()}


@dataclass
class Record:
    user_id: str
    key: str                                          # the STORAGE key (``storage_key``: a hash of the logical key)
    code: str
    plan: Plan
    actor: str = ""
    origin: str = ""
    done: list = field(default_factory=list)
    tries: int = 0
    created: float = 0.0
    raw: str = ""                                     # the stored value this copy was read as (compare-and-set)

    def dump(self) -> str:
        p = {f.name: getattr(self.plan, f.name) for f in fields(Plan)}
        return json.dumps({"v": 1, "code": self.code, "actor": self.actor, "origin": self.origin, "plan": p,
                           "done": self.done, "tries": self.tries, "created": self.created}, sort_keys=True)

    @classmethod
    def load(cls, user_id: str, key: str, raw: str) -> "Record":
        d = json.loads(raw)
        return cls(user_id, key, str(d["code"]), Plan(**d.get("plan") or {}), str(d.get("actor") or ""),
                   str(d.get("origin") or ""), list(d.get("done") or []), int(d.get("tries") or 0),
                   float(d.get("created") or 0.0), raw)


@dataclass
class Outcome:
    started: bool = False        # a record exists (resolved and written); False = nothing was destroyed
    nothing: bool = False        # the target resolved to nothing to do
    complete: bool = False       # every step done: the forget may be confirmed
    resumed: bool = False        # an open record was finished (a retry): nothing was rediscovered
    failed: list = field(default_factory=list)       # steps that failed this attempt
    done: list = field(default_factory=list)         # steps done (this attempt or an earlier one)
    counts: dict = field(default_factory=dict)       # step -> what it removed THIS attempt
    plan: Optional[Plan] = None
    error: Optional[BaseException] = None


class Conflict(RuntimeError):
    """The stored record changed under us (another process advanced it, or the member erase removed it)."""


# ── storage ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def storage_key(user_id: str, key: str) -> str:
    return PREFIX + (user_id or "") + ":" + hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:24]


_BOOT_SECRET = hashlib.sha256(f"{time.time()}:{id(object())}".encode()).digest()


def words_key(user_id: str, kind: str, words: str) -> str:
    """A record key for a target only WORDS name (a topic, a quote with no entry id): a KEYED tag of them, never the words and
    never a bare hash (a short topic is reversed by guessing). Keyed with the forget-ledger secret (``memory_forgotten.said_tag``);
    with that unconfigured, with a per-process secret (a retry in this process finds the record; after a restart ``resume_open``
    still finishes it, the repeat just opens a fresh one)."""
    import hmac

    norm = " ".join((words or "").lower().split())
    try:
        import memory_forgotten
        tag = memory_forgotten.said_tag(user_id, norm)
    except Exception:  # noqa: BLE001
        tag = ""
    return f"{kind}:" + (tag or hmac.new(_BOOT_SECRET, f"{user_id}|{norm}".encode(), hashlib.sha256).hexdigest()[:32])


def _like(user_id: str) -> str:
    return (PREFIX + (user_id or "")).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ":%"


class MemoryBackend:
    """In-process store (tests, a lab process that never had a database). Same contract as ``SqlBackend``."""

    def __init__(self) -> None:
        self.rows: dict = {}

    async def get(self, skey):
        return self.rows.get(skey)

    async def create(self, skey, value):
        if skey in self.rows:
            return False
        self.rows[skey] = value
        return True

    async def cas(self, skey, old, new):
        if self.rows.get(skey) != old:
            return False
        self.rows[skey] = new
        return True

    async def delete(self, skey):
        self.rows.pop(skey, None)

    async def list(self, user_id):
        p = PREFIX + (user_id or "") + ":"
        return [(k, v) for k, v in self.rows.items() if k.startswith(p)]

    async def forget_user(self, user_id):
        gone = [k for k, _v in await self.list(user_id)]
        for k in gone:
            self.rows.pop(k, None)
        return len(gone)


class SqlBackend:
    """``app_settings`` rows over ``db_compat`` (portable ``?`` SQL: the tests run these statements on SQLite)."""

    @staticmethod
    def _db():
        from db_compat import get_compat_db
        return get_compat_db()

    async def get(self, skey):
        async with self._db() as db:
            rows = list(await (await db.execute("SELECT value FROM app_settings WHERE key = ?", (skey,))).fetchall())
        return str(rows[0][0]) if rows else None

    async def create(self, skey, value):
        async with self._db() as db:
            cur = await db.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO NOTHING",
                                   (skey, value, _iso()))
            await db.commit()
        return (getattr(cur, "rowcount", 1) or 0) > 0

    async def cas(self, skey, old, new):
        async with self._db() as db:
            cur = await db.execute("UPDATE app_settings SET value = ?, updated_at = ? WHERE key = ? AND value = ?", (new, _iso(), skey, old))
            await db.commit()
        return (getattr(cur, "rowcount", 0) or 0) > 0

    async def delete(self, skey):
        async with self._db() as db:
            await db.execute("DELETE FROM app_settings WHERE key = ?", (skey,))
            await db.commit()

    async def list(self, user_id):
        async with self._db() as db:
            rows = await (await db.execute("SELECT key, value FROM app_settings WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                                           (_like(user_id),))).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    async def forget_user(self, user_id):
        async with self._db() as db:
            cur = await db.execute("DELETE FROM app_settings WHERE key LIKE ? ESCAPE '\\'", (_like(user_id),))
            await db.commit()
        return int(getattr(cur, "rowcount", 0) or 0)


def _iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class _Store:
    """The configured backend, with ONE fallback: a process that never had a database (a lab / bare unit run) keeps its records
    in process. A process that had a pool and LOST it raises: the forget is then not started (nothing destroyed)."""

    def __init__(self, backend: Any) -> None:
        self.backend, self.local = backend, MemoryBackend()

    async def _call(self, name, *args):
        try:
            return await getattr(self.backend, name)(*args)
        except Exception as exc:  # noqa: BLE001
            from conversation_notes import _no_store

            if isinstance(self.backend, SqlBackend) and (_no_store(exc) or "app_settings" in str(exc) and "no such table" in str(exc)):
                return await getattr(self.local, name)(*args)
            raise

    def __getattr__(self, name):
        return lambda *a: self._call(name, *a)

    async def open_records(self, user_id: str) -> list:
        return [Record.load(user_id, k, v) for k, v in await self._call("list", user_id)]


_store: Optional[_Store] = None


def get_backend() -> _Store:
    global _store
    if _store is None:
        _store = _Store(SqlBackend())
    return _store


def set_backend(backend: Any = None) -> None:
    """Swap the store (tests / lab); ``None`` restores the SQL default."""
    global _store
    _store = _Store(backend if backend is not None else SqlBackend())
    _LOCKS.clear()


async def forget_user(user_id: str) -> int:
    """Member erase: every record of the member (ids only). RAISES on a store failure (the member erase fails closed)."""
    if not (user_id or "").strip():
        return 0
    return int(await get_backend().forget_user(user_id) or 0)


# ── the steps: each reads the record only, each idempotent ───────────────────────────────────────────────────────────

async def _reject(rec: Record, svc: Any) -> int:
    decision, note, _reason = _REVIEW[rec.code]
    failed = hidden = 0
    if rec.plan.rows and svc is None:
        svc = _default_svc()
    getter = getattr(svc, "get", None)                                # a store with no point read: every row is (re)reviewed
    for rid in rec.plan.rows:
        if getter is not None:
            ref = await getter(rid)
            if ref is None:
                continue                                              # erased already
            if str((ref.metadata or {}).get("status") or "approved").lower() in ("rejected", "archived"):
                continue                                              # hidden already (this record's earlier attempt, or before)
        try:
            kw = {"origin": rec.origin} if rec.origin else {}
            if await svc.review(rid, decision=decision, actor=rec.actor or rec.user_id, note=note, **kw) is not None:
                hidden += 1
                continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("FORGET_RECORD code=reject_failed (%s)", type(exc).__name__)
        failed += 1
    if failed:
        raise RuntimeError(f"{failed} row(s) not hidden")
    return hidden


async def _erase_rows(rec: Record, svc: Any) -> int:
    if rec.plan.rows and svc is None:
        svc = _default_svc()
    if not rec.plan.rows or not hasattr(svc, "erase_rows"):
        return 0
    from memory_service import physical_erase_enabled

    if not physical_erase_enabled():
        return 0
    res = await svc.erase_rows(rec.user_id, rec.plan.rows, actor=rec.actor or rec.user_id, reason=_REVIEW[rec.code][2])
    return int((res or {}).get("rows_removed") or 0) if isinstance(res, dict) else 0


def _default_svc() -> Any:
    from memory_service import get_memory_service
    return get_memory_service()


async def _transcript(rec: Record, svc: Any) -> int:
    import exact_words
    return int(await exact_words.flag_transcript_turns(rec.user_id, rec.plan.mids) or 0)


async def _exact_words(rec: Record, svc: Any) -> int:
    import exact_words
    n = int(await exact_words.erase_turns(rec.user_id, rec.plan.mids) or 0)
    return n + int(await exact_words.erase_entries(rec.user_id, rec.plan.entries) or 0)


async def _night_mind(rec: Record, svc: Any) -> int:
    import night_mind
    n = 0
    for mid in rec.plan.mids:
        n += int(await night_mind.erase_words(rec.user_id, mid) or 0)
    return n + int(await night_mind.erase_observations(rec.user_id, rec.plan.obs) or 0)


async def _notes(rec: Record, svc: Any) -> int:
    import conversation_notes
    n = int(await conversation_notes.erase_for_turns(rec.user_id, rec.plan.mids) or 0)
    return n + int(await conversation_notes.erase_ids(rec.user_id, rec.plan.notes) or 0)


async def _card(rec: Record, svc: Any) -> int:
    import user_model_card
    return int(await user_model_card.invalidate_for_rows(rec.user_id, rec.plan.rows) or 0)


async def _registry(rec: Record, svc: Any) -> int:
    import memory_value_registry
    n = int(await memory_value_registry.erase_ids(rec.user_id, rec.plan.reg) or 0)
    n += int(await memory_value_registry.erase_for_rows(rec.user_id, rec.plan.rows) or 0)
    if rec.plan.reg_topic:      # tied to the rows, or recorded from a retracted turn (with any: a row of no source turn goes too)
        return n + int(await memory_value_registry.erase_topic(rec.user_id, row_ids=rec.plan.rows, turn_ids=rec.plan.reg_turns) or 0)
    return n + int(await memory_value_registry.erase_for_turn_ids(rec.user_id, [t for t in rec.plan.reg_turns if t]) or 0)


async def _offers(rec: Record, svc: Any) -> int:
    if not rec.plan.offers:
        return 0
    import pending_suggestions
    return int(await pending_suggestions.withdraw_ids(rec.user_id, rec.plan.offers) or 0)


_STEP_FNS: "dict[str, Callable[[Record, Any], Awaitable[int]]]" = {
    "reject": _reject, "erase_rows": _erase_rows, "transcript": _transcript, "exact_words": _exact_words,
    "night_mind": _night_mind, "notes": _notes, "card": _card, "registry": _registry, "offers": _offers,
}
assert tuple(_STEP_FNS) == STEPS
_AFTER = {"erase_rows": "reject"}        # the one ordering rule: erase for real only what is already hidden


# ── running a record ─────────────────────────────────────────────────────────────────────────────────────────────────

_LOCKS: dict = {}


def _lock(user_id: str, key: str) -> asyncio.Lock:
    k = (id(asyncio.get_running_loop()), user_id, key)
    lk = _LOCKS.get(k)
    if lk is None:
        if len(_LOCKS) > 512:
            _LOCKS.clear()
        lk = _LOCKS[k] = asyncio.Lock()
    return lk


async def _save(rec: Record) -> None:
    new = rec.dump()
    if not await get_backend().cas(rec.key, rec.raw, new):
        raise Conflict("forget record changed under this attempt")
    rec.raw = new


async def _run(rec: Record, svc: Any) -> Outcome:
    out = Outcome(started=True, plan=rec.plan, done=rec.done)
    rec.tries += 1
    if rec.tries > STUCK_TRIES:
        logger.error("FORGET_RECORD code=stuck tries=%d entry=%s left=%s", rec.tries, rec.code,
                     ",".join(s for s in STEPS if s not in rec.done))
    try:
        await _save(rec)
    except Exception as exc:  # noqa: BLE001 - the member erase took it, or another process advanced it: not confirmed here
        out.failed, out.error = [s for s in STEPS if s not in rec.done], exc
        return out
    for step in STEPS:
        if step in rec.done:
            continue
        if _AFTER.get(step) and _AFTER[step] not in rec.done:
            out.failed.append(step)
            continue
        try:
            out.counts[step] = await _STEP_FNS[step](rec, svc)
            rec.done.append(step)
            await _save(rec)
        except Exception as exc:  # noqa: BLE001 - every remaining step still runs; the forget is not confirmed
            logger.warning("FORGET_RECORD code=step_failed step=%s entry=%s (%s)", step, rec.code, type(exc).__name__)
            out.failed.append(step)
            if isinstance(exc, Conflict):
                out.error = exc
                return out
    if out.failed:
        return out
    try:
        await get_backend().delete(rec.key)
    except Exception as exc:  # noqa: BLE001 - every step IS done; a leftover record is finished (a no-op) by the next pass
        logger.warning("FORGET_RECORD code=close_failed (%s)", type(exc).__name__)
    out.complete = True
    logger.info("FORGET_RECORD code=done entry=%s tries=%d rows=%d", rec.code, rec.tries, len(rec.plan.rows))
    return out


async def forget(user_id: str, *, code: str, key: str, resolve: Callable[[], Awaitable[Optional[Plan]]], svc: Any = None,
                 actor: str = "", origin: str = "") -> Outcome:
    """Run the forget of ``key`` for ``user_id``: finish its OPEN record if there is one (``resolve`` is not called: nothing is
    rediscovered), else ``resolve()`` the target to a ``Plan`` (None = nothing to forget), write the record, then run every step.
    NEVER raises: a failure before the record was written is ``Outcome(started=False, error=...)`` with nothing destroyed."""
    if code not in _REVIEW:
        raise ValueError(f"unknown forget entry point {code!r}")
    try:
        skey = storage_key(user_id, key)               # the record is stored (and locked) by a HASH of the key, never the key
        async with _lock(user_id, skey):
            store = get_backend()
            raw = await store.get(skey)
            if raw is not None:
                out = await _run(Record.load(user_id, skey, raw), svc)
                out.resumed = True
                return out
            plan = await resolve()
            if plan is None:
                return Outcome(nothing=True)
            rec = Record(user_id, skey, code, plan, actor=actor, origin=origin, created=time.time())
            rec.raw = rec.dump()
            if not await store.create(skey, rec.raw):          # another process wrote it first: finish THAT one
                raw = await store.get(skey)
                if raw is None:
                    return Outcome(error=Conflict("forget record vanished"))
                rec = Record.load(user_id, skey, raw)
            return await _run(rec, svc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FORGET_RECORD code=not_started entry=%s (%s)", code, type(exc).__name__)
        return Outcome(error=exc)


async def run_plan(user_id: str, plan: Plan, *, svc: Any = None) -> int:
    """Run every step of ``plan`` WITHOUT a record (a caller with no rows to hide: ``turn_forget.erase_turn_words``). Every step
    runs; the first failure is RAISED once all of them did. Returns what was removed."""
    rec, n, failure = Record(user_id, "", "forget_last", plan), 0, None
    for step in STEPS:
        if step in ("reject", "erase_rows") and not plan.rows:
            continue
        try:
            n += int(await _STEP_FNS[step](rec, svc) or 0)
        except Exception as exc:  # noqa: BLE001
            failure = failure or exc
            logger.warning("FORGET_RECORD code=step_failed step=%s entry=plan (%s)", step, type(exc).__name__)
    if failure is not None:
        raise failure
    return n


async def resume_open(user_id: str, *, svc: Any = None, limit: int = MAX_RESUME, skip: Any = ()) -> int:
    """Finish up to ``limit`` of the member's open records (oldest first), but those whose storage key is in ``skip``; returns how
    many completed. NEVER raises."""
    try:
        recs = sorted((r for r in await get_backend().open_records(user_id) if r.key not in set(skip or ())),
                      key=lambda r: r.created)[:max(0, int(limit))]
        if not recs:
            return 0
        done = 0
        for rec in recs:
            async with _lock(user_id, rec.key):
                raw = await get_backend().get(rec.key)
                if raw is not None and (await _run(Record.load(user_id, rec.key, raw), svc)).complete:
                    done += 1
        if done:
            logger.info("FORGET_RECORD code=resumed n=%d", done)
        return done
    except Exception as exc:  # noqa: BLE001
        logger.warning("FORGET_RECORD code=resume_failed (%s)", type(exc).__name__)
        return 0


def reset() -> None:  # tests
    set_backend(MemoryBackend())


__all__ = ["ENTRY_POINTS", "STEPS", "Plan", "Record", "Outcome", "forget", "run_plan", "resume_open", "forget_user", "get_backend",
           "set_backend", "MemoryBackend", "SqlBackend", "storage_key", "words_key", "reset"]
