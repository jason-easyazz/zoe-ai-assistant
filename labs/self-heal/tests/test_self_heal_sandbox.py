"""v2: the gate sandbox (a hostile 'engine-written' test must not escape), junit-proven heals,
collection errors, one heal in flight per fault, orphaned pushed branches, the landing write-ahead
intent, and binaries resolved from a trusted PATH. Fake engine, throwaway repos, no network.
The hostile limits are deliberately tiny (tens of MB, a handful of processes)."""
from __future__ import annotations

import json
import os
import pwd
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from conftest import TEST, FakeEngine, fix_calc, make_cfg, seed_repo, sh, stub_bin, wait_for
from selfheal import binaries, gitops, sandbox
from selfheal.config import Config
from selfheal.detectors import Ticket, collection_errors, node_passed
from selfheal.gates import run_gates
from test_self_heal import NOON, audit, runner, state

BWRAP = sandbox.best_backend()[0] == "bwrap"
needs_bwrap = pytest.mark.skipif(not BWRAP, reason="no working bwrap: the loop refuses local/full here")


def _tree(root: Path, skip: Path) -> dict:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
            if p.is_file() and skip not in p.parents}


HOSTILE = r'''
import json, os, socket, time

def must(c):
    assert c

def test_escape():
    got = {}
    def attempt(name, fn):
        try:
            fn(); got[name] = True
        except BaseException:
            got[name] = False
    attempt("read_secret", lambda: open(%(secret)r).read())
    attempt("see_real_home", lambda: must(os.path.isdir(%(home)r + "/.ssh") or os.path.isdir(%(home)r + "/.zoe")))
    attempt("see_live_checkout", lambda: must(os.path.isdir("/home/zoe/assistant/.git")))
    attempt("read_fake_live", lambda: open(%(live)r + "/calc.py").read())
    attempt("write_outside", lambda: open(%(outside)r + "/pwned", "w").write("x"))
    attempt("write_fake_home", lambda: open(%(fakehome)r + "/pwned", "w").write("x"))
    attempt("write_usr", lambda: open("/usr/pwned-selfheal", "w").write("x"))
    attempt("env_token", lambda: os.environ["SELFHEAL_PLANTED"])
    def net():
        s = socket.socket(); s.settimeout(2); s.connect(("127.0.0.1", %(port)d))
    attempt("network", net)
    attempt("memory", lambda: bytearray(60 << 20))
    def fork():
        for _ in range(12):
            if os.fork() == 0:
                time.sleep(2); os._exit(0)
    attempt("fork", fork)
    attempt("write_host_tmp", lambda: open("/tmp/selfheal-%(token)s", "w").write("x"))
    tmpdir = os.environ["TMPDIR"]
    open(os.path.join(tmpdir, "scratch.bin"), "wb").write(b"0" * (1 << 20))   # TMPDIR works (in the worktree)
    open("tmpdir.json", "w").write(json.dumps([tmpdir, os.path.getsize(os.path.join(tmpdir, "scratch.bin"))]))
    open("escape.json", "w").write(json.dumps(got))
    must(all(got.values()))   # the attacker's success criterion: contained = this test FAILS
'''


def _gate_cfg(s, **kw):
    cfg = s.cfg
    cfg.sandbox_mem_mb, cfg.sandbox_procs = 80, 8
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


