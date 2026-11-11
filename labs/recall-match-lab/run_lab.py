"""Recall-match lab: which retrieval rule would put the needed stored fact in front of the brain on household-style turns?

Retrieval only, offline, synthetic (fixtures.py). Never opens a database, Chroma, the network or the brain. Methods:
  (i)   production: services/zoe-data recall_every_turn._ranked (the ZOE_RECALL_EVERY_TURN relevance test, recall_gate lexical
        scoring + threshold), CALLED AS A LIBRARY on an in-memory index of the synthetic rows (auth 4, every row "speakable");
        plus the same code with the threshold lowered (cfg sweep) to ask "is it a threshold?".
  (ii)  bge-small-en-v1.5 (fastembed, CPU, local cache only) cosine top-3 above a threshold tuned on the even turns of each
        class, reported on the odd (held-out) turns and on all 60.
  (iii) standing card: the top facts by activation that fit 150 estimated tokens, always present.
  (iv)  union of (ii) and (iii).
Usage: python3 labs/recall-match-lab/run_lab.py [--json out.json]   (not -I: fastembed lives in the user site-packages)
"""
import argparse
import dataclasses
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "services", "zoe-data"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")          # never download a model
os.environ.setdefault("ZOE_RECALL_GATE", "shadow")

from fixtures import FACTS, TURNS  # noqa: E402

CARD_TOKENS = 150
TOP_K = 3


def production_picks(threshold=None):
    """recall_every_turn._ranked over the synthetic pool, top MAX_ROWS within BUDGET_TOKENS (the select() budget loop)."""
    import recall_gate
    import recall_every_turn as ret

    saved = recall_gate.PACKET
    if threshold is not None:
        recall_gate.PACKET = dataclasses.replace(saved, threshold=threshold)
    try:
        rows, df = [], {}
        for fid, text, act in FACTS:
            c = recall_gate.Cand(fid, text, {"added_ts": act}, "pool", 0, 4)
            f = recall_gate.row_feat({}, text)
            rows.append((c, f))
            for k in set(f.keys):
                df[k] = df.get(k, 0) + 1
        ix = ret._Index(time.monotonic(), rows, df)
        out = []
        for _kind, utt, _need in TURNS:
            ids, tokens = [], 0
            for c in ret._ranked(ix, utt)[: ret.MAX_ROWS * 3]:
                cost = recall_gate.est_tokens(ret._cut(c.text))
                if tokens + cost > ret.BUDGET_TOKENS:
                    continue
                ids.append(c.id)
                tokens += cost
                if len(ids) >= ret.MAX_ROWS:
                    break
            out.append(ids)
        return out
    finally:
        recall_gate.PACKET = saved


def embed_scores():
    """cosine(turn, fact) for every pair with bge-small (query prefix on turns, as fastembed's query_embed does)."""
    import numpy as np
    from fastembed import TextEmbedding

    m = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=True)
    fv = np.array(list(m.passage_embed([t for _f, t, _a in FACTS])))
    qv = np.array(list(m.query_embed([u for _k, u, _n in TURNS])))
    fv /= np.linalg.norm(fv, axis=1, keepdims=True)
    qv /= np.linalg.norm(qv, axis=1, keepdims=True)
    return qv @ fv.T


def cosine_picks(sims, thr):
    ids = [f for f, _t, _a in FACTS]
    out = []
    for row in sims:
        order = sorted(range(len(ids)), key=lambda j: -row[j])[:TOP_K]
        out.append([ids[j] for j in order if row[j] >= thr])
    return out


def card():
    import recall_gate

    chosen, used = [], 0
    for fid, text, _a in sorted(FACTS, key=lambda t: -t[2]):
        cost = recall_gate.est_tokens(text)
        if used + cost <= CARD_TOKENS:
            chosen.append(fid)
            used += cost
    return chosen, used


def score(picks, subset=None):
    """per kind: hit = an accepted fact was injected; wrong = rows injected, none accepted; any = rows injected."""
    res = {}
    for i, (kind, _u, need) in enumerate(TURNS):
        if subset is not None and i not in subset:
            continue
        r = res.setdefault(kind, {"n": 0, "hit": 0, "any": 0, "wrong": 0, "rows": 0})
        got = set(picks[i])
        r["n"] += 1
        r["any"] += bool(got)
        r["rows"] += len(got)
        r["hit"] += bool(got & need)
        r["wrong"] += bool(got) and not (got & need)
    return res


