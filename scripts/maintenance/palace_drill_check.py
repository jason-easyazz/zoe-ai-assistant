#!/usr/bin/env python3
"""Open a RESTORED MemPalace copy with the real client and query it (the restore drill's palace check).

Run by ``zoe_restore_drill.py`` with the zoe-data py3.12 venv's interpreter (chromadb 1.5.x), on a palace the
drill extracted from the newest nightly tarball into a temp dir. Never pointed at the live ``~/.mempalace``:
it refuses a path that resolves to it.

Checks, printed as one JSON object on stdout:
* the format guard (``scripts/lib/palace_client.py``): the copy is chromadb 1.x format and this client agrees;
* every collection opens and the sum of ``count()`` equals the ``embeddings`` rows in the copy's SQLite (read
  before chromadb touches the file);
* one VECTOR query per non-empty collection: a stored vector is queried back and must return itself as the
  nearest neighbour - that is what loads the HNSW index from the segment files (the old weekly check counted
  SQLite rows and never opened the index).

Exit 0 all checks pass, 1 any failed (the JSON says which).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: palace_drill_check.py <restored palace dir>", file=sys.stderr)
        return 2
    palace = Path(argv[0]).resolve()
    out: dict = {"palace": str(palace), "ok": False, "problems": [], "collections": {}}
    live = Path(os.path.expanduser("~/.mempalace")).resolve()
    if palace == live:
        out["problems"].append("refused: that is the LIVE palace")
        print(json.dumps(out))
        return 1
    try:
        from palace_client import open_palace_client, palace_format  # noqa: PLC0415
        out["format"] = palace_format(str(palace))
        con = sqlite3.connect(f"file:{palace / 'chroma.sqlite3'}?mode=ro", uri=True)
        try:
            out["sqlite_embeddings"] = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        finally:
            con.close()
        client = open_palace_client(str(palace))
        import chromadb  # noqa: PLC0415
        out["chromadb"] = chromadb.__version__
        total = 0
        for c in client.list_collections():
            name = c if isinstance(c, str) else c.name
            col = client.get_collection(name)
            n = col.count()
            total += n
            info: dict = {"count": n}
            if n:
                got = col.get(limit=1, include=["embeddings"])
                rid, emb = got["ids"][0], got["embeddings"][0]
                q = col.query(query_embeddings=[list(emb)], n_results=1, include=["distances"])
                ids = q["ids"][0] if q["ids"] else []
                dist = q["distances"][0][0] if q.get("distances") and q["distances"][0] else None
                info.update({"query_top1_is_self": bool(ids) and ids[0] == rid, "distance": dist})
                if not (bool(ids) and (ids[0] == rid or (dist is not None and dist < 1e-4))):
                    out["problems"].append(f"{name}: a stored vector does not come back as its own nearest neighbour")
            out["collections"][name] = info
        if not out["collections"]:
            out["problems"].append("no collections in the restored palace")
        if total != out["sqlite_embeddings"]:
            out["problems"].append(f"collections count {total} != SQLite embeddings {out['sqlite_embeddings']}")
    except SystemExit as exc:                 # the format guard refuses with SystemExit
        out["problems"].append(f"format guard: {exc}")
    except Exception as exc:  # noqa: BLE001 - every failure is a drill finding
        out["problems"].append(f"{type(exc).__name__}: {str(exc)[:300]}")
    out["ok"] = not out["problems"]
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
