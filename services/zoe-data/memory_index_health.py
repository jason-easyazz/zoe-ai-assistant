"""Tombstone health of the MemPalace drawers HNSW index — stdlib only, read-only.

Why (measured 2026-10-04): chroma 1.x never compacts a persistent HNSW index. Every
deleted row (demo-user teardown after each bar / day-sim run, consolidation churn) stays
in the graph as a tombstone; the live drawers index held 1,591 elements for 258 live
rows and an owner-filtered sentence query returned 0 rows. ``elements ever added ÷ live
rows`` is the ratio; at ``ADVISE_RATIO`` (3) a compaction is advised.

Shared by the service (``GET /api/memories/maintenance/index-health``) and the operator
script ``scripts/maintenance/compact_drawers_index.py`` — ONE implementation. It reads
SQLite ``mode=ro`` and the segment's ``index_metadata.pickle``; it never imports chromadb,
so it is safe while zoe-data runs and under any interpreter.

What is persisted, and what is not (chroma 1.5.9, ``PersistentLocalHnswSegment``): the
pickle (``total_elements_added``) and the vector segment's ``max_seq_id`` row are written
together, ONLY by ``_persist``, which runs every ``hnsw:sync_threshold`` (1000) log
records — so after a rebuild, and for any small collection, hundreds of adds and deletes
can sit in memory with nothing on disk. Those operations are still in the write-ahead log:
``embeddings_queue`` is purged only below ``min(max_seq_id)`` over the collection's
segments (``COALESCE(…, -1)`` for a segment that never persisted), so every record above
the vector segment's persisted seq is still there. Hence ``elements_added`` here =
persisted ``total_elements_added`` + ADD/UPSERT records in the WAL above that seq (an
upper bound: an upsert of an existing id reuses its label), and a never-persisted index
is counted from the WAL alone — ``fresh`` is informational, it never forces ratio 1.0.
When neither the pickle nor the WAL can tell, the ratio is UNKNOWN (``None``), never
guessed; the weekly trigger then compacts at most once per period instead of skipping.
"""
from __future__ import annotations

import os
import pickle
import sqlite3
from pathlib import Path
from typing import Any

DRAWERS = "mempalace_drawers"
ADVISE_RATIO = 3.0   # elements ever added / live rows; above this the graph is mostly tombstones
_WAL_ADD_OPS = (0, 2)   # chroma's embeddings_queue operation codes: ADD=0 UPDATE=1 UPSERT=2 DELETE=3
_WAL_DELETE_OP = 3


def tombstone_ratio(total_added: int | None, live: int) -> float | None:
    """elements ever added ÷ live rows. ``None`` (never ``inf``/``nan`` — the API payload
    must stay JSON-compliant) when nothing is live or the total is unknown."""
    if total_added is None or live <= 0:
        return None
    return total_added / live


def compaction_advised(total_added: int | None, live: int, *, threshold: float = ADVISE_RATIO) -> bool | None:
    """True/False when the ratio is known; ``None`` when it is not (callers decide)."""
    ratio = tombstone_ratio(total_added, live)
    return None if ratio is None else ratio >= threshold


def _total_elements_added(pickle_path: Path) -> int:
    """``total_elements_added`` from either pickle shape (0.6 object attr, 1.x dict key)."""
    with open(pickle_path, "rb") as fh:
        meta = pickle.load(fh)
    if isinstance(meta, dict):
        return int(meta.get("total_elements_added") or 0)
    return int(getattr(meta, "total_elements_added", 0) or 0)


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _wal_tail(con: sqlite3.Connection, cid: str, above_seq: int) -> dict[str, int] | None:
    """ADD/UPSERT and DELETE records for this collection's topic above ``above_seq``, or
    ``None`` when this palace has no write-ahead log table (nothing to derive from)."""
    if not _table_exists(con, "embeddings_queue"):
        return None
    out = {"adds": 0, "deletes": 0, "ops": 0}
    for op, n in con.execute(
        "SELECT operation, count(*) FROM embeddings_queue WHERE topic LIKE ? AND seq_id > ? GROUP BY operation",
        (f"%/{cid}", above_seq),
    ):
        out["ops"] += int(n)
        if op in _WAL_ADD_OPS:
            out["adds"] += int(n)
        elif op == _WAL_DELETE_OP:
            out["deletes"] += int(n)
    return out


