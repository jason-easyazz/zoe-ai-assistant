#!/usr/bin/env python3
"""Report, and on request compact, the MemPalace drawers HNSW index.

Why (measured 2026-10-04): chroma 1.x never compacts a persistent HNSW index. Every
deleted row (demo-user teardown after each bar / day-sim run, consolidation churn) stays
in the graph as a tombstone. The live drawers index held 1,591 elements for 258 live
rows (84 % tombstones); for "When did I tell you about the dentist?" the owner-filtered
query returned 0 rows and the unfiltered one 18, so the recall packet carried no semantic
hits and the brain said it had nothing stored. The service now falls back to an
unfiltered over-fetch (``memory_service._semantic_search``, log line
``MEMORY_SEARCH_FALLBACK``); this tool removes the cause.

Modes
  report   (default, READ-ONLY, safe while zoe-data runs) — per collection: live rows,
           elements ever added, tombstone ratio, and whether compaction is advised.
  --compact  recreate ``mempalace_drawers`` from its own rows using the STORED
           embeddings (no re-embed, bit-identical vectors). Operator-run, with
           zoe-data STOPPED (``--i-stopped-zoe-data`` acknowledges that); writes a tar
           backup of the palace and a JSON export first, verifies the new index
           (count, unfiltered reach == count, an owner-filtered query, a metadata
           update), and exits non-zero without touching anything further on any check.

Operator recipe (compaction):
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/compact_drawers_index.py --compact --i-stopped-zoe-data
  systemctl --user start zoe-data && until curl -sf http://127.0.0.1:8000/readyz; do sleep 5; done
Rollback: ``tar -xf <backup tar printed above> -C ~`` with zoe-data stopped.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

DRAWERS = "mempalace_drawers"
ADVISE_RATIO = 3.0   # elements ever added / live rows; above this the graph is mostly tombstones


def tombstone_ratio(total_added: int, live: int) -> float:
    """elements ever added ÷ live rows (∞ when nothing is live but elements were added)."""
    if live <= 0:
        return float("inf") if total_added > 0 else 0.0
    return total_added / live


def compaction_advised(total_added: int, live: int, *, threshold: float = ADVISE_RATIO) -> bool:
    return live > 0 and tombstone_ratio(total_added, live) >= threshold


def report(palace: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{palace / 'chroma.sqlite3'}?mode=ro", uri=True)
    out = []
    for cid, name in con.execute("SELECT id, name FROM collections"):
        live = con.execute(
            "SELECT count(*) FROM embeddings e JOIN segments s ON s.id = e.segment_id WHERE s.collection = ?",
            (cid,)).fetchone()[0]
        seg = con.execute("SELECT id FROM segments WHERE collection = ? AND scope = 'VECTOR'", (cid,)).fetchone()
        total = None
        if seg and (palace / seg[0] / "index_metadata.pickle").exists():
            try:
                meta = pickle.load(open(palace / seg[0] / "index_metadata.pickle", "rb"))
                total = int(getattr(meta, "total_elements_added", None) or (meta.get("total_elements_added") if isinstance(meta, dict) else 0) or 0)
            except Exception as exc:  # noqa: BLE001 — a report must never crash on a pickle
                total = None
                print(f"  ({name}: index metadata unreadable: {type(exc).__name__})", file=sys.stderr)
        fresh = bool(seg) and not (palace / seg[0] / "index_metadata.pickle").exists()
        row = {"collection": name, "live_rows": live, "elements_added": total,
               "tombstone_ratio": (round(tombstone_ratio(total, live), 2) if total is not None else None),
               "compaction_advised": (compaction_advised(total, live) if total is not None else None),
               # chroma persists a new segment's index files lazily (sync threshold): right after
               # a rebuild the directory does not exist yet, which means ratio ≈ 1, not unknown.
               "note": ("fresh index — not persisted yet, ratio ≈ 1" if fresh else "")}
        out.append(row)
    return out


def compact(palace: Path, backups: Path) -> int:
    import chromadb
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))
    from memory_service import _drawers_embedding_function  # same EF identity ("default")

    backups.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    tar_path = backups / f"mempalace-pre-compact-{ts}.tar"
    with tarfile.open(tar_path, "w") as tf:
        tf.add(palace, arcname=palace.name)
    print(f"backup: {tar_path} ({tar_path.stat().st_size // 1024} KiB)")

    client = chromadb.PersistentClient(path=str(palace))
    ef = _drawers_embedding_function()
    col = client.get_collection(DRAWERS, embedding_function=ef)
    space = (col.metadata or {}).get("hnsw:space") or "l2"
    try:
        cfg = col._model.configuration_json
        cfg = json.loads(cfg) if isinstance(cfg, str) else dict(cfg or {})
        space = ((cfg.get("hnsw") or {}).get("space")) or space
    except Exception:  # noqa: BLE001 — the metadata key / l2 default stands
        pass
    rows = col.get(include=["embeddings", "documents", "metadatas"])
    ids, embs, docs, metas = rows["ids"], rows["embeddings"], rows["documents"], rows["metadatas"]
    n = len(ids)
    if n == 0 or n != col.count() or any(e is None or len(e) == 0 for e in embs):
        print("export incomplete — aborting before any change", file=sys.stderr)
        return 2
    export = backups / f"mempalace-drawers-export-{ts}.json"
    json.dump({"space": space, "ids": ids, "documents": docs, "metadatas": metas,
               "embeddings": [[float(x) for x in e] for e in embs]}, open(export, "w"))
    print(f"export: {n} rows, space={space} → {export}")

    client.delete_collection(DRAWERS)
    new = client.create_collection(DRAWERS, metadata={"hnsw:space": space}, embedding_function=ef)
    for i in range(0, n, 100):
        new.add(ids=ids[i:i + 100], embeddings=embs[i:i + 100], documents=docs[i:i + 100], metadatas=metas[i:i + 100])
    probe = "When did I tell you about the dentist?"
    reach = len(new.query(query_texts=[probe], n_results=n, include=[])["ids"][0])
    owner = next((m.get("user_id") for m in metas if m and m.get("user_id") and not str(m.get("user_id")).startswith("demo_")), None)
    filtered = len(new.query(query_texts=[probe], n_results=6, where={"user_id": owner}, include=[])["ids"][0]) if owner else -1
    new.update(ids=[ids[0]], metadatas=[dict(metas[0])])  # the historical crash path
    print(f"verify: count={new.count()}/{n} unfiltered_reach={reach}/{n} filtered(owner)={filtered} metadata_update=ok")
    if new.count() != n or reach != n or filtered == 0:
        print("VERIFY FAILED — restore the backup tar before starting zoe-data", file=sys.stderr)
        return 3
    print("COMPACT OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--palace", default=os.path.expanduser("~/.mempalace"))
    ap.add_argument("--backups", default=os.path.expanduser("~/.zoe/backups"))
    ap.add_argument("--compact", action="store_true", help="recreate the drawers collection (operator, zoe-data stopped)")
    ap.add_argument("--i-stopped-zoe-data", action="store_true", help="acknowledge zoe-data is stopped (required with --compact)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    palace = Path(args.palace)
    if not args.compact:
        rows = report(palace)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"{r['collection']:>20}: live={r['live_rows']:>6} added={r['elements_added']!s:>6} "
                      f"ratio={r['tombstone_ratio']!s:>6} compaction_advised={r['compaction_advised']}"
                      + (f"  ({r['note']})" if r.get("note") else ""))
        return 0
    if not args.i_stopped_zoe_data:
        print("--compact needs --i-stopped-zoe-data (stop zoe-data first: the service holds the index)", file=sys.stderr)
        return 2
    return compact(palace, Path(args.backups))


if __name__ == "__main__":
    raise SystemExit(main())
