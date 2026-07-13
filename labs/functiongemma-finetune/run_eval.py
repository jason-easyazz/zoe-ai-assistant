#!/usr/bin/env python3
"""Eval a (fine-tuned) FunctionGemma GGUF on the held-out 81-case corpus.

Same harness discipline as labs/functiongemma-feasibility/run_feasibility.py
(memory gate, port-free + identity checks, always kills its server), plus a
--variant switch:

  plain    stock FunctionGemma usage — tool declarations in the prompt;
           benches BOTH the full 20+1-tool block and the 3-tool shortlist.
  functok  Octopus-style — NO tool declarations in the prompt (that is the
           latency point of the functional-token fine-tune); the model was
           trained to open with <unusedK>. llama-server does not render
           special tokens in chat content, so scoring reads the call:NAME
           text for tool cases and treats empty output as no-call (chat).

Usage (repo root, never the live port):
  python3 labs/functiongemma-finetune/run_eval.py \
      --gguf <path.gguf> --variant functok --tag functok-q8-cpu [--ngl 0]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "needle-benchmark" / "corpus.jsonl"
LLAMA_SERVER = "/home/zoe/llama.cpp/build-jetson-new/bin/llama-server"
MIN_AVAILABLE_MB = 2048

SYSTEM_PROMPT_PLAIN = (
    "You are Zoe, a local voice assistant. Route the user's utterance to "
    "exactly one function call with the right arguments. If the utterance is "
    "ordinary conversation with no actionable command, call general_chat."
)
SYSTEM_PROMPT_FUNCTOK = (
    "You are Zoe's router. Answer with the routing token for the user's "
    "utterance, then the function call arguments."
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
        {"type": "function",
         "function": {"name": t["name"], "description": t["description"],
                      "parameters": {"type": "object",
                                     "properties": t["parameters"]}}}
        for t in raw
    ]


def post_chat(port: int, payload: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def predicted_tool(resp: dict) -> str | None:
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if calls:
        return calls[0]["function"]["name"]
    text = msg.get("content") or ""
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


def bench(port: int, tools: list[dict] | None, cases: list[dict],
          label: str, system_prompt: str) -> dict:
    lat, results, prompt_tokens = [], [], []
    for case in cases:
        payload = {
            "model": "functiongemma", "temperature": 0, "max_tokens": 64,
            "stop": ["<end_function_call>"],
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": case["text"]}],
        }
        if tools is not None:
            payload["tools"] = tools
        t0 = time.perf_counter()
        resp = post_chat(port, payload)
        ms = (time.perf_counter() - t0) * 1000
        lat.append(ms)
        prompt_tokens.append(resp.get("usage", {}).get("prompt_tokens", 0))
        name = predicted_tool(resp)
        results.append({"id": case["id"], "style": case["style"],
                        "predicted": name, "expected": case["expected"],
                        "ok": score(case, name), "latency_ms": round(ms, 1)})
    by_style: dict[str, list[bool]] = {}
    for r in results:
        by_style.setdefault(r["style"], []).append(r["ok"])
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    chat = by_style.get("chat", [])
    return {
        "label": label, "n": len(results),
        "prompt_tokens_p50": int(statistics.median(prompt_tokens)),
        "latency_ms_p50": round(statistics.median(lat), 1),
        "latency_ms_p90": round(statistics.quantiles(lat, n=10)[8], 1),
        "accuracy_overall_pct": pct([r["ok"] for r in results]),
        "accuracy_canonical_pct": pct(by_style.get("canonical", [])),
        "accuracy_paraphrase_pct": pct(by_style.get("paraphrase", [])),
        "chat_false_positive_pct": (round(100 - (pct(chat) or 0), 1)
                                    if chat else None),
        "cases": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--variant", choices=["plain", "functok"], required=True)
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--ngl", type=int, default=0)
    ap.add_argument("--tag", default="run")
    args = ap.parse_args()

    avail = mem_available_mb()
    if avail < MIN_AVAILABLE_MB:
        print(f"ABORT: MemAvailable={avail} MB < {MIN_AVAILABLE_MB} MB safety "
              "gate. This box runs the live brain — free memory and re-run.",
              file=sys.stderr)
        return 2

    cases = [json.loads(l) for l in CORPUS.open()]
    raw_tools = json.loads((HERE / "zoe_tools.json").read_text())
    raw_tools = raw_tools + [GENERAL_CHAT_TOOL]
    full_tools = to_openai_tools(raw_tools)
    small_names = {"get_time", "set_timer", "general_chat"}
    small_tools = [t for t in full_tools
                   if t["function"]["name"] in small_names]

    try:
        urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health",
                               timeout=1)
        print(f"ABORT: something is already listening on :{args.port}",
              file=sys.stderr)
        return 2
    except Exception:
        pass

    cmd = [LLAMA_SERVER, "--model", args.gguf, "--host", "127.0.0.1",
           "--port", str(args.port), "--ctx-size", "4096",
           "--n-gpu-layers", str(args.ngl), "--jinja", "--parallel", "1",
           "--threads", "4"]
    print("starting:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    out: dict = {"gguf": args.gguf, "variant": args.variant, "ngl": args.ngl,
                 "cmd": " ".join(cmd), "mem_available_mb_before": avail}
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
            print("server never became healthy", file=sys.stderr)
            return 1
        out["cold_load_s"] = round(time.perf_counter() - t0, 2)
        with urllib.request.urlopen(
                f"http://127.0.0.1:{args.port}/props", timeout=5) as r:
            served = json.load(r).get("model_path", "")
        if proc.poll() is not None or served != args.gguf:
            print(f"ABORT: server on :{args.port} is not ours "
                  f"(model_path={served!r})", file=sys.stderr)
            return 2
        out["rss_mb_after_load"] = rss_mb(proc.pid)

        if args.variant == "plain":
            post_chat(args.port, {"model": "x", "max_tokens": 8,
                                  "messages": [{"role": "user", "content": "hi"}],
                                  "tools": small_tools})
            out["small_3tool"] = bench(
                args.port, small_tools,
                [c for c in cases if c["expected"][0] in small_names
                 or "general_chat" in c["expected"]],
                "3-tool block", SYSTEM_PROMPT_PLAIN)
            out["full_20tool"] = bench(args.port, full_tools, cases,
                                       "full 20+1-tool block",
                                       SYSTEM_PROMPT_PLAIN)
        else:
            post_chat(args.port, {"model": "x", "max_tokens": 8,
                                  "messages": [{"role": "user", "content": "hi"}]})
            out["functok_noschema"] = bench(args.port, None, cases,
                                            "functional-token, no schema",
                                            SYSTEM_PROMPT_FUNCTOK)
        out["rss_mb_after_bench"] = rss_mb(proc.pid)
        out["mem_available_mb_after"] = mem_available_mb()
    finally:
        proc.kill()
        proc.wait()

    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    path = res_dir / f"{args.tag}.json"
    path.write_text(json.dumps(out, indent=1))
    slim = dict(out)
    for k in ("small_3tool", "full_20tool", "functok_noschema"):
        if k in slim:
            slim[k] = {kk: vv for kk, vv in slim[k].items() if kk != "cases"}
    print(json.dumps(slim, indent=1))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
