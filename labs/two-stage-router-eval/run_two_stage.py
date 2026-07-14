#!/usr/bin/env python3
"""Honest end-to-end eval of the TWO-STAGE router on the full 81-case corpus.

Stage 1: SetFit head (labs/setfit-router artifacts) on the frozen
BAAI/bge-small-en-v1.5 embedding -> top-3 domain shortlist + chat gate
(predicted chat OR max confidence below the gate threshold -> NO tool call).
Stage 2: stock FunctionGemma-270M Q8 (llama.cpp, CPU, :11435) decodes one
call from a schema block containing ONLY the shortlisted domains' tools
(+ general_chat escape hatch).

Configs:
  a        logreg head, gate 0.4  (headline)
  b        mlp head,    gate 0.7
  functok  single-stage fine-tuned FunctionGemma, no schema block
           (re-run of the 74.1% reference on this same harness)

Scoring is identical to labs/needle-benchmark / functiongemma-* evals:
predicted tool must be in the case's `expected` list; no call / general_chat
is correct iff general_chat is expected. Error attribution splits failures
into stage-1 (gate ate a tool turn, or gold tool absent from the offered
schema block) vs stage-2 (gold tool WAS offered, decoder picked wrong).

Same harness discipline as labs/functiongemma-feasibility/run_feasibility.py:
memory gate, port-free + /props identity check, always kills its server.
The caller is responsible for SIGSTOPping the idle trainer (see README).

Usage (repo root):
  python3 labs/two-stage-router-eval/run_two_stage.py --config a
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
CORPUS = LABS / "needle-benchmark" / "corpus.jsonl"
TOOLS_JSON = LABS / "functiongemma-finetune" / "zoe_tools.json"
ARTIFACTS = LABS / "setfit-router" / "artifacts"
LLAMA_SERVER = os.environ.get(
    "LLAMA_SERVER", "/home/zoe/llama.cpp/build-jetson-new/bin/llama-server")
GGUF_STOCK = os.environ.get(
    "GGUF_STOCK", "/home/zoe/models/lab/functiongemma-270m-it-Q8_0.gguf")
GGUF_FUNCTOK = os.environ.get(
    "GGUF_FUNCTOK",
    "/home/zoe/models/lab/functiongemma-270m-zoe-functok-Q8_0.gguf")
MIN_AVAILABLE_MB = 1500
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # pinned: prod semantic_router default

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

# Which of Zoe's 20 real tools a stage-1 domain unlocks in the stage-2 schema
# block. Static router policy (mirrors labs/setfit-router/labels.py domains);
# NOT derived from per-case gold alternates.
DOMAIN_TOOLS: dict[str, list[str]] = {
    "time": ["get_time"],
    "weather": ["get_weather"],
    "lists": ["shopping_list_add", "add_to_list", "show_list", "list_remove"],
    "calendar": ["show_calendar", "add_calendar_event"],
    "reminders": ["add_reminder", "list_reminders"],
    "timers": ["set_timer"],
    "notes": ["create_note", "note_search"],
    "journal": ["journal"],
    "people": ["people"],
    "music": ["media"],
    "smart_home": ["home"],
    "memory": ["recall_memory", "remember_fact", "remember_emotional_moment"],
    "chat": [],
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


def predicted_tool(resp: dict) -> tuple[str | None, str]:
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if calls:
        return calls[0]["function"]["name"], json.dumps(
            calls[0]["function"].get("arguments", ""))[:200]
    text = msg.get("content") or ""
    m = re.search(r"call:([a-zA-Z0-9_]+)", text)
    if m:
        return m.group(1), text[:200]
    m = re.search(r'"name"\s*:\s*"([a-zA-Z0-9_]+)"', text)
    if m:
        return m.group(1), text[:200]
    return None, text[:200]


def score(case: dict, name: str | None) -> bool:
    expected = case["expected"]
    if name is None or name == "general_chat":
        return "general_chat" in expected
    return name in expected


def pct(xs):
    return round(100 * sum(xs) / len(xs), 1) if xs else None


def summarize(results: list[dict], lat: list[float]) -> dict:
    by_style: dict[str, list[bool]] = {}
    for r in results:
        by_style.setdefault(r["style"], []).append(r["ok"])
    chat = by_style.get("chat", [])
    return {
        "n": len(results),
        "accuracy_overall_pct": pct([r["ok"] for r in results]),
        "accuracy_canonical_pct": pct(by_style.get("canonical", [])),
        "accuracy_paraphrase_pct": pct(by_style.get("paraphrase", [])),
        "chat_false_positive_pct": (round(100 - (pct(chat) or 0), 1)
                                    if chat else None),
        "latency_ms_p50": round(statistics.median(lat), 1),
        "latency_ms_p90": round(statistics.quantiles(lat, n=10)[8], 1),
    }


def start_server(gguf: str, port: int) -> subprocess.Popen:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
        raise SystemExit(f"ABORT: something already listens on :{port}")
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    cmd = [LLAMA_SERVER, "--model", gguf, "--host", "127.0.0.1",
           "--port", str(port), "--ctx-size", "4096",
           "--n-gpu-layers", "0", "--jinja", "--parallel", "1",
           "--threads", "4"]
    print("starting:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):
            if proc.poll() is not None:
                raise SystemExit("server died during load")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                       timeout=1)
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.2)
        else:
            raise SystemExit("server never became healthy")
        # identity check: our child, our gguf
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/props",
                                    timeout=5) as r:
            served = json.load(r).get("model_path", "")
        if proc.poll() is not None or served != gguf:
            raise SystemExit(f"ABORT: server on :{port} is not ours "
                             f"(model_path={served!r})")
    except BaseException:
        # never leak the spawned server, whatever failed post-spawn
        proc.kill()
        proc.wait()
        raise
    return proc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=["a", "b", "functok"], required=True)
    ap.add_argument("--port", type=int, default=11435)
    args = ap.parse_args()

    avail = mem_available_mb()
    if avail < MIN_AVAILABLE_MB:
        print(f"ABORT: MemAvailable={avail} MB < {MIN_AVAILABLE_MB} MB gate. "
              "This box runs the live brain — free memory and re-run.",
              file=sys.stderr)
        return 2

    cases = [json.loads(l) for l in CORPUS.open()]
    raw_tools = json.loads(TOOLS_JSON.read_text()) + [GENERAL_CHAT_TOOL]
    tool_by_name = {t["name"]: t for t in raw_tools}

    two_stage = args.config in ("a", "b")
    if two_stage:
        import joblib
        from fastembed import TextEmbedding
        head_name, gate = (("logreg", 0.4) if args.config == "a"
                           else ("mlp", 0.7))
        clf = joblib.load(ARTIFACTS / f"head_{head_name}.joblib")
        embedder = TextEmbedding(model_name=EMBED_MODEL)
        # warm the embedder (model load / first-call graph init)
        list(embedder.embed(["warmup"]))
        gguf = GGUF_STOCK
    else:
        head_name, gate, gguf = None, None, GGUF_FUNCTOK

    proc = start_server(gguf, args.port)
    out: dict = {"config": args.config, "gguf": gguf, "head": head_name,
                 "gate": gate, "mem_available_mb_before": avail,
                 "rss_mb_after_load": rss_mb(proc.pid)}
    results, lat_e2e, lat_s1, lat_s2 = [], [], [], []
    try:
        # warmup decode, excluded from stats
        post_chat(args.port, {"model": "x", "max_tokens": 8, "messages": [
            {"role": "user", "content": "hi"}]})
        import numpy as np
        for case in cases:
            t0 = time.perf_counter()
            rec = {"id": case["id"], "style": case["style"],
                   "expected": case["expected"]}
            if two_stage:
                v = np.asarray(list(embedder.embed([case["text"]])),
                               dtype=np.float32)
                v /= (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
                proba = clf.predict_proba(v)[0]
                order = proba.argsort()[::-1]
                classes = clf.classes_
                top = classes[order[0]]
                conf = float(proba[order[0]])
                top3 = [c for c in classes[order] if c != "chat"][:3]
                t1 = time.perf_counter()
                rec.update(top_domain=top, conf=round(conf, 3),
                           shortlist=list(top3))
                if top == "chat" or conf < gate:
                    name, raw = "general_chat", ""
                    rec["gated"] = True
                    t2 = t1
                else:
                    names = [n for d in top3 for n in DOMAIN_TOOLS[d]]
                    tools = to_openai_tools(
                        [tool_by_name[n] for n in names] + [GENERAL_CHAT_TOOL])
                    resp = post_chat(args.port, {
                        "model": "functiongemma", "temperature": 0,
                        "max_tokens": 64, "stop": ["<end_function_call>"],
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT_PLAIN},
                            {"role": "user", "content": case["text"]}],
                        "tools": tools})
                    t2 = time.perf_counter()
                    name, raw = predicted_tool(resp)
                    rec["prompt_tokens"] = resp.get("usage", {}).get(
                        "prompt_tokens", 0)
                lat_s1.append((t1 - t0) * 1000)
                lat_s2.append((t2 - t1) * 1000)
            else:
                resp = post_chat(args.port, {
                    "model": "functiongemma", "temperature": 0,
                    "max_tokens": 64, "stop": ["<end_function_call>"],
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT_FUNCTOK},
                        {"role": "user", "content": case["text"]}]})
                name, raw = predicted_tool(resp)
                rec["prompt_tokens"] = resp.get("usage", {}).get(
                    "prompt_tokens", 0)
            ms = (time.perf_counter() - t0) * 1000
            lat_e2e.append(ms)
            ok = score(case, name)
            rec.update(predicted=name, ok=ok, latency_ms=round(ms, 1),
                       raw=raw)
            # error attribution (two-stage only)
            if two_stage and not ok:
                gold_is_chat = "general_chat" in case["expected"]
                if rec.get("gated"):
                    rec["failure_stage"] = "stage1_gate_ate_tool_turn"
                elif gold_is_chat:
                    # gate let a chat turn through; general_chat was in the
                    # block, so the decoder owned the final mistake
                    rec["failure_stage"] = "stage2_chat_false_positive"
                else:
                    offered = {n for d in rec["shortlist"]
                               for n in DOMAIN_TOOLS[d]}
                    if offered & set(case["expected"]):
                        rec["failure_stage"] = "stage2_wrong_pick"
                    else:
                        rec["failure_stage"] = "stage1_shortlist_miss"
            results.append(rec)
        out["rss_mb_after_bench"] = rss_mb(proc.pid)
        out["mem_available_mb_after"] = mem_available_mb()
    finally:
        proc.kill()
        proc.wait()

    out["summary"] = summarize(results, lat_e2e)
    if two_stage:
        out["summary"]["stage1_ms_p50"] = round(statistics.median(lat_s1), 1)
        out["summary"]["stage1_ms_p90"] = round(
            statistics.quantiles(lat_s1, n=10)[8], 1)
        s2 = [x for x in lat_s2 if x > 0]
        out["summary"]["stage2_ms_p50"] = round(statistics.median(s2), 1)
        out["summary"]["stage2_ms_p90"] = round(
            statistics.quantiles(s2, n=10)[8], 1)
        attribution: dict[str, int] = {}
        for r in results:
            if "failure_stage" in r:
                attribution[r["failure_stage"]] = attribution.get(
                    r["failure_stage"], 0) + 1
        out["summary"]["failure_attribution"] = attribution
    out["cases"] = results

    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    path = res_dir / f"{args.config}.json"
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "cases"},
                     indent=1))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