@needs_bwrap
def test_hostile_gate_is_contained(local_scratch, monkeypatch):
    s, tmp = local_scratch, local_scratch.root
    fakehome = tmp / "fakehome"
    (fakehome / ".ssh").mkdir(parents=True)
    (fakehome / ".ssh" / "id_test").write_text("invented-secret-material")
    (tmp / "outside").mkdir()
    monkeypatch.setenv("HOME", str(fakehome))
    monkeypatch.setenv("SELFHEAL_PLANTED", "invented-token")
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    lst.setblocking(False)
    token = uuid.uuid4().hex[:12]
    wt = tmp / "worktrees" / "heal-hostile"
    (wt / "tests").mkdir(parents=True)
    (wt / "tests" / "test_hostile.py").write_text(HOSTILE % dict(
        secret=str(fakehome / ".ssh" / "id_test"), home=real_home, live=str(s.live), outside=str(tmp / "outside"),
        fakehome=str(fakehome), port=lst.getsockname()[1], token=token))
    before = _tree(tmp, wt)
    t = Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s", evidence={}, repro_cmd=[])
    res = run_gates(_gate_cfg(s), ["relevant_pytest"], str(wt), ["tests/test_hostile.py"], t)
    got = json.loads((wt / "escape.json").read_text())
    assert set(got) == {"read_secret", "see_real_home", "see_live_checkout", "read_fake_live", "write_outside",
                        "write_fake_home", "write_usr", "env_token", "network", "memory", "fork", "write_host_tmp"}
    assert not any(got.values()), got                                   # every attempt failed inside
    tmpdir, size = json.loads((wt / "tmpdir.json").read_text())
    assert tmpdir == str((wt / sandbox.TMP_NAME).resolve()) and size == 1 << 20   # scratch lives IN the worktree
    assert not (wt / sandbox.TMP_NAME).exists()                         # and is gone after the run
    assert not Path(f"/tmp/selfheal-{token}").exists()                  # the host /tmp untouched
    assert res == [{"gate": "relevant_pytest", "status": "fail", "detail": "files=1,rc=1,failed=1"}]
    assert _tree(tmp, wt) == before                                     # nothing outside the worktree changed
    with pytest.raises(BlockingIOError):
        lst.accept()                                                    # no connection reached the host


@needs_bwrap
def test_gate_timeout_kills_the_whole_group(local_scratch):
    """A test that sleeps past the timeout and leaves a setsid'd, double-forked grandchild."""
    s, token = local_scratch, uuid.uuid4().hex[:12]
    wt = s.root / "worktrees" / "heal-hang"
    (wt / "tests").mkdir(parents=True)
    name = f"tests/test_hang_{token}.py"
    (wt / name).write_text(
        "import os, time\n\ndef test_hang():\n    if os.fork() == 0:\n        os.setsid()\n"
        "        if os.fork() == 0:\n            time.sleep(5)\n            open('survivor', 'w').write('x')\n"
        "            os._exit(0)\n        os._exit(0)\n    time.sleep(60)\n")
    t = Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s", evidence={}, repro_cmd=[])
    t0 = time.monotonic()
    res = run_gates(_gate_cfg(s, gate_timeout_s=2), ["relevant_pytest"], str(wt), [name], t)
    assert res[0]["status"] == "fail" and res[0]["detail"].endswith("timeout")
    assert time.monotonic() - t0 < 15
    assert wait_for(lambda: subprocess.run(["pgrep", "-f", token], capture_output=True).returncode == 1, 5)
    time.sleep(5)
    assert not (wt / "survivor").exists()                              # the grandchild died with the sandbox


def test_no_sandbox_refuses_local_and_full_but_dry_plans(local_scratch, monkeypatch):
    monkeypatch.setattr(sandbox, "best_backend", lambda: ("none", "user_namespaces_unavailable"))
    for dispatch in ("local", "full"):
        cfg = local_scratch.cfg
        cfg.dispatch = dispatch
        eng = FakeEngine(fix_calc)
        out = runner(cfg, eng).run_once()
        assert (out["outcome"], out["rule"]) == ("refused", "sandbox_unavailable") and eng.calls == 0
    out = runner(cfg, FakeEngine(fix_calc)).run_once(junit=str(_junit_file(local_scratch.root)))   # detector skipped
    assert out == {"outcome": "refused", "rule": "sandbox_unavailable", "why": "user_namespaces_unavailable"}
    cfg.dispatch = "dry"
    assert runner(cfg).run_once()["outcome"] == "planned"
    assert any(r.get("sandbox") == "none:user_namespaces_unavailable" for r in audit(cfg))


def _junit_file(tmp: Path) -> Path:
    p = tmp / "given.xml"
    p.write_text(_xml('<failure message="m"/>'))
    return p


