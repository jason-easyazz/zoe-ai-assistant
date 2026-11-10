"""The shard ledger: the proof that the sharded `validate` gate ran every collected test exactly once.

`tools/ci/pytest_shard_ledger.py` (a pytest plugin) records what each pytest process selected and ran;
`tools/ci/shard_ledger.py verify` (run by the required `validate` aggregator) compares the shards against the full
collection. Both are exercised here for real — the plugin in a child pytest process, the verifier on its output —
and the verifier is fed every defect it exists to catch (the negative controls). Local, offline, no GitHub.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
CI_DIR = REPO / "tools" / "ci"

_spec = importlib.util.spec_from_file_location("shard_ledger", CI_DIR / "shard_ledger.py")
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)
_spec2 = importlib.util.spec_from_file_location("_pytest_shard_ledger_under_test", CI_DIR / "pytest_shard_ledger.py")
led = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(led)

_SUITE = '''
import secrets
import pytest

@pytest.mark.parametrize("tok", [secrets.token_hex(4) for _ in range(3)])   # ids differ per process
def test_random_ids(tok):
    assert tok

@pytest.mark.parametrize("n", range(7))
def test_numbers(n):
    assert n >= 0

def test_skipped():
    pytest.skip("reported in setup")

@pytest.mark.slow_marker_not_selected
def test_deselected():
    pass
'''


def _suite(tmp_path: Path) -> Path:
    d = tmp_path / "suite"
    d.mkdir()
    (d / "test_a.py").write_text(_SUITE)
    (d / "test_b.py").write_text("def test_one():\n    pass\n\ndef test_two():\n    pass\n")
    (d / "pytest.ini").write_text("[pytest]\nmarkers =\n    slow_marker_not_selected: x\n")
    return d


def _pytest(suite: Path, ledger: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTEST_SHARD_LEDGER=str(ledger),
               PYTHONPATH=os.pathsep.join(p for p in (str(CI_DIR), os.environ.get("PYTHONPATH", "")) if p))
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "pytest_shard_ledger",
         "-m", "not slow_marker_not_selected", *args],
        cwd=suite, env=env, capture_output=True, text=True, timeout=120)


def test_the_plugin_records_selection_after_marker_deselection_and_every_report(tmp_path):
    suite = _suite(tmp_path)
    full = tmp_path / "full.json"
    r = _pytest(suite, full, "--collect-only")
    assert r.returncode == 0, r.stdout + r.stderr
    led = json.loads(full.read_text())
    assert led["collect_only"] is True and led["exitstatus"] == 0
    assert len(led["selected"]) == 13, led["selected"]          # 3 + 7 + skipped + 2; the marked test is gone
    assert not any("test_deselected" in n for n in led["selected"])

    run = tmp_path / "run.json"
    r = _pytest(suite, run)
    assert r.returncode == 0, r.stdout + r.stderr
    led = json.loads(run.read_text())
    assert led["collect_only"] is False and led["exitstatus"] == 0
    assert set(led["ran"]) == set(led["selected"]), "the setup-skipped test must count as reported"


def test_the_plugin_is_inert_without_its_env(tmp_path):
    suite = _suite(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(CI_DIR))
    env.pop("PYTEST_SHARD_LEDGER", None)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "pytest_shard_ledger"],
                       cwd=suite, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("weights", [None, {"test_a.py": 50.0}, {"test_b.py": 0.0, "test_a.py": 1.0}])
def test_real_shards_partition_the_full_selection(tmp_path, weights):
    """End to end, exactly as validate.yml invokes it (same plugin, same flags), with and without weights."""
    suite = _suite(tmp_path)
    args = []
    if weights is not None:
        wpath = tmp_path / "w.json"
        wpath.write_text(json.dumps({"files": weights}))
        args = [f"--shard-weights={wpath}"]   # `=` form, exactly as validate.yml passes it
    assert _pytest(suite, tmp_path / "full.json", "--collect-only").returncode == 0
    shards = []
    for g in (1, 2, 3):
        p = tmp_path / f"shard-{g}.json"
        r = _pytest(suite, p, "--shard-count", "3", "--shard-index", str(g), *args)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"[zoe-shards] shard {g}/3" in r.stdout
        led = json.loads(p.read_text())
        assert set(led["durations"]) >= set(led["selected"]), "every selected test's duration is recorded"
        shards.append((p.name, led))
    full = json.loads((tmp_path / "full.json").read_text())
    assert sl.verify("suite", full, shards, 3) == []
    # contiguous: the shards, in index order, are the full ORDER, not just the full set
    flat = [sl.norm(n) for _, s in shards for n in s["selected"]]
    assert flat == [sl.norm(n) for n in full["selected"]]


def test_a_space_separated_weights_path_is_refused(tmp_path):
    w = tmp_path / "w.json"
    w.write_text(json.dumps({"files": {}}))
    r = _pytest(_suite(tmp_path), tmp_path / "x.json", "--shard-count", "2", "--shard-index", "1", "--shard-weights", str(w))
    assert r.returncode == 4 and "--shard-weights=PATH" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_a_bad_shard_index_is_a_usage_error(tmp_path):
    r = _pytest(_suite(tmp_path), tmp_path / "x.json", "--shard-count", "3", "--shard-index", "4")
    assert r.returncode == 4, r.stdout + r.stderr


# ── the cut itself ───────────────────────────────────────────────────────────────────────────────────────

def test_slices_are_contiguous_complete_and_balanced():
    for costs, count in (([1.0] * 10, 3), ([5.0, 1, 1, 1, 1, 1], 2), ([0.0] * 7, 3), ([1.0] * 3, 3), ([9.0, 0, 0, 0], 4)):
        b = led.slice_bounds(costs, count)
        assert len(b) == count and b[0][0] == 0 and b[-1][1] == len(costs)
        assert all(b[i][1] == b[i + 1][0] for i in range(count - 1)), b      # contiguous, no gap, no overlap
        assert all(e > s for s, e in b), (costs, b)                           # never an empty shard
    b = led.slice_bounds([1.0] * 100, 4)
    assert [e - s for s, e in b] == [25, 25, 25, 25]
    b = led.slice_bounds([30.0] + [1.0] * 90, 4)                              # one heavy test: the rest re-balance
    sums = [sum(([30.0] + [1.0] * 90)[s:e]) for s, e in b]
    assert max(sums) - min(sums) <= 2, sums


def test_estimate_spreads_a_file_weight_over_its_tests_and_defaults_the_rest():
    ids = ["a.py::t1", "a.py::t2", "b.py::t1"]
    assert led.estimate(ids, {"a.py": 10.0}, 0.5) == [5.0, 5.0, 0.5]


def test_the_committed_weights_file_is_well_formed():
    data = json.loads((CI_DIR / "shard_weights.json").read_text())
    files = data["files"]
    assert files and all(isinstance(v, (int, float)) and v >= 0 for v in files.values())
    assert all(k.endswith(".py") and "::" not in k for k in files), "weights are per FILE, never per test"


def test_weights_are_rebuilt_from_ledgers(tmp_path):
    a = {"durations": {"x.py::t1": 1.25, "x.py::t2": 0.5, "y.py::t[1]": 2.0}}
    b = {"y.py::t[2]": 3.0}                                     # a plain {nodeid: s} map is accepted too
    assert sl.file_weights([a, b]) == {"x.py": 1.8, "y.py": 5.0}
    (tmp_path / "a.json").write_text(json.dumps(a))
    out = tmp_path / "w.json"
    assert sl.main(["weights", "--out", str(out), str(tmp_path / "a.json")]) == 0
    assert json.loads(out.read_text())["files"] == {"x.py": 1.8, "y.py": 2.0}


# ── the verifier, on synthetic ledgers: every defect it exists to catch ──────────────────────────────────────

def _full(ids):
    return {"selected": list(ids), "ran": [], "collect_only": True, "exitstatus": 0}


def _shard(ids, ran=None, exitstatus=0):
    return {"selected": list(ids), "ran": list(ids if ran is None else ran), "collect_only": False,
            "exitstatus": exitstatus}


IDS = ["a.py::t1", "a.py::t2[x1]", "a.py::t2[x2]", "b.py::t3"]


def test_a_clean_partition_passes():
    assert sl.verify("l", _full(IDS), [("1", _shard(IDS[:2])), ("2", _shard(IDS[2:]))], 2) == []


def test_parametrize_ids_are_compared_by_test_and_count():
    other_ids = ["a.py::t1", "a.py::t2[y1]", "a.py::t2[y2]", "b.py::t3"]   # same tests, freshly minted ids
    assert sl.verify("l", _full(IDS), [("1", _shard(other_ids))], 1) == []
    # ...but losing one parametrized case is still caught
    assert any("ran in NO shard" in e for e in sl.verify("l", _full(IDS), [("1", _shard(other_ids[:2] + other_ids[3:]))], 1))


@pytest.mark.parametrize("case,shards,expect", [
    ("dropped", [("1", _shard(IDS[:1])), ("2", _shard(IDS[2:]))], "ran in NO shard"),
    ("duplicated", [("1", _shard(IDS[:3])), ("2", _shard(IDS[2:]))], "more often than collected"),
    ("foreign", [("1", _shard(IDS)), ("2", _shard(["c.py::zzz"]))], "more often than collected"),
    ("missing shard", [("1", _shard(IDS))], "expected 2 shard ledgers"),
    ("empty shard", [("1", _shard(IDS)), ("2", _shard([]))], "selected ZERO tests"),
    ("failed shard", [("1", _shard(IDS[:2])), ("2", _shard(IDS[2:], exitstatus=1))], "not 0"),
    ("unfinished shard", [("1", _shard(IDS[:2])), ("2", _shard(IDS[2:], exitstatus=None))], "not 0"),
    ("unreported test", [("1", _shard(IDS[:2], ran=IDS[:1])), ("2", _shard(IDS[2:]))], "never reported"),
])
def test_every_defect_is_reported(case, shards, expect):
    errs = sl.verify("l", _full(IDS), shards, 2)
    assert any(expect in e for e in errs), (case, errs)


def test_a_bad_full_collection_is_reported():
    assert any("ZERO" in e for e in sl.verify("l", _full([]), [("1", _shard(IDS))], 1))
    bad = _full(IDS)
    bad["exitstatus"] = 2
    assert any("collection error" in e for e in sl.verify("l", bad, [("1", _shard(IDS))], 1))
    not_collect = _full(IDS)
    not_collect["collect_only"] = False
    assert any("--collect-only" in e for e in sl.verify("l", not_collect, [("1", _shard(IDS))], 1))
    collect_shard = _shard(IDS)
    collect_shard["collect_only"] = True
    assert any("proves nothing ran" in e for e in sl.verify("l", _full(IDS), [("1", collect_shard)], 1))


def test_the_cli_fails_closed_on_a_missing_full_ledger(tmp_path):
    assert sl.main(["verify", "--lane", "l", "--full", str(tmp_path / "nope.json"), "--expect-shards", "1"]) == 1
    (tmp_path / "full.json").write_text(json.dumps(_full(IDS)))
    (tmp_path / "s1.json").write_text(json.dumps(_shard(IDS)))
    assert sl.main(["verify", "--lane", "l", "--full", str(tmp_path / "full.json"), "--expect-shards", "1",
                    str(tmp_path / "s1.json")]) == 0
    # a shard path that does not exist is not silently treated as a shard
    assert sl.main(["verify", "--lane", "l", "--full", str(tmp_path / "full.json"), "--expect-shards", "1",
                    str(tmp_path / "s9.json")]) == 1
