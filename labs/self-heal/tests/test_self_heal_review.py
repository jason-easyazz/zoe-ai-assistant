"""The six cross-review rules (PR 2443): hard floor, hard live-checkout refusal, inspect before
staging, allow-listed health checks, no content in any record, mid-run re-checks."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (PLANTED_SENTENCE, PLANTED_TOKEN, TEST, FakeEngine, fix_calc, make_cfg, seed_repo,
                      sh, stub_bin, wait_for)
from selfheal import gitops, landing as landing_mod, runner as runner_mod
from selfheal.board import BoardMirror
import selfheal.config as config_mod
from selfheal.config import HARD_LIVE_CHECKOUT, Config, check_health_argv
from selfheal.landing import NoopApplier
from selfheal.runner import Runner
from selfheal.safety import Refused, check_quiet_hours

NOON = dt.datetime(2026, 11, 13, 12, 0)


def run(cfg, engine=None, now=NOON, **kw):
    clock = now if callable(now) else (lambda: now)
    return Runner(cfg, engine=engine or FakeEngine(fix_calc), now=clock, **kw)


def audit_rows(cfg):
    p = Path(cfg.state_dir) / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def state(cfg):
    return json.loads((Path(cfg.state_dir) / "state.json").read_text())


def worktrees(cfg):
    root = Path(cfg.worktree_root)
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


# --- 1. hard floor ---------------------------------------------------------------------------

WEAKER = [
    {"forbidden": []},
    {"forbidden": [p for p in Config().forbidden if p != "scripts/maintenance/voice_gate_check.py"]},
    {"daily_cap": 4}, {"per_fault_cap": 3}, {"diff_line_cap": 301}, {"not_healed_park_after": 3},
    {"daily_cap": -1},
    {"quiet_start": "02:00", "quiet_end": "05:00"}, {"quiet_start": "01:30", "quiet_end": "04:59"},
    {"quiet_start": "12:00", "quiet_end": "13:00"},
]


@pytest.mark.parametrize("weaker", WEAKER, ids=lambda d: ",".join(d))
def test_config_cannot_weaken_the_floor(tmp_path, weaker):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(weaker))
    with pytest.raises(ValueError):
        Config.load(str(f))


@pytest.mark.parametrize("tighter", [
    {"daily_cap": 1, "per_fault_cap": 1, "diff_line_cap": 50, "not_healed_park_after": 1},
    {"forbidden": [*Config().forbidden, "docs/*"]},
    {"quiet_start": "00:00", "quiet_end": "06:00"}, {"quiet_start": "22:00", "quiet_end": "05:30"},
    {"kill_switches": []},
])
def test_config_may_tighten(tmp_path, tighter):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(tighter))
    Config.load(str(f))


def test_floor_holds_even_when_mutated_after_load(local_scratch):
    cfg = local_scratch.cfg
    cfg.daily_cap = 10
    with pytest.raises(ValueError):
        Runner(cfg, engine=FakeEngine(fix_calc))


def test_builtin_kill_switch_cannot_be_configured_away(local_scratch):
    cfg = local_scratch.cfg
    cfg.kill_switches = []
    config_mod.hard_kill_switch().parent.mkdir(parents=True, exist_ok=True)
    config_mod.hard_kill_switch().touch()
    assert run(cfg).run_once() == {"outcome": "refused", "rule": "kill_switch"}


def test_builtin_quiet_window_applies_whatever_the_config():
    cfg = Config(quiet_start="00:00", quiet_end="06:00")
    cfg.quiet_start, cfg.quiet_end = "12:00", "12:01"     # mutated past validate
    with pytest.raises(Refused):
        check_quiet_hours(cfg, dt.datetime(2026, 11, 13, 3, 0))


# --- 2. the live checkout, hard-coded ---------------------------------------------------------

@pytest.fixture
def live_link(tmp_path):
    link = tmp_path / "innocent-looking"
    os.symlink(HARD_LIVE_CHECKOUT, link)
    return link


@pytest.mark.parametrize("field", ["repo", "worktree_root", "state_dir", "apply_target"])
def test_live_checkout_refused_through_a_symlink(tmp_path, live_link, field):
    kw = {field: str(live_link / "sub")}
    with pytest.raises(ValueError, match="live checkout"):
        Config(live_checkout=str(tmp_path / "other"), **kw).validate()
    with pytest.raises(ValueError, match="live checkout"):
        Config(live_checkout=str(tmp_path / "other"), **{field: str(live_link)}).validate()


def test_live_checkout_refused_even_if_config_names_another(tmp_path):
    with pytest.raises(ValueError):
        Config(repo=HARD_LIVE_CHECKOUT, live_checkout=str(tmp_path / "decoy")).validate()


def test_gitops_never_runs_in_the_live_checkout(local_scratch, live_link):
    gitops.CMD_LOG.clear()
    with pytest.raises(gitops.GitError, match="live checkout"):
        gitops.git(str(live_link), "status")
    with pytest.raises(gitops.GitError, match="live checkout"):
        gitops.add_worktree(str(local_scratch.repo), str(live_link / "heal-x"), base="main")
    with pytest.raises(gitops.GitError, match="live checkout"):
        gitops.run(["true"], cwd=str(live_link))
    assert gitops.CMD_LOG == []                                       # refused before anything ran


def test_apply_target_refused(tmp_path, live_link):
    cfg = Config(state_dir=str(tmp_path / "s"))
    cfg.apply_target = str(live_link)
    with pytest.raises(ValueError):
        NoopApplier(cfg)


# --- 3. inspect before staging ----------------------------------------------------------------

def test_violation_never_stages_or_commits(local_scratch):
    cfg = local_scratch.cfg

    def bad(wt):
        fix_calc(wt)
        (wt / "scripts" / "landing").mkdir(parents=True)
        (wt / "scripts" / "landing" / "x.sh").write_text("exit 0\n")
    gitops.CMD_LOG.clear()
    out = run(cfg, FakeEngine(bad)).run_once()
    assert out["outcome"] == "discarded" and out["why"] == "forbidden_path"
    assert not [c for c in gitops.CMD_LOG if c[:2] == ["git", "-C"] and len(c) > 3 and c[3] in ("add", "commit")]
    assert worktrees(cfg) == [] and "self-heal/" not in gitops.git(cfg.repo, "branch", "--list")


def test_only_checked_paths_are_committed(local_scratch):
    """Gate/runner artefacts (pycache) and nothing else beyond the checked set reach the commit."""
    cfg = local_scratch.cfg

    def fix_and_cache(wt):
        fix_calc(wt)
        (wt / "__pycache__").mkdir()
        (wt / "__pycache__" / "calc.cpython-310.pyc").write_bytes(b"\0\1binary")
    out = run(cfg, FakeEngine(fix_and_cache)).run_once()
    assert out["outcome"] == "healed_local", out
    files = gitops.git(cfg.repo, "show", "--name-only", "--format=", out["sha"]).split()
    assert files == ["calc.py"]
    assert not [c for c in gitops.CMD_LOG if c[3:5] == ["add", "-A"]]


def test_stage_checked_refuses_anything_extra(local_scratch, tmp_path):
    cfg = local_scratch.cfg
    wt = str(tmp_path / "wt")
    base = gitops.rev(cfg.repo, "main")
    gitops.add_worktree(str(cfg.repo), wt, base=base, branch="t")
    (Path(wt) / "calc.py").write_text("x = 1\n")
    (Path(wt) / "other.py").write_text("y = 2\n")
    gitops.git(wt, "add", "other.py")                                 # something staged behind our back
    with pytest.raises(gitops.GitError, match="staged set"):
        gitops.stage_checked(wt, ["calc.py"], base)


def test_engine_commit_is_discarded(local_scratch):
    cfg = local_scratch.cfg

    def commit_itself(wt):
        fix_calc(wt)
        sh("git", "-C", str(wt), "commit", "-qam", "engine committed")
    out = run(cfg, FakeEngine(commit_itself)).run_once()
    assert out["outcome"] == "discarded" and out["why"] == "engine_committed"


# --- 4. health checks are an allow-list -------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "curl http://example.invalid", ["curl", "http://example.invalid"], ["wget", "x"], ["gh", "pr", "list"],
    ["ssh", "host"], ["git", "fetch"], ["bash", "-c", "true"], ["sh", "x"], ["env", "true"],
    ["python3", "-c", "print(1)"], ["python3", "/etc/x.py"], ["python3", "../x.py"],
    ["python3", "-m", "pytest", "/abs/tests"], ["systemctl", "--user", "restart", "zoe-data"],
    ["systemctl", "is-active", "zoe-data"], ["/usr/bin/nc", "-l"], ["rm", "-rf", "x"], [],
])
def test_health_check_not_allow_listed_fails_the_load(tmp_path, bad):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"health_checks": [bad]}))
    with pytest.raises(ValueError):
        Config.load(str(f))


@pytest.mark.parametrize("good", [["true"], ["false"], ["systemctl", "--user", "is-active", "zoe-data.service"],
                                  ["python3", "-m", "pytest", "-q", "tests"], ["python3", "scripts/check_health.py"]])
def test_health_check_allowed_forms(good):
    check_health_argv(good)


def test_health_check_runs_in_a_fresh_integration_worktree(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    cfg.health_checks = [[cfg.python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests"]]
    out = run(cfg).run_once()
    assert out["outcome"] == "healed", out           # pytest found tests/ => cwd was the integration tip
    assert worktrees(cfg) == []


# --- 5. no content anywhere -------------------------------------------------------------------

PLANTED_TEST = TEST.replace("assert add(2, 3) == 5", f'assert add(2, 3) == 5, "{PLANTED_SENTENCE} {PLANTED_TOKEN}"')


class RecordingBoard:
    def __init__(self):
        self.payloads = []

    async def create_issue(self, title, description="", **kw):
        self.payloads.append(json.dumps([title, description, kw]))
        return {"id": "issue-1"}

    async def append_issue_note(self, issue_id, note):
        self.payloads.append(note)
        return {}

    async def update_issue(self, issue_id, status=None, **kw):
        self.payloads.append(json.dumps([status, kw]))
        return {}


def _planted_scratch(tmp_path, landing):
    seed = tmp_path / "seed"
    seed_repo(seed, PLANTED_TEST)
    if landing == "local":
        repo = tmp_path / "repo.git"
        sh("git", "clone", "-q", "--bare", str(seed), str(repo))
    else:
        origin = tmp_path / "origin.git"
        sh("git", "clone", "-q", "--bare", str(seed), str(origin))
        repo = tmp_path / "base"
        sh("git", "clone", "-q", str(origin), str(repo))
    return make_cfg(tmp_path, repo=repo, live=seed, landing=landing, dispatch="full")


def _assert_clean(cfg, extra_texts):
    texts = list(extra_texts)
    for p in Path(cfg.state_dir).rglob("*"):
        if p.is_file():
            texts.append(p.read_text(errors="replace"))
    texts += [" ".join(c) for c in gitops.CMD_LOG]
    blob = "\n".join(texts)
    assert PLANTED_TOKEN not in blob and PLANTED_SENTENCE not in blob
    assert "flowerpot" not in blob and "ghp_" not in blob


@pytest.mark.parametrize("landing", ["github", "local"])
@pytest.mark.parametrize("engine_ok", [True, False])
@pytest.mark.parametrize("chatter", ["sentence+token", "token"])
def test_no_content_reaches_any_record(tmp_path, monkeypatch, landing, engine_ok, chatter):
    cfg = _planted_scratch(tmp_path, landing)
    gh_log = tmp_path / "gh.log"
    stubs = stub_bin(tmp_path, "gh", f'echo "$@" >> {gh_log}\n'
                                     'if [ "$1 $2" = "pr create" ]; then echo https://example.invalid/pull/9; fi\n')
    monkeypatch.setenv("PATH", f"{stubs}:{os.environ['PATH']}")
    gitops.CMD_LOG.clear()
    board = RecordingBoard()
    said = f"{PLANTED_SENTENCE} {PLANTED_TOKEN}" if chatter == "sentence+token" else PLANTED_TOKEN
    r = run(cfg, FakeEngine(fix_calc, ok=engine_ok, chatter=said))
    r.board = BoardMirror(board, r.audit)
    out = r.run_once()
    assert out["outcome"] == (("pr_open" if landing == "github" else "healed") if engine_ok else "discarded"), out
    ticket = state(cfg)["tickets"][out["ticket"]]["ticket"]
    assert set(ticket["evidence"]) == {"nodeid", "kind", "failure_hash", "base_sha"}
    if landing == "github" and engine_ok:
        body = (Path(cfg.state_dir) / f"pr-body-{out['ticket']}.md").read_text()
        assert "calc.py" in body and "tests/test_calc.py::test_add" in body and "zoe-self-heal:v1" in body
    _assert_clean(cfg, board.payloads + [gh_log.read_text() if gh_log.exists() else "", json.dumps(out)])


def test_gate_failure_output_does_not_leak(tmp_path):
    """The failing gate's pytest output carries the planted assertion text; only counts survive."""
    cfg = _planted_scratch(tmp_path, "local")
    cfg.dispatch = "local"
    gitops.CMD_LOG.clear()
    out = run(cfg, FakeEngine(lambda wt: (wt / "calc.py").write_text("def add(a, b):\n    return 0\n"))).run_once()
    assert out["outcome"] == "discarded" and out["why"] == "gate_failed"
    _assert_clean(cfg, [json.dumps(out)])


