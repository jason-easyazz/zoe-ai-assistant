#!/usr/bin/env python3
"""Export the stage-1 router heads from joblib (sklearn) to numpy `.npz` + JSON.

zoe-data serves the two router heads through the pure-numpy
`services/zoe-data/router_heads_numpy.py` (ZOE_ROUTER_HEADS_BACKEND=numpy, the
default), so the live process never imports scikit-learn/scipy. The `.joblib`
files stay the TRAINING artefacts (`labs/setfit-router/train.py`); this script
derives the runtime pair from them and PROVES parity before it will write:

  models/router_head_<name>.npz    weights, original dtypes, no pickle,
                                   byte-deterministic (fixed zip timestamps)
  models/router_head_<name>.json   architecture, classes_, source joblib
                                   sha256, npz sha256, library versions

Run it wherever the TRAINING pins are installed (scikit-learn/joblib from
`labs/setfit-router/requirements.txt`; fastembed only for --corpus):

  # after retraining + copying labs/setfit-router/artifacts/head_*.joblib in:
  python3 scripts/maintenance/export_router_heads.py --corpus \\
      --fixture services/zoe-data/tests/fixtures/router_heads_parity.npz

  # verify the committed npz still matches the committed joblib, write nothing:
  python3 scripts/maintenance/export_router_heads.py --check --corpus

Parity = max |numpy - sklearn| over predict_proba on (a) the router corpus
embedded with the prod bge-small model (needle 81-case corpus + the SetFit
training set + semantic_router.ROUTES) and (b) --random unit vectors (plus the
same count of un-normalised Gaussian vectors, which drive larger logits). Any
value above --tol (1e-6) fails the run and nothing is written. A built-in
negative control perturbs one weight of a copy and REQUIRES the comparison to
go red, so a comparison that cannot fail cannot pass.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
SVC = REPO / "services" / "zoe-data"
MODELS = SVC / "models"
HEADS = ("logreg", "mlp")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # prod semantic_router default (ZOE_ROUTER_MODEL)
CORPORA = (
    REPO / "labs" / "needle-benchmark" / "corpus.jsonl",   # the frozen 81-case corpus
    REPO / "labs" / "setfit-router" / "data" / "train.jsonl",
)

sys.path.insert(0, str(SVC))
import router_heads_numpy as rhn  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """np.savez-compatible, but byte-deterministic (fixed timestamps, sorted)."""
    buf_zip = io.BytesIO()
    with zipfile.ZipFile(buf_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(arrays):
            buf = io.BytesIO()
            np.lib.format.write_array(buf, np.require(arrays[name], requirements="C"),
                                      allow_pickle=False)
            zi = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, buf.getvalue())
    path.write_bytes(buf_zip.getvalue())


def extract(est) -> tuple[dict, dict]:
    """(arrays, meta) for a fitted sklearn head. Refuses anything unproven."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier

    classes = [str(c) for c in est.classes_]
    if isinstance(est, LogisticRegression):
        ovr = est.multi_class in ("ovr", "warn") or (
            est.multi_class in ("auto", "deprecated")
            and (est.classes_.size <= 2 or est.solver == "liblinear"))
        if ovr:
            raise SystemExit("logreg head is one-vs-rest/binary — the numpy backend "
                             "implements the multinomial softmax only")
        arrays = {"coef": est.coef_, "intercept": est.intercept_}
        meta = {"kind": "logreg", "estimator": "sklearn.linear_model.LogisticRegression",
                "decision": "softmax(X @ coef.T + intercept)"}
    elif isinstance(est, MLPClassifier):
        if est.out_activation_ != "softmax":
            raise SystemExit(f"mlp out_activation_={est.out_activation_!r} unsupported")
        arrays = {}
        for i, (W, b) in enumerate(zip(est.coefs_, est.intercepts_)):
            arrays[f"coef_{i}"] = W
            arrays[f"intercept_{i}"] = b
        meta = {"kind": "mlp", "estimator": "sklearn.neural_network.MLPClassifier",
                "n_layers": len(est.coefs_), "activation": est.activation,
                "out_activation": est.out_activation_,
                "layer_shapes": [list(W.shape) for W in est.coefs_]}
    else:
        # e.g. a Pipeline with a StandardScaler: export would silently drop the
        # scaler. Extend extract() + router_heads_numpy together if that ever ships.
        raise SystemExit(f"unsupported head type {type(est).__name__}")
    arrays["classes"] = np.asarray(classes)
    meta.update({
        "classes": classes,
        "n_features_in": int(est.n_features_in_),
        "dtypes": {k: str(v.dtype) for k, v in arrays.items() if k != "classes"},
        "preprocessing": [],  # none: input is the L2-normalised bge-small embedding
        "input": f"{EMBED_MODEL} embedding, float32, L2-normalised (semantic_router)",
    })
    return arrays, meta


