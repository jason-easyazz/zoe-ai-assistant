"""The engine adapter protocol (`selfheal/proposer.py`): the fault packet and its redaction (planted
sentinels), the allowed output, the budgets, the scripted fake and the dry-run adapter. No real engine
runs anywhere in this file; the dry-run test proves nothing is executed."""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from canned import CANNED
from conftest import FIXED, PLANTED_SENTENCE, PLANTED_TOKEN, make_cfg, seed_repo, sh, stub_bin
from selfheal import gitops, proposer
from selfheal.config import Config
from selfheal.detectors import Ticket
from selfheal.proposer import (DryRunProposer, FaultPacket, ProposerEngine, ScriptedProposer, build_packet,
                               check_patch, make_patch, parse_patch, redact)
from selfheal.safety import Refused
from test_self_heal import runner
from test_self_heal_sandbox import needs_bwrap

REPRO = "tests/test_calc.py::test_add"
EMAIL, PHONE, COORD = "someone.private@example.invalid", "+00 000 555 0199", "-12.345678, 123.456789"   # invented
KEY = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH " + "PRIVATE KEY-----"
LEAKY_TEST = '''import settings
from calc import add
from app_secret import VALUE

R = lambda s: s[::-1]   # the sentinels are spelled backwards in the SOURCE, forwards only in the OUTPUT


def test_add():
    try:
        note = open(R("{state}")).read()
    except OSError:
        note = "unreadable"
    assert add(2, 3) == 5, R("{msg}") + " note=" + note
'''
LEAK_MSG = f"tok={PLANTED_TOKEN} password=hunter2 mail={EMAIL} tel={PHONE} at {COORD} home=/home/someone/x"


def _ticket(cfg, wt):
    return Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s", evidence={"nodeid": REPRO},
                  repro_cmd=[sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", REPRO],
                  repro_files=["tests/test_calc.py"], base_sha=gitops.rev(cfg.repo, "main"))


def _packet(cfg):
    wt = str(Path(cfg.worktree_root) / "base")
    gitops.add_worktree(cfg.repo, wt, base="main")
    return build_packet(cfg, _ticket(cfg, wt), wt), wt


def _hand_packet(**kw):
    d = dict(version=1, ticket="t", fault_class="failing_test", test=REPRO, repro="python -m pytest", base_sha="0" * 40,
             failure="", files={"calc.py": "def add(a, b):\n    return a - b\n", "tests/test_calc.py": "x\n"},
             allow_edit=["calc.py"], allow_new=["tests/test_*.py"],
             budget={"seconds": 5, "turns": 40, "tokens": 1000, "patch_bytes": 64000})
    d.update(kw)
    return FaultPacket(**d)


# --- the packet ---------------------------------------------------------------------------

@needs_bwrap
def test_packet_holds_the_fault_and_only_the_files_in_scope(local_scratch):
    p, wt = _packet(local_scratch.cfg)
    assert set(p.files) == {"tests/test_calc.py", "calc.py"} and p.allow_edit == ["calc.py"]
    assert p.allow_new == ["tests/test_*.py"] and p.withheld == 0
    assert "assert" in p.failure and "1 failed" in p.failure and p.repro.startswith("python -m pytest")
    blob = p.to_json()
    for private in (str(local_scratch.root), sys.executable, wt, str(Path.home())):
        assert private not in blob


@needs_bwrap
def test_planted_sentinels_never_reach_the_packet(tmp_path, monkeypatch):
    live, repo = tmp_path / "live", tmp_path / "repo.git"
    cfg_state = tmp_path / "state"
    seed_repo(live, LEAKY_TEST.format(state=str(cfg_state / "household.txt")[::-1], msg=LEAK_MSG[::-1]))
    (live / "settings.py").write_text(f'API = "{PLANTED_TOKEN}"\n')          # tracked, secret-shaped: withheld
    (live / "app_secret.py").write_text("VALUE = 1\n")                        # tracked, secret PATH: withheld
    (live / ".env").write_text(f"NOTE={PLANTED_SENTENCE}\n")                  # tracked here, forbidden path
    sh("git", "add", "-A", cwd=live)
    sh("git", "commit", "-q", "-m", "plant", cwd=live)
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    cfg = make_cfg(tmp_path, repo=repo, live=live)
    cfg_state.mkdir()
    (cfg_state / "household.txt").write_text(PLANTED_SENTENCE)               # readable on the host ...
    assert PLANTED_SENTENCE in (cfg_state / "household.txt").read_text()
    p, wt = _packet(cfg)
    blob = p.to_json()
    for sentinel in (PLANTED_TOKEN, PLANTED_SENTENCE, EMAIL, PHONE, COORD, "hunter2", "/home/someone", KEY):
        assert sentinel not in blob, sentinel
    assert "note=unreadable" in p.failure                                    # ... and hidden from the sandboxed run
    assert set(p.files) == {"tests/test_calc.py", "calc.py"} and p.withheld == 2 and p.allow_edit == ["calc.py"]
    assert {"token", "email", "secret_value", "home"} <= set(p.redactions)
    # negative control: without redaction the same run DOES carry the planted token
    gitops.remove_worktree(cfg.repo, wt)
    monkeypatch.setattr(proposer, "redact", lambda text, **kw: (text, {}))
    leaked, _ = _packet(cfg)
    assert PLANTED_TOKEN in leaked.failure and EMAIL in leaked.failure


