"""Two-store RAM probe (HM arm, Part A.3): what a SECOND Chroma store (the verbatim tier) costs next to the one
zoe-data already holds, in three shapes.

    shape A  same process, one shared ONNX MiniLM embedding function (the verbatim tier hosted INSIDE zoe-data)
    shape B  same process, a second embedding-function instance (a second ONNX session)
    shape C  a separate process (a sidecar): its whole RSS is the cost (printed by the single-store run of
             verbatim_pilot.py; this probe only reports the in-process shapes)

Run with the bake-off venv in the scrubbed env (mp_run.sh), 284 drawers in store 1 and 400 in store 2, written one at a time (batch 1). Prints JSON.
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

SCRATCH = Path("/home/zoe/.zoe/bakeoff-2026-10/mp-work")


def rss() -> int:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024
    return -1


def fill(col, turns, tag: str, b: int = 1) -> None:
    for i in range(0, len(turns), b):
        chunk = turns[i:i + b]
        col.upsert(ids=[f"{tag}{t['turn_id']}" for t in chunk], documents=[t["text"] for t in chunk],
                   metadatas=[{"wing": t["user"], "room": "voice"} for t in chunk])


def main() -> int:
    import chromadb
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    shape = sys.argv[1] if len(sys.argv) > 1 else "A"
    turns, _q, _m = household.generate()
    turns = [t for t in turns if t["user"] != household.GUEST]
    work = SCRATCH / f"ram-two-store-{shape}"
    shutil.rmtree(work, ignore_errors=True)
    out: dict = {"shape": shape, "rss_start": rss()}
    if shape == "P":
        # per-user palaces: one PersistentClient + collection per household member (4 here), ONE shared EF,
        # 100 drawers each. Compare rss_one_palace_400 (all 400 in one palace) with rss_four_palaces_100_each.
        ef = ONNXMiniLM_L6_V2()
        one = chromadb.PersistentClient(path=str(work / "one"))
        c = one.get_or_create_collection("mempalace_drawers", embedding_function=ef,
                                         configuration={"hnsw": {"space": "cosine", "num_threads": 1}})
        fill(c, turns[:400], "o")
        out["rss_one_palace_400"] = rss()
        clients = []
        for n, user in enumerate(("dana", "tove", "mika", "leo")):
            cl = chromadb.PersistentClient(path=str(work / f"user-{user}"))
            col = cl.get_or_create_collection("mempalace_drawers", embedding_function=ef,
                                              configuration={"hnsw": {"space": "cosine", "num_threads": 1}})
            fill(col, turns[400 + n * 100: 500 + n * 100], f"u{n}")
            clients.append((cl, col))
        out["rss_plus_four_palaces_100_each"] = rss()
        out["delta_four_palaces_mb"] = out["rss_plus_four_palaces_100_each"] - out["rss_one_palace_400"]
        out["threads"] = len(list(Path("/proc/self/task").iterdir()))
        shutil.rmtree(work, ignore_errors=True)
        print(json.dumps(out))
        return 0
    # store 1: stands in for zoe-data's own palace (its EF is the process's one ONNX session)
    ef1 = ONNXMiniLM_L6_V2()
    c1 = chromadb.PersistentClient(path=str(work / "palace-zoe-data"))
    col1 = c1.get_or_create_collection("mempalace_drawers", embedding_function=ef1,
                                       configuration={"hnsw": {"space": "cosine", "num_threads": 1}})
    fill(col1, turns[:284], "z")           # the live palace holds 284 drawers
    out["rss_store1_284_drawers"] = rss()
    # store 2: the verbatim tier
    ef2 = ef1 if shape == "A" else ONNXMiniLM_L6_V2()
    c2 = chromadb.PersistentClient(path=str(work / "palace-verbatim"))
    col2 = c2.get_or_create_collection("mempalace_drawers", embedding_function=ef2,
                                       configuration={"hnsw": {"space": "cosine", "num_threads": 1}})
    out["rss_store2_opened_empty"] = rss()
    t0 = time.perf_counter()
    fill(col2, turns[:400], "v")
    out["fill_400_s"] = round(time.perf_counter() - t0, 1)
    out["rss_store2_400_drawers"] = rss()
    col2.query(query_texts=["who is my dentist"], n_results=5)
    out["rss_after_query"] = rss()
    out["delta_store2_mb"] = out["rss_after_query"] - out["rss_store1_284_drawers"]
    out["threads"] = len(list(Path("/proc/self/task").iterdir()))
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
