#!/usr/bin/env python3
"""Needle (cactus-compute/needle, 26M single-shot function-calling model) as a
tool router over Zoe's real 20-tool set — accuracy + CPU latency benchmark.

LAB-ONLY. Runs inside the spike venv built by setup.sh (python 3.11, jax CPU),
NOT the zoe-data runtime. Never touches prod services.

Modes:
  full      — the whole 20-tool schema block in the encoder (router-as-decider)
  shortlist — Needle's own contrastive retrieval head shortlists top-3 tools,
              then the decoder decides among those 3 (router + prefilter)
  retrieval — score ONLY the retrieval head: is the expected tool in top-k?
              (NOTE: the released checkpoint's contrastive head emits all-zero
              embeddings — this mode documents that failure, see README)
  oracle    — decoder best case: expected tool guaranteed in a 3-tool
              candidate set (what a WORKING external prefilter would hand it)

Engineering notes (why this wrapper exists instead of needle's generate()):
  * needle's generate() feeds the encoder a variable-length sequence, so XLA
    recompiles on nearly every query (~6s/call observed). We pad the encoder
    input to a fixed bucket → one compile, steady-state latency thereafter.
  * generate() decodes over a 512-slot buffer with a full re-decode per token
    (no KV cache). Tool calls are short; a 64-slot buffer is plenty and ~64x
    cheaper per step.
Output JSON: per-case decisions + latency stats.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# A no-tool escape hatch: as a deployed router Needle must be able to say
# "no tool — this is conversation". Single-shot FC models always emit a call,
# so we give it an explicit general_chat tool (standard router design).
GENERAL_CHAT = {
    "name": "general_chat",
    "description": ("Reply conversationally. Use for greetings, chit-chat, opinions, "
                    "general knowledge questions, and anything no other tool covers."),
    "parameters": {},
}


def load_needle(ckpt: str):
    import jax.numpy as jnp  # noqa: F401 — fail fast if env is wrong
    from needle import SimpleAttentionNetwork, load_checkpoint, get_tokenizer

    params, config = load_checkpoint(ckpt)
    model = SimpleAttentionNetwork(config)
    tok = get_tokenizer()
    return model, params, tok


def make_fast_generate(model, params, tok, enc_len: int, gen_len: int = 64):
    """Fixed-shape single-example generate → (text, ms). See module docstring."""
    import jax
    import numpy as np
    import jax.numpy as jnp
    from needle.model.architecture import make_causal_mask, make_padding_mask
    from needle.model.run import normalize_tools, restore_tool_names, _build_encoder_input
    from needle.model.constrained import build_constrained_decoder

    pad_id, eos_id = tok.pad_token_id, tok.eos_token_id
    tgt_mask = make_causal_mask(gen_len)

    @jax.jit
    def encode_fn(p, enc_input, src_mask):
        return model.apply({"params": p}, enc_input, src_mask=src_mask, method="encode")

    @jax.jit
    def decode_fn(p, dec_buffer, encoder_out, cross_mask):
        return model.apply({"params": p}, dec_buffer, encoder_out,
                           self_mask=tgt_mask, cross_mask=cross_mask, method="decode")

    def run(query: str, tools_json: str) -> tuple[str, float]:
        t0 = time.perf_counter()
        tools_json, name_map = normalize_tools(tools_json)
        toks = _build_encoder_input(tok, query, tools_json, enc_len)
        enc = np.full((1, enc_len), pad_id, dtype=np.int32)
        enc[0, :len(toks)] = toks
        enc = jnp.array(enc)
        src_mask = make_padding_mask(enc, pad_id)
        encoder_out, enc_mask = encode_fn(params, enc, src_mask)

        dec = jnp.full((1, gen_len), pad_id, dtype=jnp.int32).at[0, 0].set(eos_id)
        cd = build_constrained_decoder([tools_json], tok)
        out = []
        logits = decode_fn(params, dec, encoder_out, enc_mask)
        for i in range(gen_len - 1):
            nl = logits[0, i]
            if cd.is_active(0):
                lnp = cd.constrain_logits(np.array(nl), 0)
                nt = int(np.argmax(lnp))
            else:
                nt = int(jnp.argmax(nl))
            cd.update(0, nt)
            if nt == eos_id:
                break
            out.append(nt)
            dec = dec.at[0, i + 1].set(nt)
            logits = decode_fn(params, dec, encoder_out, enc_mask)
        text = tok.decode(out)
        if text.startswith("<tool_call>"):
            text = text[len("<tool_call>"):]
        text = restore_tool_names(text, name_map)
        return text, (time.perf_counter() - t0) * 1000

    return run


def tool_desc_strings(tools: list[dict]) -> list[str]:
    return [f"{t['name']}: {t['description']}" for t in tools]


def pred_tool_name(raw: str) -> str:
    try:
        calls = json.loads(raw)
        if isinstance(calls, list) and calls:
            return calls[0].get("name", "")
        if isinstance(calls, dict):
            return calls.get("name", "")
    except Exception:
        pass
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True, help="path to needle.pkl")
    ap.add_argument("--tools", default=str(HERE / "zoe_tools.json"))
    ap.add_argument("--corpus", default=str(HERE / "corpus.jsonl"))
    ap.add_argument("--mode", choices=["full", "shortlist", "retrieval", "oracle"],
                    default="full")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    tools = json.loads(Path(args.tools).read_text()) + [GENERAL_CHAT]
    cases = [json.loads(l) for l in Path(args.corpus).read_text().splitlines() if l.strip()]

    model, params, tok = load_needle(args.ckpt)
    full_tools_json = json.dumps(tools, separators=(",", ":"))
    n_full_tok = len(tok.encode(full_tools_json))
    print(f"tools={len(tools)} full-block needle-tokens={n_full_tok}", file=sys.stderr)

    from needle.model.run import retrieve_tools, encode_for_retrieval
    import numpy as np

    results, lat = [], []

    if args.mode == "retrieval":
        # Pre-embed tool descriptions once (that's how a deployment would run).
        descs = tool_desc_strings(tools)
        t_emb = encode_for_retrieval(model, params, tok, descs)
        _ = encode_for_retrieval(model, params, tok, ["warmup"])  # compile
        for c in cases:
            t0 = time.perf_counter()
            q_emb = encode_for_retrieval(model, params, tok, [c["text"]])
            scores = (q_emb @ t_emb.T)[0]
            top = np.argsort(-scores)[: args.top_k]
            ms = (time.perf_counter() - t0) * 1000
            names = [tools[i]["name"] for i in top]
            hit = any(e in names for e in c["expected"])
            results.append({**c, "pred": names, "ok": hit, "ms": round(ms, 1)})
            lat.append(ms)
            print(f"[{'HIT ' if hit else 'MISS'}] {c['id']:14s} {names} {ms:.0f}ms",
                  file=sys.stderr)
    else:
        if args.mode in ("shortlist", "oracle"):
            if args.mode == "shortlist":
                descs = tool_desc_strings(tools)
                t_emb = encode_for_retrieval(model, params, tok, descs)
            enc_len = 512
        else:
            enc_len = min(1024, ((n_full_tok + 96) // 64 + 1) * 64)
        gen = make_fast_generate(model, params, tok, enc_len=enc_len)
        gen("warmup", json.dumps(tools[:3]))  # trigger XLA compile
        import random
        by_name = {t["name"]: t for t in tools}
        rng = random.Random(7)
        for c in cases:
            if args.mode == "oracle":
                # Decoder-only best case: candidate set = the expected tool(s) +
                # fixed-seed distractors + general_chat (what a WORKING external
                # prefilter, e.g. Zoe's bge-small, would hand Needle).
                t0 = time.perf_counter()
                names = [e for e in c["expected"] if e in by_name][:1]
                pool = [n for n in by_name if n not in names and n != "general_chat"]
                names += rng.sample(pool, max(0, args.top_k - len(names)))
                if "general_chat" not in names:
                    names.append("general_chat")
                sub = [by_name[n] for n in names]
                rng.shuffle(sub)
                raw, _ = gen(c["text"], json.dumps(sub, separators=(",", ":")))
                ms = (time.perf_counter() - t0) * 1000
            elif args.mode == "shortlist":
                t0 = time.perf_counter()
                q_emb = encode_for_retrieval(model, params, tok, [c["text"]])
                scores = (q_emb @ t_emb.T)[0]
                top = list(np.argsort(-scores)[: args.top_k])
                # general_chat always in the candidate set (the escape hatch).
                gc = len(tools) - 1
                if gc not in top:
                    top.append(gc)
                sub = [tools[i] for i in top]
                raw, _ = gen(c["text"], json.dumps(sub, separators=(",", ":")))
                ms = (time.perf_counter() - t0) * 1000  # retrieval + decide, end to end
            else:
                raw, ms = gen(c["text"], full_tools_json)
            name = pred_tool_name(raw)
            ok = name in c["expected"]
            results.append({**c, "pred": name, "raw": raw[:160], "ok": ok,
                            "ms": round(ms, 1)})
            lat.append(ms)
            print(f"[{'OK  ' if ok else 'MISS'}] {c['id']:14s} -> {name:24s} {ms:.0f}ms",
                  file=sys.stderr)

    def acc(style):
        rows = [r for r in results if style in (None, r["style"])]
        return round(100 * sum(r["ok"] for r in rows) / max(len(rows), 1), 1)

    summary = {
        "mode": args.mode, "n": len(results),
        "acc_overall": acc(None), "acc_canonical": acc("canonical"),
        "acc_paraphrase": acc("paraphrase"), "acc_chat": acc("chat"),
        "lat_p50_ms": round(statistics.median(lat), 1),
        "lat_p90_ms": round(sorted(lat)[int(0.9 * len(lat))], 1),
        "lat_mean_ms": round(statistics.mean(lat), 1),
        "full_tools_needle_tokens": n_full_tok,
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
