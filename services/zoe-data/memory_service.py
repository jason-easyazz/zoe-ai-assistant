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
    client = _AUDIT_CLIENTS.get(key)
    if client is None:
        with _AUDIT_CLIENTS_LOCK:
            client = _AUDIT_CLIENTS.get(key)
            if client is None:
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
        return client.get_collection(_DRAWERS_COLLECTION, embedding_function=ef)
    except Exception:
        names = {getattr(c, "name", c) for c in client.list_collections()}
        if _DRAWERS_COLLECTION in names:
            raise  # it exists: this is a real error, never paper over it with a create
        return client.create_collection(
            _DRAWERS_COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=ef
        )


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
_OPS_COND = threading.Condition()   # guards _ACTIVE_OPS; notified when a lease is released
_ACTIVE_OPS = 0
_OP_THREAD = threading.local()      # .depth: this thread's lease nesting (re-entrant)
_COMPACT_LOCK = threading.Lock()    # one compaction at a time
_COMPACT_BATCH = 100
_COMPACT_PROBE = "When did I tell you about the dentist?"   # the measured failing sentence
_COMPACT_BACKUPS_DIR = os.path.expanduser("~/.zoe/palace-backups")


def _wait_for_maintenance_gate(timeout: float | None = None) -> None:
    wait = _MAINTENANCE_WAIT_S if timeout is None else timeout
    if _MAINTENANCE_OPEN.is_set() or _MAINTENANCE_OPEN.wait(wait):
        return
    raise MemoryServiceError(
        f"memory index maintenance in progress: the drawers collection did not reopen within {wait:g}s"
    )


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
        if not _MAINTENANCE_OPEN.wait(max(deadline - time.monotonic(), 0.0)):
            raise MemoryServiceError(
                f"memory index maintenance in progress: the drawers collection did not reopen within {wait:g}s"
            )
        with _OPS_COND:
            if _MAINTENANCE_OPEN.is_set():
                _ACTIVE_OPS += 1
                break
    _OP_THREAD.depth = 1
    try:
        yield
    finally:
        _OP_THREAD.depth = 0
        with _OPS_COND:
            _ACTIVE_OPS -= 1
            _OPS_COND.notify_all()


