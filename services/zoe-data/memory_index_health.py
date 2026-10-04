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

Fresh index: chroma persists a new segment's index files lazily (sync threshold), so right
after a rebuild there is no ``index_metadata.pickle`` yet. That is reported as ratio 1.0
(nothing deleted since the rebuild), never as unknown — otherwise the weekly trigger could
not tell "just compacted" from "unreadable".
"""
from __future__ import annotations

import os
import pickle
import sqlite3
from pathlib import Path
from typing import Any

DRAWERS = "mempalace_drawers"
ADVISE_RATIO = 3.0   # elements ever added / live rows; above this the graph is mostly tombstones


def tombstone_ratio(total_added: int, live: int) -> float:
    """elements ever added ÷ live rows (∞ when nothing is live but elements were added)."""
    if live <= 0:
        return float("inf") if total_added > 0 else 0.0
    return total_added / live


def compaction_advised(total_added: int, live: int, *, threshold: float = ADVISE_RATIO) -> bool:
    return live > 0 and tombstone_ratio(total_added, live) >= threshold


def _total_elements_added(pickle_path: Path) -> int:
    """``total_elements_added`` from either pickle shape (0.6 object attr, 1.x dict key)."""
    with open(pickle_path, "rb") as fh:
        meta = pickle.load(fh)
    if isinstance(meta, dict):
        return int(meta.get("total_elements_added") or 0)
    return int(getattr(meta, "total_elements_added", 0) or 0)


def report(palace: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Per collection: live rows, elements ever added, ratio, advice. Read-only."""
    palace = Path(os.path.expanduser(str(palace)))
    con = sqlite3.connect(f"file:{palace / 'chroma.sqlite3'}?mode=ro", uri=True)
    out: list[dict[str, Any]] = []
    try:
        for cid, name in con.execute("SELECT id, name FROM collections"):
            live = con.execute(
                "SELECT count(*) FROM embeddings e JOIN segments s ON s.id = e.segment_id WHERE s.collection = ?",
                (cid,)).fetchone()[0]
            seg = con.execute(
                "SELECT id FROM segments WHERE collection = ? AND scope = 'VECTOR'", (cid,)).fetchone()
            pkl = (palace / seg[0] / "index_metadata.pickle") if seg else None
            fresh = bool(seg) and not pkl.exists()
            total: int | None
            error = ""
            if fresh:
                total = live  # not persisted yet: nothing deleted since the rebuild
            elif pkl is None:
                total = None  # metadata-only collection: no vector segment at all
            else:
                try:
                    total = _total_elements_added(pkl)
                except Exception as exc:  # noqa: BLE001 — a report must never crash on a pickle
                    total, error = None, f"index metadata unreadable: {type(exc).__name__}"
            out.append({
                "collection": name,
                "live_rows": int(live),
                "elements_added": total,
                "tombstone_ratio": (round(tombstone_ratio(total, live), 2) if total is not None else None),
                "compaction_advised": (compaction_advised(total, live) if total is not None else None),
                "fresh": fresh,
                "note": ("fresh index — not persisted yet, ratio 1.0" if fresh else error),
            })
    finally:
        con.close()
    return out


def index_health(palace: str | os.PathLike[str], collection: str = DRAWERS) -> dict[str, Any]:
    """The one collection's row from :func:`report`, plus ``palace`` + ``threshold``.

    ``compaction_advised`` is always a bool here (False when the ratio is unknown, so a
    trigger never acts on an unreadable index); ``tombstone_ratio`` may be None.
    """
    rows = {r["collection"]: r for r in report(palace)}
    row = rows.get(collection) or {
        "collection": collection, "live_rows": 0, "elements_added": None, "tombstone_ratio": None,
        "compaction_advised": None, "fresh": False, "note": "collection not found",
    }
    row = dict(row)
    row["compaction_advised"] = bool(row.get("compaction_advised"))
    row["threshold"] = ADVISE_RATIO
    row["palace"] = str(Path(os.path.expanduser(str(palace))))
    return row
