"""Forget latency + rebuild fan-out at 1e3/1e4/1e5 sources, depth 1-4, and on-disk residue per strategy.

Scratch sqlite under a temp dir only. Run under the test lane:
  flock -x -w 3600 /tmp/zoe-test-lane.lock python3 labs/forget-first-memory/bench.py
"""
import json
import os
import random
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from lineage_store import WORDS, LineageStore, file_residue  # noqa: E402

VICTIMS = 30


def bulk_dag(s, rng, n, depth, fan_in=3):
    """Bulk build (bypasses the choke-point for speed; what is measured is forget, not ingest)."""
    s.db.execute("PRAGMA synchronous = OFF")
    s.db.execute("BEGIN")
    markers = {}
    rows = []
    for i in range(n):
        m = "qx%010x" % rng.getrandbits(40)
        markers[f"s{i}"] = m
        rows.append((f"s{i}", f"{rng.choice(WORDS)} {rng.choice(WORDS)} {m}"))
    s.db.executemany("INSERT INTO sources (rowid, id, payload) VALUES (?, ?, ?)", [(i + 1, *r) for i, r in enumerate(rows)])
    s.db.executemany("INSERT INTO sources_fts (rowid, id, payload) VALUES (?, ?, ?)", [(i + 1, *r) for i, r in enumerate(rows)])
    pool = [r[0] for r in rows]
    for d in range(1, depth + 1):
        arts, edges = [], []
        for j in range(n // 2):
            aid = f"a{d}_{j}"
            ps = set(rng.sample(pool, rng.randint(1, fan_in)))
            arts.append((aid, f"L{d}", d, f"L{d} summary {j}"))
            edges += [(aid, p) for p in ps]
        s.db.executemany("INSERT INTO artifacts (id, layer, depth, payload) VALUES (?, ?, ?, ?)", arts)
        s.db.executemany("INSERT INTO lineage VALUES (?, ?)", edges)
        pool += [a[0] for a in arts]
    s.db.execute("COMMIT")
    s.db.execute("PRAGMA synchronous = FULL")
    return markers


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def latency_grid(tmp):
    out = []
    for n in (1_000, 10_000, 100_000):
        for depth in (1, 2, 3, 4):
            path = os.path.join(tmp, f"g{n}_{depth}.db")
            rng = random.Random(n + depth)
            s = LineageStore(path, secure_delete=True, fts_compact=False)
            markers = bulk_dag(s, rng, n, depth)
            p1, p2, opt, fan = [], [], [], []
            for v in rng.sample(sorted(markers), VICTIMS):
                t0 = time.perf_counter(); closure = s.forget_phase1(v)
                t1 = time.perf_counter(); s.forget_phase2()
                t2 = time.perf_counter(); s.db.execute("INSERT INTO sources_fts (sources_fts) VALUES ('optimize')")
                t3 = time.perf_counter()
                p1.append((t1 - t0) * 1e3); p2.append((t2 - t1) * 1e3); opt.append((t3 - t2) * 1e3); fan.append(len(closure))
            t0 = time.perf_counter(); served = len(s.serve("L1")[:1000]) if n <= 10_000 else -1
            serve_ms = (time.perf_counter() - t0) * 1e3 if served >= 0 else None
            row = dict(sources=n, depth=depth, artifacts=n // 2 * depth, fanout_median=statistics.median(fan),
                       fanout_p95=pct(fan, .95), fanout_max=max(fan), phase1_ms_median=round(statistics.median(p1), 2),
                       phase1_ms_p95=round(pct(p1, .95), 2), phase2_ms_median=round(statistics.median(p2), 2),
                       fts_optimize_ms_median=round(statistics.median(opt), 1),
                       serve_L1_all_ms=None if serve_ms is None else round(serve_ms, 1), db_mb=round(os.path.getsize(path) / 1e6, 1))
            s.close(); os.remove(path)
            print(json.dumps(row), flush=True); out.append(row)
    return out


def choke_point_cost(tmp):
    """write_derived (lineage + parent checks + ledger check, one txn each) vs a bare insert, 5000 writes."""
    res = {}
    for mode in ("bare_insert", "write_derived"):
        s = LineageStore(os.path.join(tmp, f"w_{mode}.db"))
        s.db.execute("PRAGMA synchronous = OFF")
        for i in range(1000):
            s.ingest(f"s{i}", f"{WORDS[i % 10]} src {i}")
        rng = random.Random(1)
        t0 = time.perf_counter()
        for j in range(5000):
            ps = rng.sample(range(1000), 3)
            if mode == "bare_insert":
                s.db.execute("BEGIN IMMEDIATE")
                s.db.execute("INSERT INTO artifacts (id, layer, depth, payload) VALUES (?, 'L1', 1, ?)", (f"a{j}", f"sum {j}"))
                s.db.execute("COMMIT")
            else:
                s.write_derived(f"a{j}", "L1", f"sum {j}", [f"s{p}" for p in ps])
        res[mode + "_us_per_write"] = round((time.perf_counter() - t0) / 5000 * 1e6, 1)
        s.close()
    print(json.dumps(res), flush=True)
    return res


def residue_strategies(tmp):
    out = []
    for name, secure, compact, vacuum in (("plain_delete", False, False, False), ("secure_delete_only", True, False, False),
                                          ("fts_optimize_only", False, True, False), ("secure_delete+fts_optimize", True, True, False),
                                          ("plain+fts_optimize+VACUUM", False, True, True)):
        path = os.path.join(tmp, f"r_{name}.db")
        rng = random.Random(42)
        s = LineageStore(path, secure_delete=secure, fts_compact=False)
        markers = bulk_dag(s, rng, 10_000, 2)
        s.close()
        victims = random.Random(7).sample(sorted(markers), 10)
        before = sum(file_residue(path, markers[v]) for v in victims)
        s = LineageStore(path, secure_delete=secure, fts_compact=compact)
        t0 = time.perf_counter()
        for v in victims:
            s.forget(v)
        if vacuum:
            s.db.execute("VACUUM")
        ms = (time.perf_counter() - t0) * 1e3 / len(victims)
        in_table = sum(bool(s.residue(markers[v])) for v in victims)
        s.close()
        after = sum(file_residue(path, markers[v]) for v in victims)
        row = dict(strategy=name, ms_per_forget=round(ms, 1), in_table_residue=f"{in_table}/10",
                   file_residue_before=f"{before}/10", file_residue_after=f"{after}/10")
        print(json.dumps(row), flush=True); out.append(row); os.remove(path)
    return out


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="forget-first-") as tmp:
        t0 = time.time()
        res = dict(residue=residue_strategies(tmp), choke=choke_point_cost(tmp), grid=latency_grid(tmp))
        res["wall_s"] = round(time.time() - t0, 1)
        print(json.dumps({"wall_s": res["wall_s"]}))
        with open(os.path.join(os.path.dirname(__file__), "bench_results.json"), "w") as fh:
            json.dump(res, fh, indent=1)
