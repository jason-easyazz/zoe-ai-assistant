#!/usr/bin/env python3
"""A/B zoe-data's ORT embedders with and without the CPU memory arena.

Each variant runs in a FRESH child process (the zoe-data venv python), so RSS is
not contaminated by the other. The child builds the same two sessions zoe-data
builds - fastembed bge-small (semantic router / persona drift) and Chroma's
MiniLM (the recall palace EF, via memory_service) - using the real code paths
under ZOE_ORT_CPU_ARENA=1 (arena on, the pre-change behaviour) or 0 (arena off, the shipped default), embeds a fixed mixed-length text set, and
prints one JSON line: RSS after load / after embedding, VmHWM, latency p50/p95
and a vector fingerprint so equal outputs can be proven.

Throwaway: touches no live service and no live data. Gate: waits for
MemAvailable >= --min-avail-gb (2.5) before each child.

    python3 scripts/perf/measure_ort_arena_ab.py --wait-s 600 --out /tmp/ort_ab.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV_PY = os.path.expanduser("~/.zoe/venvs/zoe-data-py312/bin/python")

CHILD = r"""
import json, os, sys, time, statistics, hashlib
sys.path.insert(0, os.path.join(sys.argv[1], "services", "zoe-data"))
def rss():
    d = {}
    for l in open("/proc/self/status"):
        if l.split(":")[0] in ("VmRSS", "VmHWM", "VmSwap"):
            d[l.split(":")[0]] = int(l.split()[1]) // 1024
    return d
short = ["what time is it", "add milk to the shopping list", "play some jazz", "remember I like oat milk"]
longer = [("my sister's birthday is next month and I want to plan something nice " * k).strip() for k in (2, 6, 14, 30)]
texts = short * 6 + longer * 6
import ort_arena
out = {"arena_enabled": ort_arena.arena_enabled(), "rss_start": rss()}
from fastembed import TextEmbedding
t = time.time()
fe = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", **ort_arena.fastembed_kwargs())
list(fe.embed(["warm"]))
import memory_service
ef = memory_service._drawers_embedding_function()
ef(["warm"])
out["load_s"] = round(time.time() - t, 2)
out["rss_loaded"] = rss()
lat_fe, lat_ef, cpu_fe, cpu_ef = [], [], 0.0, 0.0
for rnd in range(4):
    for x in texts:
        c = time.process_time(); t = time.perf_counter(); v1 = list(fe.embed([x])); lat_fe.append((time.perf_counter() - t) * 1000); cpu_fe += time.process_time() - c
        c = time.process_time(); t = time.perf_counter(); v2 = ef([x]); lat_ef.append((time.perf_counter() - t) * 1000); cpu_ef += time.process_time() - c
out["cpu_ms_per_embed"] = {"fastembed": round(cpu_fe / len(lat_fe) * 1000, 1), "chroma": round(cpu_ef / len(lat_ef) * 1000, 1)}
out["loadavg1"] = os.getloadavg()[0]
fp = hashlib.sha1()
for x in short + longer:
    fp.update(("%.4f" % float(list(fe.embed([x]))[0][:8].sum())).encode())
    fp.update(("%.4f" % float(sum(ef([x])[0][:8]))).encode())
out["rss_after"] = rss()
lat_fe.sort(); lat_ef.sort()
out["fastembed_ms"] = {"p50": round(statistics.median(lat_fe), 1), "p95": round(lat_fe[int(.95 * len(lat_fe))], 1)}
out["chroma_ms"] = {"p50": round(statistics.median(lat_ef), 1), "p95": round(lat_ef[int(.95 * len(lat_ef))], 1)}
out["fingerprint"] = fp.hexdigest()[:12]
print("RESULT " + json.dumps(out))
"""


def avail_gb() -> float:
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) / 1048576
    return 0.0


def run(mode: str, gate: float, wait_s: float) -> dict:
    deadline = time.time() + wait_s
    while avail_gb() < gate and time.time() < deadline:
        time.sleep(5)
    if avail_gb() < gate:
        raise SystemExit(f"MemAvailable {avail_gb():.2f} GB < {gate} GB gate; not starting")
    env = dict(os.environ, ZOE_ORT_CPU_ARENA=mode, MALLOC_ARENA_MAX="2",
               MALLOC_TRIM_THRESHOLD_="131072")  # match zoe-data's 40-memory-tuning drop-in
    r = subprocess.run([VENV_PY, "-I", "-c", CHILD, str(ROOT)], env=env, capture_output=True, text=True, timeout=600)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise SystemExit(f"child failed rc={r.returncode}\n{r.stderr[-1500:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-avail-gb", type=float, default=2.5)
    ap.add_argument("--wait-s", type=float, default=0.0)
    ap.add_argument("--order", default="1,0,1,0", help="ZOE_ORT_CPU_ARENA value per run (1 = arena on, 0 = off)")
    ap.add_argument("--out", default="/tmp/ort_ab.json")
    a = ap.parse_args()
    runs = []
    for v in a.order.split(","):
        res = run(v, a.min_avail_gb, a.wait_s)
        res["variant"] = "arena_on" if v == "1" else "arena_off"
        runs.append(res)
        print(f'{res["variant"]:8} loaded={res["rss_loaded"]["VmRSS"]}MB after={res["rss_after"]["VmRSS"]}MB '
              f'swap={res["rss_after"].get("VmSwap")}MB hwm={res["rss_after"]["VmHWM"]}MB load={res["loadavg1"]:.1f} '
              f'cpu={res["cpu_ms_per_embed"]} fe_p50={res["fastembed_ms"]["p50"]}ms '
              f'chroma_p50={res["chroma_ms"]["p50"]}ms fp={res["fingerprint"]}', flush=True)
    Path(a.out).write_text(json.dumps(runs, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
