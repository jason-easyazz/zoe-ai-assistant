"""MemoryService - the sole read/write surface for Zoe memory.

This module is the backend-pluggable facade every caller should use. The
first implementation wraps MemPalace (Chroma under the hood) but callers
see only opaque `MemoryRef` objects - `wing`, `drawer`, `room`, and Chroma
collection names never leak above this module. Swapping to raw `chromadb`
or to a different vector store becomes a one-file change.

Contract (`memory_and_self-learning_audit` plan, Phase 1):

  * ingest()          - THE only way facts enter the store.
  * load_for_prompt() - the only path the agent system prompt uses.
  * search()          - the only path any semantic query uses.
  * review()          - UI approve/reject/edit.
  * tick_access()     - bumps access_count + last_accessed; called on every hit.
  * delete_user()     - admin-only `/api/users/{id}/forget`.
  * export_user()     - admin-only full JSON dump.

Safety rails enforced here, not in callers:

  * per-user `asyncio.Lock`    -> serialises concurrent writes for a single user.
  * idempotency keys           -> (user_id, user_turn_id) same call twice = one row.
  * fail-closed on missing     -> no anonymous writes. user_id is mandatory.
  * PII scrubber               -> Luhn CC, SSN-shape, 2FA, password-adjacent.
  * immutable audit log        -> every mutation appends to `mempalace_audit`.
  * metrics                    -> every path instruments `zoe_memory_*` counters.
  * AUTHORITY (memory_authority.py) -> every row carries a provenance CLASS from an
    allow-list (operator 6 > user_confirmed 5 > user_stated 4 > user_stated_derived 3 > user_unverified 2 >
    model_from_turn 1 > model_from_transcript 0; unknown writers rank 0) plus `authority`
    (user_stated | user_confirmed | inferred), `origin`, `turn_ref`, `model`, derived at
    write time from the real writer. ingest(), review(edit|archive|reject|approve),
    supersede_by() and archive_duplicate() are the ONE choke point: a write below the user
    classes can never supersede, archive or contradict a row that outranks it about the
    same subject + attribute - it leaves a `disputed` candidate linked via `contradicts_id`
    and logs `AUTHORITY_BLOCKED writer=<name> kind=<attr>`. A supersede records the NEW
    writer's provenance (never the old row's source / session / excerpt). The identity wall
    (identity_facts) is the special case "the user's own name is never writable by an
    automatic writer at all". ZOE_MEMORY_AUTHORITY=enforce|shadow|off.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
from user_prefs import MEMORY_OPT_OUT_SOURCES
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

import memory_authority as _auth
import memory_forgotten as _forgotten
import own_words as _own_words
import memory_temporal as _temporal
from live_store_guard import (
    LiveStoreViolation,
    assert_palace_open_allowed,
    assert_write_allowed,
    guard_collection,
)
from memory_captured_at import parse_captured_at, value_shape
from memory_importance import score_importance

try:
    from memory_metrics import (
        memory_write_count,
        memory_search_latency_ms,
        memory_search_hit_count,
        memory_dedup_skip_count,
        memory_pii_reject_count,
    )
    _METRICS_OK = True
except ImportError:  # pragma: no cover
    # memory_metrics is an optional instrumentation module; silently degrade.
    _METRICS_OK = False

logger = logging.getLogger(__name__)

#: the stored (``candidate_``-prefixed) spelling of the affect metadata keys
#: ``contradicts_id`` prefix of a candidate that disputes a people-graph edge
_EDGE_REF = "edge:"
_AFFECT_CANDIDATE_KEYS = frozenset(f"candidate_{k}" for k in _auth.AFFECT_KEYS)

_MEMPALACE_DATA = os.environ.get(
    "MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace")
)

_AUDIT_COLLECTION = os.environ.get("ZOE_MEMORY_AUDIT_COLLECTION", "mempalace_audit")

# Constant embedding for audit rows — they are metadata-filtered only, never
# semantically searched, so computing a real MiniLM vector per memory mutation
# is pure waste. 384-dim to match the collection's existing rows; unit-basis
# (not all-zero) so it is valid under any hnsw space. Kept as a TUPLE for
# immutability; chromadb 0.6.3 validation requires a list-of-lists
# (types.normalize_embeddings: isinstance(target[0], list)), so callers pass a
# fresh list(...) copy per upsert — which also means no shared mutable object
# ever reaches chroma. See _append_audit_sync.
_AUDIT_NULL_EMBEDDING: tuple[float, ...] = (1.0,) + (0.0,) * 383
# ONE chromadb.PersistentClient per resolved palace dir, shared by the drawers and the
# audit collections (the name predates the drawers moving onto it). Two clients on
# different spellings of one path would be two systems writing one SQLite.
_AUDIT_CLIENTS: dict[str, Any] = {}
_AUDIT_CLIENTS_LOCK = threading.Lock()

_DRAWERS_COLLECTION = "mempalace_drawers"
_DRAWERS_EF: Any = None
_DRAWERS_EF_LOCK = threading.Lock()


def _palace_client(data_dir: str) -> Any:
    """The cached PersistentClient for ``data_dir`` (resolved, so every spelling shares it)."""
    key = os.path.realpath(os.path.abspath(os.path.expanduser(data_dir)))
    # Hard guard (live_store_guard): a pytest session may never open the household palace —
    # checked on EVERY call, not only the first, so a cached client cannot launder it.
    assert_palace_open_allowed(key)
    client = _AUDIT_CLIENTS.get(key)
    if client is None:
        with _AUDIT_CLIENTS_LOCK:
            client = _AUDIT_CLIENTS.get(key)
            if client is None:
                if os.environ.get("ZOE_MEMORY_HEAP_SCRUB", "").strip().lower() in ("1", "true", "yes", "on"):
                    # opt-in (default OFF: the allocator is process-wide, the voice path shares this process):
                    # keep hnswlib's index-file writes free of the remains of freed text. See memory_residue.
                    import memory_residue
                    memory_residue.enable_heap_scrub()
                import chromadb
                _check_palace_format(key, getattr(chromadb, "__version__", "0"))
                client = chromadb.PersistentClient(path=key)
                _AUDIT_CLIENTS[key] = client
    return client


def _check_palace_format(palace_dir: str, chromadb_version: str) -> None:
    """Refuse a client whose major version disagrees with the palace's on-disk format.

    B0.8: a 1.x client silently migrates a 0.6 palace IN PLACE on first open (one-way),
    and a 0.6.3 client dies on a 1.x palace. Read the format from SQLite (``mode=ro``),
    before chromadb touches the file: sysdb migration 00010 exists only in 1.x.
    Mirrors ``scripts/lib/palace_client.py`` (scripts cannot import service code).
    """
    import sqlite3

    db = os.path.join(palace_dir, "chroma.sqlite3")
    if not os.path.exists(db):
        return  # a brand-new palace takes whatever this client writes
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT max(version) FROM migrations WHERE dir = 'sysdb'").fetchone()
    except sqlite3.OperationalError as exc:
        # An EXISTING chroma.sqlite3 whose format cannot be read (no migrations table, partial
        # restore, unknown schema) must fail closed: a 1.x client would otherwise initialise or
        # migrate it in place before the guard has identified it. Only a missing DB is "new".
        raise RuntimeError(
            f"palace {palace_dir} has a chroma.sqlite3 whose format cannot be identified ({exc}); "
            "refusing to open it (see docs/knowledge/chroma-1-5-migration.md)"
        ) from exc
    finally:
        con.close()
    on_disk = "1.x" if row and row[0] is not None and int(row[0]) >= 10 else "0.6"
    client = "1.x" if int(str(chromadb_version).split(".")[0]) >= 1 else "0.6"
    if on_disk != client:
        raise RuntimeError(
            f"palace {palace_dir} is chromadb {on_disk} format but the installed client is "
            f"{chromadb_version}; refusing to open it (see docs/knowledge/chroma-1-5-migration.md)"
        )


def _drawers_embedding_function() -> Any:
    """One MiniLM ONNX session per process, reported under the name ``"default"``.

    B0.8 (chromadb 1.5.x): the palace's collections persist the embedding-function
    identity ``"default"``. Opening one WITHOUT an EF gives chroma's
    ``DefaultEmbeddingFunction``, which builds a fresh ``ONNXMiniLM_L6_V2`` (and ONNX
    session) on EVERY call: measured 0.42-0.89 s per query vs 0.18-0.27 s with this
    cached instance, and 0.11-0.22 s on 0.6.3. Passing a differently-named EF raises
    "Embedding function conflict", hence the name. The model is the same archive (SHA
    ``913d7300…``) that chroma pins in both 0.6.3 and 1.5.9; mempalace 3.10 uses the
    same trick (``embedding._build_ef_class``).
    """
    global _DRAWERS_EF
    if _DRAWERS_EF is None:
        with _DRAWERS_EF_LOCK:
            if _DRAWERS_EF is None:
                from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

                class _ZoeMiniLM(ONNXMiniLM_L6_V2):
                    @staticmethod
                    def name() -> str:
                        return "default"

                _DRAWERS_EF = _ZoeMiniLM()
    return _DRAWERS_EF


def get_drawers_collection(data_dir: str) -> Any:
    """The recall (drawers) collection: Zoe's one opener, raw chromadb.

    Replaces ``mempalace.palace.get_collection`` (B0.8). mempalace 3.3.1's backend runs
    ``_fix_blob_seq_ids`` against the SQLite on every new client, which is unsafe on a
    1.x palace, and 3.10's wrapper adds file locks, where-validation and identity
    checks Zoe has never run under. What stays the same is what 3.3.1 did: a cached
    client, the existing collection as-is, and ``hnsw:space=cosine`` only when a brand-new
    palace has none yet. The live palace keeps its own ``l2`` space.
    """
    if not getattr(_OP_THREAD, "depth", 0):   # a leased op was admitted under the gate already
        _wait_for_maintenance_gate()
    client = _palace_client(data_dir)
    ef = _drawers_embedding_function()
    try:
        col = client.get_collection(_DRAWERS_COLLECTION, embedding_function=ef)
    except Exception:
        names = {getattr(c, "name", c) for c in client.list_collections()}
        if _DRAWERS_COLLECTION in names:
            raise  # it exists: this is a real error, never paper over it with a create
        col = client.create_collection(
            _DRAWERS_COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=ef
        )
    # The one wrapped accessor: every direct drawer writer (digest passes, tick_access, supersede,
    # zoe_agent) gets its handle here. The service gets the raw collection; a non-service process on
    # the live palace gets a write-checking proxy (live_store_guard.GuardedCollection).
    return guard_collection(col, data_dir)


# ── Maintenance gate + in-process index compaction (ZOE_MEMORY_INDEX_COMPACT) ──────────
# chroma 1.x never compacts a persistent HNSW index; demo-user churn left 1,591 elements for
# 258 live rows on 2026-10-04 and owner-filtered queries came back empty. The fix is a
# rebuild from the STORED embeddings (delete + recreate the collection, re-add; nothing is
# re-embedded). Done in-process it needs ONE guarantee: while the collection is absent,
# nothing may call ``get_drawers_collection`` — its fallback would CREATE the collection
# with ``hnsw:space=cosine`` and race the rebuild into the wrong space. Every drawers read
# and write funnels through that opener (``MemoryService._collection`` and zoe_agent's
# direct call), so the opener waits on this event; it is SET in normal operation and
# CLEARED only for the duration of the swap, by the one compaction thread, which talks to
# the cached client directly and never through the opener.
#
# The gate alone only stops NEW openers. ``_run_sync`` uses the default (multi-threaded)
# executor, so an operation that already holds a handle can still be mid-flight when the
# gate closes — a write landing between the export and the delete would be lost, a later
# one would use a stale collection id. Hence the LEASE: ``collection_op`` admits an
# operation under the gate and counts it until it ends (``_run_sync`` wraps every executor
# call; direct users of the opener wrap their whole read-modify-write). The compaction
# closes the gate, then DRAINS the count to zero (bounded) before it exports, and aborts
# with no change if it cannot. Reader/writer lock semantics, spelled out.
_MAINTENANCE_OPEN = threading.Event()
_MAINTENANCE_OPEN.set()
_MAINTENANCE_WAIT_S = 60.0          # a caller blocks at most this long before raising
_MAINTENANCE_DRAIN_S = 30.0         # the compaction waits at most this long for in-flight ops
_MAINTENANCE_POLL_S = 0.5           # waiters re-check the blocked marker this often
# Fail-closed state: set (never cleared by the compaction) when a rebuild failed AND the
# restore from the export could not be verified. The gate then STAYS cleared — reopening
# would let the opener's fallback create an empty cosine collection over a partial store —
# every opener/lease call fails fast with the reason, ``index-health`` reports it, and the
# operator restores the tar with zoe-data stopped (runbook §22). A restart clears it.
_MAINTENANCE_BLOCKED: dict[str, Any] | None = None
_OPS_COND = threading.Condition()   # guards _ACTIVE_OPS; notified when a lease is released
_ACTIVE_OPS = 0
_OP_THREAD = threading.local()      # .depth: this thread's lease nesting (re-entrant)
_COMPACT_LOCK = threading.Lock()    # one compaction at a time
_COMPACT_BATCH = 100
_COMPACT_PROBE = "When did I tell you about the dentist?"   # the measured failing sentence
_COMPACT_BACKUPS_DIR = os.path.expanduser("~/.zoe/palace-backups")


def maintenance_state() -> dict[str, Any]:
    """The gate as seen by operators: ``maintenance_open`` (normal = True),
    ``maintenance_blocked`` + ``maintenance_reason`` (+ the backup paths) when fail-closed."""
    blocked = _MAINTENANCE_BLOCKED
    out: dict[str, Any] = {
        "maintenance_open": _MAINTENANCE_OPEN.is_set(),
        "maintenance_blocked": blocked is not None,
        "maintenance_reason": (blocked or {}).get("reason"),
    }
    if blocked:
        out.update({k: blocked.get(k) for k in ("backup_tar", "export", "since")})
    return out


def _block_maintenance(reason: str, report: dict[str, Any]) -> None:
    """Fail closed: record why the gate must stay shut. The gate is NOT reopened."""
    global _MAINTENANCE_BLOCKED
    _MAINTENANCE_BLOCKED = {
        "reason": reason, "backup_tar": report.get("backup_tar"), "export": report.get("export"),
        "since": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _MAINTENANCE_OPEN.clear()
    logger.critical(
        "MEMORY_INDEX_COMPACT GATE CLOSED — drawers collection is absent or partial and the restore "
        "could not be verified; memory reads/writes fail fast until an operator restores the backup "
        "(runbook §22) and restarts zoe-data. reason=%s backup_tar=%s export=%s",
        reason, report.get("backup_tar"), report.get("export"))


def clear_maintenance_block() -> None:
    """Operator/test escape hatch after a verified manual restore (a restart does the same)."""
    global _MAINTENANCE_BLOCKED
    _MAINTENANCE_BLOCKED = None
    _MAINTENANCE_OPEN.set()


def _gate_error(wait: float) -> MemoryServiceError:
    blocked = _MAINTENANCE_BLOCKED
    if blocked is not None:
        return MemoryServiceError(
            "memory index maintenance FAILED CLOSED: the drawers collection is unavailable until an "
            f"operator restores the backup and restarts zoe-data — {blocked.get('reason')}"
            f" (backup_tar={blocked.get('backup_tar')})")
    return MemoryServiceError(
        f"memory index maintenance in progress: the drawers collection did not reopen within {wait:g}s")


def _wait_for_maintenance_gate(timeout: float | None = None) -> None:
    """Block (bounded) while the gate is cleared; fail FAST, without waiting, once the gate is
    blocked — a waiter that entered before the block sees it within ``_MAINTENANCE_POLL_S``."""
    wait = _MAINTENANCE_WAIT_S if timeout is None else timeout
    if _MAINTENANCE_OPEN.is_set():
        return
    deadline = time.monotonic() + wait
    while _MAINTENANCE_BLOCKED is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if _MAINTENANCE_OPEN.wait(min(remaining, _MAINTENANCE_POLL_S)):
            return
    raise _gate_error(wait)


@contextlib.contextmanager
def collection_op(timeout: float | None = None):
    """Admit ONE collection operation under the maintenance gate and hold its lease until
    the block ends (also on exceptions). Re-entrant per thread, so nested helpers that call
    ``get_drawers_collection`` inside a leased op skip the gate. Admission is re-checked
    under ``_OPS_COND`` so no op can slip in after the compaction closes the gate."""
    global _ACTIVE_OPS
    depth = getattr(_OP_THREAD, "depth", 0)
    if depth:
        _OP_THREAD.depth = depth + 1
        try:
            yield
        finally:
            _OP_THREAD.depth -= 1
        return
    wait = _MAINTENANCE_WAIT_S if timeout is None else timeout
    deadline = time.monotonic() + wait
    while True:
        _wait_for_maintenance_gate(max(deadline - time.monotonic(), 0.0))   # raises: timeout / blocked
        with _OPS_COND:
            if _MAINTENANCE_OPEN.is_set():
                _ACTIVE_OPS += 1
                break
    _OP_THREAD.depth = 1
    try:
        yield
    finally:
        _OP_THREAD.depth -= 1
        with _OPS_COND:
            _ACTIVE_OPS -= 1
            _OPS_COND.notify_all()


def _leased_call(fn, *args):
    with collection_op():
        return fn(*args)


class _LeasedDrawers:
    """The drawers collection for code that is NOT inside ``_run_sync``: every method call
    takes the lease, opens a FRESH handle and releases the lease when the call returns.

    Needed by the async digest passes, which run their chroma calls on the event-loop thread
    and ``await`` between them: a handle cached in a local across an ``await`` is exactly
    the op the compaction's drain cannot see (``_ACTIVE_OPS`` stays 0, the delete races
    the read or upsert). Per-call leasing means a compaction may land BETWEEN two calls —
    which is fine: the next call opens the rebuilt collection (same ids, same rows), and a
    write before the drain is in the export, one after the reopen lands on the new index.
    Never hold a lease across an ``await``."""

    __slots__ = ("_svc",)

    def __init__(self, svc: Any):
        self._svc = svc

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            with collection_op():
                return getattr(self._svc._collection(), name)(*args, **kwargs)

        call.__name__ = name
        return call


def leased_drawers(svc: Any) -> _LeasedDrawers:
    """``col = leased_drawers(svc)`` then ``col.get(...)`` / ``col.upsert(...)`` as before —
    the leased accessor for any direct collection user outside ``_run_sync``."""
    return _LeasedDrawers(svc)


def _drain_collection_ops(timeout: float) -> bool:
    """With the gate CLOSED: wait until no leased op is in flight. False on timeout."""
    deadline = time.monotonic() + timeout
    with _OPS_COND:
        while _ACTIVE_OPS > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _OPS_COND.wait(remaining)
    return True


def _as_list(value: Any) -> list:
    """A list from chroma's result columns WITHOUT truth-testing: on 1.5.x
    ``get(include=["embeddings"])`` returns a NumPy ndarray, and ``x or []`` raises
    "truth value of an array is ambiguous"."""
    return [] if value is None else list(value)


def index_compaction_enabled() -> bool:
    """Dark flag ``ZOE_MEMORY_INDEX_COMPACT`` (default OFF; per-call read)."""
    return os.environ.get("ZOE_MEMORY_INDEX_COMPACT", "").strip().lower() in ("1", "true", "yes", "on")


def _collection_space(col: Any) -> str:
    """The collection's ``hnsw:space`` (1.x configuration JSON first, then the legacy
    metadata key). The live palace is ``l2``, chroma's own default — so ``l2`` when unset."""
    try:
        cfg = col.configuration_json
        cfg = json.loads(cfg) if isinstance(cfg, str) else dict(cfg or {})
        space = (cfg.get("hnsw") or {}).get("space")
        if space:
            return str(space)
    except Exception:  # noqa: BLE001 — fall through to the metadata key
        pass
    return str((getattr(col, "metadata", None) or {}).get("hnsw:space") or "l2")


def _restore_drawers(client: Any, ef: Any, space: str, rows: dict[str, list], report: dict[str, Any]) -> bool:
    """After anything in the destructive region raised: put the exported rows back.

    The collection may be ABSENT (delete completed, rebuild failed), PARTIAL (chroma's
    SegmentAPI removes the segments before the sysdb row, so a delete that raised mid-way can
    leave the name listed over no data) or a BROKEN rebuild — all three are handled the same
    way: delete-if-listed, then create + re-add from the export. ``True`` only when the
    restored count equals the exported count; the caller keeps the gate CLOSED otherwise."""
    n = len(rows["ids"])
    report["restored"] = False
    try:
        names = {getattr(c, "name", c) for c in client.list_collections()}
        if _DRAWERS_COLLECTION in names:
            client.delete_collection(_DRAWERS_COLLECTION)
        restored = _rebuild_drawers(client, ef, space, rows)
        count = int(restored.count())
        report["restored_count"] = count
        report["restored"] = count == n
        if not report["restored"]:
            report["restore_error"] = f"restored count {count} != exported {n}"
    except Exception as rexc:  # noqa: BLE001 — the report carries it; the gate stays shut
        report["restore_error"] = f"{type(rexc).__name__}: {rexc}"
    if not report["restored"]:
        logger.error("MEMORY_INDEX_COMPACT restore FAILED — restore the tar backup %s: %s",
                     report.get("backup_tar"), report.get("restore_error"))
    return bool(report["restored"])