@pytest.mark.parametrize("text", [
    PLANTED_TOKEN, "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2", "xoxb-" + "1234567890-abcdefghij", "AKIA" + "ABCDEFGHIJKLMNOP",
    "eyJ" + "hbGciOiJI.eyJ" + "zdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", KEY, "zoe:" + "pw-1234",
    EMAIL, PHONE, COORD, "192.0.2.44", "/home/someone", "hunter2"])
def test_redact_removes_each_class(text):
    wrapped = {"hunter2": "db_password = 'hunter2'", "zoe:" + "pw-1234": "postgres://zoe:" + "pw-1234@db/zoe"}.get(text, f"saw {text} here")
    out, counts = redact(wrapped)
    assert text not in out and counts, out
    assert redact("def add(a, b):\n    return a + b\n", personal=False) == ("def add(a, b):\n    return a + b\n", {})


# --- the allowed output -------------------------------------------------------------------

@pytest.mark.parametrize("name,reason", [
    ("good", None), ("adds_test", None), ("bad", None),
    ("edits_test", "patch_outside_allowlist"), ("pinned", "patch_outside_allowlist"),
    ("workflow", "patch_outside_allowlist"), ("scratch", "patch_path_escape"), ("dotdot", "patch_path_escape"),
    ("symlink", "patch_symlink"), ("rename", "patch_rename"), ("delete", "patch_delete"), ("chmod", "patch_mode"),
    ("binary", "patch_binary"), ("oversized", "patch_oversized"), ("too_many_lines", "diff_cap"),
    ("garbage", "patch_malformed"), ("empty", "patch_empty")])
def test_patch_check(name, reason):
    packet, cfg = _hand_packet(), Config()
    patch = CANNED[name](packet)
    if reason is None:
        assert "calc.py" in check_patch(patch, packet, cfg)
        return
    with pytest.raises(Refused) as e:
        check_patch(patch, packet, cfg)
    assert e.value.rule == reason and reason in proposer.REASONS


def test_a_new_file_may_not_shadow_or_replace_a_file_in_scope():
    packet, cfg = _hand_packet(), Config()
    for path in ("tests/test_calc.py", "calc.py", "evil.py", "tests/helper.py", "tests/conftest.py"):
        with pytest.raises(Refused) as e:   # "new" only for an allow_new name that is not already in scope
            check_patch(make_patch({}, {path: "x = 1\n"}), packet, cfg)
        assert e.value.rule == "patch_outside_allowlist", path


def test_hunk_bodies_are_counted_so_a_header_lookalike_is_body_and_a_smuggled_header_is_refused():
    old = {"calc.py": "a = 1\n"}
    files = parse_patch(make_patch(old, {"calc.py": "a = 1\n++ b/conftest.py\ndiff --git a/x b/y\n"}))
    assert [(f["path"], f["lines"]) for f in files] == [("calc.py", 2)]
    smuggled = ("diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1,9 @@\n a = 1\n+b = 2\n"
                "diff --git a/conftest.py b/conftest.py\n")
    with pytest.raises(Refused) as e:
        parse_patch(smuggled)
    assert e.value.rule == "patch_malformed"


# --- budgets, through the engine seam -----------------------------------------------------

@needs_bwrap
@pytest.mark.parametrize("name,reason", [("over_tokens", "budget_tokens"), ("over_turns", "budget_turns"),
                                         ("slow", "budget_time"), ("raises", "engine_error"),
                                         ("reported_ok", ""), ("good", "")])