def corpus_vectors() -> tuple[list[str], np.ndarray]:
    texts: list[str] = []
    for path in CORPORA:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                texts.append(json.loads(line)["text"])
    import semantic_router  # ROUTES exemplars; importing does not load a model

    for utts in semantic_router.ROUTES.values():
        texts.extend(utts)
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=EMBED_MODEL)
    M = np.asarray(list(model.embed(texts)), dtype=np.float32)
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)  # == semantic_router
    return texts, M


def random_vectors(n: int, dim: int, seed: int = 20260927) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal((n, dim)).astype(np.float32)
    unit = raw / (np.linalg.norm(raw, axis=1, keepdims=True) + 1e-9)
    return {f"random_unit_{n}": unit.astype(np.float32), f"random_gauss_{n}": raw}


def max_abs(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.max(np.abs(np.asarray(a, np.float64) - np.asarray(b, np.float64))))


def compare(est, head, sets: dict[str, np.ndarray]) -> dict[str, dict]:
    out = {}
    for name, X in sets.items():
        # row-at-a-time is how prod calls it (reshape(1, -1)); batch as well
        p_sk = est.predict_proba(X)
        p_np = head.predict_proba(X)
        rows = max(max_abs(est.predict_proba(X[i:i + 1]), head.predict_proba(X[i:i + 1]))
                   for i in range(min(len(X), 200)))
        out[name] = {"n": int(len(X)), "max_abs_batch": max_abs(p_sk, p_np),
                     "max_abs_single_row": rows,
                     "argmax_agree": float(np.mean(p_sk.argmax(1) == p_np.argmax(1))),
                     "dtype_sklearn": str(p_sk.dtype), "dtype_numpy": str(p_np.dtype)}
    return out


def negative_control(est, head, X: np.ndarray, tol: float) -> float:
    """Perturb ONE weight of a copy; the comparison MUST exceed tol."""
    import copy

    bad = copy.deepcopy(head)
    if head.kind == "logreg":
        bad.coef_ = bad.coef_.copy()
        bad.coef_[0, int(np.argmax(np.abs(X).mean(0)))] += 1e-2
    else:
        bad.intercepts_ = [b.copy() for b in bad.intercepts_]
        bad.intercepts_[-1][0] += 1e-2
    diff = max_abs(est.predict_proba(X), bad.predict_proba(X))
    if not diff > tol:
        raise SystemExit(f"NEGATIVE CONTROL FAILED: a perturbed {head.kind} head still "
                         f"matched (max-abs {diff:.3g} <= {tol}) — the parity check is blind")
    return diff


_replace = os.replace  # indirection: tests inject a failure mid-publication


def _staging(dst: Path) -> Path:
    """Staging name next to `dst` (same directory, so the rename is atomic)."""
    return dst.with_name(dst.stem + ".export-tmp" + dst.suffix)


def publish(pairs: list[tuple[Path, Path]]) -> None:
    """Rename every staged file onto its destination — all of them or none.

    Each existing destination is first hard-linked (copied if links are not
    supported) to `<dst>.export-bak`, so the served file stays in place until
    its own rename. If ANY rename fails, every destination already replaced is
    restored from its backup (or removed if it did not exist before) and the
    error propagates: the served set is always the complete old one or the
    complete new one.
    """
    import shutil

    backups: list[tuple[Path, Path | None]] = []
    try:
        for tmp, dst in pairs:
            bak = None
            if dst.exists():
                bak = dst.with_name(dst.name + ".export-bak")
                bak.unlink(missing_ok=True)
                try:
                    os.link(dst, bak)
                except OSError:
                    shutil.copy2(dst, bak)
            backups.append((dst, bak))
            _replace(tmp, dst)
    except BaseException:
        for dst, bak in reversed(backups):
            if bak is not None:
                os.replace(bak, dst)
                # rename() is a no-op when bak and dst are hard links to the same
                # inode (the entry whose own rename failed): drop the leftover link
                bak.unlink(missing_ok=True)
            else:
                dst.unlink(missing_ok=True)
        raise
    for _, bak in backups:
        if bak is not None:
            bak.unlink(missing_ok=True)


