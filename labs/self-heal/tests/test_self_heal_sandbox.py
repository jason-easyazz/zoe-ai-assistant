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

from conftest import FakeEngine, fix_calc, make_cfg, seed_repo, sh, stub_bin, wait_for
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
    wt = tmp / "worktrees" / "heal-hostile"
    (wt / "tests").mkdir(parents=True)
    (wt / "tests" / "test_hostile.py").write_text(HOSTILE % dict(
        secret=str(fakehome / ".ssh" / "id_test"), home=real_home, live=str(s.live), outside=str(tmp / "outside"),
        fakehome=str(fakehome), port=lst.getsockname()[1]))
    before = _tree(tmp, wt)
    t = Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s", evidence={}, repro_cmd=[])
    res = run_gates(_gate_cfg(s), ["relevant_pytest"], str(wt), ["tests/test_hostile.py"], t)
    got = json.loads((wt / "escape.json").read_text())
    assert set(got) == {"read_secret", "see_real_home", "see_live_checkout", "read_fake_live", "write_outside",
                        "write_fake_home", "write_usr", "env_token", "network", "memory", "fork"}
    assert not any(got.values()), got                                   # every attempt failed inside
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
    """The 'fix' makes the code under test skip at import: pytest exits 0, the node never passed."""
    def skip_it(wt):   # every test skips at run time: pytest exits 0 and the gate passes
        (wt / "calc.py").write_text("def add(a, b):\n    import pytest\n    pytest.skip('x')\n")
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