def test_rc_summary_keeps_counts_only():
    from selfheal.safety import rc_summary
    out = f"F.\nE   AssertionError: {PLANTED_SENTENCE} {PLANTED_TOKEN}\nFAILED t.py::x - {PLANTED_TOKEN}\n1 failed, 1 passed in 0.1s\n"
    assert rc_summary(1, out) == "rc=1,failed=1,passed=1"


def test_engine_keeps_no_model_output(tmp_path):
    from selfheal.engines import CodexEngine
    fake = tmp_path / "codex"
    fake.write_text(f"#!/bin/bash\necho '{PLANTED_SENTENCE}'\necho {PLANTED_TOKEN} >&2\n")
    fake.chmod(0o755)
    cfg = Config(state_dir=str(tmp_path / "s"), codex_bin=str(fake), codex_args=[])
    from conftest import FakeEngine as _F  # noqa: F401
    from selfheal.detectors import Ticket
    t = Ticket(id="t", fault_class="failing_test", fault_key="k", summary="s", evidence={}, repro_cmd=["true"])
    r = CodexEngine(cfg).run(t, str(tmp_path))
    assert r.detail == "rc=0"
    assert not any(PLANTED_TOKEN in p.read_text() for p in Path(cfg.state_dir).rglob("*") if p.is_file())