def _rebuild_drawers(client: Any, ef: Any, space: str, rows: dict[str, list]) -> Any:
    """create the collection in ``space`` and re-add ``rows`` in batches (used by both the
    rebuild and the restore-after-delete path — one implementation, no drift)."""
    new = client.create_collection(
        _DRAWERS_COLLECTION, metadata={"hnsw:space": space}, embedding_function=ef
    )
    ids, embs, docs, metas = rows["ids"], rows["embeddings"], rows["documents"], rows["metadatas"]
    for i in range(0, len(ids), _COMPACT_BATCH):
        new.add(ids=ids[i:i + _COMPACT_BATCH], embeddings=embs[i:i + _COMPACT_BATCH],
                documents=docs[i:i + _COMPACT_BATCH], metadatas=metas[i:i + _COMPACT_BATCH])
    return new


def _verify_rebuilt_drawers(new: Any, rows: dict[str, list], probe: str) -> dict[str, Any]:
    """count equal; unfiltered reach for the probe == count; an owner-filtered query returns
    rows (when a non-demo owner exists); one metadata update round-trips."""
    ids, metas = rows["ids"], rows["metadatas"]
    n = len(ids)
    count = int(new.count())
    reach = len(new.query(query_texts=[probe], n_results=n, include=[])["ids"][0])
    owner = next((m.get("user_id") for m in metas
                  if isinstance(m, dict) and m.get("user_id") and not str(m["user_id"]).startswith("demo_")), None)
    filtered = -1
    if owner:
        filtered = len(new.query(query_texts=[probe], n_results=min(6, n), where={"user_id": owner},
                                 include=[])["ids"][0])
    roundtrip = True   # vacuous when no row carries metadata (chroma rejects an empty dict)
    probe_ix = next((i for i, m in enumerate(metas) if isinstance(m, dict) and m), None)
    if probe_ix is not None:
        original = dict(metas[probe_ix])
        new.update(ids=[ids[probe_ix]], metadatas=[dict(original)])   # the historical crash path
        got = (new.get(ids=[ids[probe_ix]], include=["metadatas"]).get("metadatas") or [None])[0]
        roundtrip = dict(got or {}) == original
    ok = count == n and reach == n and filtered != 0 and roundtrip
    return {"ok": ok, "count": count, "expected": n, "unfiltered_reach": reach,
            "filtered_owner": filtered, "metadata_roundtrip": roundtrip}


def compact_drawers_index_sync(
    data_dir: str = _MEMPALACE_DATA,
    *,
    backups_dir: str | None = None,
    probe: str = _COMPACT_PROBE,
    client: Any | None = None,
    ef: Any | None = None,
    drain_s: float | None = None,
) -> dict[str, Any]:
    """Rebuild ``mempalace_drawers`` from its stored embeddings, in-process, no restart.

    Runs in ONE executor thread with the maintenance gate cleared. Order: export (count +
    dims verified) → JSON export + tar backup of the palace under ``backups_dir`` → delete →
    create in the SAME space → re-add in batches → verify → reopen the gate. Any failure
    BEFORE the delete aborts with no change; the delete itself is inside the guarded region
    (``changed`` is marked before it is attempted — chroma removes the segments before the
    sysdb row, so a delete that raises may already have destroyed data): any failure from
    there on restores from the export (delete-if-listed + create + re-add) and raises
    :class:`IndexCompactionError` whose ``report`` says ``restored``. The gate reopens ONLY
    after a verified restore (count == exported); otherwise it stays closed
    (``maintenance_state()``, ``status="blocked"``) for operator recovery. Every failure is
    structured: ``report["status"]`` is ``busy`` (lock / drain), ``aborted`` (no change),
    ``restored`` or ``blocked``. Nothing is re-embedded. Logs ``MEMORY_INDEX_COMPACT``.
    """
    import tarfile

    from memory_index_health import index_health

    if getattr(_OP_THREAD, "depth", 0):
        raise IndexCompactionError(
            "compaction must not run under a collection lease (it would wait for itself) — "
            "schedule it outside _run_sync", {"changed": False, "status": "aborted"})
    if _MAINTENANCE_BLOCKED is not None:
        raise IndexCompactionError(
            f"maintenance gate is closed after a failed restore — {_MAINTENANCE_BLOCKED.get('reason')}",
            {"changed": False, "status": "blocked", **maintenance_state()})
    if not _COMPACT_LOCK.acquire(blocking=False):
        raise IndexCompactionError("a compaction is already running", {"changed": False, "status": "busy"})
    t0 = time.monotonic()
    report: dict[str, Any] = {"changed": False, "restored": False, "space": None, "status": "aborted"}
    reopen = True
    try:
        try:
            before = index_health(data_dir)
            report["before"] = {k: before.get(k) for k in ("live_rows", "elements_added", "tombstone_ratio", "fresh")}
        except Exception as exc:  # noqa: BLE001 — health is informational here
            report["before"] = {"error": f"{type(exc).__name__}: {exc}"}
        client = client if client is not None else _palace_client(data_dir)
        ef = ef if ef is not None else _drawers_embedding_function()
        backups = Path(backups_dir or _COMPACT_BACKUPS_DIR)
        _MAINTENANCE_OPEN.clear()
        try:
            drain = _MAINTENANCE_DRAIN_S if drain_s is None else drain_s
            t_drain = time.monotonic()
            if not _drain_collection_ops(drain):
                report["status"] = "busy"
                raise IndexCompactionError(
                    f"could not drain {_ACTIVE_OPS} in-flight collection operation(s) within {drain:g}s"
                    " — aborted before any change", report)
            report["drain_seconds"] = round(time.monotonic() - t_drain, 3)
            col = client.get_collection(_DRAWERS_COLLECTION, embedding_function=ef)
            space = _collection_space(col)
            report["space"] = space
            rows = col.get(include=["embeddings", "documents", "metadatas"])
            ids = _as_list(rows.get("ids"))
            # chroma 1.5.x returns the embeddings as a NumPy ndarray: iterate, never truth-test.
            embs = [[] if e is None else [float(x) for x in e] for e in _as_list(rows.get("embeddings"))]
            rows = {"ids": ids, "embeddings": embs,
                    "documents": _as_list(rows.get("documents")), "metadatas": _as_list(rows.get("metadatas"))}
            n, count = len(ids), int(col.count())
            dim_set = {len(e) for e in embs}
            dim = next(iter(dim_set)) if len(dim_set) == 1 else None
            report.update(rows=n, count=count, dims=(dim if dim is not None else sorted(dim_set)))
            if n == 0 or n != count or len(embs) != n or not dim:
                raise IndexCompactionError(
                    f"export incomplete (rows={n} count={count} dims={report['dims']}) — aborted before any change",
                    report)
            backups.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            export = backups / f"mempalace-drawers-export-{ts}.json"
            with open(export, "w", encoding="utf-8") as fh:
                json.dump({"space": space, **rows}, fh)
            tar_path = backups / f"mempalace-pre-compact-{ts}.tar"
            palace_dir = os.path.expanduser(data_dir)
            with tarfile.open(tar_path, "w") as tf:
                tf.add(palace_dir, arcname=os.path.basename(os.path.normpath(palace_dir)))
            report.update(export=str(export), backup_tar=str(tar_path))

            # ── destructive region: from here on the store may have changed ──────────────
            report["changed"] = True   # BEFORE the delete: it can raise with the segments already gone
            try:
                client.delete_collection(_DRAWERS_COLLECTION)
                new = _rebuild_drawers(client, ef, space, rows)
                verify = _verify_rebuilt_drawers(new, rows, probe)
                report["verify"] = verify
                if not verify["ok"]:
                    raise MemoryServiceError(f"verification failed: {verify}")
            except Exception as exc:  # noqa: BLE001 — delete/rebuild/verify: put the rows back
                if _restore_drawers(client, ef, space, rows, report):
                    report["status"] = "restored"
                else:
                    report["status"] = "blocked"
                    reopen = False
                    _block_maintenance(
                        f"{type(exc).__name__}: {exc}; restore failed: {report.get('restore_error')}", report)
                report.update(maintenance_state())
                raise IndexCompactionError(
                    f"{type(exc).__name__}: {exc} (after delete; restored={report['restored']})", report
                ) from exc
            # Verified rebuild: the old collection's pages are on the SQLite freelist (every drawer's text, readable)
            # and its HNSW directory is still on disk (chroma never removes it). Erase both while the gate is shut.
            report["residue"] = _scrub_residue_in_window(data_dir)
        except IndexCompactionError:
            raise
        except Exception as exc:  # noqa: BLE001 — outside the destructive region: structured
            tail = "unexpected error after the delete" if report["changed"] else "aborted before any change"
            raise IndexCompactionError(f"{type(exc).__name__}: {exc} — {tail}", report) from exc
        finally:
            if reopen:
                _MAINTENANCE_OPEN.set()
        report.update(ok=True, status="ok", elements_added=n, seconds=round(time.monotonic() - t0, 2))
        logger.warning(
            "MEMORY_INDEX_COMPACT before=%s after=%s elements_added=%s seconds=%s space=%s backup=%s",
            (report.get("before") or {}).get("elements_added"), n, n, report["seconds"], space, tar_path,
        )
        return report
    except IndexCompactionError as exc:
        exc.report["seconds"] = round(time.monotonic() - t0, 2)
        logger.error("MEMORY_INDEX_COMPACT failed status=%s changed=%s restored=%s error=%s",
                     exc.report.get("status"), exc.report.get("changed"), exc.report.get("restored"), exc)
        raise
    finally:
        _COMPACT_LOCK.release()

# ── Physical erasure: forgotten text must not survive on disk (ZOE_MEMORY_PHYSICAL_ERASE) ──────────
# A chroma ``delete`` removes the row and nothing else: the text stays in SQLite free pages and in-page
# slack, in the FTS5 trigram index, in the ``embeddings_queue`` write-ahead log and (heap residue) in HNSW
# files (see ``memory_residue``). The erase below is the part Chroma does not do. It runs INSIDE the
# maintenance gate (collection ops drained, new openers blocked) because ``VACUUM`` needs the file to itself.
_ERASE_MAX_NEEDLES = 24          # verification scans every file once per needle: bound the cost
_ERASE_NEEDLE_BYTES = 40         # a head window of each forgotten text; >= 12 bytes or it is noise


def physical_erase_enabled() -> bool:
    """Flag ``ZOE_MEMORY_PHYSICAL_ERASE`` (default ON; per-call read). OFF restores the old behaviour:
    the API delete only, text left on disk."""
    return os.environ.get("ZOE_MEMORY_PHYSICAL_ERASE", "1").strip().lower() not in ("0", "false", "no", "off")


def heap_scrub_active() -> bool:
    """True when THIS process scrubs its heap (``ZOE_MEMORY_HEAP_SCRUB=1`` ran ``mallopt(M_PERTURB)`` when the
    palace opened, or ``MALLOC_PERTURB_`` is set): what keeps the host's own HNSW writes free of the remains of
    freed text (see ``memory_residue``)."""
    import memory_residue
    return memory_residue.heap_scrub_on()


@contextlib.contextmanager
def _maintenance_window(drain_s: float | None = None, lock_wait_s: float = 60.0):
    """Exclusive access to the palace for a file-level operation: wait for any running compaction, close the
    gate, drain in-flight collection ops, yield, reopen. Raises :class:`MemoryServiceError` (nothing changed)
    when the gate is blocked, a compaction does not finish, or the ops do not drain."""
    if getattr(_OP_THREAD, "depth", 0):
        raise MemoryServiceError("a maintenance window must not be opened under a collection lease")
    if _MAINTENANCE_BLOCKED is not None:
        raise MemoryServiceError(f"maintenance gate is closed — {_MAINTENANCE_BLOCKED.get('reason')}")
    if not _COMPACT_LOCK.acquire(timeout=lock_wait_s):
        raise MemoryServiceError("a compaction is already running")
    try:
        _MAINTENANCE_OPEN.clear()
        try:
            drain = _MAINTENANCE_DRAIN_S if drain_s is None else drain_s
            if not _drain_collection_ops(drain):
                raise MemoryServiceError(
                    f"could not drain {_ACTIVE_OPS} in-flight collection operation(s) within {drain:g}s")
            yield
        finally:
            if _MAINTENANCE_BLOCKED is None:
                _MAINTENANCE_OPEN.set()
    finally:
        _COMPACT_LOCK.release()


def _scrub_residue_in_window(data_dir: str, *, vacuum: bool = True) -> dict[str, Any]:
    """The file-level erase, for a caller that ALREADY holds the gate: SQLite scrub (queue blank + purge, FTS5
    rebuild, VACUUM) + orphan HNSW segment directories. Never raises: the report carries ``error``."""
    import memory_residue

    out: dict[str, Any] = {"enabled": physical_erase_enabled()}
    if not out["enabled"]:
        return out
    palace = os.path.expanduser(data_dir)
    try:
        out["sqlite"] = memory_residue.scrub_sqlite(palace, vacuum=vacuum)
        out["orphans_removed"] = len(memory_residue.remove_orphan_segments(palace))
    except Exception as exc:  # noqa: BLE001 - a scrub failure must never fail the delete / compaction
        out["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("MEMORY_PHYSICAL_ERASE scrub failed (%s) — forgotten text may remain on disk",
                       type(exc).__name__)
    return out


def _needles_for_texts(texts: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in texts:
        head = (t or "").strip()[:_ERASE_NEEDLE_BYTES]
        if len(head.encode("utf-8")) >= 12:
            seen.setdefault(head, None)
    return list(seen)[:_ERASE_MAX_NEEDLES]


def _verify_residue(data_dir: str, needles: list[str]) -> dict[str, Any]:
    """Count (never print) how many of the forgotten text heads are still in the palace's files, in place."""
    import memory_residue

    if not needles:
        return {"checked": False, "reason": "no needles"}
    rep = memory_residue.scan_palace(os.path.expanduser(data_dir), needles, copy=False)
    sqlite_hits = index_hits = 0
    for r in rep["tokens"].values():
        for f, n in r["files"].items():
            if f.startswith("chroma.sqlite3"):
                sqlite_hits += n
            else:
                index_hits += n
    return {"checked": True, "needles": len(needles), "sqlite_hits": sqlite_hits, "index_hits": index_hits,
            "clean": sqlite_hits == 0 and index_hits == 0, "seconds": rep["seconds"]}


def erase_residue_sync(data_dir: str = _MEMPALACE_DATA, *, needles: Iterable[str] = (), rebuild: bool = True,
                       drain_s: float | None = None) -> dict[str, Any]:
    """Physically erase forgotten text, then PROVE it. One call for every hard-delete / forget path.

    1. gate shut -> :func:`_scrub_residue_in_window` (queue blank + purge, FTS5 rebuild, ``VACUUM``, orphan
       HNSW dirs) -> verify the live files for ``needles`` (head windows of the forgotten texts, in memory only);
    2. text still found in an HNSW file and ``rebuild`` and ``ZOE_MEMORY_INDEX_COMPACT`` is on: rebuild the
       drawers index (``compact_drawers_index_sync``, which scrubs again) and verify once more.

    Never raises; ``report["ok"]`` is False (and a WARNING is logged, counts only) when text is still found.
    ``heap_scrub_active`` says whether the host's allocator keeps its own future index writes clean."""
    t0 = time.monotonic()
    nd = list(needles)
    report: dict[str, Any] = {"enabled": physical_erase_enabled(), "heap_scrub_active": heap_scrub_active()}
    if not report["enabled"]:
        report["ok"] = None
        return report
    try:
        with _maintenance_window(drain_s):
            report["scrub"] = _scrub_residue_in_window(data_dir)
            report["verify"] = _verify_residue(data_dir, nd)
        verify = report["verify"]
        if verify.get("checked") and verify.get("index_hits") and rebuild and index_compaction_enabled():
            try:
                comp = compact_drawers_index_sync(data_dir, drain_s=drain_s)
                report["rebuild"] = {k: v for k, v in comp.items() if k in ("status", "rows", "seconds")}
            except IndexCompactionError as exc:
                report["rebuild"] = {"status": exc.report.get("status"), "error": str(exc)[:200]}
            report["verify_after_rebuild"] = _verify_residue(data_dir, nd)
            verify = report["verify_after_rebuild"]
        if "error" in report["scrub"]:
            report["ok"] = False
        else:
            report["ok"] = bool(verify["clean"]) if verify.get("checked") else None
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["ok"] = False
    report["seconds"] = round(time.monotonic() - t0, 3)
    if report.get("ok") is False:
        logger.warning("MEMORY_PHYSICAL_ERASE incomplete: %s (counts only; text may remain on disk)",
                       {k: report.get(k) for k in ("error", "verify", "verify_after_rebuild")})
    else:
        logger.info("MEMORY_PHYSICAL_ERASE ok=%s seconds=%s heap_scrub_active=%s", report.get("ok"),
                    report["seconds"], report["heap_scrub_active"])
    return report


_MEMORY_SCOPE_TO_VISIBILITY = {
    "personal": "personal",
    "shared": "family",
    "ambient": "personal",
    "system": "personal",
    "project": "personal",
}


def _metadata_value(value: Any) -> str | int | float | bool:
    if isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# --- Increment 2a: hybrid retrieval (flag-gated, default OFF) --------------
#
# When ``ZOE_HYBRID_RETRIEVAL_ENABLED`` is truthy, ``_semantic_search`` blends
# three cheap, O(candidates) boosts on top of the existing semantic+hotness
# score before the final sort. All boosts are additive, small, and bounded so
# semantic relevance keeps dominating. OFF is a true no-op: the boost term is
# not computed and the ordering is byte-for-byte the pre-2a behaviour.
#
# Weights are named constants so they are easy to tune later. No LLM, no
# embedder reload, no extra network, no new deps.
_HYBRID_KEYWORD_WEIGHT = 0.50      # bounded lexical/keyword-overlap boost (primary miss-fix)
_HYBRID_RECENCY_WEIGHT = 0.05      # mild recency nudge; must never dominate relevance
_HYBRID_RECENCY_HALFLIFE_DAYS = 30.0  # recency boost half-life
_HYBRID_PREFERENCE_WEIGHT = 0.05   # preference/importance nudge

# memory_type values treated as preference/important for the preference boost.
_HYBRID_PREFERENCE_TYPES = frozenset(
    {"preference", "approval", "emotional_moment", "person", "recurring_task"}
)

# Tokens ignored for keyword overlap: interrogatives / filler that carry no
# lexical signal about the target fact (e.g. "what is my dad name").
_HYBRID_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "am", "do", "does",
        "did", "what", "whats", "who", "whos", "whom", "whose", "which", "when",
        "where", "why", "how", "my", "me", "i", "you", "your", "of", "to", "in",
        "on", "at", "for", "and", "or", "it", "its", "that", "this", "tell",
        "about", "name", "names",
    }
)

_HYBRID_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HYBRID_RECENCY_LAMBDA = math.log(2) / _HYBRID_RECENCY_HALFLIFE_DAYS