def _persisted_seq(con: sqlite3.Connection, segment_id: str) -> int | None:
    if not _table_exists(con, "max_seq_id"):
        return None
    row = con.execute("SELECT seq_id FROM max_seq_id WHERE segment_id = ?", (segment_id,)).fetchone()
    return None if row is None else int(row[0])


def report(palace: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Per collection: live rows, elements ever added (persisted + WAL tail), ratio, advice.
    Read-only; every float finite; unknowns are ``None``."""
    palace = Path(os.path.expanduser(str(palace)))
    con = sqlite3.connect(f"file:{palace / 'chroma.sqlite3'}?mode=ro", uri=True)
    out: list[dict[str, Any]] = []
    try:
        for cid, name in con.execute("SELECT id, name FROM collections"):
            live = int(con.execute(
                "SELECT count(*) FROM embeddings e JOIN segments s ON s.id = e.segment_id WHERE s.collection = ?",
                (cid,)).fetchone()[0])
            seg = con.execute(
                "SELECT id FROM segments WHERE collection = ? AND scope = 'VECTOR'", (cid,)).fetchone()
            total: int | None = None
            persisted: int | None = None
            fresh = False
            note = ""
            tail: dict[str, int] | None = None
            if seg is None:
                note = "metadata-only collection: no vector segment"
            else:
                pkl = palace / seg[0] / "index_metadata.pickle"
                seq = _persisted_seq(con, seg[0])
                if pkl.exists():
                    try:
                        persisted = _total_elements_added(pkl)
                    except Exception as exc:  # noqa: BLE001 — a report must never crash on a pickle
                        note = f"index metadata unreadable: {type(exc).__name__}"
                    else:
                        tail = _wal_tail(con, cid, -1 if seq is None else seq)
                        total = persisted + (tail["adds"] if tail else 0)
                        if tail is None:
                            note = "no write-ahead log table: unpersisted adds not counted"
                elif seq is not None:
                    note = "index files missing although the segment persisted — unknown"
                else:
                    fresh = True
                    tail = _wal_tail(con, cid, -1)
                    if tail is None:
                        note = "index not persisted yet and no write-ahead log — unknown"
                    else:
                        total = tail["adds"]   # the WAL is complete for a never-persisted segment
                        note = "index not persisted yet — counted from the write-ahead log"
            ratio = tombstone_ratio(total, live)
            out.append({
                "collection": name,
                "live_rows": live,
                "elements_added": total,
                "persisted_elements_added": persisted,
                "unpersisted_ops": None if tail is None else tail["ops"],
                "tombstone_ratio": None if ratio is None else round(ratio, 2),
                "ratio_known": ratio is not None,
                "compaction_advised": compaction_advised(total, live),
                "fresh": fresh,
                "note": note,
            })
    finally:
        con.close()
    return out


def index_health(palace: str | os.PathLike[str], collection: str = DRAWERS) -> dict[str, Any]:
    """The one collection's row from :func:`report`, plus ``palace`` + ``threshold``.

    ``compaction_advised`` is ``True``/``False`` when the ratio is known and ``None`` when
    it is not (``ratio_known`` says which); ``tombstone_ratio`` is a finite float or None.
    """
    rows = {r["collection"]: r for r in report(palace)}
    row = rows.get(collection) or {
        "collection": collection, "live_rows": 0, "elements_added": None, "persisted_elements_added": None,
        "unpersisted_ops": None, "tombstone_ratio": None, "ratio_known": False,
        "compaction_advised": None, "fresh": False, "note": "collection not found",
    }
    row = dict(row)
    row["threshold"] = ADVISE_RATIO
    row["palace"] = str(Path(os.path.expanduser(str(palace))))
    return row
