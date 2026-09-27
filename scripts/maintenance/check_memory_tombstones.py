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
the warn threshold (so a timer or CI lane can gate on it), 3 = no collection over
warn but at least one whose tombstone count is UNKNOWN (a chromadb 1.x segment
with an HNSW index but no persisted index metadata yet) — never reported as ok.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pickle
import sqlite3
import subprocess
import sys

DEFAULT_PALACE = "~/.mempalace"
WARN_RATIO = 0.25
CRITICAL_RATIO = 0.40


def _meta_field(meta, name: str, default):
    """chromadb 0.6 pickles a PersistentData OBJECT; 1.x (Rust) pickles a plain DICT with the
    same keys. getattr() on the dict silently read 0/0 for every 1.x segment (B0.8)."""
    if isinstance(meta, dict):
        return meta.get(name, default)
    return getattr(meta, name, default)


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
            if os.path.exists(os.path.join(d, "header.bin")):
                # chromadb 1.x writes index_metadata.pickle only once a segment reaches its
                # sync threshold, so a small HNSW index can exist without one. Report it
                # instead of silently dropping the collection from the table.
                _, col = seg2col.get(seg_id, ("?", "?"))
                out.append({"name": names.get(col, f"<orphan segment {seg_id[:8]}>"),
                            "segment": seg_id, "path": d, "pending": True, "added": 0,
                            "live": 0, "tombstones": 0, "ratio": 0.0, "bytes": 0})
            continue  # otherwise a metadata-only segment: no HNSW index, nothing to compact
        try:
            with open(pkl, "rb") as fh:
                meta = pickle.load(fh)
        except Exception as exc:
            out.append({"name": f"<unreadable {seg_id[:8]}>", "error": str(exc)})
            continue
        _, col = seg2col.get(seg_id, ("?", "?"))
        total = int(_meta_field(meta, "total_elements_added", 0) or 0)
        live = len(_meta_field(meta, "id_to_label", {}) or {})
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
    unknown = 0
    print(f"{'collection':34s} {'added':>7s} {'live':>7s} {'dead':>6s} {'ratio':>7s}  status")
    for s in sorted(stats, key=lambda x: -x.get("ratio", 0)):
        if "error" in s:
            print(f"{s['name']:34s} {'':>7s} {'':>7s} {'':>6s} {'':>7s}  UNREADABLE: {s['error']}")
            continue
        if s.get("pending"):
            # NOT ok: without persisted index metadata the tombstone count is unknowable.
            unknown += 1
            print(f"{s['name'][:34]:34s} {'?':>7s} {'?':>7s} {'?':>6s} {'?':>7s}  "
                  "UNKNOWN (no persisted index metadata yet)")
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
    if unknown:
        print(f"\n{unknown} collection(s) have an UNKNOWN tombstone count (chromadb 1.x persists "
              "index metadata only at its sync threshold). Not reported healthy.")
        return 3
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

    # Guard 2: an export of THIS palace before any destructive step, always.
    #
    # The --db argument is load-bearing (review: Codex). Without it the exporter
    # defaults to ~/.mempalace, so `--palace /path/to/copy --execute X` would back
    # up the DEFAULT palace while deleting and rebuilding a DIFFERENT one — a
    # successful export would clear the safety guard for a target that has no
    # backup at all. A guard that passes while protecting the wrong thing is
    # worse than no guard.
    here = os.path.dirname(os.path.abspath(__file__))
    palace_db = os.path.join(palace, "chroma.sqlite3")
    print(f"taking a pre-compaction export of {palace_db} first...")
    rc = subprocess.run([sys.executable, os.path.join(here, "export_memory_store.py"),
                         "--db", palace_db, "--keep", "14"]).returncode
    if rc != 0:
        print("REFUSING: pre-compaction export failed; not touching the index.", file=sys.stderr)
        return 1

    print(f"\nabout to REBUILD {collection}: {s['live']} live rows re-embedded, "
          f"{s['tombstones']} tombstones dropped")
    if not assume_yes:
        if input(f"type the collection name to confirm: ").strip() != collection:
            print("aborted.")
            return 1

    # imported late: the read-only path must not need chromadb. The guard refuses a client
    # whose major version disagrees with the palace format (B0.8: one-way migration).
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
    from palace_client import open_palace_client
    client = open_palace_client(palace)
    col = client.get_collection(collection)
    data = col.get(include=["documents", "metadatas"])
    ids, docs, metas = data["ids"], data["documents"], data["metadatas"]

    # Spill the rows to a local file BEFORE deleting anything. The in-memory
    # copy is the primary restore source, but it dies with the process — and the
    # window being protected is exactly the one where the process may die.
    salvage = os.path.join(
        os.path.expanduser("~/.zoe"), f"compaction-salvage-{collection}.json")
    os.makedirs(os.path.dirname(salvage), mode=0o700, exist_ok=True)
    fd = os.open(salvage, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"collection": collection, "ids": ids,
                   "documents": docs, "metadatas": metas}, fh)
    print(f"salvage copy: {salvage} ({len(ids)} rows)")

    print(f"read {len(ids)} rows; deleting and re-adding...")
    client.delete_collection(collection)
    fresh = client.create_collection(collection)
    B = 256
    written = 0
    try:
        for i in range(0, len(ids), B):
            fresh.add(ids=ids[i:i+B], documents=docs[i:i+B], metadatas=metas[i:i+B])
            written = min(i + B, len(ids))
            print(f"  {written}/{len(ids)}")
    except Exception as exc:
        # A batch failed AFTER delete_collection: the collection is now empty or
        # partial and recall is silently degraded (review: Greptile). Retry the
        # remainder once from the in-memory rows; if that also fails, say
        # exactly how to restore rather than leaving the operator to discover
        # missing memories later.
        print(f"\n!! rebuild FAILED after {written}/{len(ids)} rows: {exc}", file=sys.stderr)
        print("!! attempting to re-add the remainder...", file=sys.stderr)
        try:
            for i in range(written, len(ids), B):
                fresh.add(ids=ids[i:i+B], documents=docs[i:i+B], metadatas=metas[i:i+B])
                written = min(i + B, len(ids))
            print(f"recovered: all {written} rows restored", file=sys.stderr)
        except Exception as exc2:
            print(f"!! recovery FAILED at {written}/{len(ids)}: {exc2}", file=sys.stderr)
            print(f"!! {collection} IS INCOMPLETE — recall is degraded until restored.",
                  file=sys.stderr)
            print(f"!! Restore from the salvage copy: {salvage}", file=sys.stderr)
            print(f"!! (or the pre-compaction export in ~/.zoe/memory-exports)",
                  file=sys.stderr)
            return 1
    os.unlink(salvage)  # only on full success — otherwise it is the restore path
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