def test_detector_keeps_no_failure_text():
    from selfheal.detectors import failing_test_tickets, parse_junit
    xml = (f'<testsuites><testsuite><testcase classname="tests.test_calc" name="test_add">'
           f'<failure message="{PLANTED_SENTENCE}">{PLANTED_TOKEN}</failure></testcase></testsuite></testsuites>')
    blob = json.dumps(parse_junit(xml)) + json.dumps(
        [t.to_dict() for t in failing_test_tickets(xml, base_sha="a", python="p", ignore=[], now=NOON)])
    assert PLANTED_TOKEN not in blob and PLANTED_SENTENCE not in blob


def test_audit_refuses_unknown_keys(local_scratch):
    r = run(local_scratch.cfg)
    with pytest.raises(ValueError):
        r.audit("x", "y", detail="free text")
    r.audit("x", "y", reason="a sentence with spaces")
    assert audit_rows(local_scratch.cfg)[-1]["reason"] == "<redacted>"


# --- 6. re-checks mid-run ---------------------------------------------------------------------

class Trigger:
    """Flip one of the four run conditions at a chosen moment."""

    def __init__(self, cfg, kind, root):
        self.cfg, self.kind, self.root, self.proc, self.late = cfg, kind, root, None, False
        if kind == "night_busy":
            self.script = root / "night_busy.sh"
            self.script.write_text("exit 1\n")
            cfg.night_busy_script = str(self.script)
        if kind == "voice":
            cfg.voice_lander_pattern = r"^sleep 47\.1828$"

    def now(self):
        return dt.datetime(2026, 11, 13, 2, 0) if self.late else NOON

    def fire(self):
        if self.kind == "kill":
            Path(self.cfg.kill_switches[0]).touch()
        elif self.kind == "quiet":
            self.late = True
        elif self.kind == "night_busy":
            self.script.write_text("exit 0\n")
        elif self.kind == "voice":
            self.proc = subprocess.Popen(["sleep", "47.1828"])
            assert wait_for(lambda: subprocess.run(["pgrep", "-f", self.cfg.voice_lander_pattern]).returncode == 0)

    def stop(self):
        if self.proc:
            self.proc.kill()


