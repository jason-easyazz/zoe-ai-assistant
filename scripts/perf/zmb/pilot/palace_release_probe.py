"""Does closing a MemPalace verbatim store release its Chroma client? (RAM lab, 2026-10-06.)

The HM driver opens one palace per cell (21 cells + the generic box). ``MemPalaceLibraryStore.close()`` dropped its collection but left the backend's cached ``PersistentClient``
(and Chroma's process-wide system cache) alive for every palace ever opened in the process. This opens ``--palaces`` scratch palaces in turn (25 pre-embedded rows each: no embedder, no
network), closes each by the strategy named, and prints the PSS after each: ``none`` (what close() did), ``own`` (release only this palace's client) and ``all`` (drain every client and
clear Chroma's cache: what erase_physical does).

    bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/pilot/palace_release_probe.py none|own|all [--palaces 24]
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import tempfile
from pathlib import Path


def pss_mb() -> float:
    for line in Path("/proc/self/smaps_rollup").read_text().splitlines():
        if line.startswith("Pss:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("strategy", choices=("none", "own", "all"))
    ap.add_argument("--palaces", type=int, default=24)
    a = ap.parse_args()
    import mempalace.backends.chroma as mc
    import mempalace.palace as mp
    from mempalace.palace import get_collection
    base = Path(tempfile.mkdtemp(prefix="zmb-release-"))
    out = {"strategy": a.strategy, "start_mb": round(pss_mb(), 1), "after_mb": []}
    vec = [0.01 * (i % 7) for i in range(384)]
    try:
        for i in range(a.palaces):
            path = str(base / f"p{i}")
            col = get_collection(path, create=True)
            ids = [f"r{j}" for j in range(25)]
            col.upsert(ids=ids, documents=[f"row {j} of palace {i}" for j in range(25)], metadatas=[{"wing": "w", "room": "chat"}] * 25, embeddings=[vec] * 25)
            col = None
            backend = mp._DEFAULT_BACKEND
            if a.strategy == "own":
                mc._close_client(backend._clients.pop(path, None))
                backend._freshness.pop(path, None)
            elif a.strategy == "all":
                backend._drain_clients()
                import chromadb
                chromadb.api.client.SharedSystemClient.clear_system_cache()
            gc.collect()
            out["after_mb"].append(round(pss_mb(), 1))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    out["growth_mb"] = round(out["after_mb"][-1] - out["after_mb"][0], 1)
    out["per_palace_mb"] = round(out["growth_mb"] / max(1, a.palaces - 1), 2)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
