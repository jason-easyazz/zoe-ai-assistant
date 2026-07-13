#!/usr/bin/env python3
"""Train classifier heads on frozen bge-small embeddings (CPU only).

Embeddings: BAAI/bge-small-en-v1.5 via fastembed — the EXACT model + runtime
semantic_router.py uses in prod (ZOE_ROUTER_MODEL default), so in production
the head consumes the embedding already computed per turn (≈0 added embed cost).

Heads trained + saved:
  - logreg: sklearn LogisticRegression on the frozen 384-d embedding
  - mlp:    sklearn MLPClassifier (1x256 hidden) on the same embedding

Deliberately NOT fine-tuning the embedding body (true SetFit): a fine-tuned
body could not reuse the per-turn prod embedding, forfeiting the ~0-latency
premise. See README.

Usage: python3 train.py   -> artifacts/head_logreg.joblib, head_mlp.joblib
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import numpy as np

HERE = Path(__file__).parent
MODEL_NAME = "BAAI/bge-small-en-v1.5"  # pinned: prod semantic_router default


def embed(texts: list[str]) -> np.ndarray:
    from fastembed import TextEmbedding
    m = TextEmbedding(model_name=MODEL_NAME)
    M = np.asarray(list(m.embed(texts)), dtype=np.float32)
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    return M


def main():
    rows = [json.loads(l) for l in (HERE / "data/train.jsonl").read_text().splitlines() if l.strip()]
    X_txt = [r["text"] for r in rows]
    y = np.asarray([r["label"] for r in rows])
    print(f"train rows: {len(rows)}")
    t0 = time.time()
    X = embed(X_txt)
    print(f"embedded in {time.time()-t0:.1f}s")

    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
    from sklearn.neural_network import MLPClassifier

    heads = {
        "logreg": LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced"),
        # early_stopping=False: sklearn 1.7's early-stopping scorer calls
        # np.isnan on string-label predictions and crashes; fixed budget instead.
        "mlp": MLPClassifier(hidden_layer_sizes=(256,), max_iter=400,
                             early_stopping=False, random_state=0),
    }
    (HERE / "artifacts").mkdir(exist_ok=True)
    for name, clf in heads.items():
        cv = cross_val_score(clf, X, y, cv=5)
        clf.fit(X, y)
        path = HERE / f"artifacts/head_{name}.joblib"
        joblib.dump(clf, path, compress=3)
        kb = path.stat().st_size / 1024
        print(f"{name}: 5-fold CV acc {cv.mean():.3f} ± {cv.std():.3f}; saved {path.name} ({kb:.0f} KB)")


if __name__ == "__main__":
    main()