RULE = {"kill": "kill_switch", "quiet": "quiet_hours", "night_busy": "night_busy", "voice": "voice_lander_running"}


def _arm(monkeypatch, checkpoint, trig):
    if checkpoint == "engine":
        orig = gitops.add_worktree

        def add(repo, path, *, base, branch=None):
            orig(repo, path, base=base, branch=branch)
            if branch:
                trig.fire()
        monkeypatch.setattr(gitops, "add_worktree", add)
        return FakeEngine(fix_calc)
    if checkpoint == "gates":
        return FakeEngine(lambda wt: (fix_calc(wt), trig.fire()))
    if checkpoint == "land":
        orig = runner_mod.run_gates

        def gates(*a, **k):
            res = orig(*a, **k)
            trig.fire()
            return res
        monkeypatch.setattr(runner_mod, "run_gates", gates)
        return FakeEngine(fix_calc)
    if checkpoint == "push":
        orig = landing_mod.cross_review

        def review(*a, **k):
            res = orig(*a, **k)
            trig.fire()
            return res
        monkeypatch.setattr(landing_mod, "cross_review", review)
        return FakeEngine(fix_calc)
    if checkpoint == "pr":
        orig = gitops.git

        def git(repo, *args, **kw):
            res = orig(repo, *args, **kw)
            if args[:1] == ("push",):
                trig.fire()
            return res
        monkeypatch.setattr(gitops, "git", git)
        return FakeEngine(fix_calc)
    raise AssertionError(checkpoint)


