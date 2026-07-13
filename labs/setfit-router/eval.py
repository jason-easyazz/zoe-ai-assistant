#!/usr/bin/env python3
"""Evaluate trained heads on the held-out 81-case needle-benchmark corpus.

Metrics (domain-level: predicted domain must equal the corpus `domain` field;
chat cases must predict `chat`):
  - overall accuracy
  - paraphrase-slice accuracy (style == paraphrase)
  - canonical-slice accuracy
  - chat false-positive rate (chat turns routed to any tool domain)
  - head-only latency per decision (embedding excluded: prod already computes it)

Also sweeps a confidence threshold: below threshold the head abstains ->
falls through to the brain (counted correct for chat, "no fast-tier win"
for tool turns; reported as coverage).

Usage: python3 eval.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import numpy as np

from train import embed

HERE = Path(__file__).parent


def main():
    rows = [json.loads(l) for l in (HERE / "eval/needle_corpus.jsonl").read_text().splitlines() if l.strip()]
    X = embed([r["text"] for r in rows])
    y = np.asarray([r["domain"] for r in rows])
    styles = np.asarray([r["style"] for r in rows])

    results = {}
    for name in ("logreg", "mlp"):
        clf = joblib.load(HERE / f"artifacts/head_{name}.joblib")
        # head-only latency
        t0 = time.perf_counter()
        N = 200
        for _ in range(N):
            clf.predict_proba(X[:1])
        lat_ms = (time.perf_counter() - t0) / N * 1000

        proba = clf.predict_proba(X)
        classes = clf.classes_
        pred = classes[proba.argmax(1)]
        conf = proba.max(1)

        def acc(mask):
            return float((pred[mask] == y[mask]).mean()) if mask.any() else None

        chat_mask = y == "chat"
        res = {
            "overall_acc": acc(np.ones(len(y), bool)),
            "canonical_acc": acc(styles == "canonical"),
            "paraphrase_acc": acc(styles == "paraphrase"),
            "chat_fp_rate": float((pred[chat_mask] != "chat").mean()),
            "head_latency_ms": round(lat_ms, 3),
            "errors": [
                {"text": r["text"], "gold": g, "pred": p, "conf": round(float(c), 3)}
                for r, g, p, c in zip(rows, y, pred, conf) if p != g
            ],
            "threshold_sweep": [],
        }
        for thr in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
            fire = conf >= thr
            routed = np.where(fire, pred, "chat")  # abstain -> brain/chat
            res["threshold_sweep"].append({
                "thr": thr,
                "overall_acc": float((routed == y).mean()),
                "paraphrase_acc": float((routed[styles == "paraphrase"] == y[styles == "paraphrase"]).mean()),
                "chat_fp_rate": float((routed[chat_mask] != "chat").mean()),
                "tool_coverage": float(fire[~chat_mask].mean()),  # tool turns the head decides
            })
        results[name] = res
        print(f"\n== {name} == overall {res['overall_acc']:.1%} | canonical {res['canonical_acc']:.1%} | "
              f"paraphrase {res['paraphrase_acc']:.1%} | chat-FP {res['chat_fp_rate']:.1%} | "
              f"head {res['head_latency_ms']}ms")
        for s in res["threshold_sweep"]:
            print(f"  thr={s['thr']:.1f} overall {s['overall_acc']:.1%} para {s['paraphrase_acc']:.1%} "
                  f"chatFP {s['chat_fp_rate']:.1%} cover {s['tool_coverage']:.1%}")

    (HERE / "results/eval.json").write_text(json.dumps(results, indent=1))
    print("\nwrote results/eval.json")


if __name__ == "__main__":
    main()