def test_budgets_and_engine_errors(local_scratch, name, reason):
    cfg = local_scratch.cfg
    cfg.engine_timeout_s = 1 if name == "slow" else 30
    wt = str(Path(cfg.worktree_root) / "heal")
    gitops.add_worktree(cfg.repo, wt, base="main")
    eng = ProposerEngine(cfg, ScriptedProposer(CANNED[name]))
    res = eng.run(_ticket(cfg, wt), wt)
    assert (res.ok, res.reason, eng.last["reason"]) == (not reason, reason, reason)
    assert (Path(wt) / "calc.py").read_text() == (FIXED if not reason else "def add(a, b):\n    return a - b\n")
    if name == "reported_ok":
        assert (eng.last["turns"], eng.last["tokens"]) == (3, 1200)
    if name == "good":
        assert (eng.last["turns"], eng.last["tokens"]) == (None, None)   # unreported stays unreported


def test_budgets_may_only_be_lowered(local_scratch):
    for field in ("engine_max_turns", "engine_max_tokens"):
        cfg = local_scratch.cfg
        setattr(cfg, field, 10**9)
        with pytest.raises(ValueError, match="only be lowered"):
            cfg.validate()
        setattr(cfg, field, 5)
        cfg.validate()
    assert proposer.Budget.of(cfg).turns == 5


# --- the dry-run adapter ------------------------------------------------------------------

@needs_bwrap
@pytest.mark.parametrize("cli", ["codex", "claude"])
def test_dry_run_prints_the_command_and_executes_nothing(local_scratch, tmp_path, monkeypatch, cli):
    marker = tmp_path / "engine-ran"
    stubs = stub_bin(tmp_path, cli, f"touch {marker}\n")
    cfg = make_cfg(tmp_path, repo=local_scratch.repo, live=local_scratch.live, dispatch="full",
                   **{f"{cli}_bin": str(stubs / cli)})
    main = gitops.rev(cfg.repo, "main")

    def no_net(*a, **k):
        raise AssertionError("network call in a dry run")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    eng = ProposerEngine(cfg, DryRunProposer(cfg, cli))
    out = runner(cfg, eng).run_once()
    assert (out["outcome"], out["why"]) == ("discarded", "engine_dry_run"), out
    assert eng.last["would_run"] == ["materialize", "init", "engine", "collect"]   # step KINDS only, never an argv
    pkt = FaultPacket(1, "t", "failing_test", "tests/test_calc.py::test_add", "python -m pytest -q x", "0" * 40, "", {},
                      [], [], {"seconds": 5}, 1)
    steps = {s["step"]: s for s in DryRunProposer(cfg, cli).propose(pkt).would_run}
    argv = steps["engine"]["argv"]
    assert argv[0] == str(stubs / cli) and steps["engine"]["cwd"] == "<packet-dir>"
    assert ("workspace-write" in argv and argv[argv.index("-C") + 1] == "<packet-dir>") if cli == "codex" \
        else argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[-1].startswith("<prompt chars=") and "assert" not in " ".join(argv)   # no failure text on a command line
    assert not marker.exists() and gitops.rev(cfg.repo, "main") == main
    assert all(a[0] != str(stubs / cli) for a in gitops.CMD_LOG)
    subprocess.run([str(stubs / cli)], check=True)   # negative control: the stub WOULD have left the marker
    assert marker.exists()


def test_dry_run_knows_only_named_clis(local_scratch):
    with pytest.raises(ValueError):
        DryRunProposer(local_scratch.cfg, "bash")


# --- through the runner: a good patch heals, a refused one changes nothing ----------------

@needs_bwrap
def test_good_patch_heals_through_the_supervised_local_landing(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    fake = ScriptedProposer(CANNED["adds_test"])
    out = runner(cfg, ProposerEngine(cfg, fake)).run_once()
    assert out["outcome"] == "healed" and out["verify"] == "verify:supervised,node=passed", out
    assert gitops.git(cfg.repo, "show", "main:calc.py") + "\n" == FIXED
    assert len(fake.packets) == 1 and fake.packets[0].attempt == 1


@needs_bwrap
@pytest.mark.parametrize("name,reason", [("edits_test", "patch_outside_allowlist"), ("symlink", "patch_symlink"),
                                         ("oversized", "patch_oversized")])
def test_refused_patch_is_discarded_with_its_reason(local_scratch, name, reason):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    main = gitops.rev(cfg.repo, "main")
    out = runner(cfg, ProposerEngine(cfg, ScriptedProposer(CANNED[name]))).run_once()
    assert (out["outcome"], out["why"]) == ("discarded", reason), out
    assert gitops.rev(cfg.repo, "main") == main and not list(Path(cfg.worktree_root).glob("heal-*"))