@pytest.mark.parametrize("checkpoint,kind,landing", [
    ("engine", "night_busy", "local"), ("engine", "kill", "local"),
    ("gates", "quiet", "local"), ("gates", "voice", "local"),
    ("land", "voice", "local"), ("land", "night_busy", "local"), ("merge", "night_busy", "local"),
    ("push", "kill", "github"), ("pr", "quiet", "github"),
])
def test_interrupted_mid_run(request, monkeypatch, checkpoint, kind, landing):
    scratch = request.getfixturevalue("github_scratch" if landing == "github" else "local_scratch")
    cfg = scratch.cfg
    cfg.dispatch = "full"
    trig = Trigger(cfg, kind, scratch.root)
    stubs = stub_bin(scratch.root, "gh", 'echo https://example.invalid/pull/1\n')
    monkeypatch.setenv("PATH", f"{stubs}:{os.environ['PATH']}")
    if checkpoint == "merge":   # fires after the land checkpoint, before the local fast-forward
        orig = landing_mod.gitops.checked_out_branches

        def cob(repo):
            trig.fire()
            return orig(repo)
        monkeypatch.setattr(landing_mod.gitops, "checked_out_branches", cob)
        engine = FakeEngine(fix_calc)
    else:
        engine = _arm(monkeypatch, checkpoint, trig)
    tip = gitops.rev(str(cfg.repo), "refs/heads/main")
    try:
        out = run(cfg, engine, now=trig.now).run_once()
    finally:
        trig.stop()
    assert out["outcome"] == "interrupted", out
    assert out["checkpoint"] == checkpoint and out["why"] == RULE[kind]
    st = state(cfg)
    assert st["heals_today"] == 0 and all(f["attempts"] == 0 for f in st["faults"].values())
    assert worktrees(cfg) == []
    assert gitops.rev(str(cfg.repo), "refs/heads/main") == tip
    assert "self-heal/" not in gitops.git(str(cfg.repo), "branch", "--list")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