def youden(picks, subset):
    s = score(picks, subset)
    rel_n = s["direct"]["n"] + s["oblique"]["n"]
    hit = (s["direct"]["hit"] + s["oblique"]["hit"]) / rel_n
    return hit - s["irrelevant"]["any"] / s["irrelevant"]["n"]


def fmt(name, s):
    d, o, i = s["direct"], s["oblique"], s["irrelevant"]
    return (f"| {name} | {d['hit']}/{d['n']} | {o['hit']}/{o['n']} | {i['any']}/{i['n']} | "
            f"{d['wrong'] + o['wrong']}/{d['n'] + o['n']} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    by_kind = {}
    for i, (k, _u, _n) in enumerate(TURNS):
        by_kind.setdefault(k, []).append(i)
    train = {i for v in by_kind.values() for i in v[0::2]}
    test = {i for v in by_kind.values() for i in v[1::2]}

    rows, out = [], {}
    for thr, label in ((None, "(i) production as coded (threshold 2.0)"), (1.5, "(i-b) production, threshold 1.5"),
                       (0.8, "(i-c) production, threshold 0.8")):
        p = production_picks(thr)
        out[label] = {"all": score(p)}
        rows.append(fmt(label, score(p)))

    sims = embed_scores()
    grid = [round(0.40 + 0.01 * k, 2) for k in range(51)]
    best = max(grid, key=lambda t: (youden(cosine_picks(sims, t), train), t))
    cp = cosine_picks(sims, best)
    out["cosine"] = {"threshold": best, "all": score(cp), "test": score(cp, test)}
    rows.append(fmt(f"(ii) bge-small cos top-{TOP_K} >= {best} (tuned on even turns), all 60", score(cp)))
    rows.append(fmt(f"(ii) same, held-out odd turns only", score(cp, test)))

    c_ids, c_tok = card()
    cardp = [list(c_ids) for _ in TURNS]
    out["card"] = {"ids": len(c_ids), "tokens": c_tok, "all": score(cardp)}
    rows.append(fmt(f"(iii) standing card ({len(c_ids)} facts, ~{c_tok} tok, always present)", score(cardp)))
    up = [sorted(set(a) | set(b)) for a, b in zip(cp, cardp)]
    out["union"] = {"all": score(up), "test": score(up, test)}
    rows.append(fmt("(iv) union (ii)+(iii), all 60", score(up)))
    rows.append(fmt("(iv) union, held-out odd turns only", score(up, test)))

    # where the needed fact ranks by cosine, relevant turns (is it a threshold or a ranking problem?)
    ids = [f for f, _t, _a in FACTS]
    ranks = {}
    for i, (kind, _u, need) in enumerate(TURNS):
        if need:
            order = sorted(range(len(ids)), key=lambda j: -sims[i][j])
            ranks.setdefault(kind, []).append(min(order.index(ids.index(f)) for f in need) + 1)
    out["cosine_rank_of_needed"] = ranks
    rel = [max(sims[i][ids.index(f)] for f in need) for i, (_k, _u, need) in enumerate(TURNS) if need]
    irr = [float(max(sims[i])) for i, (k, _u, _n) in enumerate(TURNS) if k == "irrelevant"]
    out["cos_needed_median"], out["cos_irrelevant_top1_median"] = sorted(rel)[len(rel) // 2], sorted(irr)[len(irr) // 2]

    print("| method | direct hit | oblique hit | irrelevant turns injected | relevant turns, wrong rows only |")
    print("|---|---|---|---|---|")
    print("\n".join(rows))
    for k, v in ranks.items():
        print(f"cosine rank of the needed fact, {k}: top1 {sum(r == 1 for r in v)}/{len(v)}, top3 {sum(r <= 3 for r in v)}/{len(v)}, "
              f"top5 {sum(r <= 5 for r in v)}/{len(v)}")
    print(f"median cosine: needed fact {out['cos_needed_median']:.3f}; best fact on an irrelevant turn "
          f"{out['cos_irrelevant_top1_median']:.3f}")
    print("\ncosine threshold sweep (top-3, all 60): thr | direct hit | oblique hit | irrelevant injected")
    sweep = {}
    for t in [round(0.50 + 0.02 * k, 2) for k in range(11)]:
        s = score(cosine_picks(sims, t))
        sweep[t] = s
        print(f"{t:.2f} | {s['direct']['hit']}/20 | {s['oblique']['hit']}/20 | {s['irrelevant']['any']}/20")
    out["cosine_sweep"] = sweep
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
