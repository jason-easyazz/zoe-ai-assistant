#!/usr/bin/env python3
"""A/B bench for the reduced-vocabulary Gemma 4 MTP draft head.

Starts one llama-server per arm on a SIDE port (default 11435) with the live brain's
flags, runs Zoe-shaped chat prompts (greedy and temp 0.7), records decode tok/s,
draft acceptance and the reply text, then kills the server.  Run it ONLY inside a
brain-stop window under `flock /tmp/zoe-voice-harness.lock` (two E4B loads do not fit
next to the live brain); see docs/knowledge/mtp-draft-vocab-trim-2026-10-10.md for the
exact wrapper.  It never touches the live unit.

  arm spec:  NAME=SERVER_BIN|LD_LIBRARY_PATH|DRAFT_GGUF

Output: JSON with per-prompt rows and per-arm medians, plus a greedy byte-identity
table (every arm vs the first arm).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SYSTEM = (
    "You are Zoe, a warm, concise household voice assistant. Keep replies short and "
    "natural, in plain spoken sentences."
)
PROMPTS = [
    "Can you remind me what a good temperature is to roast a chicken, and for how long?",
    "I'm feeling a bit tired today. Any gentle ideas for getting through the afternoon?",
    "Explain in a few sentences why the sky looks blue during the day but red at sunset.",
    "What are three quick dinner ideas I can make with rice, eggs and some frozen vegetables?",
    "Write a short polite message I can send to my neighbour about their dog barking at night.",
    "How do I tell if a houseplant is getting too much water?",
    "Give me a simple plan for a twenty minute tidy of the living room before guests arrive.",
    "What's the difference between a latte and a flat white?",
    "Summarise how to change a flat bicycle tyre, step by step.",
    "Write a small Python function that returns the nth Fibonacci number, with a one line explanation.",
]
MODES = {"greedy": {"temperature": 0.0}, "temp0.7": {"temperature": 0.7, "seed": 1234}}
LIVE_FLAGS = (
    "--spec-type draft-mtp --spec-draft-n-max 4 --spec-draft-p-min 0.6 --spec-draft-ngl 99 "
    "--ctx-size 8192 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0 --cache-ram 1024 "
    "--n-gpu-layers 99 --cont-batching --fit off --flash-attn on --temp 0.7 --top-k 64 "
    "--top-p 0.95 --jinja --reasoning off --load-mode mmap --swa-full --metrics -lv 4"
).split()
TARGET = os.path.expanduser("~/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")


def http(url: str, body: dict | None = None, timeout: float = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def run_arm(name: str, binpath: str, ld: str, draft: str, port: int, args, log_dir: Path) -> dict:
    env = dict(os.environ, LD_LIBRARY_PATH=ld)
    log = open(log_dir / f"server-{name}.log", "w")
    flags = list(LIVE_FLAGS)
    if args.slim:
        flags = [f for f in flags if f != "--swa-full"]
        for k, v in (("--ctx-size", "4096"), ("--cache-ram", "0")):
            flags[flags.index(k) + 1] = v
    cmd = [binpath, "--model", TARGET, "--model-draft", draft, "--host", "127.0.0.1", "--port", str(port)] + flags
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    rows: list[dict] = []
    t0 = time.time()
    try:
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"{name}: server exited rc={proc.returncode} (see {log.name})")
            try:
                if http(base + "/health", timeout=3).get("status") == "ok":
                    break
            except Exception:
                pass
            if time.time() - t0 > args.load_timeout:
                raise RuntimeError(f"{name}: load timeout")
            time.sleep(1.5)
        load_s = time.time() - t0
        rss_mb = int(re.search(r"VmRSS:\s+(\d+)", Path(f"/proc/{proc.pid}/status").read_text()).group(1)) // 1024
        order = PROMPTS[args.rotate :] + PROMPTS[: args.rotate]

        def ask(prompt: str, mode: str) -> dict:
            body = {
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                "max_tokens": args.max_tokens,
                "stream": False,
                **MODES[mode],
            }
            r = http(base + "/v1/chat/completions", body)
            t = r["timings"]
            return {
                "tps": t["predicted_per_second"],
                "n": t["predicted_n"],
                "draft_n": t.get("draft_n", 0),
                "draft_acc": t.get("draft_n_accepted", 0),
                "text": r["choices"][0]["message"]["content"],
            }

        ask(order[0], "greedy")  # warmup, discarded
        for mode in MODES:
            for p in order:
                row = ask(p, mode)
                row.update(arm=name, mode=mode, prompt=PROMPTS.index(p))
                rows.append(row)
        rss_after = int(re.search(r"VmRSS:\s+(\d+)", Path(f"/proc/{proc.pid}/status").read_text()).group(1)) // 1024
        return {"rows": rows, "load_s": load_s, "rss_mb": rss_mb, "rss_after_mb": rss_after}
    finally:
        try:
            os.killpg(proc.pid, signal.SIGINT)  # SIGINT -> clean shutdown prints spec statistics
            proc.wait(timeout=30)
        except Exception:
            os.killpg(proc.pid, signal.SIGKILL)
        log.close()


def summarize(res: dict) -> dict:
    out: dict = {}
    for arm, d in res.items():
        a: dict = {"load_s": round(d["load_s"], 1), "rss_mb": d["rss_mb"], "rss_after_mb": d["rss_after_mb"]}
        for mode in MODES:
            rs = [r for r in d["rows"] if r["mode"] == mode]
            tps = [r["tps"] for r in rs]
            dn, da = sum(r["draft_n"] for r in rs), sum(r["draft_acc"] for r in rs)
            a[mode] = {
                "n": len(rs),
                "tps_median": round(statistics.median(tps), 2),
                "tps_mean": round(statistics.mean(tps), 2),
                "tps_min": round(min(tps), 2),
                "tps_max": round(max(tps), 2),
                "tokens": sum(r["n"] for r in rs),
                "accept": round(da / dn, 4) if dn else None,
            }
        out[arm] = a
    first = next(iter(res))
    ident = {}
    for arm, d in res.items():
        g = {r["prompt"]: hashlib.sha1(r["text"].encode()).hexdigest() for r in d["rows"] if r["mode"] == "greedy"}
        g0 = {r["prompt"]: hashlib.sha1(r["text"].encode()).hexdigest() for r in res[first]["rows"] if r["mode"] == "greedy"}
        ident[arm] = f"{sum(g[k] == g0[k] for k in g0)}/{len(g0)} greedy replies byte-identical to {first}"
    out["_greedy_identity"] = ident
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", action="append", required=True, help="NAME=SERVER_BIN|LD_LIBRARY_PATH|DRAFT_GGUF (in run order)")
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--slim", action="store_true",
                    help="smaller server footprint (ctx 4096, no --swa-full, no prompt cache): use when MemAvailable is tight")
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--load-timeout", type=int, default=180)
    ap.add_argument("--rotate", type=int, default=0, help="rotate the prompt order by this many")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {}
    for i, spec in enumerate(args.arm):
        name, rest = spec.split("=", 1)
        binpath, ld, draft = rest.split("|")
        args.rotate = (3 * i) % len(PROMPTS)  # rotate the prompt order per arm
        print(f"[arm {name}] starting", flush=True)
        res[name] = run_arm(name, binpath, ld, draft, args.port, args, out.parent)
        out.write_text(json.dumps({"summary": summarize(res), "raw": res}, indent=1))
        print(json.dumps(summarize(res)[name]), flush=True)
    print(json.dumps(summarize(res), indent=1))


if __name__ == "__main__":
    sys.exit(main())
