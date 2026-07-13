#!/usr/bin/env python3
"""FunctionGemma-270M feasibility harness (LAB ONLY — see README.md).

Measures, against a llama-server instance it starts itself on a NON-live port:
  1. cold-load time (spawn -> /health 200)
  2. RSS of the loaded server (VmRSS), before and after the benchmark
  3. end-to-end routing latency p50/p90 (full 20-tool block AND 3-tool block)
  4. stock accuracy on the 81-case corpus (overall / canonical / paraphrase /
     chat false-positive rate), scored the same way as labs/needle-benchmark

HARD SAFETY GATE: refuses to start if MemAvailable < 2 GB (this box runs the
live brain + Kokoro and is chronically memory-tight). No override flag by
design — free memory first.

Usage (from repo root):
  python3 labs/functiongemma-feasibility/run_feasibility.py \
      --gguf /home/zoe/models/lab/functiongemma-270m-it-Q4_K_M.gguf \
      [--ngl 0] [--port 11435] [--tag q4-cpu]

Writes results/<tag>.json next to this script and ALWAYS kills its server.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LLAMA_SERVER = "/home/zoe/llama.cpp/build-jetson-new/bin/llama-server"
MIN_AVAILABLE_MB = 2048
SYSTEM_PROMPT = (
    "You are Zoe, a local voice assistant. Route the user's utterance to "
    "exactly one function call with the right arguments. If the utterance is "
    "ordinary conversation with no actionable command, call general_chat."
)
GENERAL_CHAT_TOOL = {
    "name": "general_chat",
    "description": (
        "Escape hatch: the utterance is ordinary conversation, a question for "
        "the assistant, or chit-chat that needs no tool. Call this when no "
        "other function applies."
    ),
    "parameters": {"utterance": {"type": "string"}},
}


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable not found")


def rss_mb(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) // 1024
    return -1


def to_openai_tools(raw: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {"type": "object", "properties": t["parameters"]},
            },
        }
        for t in raw
    ]


def post_chat(port: int, payload: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def predicted_tool(resp: dict) -> str | None:
    """Extract the called tool name from a llama-server chat response.

    Prefers structured tool_calls; falls back to scraping the text for a
    known-style call (FunctionGemma functional-token output or JSON)."""
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if calls:
        return calls[0]["function"]["name"]
    text = msg.get("content") or ""
    # FunctionGemma functional-token format:
    #   <start_function_call>call:NAME{arg:<escape>v<escape>}<end_function_call>
    # llama.cpp's --jinja does NOT map it to structured tool_calls, so parse it.
    import re

    m = re.search(r"call:([a-zA-Z0-9_]+)", text)
    if m:
        return m.group(1)
    m = re.search(r'"name"\s*:\s*"([a-zA-Z0-9_]+)"', text)
    if m:
        return m.group(1)
    return None


def score(case: dict, name: str | None) -> bool:
    expected = case["expected"]
    if name is None or name == "general_chat":
        return "general_chat" in expected
    return name in expected


def bench(port: int, tools: list[dict], cases: list[dict], label: str) -> dict:
    lat, results = [], []
    prompt_tokens = []
    for case in cases:
        payload = {
            "model": "functiongemma",
            "temperature": 0,
            "max_tokens": 64,
            # cut generation at the first completed functional-token call
            "stop": ["<end_function_call>"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": case["text"]},
            ],
            "tools": tools,
        }
        t0 = time.perf_counter()
        resp = post_chat(port, payload)
        ms = (time.perf_counter() - t0) * 1000
        lat.append(ms)
        prompt_tokens.append(resp.get("usage", {}).get("prompt_tokens", 0))
        name = predicted_tool(resp)
        results.append(
            {"id": case["id"], "style": case["style"], "predicted": name,
             "expected": case["expected"], "ok": score(case, name),
             "latency_ms": round(ms, 1)}
        )
    by_style: dict[str, list[bool]] = {}
    for r in results:
        by_style.setdefault(r["style"], []).append(r["ok"])
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    chat = by_style.get("chat", [])
    return {
        "label": label,
        "n": len(results),
        "prompt_tokens_p50": int(statistics.median(prompt_tokens)),
        "latency_ms_p50": round(statistics.median(lat), 1),
        "latency_ms_p90": round(statistics.quantiles(lat, n=10)[8], 1),
        "accuracy_overall_pct": pct([r["ok"] for r in results]),
        "accuracy_canonical_pct": pct(by_style.get("canonical", [])),
        "accuracy_paraphrase_pct": pct(by_style.get("paraphrase", [])),
        "chat_false_positive_pct": (
            round(100 - (pct(chat) or 0), 1) if chat else None
        ),
        "cases": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--ngl", type=int, default=0)
    ap.add_argument("--tag", default="run")
    args = ap.parse_args()

    avail = mem_available_mb()
    if avail < MIN_AVAILABLE_MB:
        print(
            f"ABORT: MemAvailable={avail} MB < {MIN_AVAILABLE_MB} MB safety "
            "gate. This box runs the live brain — free memory and re-run.",
            file=sys.stderr,
        )
        return 2

    cases = [json.loads(l) for l in (HERE / "corpus.jsonl").open()]
    raw_tools = json.loads((HERE / "zoe_tools.json").read_text())
    raw_tools = raw_tools + [GENERAL_CHAT_TOOL]
    full_tools = to_openai_tools(raw_tools)
    # 3-tool block: general_chat + the two highest-traffic domains
    small_names = {"get_time", "set_timer", "general_chat"}
    small_tools = [t for t in full_tools if t["function"]["name"] in small_names]

    # refuse to start if something already answers on the port — otherwise the
    # readiness probe (and the whole benchmark) would hit a foreign server
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=1)
        print(f"ABORT: something is already listening on :{args.port}",
              file=sys.stderr)
        return 2
    except Exception:
        pass

    cmd = [
        LLAMA_SERVER, "--model", args.gguf, "--host", "127.0.0.1",
        "--port", str(args.port), "--ctx-size", "4096",
        "--n-gpu-layers", str(args.ngl), "--jinja", "--parallel", "1",
        "--threads", "4",
    ]
    print("starting:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    out: dict = {"gguf": args.gguf, "ngl": args.ngl, "cmd": " ".join(cmd),
                 "mem_available_mb_before": avail}
    try:
        t0 = time.perf_counter()
        for _ in range(600):
            if proc.poll() is not None:
                print("server died during load", file=sys.stderr)
                return 1
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{args.port}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("server never became healthy within the polling window",
                  file=sys.stderr)
            return 1
        out["cold_load_s"] = round(time.perf_counter() - t0, 2)
        # identity check: the 200 must come from OUR child serving OUR gguf,
        # not another process that grabbed the port mid-run
        with urllib.request.urlopen(
                f"http://127.0.0.1:{args.port}/props", timeout=5) as r:
            served = json.load(r).get("model_path", "")
        if proc.poll() is not None or served != args.gguf:
            print(f"ABORT: server on :{args.port} is not ours "
                  f"(model_path={served!r}, child alive={proc.poll() is None})",
                  file=sys.stderr)
            return 2
        out["rss_mb_after_load"] = rss_mb(proc.pid)

        # one warmup call, excluded from stats
        post_chat(args.port, {"model": "x", "max_tokens": 8, "messages": [
            {"role": "user", "content": "hi"}], "tools": small_tools})

        out["small_3tool"] = bench(args.port, small_tools,
                                   [c for c in cases
                                    if c["expected"][0] in small_names
                                    or "general_chat" in c["expected"]],
                                   "3-tool block")
        out["full_20tool"] = bench(args.port, full_tools, cases,
                                   "full 20+1-tool block")
        out["rss_mb_after_bench"] = rss_mb(proc.pid)
        out["mem_available_mb_after"] = mem_available_mb()
    finally:
        proc.kill()
        proc.wait()

    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    path = res_dir / f"{args.tag}.json"
    path.write_text(json.dumps(out, indent=1))
    slim = {k: v for k, v in out.items() if k not in ("small_3tool",
                                                      "full_20tool")}
    for k in ("small_3tool", "full_20tool"):
        slim[k] = {kk: vv for kk, vv in out[k].items() if kk != "cases"}
    print(json.dumps(slim, indent=1))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
