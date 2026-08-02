#!/usr/bin/env python3
"""Report HNSW tombstone accumulation in the vector memory store.

Chroma's persistent HNSW index never reclaims space on delete: it drops the id
from `id_to_label` but leaves the vector in `data_level0.bin`. So
`total_elements_added - len(id_to_label)` is dead weight that grows forever
under the memory-consolidation/forget paths. Enough of it and every search pays
for vectors nobody can reach; it also inflates the index files that had to be
rebuilt after the 2026-07-31 torn-persist incident.

READ-ONLY BY DEFAULT. It reads the segment pickle and SQLite (`mode=ro`) and
prints a report. Compaction is genuinely destructive (delete collection +
re-embed every row) and lives behind `--execute` plus the guards below, per the
scripts/ contract that destructive maintenance is dry-run by default.

Thresholds are grounded in a live measurement (2026-08-02): drawers 0% (freshly
rebuilt), audit 19.3%, one legacy segment 21.8%. ~20% is therefore the NORMAL
resting state for an active collection, not a problem — the warn threshold sits
above it deliberately so routine churn is not alarming.

Exit codes: 0 = all below warn, 1 = error, 2 = at least one collection at/over
the warn threshold (so a timer or CI lane can gate on it).
"""
from __future__ import annotations

import argparse
import glob
import os
import pickle
import sqlite3
import subprocess
import sys

DEFAULT_PALACE = "~/.mempalace"
WARN_RATIO = 0.25
CRITICAL_RATIO = 0.40


def _segment_stats(palace: str) -> list[dict]:
    db = os.path.join(palace, "chroma.sqlite3")
    if not os.path.exists(db):
        raise SystemExit(f"tombstone check: no such palace database: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    seg2col = {s: (scope, col) for s, scope, col in con.execute(
        "SELECT id, scope, collection FROM segments")}
    names = dict(con.execute("SELECT id, name FROM collections"))
    con.close()

    out = []
    for d in sorted(glob.glob(os.path.join(palace, "*", ""))):
        seg_id = os.path.basename(d.rstrip("/"))
        pkl = os.path.join(d, "index_metadata.pickle")
        if not os.path.exists(pkl):
            continue  # metadata-only segment: no HNSW index, nothing to compact
        try:
            with open(pkl, "rb") as fh:
                meta = pickle.load(fh)
        except Exception as exc:
            out.append({"name": f"<unreadable {seg_id[:8]}>", "error": str(exc)})
            continue
        _, col = seg2col.get(seg_id, ("?", "?"))
        total = int(getattr(meta, "total_elements_added", 0) or 0)
        live = len(getattr(meta, "id_to_label", {}) or {})
        out.append({
            "name": names.get(col, f"<orphan segment {seg_id[:8]}>"),
            "segment": seg_id,
            "path": d,
            "added": total,
            "live": live,
            "tombstones": max(total - live, 0),
            "ratio": (1 - live / total) if total else 0.0,
            "bytes": os.path.getsize(os.path.join(d, "data_level0.bin"))
            if os.path.exists(os.path.join(d, "data_level0.bin")) else 0,
        })
    return out


def report(palace: str, warn: float, critical: float) -> int:
    stats = _segment_stats(palace)
    if not stats:
        print("tombstone check: no vector segments found")
        return 0
    worst = 0.0
    print(f"{'collection':34s} {'added':>7s} {'live':>7s} {'dead':>6s} {'ratio':>7s}  status")
    for s in sorted(stats, key=lambda x: -x.get("ratio", 0)):
        if "error" in s:
            print(f"{s['name']:34s} {'':>7s} {'':>7s} {'':>6s} {'':>7s}  UNREADABLE: {s['error']}")
            continue
        ratio = s["ratio"]
        worst = max(worst, ratio)
        status = "ok"
        if ratio >= critical:
            status = "COMPACT RECOMMENDED"
        elif ratio >= warn:
            status = "WARN"
        print(f"{s['name'][:34]:34s} {s['added']:7d} {s['live']:7d} "
              f"{s['tombstones']:6d} {ratio:6.1%}  {status}")
    reclaim = sum(
        int(s["bytes"] * s["ratio"]) for s in stats if "error" not in s and s["bytes"])
    print(f"\nestimated reclaimable index bytes: {reclaim/1024/1024:.1f} MB")
    if worst >= warn:
        print(f"\nAt least one collection is at/over the {warn:.0%} warn threshold.")
        print("Compaction requires a re-embed of every row: run with --execute while "
              "zoe-data is STOPPED and the box has RAM headroom.")
        return 2
    return 0


def compact(palace: str, collection: str, *, assume_yes: bool) -> int:
    """Delete + re-embed one collection. Destructive; heavily guarded."""
    # Guard 1: never rebuild underneath a live writer. Concurrent writes during a
    # delete+re-add are how you turn a slow index into a missing one.
    probe = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "3",
         "http://localhost:8000/health"], capture_output=True, text=True)
    if probe.stdout.strip() == "200":
        print("REFUSING: zoe-data is serving on :8000. Stop it first:", file=sys.stderr)
        print("  systemctl --user stop zoe-data", file=sys.stderr)
        return 1

    stats = {s["name"]: s for s in _segment_stats(palace) if "error" not in s}
    if collection not in stats:
        print(f"REFUSING: no such collection {collection!r}. Known: "
              f"{', '.join(sorted(stats))}", file=sys.stderr)
        return 1
    s = stats[collection]

    # Guard 2: an export before any destructive step, always.
    here = os.path.dirname(os.path.abspath(__file__))
    print("taking a pre-compaction export first...")
    rc = subprocess.run([sys.executable, os.path.join(here, "export_memory_store.py"),
                         "--keep", "14"]).returncode
    if rc != 0:
        print("REFUSING: pre-compaction export failed; not touching the index.", file=sys.stderr)
        return 1

    print(f"\nabout to REBUILD {collection}: {s['live']} live rows re-embedded, "
          f"{s['tombstones']} tombstones dropped")
    if not assume_yes:
        if input(f"type the collection name to confirm: ").strip() != collection:
            print("aborted.")
            return 1

    import chromadb  # imported late: the read-only path must not need it
    client = chromadb.PersistentClient(path=palace)
    col = client.get_collection(collection)
    data = col.get(include=["documents", "metadatas"])
    ids, docs, metas = data["ids"], data["documents"], data["metadatas"]
    print(f"read {len(ids)} rows; deleting and re-adding...")
    client.delete_collection(collection)
    fresh = client.create_collection(collection)
    B = 256
    for i in range(0, len(ids), B):
        fresh.add(ids=ids[i:i+B], documents=docs[i:i+B], metadatas=metas[i:i+B])
        print(f"  {min(i+B, len(ids))}/{len(ids)}")
    print(f"done: {collection} rebuilt with {len(ids)} rows")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--palace", default=DEFAULT_PALACE)
    ap.add_argument("--warn", type=float, default=WARN_RATIO)
    ap.add_argument("--critical", type=float, default=CRITICAL_RATIO)
    ap.add_argument("--execute", metavar="COLLECTION",
                    help="DESTRUCTIVE: rebuild this collection (zoe-data must be stopped)")
    ap.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    args = ap.parse_args(argv)
    palace = os.path.expanduser(args.palace)
    try:
        if args.execute:
            return compact(palace, args.execute, assume_yes=args.yes)
        return report(palace, args.warn, args.critical)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"tombstone check FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