def _hybrid_retrieval_enabled() -> bool:
    """Cheap per-call read of the hybrid-retrieval flag (default OFF).

    Read from the environment each call (same idiom as the other
    ``os.environ.get`` flags in this module). OFF must be a true no-op, so this
    stays a plain, cheap truthiness check with no side effects.
    """
    return os.environ.get("ZOE_HYBRID_RETRIEVAL_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# --- Increment 2b: graph-adjacency recall boost (flag-gated, default OFF) ----
#
# A 7th additive blend signal that lifts facts stored under people who are
# *graph-adjacent* — in the relationship graph — to the person a recall query is
# about (e.g. surfacing "my sister's husband's job" when the fact lives on the
# husband node, two hops out, and vector search alone misses it). Gated behind
# BOTH ``ZOE_RELATIONSHIP_GRAPH_ENABLED`` (the graph feature flag) AND this
# ``ZOE_GRAPH_RECALL_BOOST`` sub-flag (default OFF) so the graph endpoint and the
# recall boost flip independently. OFF is a true no-op: the neighbourhood is
# never fetched (``depth_by_pid`` stays empty) and the term is neither computed
# nor added, so ordering is byte-for-byte the pre-2b behaviour. No new model,
# embedder, or network — one bounded BFS SQL query on person turns, resolved off
# the hot path in the async ``search`` before ``_run_sync``.
_GRAPH_RECALL_WEIGHT_DEFAULT = 0.30  # bounded adjacency boost; strong secondary to keyword

# Name candidates pulled from a recall query to seed the graph boost. This is a
# cheap regex, NOT a new NLU model: it only feeds the *existing*
# ``person_extractor._resolve_person_uuid`` name→people.id resolver. A trailing
# possessive ("Alice's" / "Alice’s") is stripped so "what does Alice's husband
# do" yields ["Alice"]. Two tiers: a precise capitalized pass first (proper-noun
# chat/typed queries), then a case-insensitive fallback so lowercase voice/STT
# transcripts ("what is alice's husband's job") still surface a candidate — the
# resolver returns None for any non-name token, so the extra tokens are harmless.
_QUERY_NAME_RE = re.compile(r"\b([A-Z][a-zA-Z'’-]+)\b")
_QUERY_NAME_RE_CI = re.compile(r"\b([a-zA-Z][a-zA-Z'’-]+)\b")
_MAX_NAME_CANDIDATES = 8  # bound the resolver round-trips on a person turn

# Capitalized sentence-openers / interrogatives the name regex over-captures but
# which are never a person's name. Lower-cased membership test (cf.
# person_extractor._NON_NAME_TOKENS); deliberately conservative so real given
# names are never dropped.
_QUERY_NAME_STOPWORDS = frozenset({
    "what", "whats", "who", "whos", "whose", "which", "when", "where", "why",
    "how", "tell", "does", "did", "do", "is", "are", "was", "were", "the", "a",
    "an", "my", "me", "i", "you", "your", "he", "she", "they", "we", "it",
    "him", "her", "them", "us", "this", "that", "these", "those", "and", "or",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})


def _graph_recall_boost_enabled() -> bool:
    """Cheap per-call read of the graph-recall sub-flag (default OFF).

    Mirrors the ``_hybrid_retrieval_enabled`` idiom. This is only half the gate;
    the caller also requires ``relationship_graph.relationship_graph_enabled()``.
    """
    return os.environ.get("ZOE_GRAPH_RECALL_BOOST", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _candidate_person_names(query: str) -> list[str]:
    """Ordered, de-duped capitalized name candidates from a recall query.

    Not NLU — a regex over capitalized tokens minus a conservative stop-set,
    used only to feed the existing ``_resolve_person_uuid`` resolver. Returns
    ``[]`` when nothing plausible is present (⇒ no graph boost, a no-op).
    """
    if not query:
        return []

    def _collect(pattern: re.Pattern[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for tok in pattern.findall(query):
            # Drop a trailing possessive ("Alice's"→"Alice", "Chris'"→"Chris")
            # while leaving internal apostrophes ("O'Brien") intact, then trim.
            name = re.sub(r"['’]s?$", "", tok).strip("'’-")
            if len(name) < 2 or name.lower() in _QUERY_NAME_STOPWORDS:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
            if len(out) >= _MAX_NAME_CANDIDATES:
                break
        return out

    # Precise capitalized pass first; fall back to any-case only if it finds
    # nothing (a fully lowercase voice/STT transcript).
    names = _collect(_QUERY_NAME_RE)
    return names if names else _collect(_QUERY_NAME_RE_CI)


def _hybrid_tokens(text: str) -> set[str]:
    """Cheap normalized token set: lowercase alnum tokens minus stopwords."""
    if not text:
        return set()
    return {
        tok
        for tok in _HYBRID_TOKEN_RE.findall(text.lower())
        if tok not in _HYBRID_STOPWORDS and len(tok) > 1
    }


def _hybrid_keyword_overlap(query_tokens: set[str], doc: str) -> float:
    """Bounded [0,1] lexical overlap of query terms against the fact text.

    Fraction of (content) query tokens that appear in the fact text, matched
    either as a whole token or as a substring (so "dad" matches "dad's").
    Cheap: O(query_tokens) per candidate, no TF-IDF, no model loads.
    """
    if not query_tokens:
        return 0.0
    doc_lower = doc.lower()
    doc_tokens = _hybrid_tokens(doc)
    hits = 0
    for tok in query_tokens:
        if tok in doc_tokens or tok in doc_lower:
            hits += 1
    return hits / len(query_tokens)


def _parse_aware_datetime(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    elif isinstance(value, datetime.date):
        # Legacy date-only expiries mean "valid through this UTC day".
        dt = datetime.datetime.combine(value, datetime.time.max)
    elif isinstance(value, str):
        raw = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                legacy_date = datetime.date.fromisoformat(raw)
            except ValueError:
                return None
            # Date-only legacy strings expire after the calendar day ends in UTC.
            dt = datetime.datetime.combine(legacy_date, datetime.time.max)
        else:
            try:
                dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _normalize_expires_at(expires_at: str) -> str:
    dt = _parse_aware_datetime(expires_at)
    if dt is None:
        raise MemoryServiceError("expires_at must be ISO-8601")
    return dt.isoformat().replace("+00:00", "Z")


def _memory_expired(expires_at: Any, now: datetime.datetime | None = None) -> bool:
    expires_dt = _parse_aware_datetime(expires_at)
    if expires_dt is None:
        logger.warning("memory_service: invalid expires_at metadata kept active: %r", expires_at)
        return False
    now_dt = now or datetime.datetime.now(datetime.timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=datetime.timezone.utc)
    return expires_dt <= now_dt.astimezone(datetime.timezone.utc)


_BLOCKED_READ_STATUSES = {"archived", "rejected", "superseded", "pending", "disputed"}

# Cap for the in-memory idempotency fast-path cache (_seen_keys). Durable dedup is
# guaranteed by the deterministic mem_id + upsert, so this cache only avoids redundant
# write attempts; evicting the oldest key at worst lets one duplicate reach the
# idempotent write path. Bounded to keep memory flat over the process lifetime.
_SEEN_KEYS_MAX = 50_000

# Cap for the per-row distinct-query hash blob (_query_hashes metadata). The blob is
# the dedup oracle and never evicts; once it is full both the blob and the derived
# unique_query_count freeze, so churned-out queries can't re-count and inflate the
# signal. unique_query_count is thus monotonic and == min(distinct queries, this cap).
_MAX_QUERY_HASHES = 256


class _BoundedKeySet:
    """Insertion-ordered set with a hard cap; oldest entries evict first (FIFO).

    Drop-in for the ``in`` / ``.add`` usage of a plain set, but bounded so it cannot
    grow without limit across the process lifetime. Each key may carry an opaque
    value (e.g. its owner); ``on_evict(key, value)`` fires for every evicted entry
    so side indexes can stay in sync.
    """

    def __init__(
        self,
        maxlen: int = _SEEN_KEYS_MAX,
        on_evict: Optional[Callable[[str, Any], None]] = None,
    ):
        self._maxlen = maxlen
        self._on_evict = on_evict
        self._items: "OrderedDict[str, Any]" = OrderedDict()

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def add(self, key: str, value: Any = None) -> None:
        if key in self._items:
            return
        self._items[key] = value
        while len(self._items) > self._maxlen:
            evicted_key, evicted_value = self._items.popitem(last=False)
            if self._on_evict is not None:
                self._on_evict(evicted_key, evicted_value)

    def __len__(self) -> int:
        return len(self._items)

    def discard(self, key: str) -> None:
        self._items.pop(key, None)



#: ``ZOE_RECALL_DURABLE_NO_DECAY`` (default ON): a row the owner stated does not age out of search ranking
_NO_DECAY_ENV = "ZOE_RECALL_DURABLE_NO_DECAY"
#: types that are moments, not facts: they keep decaying whoever wrote them
_DECAYING_TYPES = frozenset({"emotional_moment", "state_change", "episode", "moment"})


def _durable_user_fact(metadata: Mapping[str, Any], text: str = "") -> bool:
    """Is this row a durable fact the OWNER stated (class ``user_stated`` or above, never an emotional moment), so
    its age must not lower its rank in a semantic search? Never raises: a row that cannot be classified decays as before."""
    if os.environ.get(_NO_DECAY_ENV, "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        if str(metadata.get("memory_type") or "").lower() in _DECAYING_TYPES:
            return False
        return _auth.row_rank(metadata, text) >= _auth.USER_RANK
    except Exception:  # noqa: BLE001
        return False


def _memory_visible_to_user(metadata: Mapping[str, Any], user_id: str) -> bool:
    """Return True only for the caller's personal rows or shared family rows."""

    caller = str(user_id or "").strip().lower()
    if is_guest_memory_user(caller):
        return False

    visibility = str(metadata.get("visibility") or "").strip().lower()
    if visibility == "family":
        return True
    uid = str(metadata.get("user_id") or "").strip().lower()
    wing = str(metadata.get("wing") or "").strip().lower()
    return bool(caller and ((uid and uid == caller) or (wing and wing == caller)))


def is_guest_memory_user(user_id: str | None) -> bool:
    """Return True for unauthenticated identities that must not receive prompt memory."""

    return str(user_id or "").strip().lower() in {"", "guest", "anonymous", "voice-guest"}


def _memory_status_visible(metadata: Mapping[str, Any]) -> bool:
    status = str(metadata.get("status", "approved") or "approved").strip().lower()
    return status not in _BLOCKED_READ_STATUSES


def _scope_visibility(scope: Any | None) -> str:
    if scope is None:
        return "personal"
    scope_value = str(scope)
    if not scope_value.strip():
        raise MemoryServiceError("memory scope cannot be blank")
    if scope_value not in _MEMORY_SCOPE_TO_VISIBILITY:
        raise MemoryServiceError(f"unsupported memory scope: {scope_value}")
    return _MEMORY_SCOPE_TO_VISIBILITY[scope_value]


_TOMBSTONE_MAX_HASHES = 200   # keeps the JSON inside the audit row's 4,000-char cap


def _delete_tombstone_id(user_id: str, ids: list[str]) -> str:
    """Stable, content-free id for a ``delete_user`` audit row."""
    basis = f"{user_id}|{len(ids)}|{time.time_ns()}".encode()
    return f"delete_user:{hashlib.sha256(basis).hexdigest()[:16]}"


def _delete_tombstone_body(ids: list[str]) -> dict[str, Any]:
    """What a hard delete records about the rows it targets: a count and short hashes of the
    row ids (themselves text-derived hashes). Never the text, never the metadata."""
    hashes = [hashlib.sha256(i.encode()).hexdigest()[:8] for i in ids[:_TOMBSTONE_MAX_HASHES]]
    return {"rows_targeted": len(ids), "id_hashes": hashes, "truncated": len(ids) > len(hashes)}


def _memory_id(user_id: str, text: str, metadata: Mapping[str, Any]) -> str:
    """Stable row id; include durable identity so same text can exist in distinct lanes."""

    identity = {
        "user_id": user_id,
        "text": text,
        "source": metadata.get("source", ""),
        "scope": metadata.get("scope", ""),
        "visibility": metadata.get("visibility", ""),
        "memory_type": metadata.get("memory_type", ""),
        "entity_type": metadata.get("entity_type", ""),
        "entity_id": metadata.get("entity_id", ""),
    }
    basis = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return f"zoe_{user_id}_{hashlib.sha256(basis).hexdigest()[:24]}"


def _invalidate_agent_user_facts_cache(user_id: str) -> None:
    try:
        from zoe_agent import _invalidate_user_facts_cache  # type: ignore[import]

        _invalidate_user_facts_cache(user_id)
    except Exception as exc:
        logger.debug("memory_service: user facts cache invalidation skipped: %s", exc)


def _stamp_claim(md: dict[str, Any], claim: Any) -> None:
    """Store the extractor's claim row on the row being written (``metadata["claim"]``, JSON): polarity, modality and
    tense decided ONCE, with the owner's verbatim quote. Nothing when there is no valid claim or the flag is off."""
    if not claim:
        return
    try:
        import structural_claims as sc

        if not sc.active():
            return
        parsed = claim if isinstance(claim, sc.Claim) else sc.parse_claim(claim)[0]
        if parsed is None:
            return
        # the quote and the value are slices of the owner's turn: they pass the SAME PII scrub the evidence excerpt does, and a claim
        # that would carry anything the scrub touches is not stored at all (the fact beside it was scrubbed separately)
        for field_ in (parsed.quote, parsed.obj):
            scrubbed, reject = scrub_pii(field_)
            if reject or scrubbed != field_:
                return
        md["claim"] = parsed.to_json()
    except Exception:  # noqa: BLE001 - a stamp never costs a write
        pass


def _promote_event_metadata(md: dict[str, Any], extra: dict[str, Any]) -> None:
    for key in ("event_id", "evidence_refs", "relationships", "supersedes", "retention_policy"):
        value = extra.get(key)
        if value is not None:
            md[key] = _metadata_value(value)


@dataclass(frozen=True)
class MemoryRef:
    """Opaque reference returned by MemoryService."""
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


# Hard cap on the rows a time-bounded recency query may return (see
# MemoryService._recent_read). Well above any real user's 72 h capture volume.
_RECENT_SCAN_CAP = 300


def memory_affect(ref: MemoryRef) -> str:
    """The first-person feeling captured with this row (turn digest's
    ``affect`` metadata, stored as ``candidate_affect``), or "". Sanitised to a
    short lowercase word so it is safe to render inline."""
    raw = str((ref.metadata or {}).get("candidate_affect") or "").strip().lower()
    return raw if re.fullmatch(r"[a-z][a-z ]{0,23}", raw) else ""


def is_emotional_memory(ref: MemoryRef) -> bool:
    """An `emotional_moment` row, or a row whose text carries an emotional cue
    ("I'm pretty anxious about…") — the 4B extractor often stores a worry as a
    plain fact, so the type alone would miss it. Single definition, shared with
    the for-prompt composer."""
    meta = ref.metadata or {}
    if str(meta.get("memory_type")) == "emotional_moment" or memory_affect(ref):
        return True
    from memory_gate import message_needs_emotional_recall

    return message_needs_emotional_recall(ref.text or "")


# PII scrubber
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_TOTP_RE = re.compile(r"(?:\b|:\s*)(\d{6})\b")
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SECRET_LABEL_RE = re.compile(
    r"(?i)(?:^|\W)(password|passcode|passphrase|pin|api[\s_-]?key|token|secret|auth[\s_-]?token)\b",
)
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)?PRIVATE KEY-----")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_BANKING_CONTEXT_RE = re.compile(r"(?i)\b(iban|bank|account|swift|bic|transfer)\b")


def _luhn_valid(digits: str) -> bool:
    digits = re.sub(r"\D", "", digits)
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        n = int(d)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def scrub_pii(text: str) -> tuple[str, Optional[str]]:
    """Return (possibly-redacted-text, reject_reason_or_None)."""
    if not text:
        return text, None

    for match in _CC_RE.finditer(text):
        if _luhn_valid(match.group(0)):
            return text, "luhn_cc"

    if _SSN_RE.search(text):
        return text, "ssn"

    if _SECRET_LABEL_RE.search(text):
        if _TOTP_RE.search(text):
            return text, "totp"

    if _AWS_KEY_RE.search(text):
        return text, "aws_key"

    if _PEM_RE.search(text):
        return text, "pem"

    if _JWT_RE.search(text):
        return text, "jwt"

    if _IBAN_RE.search(text) and _BANKING_CONTEXT_RE.search(text):
        return text, "iban"

    redacted = re.sub(
        r"(?i)\b(password|passcode|passphrase|pin|api[\s_-]?key|token|secret|auth[\s_-]?token)\b(\s*(?:is|=|:)\s*)\S+",
        r"\1\2[REDACTED]",
        text,
    )
    return redacted, None


#: Cap for the verbatim evidence stored beside a fact (metadata
#: ``source_excerpt``). The write boundary owns BOTH the scrub and the cut, so
#: producers pass the whole utterance. Sized above the 220-char utterance cut
#: hindsight_retain_candidates uses so its appended "\nEvidence: <refs>" survives.
_SOURCE_EXCERPT_MAX_CHARS = 400


def scrub_source_excerpt(text: Optional[str], *, limit: int = _SOURCE_EXCERPT_MAX_CHARS) -> Optional[str]:
    """PII-scrub THEN cap an evidence excerpt; None when there is nothing safe to keep.

    Scrub runs on the whole input before the cut, so truncation can never slice a
    card number below the Luhn check and store its digits. A hard reject (card,
    SSN, key, PEM, JWT…) drops the EXCERPT only — the fact it sits beside was
    scrubbed separately and is not affected.
    """
    raw = (text or "").strip() if isinstance(text, str) else ""
    if not raw:
        return None
    scrubbed, reject = scrub_pii(raw)
    if reject:
        return None
    return scrubbed[:limit].rstrip() or None


async def _user_opted_out(user_id: str) -> bool:
    """Per-user ``memory_opt_out`` preference. Fail-open: a lookup failure (no
    pool in tests, DB blip) returns False — a preference read must never lose a fact."""
    try:
        import user_prefs
        return await user_prefs.is_memory_opted_out(user_id)
    except Exception as exc:
        logger.debug("memory opt-out lookup failed (%s) — treating as opted in", exc)
        return False


def _identity_assertion_blocked(text: str, *, user_id: str, source: str) -> bool:
    """True when an AUTOMATIC writer is asserting the user's own name. "Automatic" is
    everything that is not an allow-listed direct source (``identity_facts.DIRECT_USER_SOURCES``:
    ``voice_fact``, ``brain_tool``, ``review_ui``, ``proposal``, the audit tool) - so a label
    nobody anticipated (``chat_regex_fallback``) is walled by default. Logs a label only (never the
    text). Never raises."""
    try:
        from identity_facts import is_automatic_source, is_user_name_assertion

        if not is_automatic_source(source, owner=user_id) or not is_user_name_assertion(text):
            return False
    except Exception:  # noqa: BLE001 — the guard must never break ingestion
        return False
    logger.info("IDENTITY_FACT_BLOCKED user=%s source=%s kind=name origin=automatic", user_id, source)
    return True


def _foreign_voice_reason(fact: str, evidence: Optional[str]) -> str:
    """``pasted_content`` / ``third_person_speech`` when a per-turn MODEL writer's ``fact`` rests on words that are
    not the owner's own voice (own_words), else "". The owner's own words supporting the fact always keep it.

    * a PASTED turn (an email, a ``system:`` line, a quoted instruction): a model writer's fact is kept only when
      the owner's OWN part of the turn supports it - the brain acting on "Remember that the PIN is ..." inside a
      pasted email is the confused deputy this closes;
    * another person's quoted speech ("Dana says: I live in Hobart"): the fact is refused only when the speech is
      what supports it (the owner's own part does not).
    Never raises; a turn that is the owner's alone returns "" without further work."""
    if not evidence or not fact:
        return ""
    try:
        own = _own_words.analyze(evidence)
        if not own.changed:
            return ""
        if own.has_own and _auth.supports(fact, own.text):
            return ""
        if own.pasted:
            return _own_words.PASTED_CONTENT
        if _auth.supports(fact, own.original):
            return _own_words.THIRD_PERSON_SPEECH
    except Exception:  # noqa: BLE001 - the guard must never break ingestion
        return ""
    return ""


class MemoryServiceError(Exception):
    """Raised for operational failures."""


class IndexCompactionError(MemoryServiceError):
    """A compaction that did not complete; ``report`` says whether anything changed."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = dict(report, ok=False, error=message)


class MemoryService:
    """The sole read/write surface for Zoe memory."""

    def __init__(self, data_dir: str = _MEMPALACE_DATA):
        self._data_dir = data_dir
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._seen_keys: _BoundedKeySet = _BoundedKeySet(
            on_evict=self._on_seen_key_evicted
        )
        # Tracks which idempotency keys belong to which user_id, purely so
        # delete_user() can invalidate a forgotten user's cached keys without
        # scanning the whole (hashed) _seen_keys set. Kept in exact sync with
        # _seen_keys: delete_user pops a user's whole entry on forget, and the
        # _seen_keys eviction callback (_on_seen_key_evicted) prunes each key
        # here as it ages out, so total size is hard-capped at _SEEN_KEYS_MAX.
        self._seen_keys_by_user: dict[str, set[str]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        # the last physical-erase report (counts only) of a hard delete / forget, for the operator endpoints
        self.last_erase_report: dict[str, Any] | None = None

    async def ingest(
        self,
        text: str,
        *,
        user_id: str,
        source: str,
        session_id: Optional[str] = None,
        user_turn_id: Optional[str] = None,
        memory_type: str = "fact",
        confidence: float = 0.7,
        status: str = "approved",
        tags: Optional[list[str]] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        source_excerpt: Optional[str] = None,
        scope: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        opt_out: bool = False,
        authority: Optional[str] = None,
        anchor_text: Optional[str] = None,
        turn_ref: Optional[str] = None,
        origin: Optional[str] = None,
        prompt_text: Optional[str] = None,
        speaker_verified: Optional[bool] = None,
        captured_at: Optional[str] = None,
        hold: Optional[str] = None,
        claim: Any = None,
        claim_siblings: Any = (),
    ) -> Optional[MemoryRef]:
        """Store a fact. Returns None when silently dropped.

        ``claim`` is the extractor's structured CLAIM ROW for this fact (``structural_claims``; a dict or ``Claim``): stored
        on the row as ``metadata["claim"]`` (polarity / modality / tense decided once, with the owner's verbatim quote),
        consulted by ``memory_authority.resolve_write`` per ``ZOE_STRUCTURAL_CLAIMS`` (shadow logs it beside the lexical
        decision; enforce lets it decide). ``claim_siblings`` are the other claims of the same owner sentence (a
        contrast's "not Y"). Without a claim the write is exactly what it was.

        ``hold`` (a short reason label) is the CALLER's verdict that the user's own words do not carry this fact (the nightly
        digest's observation gate, ``memory_authority.check_observation``): an ``approved`` write is stored ``pending`` - a
        candidate, never served - unless it disputes a row the user said, which stays the ``disputed`` candidate it always was
        (linked by ``contradicts_id``, so the owner is asked).

        ``captured_at`` (ISO-8601, optional) is for RESTORES only: the instant the fact was
        originally captured. It replaces "now" for ``added_at`` / ``added_ts`` / ``last_accessed``
        (and ``valid_from``, unless the person's own words state an earlier event time), so a restored July memory still answers
        "when did I tell you" with July. Unparseable, or more than 5 minutes in the future → ignored with a
        WARNING naming the value's shape (never the value); the row is then stored as captured now.

        When ``scope`` is None, ``metadata["scope"]`` is treated as the
        authoritative memory scope and is validated before any durable write.

        Provenance (``memory_authority``): the row's class is derived from ``source`` and,
        for a model-assisted writer, from ``anchor_text`` - the user's OWN turn text(s) the
        fact was mined from (user turns only; defaults to ``source_excerpt``).
        ``authority`` may only DOWNGRADE (``"inferred"``) or confirm (``"user_confirmed"``,
        user-class sources). ``origin`` names the WRITER when it is finer than the lane
        label ``source`` (``person_extractor_llm`` writes under ``source="conversation"``).
        A fact from a writer below the user classes that contradicts an approved row
        outranking it is stored as a ``disputed`` candidate (``contradicts_id``) instead -
        the returned ref says so. ``prompt_text`` is the assistant QUESTION a short elliptical
        answer responds to (context only, never evidence); ``speaker_verified=False`` is the
        voice lane's "the speaker-id did not confirm the member" (a self-fact becomes
        ``user_unverified``; ``None`` = the lane reports no verdict).
        """
        self._require(user_id, "user_id is required")
        assert_write_allowed(getattr(self, "_data_dir", _MEMPALACE_DATA), user_id, "ingest")
        if not text or not text.strip():
            raise MemoryServiceError("empty text")

        # Opt-out is enforced HERE, the one durable-write chokepoint, so every
        # automatic writer (per-turn extractor, turn digest, person extractors,
        # idle/nightly digest, consolidation, synthesis) honours it without each
        # caller remembering to. Explicit teach sources are never dropped.
        if source in MEMORY_OPT_OUT_SOURCES and (opt_out or await _user_opted_out(user_id)):
            self._bump("opt_out", source)
            return None

        # Identity is an ACCOUNT fact, never a recalled one: an automatic writer (regex,
        # digest, consolidation, person extractor…) must not store "the user's name is X"
        # — it mishears and mis-attributes (a speech-to-text fragment naming a third
        # person became the owner's name). identity_facts answers from the account.
        if _identity_assertion_blocked(text, user_id=user_id, source=source):
            self._bump("identity_drop", source)
            await self._third_person_candidate(
                text, user_id=user_id, source=source,
                anchor_text=anchor_text if anchor_text is not None else source_excerpt,
                session_id=session_id,
            )
            return None

        # Pasted content / another person's quoted speech (own_words; ZMB I1/I2/I4): a per-turn MODEL writer
        # (the brain's memory tool, the turn digest, the person LLM) may not store a fact whose only support is
        # words that are not the owner's own voice. The deterministic miners never reach here with such text.
        if ((source in _auth.MODEL_FROM_TURN_WRITERS or (origin or "") in _auth.MODEL_FROM_TURN_WRITERS)
                and source != _own_words.PASTE_NOTE_SOURCE):
            foreign = _foreign_voice_reason(
                text, anchor_text if anchor_text is not None else source_excerpt)
            if foreign:
                self._bump("guard_drop", source)
                _own_words.count_drops(source, None, (foreign,))
                logger.info("memory_service: ingest dropped - fact rests on %s (user=%s source=%s)",
                            foreign, user_id, source)
                return None

        # Consent gate (governance/emotional-safety-note.md section 6): a RECORD of how someone
        # seems is kept for consenting adult members only. Guests and children: never.
        if _auth.is_affective(memory_type, metadata) and not await self._affect_allowed(user_id):
            self._bump("affect_drop", source)
            logger.info("AFFECT_NOT_STORED writer=%s kind=emotional_moment", source)
            return None
        if _auth.carries_affect(metadata) and not await self._affect_allowed(user_id):
            # an ordinary fact that carries a feeling keeps the fact, not the feeling
            metadata = {k: v for k, v in (metadata or {}).items() if k not in _auth.AFFECT_KEYS}
            logger.info("AFFECT_STRIPPED writer=%s", source)

        scrubbed, reject = scrub_pii(text)
        if reject:
            self._bump("pii_reject", source)
            if _METRICS_OK:
                memory_pii_reject_count.labels(pattern=reject).inc()
            logger.info(
                "memory_service: ingest rejected by PII scrubber user=%s source=%s pattern=%s",
                user_id, source, reject,
            )
            return None

        # Forgetting. "forget everything about X" shadows X so a late extractor pass cannot
        # resurrect the name (ingest is the one durable-write chokepoint every lane funnels
        # through): the 300 s in-process tombstone is the fast path for in-flight writers, the
        # hashed ``memory_forgotten`` ledger is the durable one (the nightly digest re-reads the
        # forgotten turns hours later). BOTH apply to EVERY source except the person's own
        # explicit re-teach from a speaker the lane did not reject; ``brain_tool`` paraphrases
        # are not exempt - the brain acting on an explicit "remember ..." turn is labelled
        # ``origin="explicit_teach"`` by the caller. The evidence turn counts too: a fact mined
        # from a turn that names a forgotten entity is a forgotten turn, re-mined.
        reteach = _forgotten.is_explicit_reteach(
            origin or source, user_id=user_id, speaker_verified=speaker_verified)
        if not reteach:
            try:
                from memory_tombstones import matching_tombstone
                if matching_tombstone(user_id, scrubbed):
                    self._bump("tombstone_drop", source)
                    logger.info(
                        "memory_service: ingest dropped — mentions a just-forgotten "
                        "entity (user=%s source=%s)", user_id, source,
                    )
                    return None
            except Exception:
                pass  # the guard must never break ingestion
            try:
                evidence = anchor_text if anchor_text is not None else source_excerpt
                if await _forgotten.matches(user_id, scrubbed) or (
                        evidence and await _forgotten.matches(user_id, evidence)):
                    self._bump("forgotten_drop", source)
                    logger.info(
                        "memory_service: ingest dropped — names a forgotten entity (ledger) "
                        "(user=%s source=%s)", user_id, source,
                    )
                    return None
            except Exception:
                pass  # fail-open: a ledger blip must never lose a fact

        idem_key = self._idempotency_key(
            user_id,
            user_turn_id,
            scrubbed,
            memory_type=memory_type,
            scope=scope,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if idem_key in self._seen_keys:
            self._bump("dedup", source)
            if _METRICS_OK:
                memory_dedup_skip_count.inc()
            return None

        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if idem_key in self._seen_keys:
                self._bump("dedup", source)
                return None

            # Authority: who really wrote this, decided HERE from the writer + the user's
            # own turn text. An inferred fact that contradicts a protected row becomes a
            # pending candidate (the user's words stay approved; nothing is lost).
            writer = origin or source
            resolved = _auth.resolve_write(
                writer, scrubbed,
                anchor_text=anchor_text if anchor_text is not None else source_excerpt,
                claimed=authority, user_id=user_id, prompt_text=prompt_text,
                speaker_verified=speaker_verified, claim=claim, claim_siblings=claim_siblings,
            )
            _auth.note_structural(resolved, lane=writer, user_id=user_id)
            clash = None
            if resolved.power < _auth.RANK[_auth.OPERATOR] and status == "approved" and _auth.active():
                clash = await self._protected_conflict(user_id, scrubbed, resolved.power)
            if clash is not None:
                self._bump("authority_demote", source)
                _auth.log_blocked(writer, clash[1], user_id=user_id, action="ingest")
                if not _auth.enabled():
                    clash = None  # shadow: logged what WOULD have been held back
            if clash is not None:
                dup = await self._existing_candidate(user_id, scrubbed, clash[0].id)
                if dup is not None:
                    self._remember_seen_key(user_id, idem_key)
                    return dup

            # A speaker the panel did not verify cannot state the OWNER's facts (audit section 6.0, ZMB I2):
            # a self-assertion from `user_unverified` is a CANDIDATE the owner confirms (`pending`), never an
            # approved row and never served as "you told me". A dispute (above) keeps its own, stronger status.
            write_status = "disputed" if clash is not None else status
            if clash is None and status == "approved" and hold:
                self._bump("observation_hold", source)
                write_status = "pending"
            if (clash is None and status == "approved" and resolved.cls == _auth.USER_UNVERIFIED
                    and _auth.is_self_assertion(scrubbed)):
                self._bump("unverified_pending", source)
                logger.info("UNVERIFIED_SELF_FACT_PENDING writer=%s", writer)
                if _auth.enabled():
                    write_status = "pending"

            # Provenance (ZMB A3): a row written from a user turn says which words and which turn
            ev_excerpt, ev_turn_id = _auth.turn_evidence(
                writer, resolved, scrubbed, user_id=user_id,
                anchor_text=anchor_text, source_excerpt=source_excerpt, user_turn_id=user_turn_id)

            metadata = self._build_metadata(
                validity_span=self._validity_span(resolved, source_excerpt, scrubbed),
                user_id=user_id,
                source=source,
                session_id=session_id,
                user_turn_id=ev_turn_id,
                memory_type=memory_type,
                confidence=confidence,
                status=write_status,
                tags=tags or [],
                entity_type=entity_type,
                entity_id=entity_id,
                expires_at=expires_at,
                source_excerpt=ev_excerpt,
                scope=scope,
                extra_metadata=metadata,
                idem_key=idem_key,
                text=scrubbed,
                captured_at=captured_at,
            )
            metadata.update(_auth.provenance(writer, resolved, turn_ref=turn_ref or ev_turn_id))
            _stamp_claim(metadata, claim)
            if clash is not None:
                metadata["contradicts_id"] = clash[0].id
                metadata["authority_blocked"] = True

            mem_id = _memory_id(user_id, scrubbed, metadata)

            # Durable dedup guard: the in-memory _seen_keys cache is lost on
            # restart, but mem_id is deterministic (text+lanes only, no
            # status/review fields), so a re-POSTed proposal can compute the
            # same mem_id as a row that has already been reviewed. Without
            # this check the upsert below would clobber an approved/rejected/
            # archived row's status and review metadata back to a fresh
            # 'pending' write. Treat that case as a no-op duplicate instead.
            existing = await self._run_sync(self._get_sync, mem_id)
            if existing is not None:
                existing_status = str(
                    existing.metadata.get("status", "") or ""
                ).strip().lower()
                if existing_status not in {"", "pending", "disputed"}:
                    self._bump("dedup", source)
                    if _METRICS_OK:
                        memory_dedup_skip_count.inc()
                    self._remember_seen_key(user_id, idem_key)
                    return None

            try:
                await self._run_sync(
                    self._write_row, mem_id, scrubbed, metadata
                )
            except LiveStoreViolation:
                raise
            except Exception as exc:
                self._bump("error", source)
                logger.warning("memory_service: write failed user=%s source=%s: %s",
                               user_id, source, exc)
                raise MemoryServiceError(f"write failed: {exc}") from exc

            self._remember_seen_key(user_id, idem_key)
            await self._append_audit(
                mem_id=mem_id,
                user_id=user_id,
                actor=source,
                action="ingest",
                before=None,
                after={"text": scrubbed, **metadata},
            )
            self._bump("ok", source)
            if reteach:
                # the person taught it again, AFTER the store succeeded: lift the shield
                try:
                    await _forgotten.release(user_id, scrubbed)
                except Exception:
                    pass
            return MemoryRef(id=mem_id, text=scrubbed, metadata=metadata)

    async def load_for_prompt(
        self, user_id: str, *, limit: int = 20
    ) -> list[MemoryRef]:
        """Fast metadata-filter read for system prompt injection."""
        if is_guest_memory_user(user_id):
            return []
        self._require(user_id, "user_id is required")
        try:
            rows = await self._run_sync(self._metadata_read, user_id, limit)
        except Exception as exc:
            logger.warning("memory_service: load_for_prompt failed user=%s: %s",
                           user_id, exc)
            return []
        ids = [r.id for r in rows]
        if ids:
            self._track_background_task(
                self._tick_access(user_id, ids),
                name="memory_tick_access_prompt",
            )
        return rows

    async def load_recent_for_prompt(
        self, user_id: str, *, window_s: float, limit: int, emotional_first: bool = False
    ) -> list[MemoryRef]:
        """Rows captured in the last ``window_s`` seconds, newest first (at most
        ``limit``; ``emotional_first`` orders emotional rows ahead before the
        cut) — the recency read under the for-prompt continuity mode.

        The ranked ``load_for_prompt`` orders by confidence × decay + access
        hotness, so for a heavy user a memory captured yesterday can sit past any
        fixed prefix of it; this read selects by recency directly. Read-only: no
        access ticks (a recency read is not evidence the row was useful).
        """
        if is_guest_memory_user(user_id):
            return []
        self._require(user_id, "user_id is required")
        try:
            return await self._run_sync(
                self._recent_read, user_id, window_s, limit, emotional_first
            )
        except Exception as exc:
            logger.warning("memory_service: load_recent_for_prompt failed user=%s: %s",
                           user_id, exc)
            return []

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        limit: int = 10,
        timeout_s: float = 2.0,
        as_of: Any = None,
        history: Optional[bool] = None,
    ) -> list[MemoryRef]:
        """Semantic recall of this user's current facts.

        ``as_of`` (epoch seconds, ISO-8601 or datetime) answers "what was true then": the rows whose half-open
        validity interval ``[valid_from, end)`` contains the instant, ``superseded`` rows included (a row that was
        replaced is history, not gone). ``history``: ``None`` = automatic - when the QUESTION asks how things used
        to be ("where did I live before?", ``memory_temporal.is_history_question``) the facts that were replaced
        are added, each labelled "Before that (...)" so it cannot be read as current; ``False`` = never (the write
        path's own lookups); ``True`` = always. A plain question never sees history."""
        if is_guest_memory_user(user_id):
            return []
        self._require(user_id, "user_id is required")
        if not query or not query.strip():
            return []
        as_of_ts: Optional[float] = None
        if as_of is not None:
            try:
                as_of_ts = _temporal.to_epoch(as_of)
            except ValueError as exc:     # a read must never quietly answer "now" for a time it could not read
                raise MemoryServiceError(str(exc)) from exc
        t0 = time.monotonic()
        # Increment 2b: resolve the relationship-graph neighbourhood for the
        # query's person on the async side (the graph fetch is async + needs the
        # DB; the blend is sync). Best-effort and gated: OFF ⇒ {} ⇒ no boost,
        # zero DB work, never crashes or slows a turn.
        depth_by_pid = await self._graph_depth_by_pid(query, user_id)
        try:
            rows = await asyncio.wait_for(
                self._run_sync(
                    self._semantic_search, query, user_id, limit, depth_by_pid,
                    *(() if as_of_ts is None else (as_of_ts,)),
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.debug("memory_service: search timed out after %.1fs", timeout_s)
            return []
        except Exception as exc:
            logger.warning("memory_service: search failed user=%s: %s", user_id, exc)
            return []
        finally:
            if _METRICS_OK:
                memory_search_latency_ms.observe((time.monotonic() - t0) * 1000)

        if _METRICS_OK:
            memory_search_hit_count.observe(len(rows))
        ids = [r.id for r in rows]
        if as_of_ts is None and history is not False and limit > 1 and (
                history is True or _temporal.is_history_question(query)):
            rows = await self._with_history(rows, query, user_id, limit, timeout_s)
        if ids:
            # Pass query for unique_query_count tracking in dreaming memory
            self._track_background_task(
                self.tick_access(user_id, ids, query=query),
                name="memory_tick_access_search",
            )
        return rows

    async def _with_history(self, rows: list[MemoryRef], query: str, user_id: str, limit: int,
                            timeout_s: float) -> list[MemoryRef]:
        """``rows`` plus the facts they replaced, labelled "Before that (...)": each placed right after its
        successor, the whole still within ``limit``. Best-effort: a failure leaves the plain answer."""
        try:
            old = await asyncio.wait_for(
                self.history(query, user_id=user_id, anchors=rows, limit=min(_temporal.HISTORY_MAX, limit - 1)),
                timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory_service: history read failed user=%s: %s", user_id, type(exc).__name__)
            return rows
        if not old:
            return rows
        marked = [MemoryRef(id=o.id, text=_temporal.before_that(o.text, o.metadata), metadata=o.metadata,
                            score=o.score) for o in old]
        out = list(rows[: limit - len(marked)])
        for m in marked:
            at = next((i for i, r in enumerate(out) if r.id == str(m.metadata.get("superseded_by_id") or "")), None)
            out.insert(len(out) if at is None else at + 1, m)
        return out

    async def history(
        self,
        query: Optional[str] = None,
        *,
        user_id: str,
        entity_id: Optional[str] = None,
        anchors: Iterable[MemoryRef] = (),
        limit: int = _temporal.HISTORY_MAX,
    ) -> list[MemoryRef]:
        """The facts that WERE true and were replaced (``superseded`` rows, text untouched, ``metadata["history"]``
        True), most relevant first, for a question about how things used to be, or for one ``entity_id``.
        ``anchors`` are rows already retrieved: their predecessors (``supersedes_id`` chains) come first. A row that
        names an entity the person asked Zoe to forget is never returned. Read-only."""
        if is_guest_memory_user(user_id):
            return []
        self._require(user_id, "user_id is required")
        seeds = [str(a.metadata.get("supersedes_id")) for a in anchors if a.metadata.get("supersedes_id")]
        rows, chain = await self._run_sync(self._history_rows_sync, user_id, seeds)
        ranked = _temporal.rank_history(query or "", rows, anchor_ids=chain, entity_id=entity_id or "", limit=limit * 3)
        out: list[MemoryRef] = []
        for rid, text, meta in ranked:
            if await self._names_forgotten(user_id, text):
                continue
            out.append(MemoryRef(id=rid, text=text, metadata=dict(meta, history=True)))
            if len(out) >= limit:
                break
        return out

    async def _names_forgotten(self, user_id: str, text: str) -> bool:
        """Does ``text`` name something the person asked to forget (ledger or tombstone)? Fails CLOSED: history is
        optional, so an error hides the row rather than risk showing a forgotten name."""
        try:
            from memory_tombstones import matching_tombstone
            return bool(matching_tombstone(user_id, text) or await _forgotten.matches(user_id, text))
        except Exception:  # noqa: BLE001
            return True

    def _history_rows_sync(self, user_id: str, seed_ids: list[str]) -> tuple[list[tuple[str, str, dict]], list[str]]:
        """The owner's ``superseded`` rows as ``(id, text, metadata)`` and the predecessor chain of ``seed_ids``."""
        col = self._collection()
        owner = dict()
        owner["$or"] = [dict(user_id=user_id), dict(wing=user_id)]
        where = dict()
        where["$and"] = [owner, dict(status="superseded")]
        got = col.get(where=where, include=["documents", "metadatas"])
        now = datetime.datetime.now(datetime.timezone.utc)
        rows: list[tuple[str, str, dict]] = []
        for rid, doc, meta in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
            md = dict(meta) if isinstance(meta, dict) else {}
            if md.get("expires_at") and _memory_expired(md.get("expires_at"), now):
                continue
            rows.append((rid, doc or "", md))
        by_id = {rid: md for rid, _t, md in rows}
        chain: list[str] = []
        frontier = list(seed_ids)
        for _ in range(_temporal.HISTORY_MAX):
            nxt = []
            for rid in frontier:
                if rid in by_id and rid not in chain:
                    chain.append(rid)
                    if by_id[rid].get("supersedes_id"):
                        nxt.append(str(by_id[rid]["supersedes_id"]))
            frontier = nxt
        return rows, chain

    async def delete_user(self, user_id: str, *, actor: str, reason: str = "") -> int:
        """Right-to-be-forgotten. Returns number of rows removed.

        A hard delete is the one removal that bypasses the ``status`` lifecycle, so it must not be
        silent. When rows match, a content-free ``delete_user`` INTENT row is written BEFORE anything
        is deleted (actor, reason, ``rows_targeted``, short id hashes — never text) and a
        ``delete_user_done`` row (same tombstone id, ``rows_removed``) AFTER ``_delete_ids`` succeeds,
        so "attempted" and "done" are distinguishable and a failed delete never leaves a false
        "removed" record. If the intent cannot be written nothing is deleted (fail closed). Both rows
        survive the purge of the user's own per-row trail (that trail carries text and is removed). A
        sweep that matches no rows writes nothing — it removes nothing. Documented in
        docs/knowledge/memory-loss-audit-2026-10-05.md."""
        self._require(user_id, "user_id is required")
        assert_write_allowed(getattr(self, "_data_dir", _MEMPALACE_DATA), user_id, "delete_user")
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            # The owner's verbatim turns (exact_words) go with the rows: a right-to-be-forgotten leaves no copy of the words.
            # FIRST, and fail closed: if they cannot be erased nothing else is touched (no "done" audit row, no success), so the
            # caller retries the whole delete - a store blip must never read as "forgotten" while the words stay readable.
            try:
                import exact_words
                await exact_words.delete_user(user_id)
            except Exception as exc:
                raise MemoryServiceError(f"delete_user failed: exact-turn erasure failed ({type(exc).__name__})") from exc
            needles: list[str] = []
            try:
                ids = await self._run_sync(self._list_ids_for_user, user_id)
                if ids and physical_erase_enabled():
                    # heads of the texts about to be erased, held in memory only, to PROVE them gone afterwards
                    needles = _needles_for_texts(await self._run_sync(self._texts_for_ids_sync, ids))
                if ids:
                    tomb_id = _delete_tombstone_id(user_id, ids)
                    await self._run_sync(
                        self._append_audit_sync,
                        tomb_id, user_id, actor, "delete_user",
                        _delete_tombstone_body(ids), None,
                        reason or "hard delete (right-to-be-forgotten / synthetic sweep)",
                    )
                    await self._run_sync(self._delete_ids, ids)
                    await self._run_sync(
                        self._append_audit_sync,
                        tomb_id, user_id, actor, "delete_user_done",
                        {"rows_removed": len(ids)}, None, "",
                    )
                audit_removed = await self._run_sync(self._delete_audit_for_user_sync, user_id)
            except Exception as exc:
                raise MemoryServiceError(f"delete_user failed: {exc}") from exc
            # The API delete left the text on disk (free pages, FTS5 index, write-ahead log, orphan HNSW
            # dirs): erase it physically and verify. Best-effort — the rows ARE gone either way.
            if ids or audit_removed:
                self.last_erase_report = await self._physical_erase(needles)
            # Purge this user's idempotency-cache entries so re-teaching a
            # previously known fact after a forget isn't dropped as a
            # duplicate for the rest of the process lifetime.
            stale_keys = self._seen_keys_by_user.pop(user_id, None)
            if stale_keys:
                for key in stale_keys:
                    self._seen_keys.discard(key)
            _invalidate_agent_user_facts_cache(user_id)
            return len(ids)

    async def list_by_status(
        self,
        *,
        user_id: str,
        status: str = "pending",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRef]:
        """List rows for a user by status, newest first."""
        self._require(user_id, "user_id is required")
        try:
            rows = await self._run_sync(self._list_by_status_sync, user_id, status)
        except Exception as exc:
            logger.warning("memory_service: list_by_status failed: %s", exc)
            return []
        return rows[offset: offset + limit]

    async def forget_last(
        self,
        *,
        user_id: str,
        actor: Optional[str] = None,
        window_s: int = 600,
        note: Optional[str] = None,
    ) -> Optional[MemoryRef]:
        """Soft-delete the most recently ingested memory for a user."""
        self._require(user_id, "user_id is required")
        approved = await self.list_by_status(user_id=user_id, status="approved", limit=5)
        pending = await self.list_by_status(user_id=user_id, status="pending", limit=5)
        candidates = approved + pending
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.metadata.get("added_at", ""), reverse=True)
        newest = candidates[0]
        added_at = newest.metadata.get("added_at", "")
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(added_at.replace("Z", "+00:00")) if added_at else None
            if dt is not None:
                now = datetime.now(timezone.utc)
                if (now - dt).total_seconds() > window_s:
                    return None
        except Exception:
            return None
        return await self.review(
            newest.id,
            decision="reject",
            actor=actor or user_id,
            note=note or "forget_last",
        )

    async def sweep_soft_archive(
        self,
        *,
        user_id: str,
        actor: str = "decay_sweep",
        min_age_days: int = 30,
        score_threshold: float = 0.02,
    ) -> list[str]:
        """Soft-archive low-score approved memories."""
        self._require(user_id, "user_id is required")
        approved = await self.list_by_status(
            user_id=user_id, status="approved", limit=10_000
        )
        if not approved:
            return []

        import math
        now = datetime.datetime.utcnow()
        HALF_LIFE_DAYS = 70.0
        LAMBDA = math.log(2) / HALF_LIFE_DAYS

        to_archive: list[str] = []
        for ref in approved:
            md = ref.metadata
            added_at = md.get("added_at") or ""
            try:
                dt = datetime.datetime.fromisoformat(added_at.replace("Z", ""))
                age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            except Exception:
                continue
            if age_days < min_age_days:
                continue
            try:
                conf = float(md.get("confidence", 0.7) or 0.7)
            except (TypeError, ValueError):
                conf = 0.7
            try:
                access_count = int(md.get("access_count", 0) or 0)
            except (TypeError, ValueError):
                access_count = 0
            score = conf * math.exp(-LAMBDA * age_days) + 0.1 * math.log1p(access_count)
            if score < score_threshold:
                to_archive.append(ref.id)

        archived: list[str] = []
        for mid in to_archive:
            try:
                if await self.review(
                    mid,
                    decision="archive",
                    actor=actor,
                    note=f"soft-archive: score<{score_threshold}, age>={min_age_days}d",
                ) is not None:       # None = refused (the authority wall: a row the user said)
                    archived.append(mid)
            except Exception as exc:
                logger.warning(
                    "memory_service: soft-archive failed id=%s: %s", mid, exc
                )
        if archived:
            logger.info(
                "memory_service: soft-archived %d rows user=%s",
                len(archived), user_id,
            )
        return archived

    async def get(self, mem_id: str) -> Optional[MemoryRef]:
        """Fetch a single row by id."""
        try:
            row = await self._run_sync(self._get_sync, mem_id)
        except Exception as exc:
            logger.warning("memory_service: get failed id=%s: %s", mem_id, exc)
            return None
        return row

    async def review(
        self,
        mem_id: str,
        *,
        decision: str,
        actor: str,
        edits: Optional[str] = None,
        note: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        source_excerpt: Optional[str] = None,
        session_id: Optional[str] = None,
        anchor_text: Optional[str] = None,
        turn_ref: Optional[str] = None,
        authority: Optional[str] = None,
        origin: Optional[str] = None,
        prompt_text: Optional[str] = None,
        speaker_verified: Optional[bool] = None,
        claim: Any = None,
        claim_siblings: Any = (),
    ) -> Optional[MemoryRef]:
        """Approve / reject / edit a pending memory.

        ``claim`` / ``claim_siblings`` (``edit`` only): the claim row of the NEW fact, exactly as ``ingest`` takes it.

        Authority (``memory_authority``): an ``edit`` writes a NEW row stamped with the
        NEW writer's provenance - ``source`` / ``origin`` = this ``actor``, ``session_id`` =
        the ``session_id`` passed here (or none), ``turn_ref``, ``model`` - and never
        inherits the old row's source, session, turn or (for an inferred writer) excerpt.
        ``anchor_text`` is the user's own turn text the new fact was mined from (defaults
        to ``source_excerpt``). An automatic / model actor whose new text the user's turn
        does not support CANNOT edit, archive or reject an approved row that outranks it,
        nor approve a candidate raised against one: it gets ``None`` (callers already treat
        that as "supersede refused") and, for a contradicting edit, a ``disputed``
        candidate linked by ``contradicts_id`` is left behind. A person approving a
        candidate makes it ``user_confirmed`` and retires the row it disputed. ``actor ==``
        the row's owner (the account acting on its own rows) is ``user_confirmed``.

        ``source_excerpt`` (``edit`` only) is the evidence for the NEW text — a
        correcting utterance replaces the edited row's excerpt; omitted, the old
        excerpt is carried forward.

        ``metadata`` (``edit`` only) is extra event metadata for the NEW row —
        stored ``candidate_``-prefixed exactly like ``ingest(metadata=...)`` and
        winning over the value carried forward from the edited row (e.g. the
        turn digest's ``affect`` when an update supersedes a neutral fact).

        Returns None only when an AUTOMATIC actor (``MEMORY_OPT_OUT_SOURCES``)
        tries to ``edit`` an opted-out user's memory — the reconcile UPDATE
        path supersedes via this method instead of ``ingest``, so it must hit
        the same opt-out wall. Every such caller already treats None as
        "supersede failed" and falls through to ``ingest``, which drops.
        """
        decision = decision.lower().strip()
        if decision not in {"approve", "reject", "archive", "edit"}:
            raise MemoryServiceError(
                f"decision must be approve|reject|archive|edit, got {decision!r}"
            )
        current = await self.get(mem_id)
        if current is None:
            raise MemoryServiceError(f"memory {mem_id} not found")
        user_id = current.metadata.get("user_id") or current.metadata.get("wing")
        self._require(user_id, "reviewed row is missing user_id metadata")
        if (
            decision == "edit"
            and actor in MEMORY_OPT_OUT_SOURCES
            and await _user_opted_out(user_id)
        ):
            self._bump("opt_out", actor)
            return None

        # The nightly digest's contradiction pass SUPERSEDES via review(edit): the edited
        # row's source/session_id carry forward, so a polluted name written here looked
        # like a regex/Telegram row. Same identity wall as ingest().
        if decision == "edit" and _identity_assertion_blocked(
            edits or "", user_id=user_id, source=actor
        ):
            self._bump("identity_drop", actor)
            await self._third_person_candidate(
                edits or "", user_id=user_id, source=actor,
                anchor_text=anchor_text if anchor_text is not None else source_excerpt,
                session_id=session_id,
            )
            return None

        # Forgotten ledger (P2.2): an edit WRITES a new row, so it hits the same wall as ingest - new text
        # naming an entity the user asked Zoe to forget is refused unless it is the person's own edit
        # (their account acting in the review UI, or an explicit re-teach writer).
        if decision == "edit" and edits and not (
                (actor == user_id and not origin)
                or _forgotten.is_explicit_reteach(origin or actor, user_id=user_id,
                                                  speaker_verified=speaker_verified)):
            try:
                if await _forgotten.matches(user_id, edits):
                    self._bump("forgotten_drop", actor)
                    logger.info("memory_service: edit refused — names a forgotten entity (ledger) "
                                "(user=%s actor=%s)", user_id, actor)
                    return None
            except Exception:
                pass  # fail-open

        # Consent gate: an edit may not turn a row into an affective record for a member who
        # has not consented (the edited row carries its memory_type forward).
        if decision == "edit" and _auth.is_affective(
                current.metadata.get("memory_type"), current.metadata) \
                and not await self._affect_allowed(user_id):
            self._bump("affect_drop", actor)
            return None
        # ... and an edit may not attach a FEELING (``metadata={"affect": ...}``, the turn
        # digest's update path) to an ordinary fact for a member who has not consented: the
        # same strip ``ingest`` applies - the fact stays, the feeling does not. A feeling the
        # superseded row already carried is not carried forward either.
        strip_affect = False
        if decision == "edit" and (
            _auth.carries_affect(metadata) or _auth.carries_affect(current.metadata)
        ) and not await self._affect_allowed(user_id):
            strip_affect = True
            metadata = {k: v for k, v in (metadata or {}).items()
                        if k not in _auth.AFFECT_KEYS and k not in _AFFECT_CANDIDATE_KEYS}
            logger.info("AFFECT_STRIPPED writer=%s", actor)

        # The authority wall (memory_authority): an inferred writer may not retire, rewrite
        # or contradict a row the USER said. ``edit`` text the user's own turn supports is
        # itself user_stated (resolved below) and passes.
        if await self._authority_refuses(
            current, decision=decision, actor=origin or actor, user_id=user_id, edits=edits,
            anchor_text=anchor_text if anchor_text is not None else source_excerpt,
            authority=authority, session_id=session_id, prompt_text=prompt_text,
            speaker_verified=speaker_verified, claim=claim, claim_siblings=claim_siblings,
        ):
            self._bump("authority_block", actor)
            return None

        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if decision in {"approve", "reject", "archive"}:
                new_status = {
                    "approve": "approved",
                    "reject": "rejected",
                    "archive": "archived",
                }[decision]
                new_meta = dict(current.metadata)
                new_meta["status"] = new_status
                new_meta["reviewed_by"] = actor
                new_meta["reviewed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
                if decision == "archive":
                    # invalidate, never delete: the row is kept, with the instant it stopped being believed
                    new_meta.update(_temporal.archive_fields(
                        current.metadata, now=datetime.datetime.now(datetime.timezone.utc).timestamp()))
                if note:
                    new_meta["review_note"] = note[:1024]
                disputed = ""
                if decision == "approve" and not _auth.writer_is_inferred(
                        origin or actor, user_id=user_id):
                    # A person approving a row IS the user stating it: from here it is
                    # user_confirmed, and the row it was raised against is retired below.
                    if _auth.row_rank(current.metadata, current.text) < _auth.USER_RANK:
                        new_meta["authority"] = _auth.USER_CONFIRMED
                        new_meta["authority_class"] = _auth.USER_CONFIRMED
                        new_meta["authority_basis"] = "user_approved"
                    disputed = str(current.metadata.get("contradicts_id") or "")
                if disputed.startswith(_EDGE_REF):
                    # The candidate disputes a people-graph EDGE, not a memory row: approving
                    # it must change the structured relationship (person_extractor
                    # .apply_edge_dispute), or the candidate becomes an approved memory that
                    # contradicts the graph. If the edge cannot be changed the candidate stays
                    # disputed - nothing is approved.
                    new_edge = await self._apply_edge_dispute(user_id, current.metadata, disputed)
                    if new_edge is None:
                        self._bump("edge_dispute_unapplied", actor)
                        logger.warning("EDGE_DISPUTE_NOT_APPLIED user=%s", user_id)
                        return None
                    new_meta["edge_applied_id"] = new_edge
                    new_meta["supersedes_edge_id"] = disputed[len(_EDGE_REF):]
                    disputed = ""
                await self._run_sync(
                    self._write_row, mem_id, current.text, new_meta
                )
                await self._append_audit(
                    mem_id=mem_id,
                    user_id=user_id,
                    actor=actor,
                    action=decision,
                    before={"status": current.metadata.get("status"), "text": current.text},
                    after={"status": new_status, "text": current.text},
                    reason=note or "",
                )
                if disputed and await self._run_sync(
                    self._supersede_by_sync, user_id, disputed, mem_id
                ):
                    await self._append_audit(
                        mem_id=disputed, user_id=user_id, actor=actor, action="supersede",
                        before={"status": "approved"},
                        after={"status": "superseded", "superseded_by_id": mem_id},
                        reason="user approved the candidate that disputed it",
                    )
                    _invalidate_agent_user_facts_cache(user_id)
                return MemoryRef(id=mem_id, text=current.text, metadata=new_meta)

            # decision == "edit"
            new_text = (edits or current.text).strip()
            if not new_text:
                raise MemoryServiceError("edit decision requires non-empty edits")
            scrubbed, reject = scrub_pii(new_text)
            if reject:
                raise MemoryServiceError(f"edit rejected by PII scrubber: {reject}")
            current_scope = current.metadata.get("scope")
            # The NEW row is the NEW writer's: its source / session / turn / excerpt are
            # this edit's, never the superseded row's (the carried-forward label made a
            # digest rewrite look like a regex write - see memory_authority).
            writer = origin or actor
            edit_res = _auth.resolve_write(
                writer, scrubbed,
                anchor_text=anchor_text if anchor_text is not None else source_excerpt,
                claimed=authority, user_id=user_id, prompt_text=prompt_text,
                speaker_verified=speaker_verified, claim=claim, claim_siblings=claim_siblings,
            )
            _auth.note_structural(edit_res, lane=writer, user_id=user_id)
            edit_source = writer if _auth.is_known_writer(writer) else "review_ui"
            carried_excerpt = (
                None if edit_res.rank < _auth.USER_RANK
                else current.metadata.get("source_excerpt")
            )
            # ZMB A3: the edited row is the NEW writer's, so it says which turn and which words it came from
            edit_excerpt, edit_turn_id = _auth.turn_evidence(
                writer, edit_res, scrubbed, user_id=user_id,
                anchor_text=anchor_text if anchor_text is not None else source_excerpt,
                source_excerpt=source_excerpt, teach=False,
                # a turn reference names the turn of a model reading of a USER turn only; the person editing in the
                # review UI is not a turn (and a stamped edit must not look like one)
                user_turn_id=turn_ref if edit_res.cls in (_auth.USER_STATED_DERIVED, _auth.USER_UNVERIFIED) else None)
            new_meta = self._build_metadata(
                validity_span=self._validity_span(edit_res, source_excerpt, scrubbed),
                user_id=user_id,
                source=edit_source,
                session_id=session_id,
                user_turn_id=edit_turn_id,
                memory_type=current.metadata.get("memory_type", "fact"),
                confidence=float(current.metadata.get("confidence", 0.7)),
                status="approved",
                tags=self._unpack_tags(current.metadata.get("tags", "")),
                entity_type=current.metadata.get("entity_type"),
                entity_id=current.metadata.get("entity_id"),
                expires_at=current.metadata.get("expires_at"),
                source_excerpt=(edit_excerpt if edit_excerpt else carried_excerpt),
                scope=current_scope,
                extra_metadata=metadata,
                idem_key=self._idempotency_key(
                    user_id,
                    mem_id,
                    scrubbed,
                    memory_type=current.metadata.get("memory_type", "fact"),
                    scope=current_scope,
                    entity_type=current.metadata.get("entity_type"),
                    entity_id=current.metadata.get("entity_id"),
                ),
                text=scrubbed,
            )
            # _build_metadata only knows the first-class params above; carry
            # forward any remaining provenance/event metadata (candidate_*
            # extras, event_id, evidence_refs, relationships, supersedes,
            # retention_policy, etc.) from the row being edited so an edit
            # doesn't silently drop it.
            _EDIT_CARRY_FORWARD_SKIP = {
                "user_id", "wing", "room", "visibility", "memory_type", "confidence",
                "source", "status", "added_by", "added_at", "added_ts", "last_accessed",
                "access_count", "embedding_model_version", "idempotency_key", "tags",
                "concept_tags", "related_ids", "unique_query_count", "consolidation_count",
                "session_id", "user_turn_id", "entity_type", "entity_id", "expires_at",
                "source_excerpt", "scope", "supersedes_id", "reviewed_by", "reviewed_at",
                "review_note", "superseded_by_id", "_query_hashes",
                # validity interval: the NEW row's own (written by _build_metadata)
                *_temporal.KEYS,
                # importance is a computed function of the row's TEXT (like
                # memory_type/confidence above), so it must be recomputed for the
                # edited text by _build_metadata — never carried forward, or an
                # edit from a high-stakes fact to ordinary text would keep a stale
                # 0.9 boost.
                "importance",
            } | _auth.PROVENANCE_KEYS
            for key, value in current.metadata.items():
                if key in _EDIT_CARRY_FORWARD_SKIP or key in new_meta:
                    continue
                if strip_affect and key in _AFFECT_CANDIDATE_KEYS:
                    continue
                new_meta[key] = value
            new_meta.update(_auth.provenance(writer, edit_res, turn_ref=turn_ref))
            _stamp_claim(new_meta, claim)
            new_id = _memory_id(user_id, scrubbed, new_meta)
            new_meta["supersedes_id"] = mem_id
            new_meta["reviewed_by"] = actor
            new_meta["reviewed_at"] = new_meta["added_at"]
            if note:
                new_meta["review_note"] = note[:1024]
            # Write the NEW row FIRST, then mark the old one superseded — the same
            # safe order expert_dispatch._maybe_supersede uses. "superseded" is in
            # _BLOCKED_READ_STATUSES (instantly invisible on read); marking the old
            # row first and then failing the new write would make the fact vanish.
            await self._run_sync(
                self._write_row, new_id, scrubbed, new_meta
            )
            # Guard new_id != mem_id: an edit whose text+durable identity hash to the
            # same mem_id just overwrote the row in place, so there is no distinct old
            # row to retire — superseding it would hide the only surviving copy.
            if new_id != mem_id:
                old_meta = dict(current.metadata)
                old_meta["status"] = "superseded"
                old_meta["superseded_by_id"] = new_id
                old_meta.update(_temporal.retire_fields(
                    old_meta, new_meta, now=datetime.datetime.now(datetime.timezone.utc).timestamp()))
                await self._run_sync(
                    self._write_row, mem_id, current.text, old_meta
                )
            await self._append_audit(
                mem_id=new_id,
                user_id=user_id,
                actor=actor,
                action="edit",
                before={"id": mem_id, "text": current.text},
                after={"id": new_id, "text": scrubbed},
                reason=note or "",
            )
        if new_id != mem_id:
            # A corrected fact closes the open loops resting on what it corrected away
            # (ZOE_LOOP_LIFECYCLE; no I/O when off; never raises). Outside the lock.
            from open_loop_lifecycle import resolve_for_supersede

            await resolve_for_supersede(user_id, [(current.text, scrubbed)], ended=False,
                                        source=f"edit:{actor}")
        return MemoryRef(id=new_id, text=scrubbed, metadata=new_meta)

    async def supersede_by(
        self, user_id: str, old_id: str, new_id: str, *, actor: str, note: str = "",
    ) -> bool:
        """Retire ``old_id`` in favour of an EXISTING row ``new_id`` (metadata-only).

        The implicit-supersede path (``memory_supersede``): unlike ``review(edit)`` it
        writes no new text, because the successor was already stored by the turn. Under
        the per-user lock it re-reads both rows and acts only when both belong to
        ``user_id``, differ, and ``old_id`` is still ``approved`` (idempotent and
        race-safe). Old row: ``status=superseded``, ``superseded_by_id``, ``invalid_at``
        (epoch seconds). New row: ``supersedes_id`` (first link wins) and ``valid_from``.
        ``col.update`` without documents keeps both embeddings. Never raises.
        """
        if not user_id or not old_id or not new_id or old_id == new_id:
            return False
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        try:
            async with lock:
                done = await self._run_sync(self._supersede_by_sync, user_id, old_id, new_id)
        except Exception as exc:
            logger.warning("memory_service: supersede_by failed id=%s: %s", old_id,
                           type(exc).__name__)
            return False
        if done:
            await self._append_audit(
                mem_id=old_id, user_id=user_id, actor=actor, action="supersede",
                before={"status": "approved"},
                after={"status": "superseded", "superseded_by_id": new_id},
                reason=note,
            )
            _invalidate_agent_user_facts_cache(user_id)
        return bool(done)

    def _supersede_by_sync(self, user_id: str, old_id: str, new_id: str) -> bool:
        col = self._collection()
        got = col.get(ids=[old_id, new_id], include=["metadatas", "documents"])
        ids_ = got.get("ids") or []
        metas = {i: dict(m or {}) for i, m in zip(ids_, got.get("metadatas") or [])}
        docs = {i: d or "" for i, d in zip(ids_, got.get("documents") or [])}
        old_m, new_m = metas.get(old_id), metas.get(new_id)
        if old_m is None or new_m is None:
            return False
        if any(str(m.get("user_id") or m.get("wing") or "") != user_id for m in (old_m, new_m)):
            return False
        if str(old_m.get("status") or "") != "approved":
            return False
        # Authority: an INFERRED successor can never retire a row the user said. (The
        # implicit-supersede passes pick pairs by topic; they have no user turn in hand
        # when they run nightly, so the rows' own authority decides.)
        if _auth.active():
            new_power = _auth.row_power(new_m, docs.get(new_id, ""))
            if not _auth.may_override(new_power, _auth.row_class(old_m, docs.get(old_id, ""))):
                _auth.log_blocked(str(new_m.get("origin") or new_m.get("source") or ""),
                                  _auth.kind_of(docs.get(old_id, "")), user_id=user_id,
                                  action="supersede")
                if _auth.enabled():
                    return False
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        new_m.setdefault("valid_from", new_m.get("added_ts") or now)
        old_m.update(status="superseded", superseded_by_id=new_id)
        # the old row stopped being true where the new one began (half-open: its invalid_at is the successor's
        # valid_from), and Zoe stopped believing it now
        old_m.update(_temporal.retire_fields(old_m, new_m, now=now))
        if not new_m.get("supersedes_id"):
            new_m["supersedes_id"] = old_id
        col.update(ids=[old_id, new_id], metadatas=[old_m, new_m])
        return True

    async def restore_superseded(
        self, user_id: str, mem_id: str, *, expected_successor_id: str, actor: str, note: str = "",
    ) -> Optional[dict[str, Any]]:
        """Take back a WRONG retirement: ``mem_id`` was superseded by a row about a different person or
        attribute (bake-off X1 / X2), so it is true again. The operator's audited restore
        (``scripts/maintenance/memory_supersede_collateral_audit.py --apply-restore``); there is no review decision
        for it, and the authority wall does not apply (a person ran it, on rows a matcher bug retired).

        Under the per-user lock it re-reads the row and acts only when it belongs to ``user_id``, is still
        ``superseded`` and its ``superseded_by_id`` is ``expected_successor_id`` (so a plan made before a later
        edit never overwrites it, and a second run restores nothing). The row goes back to ``approved`` with
        ``invalid_at`` / ``expired_at`` / ``superseded_by_id`` cleared and its ORIGINAL ``valid_from`` kept
        (``memory_temporal.restore_fields``: a new open-ended interval); its embedding is rebuilt when the index
        lost it. The successor stays as it is (a true fact about someone else) except that a ``supersedes_id``
        pointing back at this row is cleared and noted. Audit rows (no text): ``restore_collateral`` on the
        restored row, ``restore_collateral_unlink`` on the successor. Returns ``{"reindexed", "unlinked"}`` or
        None when nothing was restored. Raises ``LiveStoreViolation`` (never swallowed) against the live
        palace from a non-service process."""
        if not user_id or not mem_id or not expected_successor_id or mem_id == expected_successor_id:
            return None
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            done = await self._run_sync(self._restore_superseded_sync, user_id, mem_id, expected_successor_id)
        if done is None:
            return None
        before, after, succ_before = done.pop("before"), done.pop("after"), done.pop("succ_before")
        await self._append_audit(
            mem_id=mem_id, user_id=user_id, actor=actor, action="restore_collateral",
            before=before, after=after, reason=note,
        )
        if succ_before is not None:
            await self._append_audit(
                mem_id=expected_successor_id, user_id=user_id, actor=actor, action="restore_collateral_unlink",
                before=succ_before, after={"supersedes_id": ""}, reason=note,
            )
        _invalidate_agent_user_facts_cache(user_id)
        return done

    def _restore_superseded_sync(
        self, user_id: str, old_id: str, new_id: str,
    ) -> Optional[dict[str, Any]]:
        assert_write_allowed(getattr(self, "_data_dir", _MEMPALACE_DATA), user_id, "row restore")
        col = self._collection()
        got = col.get(ids=[old_id, new_id], include=["metadatas", "documents", "embeddings"])
        ids_ = list(got.get("ids") or [])
        metas = {i: dict(m or {}) for i, m in zip(ids_, got.get("metadatas") or [])}
        docs = {i: d or "" for i, d in zip(ids_, got.get("documents") or [])}
        embs = got.get("embeddings")
        old_m, new_m = metas.get(old_id), metas.get(new_id)
        if old_m is None or new_m is None:
            return None
        if any(str(m.get("user_id") or m.get("wing") or "") != user_id for m in (old_m, new_m)):
            return None
        if str(old_m.get("status") or "") != "superseded" or str(old_m.get("superseded_by_id") or "") != new_id:
            return None
        keys = ("status", "superseded_by_id", "invalid_at", "expired_at", "valid_from", "valid_until")
        before = {k: old_m.get(k) for k in keys if old_m.get(k) is not None}
        now = datetime.datetime.now(datetime.timezone.utc)
        sets, drops = _temporal.restore_fields(old_m, now=now.timestamp())
        for k in drops:
            old_m.pop(k, None)
        old_m.update(sets)
        old_m.update(status="approved", reviewed_by="operator_restore",
                     reviewed_at=now.replace(tzinfo=None).isoformat() + "Z",
                     review_note=("restored: retired by a row about a different person or attribute "
                                  f"(conflict-pass collateral); successor {new_id}")[:1024])
        after = {k: old_m.get(k) for k in ("status", "valid_from", "valid_until", "restored_at")
                 if old_m.get(k) is not None}
        succ_before = None
        if str(new_m.get("supersedes_id") or "") == old_id:
            succ_before = {"supersedes_id": old_id}
            new_m.pop("supersedes_id", None)
            new_m["supersedes_cleared_id"] = old_id
            new_m["supersedes_cleared_note"] = "restore_collateral: the older row was retired for a different fact"
        # The row is still in the collection (a retirement keeps it), so a metadata-only update keeps its
        # embedding. One the index lost (the collection reports an empty vector) is rebuilt from the text.
        lost = False
        if embs is not None:
            vec = embs[ids_.index(old_id)] if len(embs) == len(ids_) else None
            lost = vec is None or len(vec) == 0
        reindexed = lost and bool(docs.get(old_id))
        if reindexed:
            col.upsert(ids=[old_id], documents=[docs[old_id]], metadatas=[old_m])
            if succ_before is not None:
                col.update(ids=[new_id], metadatas=[new_m])
        elif succ_before is not None:
            col.update(ids=[old_id, new_id], metadatas=[old_m, new_m])
        else:
            col.update(ids=[old_id], metadatas=[old_m])
        return {"reindexed": reindexed, "unlinked": succ_before is not None,
                "before": before, "after": after, "succ_before": succ_before}

    # ── authority (memory_authority.py) ───────────────────────────────────────

    async def _apply_edge_dispute(self, user_id: str, meta: Mapping[str, Any], ref: str) -> Optional[str]:
        """Apply an approved ``edge:<id>`` dispute candidate to ``person_relationships``: the
        candidate's stored edge change (``edge_new_rel`` / person ids / group, written by
        ``person_extractor._edge_may_change``) closes the current edge and opens the new one,
        stamped ``user_confirmed``. Returns the new current edge id, or None (candidate lacks the
        edge data, the pair is gone, or the write failed). Never raises."""
        try:
            new_rel = str(meta.get("edge_new_rel") or "")
            if not new_rel:
                return None
            from db_pool import get_db_ctx
            from person_extractor import apply_edge_dispute

            async with get_db_ctx() as db:
                return await apply_edge_dispute(
                    db, user_id, ref[len(_EDGE_REF):], new_rel_type=new_rel,
                    rel_group=str(meta.get("edge_rel_group") or "personal"),
                    person_a_id=str(meta.get("edge_person_a_id") or ""),
                    person_b_id=str(meta.get("edge_person_b_id") or ""),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_service: edge dispute not applied (%s)", type(exc).__name__)
            return None

    async def _affect_allowed(self, user_id: str) -> bool:
        """May an AFFECTIVE record (an ``emotional_moment`` row, a feeling in a row's metadata)
        be kept for this person? Owner product decision 2026-10-05
        (docs/governance/emotional-safety-note.md section 6). Modes (``memory_authority.affect_gate_mode``):

        * ``household`` (DEFAULT): every household member incl. children, no stored consent row.
          Guests are refused - a guest is the sentinel principal in ``user_filters.GUEST_USERS``
          (``guest`` / ``anonymous`` / ``voice-guest`` / ``voice-daemon`` / empty), the same set
          the batch memory passes and ``auth`` use. A person with no ``member_modes`` row is a
          member. A failed member lookup refuses (closed).
        * ``members``: as above but a member flagged a minor is refused; a failed lookup fails
          OPEN with a warning (the minor flag lives in Postgres; a DB blip must not silence it).
        * ``optin``: adult members with a stored persona mode; fails CLOSED.
        * ``off``: allows all."""
        mode = _auth.affect_gate_mode()
        if mode == "off":
            return True
        uid = (user_id or "").strip()
        try:
            from user_filters import GUEST_USERS

            if uid.lower() in GUEST_USERS:
                return False
        except Exception:  # noqa: BLE001
            if not uid or uid.lower() in {"guest", "anonymous", "voice-guest", "voice-daemon"}:
                return False
        try:
            from persona_layer import UNSET_MODE, load_member_mode

            member = await asyncio.wait_for(load_member_mode(uid), timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            closed = mode in ("household", "optin")
            logger.warning("memory_service: affect gate lookup failed (%s) - %s",
                           type(exc).__name__, "closed" if closed else "open")
            return not closed
        if mode == "household":
            return True
        if member.minor:
            return False
        return not (mode == "optin" and member.mode == UNSET_MODE)

    async def _protected_conflict(
        self, user_id: str, text: str, writer_rank: int
    ) -> Optional[tuple[Any, str]]:
        """``(row, kind)`` of the approved row that OUTRANKS ``writer_rank`` and that ``text``
        contradicts, else None. Fail-open (a read error never blocks a write)."""
        if not _auth.active():
            return None
        try:
            if writer_rank >= _auth.USER_RANK:
                # a direct user write only has an OPERATOR row to answer to: ask the index for
                # those alone instead of reading every approved row on every user write
                rows = await self._run_sync(self._operator_rows_sync, user_id)
            else:
                rows = await self._run_sync(self._list_by_status_sync, user_id, "approved")
            # every approved row is compared (no recency prefix), off the event loop
            return await self._run_sync(_auth.find_conflict, text, rows, writer_rank)
        except Exception as exc:  # noqa: BLE001 - the guard must never break ingestion
            logger.debug("memory_service: authority conflict scan skipped (%s)", type(exc).__name__)
            return None

    async def edit_outranked(
        self,
        mem_id: str,
        *,
        actor: str,
        edits: str,
        origin: Optional[str] = None,
        anchor_text: Optional[str] = None,
        authority: Optional[str] = None,
        prompt_text: Optional[str] = None,
        speaker_verified: Optional[bool] = None,
    ) -> bool:
        """Would ``review(mem_id, decision="edit", edits=...)`` be REFUSED by the authority wall
        (the row outranks this writer)? Pure: no log, no candidate, no write. ``review`` returns
        None for a refusal and for ordinary failures alike; a caller that must not retry a refusal
        through another route (``person_extractor``: ordinary ``ingest`` -> structured tables)
        asks this after the None. False in ``shadow`` mode, on any read error, and for a row that
        is not approved."""
        if not (_auth.active() and _auth.enabled()):
            return False
        try:
            current = await self.get(mem_id)
            if current is None:
                return False
            meta = current.metadata or {}
            if str(meta.get("status") or "") != "approved":
                return False
            user_id = str(meta.get("user_id") or meta.get("wing") or "")
            res = _auth.resolve_write(
                origin or actor, (edits or current.text).strip(),
                anchor_text=anchor_text, claimed=authority, user_id=user_id,
                prompt_text=prompt_text, speaker_verified=speaker_verified)
            return not _auth.may_override(res.power, _auth.row_class(meta, current.text))
        except Exception:  # noqa: BLE001 - a read error is not a refusal
            return False

    async def _authority_refuses(
        self,
        current: MemoryRef,
        *,
        decision: str,
        actor: str,
        user_id: str,
        edits: Optional[str],
        anchor_text: Optional[str],
        authority: Optional[str],
        session_id: Optional[str],
        prompt_text: Optional[str] = None,
        speaker_verified: Optional[bool] = None,
        claim: Any = None,
        claim_siblings: Any = (),
    ) -> bool:
        """True when ``actor`` (a writer below the user classes) may not apply ``decision`` to
        ``current`` because ``current`` outranks it. Logs ``AUTHORITY_BLOCKED`` (labels
        only) and, for a contradicting edit, leaves a disputed candidate behind. In
        ``shadow`` mode it only logs ``AUTHORITY_WOULD_BLOCK`` and returns False."""
        if not _auth.active():
            return False
        meta = current.metadata or {}
        status = str(meta.get("status") or "")
        kind, action, candidate = "", decision, False
        if decision == "approve":
            # a candidate raised AGAINST a row is that row's owner's to approve
            if not (status in ("pending", "disputed") and meta.get("contradicts_id")
                    and _auth.writer_is_inferred(actor, user_id=user_id)):
                return False
            kind = _auth.kind_of(current.text)
        else:
            if status != "approved":
                return False
            if decision == "edit":
                res = _auth.resolve_write(
                    actor, (edits or current.text).strip(), anchor_text=anchor_text,
                    claimed=authority, user_id=user_id, prompt_text=prompt_text,
                    speaker_verified=speaker_verified, claim=claim, claim_siblings=claim_siblings)
            else:  # archive / reject: the actor's own standing, no text to anchor
                res = _auth.Resolved(_auth.writer_class(actor, user_id=user_id), "action")
            if _auth.may_override(res.power, _auth.row_class(meta, current.text)):
                return False
            if decision == "edit":
                new_text = (edits or current.text).strip()
                kind = _auth.conflict_kind(new_text, current.text) or ""
                candidate = bool(kind)
                action = "edit" if kind else "rewrite"
            kind = kind or _auth.kind_of(current.text)
        _auth.log_blocked(actor, kind, user_id=user_id, action=action)
        if not _auth.enabled():
            return False
        if candidate:
            await self._write_candidate(
                (edits or current.text).strip(), user_id=user_id, writer=actor,
                contradicts=current.id, kind=kind, session_id=session_id,
                memory_type=str(meta.get("memory_type") or "fact"),
                entity_type=meta.get("entity_type"), entity_id=meta.get("entity_id"),
                cls=res.cls, basis=res.basis,
            )
        return True

    async def archive_duplicate(self, mem_id: str, keeper_id: str, *, actor: str,
                                note: str = "identical duplicate") -> bool:
        """Archive ``mem_id`` because ``keeper_id`` says EXACTLY the same thing (normalised
        text) and is approved, the same user's, and of at least the same class: nothing is
        lost, so no authority is needed beyond that (the weekly merge used to REWRITE the
        weaker row - 6,585 no-op edits). Never raises; returns whether it archived."""
        try:
            cur, keep = await self.get(mem_id), await self.get(keeper_id)
            if cur is None or keep is None or mem_id == keeper_id:
                return False
            uid = cur.metadata.get("user_id") or cur.metadata.get("wing")
            if (not uid or uid != (keep.metadata.get("user_id") or keep.metadata.get("wing"))
                    or str(cur.metadata.get("status")) != "approved"
                    or str(keep.metadata.get("status")) != "approved"):
                return False
            norm = lambda t: re.sub(r"\s+", " ", (t or "").strip().lower()).strip(" .!?")  # noqa: E731
            if norm(cur.text) != norm(keep.text) or (
                    _auth.row_rank(keep.metadata, keep.text) < _auth.row_rank(cur.metadata, cur.text)):
                return False
            lock = self._user_locks.setdefault(uid, asyncio.Lock())
            async with lock:
                md = dict(cur.metadata)
                md.update(status="archived", reviewed_by=actor,
                          reviewed_at=datetime.datetime.utcnow().isoformat() + "Z",
                          review_note=note[:1024], duplicate_of=keeper_id)
                md.update(_temporal.archive_fields(
                    cur.metadata, now=datetime.datetime.now(datetime.timezone.utc).timestamp()))
                await self._run_sync(self._write_row, mem_id, cur.text, md)
                await self._append_audit(
                    mem_id=mem_id, user_id=uid, actor=actor, action="archive_duplicate",
                    before={"status": "approved"}, after={"status": "archived", "duplicate_of": keeper_id},
                    reason=note)
            _invalidate_agent_user_facts_cache(uid)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_service: archive_duplicate failed (%s)", type(exc).__name__)
            return False

    async def _existing_candidate(
        self, user_id: str, text: str, contradicts: str
    ) -> Optional[MemoryRef]:
        """The still-PENDING candidate that already says ``text`` against ``conflicts_with``
        (the digest's review path and its plain-ingest fallback both raise one for the same
        fact; one question is enough). Fail-open."""
        try:
            for status in ("disputed", "pending"):
                for r in await self._run_sync(self._list_by_status_sync, user_id, status):
                    if r.text == text and str(r.metadata.get("contradicts_id") or "") == contradicts:
                        return r
        except Exception:  # noqa: BLE001
            pass
        return None

    async def record_candidate(
        self,
        text: str,
        *,
        user_id: str,
        writer: str,
        contradicts: str = "",
        kind: str = "other",
        **kw: Any,
    ) -> Optional[str]:
        """Public form of the candidate write for the people-graph writers: a PENDING,
        inferred row that disagrees with something the user said (``conflicts_with`` may be
        a graph edge id, written as ``edge:<id>``)."""
        return await self._write_candidate(
            text, user_id=user_id, writer=writer, contradicts=contradicts, kind=kind, **kw)

    async def _write_candidate(
        self,
        text: str,
        *,
        user_id: str,
        writer: str,
        contradicts: str,
        kind: str,
        session_id: Optional[str] = None,
        memory_type: str = "fact",
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        basis: str = "authority_blocked",
        status: str = "disputed",
        cls: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Optional[str]:
        """Store ``text`` as a ``disputed`` (or, for a person to ask about, ``pending``)
        candidate - never approved, never recalled - linked to the row it disagrees with. Idempotent (deterministic id); a
        candidate the user already reviewed is not resurrected. Never raises."""
        try:
            scrubbed, reject = scrub_pii(text)
            if reject or not scrubbed.strip():
                return None
            md = self._build_metadata(
                user_id=user_id, source=writer, session_id=session_id, user_turn_id=None,
                memory_type=memory_type, confidence=0.5, status=status,
                tags=["authority_candidate"], entity_type=entity_type, entity_id=entity_id,
                expires_at=None, text=scrubbed,
            )
            cand_cls = cls or (_auth.MODEL_FROM_TURN if _auth.writer_class(writer) == _auth.MODEL_FROM_TURN
                               else _auth.MODEL_FROM_TRANSCRIPT)
            md.update(_auth.provenance(writer, _auth.Resolved(cand_cls, basis)))
            if contradicts:
                md["contradicts_id"] = contradicts
            for k, v in (extra or {}).items():  # scalar payload (e.g. the edge change of a dispute)
                if isinstance(v, (str, int, float, bool)):
                    md[str(k)] = v
            md["authority_blocked"] = True
            mem_id = _memory_id(user_id, scrubbed, md)
            existing = await self._run_sync(self._get_sync, mem_id)
            if existing is not None and str(
                existing.metadata.get("status") or ""
            ).strip().lower() not in {"", "pending", "disputed"}:
                return None
            dup = await self._existing_candidate(user_id, scrubbed, contradicts)
            if dup is not None:
                return dup.id
            await self._run_sync(self._write_row, mem_id, scrubbed, md)
            await self._append_audit(
                mem_id=mem_id, user_id=user_id, actor=writer, action="authority_candidate",
                before=None, after={"contradicts_id": contradicts, "kind": kind,
                                    "authority": _auth.INFERRED},
                reason=basis,
            )
            self._bump("authority_candidate", writer)
            return mem_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_service: authority candidate not stored (%s)", type(exc).__name__)
            return None

    async def _third_person_candidate(
        self,
        text: str,
        *,
        user_id: str,
        source: str,
        anchor_text: Optional[str],
        session_id: Optional[str],
    ) -> None:
        """The identity wall dropped ``User's name is <X>`` from a model writer. When the
        user's own turn text merely MENTIONS ``<X>`` (a speech-to-text fragment, a third
        person) without claiming it as their name, ``<X>`` is a PERSON to ask about - a
        pending person candidate - never a user attribute. Silent when the user did say it."""
        if not _auth.enabled() or not anchor_text:
            return
        try:
            from identity_facts import asserted_user_name

            name = asserted_user_name(text)
            if not name:
                return
            if _auth.resolve_write(source, text, anchor_text=anchor_text).rank >= _auth.DERIVED_RANK:
                return
            words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", name)}
            heard = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", anchor_text)}
            if not words or not words <= heard:
                return
            slug = "_".join(sorted(words))[:60]
            await self._write_candidate(
                f"{name.strip()} was mentioned in conversation (who they are is not known).",
                user_id=user_id, writer=source, contradicts="", kind="person",
                session_id=session_id, memory_type="person", entity_type="person_pending",
                entity_id=f"slug:{slug}", basis="third_person_in_user_turn", status="pending",
            )
            logger.info("PERSON_CANDIDATE writer=%s kind=name", source)
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory_service: person candidate skipped (%s)", type(exc).__name__)

    async def export_user(self, user_id: str) -> dict[str, Any]:
        """Full JSON dump for GDPR-style export."""
        self._require(user_id, "user_id is required")
        try:
            items = await self._run_sync(self._export_rows, user_id)
        except Exception as exc:
            raise MemoryServiceError(f"export_user failed: {exc}") from exc
        return {
            "user_id": user_id,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "count": len(items),
            "items": items,
        }

    async def collection_sizes_by_user(self) -> dict[str, int]:
        """Return {user_id: record_count} across the whole MemPalace."""
        try:
            return await self._run_sync(self._collection_sizes_sync)
        except Exception:
            return {}

    async def index_health(self) -> dict[str, Any]:
        """Tombstone health of the drawers index (read-only SQLite + pickle, no chroma call).
        Deliberately NOT via ``_run_sync``: that path takes the collection lease, and this
        read is exactly what an operator calls DURING a compaction (gate closed) to watch
        it — it must answer, never wait on the gate."""
        from memory_index_health import index_health

        loop = asyncio.get_event_loop()
        row = await loop.run_in_executor(None, index_health, self._data_dir)
        row.update(maintenance_state())   # fail-closed gate → maintenance_blocked + reason
        # physical-erase posture (docs/knowledge/forgotten-text-physical-erase.md): is the host allocator
        # scrubbing, and how many HNSW dirs does no collection own (pre-rebuild index files left on disk)
        try:
            import memory_residue
            row["orphan_segment_dirs"] = len(memory_residue.orphan_segment_dirs(self._data_dir))
        except Exception:  # noqa: BLE001 - informational
            row["orphan_segment_dirs"] = None
        row["heap_scrub_active"] = heap_scrub_active()
        row["physical_erase_enabled"] = physical_erase_enabled()
        return row

    async def compact_index(self) -> dict[str, Any]:
        """In-process drawers index compaction (see ``compact_drawers_index_sync``).
        Raises ``IndexCompactionError`` (``.report``) when it did not complete. Deliberately
        NOT via ``_run_sync``: that path takes a collection lease, and the compaction drains
        the leases — it would wait for itself."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, compact_drawers_index_sync, self._data_dir)

    async def _physical_erase(self, needles: list[str]) -> dict[str, Any]:
        """:func:`erase_residue_sync` off the event loop. Deliberately NOT via ``_run_sync``: that takes a
        collection lease and the erase drains the leases. Never raises."""
        if not physical_erase_enabled():
            return {"enabled": False}
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: erase_residue_sync(self._data_dir, needles=needles))
        except Exception as exc:  # noqa: BLE001 - the rows are already gone; the report says the erase failed
            logger.warning("MEMORY_PHYSICAL_ERASE failed (%s)", type(exc).__name__)
            return {"enabled": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def scrub_residue(self, *, tokens: Iterable[str] = ()) -> dict[str, Any]:
        """The one-time / on-demand scrub of the WHOLE palace's SQLite file (queue blank + purge, FTS5
        rebuild, ``VACUUM``) and its orphan HNSW dirs, under the maintenance gate. ``tokens`` are optional
        strings to verify gone afterwards (counts only are returned). Serving never stops: the gate holds
        collection ops for the duration (measured ~1 s on the 63 MB live store)."""
        return await self._physical_erase(list(tokens))

    def _texts_for_ids_sync(self, ids: list[str]) -> list[str]:
        col = self._collection()
        out: list[str] = []
        for i in range(0, len(ids), 100):
            got = col.get(ids=ids[i:i + 100], include=["documents"])
            out.extend(str(d) for d in (got.get("documents") or []) if d)
        return out

    def _owned_rows_sync(self, user_id: str, ids: list[str]) -> tuple[list[str], list[str]]:
        """``(ids, texts)`` of the rows among ``ids`` that ``user_id`` owns - a forget only ever erases the
        caller's own rows (a family-visible row of another member is never theirs to erase)."""
        col = self._collection()
        owned: list[str] = []
        texts: list[str] = []
        for i in range(0, len(ids), 100):
            got = col.get(ids=ids[i:i + 100], include=["documents", "metadatas"])
            for rid, doc, meta in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
                meta = meta or {}
                if meta.get("user_id") == user_id or meta.get("wing") == user_id:
                    owned.append(rid)
                    texts.append(str(doc or ""))
        return owned, texts

    def _delete_audit_for_rows_sync(self, row_ids: list[str]) -> int:
        """Delete the per-row audit trail (it carries the row's text in ``before`` / ``after`` and the
        forget note in ``reason``) of rows being erased. Tombstones are never matched: they name no row."""
        col = self._audit_collection()
        n = 0
        for i in range(0, len(row_ids), 100):
            got = col.get(where={"mempalace_id": {"$in": row_ids[i:i + 100]}})
            ids = list(got.get("ids") or [])
            if ids:
                col.delete(ids=ids)
                n += len(ids)
        return n

    async def erase_rows(self, user_id: str, ids: Iterable[str], *, actor: str,
                         reason: str = "forgotten by request") -> dict[str, Any]:
        """HARD-erase specific rows of ONE user, the part of "forgotten means forever" that is the drawers:
        a content-free ``forget_erase`` intent row (counts and short id hashes, never text or name), the
        rows themselves, their per-row audit trail, a ``forget_erase_done`` row, then the physical erase
        (:func:`erase_residue_sync`: free pages, FTS5, write-ahead log, orphan HNSW dirs, verified). Only
        rows ``user_id`` owns are touched. ``reason`` must never carry the forgotten name. Fail-closed: if the
        intent row cannot be written nothing is deleted. Returns counts only."""
        self._require(user_id, "user_id is required")
        assert_write_allowed(getattr(self, "_data_dir", _MEMPALACE_DATA), user_id, "erase_rows")
        want = [i for i in dict.fromkeys(str(x) for x in ids) if i]
        if not want:
            return {"rows_removed": 0}
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            try:
                owned, texts = await self._run_sync(self._owned_rows_sync, user_id, want)
                if not owned:
                    return {"rows_removed": 0}
                needles = _needles_for_texts(texts) if physical_erase_enabled() else []
                tomb_id = _delete_tombstone_id(user_id, owned)
                await self._run_sync(
                    self._append_audit_sync, tomb_id, user_id, actor, "forget_erase",
                    _delete_tombstone_body(owned), None, reason)
                await self._run_sync(self._delete_ids, owned)
                audit_removed = await self._run_sync(self._delete_audit_for_rows_sync, owned)
                await self._run_sync(
                    self._append_audit_sync, tomb_id, user_id, actor, "forget_erase_done",
                    {"rows_removed": len(owned)}, None, "")
            except Exception as exc:
                raise MemoryServiceError(f"erase_rows failed: {exc}") from exc
            report = await self._physical_erase(needles)
            self.last_erase_report = report
            _invalidate_agent_user_facts_cache(user_id)
            return {"rows_removed": len(owned), "audit_removed": audit_removed, "physical": report}

    def _collection_sizes_sync(self) -> dict[str, int]:
        from collections import Counter as _Counter
        col = self._collection()
        result = col.get(include=["metadatas"])
        metas = result.get("metadatas") or []
        counts: _Counter = _Counter()
        for m in metas:
            if not isinstance(m, dict):
                continue
            uid = m.get("user_id") or m.get("wing") or "unknown"
            counts[uid] += 1
        return dict(counts)

    # Internal helpers

    @staticmethod
    def _require(value: Any, msg: str) -> None:
        if not value:
            raise MemoryServiceError(msg)

    @staticmethod
    def _idempotency_key(
        user_id: str,
        turn_id: Optional[str],
        text: str,
        *,
        memory_type: str = "",
        scope: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> str:
        # Include the same lane-distinguishing fields _memory_id hashes into the
        # durable row id, so two legitimately distinct memories (same text,
        # different memory_type/scope/entity) don't collide in this cache and
        # have the second one silently dropped.
        basis = (
            f"{user_id}|{turn_id or ''}|{text}|{memory_type or ''}|"
            f"{scope or ''}|{entity_type or ''}|{entity_id or ''}"
        ).encode()
        return hashlib.sha256(basis).hexdigest()

    @staticmethod
    def _validity_span(resolved: Any, source_excerpt: Optional[str], text: str) -> Optional[str]:
        """The words an event time may be read from: the person's own (a user-class write), never a model's."""
        if getattr(resolved, "cls", "") not in (_auth.USER_STATED, _auth.USER_CONFIRMED):
            return None
        return source_excerpt or text

    @staticmethod
    def _build_metadata(
        *,
        user_id: str,
        source: str,
        session_id: Optional[str],
        user_turn_id: Optional[str],
        memory_type: str,
        confidence: float,
        status: str,
        tags: list[str],
        entity_type: Optional[str],
        entity_id: Optional[str],
        expires_at: Optional[str],
        source_excerpt: Optional[str] = None,
        scope: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
        idem_key: str = "",
        text: str = "",
        captured_at: Optional[str] = None,
        validity_span: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build durable metadata for a memory row.

        ``validity_span`` is the PERSON'S OWN words the fact came from (the caller passes it only for a
        user-class write; a model's paraphrase never gets one): an event time stated in it ("since 2018")
        becomes the row's ``valid_from``. Without it ``valid_from`` is the capture time. Two timelines on
        every row, no flag (``memory_temporal``).

        When ``scope`` is None, ``extra_metadata["scope"]`` is promoted to the
        first-class Zoe memory scope and drives legacy visibility mapping.
        """
        _now_dt = datetime.datetime.utcnow()
        if captured_at:   # restore path: keep the original capture instant (see ingest)
            _c, _why = parse_captured_at(captured_at)
            if _c is not None:
                _now_dt = _c
            else:
                # Never silent: a restore that quietly dates a row "now" loses the one thing captured_at is for.
                # Log the SHAPE of the value, never the value (it travels beside the row's text in the caller).
                logger.warning(
                    "memory_service: captured_at ignored (%s), stored as captured now: %s",
                    _why, value_shape(captured_at),
                )
        now = _now_dt.isoformat() + "Z"
        extra = dict(extra_metadata or {})
        event_scope = scope if scope is not None else extra.get("scope")
        visibility = _scope_visibility(event_scope)
        md: dict[str, Any] = {
            "user_id": user_id,
            "wing": user_id,
            "room": "conversations",
            "visibility": visibility,
            "memory_type": memory_type,
            "confidence": float(confidence),
            "source": source,
            "status": status,
            "added_by": source,
            "added_at": now,
            # Numeric twin of added_at: chroma `where` compares ($gte/$lt) only
            # ints/floats, so a time-bounded store query needs this field
            # (load_recent_for_prompt). Same instant as added_at.
            "added_ts": _now_dt.replace(tzinfo=datetime.timezone.utc).timestamp(),
            "last_accessed": now,
            "access_count": 0,
            "embedding_model_version": os.environ.get(
                "ZOE_EMBEDDING_MODEL_VERSION", "minilm-v1"
            ),
            "idempotency_key": idem_key,
            "tags": ",".join(tags),
            # Dreaming memory fields (arXiv:2604.20943)
            "concept_tags": "",        # comma-sep entity types/topics (filled by REM pass)
            "related_ids": "",         # comma-sep IDs of semantically related memories
            "unique_query_count": 0,   # distinct queries that have surfaced this memory
            "consolidation_count": 0,  # weekly deep-sleep passes that have touched this memory
        }
        # Validity interval (audit P2.1), unconditional: valid_from = the event time the person stated, else the
        # capture time (learned_at = added_ts). invalid_at is written when the row is superseded or archived.
        stated = _temporal.parse_validity(validity_span or "", text, now=_now_dt) if validity_span else _temporal.Validity()
        md.update(_temporal.stamp(stated, md["added_ts"]))
        if session_id:
            md["session_id"] = session_id
        if user_turn_id:
            md["user_turn_id"] = user_turn_id
        if entity_type:
            md["entity_type"] = entity_type
        if entity_id:
            md["entity_id"] = entity_id
        if expires_at:
            md["expires_at"] = _normalize_expires_at(expires_at)
        # Every write path (ingest, review edit, carry-forward) builds metadata
        # here, so the excerpt is scrubbed at this one boundary — no caller can
        # store raw text by skipping its own scrub.
        excerpt = scrub_source_excerpt(source_excerpt)
        if excerpt:
            md["source_excerpt"] = excerpt
        if event_scope:
            md["scope"] = str(event_scope)
        _promote_event_metadata(md, extra)
        for key, value in extra.items():
            target_key = f"candidate_{key}"
            if target_key in md or value is None:
                continue
            md[target_key] = _metadata_value(value)
        # Importance (3b): score high-stakes content (allergy/med/dietary/vital-id)
        # so the 2a hybrid importance arm can rank it up. Only written when > 0, so
        # ordinary facts carry no `importance` key and the arm stays a no-op for
        # them. Guarded on absence so a promoted event field is never clobbered.
        if "importance" not in md:
            imp = score_importance(text)
            if imp > 0.0:
                md["importance"] = imp
        return md

    def _remember_seen_key(self, user_id: str, idem_key: str) -> None:
        """Add an idempotency key to the fast-path cache, tracked by user_id
        so delete_user() can purge it (see _seen_keys_by_user)."""
        self._seen_keys.add(idem_key, user_id)
        self._seen_keys_by_user.setdefault(user_id, set()).add(idem_key)

    def _on_seen_key_evicted(self, idem_key: str, user_id: Any) -> None:
        """Keep _seen_keys_by_user in sync when _seen_keys evicts an old key,
        so the per-user index cannot grow beyond the live cache."""
        keys = self._seen_keys_by_user.get(user_id)
        if keys is None:
            return
        keys.discard(idem_key)
        if not keys:
            self._seen_keys_by_user.pop(user_id, None)

    # Statuses that mean "a candidate reached ingest and was NOT written": counted in the
    # durable reject ledger (memory_reject_ledger) so the nightly summary can say why an
    # ingest left no row. ``error`` is separate (a failed write is loud, never a reject).
    _REFUSED_STATUSES = frozenset({"opt_out", "pii_reject", "tombstone_drop", "forgotten_drop", "dedup",
                                 "identity_drop"})

    def _bump(self, status: str, source: str) -> None:
        if _METRICS_OK:
            memory_write_count.labels(source=source, status=status).inc()
        if status in self._REFUSED_STATUSES:
            try:
                from memory_reject_ledger import record_reject
                record_reject(source, status, gate=False)
            except Exception:  # noqa: BLE001
                pass

    async def _graph_depth_by_pid(self, query: str, user_id: str) -> dict[str, int]:
        """Best-effort relationship-graph neighbourhood for the query's person.

        Returns ``{people.id: depth}`` (start person at depth 0, direct relations
        at 1, friend-of at 2) for the 7th ``_semantic_search`` blend signal.
        Gated behind BOTH ``ZOE_RELATIONSHIP_GRAPH_ENABLED`` and
        ``ZOE_GRAPH_RECALL_BOOST`` (default OFF); either off ⇒ ``{}`` with zero
        DB work, so the boost stays off the hot path. Any failure ⇒ ``{}`` ⇒ no
        boost — never crashing or slowing a turn. Reuses the ambiguity-safe
        ``_resolve_unique_person_uuid`` resolver (no new NLU model) so an
        ambiguous query fragment skips the boost rather than boosting the wrong
        person.
        """
        try:
            import relationship_graph

            if not (
                relationship_graph.relationship_graph_enabled()
                and _graph_recall_boost_enabled()
            ):
                return {}
            names = _candidate_person_names(query)
            if not names:
                return {}

            from db_pool import get_db_ctx
            from memory_extractor import _resolve_unique_person_uuid

            async with get_db_ctx() as db:
                start_pid = None
                # Ambiguity-safe resolution: `_resolve_unique_person_uuid` links
                # ONLY on an unambiguous match (unique exact name, or a single
                # whole-token substring hit), else None. The loose
                # `_resolve_person_uuid` (substring LIKE, first row) would boost
                # the WRONG person's neighbourhood on an ambiguous fragment
                # ("Ali" vs {Alice, Alison}); don't guess — skip the boost.
                for name in names:
                    start_pid = await _resolve_unique_person_uuid(name, user_id, db)
                    if start_pid:
                        break
                if not start_pid:
                    return {}
                neighbors = await relationship_graph.neighbors(
                    db, user_id, start_pid, max_depth=2, limit=32
                )
            depth_by_pid: dict[str, int] = {start_pid: 0}
            for n in neighbors:
                pid = n.get("person_id")
                if pid is None:
                    continue
                try:
                    depth_by_pid[pid] = int(n.get("depth", 1))
                except (TypeError, ValueError):
                    depth_by_pid[pid] = 1
            return depth_by_pid
        except Exception as exc:  # best-effort: never break or slow a turn
            logger.debug("memory_service: graph recall boost skipped: %s", exc)
            return {}

    @staticmethod
    async def _run_sync(fn, *args):
        # Every executor call holds a collection lease for its whole duration, so the
        # index compaction can drain in-flight work before it swaps the collection.
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _leased_call, fn, *args)

    def _track_background_task(self, coro, *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)

        def _done(done: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(done)
            if done.cancelled():
                return
            try:
                exc = done.exception()
            except Exception:
                logger.warning("memory_service: background task inspection failed", exc_info=True)
                return
            if exc is not None:
                logger.warning(
                    "memory_service: background task %s failed",
                    name,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_done)
        return task

    def _collection(self):
        return get_drawers_collection(self._data_dir)

    def _audit_collection(self):
        return _palace_client(self._data_dir).get_or_create_collection(_AUDIT_COLLECTION)

    def _write_row(self, mem_id: str, text: str, metadata: dict[str, Any]) -> None:
        assert_write_allowed(
            getattr(self, "_data_dir", _MEMPALACE_DATA), str(metadata.get("user_id") or metadata.get("wing") or ""), "row write")
        col = self._collection()
        col.upsert(ids=[mem_id], documents=[text], metadatas=[metadata])

    @staticmethod
    def _scope_where(user_id: str) -> dict[str, Any]:
        return {"$or": [{"user_id": user_id}, {"wing": user_id}, {"visibility": "family"}]}

    def _visible_rows(self, user_id: str, now: datetime.datetime) -> list[MemoryRef]:
        """Every row this user may read (unexpired, visible, status-visible) —
        the shared filter under both the ranked and the recency reads."""
        col = self._collection()
        result = col.get(
            where=self._scope_where(user_id),
            include=["documents", "metadatas"],
        )
        return self._filter_visible(result, user_id, now)

    @staticmethod
    def _filter_visible(
        result: Mapping[str, Any], user_id: str, now: datetime.datetime
    ) -> list[MemoryRef]:
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        ids = result.get("ids") or []
        filtered: list[MemoryRef] = []
        for rid, doc, meta in zip(ids, docs, metas):
            if not isinstance(meta, dict):
                meta = {}
            expires = meta.get("expires_at")
            if expires and _memory_expired(expires, now):
                continue
            if not _memory_visible_to_user(meta, user_id):
                continue
            if not _memory_status_visible(meta):
                continue
            filtered.append(MemoryRef(id=rid, text=doc or "", metadata=dict(meta)))
        return filtered

    def _metadata_read(self, user_id: str, limit: int) -> list[MemoryRef]:
        now = datetime.datetime.now(datetime.timezone.utc)
        filtered = self._visible_rows(user_id, now)

        import math
        HALF_LIFE_DAYS = 70.0
        LAMBDA = math.log(2) / HALF_LIFE_DAYS

        def _score(ref: MemoryRef) -> tuple[float, str]:
            md = ref.metadata
            try:
                conf = float(md.get("confidence", 0.7) or 0.7)
            except (TypeError, ValueError):
                conf = 0.7
            try:
                access_count = int(md.get("access_count", 0) or 0)
            except (TypeError, ValueError):
                access_count = 0
            added_at = md.get("added_at") or ""
            try:
                dt = _parse_aware_datetime(added_at)
                age_days = max(0.0, (now - dt).total_seconds() / 86400.0) if dt else 0.0
            except Exception:
                age_days = 0.0
            score = conf * math.exp(-LAMBDA * age_days) + 0.1 * math.log1p(access_count)
            return (score, added_at)

        filtered.sort(key=_score, reverse=True)
        return filtered[:limit]

    def _recent_read(
        self, user_id: str, window_s: float, limit: int, emotional_first: bool
    ) -> list[MemoryRef]:
        """Visible rows added within ``window_s``, selected emotional-first (when
        asked) then newest, at most ``limit``.

        The time bound and a hard row cap (``_RECENT_SCAN_CAP``) go INTO the
        store query via the numeric ``added_ts`` field, so the read is bounded on
        a large store. Ordering happens over the whole bounded candidate set,
        BEFORE truncation — so a burst of ordinary captures after a worry cannot
        push the worry out. Trade-offs, both deliberate:
          * chroma cannot sort, so if one user has more than _RECENT_SCAN_CAP
            rows inside the window the store picks which of them come back;
          * rows written before ``added_ts`` existed (or by a writer that does
            not set it) are invisible to the bounded query. When it finds
            NOTHING, the read falls back to the full visible-row scan filtered
            by ``added_at`` — the same scan ``load_for_prompt`` makes every
            turn — so a user whose recent rows predate the field still gets
            continuity. The composer also merges the ranked rows.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now - datetime.timedelta(seconds=window_s)
        col = self._collection()
        result = col.get(
            where={"$and": [
                self._scope_where(user_id),
                {"added_ts": {"$gte": cutoff.timestamp()}},
            ]},
            include=["documents", "metadatas"],
            limit=_RECENT_SCAN_CAP,
        )
        rows = self._filter_visible(result, user_id, now)
        if not rows:
            rows = self._visible_rows(user_id, now)  # legacy rows without added_ts
        dated: list[tuple[datetime.datetime, MemoryRef]] = []
        for ref in rows:
            try:
                dt = _parse_aware_datetime(ref.metadata.get("added_at") or "")
            except Exception:
                dt = None
            if dt is not None and dt >= cutoff:
                dated.append((dt, ref))
        if emotional_first:
            dated.sort(key=lambda p: (is_emotional_memory(p[1]), p[0]), reverse=True)
        else:
            dated.sort(key=lambda p: p[0], reverse=True)
        return [ref for _, ref in dated[:limit]]

    @staticmethod
    def _valid_as_of(col: Any, md: Mapping[str, Any], ts: float) -> bool:
        """Was this row true at ``ts``? (``memory_temporal.valid_at``; a superseded row from before ``invalid_at`` was
        stamped ends where its successor began, looked up here.)"""
        successor_start = None
        if (str(md.get("status") or "") == "superseded" and _temporal.row_end(md) is None
                and md.get("superseded_by_id")):
            got = col.get(ids=[str(md["superseded_by_id"])], include=["metadatas"])
            metas = got.get("metadatas") or []
            successor_start = _temporal.row_start(metas[0] or {}) if metas else None
        return _temporal.valid_at(md, ts, successor_start=successor_start)

    def _semantic_search(
        self,
        query: str,
        user_id: str,
        limit: int,
        depth_by_pid: dict[str, int] | None = None,
        as_of_ts: float | None = None,
    ) -> list[MemoryRef]:
        col = self._collection()
        where = {"$or": [{"user_id": user_id}, {"wing": user_id}, {"visibility": "family"}]}
        now = datetime.datetime.now(datetime.timezone.utc)
        hits: list[MemoryRef] = []
        seen: set[str] = set()

        def _collect(result: dict) -> None:
            ids = (result.get("ids") or [[]])[0]
            docs = (result.get("documents") or [[]])[0]
            metas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            for rid, doc, meta, dist in zip(ids, docs, metas, distances):
                if rid in seen:
                    continue
                md = dict(meta) if isinstance(meta, dict) else {}
                expires = md.get("expires_at")
                if expires and _memory_expired(expires, now):
                    continue
                if not _memory_visible_to_user(md, user_id):
                    continue
                if as_of_ts is None:
                    if not _memory_status_visible(md):
                        continue
                elif not self._valid_as_of(col, md, as_of_ts):
                    continue
                seen.add(rid)
                hits.append(MemoryRef(id=rid, text=doc or "", metadata=md, score=float(dist or 0.0)))

        # Order matters (measured 2026-10-04, day-sim ask 4 after the first fallback shipped):
        # the owner-filtered HNSW query can come back FULL BUT WRONG — hnswlib fills ``ef``
        # with the allowed rows it happens to meet while the asked-about rows sit behind
        # tombstones (demo churn: 1,591 elements for 258 live rows), so a "short result"
        # trigger never fires. Query UNFILTERED first (an over-fetch capped at the
        # collection size; at palace scale this is a few hundred rows) and apply the same
        # visibility / status / expiry rules in Python; the filtered query only
        # supplements when the owner's visible rows are still fewer than ``limit``.
        # NEVER call ``col.count()`` here: chroma 1.5.9's Rust client wedged the whole
        # service on 2026-10-04 08:07 (one worker blocked in ``rust.py:_count`` while a
        # concurrent query/ingest held the other side; every later memory call queued
        # behind it, /readyz stopped answering). Reproduced on a palace copy with two
        # searchers + two writers; without the count the same run completes. The index
        # caps ``k`` at its own size, so a fixed over-fetch is safe at any store size.
        _collect(col.query(
            query_texts=[query],
            n_results=max(limit * 20, 200),
            include=["documents", "metadatas", "distances"],
        ))
        wide = len(hits)
        if len(hits) < limit:
            _collect(col.query(
                query_texts=[query],
                n_results=max(limit * 3, limit),
                where=where,
                include=["documents", "metadatas", "distances"],
            ))
            logger.info("MEMORY_SEARCH_SUPPLEMENT user=%s unfiltered_visible=%d limit=%d "
                        "filtered_added=%d", user_id, wide, limit, len(hits) - wide)

        # Re-rank by blending semantic distance with hotness signals.
        # load_for_prompt already does this for the metadata-only path; here we
        # apply the same principle so frequently-accessed memories about known
        # people/topics surface ahead of semantically-close but cold newcomers.
        # Formula: relevance = (1 / (1 + dist)) * conf * decay + 0.05 * log1p(access)
        # The dist→relevance inversion means lower L2 distance → higher score.
        _LAMBDA = math.log(2) / 70.0  # 70-day half-life, same as load_for_prompt
        _HOTNESS_WEIGHT = float(os.environ.get("ZOE_SEARCH_HOTNESS_WEIGHT", "0.05"))

        # Increment 2a: hybrid boosts, flag-gated (default OFF). OFF is a true
        # no-op — the boost term below is skipped entirely and ordering is
        # byte-for-byte the pre-2a semantic+hotness behaviour.
        _hybrid_on = _hybrid_retrieval_enabled()
        _query_tokens = _hybrid_tokens(query) if _hybrid_on else set()

        # Increment 2b: graph-adjacency boost. ``depth_by_pid`` is populated by
        # the async ``search`` caller ONLY when both graph flags are on (else it
        # is empty/None). When empty the 7th term is skipped entirely, so OFF is
        # byte-for-byte identical to the pre-2b ordering.
        _graph_on = bool(depth_by_pid)
        _graph_weight = 0.0
        if _graph_on:
            # A malformed weight must disable ONLY the graph term — never raise
            # out of the blend into search()'s catch-all, which would drop every
            # semantic result. Fall back to the default on a bad value.
            try:
                _graph_weight = float(
                    os.environ.get("ZOE_GRAPH_RECALL_WEIGHT", _GRAPH_RECALL_WEIGHT_DEFAULT)
                )
            except (TypeError, ValueError):
                logger.warning(
                    "memory_service: invalid ZOE_GRAPH_RECALL_WEIGHT; using default %.2f",
                    _GRAPH_RECALL_WEIGHT_DEFAULT,
                )
                _graph_weight = _GRAPH_RECALL_WEIGHT_DEFAULT

        def _blend(ref: MemoryRef) -> float:
            md = ref.metadata
            dist = ref.score
            try:
                conf = float(md.get("confidence", 0.7) or 0.7)
            except (TypeError, ValueError):
                conf = 0.7
            try:
                access_count = int(md.get("access_count", 0) or 0)
            except (TypeError, ValueError):
                access_count = 0
            added_at = md.get("added_at") or ""
            try:
                dt = _parse_aware_datetime(added_at)
                age_days = max(0.0, (now - dt).total_seconds() / 86400.0) if dt else 0.0
            except Exception:
                age_days = 0.0
            # A fact the OWNER stated is durable: it does not become less true, or less relevant to a question about it,
            # with age (audit P2.4; ZMB L: with the 70-day half-life the older of two facts the owner told Zoe
            # was buried under last week's chatter, and a two-fact question lost it). Model-written rows and
            # moods still decay.
            decays = not _durable_user_fact(md, ref.text)
            semantic = (1.0 / (1.0 + dist)) * conf * (math.exp(-_LAMBDA * age_days) if decays else 1.0)
            hotness  = _HOTNESS_WEIGHT * math.log1p(access_count)
            base = semantic + hotness
            # 7th signal: relationship-graph adjacency. depth 0 = the person the
            # query is about, 1 = a direct relation, 2 = friend-of. This surfaces
            # facts stored under a *connected* person (the multi-hop win) that
            # vector distance alone misses. Skipped whole when the graph flags
            # are off (``_graph_on`` false), keeping OFF byte-identical.
            graph = 0.0
            if _graph_on:
                entity_id = md.get("entity_id")
                if entity_id in depth_by_pid:
                    graph = _graph_weight * (1.0 / (1 + depth_by_pid[entity_id]))
            if not _hybrid_on:
                return base + graph if _graph_on else base
            # 1) Keyword/lexical boost — primary fix for 0-hit semantic misses.
            keyword = _HYBRID_KEYWORD_WEIGHT * _hybrid_keyword_overlap(
                _query_tokens, ref.text
            )
            # 2) Temporal-proximity boost — mild, exponential decay on age.
            recency = _HYBRID_RECENCY_WEIGHT * math.exp(-_HYBRID_RECENCY_LAMBDA * age_days)
            # 3) Preference/importance boost — memory_type / importance signal.
            #    `importance` is not currently written to metadata, so that arm
            #    stays a no-op until a producer emits it; the memory_type arm is
            #    active (values like "preference"/"person").
            pref_signal = 0.0
            if str(md.get("memory_type", "")).lower() in _HYBRID_PREFERENCE_TYPES:
                pref_signal = 1.0
            else:
                try:
                    importance = float(md.get("importance", 0.0) or 0.0)
                except (TypeError, ValueError):
                    importance = 0.0
                pref_signal = max(0.0, min(1.0, importance))
            preference = _HYBRID_PREFERENCE_WEIGHT * pref_signal
            hybrid = base + keyword + recency + preference
            return hybrid + graph if _graph_on else hybrid

        hits.sort(key=_blend, reverse=True)
        return hits[:limit]

    def _list_ids_for_user(self, user_id: str) -> list[str]:
        col = self._collection()
        result = col.get(
            where={"$or": [{"user_id": user_id}, {"wing": user_id}]},
            include=[],
        )
        return list(result.get("ids") or [])

    def _delete_ids(self, ids: list[str]) -> None:
        col = self._collection()
        col.delete(ids=ids)

    def _delete_audit_for_user_sync(self, user_id: str) -> int:
        col = self._audit_collection()
        # The ``delete_user`` / ``forget_erase`` (+ ``_done``) rows are the record OF the removal — they outlive it.
        result = col.get(where={"$and": [{"user_id": user_id},
                                         {"action": {"$nin": ["delete_user", "delete_user_done", "forget_erase", "forget_erase_done"]}}]})
        ids = list(result.get("ids") or [])
        if ids:
            col.delete(ids=ids)
        return len(ids)

    def _list_by_status_sync(self, user_id: str, status: str) -> list[MemoryRef]:
        col = self._collection()
        result = col.get(
            where={
                "$and": [
                    {"$or": [{"user_id": user_id}, {"wing": user_id}]},
                    {"status": status},
                ]
            },
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        now = datetime.datetime.now(datetime.timezone.utc)
        keep = []
        for rid, doc, meta in zip(ids, docs, metas):
            md = dict(meta) if isinstance(meta, dict) else {}
            expires = md.get("expires_at")
            if expires and _memory_expired(expires, now):
                continue
            keep.append((rid, doc, md))
        ids = [r[0] for r in keep]
        docs = [r[1] for r in keep]
        metas = [r[2] for r in keep]
        rows = [
            MemoryRef(id=rid, text=doc or "", metadata=dict(meta) if isinstance(meta, dict) else {})
            for rid, doc, meta in zip(ids, docs, metas)
        ]
        rows.sort(key=lambda r: r.metadata.get("added_at", ""), reverse=True)
        return rows

    def _operator_rows_sync(self, user_id: str) -> list[MemoryRef]:
        col = self._collection()
        result = col.get(
            where={"$and": [
                {"$or": [{"user_id": user_id}, {"wing": user_id}]},
                {"status": "approved"},
                {"authority_class": _auth.OPERATOR},
            ]},
            include=["documents", "metadatas"],
        )
        return [
            MemoryRef(id=rid, text=doc or "", metadata=dict(meta) if isinstance(meta, dict) else {})
            for rid, doc, meta in zip(result.get("ids") or [], result.get("documents") or [],
                                      result.get("metadatas") or [])
            if isinstance(meta, dict) and meta.get("authority_class") == _auth.OPERATOR
        ]

    def _get_sync(self, mem_id: str) -> Optional[MemoryRef]:
        col = self._collection()
        result = col.get(ids=[mem_id], include=["documents", "metadatas"])
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        if not ids:
            return None
        meta = metas[0] if isinstance(metas[0], dict) else {}
        return MemoryRef(id=ids[0], text=docs[0] or "", metadata=dict(meta))

    @staticmethod
    def _unpack_tags(raw: Any) -> list[str]:
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(t) for t in raw if t]
        return [t for t in str(raw).split(",") if t]

    def _export_rows(self, user_id: str) -> list[dict[str, Any]]:
        col = self._collection()
        result = col.get(
            where={"$or": [{"user_id": user_id}, {"wing": user_id}]},
            include=["documents", "metadatas"],
        )
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        return [
            {"id": rid, "text": doc, "metadata": meta}
            for rid, doc, meta in zip(ids, docs, metas)
        ]

    async def _tick_access(self, user_id: str, ids: Iterable[str]) -> None:
        """Best-effort metadata update - never raises."""
        ids_list = list(ids)
        if not ids_list:
            return
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        try:
            async with lock:
                await self._run_sync(self._tick_access_sync, user_id, ids_list)
        except Exception:
            pass

    def _tick_access_sync(self, user_id: str, ids: list[str], query_hash: Optional[str] = None) -> None:
        if not ids:
            return
        col = self._collection()
        result = col.get(ids=ids, include=["metadatas"])
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        got_ids = result.get("ids") or []
        got_metas = result.get("metadatas") or []
        new_metas = []
        for meta in got_metas:
            m = dict(meta) if isinstance(meta, dict) else {}
            m["access_count"] = int(m.get("access_count", 0) or 0) + 1
            m["last_accessed"] = now_iso
            if query_hash:
                # Track distinct queries that have surfaced this memory. The hash blob
                # is the dedup oracle AND must stay bounded, so it is capped at
                # _MAX_QUERY_HASHES and never evicts. Once saturated we can no longer
                # tell a genuinely new query from one whose hash was already dropped,
                # so unique_query_count FREEZES at the cap rather than re-counting
                # churned-out queries (which would inflate the promotion/diversity
                # signal). It stays a monotonic, non-decreasing count == min(distinct
                # queries, _MAX_QUERY_HASHES); the cap (256) is far above any promotion
                # threshold so freezing loses no real signal.
                seen_hashes = [h for h in (m.get("_query_hashes") or "").split(",") if h]
                if query_hash not in seen_hashes and len(seen_hashes) < _MAX_QUERY_HASHES:
                    seen_hashes.append(query_hash)
                    m["_query_hashes"] = ",".join(seen_hashes)
                    m["unique_query_count"] = int(m.get("unique_query_count", 0) or 0) + 1
            new_metas.append(m)
        if got_ids:
            # Metadata-only write: col.update() omits documents, so Chroma does NOT
            # recompute embeddings. col.upsert() would re-embed every doc on every
            # recall hit just to bump access_count/last_accessed (pure waste).
            col.update(ids=got_ids, metadatas=new_metas)

    async def tick_access(self, user_id: str, ids: list[str], query: Optional[str] = None) -> None:
        """Public method: bump access_count + last_accessed for the given memory IDs.

        If `query` is provided, also increments `unique_query_count` when this query
        is distinct from previously seen queries (via SHA-1 hash tracking).
        """
        if not ids:
            return
        query_hash: Optional[str] = None
        if query:
            query_hash = hashlib.sha1(query.lower().strip().encode()).hexdigest()[:16]
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        try:
            async with lock:
                await self._run_sync(self._tick_access_sync, user_id, ids, query_hash)
        except Exception:
            pass

    async def tick_consolidation(self, user_id: str, ids: list[str]) -> None:
        """Increment consolidation_count on the given memory IDs (called by deep-sleep pass)."""
        if not ids:
            return
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        try:
            async with lock:
                await self._run_sync(self._tick_consolidation_sync, user_id, ids)
        except Exception:
            pass

    def _tick_consolidation_sync(self, user_id: str, ids: list[str]) -> None:
        col = self._collection()
        result = col.get(ids=ids, include=["metadatas"])
        got_ids  = result.get("ids")       or []
        got_metas = result.get("metadatas") or []
        new_metas = []
        for m in got_metas:
            m = dict(m) if m else {}
            m["consolidation_count"] = int(m.get("consolidation_count", 0) or 0) + 1
            new_metas.append(m)
        if got_ids:
            # Metadata-only write (see _tick_access_sync): col.update() skips the
            # embedding recompute that col.upsert() would force on every doc.
            col.update(ids=got_ids, metadatas=new_metas)

    async def relink_entity(
        self, user_id: str, mem_id: str, entity_type: str, entity_id: str
    ) -> bool:
        """Re-key a still-pending person fact's entity link (metadata-only).

        Used by the idle dream-cycle resolver (``memory_digest``) to promote a
        ``person_pending`` fact to a real ``people.id`` once the contact exists.
        Runs under the SAME per-user lock as ``tick_access`` and re-reads the
        row's current metadata inside the lock, so a concurrent access-tick can't
        clobber the relink (and vice-versa) — only ``entity_type`` / ``entity_id``
        change, every other field is preserved. Uses ``col.update`` (no
        ``documents``) so Chroma keeps the existing embedding (no re-embed).

        Returns True only when a row was actually relinked. No-op (False) when the
        id is unknown, owned by another user, or no longer ``person_pending``
        (idempotent + race-safe).
        """
        if not user_id or not mem_id:
            return False
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        try:
            async with lock:
                return bool(
                    await self._run_sync(
                        self._relink_entity_sync, user_id, mem_id, entity_type, entity_id
                    )
                )
        except Exception as exc:
            logger.warning("memory_service: relink_entity failed id=%s: %s", mem_id, exc)
            return False

    def _relink_entity_sync(
        self, user_id: str, mem_id: str, entity_type: str, entity_id: str
    ) -> bool:
        col = self._collection()
        result = col.get(ids=[mem_id], include=["metadatas"])
        got_ids = result.get("ids") or []
        got_metas = result.get("metadatas") or []
        if not got_ids:
            return False
        m = dict(got_metas[0]) if got_metas and got_metas[0] else {}
        # Never relink another user's row (ownerless legacy rows are treated as
        # the caller's, matching the rest of this module's back-compat stance).
        owner = str(m.get("user_id") or "")
        if owner and owner != user_id:
            return False
        # Only a still-pending person fact is eligible — idempotent under a race
        # (a second relink, or one that lost to a concurrent write, is a no-op).
        if str(m.get("entity_type") or "") != "person_pending":
            return False
        m["entity_type"] = entity_type
        m["entity_id"] = entity_id
        col.update(ids=got_ids, metadatas=[m])
        return True

    async def _append_audit(
        self,
        *,
        mem_id: str,
        user_id: str,
        actor: str,
        action: str,
        before: Optional[dict[str, Any]],
        after: Optional[dict[str, Any]],
        reason: str = "",
    ) -> None:
        try:
            await self._run_sync(
                self._append_audit_sync,
                mem_id, user_id, actor, action, before, after, reason,
            )
        except LiveStoreViolation:
            raise  # a guard trip is a configuration bug, never a "best-effort" miss
        except Exception as exc:
            logger.warning("memory_service: audit append failed: %s", exc)

    def _append_audit_sync(
        self,
        mem_id: str,
        user_id: str,
        actor: str,
        action: str,
        before: Optional[dict[str, Any]],
        after: Optional[dict[str, Any]],
        reason: str,
    ) -> None:
        import json as _json
        assert_write_allowed(getattr(self, "_data_dir", _MEMPALACE_DATA), user_id, "audit append")
        col = self._audit_collection()
        audit_id = str(uuid.uuid4())
        summary = f"{action} {mem_id} by {actor} for {user_id}"
        metadata = {
            "mempalace_id": mem_id,
            "user_id": user_id,
            "actor": actor,
            "action": action,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "before": _json.dumps(before or {}, default=str)[:4000],
            "after": _json.dumps(after or {}, default=str)[:4000],
            "reason": reason,
        }
        # Audit rows are only ever METADATA-filtered (_delete_audit_for_user_sync
        # uses col.get(where=...)); nothing queries them semantically. Providing
        # an explicit constant embedding skips the per-mutation ONNX MiniLM
        # inference chroma would otherwise run on the summary (opensrc-verified,
        # chromadb 0.6.3 CollectionCommon._validate_and_prepare_upsert_request:
        # embeds only when embeddings is None) and stops the audit HNSW index
        # growing meaningful vectors it will never search. 384-dim matches the
        # collection's existing MiniLM rows; unit-basis (not all-zero) so the
        # vector stays valid under any hnsw space. Memory-pressure profile
        # candidate #5 (docs/knowledge/memory-pressure-profile.md).
        col.upsert(
            ids=[audit_id],
            documents=[summary],
            metadatas=[metadata],
            # fresh list per call: chroma 0.6.3 requires list-of-lists, and a
            # per-call copy means chroma can never mutate the shared constant
            embeddings=[list(_AUDIT_NULL_EMBEDDING)],
        )


    def _entity_ids_sync(self, entity_id: str, user_id: str) -> list[str]:
        col = self._collection()
        results = col.get(
            where={"$and": [
                {"$or": [{"user_id": user_id}, {"wing": user_id}]},
                {"entity_id": entity_id},
            ]},
            include=["metadatas"],
        )
        return results.get("ids", []) if results else []

    def _entity_rows_sync(self, entity_ids: list[str], user_id: str) -> list["MemoryRef"]:
        col = self._collection()
        results = col.get(
            where={"$and": [
                {"$or": [{"user_id": user_id}, {"wing": user_id}]},
                {"entity_id": {"$in": entity_ids}} if len(entity_ids) > 1
                else {"entity_id": entity_ids[0]},
            ]},
            include=["documents", "metadatas"],
        )
        refs: list[MemoryRef] = []
        for i, doc, meta in zip(
            results.get("ids", []),
            results.get("documents", []) or [],
            results.get("metadatas", []) or [],
        ):
            refs.append(MemoryRef(id=i, text=doc or "", metadata=meta or {}))
        return refs

    async def list_by_entity(
        self, user_id: str, entity_ids: list[str], *, status: str = "approved"
    ) -> list["MemoryRef"]:
        """All of a user's rows linked to any of ``entity_ids`` (person uuid or
        pending slug), filtered to ``status``. Used by the person-extractor's
        entity-keyed reconciliation — compact "Name: value" rows have no
        parseable attribute for text reconciliation, so supersession for them
        is decided by linkage + fact kind instead."""
        ids = [e for e in entity_ids if e]
        if not ids:
            return []
        self._require(user_id, "user_id is required")
        try:
            rows = await self._run_sync(self._entity_rows_sync, ids, user_id)
        except Exception as exc:
            logger.debug("list_by_entity failed (%s)", type(exc).__name__)
            return []
        if status:
            rows = [r for r in rows if str(r.metadata.get("status") or "") == status]
        # Expired rows are invisible to every other read path — an expired
        # entity row must not swallow a fresh restatement as a dedup-skip.
        rows = [
            r for r in rows
            if not (r.metadata.get("expires_at")
                    and _memory_expired(r.metadata.get("expires_at")))
        ]
        return rows

    async def archive_by_entity(self, entity_id: str, user_id: str) -> int:
        """Archive all MemPalace facts for a given entity (e.g. when a person is deleted).

        Queries Chroma for documents whose metadata has entity_id=<entity_id> and
        user_id=<user_id>, then archives each one. Returns count archived.
        """
        try:
            # Offload the blocking full-collection metadata scan to the executor,
            # matching every other Chroma access in this module — running col.get()
            # directly here would block the event loop.
            ids = await self._run_sync(self._entity_ids_sync, entity_id, user_id)
            archived = 0
            for mem_id in ids:
                try:
                    await self.review(
                        mem_id,
                        decision="archive",
                        actor="system",
                        note="entity_deleted",
                    )
                    archived += 1
                except Exception as exc:
                    logger.debug("archive_by_entity: skip %s: %s", mem_id, exc)
            return archived
        except Exception as exc:
            logger.warning("archive_by_entity failed for entity %s: %s", entity_id, exc)
            return 0


_service_singleton: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = MemoryService()
    return _service_singleton


__all__ = ["MemoryService", "MemoryRef", "MemoryServiceError", "get_memory_service", "scrub_pii"]