def _tmp_stem(npz: Path) -> Path:
    """Staging name for an export: `<stem>.export-tmp` (+ .npz / .json)."""
    return npz.with_name(npz.stem + ".export-tmp")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models-dir", type=Path, default=MODELS)
    ap.add_argument("--heads", default=",".join(HEADS))
    ap.add_argument("--check", action="store_true",
                    help="verify the committed npz against the joblib; STRICTLY read-only "
                         "(refuses --fixture/--report)")
    ap.add_argument("--corpus", action="store_true",
                    help="also compare on the embedded router corpus (needs fastembed)")
    ap.add_argument("--random", type=int, default=1000)
    ap.add_argument("--tol", type=float, default=1e-6)
    ap.add_argument("--fixture", type=Path,
                    help="write the ci_safe parity fixture (50 corpus vectors + sklearn "
                         "probabilities); requires --corpus")
    ap.add_argument("--report", type=Path, help="write the parity report JSON here")
    args = ap.parse_args(argv)
    if args.check and (args.fixture or args.report):
        ap.error("--check is read-only: it cannot be combined with --fixture/--report")
    if args.fixture and not args.corpus:
        ap.error("--fixture needs --corpus")

    import joblib
    import sklearn

    sets: dict[str, np.ndarray] = {}
    texts: list[str] = []
    if args.corpus:
        texts, sets["corpus"] = corpus_vectors()
    report: dict = {"tol": args.tol, "versions": {
        "sklearn": sklearn.__version__, "joblib": joblib.__version__, "numpy": np.__version__},
        "heads": {}}
    fixture: dict[str, np.ndarray] = {}
    failed = False
    # ALL-OR-NOTHING: every output (both heads' npz + json, and the fixture if
    # requested) is staged next to its destination as `*.export-tmp.*` and every
    # head verified (parity + a round trip through the runtime loader) BEFORE
    # anything is published; publish() then renames them all or restores all.
    # A failing head, a failed fixture write or a failed rename leaves the served
    # set exactly as it was — a mixed pair of heads is never left behind.
    staged: list[tuple[Path, Path]] = []
    try:
        for name in args.heads.split(","):
            src = args.models_dir / f"router_head_{name}.joblib"
            npz, js = (Path(p) for p in rhn.sidecar_paths(str(src)))
            est = joblib.load(src)
            arrays, meta = extract(est)
            meta = {"format": rhn.FORMAT, **meta,
                    "source": src.name, "source_sha256": _sha256(src),
                    "exported_with": report["versions"]}
            sets.update(random_vectors(args.random, meta["n_features_in"]))
            if args.check:
                head = rhn.load_npz(str(src))
                committed = json.loads(js.read_text(encoding="utf-8"))
                if committed.get("source_sha256") != meta["source_sha256"]:
                    print(f"FAIL {name}: {js.name} was exported from a different joblib "
                          f"({committed.get('source_sha256', '')[:12]} != "
                          f"{meta['source_sha256'][:12]}) — re-export")
                    failed = True
            else:
                stem = _tmp_stem(npz)
                tmp_npz, tmp_js = stem.with_name(stem.name + ".npz"), stem.with_name(stem.name + ".json")
                staged += [(tmp_npz, npz), (tmp_js, js)]  # registered first: cleanup
                write_npz(tmp_npz, arrays)
                meta["npz_sha256"] = _sha256(tmp_npz)
                tmp_js.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                # the staged pair, through the RUNTIME loader (sha256 check included)
                head = rhn.load_npz(str(stem) + ".joblib")
            res = compare(est, head, sets)
            worst = max(max(r["max_abs_batch"], r["max_abs_single_row"]) for r in res.values())
            probe = sets.get("corpus", sets[f"random_unit_{args.random}"])
            neg = negative_control(est, head, probe, args.tol)
            report["heads"][name] = {"parity": res, "worst_max_abs": worst,
                                     "negative_control_max_abs": neg,
                                     "source_sha256": meta["source_sha256"]}
            ok = worst <= args.tol
            print(f"{'ok  ' if ok else 'FAIL'} {name}: worst max-abs {worst:.3g} "
                  f"(tol {args.tol:g}); negative control {neg:.3g}; "
                  + ", ".join(f"{k}[{v['n']}]={max(v['max_abs_batch'], v['max_abs_single_row']):.3g}"
                              for k, v in res.items()))
            failed |= not ok
            if args.fixture:
                idx = np.linspace(0, len(texts) - 1, 50).round().astype(int)
                fixture["vectors"] = sets["corpus"][idx]
                fixture["texts"] = np.asarray([texts[i] for i in idx])
                fixture[f"proba_{name}"] = est.predict_proba(sets["corpus"][idx])
                fixture[f"source_sha256_{name}"] = np.asarray(meta["source_sha256"])
        if not failed and not args.check:
            if args.fixture:
                staged.append((_staging(args.fixture), args.fixture))  # before: cleanup
                write_npz(_staging(args.fixture), fixture)
            publish(staged)
            for _, dst in staged:
                print(f"     wrote {dst}")
    finally:
        # whatever happened, no staging file survives (after a successful
        # publish they have all been renamed away already)
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
    if staged and failed:
        print("     nothing written: every head must pass before any served file is replaced")
    if args.report:
        # diagnostics only (never a served file); written on failure too, since
        # that is when it is most useful. --check refuses it (read-only).
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