def _leased_call(fn, *args):
    with collection_op():
        return fn(*args)


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
    BEFORE the delete aborts with no change; any failure AFTER it restores from the export
    (create + re-add) and raises :class:`IndexCompactionError` whose ``report`` says
    ``restored``. Nothing is re-embedded. Logs ``MEMORY_INDEX_COMPACT``.
    """
    import tarfile

    from memory_index_health import index_health

    if getattr(_OP_THREAD, "depth", 0):
        raise IndexCompactionError(
            "compaction must not run under a collection lease (it would wait for itself) — "
            "schedule it outside _run_sync", {"changed": False})
    if not _COMPACT_LOCK.acquire(blocking=False):
        raise IndexCompactionError("a compaction is already running", {"changed": False})
    t0 = time.monotonic()
    report: dict[str, Any] = {"changed": False, "restored": False, "space": None}
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

            client.delete_collection(_DRAWERS_COLLECTION)
            report["changed"] = True
            try:
                new = _rebuild_drawers(client, ef, space, rows)
                verify = _verify_rebuilt_drawers(new, rows, probe)
                report["verify"] = verify
                if not verify["ok"]:
                    raise MemoryServiceError(f"verification failed: {verify}")
            except Exception as exc:  # noqa: BLE001 — after the delete: put the rows back
                try:
                    try:
                        client.delete_collection(_DRAWERS_COLLECTION)
                    except Exception:  # noqa: BLE001 — may not exist yet
                        pass
                    restored = _rebuild_drawers(client, ef, space, rows)
                    report["restored"] = int(restored.count()) == n
                    report["restored_count"] = int(restored.count())
                except Exception as rexc:  # noqa: BLE001
                    report["restore_error"] = f"{type(rexc).__name__}: {rexc}"
                    logger.error("MEMORY_INDEX_COMPACT restore FAILED — restore the tar backup %s: %s",
                                 report.get("backup_tar"), rexc)
                raise IndexCompactionError(
                    f"{type(exc).__name__}: {exc} (after delete; restored={report['restored']})", report
                ) from exc
        finally:
            _MAINTENANCE_OPEN.set()
        report.update(ok=True, elements_added=n, seconds=round(time.monotonic() - t0, 2))
        logger.warning(
            "MEMORY_INDEX_COMPACT before=%s after=%s elements_added=%s seconds=%s space=%s backup=%s",
            (report.get("before") or {}).get("elements_added"), n, n, report["seconds"], space, tar_path,
        )
        return report
    except IndexCompactionError as exc:
        exc.report["seconds"] = round(time.monotonic() - t0, 2)
        logger.error("MEMORY_INDEX_COMPACT failed changed=%s restored=%s error=%s",
                     exc.report.get("changed"), exc.report.get("restored"), exc)
        raise
    finally:
        _COMPACT_LOCK.release()

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


def _implicit_supersede_on() -> bool:
    """``ZOE_MEMORY_IMPLICIT_SUPERSEDE`` (memory_supersede.enabled), read per call."""
    try:
        from memory_supersede import enabled
        return enabled()
    except Exception:
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
    ) -> Optional[MemoryRef]:
        """Store a fact. Returns None when silently dropped.

        When ``scope`` is None, ``metadata["scope"]`` is treated as the
        authoritative memory scope and is validated before any durable write.
        """
        self._require(user_id, "user_id is required")
        if not text or not text.strip():
            raise MemoryServiceError("empty text")

        # Opt-out is enforced HERE, the one durable-write chokepoint, so every
        # automatic writer (per-turn extractor, turn digest, person extractors,
        # idle/nightly digest, consolidation, synthesis) honours it without each
        # caller remembering to. Explicit teach sources are never dropped.
        if source in MEMORY_OPT_OUT_SOURCES and (opt_out or await _user_opted_out(user_id)):
            self._bump("opt_out", source)
            return None

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

        # Forget tombstones: "forget everything about X" shadows X for a few
        # minutes so in-flight/late extractor passes can't resurrect the name
        # (ingest is the one durable-write chokepoint every lane funnels
        # through). Explicit re-teach paths clear the tombstone first.
        try:
            from memory_tombstones import EXPLICIT_TEACH_SOURCES, matching_tombstone
            if source not in EXPLICIT_TEACH_SOURCES and matching_tombstone(user_id, scrubbed):
                self._bump("tombstone_drop", source)
                logger.info(
                    "memory_service: ingest dropped — mentions a just-forgotten "
                    "entity (user=%s source=%s)", user_id, source,
                )
                return None
        except Exception:
            pass  # the guard must never break ingestion

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

            metadata = self._build_metadata(
                user_id=user_id,
                source=source,
                session_id=session_id,
                user_turn_id=user_turn_id,
                memory_type=memory_type,
                confidence=confidence,
                status=status,
                tags=tags or [],
                entity_type=entity_type,
                entity_id=entity_id,
                expires_at=expires_at,
                source_excerpt=source_excerpt,
                scope=scope,
                extra_metadata=metadata,
                idem_key=idem_key,
                text=scrubbed,
            )

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
                if existing_status not in {"", "pending"}:
                    self._bump("dedup", source)
                    if _METRICS_OK:
                        memory_dedup_skip_count.inc()
                    self._remember_seen_key(user_id, idem_key)
                    return None

            try:
                await self._run_sync(
                    self._write_row, mem_id, scrubbed, metadata
                )
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
    ) -> list[MemoryRef]:
        if is_guest_memory_user(user_id):
            return []
        self._require(user_id, "user_id is required")
        if not query or not query.strip():
            return []
        t0 = time.monotonic()
        # Increment 2b: resolve the relationship-graph neighbourhood for the
        # query's person on the async side (the graph fetch is async + needs the
        # DB; the blend is sync). Best-effort and gated: OFF ⇒ {} ⇒ no boost,
        # zero DB work, never crashes or slows a turn.
        depth_by_pid = await self._graph_depth_by_pid(query, user_id)
        try:
            rows = await asyncio.wait_for(
                self._run_sync(
                    self._semantic_search, query, user_id, limit, depth_by_pid
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
        if ids:
            # Pass query for unique_query_count tracking in dreaming memory
            self._track_background_task(
                self.tick_access(user_id, ids, query=query),
                name="memory_tick_access_search",
            )
        return rows

    async def delete_user(self, user_id: str, *, actor: str) -> int:
        """Right-to-be-forgotten. Returns number of rows removed."""
        self._require(user_id, "user_id is required")
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            try:
                ids = await self._run_sync(self._list_ids_for_user, user_id)
                if ids:
                    await self._run_sync(self._delete_ids, ids)
                await self._run_sync(self._delete_audit_for_user_sync, user_id)
            except Exception as exc:
                raise MemoryServiceError(f"delete_user failed: {exc}") from exc
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
        actor: str = "system",
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
                await self.review(
                    mid,
                    decision="archive",
                    actor=actor,
                    note=f"soft-archive: score<{score_threshold}, age>={min_age_days}d",
                )
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
    ) -> Optional[MemoryRef]:
        """Approve / reject / edit a pending memory.

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
                if note:
                    new_meta["review_note"] = note[:1024]
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
                return MemoryRef(id=mem_id, text=current.text, metadata=new_meta)

            # decision == "edit"
            new_text = (edits or current.text).strip()
            if not new_text:
                raise MemoryServiceError("edit decision requires non-empty edits")
            scrubbed, reject = scrub_pii(new_text)
            if reject:
                raise MemoryServiceError(f"edit rejected by PII scrubber: {reject}")
            current_scope = current.metadata.get("scope")
            new_meta = self._build_metadata(
                user_id=user_id,
                source=current.metadata.get("source", "review_ui"),
                session_id=current.metadata.get("session_id"),
                user_turn_id=current.metadata.get("user_turn_id"),
                memory_type=current.metadata.get("memory_type", "fact"),
                confidence=float(current.metadata.get("confidence", 0.7)),
                status="approved",
                tags=self._unpack_tags(current.metadata.get("tags", "")),
                entity_type=current.metadata.get("entity_type"),
                entity_id=current.metadata.get("entity_id"),
                expires_at=current.metadata.get("expires_at"),
                source_excerpt=(source_excerpt if source_excerpt
                                else current.metadata.get("source_excerpt")),
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
                "valid_from", "invalid_at",
                # importance is a computed function of the row's TEXT (like
                # memory_type/confidence above), so it must be recomputed for the
                # edited text by _build_metadata — never carried forward, or an
                # edit from a high-stakes fact to ordinary text would keep a stale
                # 0.9 boost.
                "importance",
            }
            for key, value in current.metadata.items():
                if key in _EDIT_CARRY_FORWARD_SKIP or key in new_meta:
                    continue
                new_meta[key] = value
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
                if _implicit_supersede_on():
                    old_meta["invalid_at"] = new_meta["added_ts"]
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
        got = col.get(ids=[old_id, new_id], include=["metadatas"])
        metas = {i: dict(m or {}) for i, m in zip(got.get("ids") or [], got.get("metadatas") or [])}
        old_m, new_m = metas.get(old_id), metas.get(new_id)
        if old_m is None or new_m is None:
            return False
        if any(str(m.get("user_id") or m.get("wing") or "") != user_id for m in (old_m, new_m)):
            return False
        if str(old_m.get("status") or "") != "approved":
            return False
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        old_m.update(status="superseded", superseded_by_id=new_id, invalid_at=now)
        new_m.setdefault("valid_from", new_m.get("added_ts") or now)
        if not new_m.get("supersedes_id"):
            new_m["supersedes_id"] = old_id
        col.update(ids=[old_id, new_id], metadatas=[old_m, new_m])
        return True

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
        return await loop.run_in_executor(None, index_health, self._data_dir)

    async def compact_index(self) -> dict[str, Any]:
        """In-process drawers index compaction (see ``compact_drawers_index_sync``).
        Raises ``IndexCompactionError`` (``.report``) when it did not complete. Deliberately
        NOT via ``_run_sync``: that path takes a collection lease, and the compaction drains
        the leases — it would wait for itself."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, compact_drawers_index_sync, self._data_dir)

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
    ) -> dict[str, Any]:
        """Build durable metadata for a memory row.

        When ``scope`` is None, ``extra_metadata["scope"]`` is promoted to the
        first-class Zoe memory scope and drives legacy visibility mapping.
        """
        _now_dt = datetime.datetime.utcnow()
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
        if _implicit_supersede_on():
            # Validity interval (gap #4): valid_from = capture time; invalid_at is
            # written when the row is superseded (supersede_by / review edit).
            md["valid_from"] = md["added_ts"]
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

    def _bump(self, status: str, source: str) -> None:
        if _METRICS_OK:
            memory_write_count.labels(source=source, status=status).inc()

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

    def _semantic_search(
        self,
        query: str,
        user_id: str,
        limit: int,
        depth_by_pid: dict[str, int] | None = None,
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
                if not _memory_status_visible(md):
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
            semantic = (1.0 / (1.0 + dist)) * conf * math.exp(-_LAMBDA * age_days)
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
        result = col.get(where={"user_id": user_id})
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