@needs_bwrap
@pytest.mark.parametrize("bad", [{"sandbox": "none"}, {"sandbox": "firejail"}, {"sandbox_mem_mb": 4096},
                                 {"sandbox_procs": 1000}, {"sandbox_network": True},
                                 {"sandbox_writable": ["/home"]}])
def test_config_cannot_weaken_the_sandbox(tmp_path, bad):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"state_dir": str(tmp_path / "s"), **bad}))
    with pytest.raises(ValueError):
        Config.load(str(p))


# --- (a) fixed = the reproduction node PASSED ----------------------------------------------

def _xml(child: str) -> str:
    return f'<testsuites><testsuite><testcase classname="tests.test_calc" name="test_add">{child}</testcase></testsuite></testsuites>'


@pytest.mark.parametrize("child,want", [("", "passed"), ('<skipped message="s"/>', "skipped"),
                                        ('<skipped type="pytest.xfail" message="x"/>', "skipped"),
                                        ('<failure message="m"/>', "failure"), ('<error message="e"/>', "error")])
def test_node_passed_reads_the_node(child, want):
    assert node_passed(_xml(child), "tests/test_calc.py::test_add") == want
    assert node_passed(_xml(child), "tests/test_calc.py::test_other") == "not_run"   # deselected / no tests ran


def test_exit_zero_with_the_node_skipped_is_not_healed(local_scratch):
    """The code under test skips only for the ORIGINAL test's inputs, and the heal's copy of the test
    uses other inputs: the gate sees the node pass, the verify (original file) exits 0 with it SKIPPED."""
    def skip_it(wt):
        (wt / "calc.py").write_text("def add(a, b):\n    if (a, b) == (2, 3):\n        import pytest\n"
                                    "        pytest.skip('x')\n    return a + b\n")
        p = wt / "tests" / "test_calc.py"
        p.write_text(p.read_text().replace("add(2, 3)", "add(3, 2)"))
    out = runner(local_scratch.cfg, FakeEngine(skip_it)).run_once()
    assert out["outcome"] == "not_healed", out
    assert out["verify"].startswith("rc=0") and out["verify"].endswith("node=skipped")


@pytest.mark.parametrize("edit,rule", [
    (lambda wt: (wt / "tests" / "test_calc.py").write_text(
        "import pytest\nfrom calc import add\n\n\n@pytest.mark.skip\ndef test_add():\n    assert add(2, 3) == 5\n"),
     "test_weakened"),
    (lambda wt: (wt / "tests" / "test_extra.py").write_text("import pytest\npytest.skip('x', allow_module_level=True)\n"),
     "test_weakened"),
    (lambda wt: (wt / "conftest.py").write_text("collect_ignore = ['tests']\n"), "forbidden_path"),
    (lambda wt: (wt / "pytest.ini").write_text("[pytest]\naddopts = -k nothing\n"), "forbidden_path"),
])
def test_test_weakening_is_forbidden(local_scratch, edit, rule):
    out = runner(local_scratch.cfg, FakeEngine(lambda wt: (fix_calc(wt), edit(wt)))).run_once()
    assert out["outcome"] == "discarded" and out["why"] == rule, out


# --- (b) collection errors -----------------------------------------------------------------

@needs_bwrap
def test_collection_error_is_recorded_not_healed(tmp_path):
    live = tmp_path / "live"
    seed_repo(live, test_text="from calc import add\n\ndef test_add(:\n")   # does not even parse
    repo = tmp_path / "repo.git"
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    cfg = make_cfg(tmp_path, repo=repo, live=live)
    eng = FakeEngine(fix_calc)
    out = runner(cfg, eng).run_once()
    assert out == {"outcome": "collection_error", "files": ["tests/test_calc.py"]}
    assert eng.calls == 0 and not (Path(cfg.state_dir) / "state.json").exists()
    row = [r for r in audit(cfg) if r["step"] == "detect"][-1]
    assert row["outcome"] == "cannot_reproduce" and row["fault_class"] == "collection_error"


