#!/usr/bin/env python3
"""train_restraint_head.py - fit the semantic half of the restraint classifier (``services/zoe-data/restraint_head.py``).

One logistic unit per sensitivity class on the L2-normalised bge-small-en-v1.5 embedding (the model ``semantic_router``
already loads), trained on the DEV split only (``scripts/perf/restraint_eval.py load_sets()["dev"]``). The held-out split is
never read here. Output: ``services/zoe-data/models/restraint_head.npz`` (W, b, thr) + ``.json`` (format, classes, dim,
embedder, n_train, train_sha, the per-class cross-validated numbers that chose the thresholds).

Threshold rule (fixed before the run, from the ledger's "0 plain false positives"): for each class, the LOWEST threshold at
which the head alone fires on at most ``--fp-budget`` of the out-of-fold PLAIN rows (default 1 %); the cross-validation is
5-fold over the dev rows, so the threshold is never chosen on a row the unit was fitted on. ``C`` is chosen once, by the mean
out-of-fold average precision across the classes.

    python3 scripts/maintenance/train_restraint_head.py            # writes the head
    python3 scripts/maintenance/train_restraint_head.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "perf"))
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

import restraint_eval  # noqa: E402
import restraint_head  # noqa: E402

CLASSES = restraint_eval.CLASSES
EMBEDDER = "BAAI/bge-small-en-v1.5"
SEED = 0


def embed(texts: list[str]) -> np.ndarray:
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name=EMBEDDER)
    M = np.asarray(list(model.embed(texts)), dtype=np.float32)
    return M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)


def fit_one(X, y, C):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=C, max_iter=2000, class_weight="balanced").fit(X, y)


def oof_probs(X, y, C, folds=5):
    from sklearn.model_selection import KFold
    p = np.zeros(len(y), dtype=np.float64)
    for tr, te in KFold(folds, shuffle=True, random_state=SEED).split(X):
        if y[tr].sum() == 0:
            continue
        p[te] = fit_one(X[tr], y[tr], C).predict_proba(X[te])[:, 1]
    return p


def pick_threshold(p, y, plain, budget):
    """Lowest threshold whose out-of-fold fire rate on plain rows is <= budget; recall at it."""
    for t in np.arange(0.05, 0.9951, 0.005):
        if (p[plain] >= t).mean() <= budget:
            return float(t), float((p[y == 1] >= t).mean())
    return 0.995, float((p[y == 1] >= 0.995).mean())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fp-budget", type=float, default=0.01)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dev = restraint_eval.load_sets()["dev"]
    texts = [t for _w, t in dev]
    X = embed(texts)
    Y = np.array([[1 if c in want else 0 for c in CLASSES] for want, _t in dev])
    plain = Y.sum(axis=1) == 0
    print(f"dev rows {len(dev)}  plain {int(plain.sum())}  per class " +
          ", ".join(f"{c}={int(Y[:, i].sum())}" for i, c in enumerate(CLASSES)))

    from sklearn.metrics import average_precision_score
    best = None
    for C in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0):
        ap_ = np.mean([average_precision_score(Y[:, i], oof_probs(X, Y[:, i], C)) for i in range(len(CLASSES))])
        print(f"  C={C:<5} mean OOF average precision {ap_:.4f}")
        if best is None or ap_ > best[1]:
            best = (C, ap_)
    C = best[0]
    print(f"C = {C}")

    W, b, thr, cv = [], [], [], {}
    for i, c in enumerate(CLASSES):
        p = oof_probs(X, Y[:, i], C)
        t, rec = pick_threshold(p, Y[:, i], plain, args.fp_budget)
        m = fit_one(X, Y[:, i], C)
        W.append(m.coef_[0]); b.append(m.intercept_[0]); thr.append(t)
        cv[c] = {"threshold": round(t, 3), "oof_recall": round(rec, 3), "oof_plain_fire": round(float((p[plain] >= t).mean()), 4)}
        print(f"  {c:<16} thr {t:.3f}  OOF recall {rec:.3f}  OOF plain fire {cv[c]['oof_plain_fire']:.4f}")

    if args.dry_run:
        return 0
    out = restraint_head.HEAD_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, W=np.asarray(W, dtype=np.float32), b=np.asarray(b, dtype=np.float32), thr=np.asarray(thr, dtype=np.float32))
    sha = hashlib.sha256("\n".join(sorted(texts)).encode("utf-8")).hexdigest()
    meta = {"format": restraint_head.FORMAT, "classes": list(CLASSES), "dim": int(X.shape[1]), "embedder": EMBEDDER,
            "n_train": len(texts), "train_sha": sha, "C": C, "fp_budget": args.fp_budget, "cv": cv}
    restraint_head.META_PATH.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} and {restraint_head.META_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
