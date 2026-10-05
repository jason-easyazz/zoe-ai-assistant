"""The verbatim-tier pilot (HM arm, Part A.2): ingest the synthetic household transcript into MemPalace used as a
LIBRARY and into a plain Chroma 1.5.9 collection with the same embedder, then measure ingest, size, RSS, exact-
reference hit@k, query latency and MemPalace's dedup behaviour.

Run with the BAKE-OFF venv python in the scrubbed env (never the live venv, never ``~/.mempalace``):

    bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/pilot/verbatim_pilot.py \
        --backend mempalace --ingest per_turn --out /home/zoe/.zoe/bakeoff-2026-10/mp-work/pilot-mempalace.json

``--backend``: ``mempalace`` (get_collection + upsert; queries through ``search_memories`` = the hybrid BM25 + vector
re-rank, AND through the raw collection = vector only) | ``chroma`` (PersistentClient + the same ONNX MiniLM EF).
``--ingest``: ``per_turn`` (one upsert per turn: the write-behind path, whose per-write latency is what a voice
turn would pay if it were synchronous) | ``batch`` (``--batch-size`` turns per upsert, default 8).

Everything is under ``--workdir`` (default a fresh dir below the bake-off scratch root). Prints one JSON document.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))        # scripts/perf
from zmb.pilot import household  # noqa: E402

SCRATCH = Path("/home/zoe/.zoe/bakeoff-2026-10/mp-work")


def rss_mb() -> "dict[str, int]":
    d: dict[str, int] = {}
    for line in open("/proc/self/status"):
        if line.startswith(("VmRSS", "VmHWM")):
            d[line.split(":")[0]] = int(line.split()[1]) // 1024
    return d


def wilson(k: int, n: int, z: float = 1.96) -> "list[float]":
    """Wilson score interval for k successes in n trials (same formula as zmb.scorers)."""
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - h) / d, 3), round((c + h) / d, 3)]


def pct(xs: "list[float]", q: float) -> float:
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(math.ceil(q * len(s))) - 1)], 1) if s else 0.0


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def meta_for(t: dict) -> dict:
    return {"wing": t["user"], "room": "voice", "source_file": f"hm:{t['user']}:day{t['day']}",
            "added_by": "pilot", "filed_at": f"2026-10-0{t['day']}T09:00:00", "turn_id": t["turn_id"],
            "speaker": t["speaker"], "speaker_verified": bool(t["verified"]), "kind": t["kind"]}


class MemPalaceStore:
    name = "mempalace"

    def __init__(self, path: Path):
        from mempalace.palace import get_collection
        from mempalace.searcher import search_memories
        self.path = str(path)
        path.mkdir(parents=True, exist_ok=True)
        self.col = get_collection(self.path, create=True)
        self._search = search_memories

    def upsert(self, turns: "list[dict]") -> None:
        self.col.upsert(ids=[t["turn_id"] for t in turns], documents=[t["text"] for t in turns],
                        metadatas=[meta_for(t) for t in turns])

    def hybrid(self, q: str, wing: "str | None", k: int) -> "list[tuple[str, str]]":
        r = self._search(q, self.path, wing=wing, n_results=k)
        return [(h["drawer_id"], h.get("wing", "")) for h in r.get("results", [])]

    def vector(self, q: str, wing: "str | None", k: int) -> "list[tuple[str, str]]":
        kw = {"where": {"wing": wing}} if wing else {}
        r = self.col.query(query_texts=[q], n_results=k, include=["metadatas"], **kw)
        return [(i, (m or {}).get("wing", "")) for i, m in zip(r["ids"][0], r["metadatas"][0])]


class PlainChromaStore:
    name = "chroma"

    def __init__(self, path: Path):
        import chromadb
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path))
        self.col = self.client.get_or_create_collection(
            "verbatim", embedding_function=ONNXMiniLM_L6_V2(),
            configuration={"hnsw": {"space": "cosine", "num_threads": 1}})

    def upsert(self, turns: "list[dict]") -> None:
        self.col.upsert(ids=[t["turn_id"] for t in turns], documents=[t["text"] for t in turns],
                        metadatas=[meta_for(t) for t in turns])

    def vector(self, q: str, wing: "str | None", k: int) -> "list[tuple[str, str]]":
        kw = {"where": {"wing": wing}} if wing else {}
        r = self.col.query(query_texts=[q], n_results=k, include=["metadatas"], **kw)
        return [(i, (m or {}).get("wing", "")) for i, m in zip(r["ids"][0], r["metadatas"][0])]

    hybrid = vector


def measure_queries(fn, queries: "list[dict]", scoped: bool, k: int, passes: int) -> dict:
    lat: list[float] = []
    ranks: list[int | None] = []
    leaks = 0
    for ps in range(passes):
        for q in queries:
            wing = q["user"] if scoped else None
            t0 = time.perf_counter()
            hits = fn(q["text"], wing, k)
            dt = (time.perf_counter() - t0) * 1000
            if ps > 0:                       # pass 0 is the warm-up (ONNX session, caches); not in the latency stats
                lat.append(dt)
            if ps == passes - 1:
                ids = [h[0] for h in hits]
                rank = next((i + 1 for i, x in enumerate(ids) if x in q["gold"]), None)
                ranks.append(rank)
                if scoped:
                    leaks += sum(1 for h in hits if h[1] and h[1] != q["user"])
    n = len(queries)
    hit1 = sum(1 for r in ranks if r == 1)
    hit5 = sum(1 for r in ranks if r is not None and r <= 5)
    hitk = sum(1 for r in ranks if r is not None)
    by_kind: dict[str, dict] = {}
    for q, r in zip(queries, ranks):
        b = by_kind.setdefault(q["kind"], {"n": 0, "hit5": 0})
        b["n"] += 1
        b["hit5"] += 1 if (r is not None and r <= 5) else 0
    return {"n": n, "hit@1": hit1, "hit@1_wilson95": wilson(hit1, n), "hit@5": hit5, "hit@5_wilson95": wilson(hit5, n),
            f"hit@{k}": hitk, "mean_rank_when_hit": round(statistics.mean([r for r in ranks if r]), 2),
            "latency_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": round(max(lat), 1), "n": len(lat)},
            "cross_user_leaks": leaks, "by_kind_hit@5": by_kind,
            "missed": [q["qid"] for q, r in zip(queries, ranks) if r is None or r > 5]}


def near_dup_report(store, turns: "list[dict]", meta: dict) -> dict:
    """MemPalace's own dedup, applied to the transcript's near-duplicate reminder clusters, in DRY-RUN."""
    from mempalace import dedup
    by_text = {t["text"]: t for t in turns}
    dists: list[float] = []
    pairs_below_015 = 0
    pairs = 0
    for variants in meta["near_dup_clusters"]:
        for i, v in enumerate(variants):
            others = [x for j, x in enumerate(variants) if j != i]
            tid = by_text[v]["turn_id"]
            r = store.col.query(query_texts=[v], n_results=6, include=["distances"],
                                where={"wing": by_text[v]["user"]})
            for oid, dist in zip(r["ids"][0], r["distances"][0]):
                if oid != tid and any(by_text[o]["turn_id"] == oid for o in others):
                    dists.append(dist)
                    pairs += 1
                    pairs_below_015 += 1 if dist < dedup.DEFAULT_THRESHOLD else 0
    groups = dedup.get_source_groups(store.col, min_count=5, palace_path=store.path)
    would_delete_015 = 0
    short_deleted = 0
    near_dup_texts = {v for vs in meta["near_dup_clusters"] for v in vs}
    deleted_near_dup = 0
    deleted_other = 0
    id2text = {t["turn_id"]: t["text"] for t in turns}
    for src, ids in groups.items():
        _kept, deleted = dedup.dedup_source_group(store.col, ids, dedup.DEFAULT_THRESHOLD, dry_run=True)
        would_delete_015 += len(deleted)
        for d in deleted:
            txt = id2text.get(d, "")
            short_deleted += 1 if len(txt) < 20 else 0
            if txt in near_dup_texts:
                deleted_near_dup += 1
            else:
                deleted_other += 1
    # an exact re-add of the same text: MemPalace ids drawers by content in tool_add_drawer; the library path (upsert with
    # our own id) replaces by id. Count what a replay of one turn does.
    before = store.col.count()
    sample = turns[7]
    store.upsert([sample])
    after = store.col.count()
    return {"near_dup_pairs_measured": pairs, "cosine_distance_p50": round(statistics.median(dists), 3) if dists else None,
            "cosine_distance_min": round(min(dists), 3) if dists else None,
            "cosine_distance_max": round(max(dists), 3) if dists else None,
            "pairs_below_default_threshold_0.15": pairs_below_015,
            "dedup_default_threshold": dedup.DEFAULT_THRESHOLD, "source_groups_ge5": len(groups),
            "dry_run_would_delete_total": would_delete_015, "dry_run_would_delete_near_dup_reminders": deleted_near_dup,
            "dry_run_would_delete_other_turns": deleted_other, "dry_run_would_delete_shorter_than_20_chars": short_deleted,
            "total_turns": len(turns), "replay_same_id_count_before_after": [before, after]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=("mempalace", "chroma"), required=True)
    ap.add_argument("--ingest", choices=("per_turn", "batch"), default="per_turn")
    ap.add_argument("--batch-size", type=int, default=8,
                    help="turns per upsert in --ingest batch (RAM grows with it: 8 -> ~375 MB, 32+ -> ~760 MB)")
    ap.add_argument("--n-turns", type=int, default=1000)
    ap.add_argument("--seed", default="hm-pilot-v1")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--workdir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--keep", action="store_true", help="keep the palace dir (default: delete after)")
    a = ap.parse_args()
    turns, queries, meta = household.generate(a.seed, a.n_turns)
    # store only what the verbatim tier would store: verified household speakers (no guest, no unverified fragment)
    storable = [t for t in turns if t["user"] != household.GUEST]
    work = a.workdir or (SCRATCH / f"pilot-{a.backend}-{a.ingest}-{os.getpid()}")
    if work.exists():
        shutil.rmtree(work)
    out: dict = {"backend": a.backend, "ingest": a.ingest, "seed": a.seed, "turns_total": len(turns),
                 "turns_stored": len(storable), "queries": len(queries), "rss_start": rss_mb()}
    t0 = time.perf_counter()
    store = (MemPalaceStore if a.backend == "mempalace" else PlainChromaStore)(work / "palace")
    out["open_s"] = round(time.perf_counter() - t0, 2)
    out["rss_after_open"] = rss_mb()
    # a first write loads the ONNX session: time it apart so it never pollutes the per-write latency
    t0 = time.perf_counter()
    store.upsert(storable[:1])
    out["first_write_incl_onnx_load_s"] = round(time.perf_counter() - t0, 2)
    out["rss_after_first_write"] = rss_mb()
    lat: list[float] = []
    t_all = time.perf_counter()
    if a.ingest == "per_turn":
        for t in storable[1:]:
            t1 = time.perf_counter()
            store.upsert([t])
            lat.append((time.perf_counter() - t1) * 1000)
    else:
        rest = storable[1:]
        for i in range(0, len(rest), a.batch_size):
            t1 = time.perf_counter()
            store.upsert(rest[i:i + a.batch_size])
            lat.append((time.perf_counter() - t1) * 1000)
    out["ingest_total_s"] = round(time.perf_counter() - t_all, 2)
    out["write_latency_ms"] = {"unit": "per upsert call (%s)" % a.ingest, "p50": pct(lat, 0.5), "p95": pct(lat, 0.95),
                               "max": round(max(lat), 1), "n": len(lat)}
    out["count"] = store.col.count()
    out["rss_after_ingest"] = rss_mb()
    out["disk_bytes"] = dir_size(work)
    out["disk_mb"] = round(out["disk_bytes"] / 1e6, 2)
    out["queries_scoped_vector"] = measure_queries(store.vector, queries, True, a.k, a.passes)
    out["queries_global_vector"] = measure_queries(store.vector, queries, False, a.k, a.passes)
    if a.backend == "mempalace":
        out["queries_scoped_hybrid"] = measure_queries(store.hybrid, queries, True, a.k, a.passes)
        out["queries_global_hybrid"] = measure_queries(store.hybrid, queries, False, a.k, a.passes)
        out["dedup"] = near_dup_report(store, storable, meta)
    out["rss_end"] = rss_mb()
    out["threads"] = len(os.listdir("/proc/self/task"))
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    text = json.dumps(out, indent=1)
    if a.out:
        a.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