def test_collection_errors_parse():
    xml = ('<testsuites><testsuite><testcase classname="" name="pkg.tests.test_x">'
           '<error message="collection failure">tb</error></testcase></testsuite></testsuites>')
    assert collection_errors(xml) == ["pkg/tests/test_x.py"]
    assert collection_errors(_xml('<error message="ImportError"/>')) == []


# --- (c) one heal in flight per fault --------------------------------------------------------

def test_unlanded_local_heal_branch_blocks_a_second_heal(local_scratch):
    cfg = local_scratch.cfg
    assert runner(cfg).run_once()["outcome"] == "healed_local"
    eng = FakeEngine(fix_calc)
    out = runner(cfg, eng).run_once()
    assert out == {"outcome": "all_capped", "skipped": ["heal_open"]} and eng.calls == 0
    for b in gitops.heal_branches(cfg.repo):            # the owner deletes the branch: the fault is free again
        gitops.remove_worktree(cfg.repo, str(Path(cfg.worktree_root) / f"heal-{b.split('/')[1]}"), branch=b)
    assert runner(cfg).run_once()["outcome"] == "healed_local"


def test_open_pr_blocks_and_a_closed_pr_releases(local_scratch, monkeypatch):
    cfg = local_scratch.cfg
    cfg.dispatch = "dry"
    tid = runner(cfg).run_once()["ticket"]
    st = state(cfg)
    st["tickets"][tid].update(status="pr_open", pr_url="https://example.invalid/pull/1")
    (Path(cfg.state_dir) / "state.json").write_text(json.dumps(st))
    assert runner(cfg).run_once() == {"outcome": "all_capped", "skipped": ["heal_open"]}
    r = runner(cfg)
    monkeypatch.setattr(r.landing, "merged_commit", lambda url: False, raising=False)
    assert r.verify_pending()[0]["outcome"] == "pr_closed"
    assert runner(cfg).run_once()["outcome"] == "planned"


# --- (e) a pushed branch with no PR after a late interrupt -----------------------------------

def _gh(scratch, prs: str):
    return stub_bin(scratch.root, "gh", f'if [ "$1 $2" = "pr list" ]; then echo \'{prs}\'; exit 0; fi\n'
                    'echo https://example.invalid/pull/7\n')


@pytest.mark.parametrize("prs,want", [("[]", "deleted"), ('[{"url": "https://example.invalid/pull/9"}]', "pr")])
def test_orphan_pushed_branch_is_cleaned_up_next_run(github_scratch, monkeypatch, prs, want):
    s, cfg = github_scratch, github_scratch.cfg
    cfg.dispatch = "full"
    monkeypatch.setenv("PATH", f"{_gh(s, prs)}:{os.environ['PATH']}")
    origin = str(s.root / "origin.git")
    r = runner(cfg)
    real = r.checkpoint

    def late(name):
        if name == "pr":
            from selfheal.runner import Interrupted
            raise Interrupted(name, "kill_switch")
        real(name)
    r.checkpoint = late
    out = r.run_once()
    assert out["outcome"] == "interrupted"
    branch = f"self-heal/{out['ticket']}"
    assert sh("git", "-C", origin, "branch", "--list", branch)              # left on origin
    sh("git", "-C", origin, "branch", "self-heal/not-ours", "main")
    runner(cfg, now=NOON.replace(hour=13)).run_once()   # a later run: a new ticket id
    rec = state(cfg)["tickets"][out["ticket"]]
    if want == "deleted":
        assert not sh("git", "-C", origin, "branch", "--list", branch)
        assert rec["status"] == "interrupted" and rec["pushed_branch"] is None
    else:
        assert sh("git", "-C", origin, "branch", "--list", branch)
        assert rec["status"] == "pr_open" and rec["pr_url"].endswith("/pull/9")
    assert sh("git", "-C", origin, "branch", "--list", "self-heal/not-ours")   # never someone else's
    assert any(a["step"] == "recover" and a["outcome"] == want for a in audit(cfg))


# --- (f) an interrupt DURING the rollback ----------------------------------------------------

