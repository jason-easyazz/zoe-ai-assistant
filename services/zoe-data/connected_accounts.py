"""connected_accounts - who connected which external account, its sync runs, and the generation fence (migration 0078).

Stage 0 of the feed plumbing (record ``docs/knowledge/connected-accounts.md``): the rows every feed (calendar, contacts, mail) hangs off.
Nothing here talks to a provider, and no route exposes it yet.

* ``create`` records a connection (status ``pending``); the provider subject is stored only as ``subject_hash``.
* ``disconnect`` bumps ``account_generation``, marks the row ``revoked`` and deletes its secrets. A sync that started under the old
  generation is FENCED: ``finish_run`` refuses to record it as done and ``still_current`` (checked by the writer inside its own
  transaction) says no, so a batch already in flight cannot recreate rows after a disconnect.
* ``start_run`` / ``finish_run`` keep the sync log: counts, outcome, an error CLASS (``ERROR_CLASSES``, never a provider message),
  an opaque cursor before / after, ``backoff_until``; since 0079 also the feed counts, bytes, duration and an error CODE
  (``ERROR_CODES``, the plain-words reason, mirrored on the account as ``last_error_code``).
* ``upsert_by_external_id`` is the idempotent write a feed importer adopts (Stage 1 moves ``calendar_ical`` onto it).
* ``forget_user`` (``MemoryService.delete_user``) erases the member's accounts, runs and secrets under the member's erase gate.

Every member write snapshots ``user_erase_gate.generation`` when it is scheduled and writes inside ``erase_gate.writing``: a write
scheduled before an erase is dropped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

import account_secrets
import user_erase_gate as erase_gate

logger = logging.getLogger(__name__)

KINDS = ("calendar", "contacts", "mail", "music", "other")
STATUSES = ("pending", "active", "error", "revoked")
ERROR_CLASSES = ("auth", "network", "rate_limited", "quota", "provider", "config", "internal")
OUTCOMES = ("ok", "partial", "error")
#: the plain-words error CODES a feed records beside its class (alembic 0079 CHECKs both columns against the same list)
ERROR_CODES = ("http_404", "too_large", "not_calendar", "ssrf_blocked", "parse_failed", "revoked_link", "suspect_empty",
               "ledger_unavailable")
#: ``sync_runs`` counts: the 0078 four, then the 0079 ones (written only when the caller gives them, so a 0078 database still works)
COUNTS = ("seen", "created", "updated", "deleted")
COUNTS_0079 = ("unchanged", "skipped", "pruned", "suppressed", "rrule_unsupported")
_UNSET = object()
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
#: tables ``upsert_by_external_id`` may write (feed rows only)
UPSERT_TABLES = frozenset({"events", "calendars", "source_contacts"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db():
    from db_compat import get_compat_db
    return get_compat_db()


def _no_store(exc: BaseException) -> bool:
    """A table not migrated yet, or a process that never had a database pool (a bare unit run): nothing stored to read or erase."""
    s = str(exc).lower()
    if type(exc).__name__ == "UndefinedTableError" or "no such table" in s:
        return True
    if isinstance(exc, RuntimeError) and "pool not initialised" in s:
        from db_pool import pool_ever_initialised
        return not pool_ever_initialised()
    return False


def subject_hash(provider: str, subject: str) -> str:
    return hashlib.sha256(f"{provider}\x00{(subject or '').strip().lower()}".encode()).hexdigest()


async def create(user_id: str, provider: str, kind: str, subject: str, *, label: str = "", scopes: Iterable[str] = (),
                 scheduled_at: Optional[int] = None) -> Optional[str]:
    """Record a connection; the account id, or None when the member is being (or was since scheduled) erased. A reconnect of the same
    (member, provider, subject) returns the existing id; if that account was ``revoked`` it starts over as a fresh link does
    (``pending``, active after its first good sync) under a NEW generation, so a run from the old connection stays fenced.
    Raises ``ValueError`` on bad input and on a database error."""
    if kind not in KINDS or not _PROVIDER.match(provider or "") or not (user_id and subject):
        raise ValueError("connected_accounts.create: bad provider / kind / member / subject")
    gen = erase_gate.generation(user_id) if scheduled_at is None else scheduled_at
    shash, now = subject_hash(provider, subject), _now()
    async with erase_gate.writing(user_id, gen) as live:
        if not live:
            logger.info("ACCOUNTS event=dropped reason=erased_since_scheduled provider=%s", provider)
            return None
        async with _db() as db:
            cur = await db.execute("SELECT id FROM connected_accounts WHERE user_id = ? AND provider = ? AND subject_hash = ?",
                                   (user_id, provider, shash))
            row = await cur.fetchone()
            if row is not None:
                async with db.transaction():
                    await db.execute(f"SELECT id FROM connected_accounts WHERE id = ?{_for_update(db)}", (row[0],))
                    await db.execute("UPDATE connected_accounts SET status = 'pending', account_generation = account_generation + 1, "
                                     "kind = ?, label = ?, scopes = ?, last_error_class = NULL, updated_at = ? "
                                     "WHERE id = ? AND status = 'revoked'",
                                     (kind, label[:120], json.dumps(sorted(set(scopes))), now, row[0]))
                return str(row[0])
            aid = f"acct_{uuid.uuid4().hex}"
            await db.execute("INSERT INTO connected_accounts (id, user_id, provider, kind, subject_hash, label, scopes, status, "
                             "account_generation, created_at, updated_at) VALUES (?,?,?,?,?,?,?,'pending',1,?,?)",
                             (aid, user_id, provider, kind, shash, label[:120], json.dumps(sorted(set(scopes))), now, now))
            await db.commit()
    logger.info("ACCOUNTS event=created provider=%s kind=%s", provider, kind)
    return aid


def _for_update(db) -> str:
    """`` FOR UPDATE`` on PostgreSQL (the asyncpg compat connection): the account row stays locked to the end of the caller's
    transaction, so a disconnect cannot land between a fence check and the write it guards. SQLite (lab / tests) has one writer."""
    return " FOR UPDATE" if type(db).__name__ == "AsyncpgCompat" else ""


async def still_current(db, account_id: str, generation: int) -> bool:
    """Is the account still connected at ``generation``? A feed writer asks this INSIDE the transaction that writes its batch; on
    PostgreSQL the row is then locked (``FOR UPDATE``) until that transaction ends, and ``disconnect`` waits for it."""
    cur = await db.execute(f"SELECT account_generation, status FROM connected_accounts WHERE id = ?{_for_update(db)}", (account_id,))
    row = await cur.fetchone()
    return row is not None and int(row[0]) == int(generation) and str(row[1]) != "revoked"


async def disconnect(account_id: str) -> Optional[int]:
    """Fence and revoke: bump the generation (in-flight batches stop counting), mark ``revoked``, delete the secrets. The new
    generation, or None when there is no such account. The member's synced rows are removed by the feed's own disconnect step (Stage 1)."""
    async with _db() as db:
        async with db.transaction():   # the same row lock as ``still_current``: never between a run's fence check and its write
            await db.execute(f"SELECT id FROM connected_accounts WHERE id = ?{_for_update(db)}", (account_id,))
            await db.execute("UPDATE connected_accounts SET account_generation = account_generation + 1, status = 'revoked', "
                             "updated_at = ? WHERE id = ?", (_now(), account_id))
            cur = await db.execute("SELECT account_generation FROM connected_accounts WHERE id = ?", (account_id,))
            row = await cur.fetchone()
    if row is None:
        return None
    await account_secrets.remove(account_id)
    return int(row[0])


@dataclass(frozen=True)
class Run:
    run_id: str
    account_id: str
    user_id: str
    generation: int          # the account generation the run started under
    scheduled_at: int        # the member's erase generation when the run was scheduled


async def start_run(account_id: str, user_id: str, *, scope: str = "", cursor_before: Optional[str] = None,
                    scheduled_at: Optional[int] = None) -> Optional[Run]:
    """Open a sync run, or None (erased since scheduled, no such account of this member, or revoked)."""
    gen = erase_gate.generation(user_id) if scheduled_at is None else scheduled_at
    async with erase_gate.writing(user_id, gen) as live:
        if not live:
            return None
        async with _db() as db:
            cur = await db.execute("SELECT account_generation, status FROM connected_accounts WHERE id = ? AND user_id = ?",
                                   (account_id, user_id))
            row = await cur.fetchone()
            if row is None or str(row[1]) == "revoked":
                return None
            run = Run(f"run_{uuid.uuid4().hex}", account_id, user_id, int(row[0]), gen)
            await db.execute("INSERT INTO sync_runs (id, account_id, user_id, scope, account_generation, started_at, cursor_before) "
                             "VALUES (?,?,?,?,?,?,?)", (run.run_id, account_id, user_id, scope[:200], run.generation, _now(),
                                                         cursor_before))
            await db.commit()
    return run


async def finish_run(run: Run, *, outcome: str, counts: Optional[Mapping[str, int]] = None, cursor_after: Optional[str] = None,
                     error_class: Optional[str] = None, backoff_until: Optional[str] = None, error_code: Any = _UNSET,
                     n_bytes: Optional[int] = None, duration_ms: Optional[int] = None) -> bool:
    """Close a run. True when recorded as ``outcome``; False when the member was erased since (nothing written) or the account was
    disconnected since (the run is recorded ``fenced``, its cursor dropped, the account untouched).

    The 0079 extras are written only when given: a ``COUNTS_0079`` key in ``counts``, ``n_bytes`` / ``duration_ms``, and
    ``error_code`` (a code from ``ERROR_CODES`` or None; passing it also sets / clears ``connected_accounts.last_error_code``)."""
    if outcome not in OUTCOMES or (error_class is not None and error_class not in ERROR_CLASSES):
        raise ValueError("connected_accounts.finish_run: unknown outcome / error class")
    if error_code is not _UNSET and error_code is not None and error_code not in ERROR_CODES:
        raise ValueError("connected_accounts.finish_run: unknown error code")
    c = {k: max(0, int((counts or {}).get(k, 0))) for k in COUNTS}
    run_extra = {f"n_{k}": max(0, int(counts[k])) for k in COUNTS_0079 if counts and k in counts}
    for col, val in (("bytes", n_bytes), ("duration_ms", duration_ms)):
        if val is not None:
            run_extra[col] = max(0, int(val))
    acct_extra: dict = {}
    if error_code is not _UNSET:
        run_extra["error_code"] = acct_extra["last_error_code"] = error_code
    now = _now()
    async with erase_gate.writing(run.user_id, run.scheduled_at) as live:
        if not live:
            return False
        async with _db() as db:
            async with db.transaction():
                # the check and the write are ONE step: the row is locked by the check (PostgreSQL), and the write itself only lands
                # on the generation the run started under - zero rows means a disconnect got there first, and the run is fenced
                moved = 0
                if await still_current(db, run.account_id, run.generation):
                    extra_sql = "".join(f", {k} = ?" for k in acct_extra)
                    cur = await db.execute(
                        f"UPDATE connected_accounts SET status = ?, last_sync_at = ?, last_error_class = ?, updated_at = ?{extra_sql} "
                        "WHERE id = ? AND account_generation = ? AND status <> 'revoked'",
                        ("error" if outcome == "error" else "active", now, error_class, now, *acct_extra.values(), run.account_id,
                         run.generation))
                    moved = int(getattr(cur, "rowcount", 0) or 0)
                if not moved:
                    await db.execute("UPDATE sync_runs SET outcome = 'fenced', finished_at = ? WHERE id = ?", (now, run.run_id))
                    return False
                extra_sql = "".join(f", {k} = ?" for k in run_extra)
                await db.execute("UPDATE sync_runs SET outcome = ?, finished_at = ?, error_class = ?, n_seen = ?, n_created = ?, "
                                 f"n_updated = ?, n_deleted = ?, cursor_after = ?, backoff_until = ?{extra_sql} WHERE id = ?",
                                 (outcome, now, error_class, c["seen"], c["created"], c["updated"], c["deleted"], cursor_after,
                                  backoff_until, *run_extra.values(), run.run_id))
    return True


async def upsert_by_external_id(db, table: str, *, match: Mapping[str, Any], values: Mapping[str, Any],
                                version_col: Optional[str] = None) -> str:
    """Write one feed row idempotently: ``created`` | ``updated`` | ``unchanged``. ``match`` is the row's external identity (e.g.
    ``{"calendar_id": c, "external_uid": u, "recurrence_id": r}``; a None matches NULL); ``values`` the columns to set (an insert also
    needs ``id``). With ``version_col`` (``etag`` / ``sequence``) an equal stored version is ``unchanged`` and nothing is written.
    Runs on the caller's connection, so it sits inside the caller's transaction and its ``still_current`` fence."""
    cols = [*match, *values]
    if table not in UPSERT_TABLES or not all(_IDENT.match(c) for c in cols) or (version_col and version_col not in values):
        raise ValueError("connected_accounts.upsert_by_external_id: table or column not allowed")
    where = " AND ".join(f"{k} IS NULL" if v is None else f"{k} = ?" for k, v in match.items())
    params = tuple(v for v in match.values() if v is not None)
    cur = await db.execute(f"SELECT id{', ' + version_col if version_col else ''} FROM {table} WHERE {where}", params)
    row = await cur.fetchone()
    if row is None:
        row_cols = {**match, **values}
        await db.execute(f"INSERT INTO {table} ({', '.join(row_cols)}) VALUES ({', '.join('?' for _ in row_cols)})",
                         tuple(row_cols.values()))
        return "created"
    if version_col and row[1] is not None and str(row[1]) == str(values[version_col]):
        return "unchanged"
    upd = {k: v for k, v in values.items() if k != "id"}
    await db.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in upd)} WHERE id = ?", (*upd.values(), row[0]))
    return "updated"


async def forget_user(user_id: str) -> int:
    """Right to be forgotten: the member's secrets, sync runs and connected accounts, under the member's erase gate. Raises on any
    failure but "nothing stored" (the caller fails closed)."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    n = 0
    try:
        async with erase_gate.erasing(uid):
            n += await account_secrets.forget_user(uid)
            async with _db() as db:
                for table in ("sync_runs", "connected_accounts"):
                    cur = await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
                    n += int(getattr(cur, "rowcount", 0) or 0)
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return n
        logger.error("ACCOUNTS forget failed (%s): rows NOT erased", type(exc).__name__)
        raise
    logger.info("ACCOUNTS forget rows=%d", n)
    return n
