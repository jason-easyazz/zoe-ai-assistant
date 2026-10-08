"""Embedder quality probe (RAM lab, 2026-10-06): can ONE embedding model serve every tier?  hit@5 of bge-small (the router's, what the bake-off's shim serves) against
MiniLM (zoe-data's live embedder) on the lab's 20-needle sets, plus whether the shim's MiniLM vectors are the SAME vectors Chroma's own MiniLM function produces.

Pure retrieval, no Hindsight, no server: for each seed it embeds the 20 needle facts and ``distractors`` filler facts of the same shapes (``needles.chatter`` near-misses and
preferences), embeds the 40 questions (20 direct, 20 paraphrase), ranks by cosine and counts the questions whose answer token sits in the top 5 texts. It uses the shim's own
``OnnxEmbedder`` (the code the bake-off serves), one model at a time, so its footprint is one model (<= 220 MB).

    /home/zoe/.zoe/bakeoff-2026-10/hindsight-venv/bin/python scripts/perf/zmb/pilot/embed_quality_probe.py --seeds 12 --model bge|minilm
    /home/zoe/.zoe/bakeoff-2026-10/mempalace-venv/bin/python  scripts/perf/zmb/pilot/embed_quality_probe.py --parity      # shim MiniLM vs Chroma's MiniLM function
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from zmb import embed_shim, needles  # noqa: E402


def load(model: str) -> "embed_shim.OnnxEmbedder":
    onnx, tok, pooling, mid = embed_shim.find_model(None, model)
    return embed_shim.OnnxEmbedder(onnx, tok, pooling, mid)


def embed_all(emb, texts: "list[str]") -> "list[list[float]]":
    vecs, _ = embed_shim.embed_all(emb, texts)
    return vecs


def top5(qv, doc_vecs, texts) -> "list[str]":
    sc = sorted(range(len(texts)), key=lambda i: -sum(a * b for a, b in zip(qv, doc_vecs[i])))
    return [texts[i] for i in sc[:5]]


def seed_hits(emb, seed: str, distractors: int) -> "dict[str, int]":
    nd = needles.corpus(seed)
    filler = [c["text"] for c in needles.chatter(seed, 900) if c["kind"] != "chatter"][:distractors]
    texts = [n.fact for n in nd] + filler
    dv = embed_all(emb, texts)
    out = {"direct": 0, "paraphrase": 0}
    for kind in ("direct", "paraphrase"):
        qs = [getattr(n, kind) for n in nd]
        for n, qv in zip(nd, embed_all(emb, qs)):
            if any(n.answer.lower() in t.lower() for t in top5(qv, dv, texts)):
                out[kind] += 1
    return out


def parity() -> int:
    """The shim's MiniLM against Chroma's own ONNXMiniLM_L6_V2 on the same 120 texts (needs chromadb: the mempalace venv)."""
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    emb = load("minilm")
    texts = [n.fact for n in needles.corpus("parity")] + [n.paraphrase for n in needles.corpus("parity")] + [c["text"] for c in needles.chatter("parity", 400)][:80]
    mine = embed_all(emb, texts)
    ref = ONNXMiniLM_L6_V2()(texts)
    cos = [sum(float(a) * float(b) for a, b in zip(m, r)) / ((sum(float(a) ** 2 for a in m) ** 0.5) * (sum(float(b) ** 2 for b in r) ** 0.5)) for m, r in zip(mine, ref)]
    print(json.dumps({"texts": len(texts), "cos_min": round(min(cos), 6), "cos_mean": round(sum(cos) / len(cos), 6), "dim": emb.dim, "model": emb.model_id}))
    return 0 if min(cos) > 0.999 else 1


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", choices=("bge", "minilm"), default="bge")
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--distractors", type=int, default=180)
    ap.add_argument("--parity", action="store_true")
    a = ap.parse_args(argv)
    if a.parity:
        return parity()
    emb = load(a.model)
    tot = {"direct": 0, "paraphrase": 0}
    per = []
    for i in range(a.seeds):
        h = seed_hits(emb, f"q{i:02d}", a.distractors)
        per.append(h)
        for k in tot:
            tot[k] += h[k]
    n = a.seeds * needles.N_NEEDLES
    print(json.dumps({"model": emb.model_id, "seeds": a.seeds, "distractors": a.distractors, "needles_per_seed": needles.N_NEEDLES, "direct": f"{tot['direct']}/{n}",
                      "paraphrase": f"{tot['paraphrase']}/{n}", "direct_rate": round(tot["direct"] / n, 3), "paraphrase_rate": round(tot["paraphrase"] / n, 3),
                      "per_seed": per}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
