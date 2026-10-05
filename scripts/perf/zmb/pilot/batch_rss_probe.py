"""RSS and time of embedding + upserting 200 synthetic turns with batch size B through ONE Chroma collection.

Why: the two-store probe found that a single ``upsert`` of 100 short documents took the process from about 110 MB
to about 780 MB RSS (the ONNX MiniLM session's CPU arena grows with the batch), against about 290 MB for the same
1,000 documents written one at a time. The batch size of a backfill / write-behind flush is therefore a RAM knob.

    bash mp_run.sh batch_rss_probe.py <B>      (B = 1, 8, 16, 32 ...; do NOT run B >= 64 with < 1.5 GB free)
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from zmb.pilot import household  # noqa: E402


def rss() -> "tuple[int, int]":
    cur = hwm = -1
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            cur = int(line.split()[1]) // 1024
        if line.startswith("VmHWM"):
            hwm = int(line.split()[1]) // 1024
    return cur, hwm


def main() -> int:
    import chromadb
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    b = int(sys.argv[1])
    if b > 48:
        print("refusing: batch > 48 can take the process past 700 MB", file=sys.stderr)
        return 2
    turns, _q, _m = household.generate()
    turns = [t for t in turns if t["user"] != household.GUEST][:200]
    work = Path("/home/zoe/.zoe/bakeoff-2026-10/mp-work") / f"batch-rss-{b}"
    shutil.rmtree(work, ignore_errors=True)
    out: dict = {"batch": b, "rss_start": rss()[0]}
    client = chromadb.PersistentClient(path=str(work))
    col = client.get_or_create_collection("batchprobe",embedding_function=ONNXMiniLM_L6_V2(),
                                          configuration={"hnsw": {"space": "cosine", "num_threads": 1}})
    t0 = time.perf_counter()
    for i in range(0, len(turns), b):
        chunk = turns[i:i + b]
        col.upsert(ids=[t["turn_id"] for t in chunk], documents=[t["text"] for t in chunk],
                   metadatas=[{"wing": t["user"]} for t in chunk])
    out["seconds_200_docs"] = round(time.perf_counter() - t0, 1)
    out["rss_end"], out["rss_hwm"] = rss()
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
