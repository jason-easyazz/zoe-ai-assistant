#!/usr/bin/env python3
"""Grammar-constrained router eval on the full 81-case corpus (:11435).

Extends labs/two-stage-router-eval/run_two_stage.py with llama.cpp GBNF
grammars that constrain the decoder's output to EXACTLY the legal call
syntax, with tool names restricted to a legal set:

  functok-ga  fine-tuned functok GGUF, single-stage, grammar over ALL 21
              names (20 tools + <unused20> chat escape).  The grammar(a) leg.
  functok-gb  HYBRID: SetFit stage-1 top-3 shortlist (logreg head, the
              zero-miss stage 1 from labs/two-stage-router-eval) + functok
              decoder with the grammar restricted to the SHORTLIST's tools.
              No schema block in the prompt (functok keeps its ~47-token
              prompt); the shortlist only narrows the grammar.
  stock-gb    two-stage config-a with grammar(b): stock FunctionGemma Q8,
              shortlisted tool declarations rendered via /apply-template
              (the model's own chat template), decoded via /completion with
              the shortlist grammar.  llama-server rejects a custom
              "grammar" together with "tools" (server-common.cpp: "Cannot
              use custom grammar constraints with tools."), hence the
              apply-template + /completion route.

  --gate G    stage-1 chat gate for the *-gb configs (default 0.0 = OFF:
              grammar has its own chat escape; the 0.4 gate ate 17 tool
              turns in the ungrammared config-a run).  Pass 0.4 to
              reproduce config-a gating.

Grammar shape (verified live against this box's build b9733; special-token
pieces like <start_function_call> and <unusedK> match GBNF string literals;
<unused20> is not rendered into content, so empty output == chat):

  root ::= chat | call
  chat ::= "<unused20>"                     # functok configs only
  call ::= prefix? "<start_function_call>call:" name "{" inner "}"
           "<end_function_call>"
  name ::= "tool_a" | "tool_b" | ...        # the legal set under test

Same harness discipline as the parent labs: memory gate (500 MB floor per
the 2026-07-14 non-prod regime), port-free + /props identity check, always
kills its server.  Scoring identical to labs/needle-benchmark.

Usage (repo root):
  python3 labs/router-90-campaign/run_grammar_eval.py --config functok-ga
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
sys.path.insert(0, str(LABS / "two-stage-router-eval"))
from run_two_stage import (  # noqa: E402
    CORPUS, TOOLS_JSON, ARTIFACTS, GGUF_STOCK, GGUF_FUNCTOK, EMBED_MODEL,
    DOMAIN_TOOLS, GENERAL_CHAT_TOOL, SYSTEM_PROMPT_PLAIN,
    SYSTEM_PROMPT_FUNCTOK, mem_available_mb, rss_mb, to_openai_tools,
    post_chat, score, start_server, summarize)

MIN_AVAILABLE_MB = 500  # non-prod regime floor (2026-07-14)


def build_grammar(names: list[str], functok: bool) -> str:
    """GBNF constraining output to one legal call (or the chat escape)."""
    name_alt = " | ".join(f'"{n}"' for n in names)
    lines = []
    if functok:
        prefix_alt = " | ".join(f'"<unused{i}>"' for i in range(21))
        lines += ["root ::= chat | call", 'chat ::= "<unused20>"',
                  f"prefix ::= {prefix_alt}",
                  ('call ::= prefix? "<start_function_call>call:" name '
                   '"{" inner "}" "<end_function_call>"')]
    else:
        # stock model: no functional tokens; general_chat must be in `names`
        lines += ["root ::= call",
                  ('call ::= "<start_function_call>call:" name '
                   '"{" inner "}" "<end_function_call>"')]
    lines += [f"name ::= {name_alt}", "inner ::= [^{}]*"]
    return "\n".join(lines)


def post_json(port: int, path: str, payload: dict, timeout=120.0) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def parse_call(text: str) -> str | None:
    m = re.search(r"call:([a-zA-Z0-9_]+)", text or "")
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True,
                    choices=["functok-ga", "functok-gb", "stock-gb"])
    ap.add_argument("--gate", type=float, default=0.0,
                    help="stage-1 chat gate for *-gb configs (0.0 = off)")
    ap.add_argument("--head", default="logreg", choices=["logreg", "mlp"],
                    help="stage-1 SetFit head for *-gb configs")
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    avail = mem_available_mb()
    if avail < MIN_AVAILABLE_MB:
        print(f"ABORT: MemAvailable={avail} MB < {MIN_AVAILABLE_MB} MB",
              file=sys.stderr)
        return 2

    cases = [json.loads(l) for l in CORPUS.open()]
    raw_tools = json.loads(TOOLS_JSON.read_text())
    all_names = [t["name"] for t in raw_tools]
    tool_by_name = {t["name"]: t for t in raw_tools}
    tool_by_name["general_chat"] = GENERAL_CHAT_TOOL

    use_shortlist = args.config.endswith("-gb")
    functok = args.config.startswith("functok")
    gguf = GGUF_FUNCTOK if functok else GGUF_STOCK

    if use_shortlist:
        import joblib
        import numpy as np
        from fastembed import TextEmbedding
        clf = joblib.load(ARTIFACTS / f"head_{args.head}.joblib")
        embedder = TextEmbedding(model_name=EMBED_MODEL)
        list(embedder.embed(["warmup"]))

    proc = start_server(gguf, args.port)
    out: dict = {"config": args.config, "gguf": gguf, "gate": args.gate,
                 "head": args.head if use_shortlist else None,
                 "grammar": "shortlist" if use_shortlist else "all-21",
                 "mem_available_mb_before": avail,
                 "rss_mb_after_load": rss_mb(proc.pid)}
    results, lat = [], []
    try:
        post_chat(args.port, {"model": "x", "max_tokens": 8, "messages": [
            {"role": "user", "content": "hi"}]})
        for case in cases:
            t0 = time.perf_counter()
            rec = {"id": case["id"], "style": case["style"],
                   "expected": case["expected"]}
            gated = False
            if use_shortlist:
                import numpy as np
                v = np.asarray(list(embedder.embed([case["text"]])),
                               dtype=np.float32)
                v /= (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
                proba = clf.predict_proba(v)[0]
                order = proba.argsort()[::-1]
                classes = clf.classes_
                top, conf = classes[order[0]], float(proba[order[0]])
                top3 = [c for c in classes[order] if c != "chat"][:3]
                rec.update(top_domain=top, conf=round(conf, 3),
                           shortlist=list(top3))
                gated = args.gate > 0 and (top == "chat" or conf < args.gate)
                legal = [n for d in top3 for n in DOMAIN_TOOLS[d]]
            else:
                legal = list(all_names)

            if gated:
                name, raw = "general_chat", ""
                rec["gated"] = True
            elif functok:
                grammar = build_grammar(legal, functok=True)
                resp = post_chat(args.port, {
                    "model": "functiongemma", "temperature": 0,
                    "max_tokens": 64, "stop": ["<end_function_call>"],
                    "grammar": grammar,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT_FUNCTOK},
                        {"role": "user", "content": case["text"]}]})
                raw = resp["choices"][0]["message"].get("content") or ""
                name = parse_call(raw)
            else:  # stock-gb: apply-template with shortlist tools, /completion
                grammar = build_grammar(legal + ["general_chat"],
                                        functok=False)
                tools = to_openai_tools(
                    [tool_by_name[n] for n in legal] + [GENERAL_CHAT_TOOL])
                tpl = post_json(args.port, "/apply-template", {
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT_PLAIN},
                        {"role": "user", "content": case["text"]}],
                    "tools": tools})
                resp = post_json(args.port, "/completion", {
                    "prompt": tpl["prompt"], "temperature": 0,
                    "n_predict": 64, "grammar": grammar,
                    "stop": ["<end_function_call>"]})
                raw = resp.get("content", "")
                name = parse_call(raw)
            ms = (time.perf_counter() - t0) * 1000
            lat.append(ms)
            ok = score(case, name)
            rec.update(predicted=name, ok=ok, latency_ms=round(ms, 1),
                       raw=raw[:200])
            if not ok and use_shortlist and not gated:
                offered = set(legal)
                rec["failure_stage"] = (
                    "stage2_wrong_pick" if offered & set(case["expected"])
                    or "general_chat" in case["expected"]
                    else "stage1_shortlist_miss")
            elif not ok and gated:
                rec["failure_stage"] = "stage1_gate_ate_tool_turn"
            results.append(rec)
        out["rss_mb_after_bench"] = rss_mb(proc.pid)
        out["mem_available_mb_after"] = mem_available_mb()
    finally:
        proc.kill()
        proc.wait()

    out["summary"] = summarize(results, lat)
    attribution: dict[str, int] = {}
    for r in results:
        if "failure_stage" in r:
            attribution[r["failure_stage"]] = attribution.get(
                r["failure_stage"], 0) + 1
    if attribution:
        out["summary"]["failure_attribution"] = attribution
    out["cases"] = results

    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    tag = args.tag or (args.config + (f"-gate{args.gate}" if args.gate else ""))
    path = res_dir / f"{tag}.json"
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "cases"}, indent=1))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