def test_interrupt_during_rollback_is_rolled_back_next_run(local_scratch, monkeypatch):
    cfg = local_scratch.cfg
    cfg.dispatch, cfg.health_checks = "full", [["false"]]
    tip = gitops.rev(cfg.repo, "refs/heads/main")
    real = gitops.git

    def ctrl_c_on_restore(repo, *args, **kw):
        if args[:1] == ("update-ref",) and len(args) == 4 and args[2] == tip:
            raise KeyboardInterrupt
        return real(repo, *args, **kw)
    monkeypatch.setattr(gitops, "git", ctrl_c_on_restore)
    with pytest.raises(KeyboardInterrupt):
        runner(cfg).run_once()
    assert gitops.rev(cfg.repo, "refs/heads/main") != tip                      # left advanced
    rec = next(iter(state(cfg)["tickets"].values()))
    assert rec["landing_intent"]["from"] == tip
    monkeypatch.setattr(gitops, "git", real)
    runner(cfg).run_once()
    assert gitops.rev(cfg.repo, "refs/heads/main") == tip                      # restored by the next run
    rec = next(iter(state(cfg)["tickets"].values()))
    assert rec["landing_intent"] is None and rec["status"] == "rolled_back"
    assert next(iter(state(cfg)["faults"].values()))["parked"] is True
    runner(cfg).run_once()                                                     # idempotent: nothing more to do
    assert gitops.rev(cfg.repo, "refs/heads/main") == tip


# --- (g) binaries from a trusted PATH ------------------------------------------------------

@pytest.fixture
def real_binary_rules(monkeypatch):
    monkeypatch.setattr(binaries, "trusted_path", lambda: binaries.TRUSTED_PATH)
    monkeypatch.setattr(binaries, "TEMP_ROOTS", ("/tmp", "/var/tmp", "/dev/shm"))


def test_binaries_resolve_from_the_trusted_path_and_are_audited(local_scratch, monkeypatch, real_binary_rules):
    shadow = stub_bin(local_scratch.root, "git", "exit 0\n")
    monkeypatch.setenv("PATH", f"{shadow}:{os.environ['PATH']}")             # a hostile inherited PATH
    cfg = local_scratch.cfg
    cfg.dispatch = "dry"
    r = runner(cfg)
    assert r.bins["git"] in ("/usr/bin/git", "/usr/local/bin/git", "/bin/git")
    assert all(os.path.isabs(p) for p in r.bins.values())
    r.run_once()
    row = [a for a in audit(cfg) if a["step"] == "preflight" and a["outcome"] == "bins"][0]
    assert f"git={r.bins['git']}" in row["bins"] and row["sandbox"].startswith(sandbox.best_backend()[0])


@pytest.mark.parametrize("where", ["temp", "worktree_root", "state_dir", "group_writable"])
def test_unsafe_binary_paths_are_refused(local_scratch, monkeypatch, real_binary_rules, where):
    cfg = local_scratch.cfg
    if where == "temp":
        d = local_scratch.root / "bin"
    else:
        monkeypatch.setattr(binaries, "TEMP_ROOTS", ())
        d = {"worktree_root": Path(cfg.worktree_root), "state_dir": Path(cfg.state_dir),
             "group_writable": local_scratch.root / "bin"}[where] / "b"
    d.mkdir(parents=True, exist_ok=True)
    fake = d / "codex"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o775 if where == "group_writable" else 0o755)
    cfg.codex_bin = str(fake)
    out = runner(cfg).run_once()
    assert out == {"outcome": "refused", "rule": "unsafe_binary"}


@needs_bwrap
def test_worktree_growth_cap(local_scratch):
    """Many small files or one big one: the worktree may not grow past `sandbox_disk_mb` per step."""
    s = local_scratch
    wt = s.root / "worktrees" / "heal-fill"
    (wt / "tests").mkdir(parents=True)
    (wt / "tests" / "test_fill.py").write_text(
        "import os\n\ndef test_fill():\n    for i in range(30):\n"
        "        open(os.path.join(os.environ['TMPDIR'], f'f{i}'), 'wb').write(b'0' * (100 << 10))\n")
    t = Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s", evidence={}, repro_cmd=[])
    res = run_gates(_gate_cfg(s, sandbox_disk_mb=2), ["relevant_pytest"], str(wt), ["tests/test_fill.py"], t)
    assert res[0]["status"] == "fail" and res[0]["detail"].endswith("worktree_quota")
    assert not (wt / sandbox.TMP_NAME).exists()


