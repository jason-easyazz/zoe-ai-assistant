"""scripts/maintenance/affected_tests.py — the inner-loop test selector.

Every case runs against a small synthetic git repo laid out like this one (services/zoe-data + its tests,
tests/unit, a workflow, docs), so the selector is exercised end to end with real ``git`` and real ``pytest``
and nothing here depends on this checkout's own graph. Offline by construction: no ``origin`` remote, and a
``gh`` on PATH that records any call (there must be none).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("affected_tests", REPO / "scripts/maintenance/affected_tests.py")
at = importlib.util.module_from_spec(_spec)
sys.modules["affected_tests"] = at
_spec.loader.exec_module(at)

FILES = {
    "pytest.ini": "[pytest]\nmarkers =\n    ci_safe: slim\npythonpath = services/zoe-data\n",
    "services/zoe-data/alarms.py": "import util\n\ndef ring():\n    return util.twice(1)\n",
    "services/zoe-data/util.py": "def twice(x):\n    return 2 * x\n",
    "services/zoe-data/lists.py": "def items():\n    return []\n",
    "services/zoe-data/fixture_state.py": "STATE = {}\n",
    "services/zoe-data/registry.py": "def load():\n    from lists import items\n    return items()\n",
    "services/zoe-data/tests/conftest.py": ("import pytest\n\n@pytest.fixture(autouse=True)\ndef _reset():\n"
                                            "    import fixture_state\n    fixture_state.STATE.clear()\n    yield\n"),
    "services/zoe-data/tests/test_alarms.py": ("import pytest\nimport alarms\npytestmark = pytest.mark.ci_safe\n\n"
                                               "def test_ring():\n    assert alarms.ring() == 2\n"),
    "services/zoe-data/tests/test_lists.py": ("import pytest\nfrom lists import items\npytestmark = pytest.mark.ci_safe\n\n"
                                              "def test_items():\n    assert items() == []\n"),
    "services/zoe-data/tests/test_registry.py": ("import pytest\nimport registry\npytestmark = pytest.mark.ci_safe\n\n"
                                                 "def test_load():\n    assert registry.load() == []\n"),
    "tests/__init__.py": "",
    "tests/unit/__init__.py": "",
    "tests/unit/test_workflow_text.py": (
        "import pytest\nfrom pathlib import Path\npytestmark = pytest.mark.ci_safe\n"
        "ROOT = Path(__file__).resolve().parents[2]\n\n"
        "def test_reads_workflow():\n"
        "    assert 'pytest' in (ROOT / '.github' / 'workflows' / 'validate.yml').read_text()\n"),
    "tests/unit/test_script_runs.py": (
        "import pytest, subprocess\nfrom pathlib import Path\npytestmark = pytest.mark.ci_safe\n"
        "ROOT = Path(__file__).resolve().parents[2]\n\n"
        "def test_runs():\n"
        "    subprocess.run(['bash', str(ROOT / 'scripts/maintenance/hello.sh')], check=True)\n"),
    "scripts/maintenance/hello.sh": "#!/usr/bin/env bash\nsource \"$(dirname \"$0\")/lib_hello.sh\"\n",
    "scripts/maintenance/lib_hello.sh": "true\n",
    ".github/workflows/validate.yml": "jobs:\n  validate:\n    steps:\n      - run: pytest\n",
    "docs/guide.md": "# guide\n",
    "README.md": "# repo\n",
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feature")
    # offline: a `gh` that records any call; the selector must never reach it
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(f"#!/bin/sh\necho called >> {tmp_path / 'gh-called'}\nexit 1\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AFFECTED_TESTS_NO_CACHE", "1")
    monkeypatch.delenv("AFFECTED_TESTS_BASE", raising=False)
    yield root
    assert not (tmp_path / "gh-called").exists(), "affected_tests called gh (it must work fully offline)"


def _run(root: Path, *args: str) -> tuple[int, dict]:
    out = root.parent / "summary.json"
    rc = at.main(["--repo", str(root), "--no-lock", "--json", str(out), *args])
    return rc, json.loads(out.read_text())


def _selected(summary: dict) -> dict[str, list[str] | None]:
    return {ln["lane"]: ln["tests"] for ln in summary["lanes"]}


def _edit(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_source_change_selects_its_tests_transitively(repo):
    _edit(repo, "services/zoe-data/util.py", "def twice(x):\n    return x + x\n")
    rc, s = _run(repo, "--dry-run")
    assert rc == 0 and s["mode"] == "selected" and s["base"] == "main"
    assert _selected(s) == {"zoe-data": ["services/zoe-data/tests/test_alarms.py"]}
    why = s["lanes"][0]["why"]["services/zoe-data/tests/test_alarms.py"]
    assert "alarms.py" in why and "util.py" in why


def test_lazy_import_is_followed_to_direct_importers(repo):
    _edit(repo, "services/zoe-data/lists.py", "def items():\n    return list()\n")
    _, s = _run(repo, "--dry-run")
    assert _selected(s)["zoe-data"] == ["services/zoe-data/tests/test_lists.py",
                                        "services/zoe-data/tests/test_registry.py"]


@pytest.mark.parametrize("args", [["-n", "3"], ["-n3"], ["--numprocesses=2"], ["--dist", "load"]])
def test_xdist_passthrough_is_refused(repo, args):
    with pytest.raises(SystemExit) as exc:
        at.main(["--repo", str(repo), "--no-lock", "--dry-run", "--", *args])
    assert exc.value.code == 2


def test_lazy_import_bound_is_two_importer_levels(repo):
    """Documented precision bound (tests/AGENTS.md): a lazy import reaches LAZY_HOPS levels of importers.
    Pinned so widening or narrowing it is a deliberate, reviewed change, not drift."""
    _edit(repo, "services/zoe-data/mid.py", "import registry\n\ndef go():\n    return registry.load()\n")
    _edit(repo, "services/zoe-data/top.py", "import mid\n\ndef run():\n    return mid.go()\n")
    for name, mod, call in (("test_mid.py", "mid", "go"), ("test_top.py", "top", "run")):
        _edit(repo, f"services/zoe-data/tests/{name}", f"import pytest\nimport {mod}\npytestmark = pytest.mark.ci_safe\n\n"
                                                       f"def test_it():\n    assert {mod}.{call}() == []\n")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "chain")
    _edit(repo, "services/zoe-data/lists.py", "def items():\n    return list(())\n")
    _, s = _run(repo, "--dry-run", "HEAD")
    sel = _selected(s)["zoe-data"]
    assert "services/zoe-data/tests/test_registry.py" in sel and "services/zoe-data/tests/test_mid.py" in sel
    assert "services/zoe-data/tests/test_top.py" not in sel     # 3 levels above the lazy import: a known gap
    assert at.LAZY_HOPS == 2


def test_lazy_import_is_not_followed_into_importers_that_never_call_it(repo):
    _edit(repo, "services/zoe-data/tests/test_registry_other.py",
          "import pytest\nimport registry\npytestmark = pytest.mark.ci_safe\n\ndef test_mod():\n    assert registry\n")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "other")
    _edit(repo, "services/zoe-data/lists.py", "def items():\n    return list(())\n")
    _, s = _run(repo, "--dry-run", "HEAD")
    assert "services/zoe-data/tests/test_registry_other.py" not in _selected(s)["zoe-data"]


def test_conftest_change_falls_back_to_the_full_lane(repo):
    _edit(repo, "services/zoe-data/tests/conftest.py", FILES["services/zoe-data/tests/conftest.py"] + "\n# x\n")
    _, s = _run(repo, "--dry-run")
    lane = next(ln for ln in s["lanes"] if ln["lane"] == "zoe-data")
    assert lane["mode"] == "full" and "conftest" in lane["reason"] and lane["tests"] is None
    assert "services/zoe-data/tests" in lane["command"]


def test_module_a_conftest_uses_directly_falls_back_to_the_full_lane(repo):
    _edit(repo, "services/zoe-data/fixture_state.py", "STATE = {'x': 1}\n")
    _, s = _run(repo, "--dry-run")
    lane = next(ln for ln in s["lanes"] if ln["lane"] == "zoe-data")
    assert lane["mode"] == "full" and "fixture_state.py" in lane["reason"]


def test_schema_migration_falls_back_to_the_full_lane(repo):
    _edit(repo, "services/zoe-data/alembic/versions/0001_x.py", "revision = '0001'\n")
    _, s = _run(repo, "--dry-run")
    lane = next(ln for ln in s["lanes"] if ln["lane"] == "zoe-data")
    assert lane["mode"] == "full" and "migration" in lane["reason"]


@pytest.mark.parametrize("rel", ["pytest.ini", "services/zoe-data/requirements.txt"])
def test_config_and_dependency_changes_fall_back_to_full(repo, rel):
    _edit(repo, rel, "# changed\n")
    _, s = _run(repo, "--dry-run")
    modes = {ln["lane"]: ln["mode"] for ln in s["lanes"]}
    # every lane that loads zoe-data (tests/unit puts it on PYTHONPATH) runs full, not just the owner
    assert modes.get("zoe-data") == "full" and modes.get("tests-unit") == "full"
    if rel == "pytest.ini":
        assert s["mode"] == "full" and "pytest.ini" in s["fallback_reason"]


def test_text_read_file_selects_the_tests_that_read_it(repo):
    _edit(repo, ".github/workflows/validate.yml", FILES[".github/workflows/validate.yml"] + "# x\n")
    _, s = _run(repo, "--dry-run")
    assert _selected(s) == {"tests-unit": ["tests/unit/test_workflow_text.py"]}


def test_sourced_shell_library_selects_the_test_that_runs_the_script(repo):
    _edit(repo, "scripts/maintenance/lib_hello.sh", "true\ntrue\n")
    _, s = _run(repo, "--dry-run")
    assert _selected(s) == {"tests-unit": ["tests/unit/test_script_runs.py"]}


def test_docs_only_diff_selects_nothing_and_says_so(repo, capsys):
    _edit(repo, "docs/guide.md", "# guide v2\n")
    rc, s = _run(repo)
    assert rc == 0 and s["mode"] == "none" and s["lanes"] == []
    assert any("docs-only" in n for n in s["notes"])
    assert "docs-only" in capsys.readouterr().out


def test_unreached_code_change_is_named_and_runs_its_lane(repo):
    """Code no test reaches statically may be reached at runtime: never a green zero-test run for it."""
    _edit(repo, "scripts/maintenance/orphan.py", "X = 1\n")
    _, s = _run(repo, "--dry-run")
    assert "scripts/maintenance/orphan.py" in s["unreached"]
    lane = next(ln for ln in s["lanes"] if ln["lane"] == "tests-unit")
    assert lane["mode"] == "full" and "no test reaches it" in lane["reason"]
    _edit(repo, "services/zoe-data/orphan_mod.py", "X = 1\n")
    _, s = _run(repo, "--dry-run")
    assert {ln["lane"]: ln["mode"] for ln in s["lanes"]}["zoe-data"] == "full"
    _edit(repo, "services/omi-sync/omi_sync/spool.py", "X = 1\n")    # a service no lane owns -> tests/unit
    _, s = _run(repo, "--dry-run")
    assert {ln["lane"]: ln["mode"] for ln in s["lanes"]}["tests-unit"] == "full"


def test_unreached_data_file_is_only_named(repo):
    _edit(repo, "services/zoe-data/data/table.yml", "a: 1\n")
    _, s = _run(repo, "--dry-run")
    assert "services/zoe-data/data/table.yml" in s["unreached"] and s["mode"] == "none"


def test_unknown_file_type_falls_back_to_full(repo):
    (repo / "services/zoe-data/model.bin").write_bytes(b"\x00\x01")
    _, s = _run(repo, "--dry-run")
    assert s["mode"] == "full" and "unknown file type" in s["fallback_reason"]


def test_unresolvable_base_falls_back_to_full(repo):
    _edit(repo, "services/zoe-data/util.py", "def twice(x):\n    return x * 2\n")
    _, s = _run(repo, "--dry-run", "no-such-ref")
    assert s["mode"] == "full" and "does not resolve" in s["fallback_reason"]


def test_no_default_base_falls_back_to_full(repo):
    _git(repo, "branch", "-m", "main", "trunk")   # no origin remote, no main, no master
    _edit(repo, "services/zoe-data/util.py", "def twice(x):\n    return x * 2\n")
    _, s = _run(repo, "--dry-run")
    assert s["mode"] == "full" and s["base"] is None


def test_local_commit_as_base_and_committed_changes_count(repo):
    base = _git(repo, "rev-parse", "HEAD").strip()
    _edit(repo, "services/zoe-data/lists.py", "def items():\n    return [][:]\n")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qam", "wip")
    _, s = _run(repo, "--dry-run", base)
    assert s["merge_base"] == base
    assert "services/zoe-data/tests/test_lists.py" in _selected(s)["zoe-data"]


def test_corrupt_cache_is_rebuilt_not_trusted(repo, monkeypatch):
    monkeypatch.delenv("AFFECTED_TESTS_NO_CACHE")
    cache = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / "zoe-affected-tests-cache.json"
    cache.write_text("{not json")
    _edit(repo, "services/zoe-data/util.py", "def twice(x):\n    return x * 2\n")
    _, s = _run(repo, "--dry-run")
    assert _selected(s) == {"zoe-data": ["services/zoe-data/tests/test_alarms.py"]}
    assert json.loads(cache.read_text())["v"] == at.CACHE_VERSION


def test_stale_cache_entry_is_reparsed(repo, monkeypatch):
    monkeypatch.delenv("AFFECTED_TESTS_NO_CACHE")
    _run(repo, "--dry-run")                                   # warm the cache
    _edit(repo, "services/zoe-data/tests/test_lists.py",     # now ALSO imports util
          FILES["services/zoe-data/tests/test_lists.py"] + "import util\n")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qam", "lists uses util")
    _edit(repo, "services/zoe-data/util.py", "def twice(x):\n    return x * 2\n")
    _, s = _run(repo, "--dry-run", "HEAD")
    assert "services/zoe-data/tests/test_lists.py" in _selected(s)["zoe-data"]


def test_selector_error_fails_safe_to_full(repo, monkeypatch):
    _edit(repo, "services/zoe-data/util.py", "def twice(x):\n    return x * 2\n")
    monkeypatch.setattr(at, "select", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _, s = _run(repo, "--dry-run")
    assert s["mode"] == "full" and "selector error" in s["fallback_reason"]


@pytest.mark.parametrize("body,want", [("return x + x", 0), ("return 3 * x", 1)])
def test_runs_only_the_selection_and_propagates_pytest_rc(repo, body, want):
    _edit(repo, "services/zoe-data/util.py", f"def twice(x):\n    {body}\n")
    rc, s = _run(repo)
    assert rc == want == s["rc"]
    lane = s["lanes"][0]
    assert lane["lane"] == "zoe-data" and lane["rc"] == want and lane["duration_s"] is not None
    assert lane["tests"] == ["services/zoe-data/tests/test_alarms.py"] and "-n" not in lane["command"]
    assert "services/zoe-data/tests/test_alarms.py" in lane["command"]
    assert "services/zoe-data/tests" not in lane["command"]          # never the whole lane for a selection


def test_json_stdout_is_one_clean_document(repo, capsys):
    _edit(repo, "docs/guide.md", "# guide v3\n")
    rc = at.main(["--repo", str(repo), "--no-lock", "--dry-run", "--json", "-"])
    out = capsys.readouterr()
    assert rc == 0 and json.loads(out.out)["schema"] == at.SUMMARY_SCHEMA
    assert "[affected-tests]" in out.err


def test_lane_lock_times_out_with_tempfail(tmp_path):
    import fcntl
    lock = tmp_path / "lane.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(TimeoutError):
            with at.LaneLock(str(lock), 0):
                pass
    finally:
        os.close(fd)
    with at.LaneLock(str(lock), 0):   # free again: taken immediately
        pass


def test_lane_slot_rechecks_the_box_under_the_lock(tmp_path, monkeypatch):
    """A lander that starts during a long lock wait must still go first: busy under the lock -> release, wait."""
    import fcntl
    import types
    lock = tmp_path / "lane.lock"
    monkeypatch.setattr(at, "LOCK_PATH", str(lock))
    monkeypatch.setattr(at, "wait_for_box", lambda *a: None)
    states = iter(["a voice landing is running", None])
    monkeypatch.setattr(at, "_box_busy", lambda mem: next(states))
    released = []

    def fake_sleep(_s):   # while "waiting", the lane lock must be FREE for the lander
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            released.append(True)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    monkeypatch.setattr(at.time, "sleep", fake_sleep)
    args = types.SimpleNamespace(no_lock=False, lander_wait=3600, mem_min_mb=0, mem_wait=0, lock_timeout=5)
    with at._lane_slot(args) as slot:
        assert slot.lock is not None and slot.lock.fd is not None
    assert released == [True]


def test_nested_pytest_config_forces_the_lanes_that_load_it(repo):
    _edit(repo, "services/zoe-data/pyproject.toml", "[tool.pytest.ini_options]\n")
    _, s = _run(repo, "--dry-run")
    assert {ln["lane"]: ln["mode"] for ln in s["lanes"]}["zoe-data"] == "full"


@pytest.mark.parametrize("cmdline,hit", [
    ("bash /home/zoe/assistant/scripts/landing/land_voice_pr.sh 2367 /w fix/x pr2367", True),
    ("/bin/bash /home/zoe/assistant/scripts/landing/land_voice_pr.sh 2367", True),
    ("/usr/bin/bash scripts/landing/land_voice_pr.sh", True),
    ("bash -c until ! pgrep -f land_voice_pr.sh; do sleep 5; done", False),   # a waiter must not self-match
    ("python3 tail_land_voice_pr.sh.log", False),
])
def test_lander_pattern_is_argv_anchored(cmdline, hit):
    assert bool(re.search(at.LANDER_RE, cmdline)) is hit


def test_referenced_unknown_file_type_still_falls_back_to_full(repo):
    _edit(repo, "services/zoe-data/models/head.npz", "x")
    _edit(repo, "services/zoe-data/tests/test_lists.py",
          FILES["services/zoe-data/tests/test_lists.py"] + "HEAD = 'models/head.npz'\n")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "head")
    _edit(repo, "services/zoe-data/models/head.npz", "y")
    _, s = _run(repo, "--dry-run", "HEAD")
    assert s["mode"] == "full" and "unknown file type" in s["fallback_reason"]


def test_shell_wrapper_is_the_entrypoint(repo):
    """scripts/maintenance/affected_tests.sh is what agents and the self-evolution harness call."""
    _edit(repo, "docs/guide.md", "# guide v4\n")
    r = subprocess.run(["bash", str(REPO / "scripts/maintenance/affected_tests.sh"), "--repo", str(repo),
                        "--no-lock", "--dry-run", "--json", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["mode"] == "none"
    _edit(repo, "services/zoe-data/util.py", "def twice(x):\n    return 3 * x\n")   # a real red test
    r = subprocess.run(["bash", str(REPO / "scripts/maintenance/affected_tests.sh"), "--repo", str(repo),
                        "--no-lock", "--json", "-"], capture_output=True, text=True)
    assert r.returncode == 1 and json.loads(r.stdout)["rc"] == 1


def test_lanes_mirror_validate_workflow():
    """The lanes must stay what validate.yml runs (dir, marker, PYTHONPATH), or local selection drifts from CI."""
    wf = (REPO / ".github/workflows/validate.yml").read_text()
    job = wf.split("\n  zoe-data-py312:")[0]
    lanes = {ln.name: ln for ln in at.LANES}
    assert re.search(r"pytest services/zoe-data/tests -m ci_safe", job)
    assert "--ignore=services/zoe-data/tests/samantha_live" in job
    assert lanes["zoe-data"].marker == "ci_safe" and lanes["zoe-data"].pythonpath == ("services/zoe-data",)
    # tests/unit: repo root + zoe-data (CI also adds tools/ci for its shard plugin, which a local run does not load)
    assert re.search(r'PYTHONPATH="\$\{\{ github.workspace \}\}:\$\{\{ github.workspace \}\}/services/zoe-data[:"]', job)
    assert re.search(r"tests/unit -m ci_safe", job) and lanes["tests-unit"].pythonpath == (".", "services/zoe-data")
    assert re.search(r"services/zoe-auth/tests \\\n\s+--maxfail", job) and lanes["zoe-auth"].marker is None
    for f in lanes["ha-bridge"].only_files + lanes["people-mempalace"].only_files:
        assert f in job
    for k, v in at.LANE_ENV.items():
        assert f'{k}: "{v}"' in job
    assert not any(ln.name for ln in at.LANES if "-n" in ln.extra_args)


def test_real_repo_selection_is_sane():
    """Against THIS checkout: a leaf script selects its own test, a conftest forces its lane, docs select little."""
    files = at.repo_files(REPO)
    s = at.select(REPO, [("M", "scripts/maintenance/affected_tests.py")], files)
    assert "tests/unit/test_affected_tests.py" in s.tests.get("tests-unit", {})
    s = at.select(REPO, [("M", "services/zoe-data/tests/conftest.py")], files)
    assert "zoe-data" in s.lane_full
