"""Pure-numpy inference for the two stage-1 router heads.

zoe-data loads two tiny classifier heads that run on the bge-small embedding
`semantic_router` already computes per turn:

  models/router_head_logreg.*   sklearn LogisticRegression (multinomial, 13 classes)
  models/router_head_mlp.*      sklearn MLPClassifier (384 -> 256 relu -> 13 softmax)
                                — stage 1 of the live two-stage router

Loading them with `joblib.load` imports scikit-learn + scipy (~815 modules,
~72 MB RSS, ~1.2 s — measured 2026-09-27) for what is two matrix products and
a softmax. This module replays `predict_proba` from weights exported to `.npz`
by `scripts/maintenance/export_router_heads.py`, with the SAME operations in the
SAME order and dtypes as sklearn 1.7.2, so the result is bit-for-bit identical
on this numpy (parity is pinned by tests/test_router_heads_numpy.py at <= 1e-6
and measured at 0.0).

Backend flag (one-release escape hatch):
  ZOE_ROUTER_HEADS_BACKEND = numpy (default) | joblib
    numpy   read `<head>.npz` + `<head>.json` next to the `.joblib` path; a
            CUSTOM `.joblib` path (outside models/) with NO export beside it
            falls back to joblib for that head only (warning logged) instead of
            disabling it. A shipped head with a missing export is an ERROR:
            the head is disabled, sklearn is never imported for it.
    joblib  the pre-2026-09-27 path: joblib.load the pickled sklearn estimator
            (needs scikit-learn/joblib at the training pins)

The `.joblib` files remain the TRAINING artefacts (labs/setfit-router); the
`.npz`/`.json` pair is derived from them. `load_head()` is the single entry
point both loaders use; it raises on any failure and the callers keep their
existing "load failed -> head disabled, non-fatal" handling.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

BACKEND_ENV = "ZOE_ROUTER_HEADS_BACKEND"
BACKENDS = ("numpy", "joblib")
FORMAT = "zoe-router-head/1"


def backend() -> str:
    """The configured head backend; anything unknown falls back to numpy."""
    val = (os.environ.get("ZOE_ROUTER_HEADS_BACKEND", "numpy") or "numpy").strip().lower()
    if val in BACKENDS:
        return val
    logger.warning("unknown %s=%r — using 'numpy'", BACKEND_ENV, val)
    return "numpy"


def _as_2d(X: Any, n_features: int) -> np.ndarray:
    # sklearn's validate_data(dtype="numeric") keeps float32/float64 as-is and
    # upcasts everything else to float64; it also rejects 1-D input.
    X = np.asarray(X)
    if X.dtype.kind != "f":
        X = X.astype(np.float64)
    if X.ndim != 2:
        raise ValueError(f"expected 2D array, got {X.ndim}D")
    if X.shape[1] != n_features:
        raise ValueError(f"X has {X.shape[1]} features, head expects {n_features}")
    return X


def _softmax_logreg(X: np.ndarray) -> np.ndarray:
    # sklearn.utils.extmath.softmax(copy=False), numpy branch.
    X -= np.max(X, axis=1).reshape(-1, 1)
    np.exp(X, out=X)
    X /= np.sum(X, axis=1).reshape(-1, 1)
    return X


def _softmax_mlp(X: np.ndarray) -> np.ndarray:
    # sklearn.neural_network._base.inplace_softmax.
    tmp = X - X.max(axis=1)[:, np.newaxis]
    np.exp(tmp, out=X)
    X /= X.sum(axis=1)[:, np.newaxis]
    return X


# sklearn.neural_network._base inplace activations that are pure numpy there
# too. "logistic" is deliberately absent: sklearn computes it with
# scipy.special.expit, which this module cannot reproduce bit-for-bit — an
# unsupported activation fails the load (head disabled), never mis-scores.
_HIDDEN = {
    "relu": lambda X: np.maximum(X, 0, out=X),
    "identity": lambda X: X,
    "tanh": lambda X: np.tanh(X, out=X),
}


class LogRegHead:
    """Multinomial LogisticRegression.predict_proba: softmax(X @ W.T + b)."""

    kind = "logreg"

    def __init__(self, classes: np.ndarray, coef: np.ndarray, intercept: np.ndarray):
        if coef.ndim != 2 or coef.shape[0] != len(classes) or intercept.shape != (len(classes),):
            raise ValueError(f"logreg shape mismatch: coef {coef.shape}, "
                             f"intercept {intercept.shape}, {len(classes)} classes")
        if len(classes) < 3:
            # binary sklearn logreg is one-vs-rest sigmoid on a single row — not
            # what was trained here; refuse rather than silently mis-score.
            raise ValueError("binary logreg head not supported by the numpy backend")
        self.classes_ = classes
        self.coef_ = coef
        self.intercept_ = intercept
        self.n_features_in_ = coef.shape[1]

    def predict_proba(self, X: Any) -> np.ndarray:
        X = _as_2d(X, self.n_features_in_)
        scores = X @ self.coef_.T + self.intercept_
        return _softmax_logreg(scores)


class MLPHead:
    """MLPClassifier.predict_proba: hidden layers + softmax output."""

    kind = "mlp"

    def __init__(self, classes: np.ndarray, coefs: list[np.ndarray],
                 intercepts: list[np.ndarray], activation: str, out_activation: str):
        if activation not in _HIDDEN:
            raise ValueError(f"unsupported hidden activation {activation!r}")
        if out_activation != "softmax":
            raise ValueError(f"unsupported output activation {out_activation!r}")
        if not coefs or len(coefs) != len(intercepts):
            raise ValueError("mlp needs one intercept per weight matrix")
        for i, (W, b) in enumerate(zip(coefs, intercepts)):
            if W.ndim != 2 or b.shape != (W.shape[1],):
                raise ValueError(f"layer {i}: W {W.shape} / b {b.shape} mismatch")
            if i and coefs[i - 1].shape[1] != W.shape[0]:
                raise ValueError(f"layer {i}: input {W.shape[0]} != previous output "
                                 f"{coefs[i - 1].shape[1]}")
        if coefs[-1].shape[1] != len(classes):
            raise ValueError(f"output width {coefs[-1].shape[1]} != {len(classes)} classes")
        self.classes_ = classes
        self.coefs_ = coefs
        self.intercepts_ = intercepts
        self.activation = activation
        self.out_activation_ = out_activation
        self.n_features_in_ = coefs[0].shape[0]

    def predict_proba(self, X: Any) -> np.ndarray:
        # sklearn BaseMultilayerPerceptron._forward_pass_fast, verbatim order.
        activation = _as_2d(X, self.n_features_in_)
        hidden = _HIDDEN[self.activation]
        last = len(self.coefs_) - 1
        for i, (W, b) in enumerate(zip(self.coefs_, self.intercepts_)):
            activation = activation @ W
            activation += b
            if i != last:
                hidden(activation)
        return _softmax_mlp(activation)


def sidecar_paths(path: str) -> tuple[str, str]:
    """`<stem>.npz`, `<stem>.json` for a head path given as .joblib, .npz or stem."""
    stem, ext = os.path.splitext(path)
    if ext not in (".joblib", ".npz", ".json"):
        stem = path
    return stem + ".npz", stem + ".json"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_npz(path: str) -> LogRegHead | MLPHead:
    """Load an exported head. Verifies the npz against its JSON sidecar."""
    npz_path, json_path = sidecar_paths(path)
    with open(json_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    if meta.get("format") != FORMAT:
        raise ValueError(f"{json_path}: format {meta.get('format')!r} != {FORMAT!r}")
    digest = _sha256(npz_path)
    if digest != meta.get("npz_sha256"):
        raise ValueError(f"{npz_path}: sha256 {digest[:12]} does not match "
                         f"{os.path.basename(json_path)} ({str(meta.get('npz_sha256'))[:12]}) "
                         "— re-run scripts/maintenance/export_router_heads.py")
    classes = np.asarray(meta["classes"])
    with np.load(npz_path, allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    if "classes" in arrays and list(arrays["classes"]) != list(classes):
        raise ValueError(f"{npz_path}: classes disagree with {json_path}")
    kind = meta.get("kind")
    if kind == "logreg":
        head: LogRegHead | MLPHead = LogRegHead(classes, arrays["coef"], arrays["intercept"])
    elif kind == "mlp":
        n = int(meta["n_layers"])
        head = MLPHead(classes,
                       [arrays[f"coef_{i}"] for i in range(n)],
                       [arrays[f"intercept_{i}"] for i in range(n)],
                       meta["activation"], meta["out_activation"])
    else:
        raise ValueError(f"{json_path}: unknown head kind {kind!r}")
    if head.n_features_in_ != int(meta["n_features_in"]):
        raise ValueError(f"{npz_path}: n_features_in {head.n_features_in_} != "
                         f"{meta['n_features_in']}")
    return head


# The committed heads live here; every file in it is tracked (see .gitignore).
SHIPPED_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def is_shipped(path: str) -> bool:
    """True when `path` is inside the shipped models dir (symlinks resolved)."""
    return (os.path.dirname(os.path.realpath(path))
            == os.path.realpath(SHIPPED_MODELS_DIR))


def load_head(path: str) -> Any:
    """Load a stage-1 router head through the configured backend. Raises on failure.

    `path` is the `.joblib` path the callers have always configured
    (ZOE_ROUTER_HEAD_PATH / ZOE_ROUTER_HEAD_MLP_PATH); the numpy backend reads
    the `.npz` + `.json` beside it. Either way the result exposes
    `predict_proba(X)` and `classes_` exactly as the sklearn estimator did.
    """
    if backend() == "joblib":
        return _load_joblib(path)
    npz_path, json_path = sidecar_paths(path)
    missing = [p for p in (npz_path, json_path) if not os.path.exists(p)]
    if missing and is_shipped(path):
        # The SHIPPED heads always carry their export in git. A missing file here
        # is a broken deploy/checkout, not a custom head: fail visibly (the caller
        # disables the head) and never pull sklearn into the live process for it.
        logger.error("shipped router head export missing: %s — head disabled; "
                     "restore it from git or re-run "
                     "scripts/maintenance/export_router_heads.py", ", ".join(missing))
        raise FileNotFoundError(f"shipped router head export missing: {', '.join(missing)}")
    if missing and path.endswith(".joblib") and os.path.exists(path):
        # A CUSTOM ZOE_ROUTER_HEAD_PATH / ZOE_ROUTER_HEAD_MLP_PATH (outside the
        # shipped models dir) that predates the numpy exports: keep that head
        # WORKING (the two-stage router would otherwise drop to similarity routing
        # for the life of the process) by loading it through joblib — for this
        # head only, and loudly. Only a MISSING export falls back; a
        # stale/tampered one still refuses (load_npz).
        logger.warning(
            "router head %s has no numpy export (%s missing) — loading it via joblib "
            "(imports scikit-learn). Export it with "
            "scripts/maintenance/export_router_heads.py to drop sklearn.",
            path, ", ".join(os.path.basename(p) for p in missing))
        return _load_joblib(path)
    return load_npz(path)


def _load_joblib(path: str) -> Any:
    import joblib  # deliberate: the only sklearn-importing path left

    return joblib.load(path)