def test_disk_cap_may_only_be_lowered(tmp_path):
    with pytest.raises(ValueError):
        Config(state_dir=str(tmp_path), sandbox_disk_mb=1024).validate()


# --- review round: a rollback that is not VERIFIED keeps the intent and stops everything ------

TWO = TEST + "\n\ndef test_two():\n    assert add(1, 1) == 2\n"


def test_unverified_rollback_is_stuck_until_a_retry_verifies(tmp_path, monkeypatch):
    live = tmp_path / "live"
    seed_repo(live, test_text=TWO)                                     # two faults: test_add, test_two
    repo = tmp_path / "repo.git"
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    cfg = make_cfg(tmp_path, repo=repo, live=live, dispatch="full", health_checks=[["false"]])
    tip = gitops.rev(cfg.repo, "refs/heads/main")
    real = gitops.git

    def restore_silently_fails(repo_, *args, **kw):                    # the restoring update-ref does nothing
        if args[:1] == ("update-ref",) and len(args) == 4 and args[2] == tip:
            return ""
        return real(repo_, *args, **kw)
    monkeypatch.setattr(gitops, "git", restore_silently_fails)
    out = runner(cfg).run_once()
    assert out["outcome"] == "rollback_stuck", out
    assert gitops.rev(cfg.repo, "refs/heads/main") != tip
    rec = state(cfg)["tickets"][out["ticket"]]
    assert rec["landing_intent"]["from"] == tip                        # NOT forgotten
    assert any(f.get("rollback_stuck") for f in state(cfg)["faults"].values())
    for dispatch in ("full", "dry"):                                   # every run refuses; the second fault waits
        cfg.dispatch = dispatch
        eng = FakeEngine(fix_calc)
        assert runner(cfg, eng).run_once() == {"outcome": "refused", "rule": "rollback_stuck"} and eng.calls == 0
    assert any(a["step"] == "recover" and a["outcome"] == "rollback_stuck" for a in audit(cfg))
    monkeypatch.setattr(gitops, "git", real)                           # the restore can work again
    out2 = runner(cfg).run_once()                                      # dry: retry, verify, clear, then plan
    assert gitops.rev(cfg.repo, "refs/heads/main") == tip
    rec = state(cfg)["tickets"][out["ticket"]]
    assert rec["landing_intent"] is None and rec["status"] == "rolled_back"
    assert out2["outcome"] == "planned" and out2["ticket"] != out["ticket"]   # now the second fault


# --- review round: weakening is caught at RUNTIME, node by node, whatever the spelling --------

ZERO_ALIAS = TEST.replace("def test_zero():\n", "def test_zero():\n    s_('x')\n").replace(
    "from calc import add\n", "from calc import add\nfrom pytest import skip as s_\n")
ZERO_GETATTR = TEST.replace("def test_zero():\n", "def test_zero():\n    getattr(pytest, 'sk' + 'ip')('x')\n").replace(
    "from calc import add\n", "import pytest\nfrom calc import add\n")
FIXTURE = TEST.replace("from calc import add\n", "import pytest\nfrom calc import add\n\n\n@pytest.fixture(autouse=True)\n"
                       "def _f(request):\n    if request.node.name == 'test_zero':\n        getattr(pytest, 'sk' + 'ip')('x')\n")
MARK_ALIAS = TEST.replace("from calc import add\n", "import pytest as p_\nfrom calc import add\n"
                          "pytestmark = [getattr(p_.mark, 'sk' + 'ip')]\n")
DELETED = TEST.split("\n\n\ndef test_zero")[0] + "\n"
RENAMED = TEST.replace("def test_add()", "def test_add_fixed()")


