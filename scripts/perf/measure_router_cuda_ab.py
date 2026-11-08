#!/usr/bin/env python3
"""A/B the FunctionGemma router sidecar with and without a CUDA context.

The live unit runs `--n-gpu-layers 0` but the build still initialises CUDA
(libcuda/cublas mapped, a nvgpu control page). This starts THROWAWAY copies of
the exact live command line on a spare localhost port, one per variant, probes
a fixed utterance set through the real grammar path (router_two_stage), and
reports RSS/PSS/VmLck, answers and latency per variant. It never touches the
live :11436 sidecar and only kills PIDs it started.

Variants: `cuda` (as live) and `nocuda` (CUDA_VISIBLE_DEVICES= ).
Gate: refuses to start a variant unless MemAvailable >= --min-avail-gb (2.5).

    python3 scripts/perf/measure_router_cuda_ab.py --out /tmp/router_ab.json
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

BIN = "/home/zoe/llama.cpp/build-jetson-new/bin/llama-server"
MODEL = "/home/zoe/models/functiongemma-router/functiongemma-270m-zoe-functok-r2-Q8_0.gguf"
WAIT_S = 0.0
PROBES = [
    "what time is it", "what's the weather like today", "add milk to the shopping list",
    "put eggs and bread on my shopping list", "show my shopping list", "remove milk from the list",
    "what's on my calendar tomorrow", "add dentist to my calendar on friday at 3pm",
    "remind me to call mum at 5pm", "what reminders do I have", "set a timer for ten minutes",
    "take a note that the wifi password is on the fridge", "search my notes for the plumber",
    "play some jazz", "turn off the lounge lights", "turn the heating up to 22",
    "do you remember what I said about my sister's birthday", "remember that I like oat milk",
    "tell me a joke", "how are you feeling today", "who is Sam", "write a journal entry about today",
    "set a five minute timer for the eggs", "what's the forecast for the weekend",
    "add call the bank to my todo list", "is the garage door closed", "pause the music",
    "what did I do yesterday", "remind me tomorrow to water the plants", "what's the time in Perth",
]


def avail_gb() -> float:
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) / 1048576
    return 0.0


def proc_mem(pid: int) -> dict:
    out: dict = {}
    for line in open(f"/proc/{pid}/status"):
        k = line.split(":")[0]
        if k in ("VmRSS", "VmHWM", "VmLck", "VmSwap"):
            out[k] = int(line.split()[1]) // 1024
    pss = 0
    for line in open(f"/proc/{pid}/smaps"):
        if line.startswith("Pss:"):
            pss += int(line.split()[1])
    out["PssMB"] = pss // 1024
    libs = set()
    for line in open(f"/proc/{pid}/maps"):
        parts = line.split()
        if parts and ".so" in parts[-1] and any(s in parts[-1] for s in ("cuda", "cublas", "libcu", "nvidia")):
            libs.add(os.path.basename(parts[-1]).split(".so")[0])
    out["cuda_libs_mapped"] = sorted(libs)
    return out


def run_variant(name: str, port: int, probes: list[str], gate_gb: float) -> dict:
    a = avail_gb()
    deadline = time.time() + WAIT_S
    while a < gate_gb and time.time() < deadline:
        time.sleep(5)
        a = avail_gb()
    if a < gate_gb:
        raise SystemExit(f"MemAvailable {a:.2f} GB < {gate_gb} GB gate; not starting {name}")
    env = dict(os.environ, LD_LIBRARY_PATH="/home/zoe/llama.cpp/build-jetson-new/bin")
    if name == "nocuda":
        env["CUDA_VISIBLE_DEVICES"] = ""
    cmd = [BIN, "--model", MODEL, "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "1024",
           "--cache-ram", "64", "--n-gpu-layers", "0", "--parallel", "1", "--threads", "4",
           "--mlock", "--jinja"]
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL,
                         stderr=open(f"/tmp/router_ab_{name}.log", "w"))
    try:
        t0 = time.time()
        while time.time() - t0 < 90:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read()
                break
            except Exception:
                if p.poll() is not None:
                    raise SystemExit(f"{name}: exited rc={p.returncode}; see /tmp/router_ab_{name}.log")
                time.sleep(0.5)
        load_s = time.time() - t0
        os.environ["ZOE_ROUTER_SIDECAR_URL"] = f"http://127.0.0.1:{port}"
        os.environ["ZOE_ROUTER_TWO_STAGE_TIMEOUT_S"] = "10"
        import router_two_stage as rt
        names = sorted({t for ts in rt.DOMAIN_TOOLS.values() for t in ts})
        grammar = rt.build_grammar(names)
        idle = proc_mem(p.pid)
        rt._post_sidecar("what time is it", grammar)  # warm
        answers, lat = [], []
        for rnd in range(3):
            for q in probes:
                t = time.perf_counter()
                raw = rt._post_sidecar(q, grammar)
                lat.append((time.perf_counter() - t) * 1000)
                if rnd == 0:
                    answers.append(raw)
        after = proc_mem(p.pid)
        lat.sort()
        return {"variant": name, "avail_gb_at_start": round(a, 2), "load_s": round(load_s, 1),
                "mem_idle": idle, "mem_after_probes": after, "answers": answers,
                "lat_ms": {"p50": round(statistics.median(lat)), "p95": round(lat[int(.95 * len(lat))]),
                           "n": len(lat)}}
    finally:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11446)
    ap.add_argument("--min-avail-gb", type=float, default=2.5)
    ap.add_argument("--out", default="/tmp/router_ab.json")
    ap.add_argument("--order", default="cuda,nocuda")
    ap.add_argument("--wait-s", type=float, default=0.0,
                    help="poll up to this long for MemAvailable to reach the gate before each variant")
    a = ap.parse_args()
    global WAIT_S
    WAIT_S = a.wait_s
    runs = [run_variant(v, a.port, PROBES, a.min_avail_gb) for v in a.order.split(",")]
    base = next(r for r in runs if r["variant"] == "cuda")["answers"]
    for r in runs:
        r["answers_identical_to_cuda"] = r["answers"] == base
    Path(a.out).write_text(json.dumps(runs, indent=1))
    for r in runs:
        m = r["mem_after_probes"]
        print(f'{r["variant"]:7} rss={m["VmRSS"]}MB pss={m["PssMB"]}MB hwm={m["VmHWM"]}MB '
              f'lck={m["VmLck"]}MB p50={r["lat_ms"]["p50"]}ms p95={r["lat_ms"]["p95"]}ms '
              f'same_answers={r["answers_identical_to_cuda"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
