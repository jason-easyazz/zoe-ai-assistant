"""scripts/maintenance/export_router_heads.py — read-only --check, all-or-nothing export.

sklearn-free: `sklearn` and `joblib` are faked with estimators rebuilt from the
committed numpy exports, so the exporter's real control flow (staging, parity,
negative control, rename) runs in the slim CI lane.

* `--check` is strictly read-only: it refuses --fixture/--report, and a plain
  check leaves every file in the models dir untouched (bytes, inode, mtime).
* The export is atomic across BOTH heads: if the second head fails parity, the
  first head's served files are byte- and inode-identical and no staging file
  is left behind. Control: when both pass, both served pairs ARE replaced.
"""
import importlib.util
import shutil
import sys
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "services" / "zoe-data" / "models"
SCRIPT = REPO / "scripts" / "maintenance" / "export_router_heads.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("export_router_heads_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def exporter(monkeypatch):
    """The script with fake sklearn/joblib; `bad` names heads whose 'sklearn'
    probabilities are skewed (so their parity check fails)."""
    mod = _load_script()
    rhn = mod.rhn
    bad: set[str] = set()

    class _Proba:
        def predict_proba(self, X):
            return self._h.predict_proba(X) + self._skew

    class LogisticRegression(_Proba):
        multi_class, solver = "deprecated", "lbfgs"

        def __init__(self, h, skew):
            self.classes_, self.coef_, self.intercept_ = h.classes_, h.coef_, h.intercept_
            self.n_features_in_, self._h, self._skew = h.n_features_in_, h, skew

    class MLPClassifier(_Proba):  # NOT a LogisticRegression (isinstance order matters)
        def __init__(self, h, skew):
            self.classes_, self.coefs_, self.intercepts_ = h.classes_, h.coefs_, h.intercepts_
            self.activation, self.out_activation_ = h.activation, h.out_activation_
            self.n_features_in_, self._h, self._skew = h.n_features_in_, h, skew

    def fake_load(path):
        name = Path(path).stem.replace("router_head_", "")
        h = rhn.load_npz(str(MODELS / f"router_head_{name}.joblib"))
        cls = MLPClassifier if name == "mlp" else LogisticRegression
        return cls(h, 1e-3 if name in bad else 0.0)

    sk = types.ModuleType("sklearn")
    sk.__version__ = "1.7.2"
    lm = types.ModuleType("sklearn.linear_model")
    lm.LogisticRegression = LogisticRegression
    nn = types.ModuleType("sklearn.neural_network")
    nn.MLPClassifier = MLPClassifier
    jl = types.ModuleType("joblib")
    jl.__version__, jl.load = "1.5.3", fake_load
    for k, v in {"sklearn": sk, "sklearn.linear_model": lm,
                 "sklearn.neural_network": nn, "joblib": jl}.items():
        monkeypatch.setitem(sys.modules, k, v)
    # no fastembed in CI: a small stand-in "corpus" (unit vectors)
    rng = np.random.default_rng(0)
    vecs = rng.standard_normal((60, 384)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    monkeypatch.setattr(mod, "corpus_vectors", lambda: ([f"u{i}" for i in range(60)], vecs))
    mod.bad = bad
    return mod


@pytest.fixture
def models(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    for name in ("logreg", "mlp"):
        for ext in (".joblib", ".npz", ".json"):
            shutil.copy(MODELS / f"router_head_{name}{ext}", d / f"router_head_{name}{ext}")
    return d


def _snapshot(d):
    return {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
            for p in sorted(d.iterdir())}


# ── --check is read-only ────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["--fixture", "--report"])
def test_check_refuses_write_flags(exporter, models, tmp_path, flag):
    target = tmp_path / "must-not-exist"
    with pytest.raises(SystemExit) as exc:
        # --corpus so --fixture is otherwise valid: only the --check guard refuses
        exporter.main(["--check", "--models-dir", str(models), "--random", "20",
                       "--corpus", flag, str(target)])
    assert exc.value.code == 2
    assert not target.exists()


def test_check_touches_nothing(exporter, models):
    before = _snapshot(models)
    assert exporter.main(["--check", "--models-dir", str(models), "--random", "20"]) == 0
    assert _snapshot(models) == before


# ── the export is all-or-nothing across both heads ──────────────────────────

def test_second_head_failing_leaves_the_first_head_untouched(exporter, models):
    exporter.bad.add("mlp")
    before = _snapshot(models)
    assert exporter.main(["--models-dir", str(models), "--random", "20"]) == 1
    assert _snapshot(models) == before  # logreg (which PASSED) included; no tmp left


def test_control_both_heads_passing_replace_both_pairs(exporter, models):
    before = _snapshot(models)
    assert exporter.main(["--models-dir", str(models), "--random", "20"]) == 0
    after = _snapshot(models)
    assert set(after) == set(before)  # no staging file left behind
    for name in ("router_head_logreg.npz", "router_head_logreg.json",
                 "router_head_mlp.npz", "router_head_mlp.json"):
        assert after[name][1:] != before[name][1:], f"{name} was not replaced"
    # the npz bytes are deterministic: a faithful re-export is byte-identical
    assert after["router_head_mlp.npz"][0] == before["router_head_mlp.npz"][0]