@pytest.mark.parametrize("text", [ZERO_ALIAS, ZERO_GETATTR, FIXTURE, MARK_ALIAS, DELETED, RENAMED],
                         ids=["import_alias", "getattr", "fixture_skip", "pytestmark_alias", "deleted_test", "renamed_repro"])
def test_runtime_comparison_refuses_what_the_regex_misses(local_scratch, text):
    from selfheal.safety import WEAKEN
    assert WEAKEN.search(text) is None                                  # the cheap regex does NOT see it
    out = runner(local_scratch.cfg, FakeEngine(lambda wt: (fix_calc(wt), (wt / "tests" / "test_calc.py").write_text(text)))).run_once()
    assert out["outcome"] == "discarded" and out["why"] == "test_weakened", out


def test_legitimate_heal_that_adds_a_test_passes(local_scratch):
    def fix_and_add(wt):
        fix_calc(wt)
        (wt / "tests" / "test_more.py").write_text("from calc import add\n\n\ndef test_more():\n    assert add(1, 2) == 3\n")
    out = runner(local_scratch.cfg, FakeEngine(fix_and_add)).run_once()
    assert out["outcome"] == "healed_local", out
    gates = [a for a in audit(local_scratch.cfg) if a["step"] == "gates" and "gates" in a][-1]["gates"]
    assert any(g["gate"] == "relevant_pytest" and "baseline=2" in g["detail"] for g in gates)


def test_reproduction_must_go_from_failing_to_passed(local_scratch):
    """A ticket whose node already PASSES at base (a stale or wrong report) is not a heal, even when
    every other node is intact."""
    given = local_scratch.root / "stale.xml"
    given.write_text('<testsuites><testsuite><testcase classname="tests.test_calc" name="test_zero">'
                     '<failure message="m"/></testcase></testsuite></testsuites>')
    out = runner(local_scratch.cfg).run_once(junit=str(given))
    assert out["outcome"] == "discarded" and out["why"] == "test_weakened", out


# --- review round 2: a heal may never delete or rename a test file -------------------------

EXTRA = "from calc import add\n\n\ndef test_extra():\n    assert add(1, 0) == 1\n"


@pytest.fixture
def extra_scratch(tmp_path):
    """The scratch repo plus an unrelated, PASSING tests/test_extra.py."""
    live = tmp_path / "live"
    seed_repo(live)
    (live / "tests" / "test_extra.py").write_text(EXTRA)
    sh("git", "add", "-A", cwd=live)
    sh("git", "commit", "-q", "-m", "extra test", cwd=live)
    repo = tmp_path / "repo.git"
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    return make_cfg(tmp_path, repo=repo, live=live)


def _rename(wt):
    (wt / "tests" / "test_extra.py").rename(wt / "tests" / "test_extra_renamed.py")


@pytest.mark.parametrize("edit,why", [(lambda wt: (wt / "tests" / "test_extra.py").unlink(), "test_deleted"),
                                      (_rename, "test_deleted"),
                                      (lambda wt: (wt / "conftest.py").unlink(), "forbidden_path")],
                         ids=["delete_other_test", "rename_test", "delete_conftest"])
def test_deleting_or_renaming_a_test_file_is_refused(extra_scratch, edit, why):
    out = runner(extra_scratch, FakeEngine(lambda wt: (fix_calc(wt), edit(wt)))).run_once()
    assert out["outcome"] == "discarded" and out["why"] == why, out


def test_editing_and_adding_tests_still_heals(extra_scratch):
    def fix_and_extend(wt):
        fix_calc(wt)
        p = wt / "tests" / "test_extra.py"
        p.write_text(p.read_text() + "\n\ndef test_extra_two():\n    assert add(2, 2) == 4\n")
        (wt / "tests" / "test_new.py").write_text("from calc import add\n\n\ndef test_new():\n    assert add(5, 5) == 10\n")
    out = runner(extra_scratch, FakeEngine(fix_and_extend)).run_once()
    assert out["outcome"] == "healed_local", out


