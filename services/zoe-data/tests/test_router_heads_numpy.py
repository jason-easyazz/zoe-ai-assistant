"""The stage-1 router heads run on pure numpy, at sklearn parity.

`router_heads_numpy` replays sklearn's `predict_proba` for the two committed
heads from their `.npz` export, so zoe-data never imports scikit-learn/scipy
(~72 MB, ~1.2 s, ~815 modules measured 2026-09-27). What is pinned here:

* parity: the committed npz reproduces the probabilities sklearn 1.7.2 computed
  for 50 real corpus embeddings (fixture written once by
  scripts/maintenance/export_router_heads.py) to <= 1e-6 — measured 0.0;
* the check can fail: one perturbed weight goes red (negative control), and a
  tampered npz on disk is refused by the loader's sha256 check;
* drift: the npz, the fixture and the committed .joblib all name the same
  source sha256 — retraining a head without re-exporting fails here;
* the backend flag (numpy default, joblib one-release fallback, unknown -> numpy)
  and its inventory row;
* a fresh interpreter that loads BOTH heads through the real zoe-data loaders
  never imports sklearn, scipy or joblib.

sklearn-free and network-free (ci_safe).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
rhn = pytest.importorskip("router_heads_numpy")

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]
MODELS = SVC / "models"
FIXTURE = SVC / "tests" / "fixtures" / "router_heads_parity.npz"
TOL = 1e-6
HEADS = ("logreg", "mlp")


@pytest.fixture(scope="module")
def fixture():
    with np.load(FIXTURE, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _head(name):
    return rhn.load_npz(str(MODELS / f"router_head_{name}.joblib"))


def _max_abs(a, b):
    return float(np.max(np.abs(np.asarray(a, np.float64) - np.asarray(b, np.float64))))


@pytest.mark.parametrize("name", HEADS)
def test_numpy_head_matches_sklearn_probabilities(name, fixture):
    head = _head(name)
    X = fixture["vectors"]
    assert X.shape == (50, 384) and X.dtype == np.float32
    expected = fixture[f"proba_{name}"]
    assert _max_abs(head.predict_proba(X), expected) <= TOL
    # prod calls one row at a time (vec.reshape(1, -1)) — same answer
    for i in range(len(X)):
        assert _max_abs(head.predict_proba(X[i:i + 1]), expected[i:i + 1]) <= TOL
    assert (head.predict_proba(X).argmax(1) == expected.argmax(1)).all()


@pytest.mark.parametrize("name", HEADS)
def test_negative_control_one_perturbed_weight_goes_red(name, fixture):
    head = _head(name)
    if name == "logreg":
        head.coef_ = head.coef_.copy()
        head.coef_[0, 0] += 1e-2
    else:
        head.intercepts_ = [b.copy() for b in head.intercepts_]
        head.intercepts_[-1][0] += 1e-2
    assert _max_abs(head.predict_proba(fixture["vectors"]), fixture[f"proba_{name}"]) > TOL


def test_tampered_npz_is_refused_by_the_loader(tmp_path):
    for ext in (".npz", ".json"):
        shutil.copy(MODELS / f"router_head_mlp{ext}", tmp_path / f"router_head_mlp{ext}")
    good = rhn.load_npz(str(tmp_path / "router_head_mlp.joblib"))
    arrays = {f"coef_{i}": W for i, W in enumerate(good.coefs_)}
    arrays.update({f"intercept_{i}": b for i, b in enumerate(good.intercepts_)})
    arrays["intercept_1"] = arrays["intercept_1"] + np.float32(1e-3)
    arrays["classes"] = good.classes_
    np.savez(tmp_path / "router_head_mlp.npz", **arrays)
    with pytest.raises(ValueError, match="sha256"):
        rhn.load_npz(str(tmp_path / "router_head_mlp.joblib"))


@pytest.mark.parametrize("name", HEADS)
def test_export_names_the_committed_joblib(name, fixture):
    """Retrain + copy a new .joblib without re-exporting -> red here."""
    meta = json.loads((MODELS / f"router_head_{name}.json").read_text())
    joblib_sha = hashlib.sha256((MODELS / f"router_head_{name}.joblib").read_bytes()).hexdigest()
    assert meta["source_sha256"] == joblib_sha, (
        f"router_head_{name}.npz was exported from a different joblib — run "
        "scripts/maintenance/export_router_heads.py --corpus --fixture "
        "services/zoe-data/tests/fixtures/router_heads_parity.npz")
    assert str(fixture[f"source_sha256_{name}"]) == joblib_sha
    assert meta["format"] == rhn.FORMAT and meta["preprocessing"] == []


def test_head_architecture_is_what_the_numpy_backend_implements():
    lr, mlp = _head("logreg"), _head("mlp")
    assert isinstance(lr, rhn.LogRegHead) and isinstance(mlp, rhn.MLPHead)
    assert list(lr.classes_) == list(mlp.classes_) and len(lr.classes_) == 13
    assert "chat" in list(mlp.classes_)
    assert lr.coef_.shape == (13, 384) and lr.coef_.dtype == np.float64
    assert [W.shape for W in mlp.coefs_] == [(384, 256), (256, 13)]
    assert mlp.activation == "relu" and mlp.out_activation_ == "softmax"


def test_input_validation_matches_sklearn(fixture):
    head = _head("mlp")
    with pytest.raises(ValueError, match="2D"):
        head.predict_proba(fixture["vectors"][0])
    with pytest.raises(ValueError, match="features"):
        head.predict_proba(np.zeros((1, 10), dtype=np.float32))


def test_unsupported_architectures_fail_the_load_not_the_turn():
    c = np.asarray(["a", "b", "c"])
    with pytest.raises(ValueError, match="activation"):
        rhn.MLPHead(c, [np.zeros((4, 3), np.float32)], [np.zeros(3, np.float32)],
                    "logistic", "softmax")
    with pytest.raises(ValueError, match="binary"):
        rhn.LogRegHead(c[:2], np.zeros((2, 4)), np.zeros(2))


# ── backend flag ────────────────────────────────────────────────────────────

def test_backend_default_is_numpy(monkeypatch):
    monkeypatch.delenv("ZOE_ROUTER_HEADS_BACKEND", raising=False)
    assert rhn.backend() == "numpy"
    assert isinstance(rhn.load_head(str(MODELS / "router_head_mlp.joblib")), rhn.MLPHead)


def test_backend_unknown_falls_back_to_numpy(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEADS_BACKEND", "tensorflow")
    assert rhn.backend() == "numpy"


def test_backend_joblib_uses_joblib_load(monkeypatch):
    calls = []
    fake = types.ModuleType("joblib")
    fake.load = lambda p: calls.append(p) or "sklearn-estimator"
    monkeypatch.setitem(sys.modules, "joblib", fake)
    monkeypatch.setenv("ZOE_ROUTER_HEADS_BACKEND", " JobLib ")
    path = str(MODELS / "router_head_logreg.joblib")
    assert rhn.load_head(path) == "sklearn-estimator"
    assert calls == [path]


def test_numpy_backend_accepts_an_npz_path_too():
    assert rhn.sidecar_paths("/m/router_head_mlp.npz") == (
        "/m/router_head_mlp.npz", "/m/router_head_mlp.json")
    assert rhn.sidecar_paths("/m/router_head_mlp.joblib")[0] == "/m/router_head_mlp.npz"


def test_backend_flag_is_in_the_inventory():
    inv = json.loads((REPO / "docs" / "knowledge" / "flag-inventory.json").read_text())
    row = inv["flags"]["prod"]["ZOE_ROUTER_HEADS_BACKEND"]
    assert row["defaults"] == ["'numpy'"]
    assert row["readers"] == ["services/zoe-data/router_heads_numpy.py"]


# ── the point of the change: the live loaders never import sklearn ─────────

def test_live_loaders_never_import_sklearn_scipy_or_joblib(fixture):
    code = (
        "import os, sys, json\n"
        "os.environ['ZOE_ROUTER_HEAD'] = 'active'\n"
        "os.environ.pop('ZOE_ROUTER_HEADS_BACKEND', None)\n"
        "import numpy as np, semantic_router, router_two_stage\n"
        "semantic_router._ensure_head_loaded()\n"
        "mlp = router_two_stage._ensure_head()\n"
        "X = np.load(sys.argv[1])['vectors']\n"
        "p = [semantic_router._HEAD.predict_proba(X).tolist(), mlp.predict_proba(X).tolist()]\n"
        "heavy = sorted({m.split('.')[0] for m in sys.modules} & {'sklearn', 'scipy', 'joblib'})\n"
        "print(json.dumps({'types': [type(semantic_router._HEAD).__name__, type(mlp).__name__],\n"
        "                  'heavy': heavy, 'p': p}))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("ZOE_ROUTER_HEAD")}
    proc = subprocess.run([sys.executable, "-c", code, str(FIXTURE)], cwd=SVC, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["types"] == ["LogRegHead", "MLPHead"]
    assert out["heavy"] == []
    assert _max_abs(out["p"][0], fixture["proba_logreg"]) <= TOL
    assert _max_abs(out["p"][1], fixture["proba_mlp"]) <= TOL