def test_gate_baseline_lists_the_base_trees_test_files(extra_scratch):
    """Even past the diff policy: a test file present at base but gone after fails the gate."""
    cfg = extra_scratch
    base = Path(cfg.worktree_root) / "base"
    wt = Path(cfg.worktree_root) / "heal"
    gitops.add_worktree(cfg.repo, str(base), base="main")
    gitops.add_worktree(cfg.repo, str(wt), base="main")
    fix_calc(wt)
    (wt / "tests" / "test_extra.py").unlink()
    t = Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s",
               evidence={"nodeid": "tests/test_calc.py::test_add"}, repro_cmd=[], repro_files=["tests/test_calc.py"])
    res = run_gates(cfg, ["relevant_pytest"], str(wt), ["calc.py", "tests/test_extra.py"], t, base_wt=str(base))
    assert res == [{"gate": "relevant_pytest", "status": "fail", "detail": "test_weakened:files_missing=1"}]


# --- review round 3: a planted or stale JUnit report is never proof ----------------------------

PASSING_REPORT = ('<testsuites><testsuite><testcase classname="tests.test_calc" name="test_add"/>'
                  '<testcase classname="tests.test_calc" name="test_zero"/></testsuite></testsuites>')
EXIT_CALC = "import os\nos._exit(0)\n"   # the tests import it: the process ends 0 before any report


@pytest.mark.parametrize("name", [".self-heal-verify.xml", "data/innocent.xml"], ids=["by_name", "by_root"])
def test_planted_report_is_refused_by_the_diff_policy(local_scratch, name):
    def plant(wt):
        (wt / "calc.py").write_text(EXIT_CALC)
        (wt / name).parent.mkdir(exist_ok=True)
        (wt / name).write_text(PASSING_REPORT)
    out = runner(local_scratch.cfg, FakeEngine(plant)).run_once()
    assert out["outcome"] == "discarded" and out["why"] == "report_planted", out


def _exit_worktrees(cfg):
    base, wt = Path(cfg.worktree_root) / "base", Path(cfg.worktree_root) / "heal"
    gitops.add_worktree(cfg.repo, str(base), base="main")
    gitops.add_worktree(cfg.repo, str(wt), base="main")
    (wt / "calc.py").write_text(EXIT_CALC)
    for n in (".self-heal-gate.xml", ".self-heal-verify.xml", ".self-heal-junit.xml"):   # where v1 read them
        (wt / n).write_text(PASSING_REPORT)
    t = Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s",
               evidence={"nodeid": "tests/test_calc.py::test_add"}, repro_cmd=[], repro_files=["tests/test_calc.py"])
    return base, wt, t


def test_exit_zero_without_a_fresh_report_fails_the_gate(local_scratch):
    """Past the diff policy: exit 0, no report written, passing reports planted in the worktree."""
    cfg = local_scratch.cfg
    base, wt, t = _exit_worktrees(cfg)
    res = run_gates(cfg, ["relevant_pytest"], str(wt), ["calc.py"], t, base_wt=str(base))
    assert res[0]["status"] == "fail" and res[0]["detail"].endswith("rc=0,no_report"), res
    out = runner(cfg, FakeEngine(lambda w: (w / "calc.py").write_text(EXIT_CALC))).run_once()
    assert out["outcome"] == "discarded" and out["why"] == "gate_failed", out


def test_stale_report_at_the_real_path_is_refused(local_scratch, monkeypatch):
    cfg = local_scratch.cfg
    _base, wt, _t = _exit_worktrees(cfg)
    fixed = local_scratch.root / "reports"
    fixed.mkdir()
    (fixed / "report.xml").write_text(PASSING_REPORT)
    os.utime(fixed / "report.xml", (946684800, 946684800))           # a report from long ago
    monkeypatch.setattr(sandbox.tempfile, "mkdtemp", lambda prefix="": str(fixed))
    rc, _out, xml = sandbox.Sandbox(cfg).run_report(
        [cfg.python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_calc.py"], str(wt), 60)
    assert rc == 0 and xml is None
