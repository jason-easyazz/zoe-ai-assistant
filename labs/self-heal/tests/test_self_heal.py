"""Self-heal lab tests: every safety rule, both landing adapters, the detector, the engines'
argv. All with a FAKE engine on throwaway repos; no model, no GitHub, no network."""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (FIXED, FakeEngine, fix_calc, sh, stub_bin, wait_for)
from selfheal import gitops
from selfheal.config import Config
from selfheal.detectors import FAILING_TEST, Ticket, failing_test_tickets, parse_junit
from selfheal.engines import ClaudeEngine, CodexEngine, make_engine, select_engine
from selfheal.landing import LocalLanding, NoopApplier, cross_review
from selfheal.runner import Runner
from selfheal.safety import DiffReport, HealLock, Refused, State, check_diff, check_quiet_hours

NOON = dt.datetime(2026, 11, 13, 12, 0)


def runner(cfg, engine=None, applier=None, now=NOON, **kw):
    return Runner(cfg, engine=engine or FakeEngine(fix_calc), applier=applier, now=lambda: now, **kw)


def audit(cfg):
    p = Path(cfg.state_dir) / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def state(cfg):
    return json.loads((Path(cfg.state_dir) / "state.json").read_text())


def worktrees(cfg):
    root = Path(cfg.worktree_root)
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def ticket(**kw):
    d = dict(id="t1", fault_class=FAILING_TEST, fault_key="k1", summary="s", evidence={},
             repro_cmd=["true"], repro_files=["tests/test_calc.py"], base_sha="0" * 40)
    d.update(kw)
    return Ticket(**d)


# --- dispatch modes ----------------------------------------------------------------------

def test_dry_plans_only(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "dry"
    eng = FakeEngine(fix_calc)
    out = runner(cfg, eng).run_once()
    assert out["outcome"] == "planned" and eng.calls == 0
    assert worktrees(cfg) == []                                     # scan worktree cleaned, no heal worktree
    assert state(cfg)["heals_today"] == 0                            # a dry run spends no cap
    assert [r for r in audit(cfg) if r["step"] == "plan"][0]["outcome"] == "dry"


def test_local_mode_commits_and_verifies_without_landing(local_scratch):
    cfg = local_scratch.cfg
    tip = gitops.rev(cfg.repo, "refs/heads/main")
    out = runner(cfg).run_once()
    assert out["outcome"] == "healed_local", out
    assert gitops.rev(cfg.repo, "refs/heads/main") == tip            # integration untouched in `local`
    assert gitops.git(cfg.repo, "show", f"{out['sha']}:calc.py") + "\n" == FIXED
    assert worktrees(cfg) == [f"heal-{out['ticket']}"]               # kept for inspection


def test_full_local_landing_heals(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    tip = gitops.rev(cfg.repo, "refs/heads/main")
    applier = NoopApplier(cfg)
    out = runner(cfg, applier=applier).run_once()
    assert out["outcome"] == "healed", out
    assert gitops.rev(cfg.repo, "refs/heads/main") == out["sha"]     # fast-forwarded
    assert gitops.rev(cfg.repo, f"refs/self-heal/snapshots/{out['ticket']}") == tip
    assert applier.calls == [("apply", out["sha"])]
    assert out["review"] == "not reviewed (single engine)"
    land = [r for r in audit(cfg) if r["step"] == "gates" and "gates" in r][0]
    assert [g["gate"] for g in land["gates"]] == cfg.local_gates[:len(land["gates"])]
    assert any(g["gate"] == "offline_lane" and g["status"] == "pass" for g in land["gates"])


def test_full_local_landing_rolls_back_on_failed_health_check(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch, cfg.health_checks = "full", [["false"]]
    tip = gitops.rev(cfg.repo, "refs/heads/main")
    applier = NoopApplier(cfg)
    out = runner(cfg, applier=applier).run_once()
    assert out["outcome"] == "rolled_back"
    assert gitops.rev(cfg.repo, "refs/heads/main") == tip            # snapshot restored
    assert [c[0] for c in applier.calls] == ["apply", "rollback"] and applier.calls[1][1] == tip
    f = next(iter(state(cfg)["faults"].values()))
    assert f["parked"] is True
    assert runner(cfg).run_once()["outcome"] == "all_capped"         # parked faults are not retried


def test_full_local_landing_rolls_back_when_fix_only_weakened_the_test(local_scratch):
    """Gates pass on the weakened test; verify re-runs the ORIGINAL test file and fails."""
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    tip = gitops.rev(cfg.repo, "refs/heads/main")

    def cheat(wt):
        p = wt / "tests" / "test_calc.py"
        p.write_text(p.read_text().replace("== 5", "== -1"))
    out = runner(cfg, FakeEngine(cheat)).run_once()
    assert out["outcome"] == "rolled_back", out
    assert gitops.rev(cfg.repo, "refs/heads/main") == tip
    assert next(iter(state(cfg)["faults"].values()))["parked"] is True


def test_local_landing_needs_no_gh_and_no_network(local_scratch, monkeypatch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    marker = local_scratch.root / "gh-was-called"
    stubs = stub_bin(local_scratch.root, "gh", f"touch {marker}\nexit 1\n")
    monkeypatch.setenv("PATH", f"{stubs}:{os.environ['PATH']}")

    def no_net(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    gitops.CMD_LOG.clear()
    out = runner(cfg).run_once()
    assert out["outcome"] == "healed", out
    assert not marker.exists()
    flat = [" ".join(c) for c in gitops.CMD_LOG]
    assert not any(w in c.split() for c in flat for w in ("gh", "push", "fetch", "pull", "clone"))
    assert not any("cross_review" in c for c in flat)


def test_local_landing_refuses_when_integration_moved(local_scratch):
    cfg = local_scratch.cfg
    t = ticket(base_sha="1" * 40)
    ll = LocalLanding(cfg)
    from selfheal.runner import LandCtx
    r = ll.land(LandCtx(t, "", "", "2" * 40, "fake", [], DiffReport(), {}, ""))
    assert r.status == "failed" and "moved" in r.detail


# --- safety envelope ---------------------------------------------------------------------

def test_kill_switch_refuses_before_anything(local_scratch):
    cfg = local_scratch.cfg
    Path(cfg.kill_switches[0]).touch()
    eng = FakeEngine(fix_calc)
    out = runner(cfg, eng).run_once()
    assert out == {"outcome": "refused", "rule": "kill_switch", "detail": cfg.kill_switches[0]}
    assert eng.calls == 0 and worktrees(cfg) == []


def test_kill_switch_checked_before_every_step(local_scratch):
    cfg = local_scratch.cfg

    def fix_then_pause(wt):
        fix_calc(wt)
        Path(cfg.kill_switches[0]).touch()
    out = runner(cfg, FakeEngine(fix_then_pause)).run_once()
    assert out["rule"] == "kill_switch"
    refused = [r for r in audit(cfg) if r["outcome"] == "refused" and r["step"] == "diff_policy"]
    assert refused, "the step after the engine must see the switch"
    assert worktrees(cfg) == []                                      # discarded, not committed


def test_one_heal_at_a_time(local_scratch):
    cfg = local_scratch.cfg
    with HealLock(cfg):
        out = runner(cfg).run_once()
    assert out["rule"] == "lock"


def test_daily_cap(local_scratch):
    cfg = local_scratch.cfg
    st = State(cfg, NOON.date())
    st.data["heals_today"] = cfg.daily_cap
    st.save()
    assert runner(cfg).run_once()["rule"] == "daily_cap"


def test_daily_cap_resets_next_day(local_scratch):
    cfg = local_scratch.cfg
    st = State(cfg, NOON.date())
    st.data["heals_today"] = cfg.daily_cap
    st.save()
    assert State(cfg, NOON.date() + dt.timedelta(days=1)).data["heals_today"] == 0


def test_per_fault_retry_cap(local_scratch):
    cfg = local_scratch.cfg
    broken = FakeEngine(lambda wt: (wt / "calc.py").write_text("def add(a, b):\n    return a * b\n"))
    assert runner(cfg, broken).run_once()["outcome"] == "discarded"
    assert runner(cfg, broken).run_once()["outcome"] == "discarded"
    out = runner(cfg, broken).run_once()
    assert out["outcome"] == "all_capped" and out["skipped"] == ["per_fault_cap"]
    assert broken.calls == 2


@pytest.mark.parametrize("hm,refused", [("01:29", False), ("01:30", True), ("03:00", True),
                                        ("04:59", True), ("05:00", False), ("12:00", False)])
def test_quiet_hours(hm, refused):
    cfg = Config()
    h, m = map(int, hm.split(":"))
    now = dt.datetime(2026, 11, 13, h, m)
    if refused:
        with pytest.raises(Refused, match="quiet_hours"):
            check_quiet_hours(cfg, now)
    else:
        check_quiet_hours(cfg, now)


def test_quiet_hours_end_to_end(local_scratch):
    out = runner(local_scratch.cfg, now=dt.datetime(2026, 11, 13, 2, 0)).run_once()
    assert out["rule"] == "quiet_hours"


def test_refuses_while_voice_lander_runs(local_scratch):
    cfg = local_scratch.cfg
    cfg.voice_lander_pattern = r"^sleep 31\.4159$"
    p = subprocess.Popen(["sleep", "31.4159"])
    try:
        assert wait_for(lambda: subprocess.run(["pgrep", "-f", cfg.voice_lander_pattern]).returncode == 0)
        assert runner(cfg).run_once()["rule"] == "voice_lander_running"
    finally:
        p.kill()


@pytest.mark.parametrize("rc,rule", [(0, "night_busy"), (None, "night_busy_missing")])
def test_night_busy(local_scratch, rc, rule):
    cfg = local_scratch.cfg
    script = local_scratch.root / "night_busy.sh"
    if rc is not None:
        script.write_text(f"exit {rc}\n")
    cfg.night_busy_script = str(script)
    assert runner(cfg).run_once()["rule"] == rule


def test_night_busy_exit_1_means_may_run(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "dry"
    script = local_scratch.root / "night_busy.sh"
    script.write_text("exit 1\n")
    cfg.night_busy_script = str(script)
    assert runner(cfg).run_once()["outcome"] == "planned"


FORBIDDEN_SAMPLES = [
    ".github/workflows/validate.yml", "docker-compose.yml", "services/x/docker-compose.yaml",
    "scripts/setup/systemd/zoe-data.service", "foo/bar.timer", "scripts/landing/autoland.sh",
    ".env", "services/zoe-data/.env.local", "services/zoe-data/alembic/versions/0099_x.py",
    "services/zoe-auth/main.py", "docs/CANONICAL.md", "services/zoe-data/tests/test_canonical_invariants.py",
    "scripts/maintenance/voice_gate_check.py", "scripts/maintenance/voice_regression_probe.py",
    "scripts/maintenance/deploy_live.sh", "scripts/deploy/migrate.sh", "scripts/night/night_window.sh",
    "scripts/maintenance/requirements_drift_check.py", "scripts/maintenance/wait_household_quiet.sh",
    "models/router.gguf", "services/zoe-data/models/head.safetensors", "labs/self-heal/selfheal/safety.py",
    "../outside.py", "/etc/passwd",
]


@pytest.mark.parametrize("path", FORBIDDEN_SAMPLES)
def test_forbidden_paths(path):
    with pytest.raises(Refused, match="forbidden_path"):
        check_diff(Config(), DiffReport(files=["calc.py", path], added=2), ticket_has_repro_test=True)


def test_allowed_path_passes():
    check_diff(Config(), DiffReport(files=["services/zoe-data/calc.py"], added=2), ticket_has_repro_test=True)


@pytest.mark.parametrize("path", ["scripts/maintenance/voice_gate_check.py", "scripts/deploy/migrate.sh",
                                  ".github/workflows/deploy.yml"])
def test_forbidden_path_discards_attempt_end_to_end(local_scratch, path):
    cfg = local_scratch.cfg

    def touch_gate(wt):
        fix_calc(wt)
        (wt / path).parent.mkdir(parents=True, exist_ok=True)
        (wt / path).write_text("exit 0\n")
    out = runner(cfg, FakeEngine(touch_gate)).run_once()
    assert out["outcome"] == "discarded" and out["why"].startswith("forbidden_path")
    assert worktrees(cfg) == []
    assert "self-heal/" not in gitops.git(cfg.repo, "branch", "--list")


def test_diff_cap(local_scratch):
    cfg = local_scratch.cfg

    def big(wt):
        fix_calc(wt)
        (wt / "padding.py").write_text("x = 1\n" * (cfg.diff_line_cap + 1))
    out = runner(cfg, FakeEngine(big)).run_once()
    assert out["outcome"] == "discarded" and out["why"].startswith("diff_cap")


def test_missing_test_rule():
    with pytest.raises(Refused, match="missing_test"):
        check_diff(Config(), DiffReport(files=["svc/a.py"], added=1), ticket_has_repro_test=False)
    check_diff(Config(), DiffReport(files=["svc/a.py", "svc/tests/test_a.py"], added=2), ticket_has_repro_test=False)


def test_empty_diff_discarded(local_scratch):
    out = runner(local_scratch.cfg, FakeEngine(None)).run_once()
    assert out["outcome"] == "discarded" and out["why"].startswith("empty_diff")


def test_escaping_symlink_discarded(local_scratch):
    def link(wt):
        fix_calc(wt)
        os.symlink("/etc/hostname", wt / "leak.txt")
    out = runner(local_scratch.cfg, FakeEngine(link)).run_once()
    assert out["outcome"] == "discarded" and out["why"].startswith("outside_repo")


def test_gate_failure_discards(local_scratch):
    cfg = local_scratch.cfg
    out = runner(cfg, FakeEngine(lambda wt: (wt / "calc.py").write_text("def add(a, b):\n    return 0\n"))).run_once()
    assert out["outcome"] == "discarded" and out["why"] == "gate failed"
    gates = [r for r in audit(cfg) if r["step"] == "gates" and "gates" in r][0]["gates"]
    assert gates[-1] == {**gates[-1], "gate": "relevant_pytest", "status": "fail"}
    assert worktrees(cfg) == []


def test_engine_failure_discards(local_scratch):
    out = runner(local_scratch.cfg, FakeEngine(fix_calc, ok=False)).run_once()
    assert out["outcome"] == "discarded" and out["why"].startswith("engine")


def test_live_checkout_guard(local_scratch):
    cfg = local_scratch.cfg

    def touch_live(wt):
        fix_calc(wt)
        (Path(cfg.live_checkout) / ".git" / "HEAD").write_text("ref: refs/heads/other\n")
    out = runner(cfg, FakeEngine(touch_live)).run_once()
    assert out["outcome"] == "discarded" and "alarm" in out["why"]


def test_config_refuses_paths_inside_live_checkout(tmp_path):
    with pytest.raises(ValueError, match="live checkout"):
        Config(repo=str(tmp_path / "live" / "x"), live_checkout=str(tmp_path / "live")).validate()


def test_audit_log_has_every_step_and_no_content(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    runner(cfg).run_once()
    steps = [r["step"] for r in audit(cfg) if r["outcome"] == "start"]
    assert steps == ["preflight", "base", "detect", "worktree", "engine", "diff_policy", "gates", "commit", "land"]
    assert all(set(r) >= {"ts", "run", "step", "outcome", "dispatch", "landing", "engine"} for r in audit(cfg))


# --- github landing (local bare origin + stub gh: no network) -----------------------------

def _gh_stub(scratch, merged_sha=""):
    log = scratch.root / "gh.log"
    script = (f'echo "$@" >> {log}\n'
              f'if [ "$1 $2" = "pr create" ]; then echo https://example.invalid/pull/7; exit 0; fi\n'
              f'if [ "$1 $2" = "pr view" ]; then echo \'{{"state":"{"MERGED" if merged_sha else "OPEN"}",'
              f'"mergeCommit":{{"oid":"{merged_sha}"}}}}\'; exit 0; fi\nexit 1\n')
    return stub_bin(scratch.root, "gh", script), log


def test_github_landing_opens_draft_only(github_scratch, monkeypatch):
    cfg = github_scratch.cfg
    cfg.dispatch = "full"
    stubs, log = _gh_stub(github_scratch)
    monkeypatch.setenv("PATH", f"{stubs}:{os.environ['PATH']}")
    out = runner(cfg).run_once()
    assert out["outcome"] == "pr_open", out
    calls = log.read_text().splitlines()
    assert len(calls) == 1 and calls[0].startswith("pr create --draft")
    assert "--label self-heal" in calls[0]
    assert not any(w in log.read_text() for w in ("ready", "merge", "--admin", "--force"))
    body = (Path(cfg.state_dir) / f"pr-body-{out['ticket']}.md").read_text()
    assert "<!-- zoe-self-heal:v1" in body and "opened by Zoe's self-heal loop".lower() in body.lower()
    assert "not reviewed: cross_review.sh not found" in body           # no script in the scratch repo
    marker = json.loads(body.split("<!-- zoe-self-heal:v1", 1)[1].split("-->", 1)[0])
    assert marker["ticket"] == out["ticket"] and marker["fault_class"] == FAILING_TEST
    assert sh("git", "ls-remote", str(github_scratch.root / "origin.git"), f"refs/heads/self-heal/{out['ticket']}")


def test_cross_review_unavailable_is_not_reviewed(tmp_path):
    cfg = Config(state_dir=str(tmp_path / "s"), cross_review_script="cr.sh")
    (tmp_path / "cr.sh").write_text("echo 'cross-review unavailable'; exit 2\n")
    assert cross_review(cfg, str(tmp_path), str(tmp_path), "a", "b", "claude").startswith("not reviewed")
    (tmp_path / "cr.sh").write_text("exit 1\n")
    assert cross_review(cfg, str(tmp_path), str(tmp_path), "a", "b", "claude").startswith("not reviewed")
    (tmp_path / "cr.sh").write_text("echo 'REVIEWER-VENDOR: openai'; exit 0\n")
    assert cross_review(cfg, str(tmp_path), str(tmp_path), "a", "b", "claude").startswith("reviewed")


def test_verify_after_merge_heals_or_parks(github_scratch, monkeypatch):
    cfg = github_scratch.cfg
    cfg.dispatch = "full"
    stubs, _ = _gh_stub(github_scratch)
    monkeypatch.setenv("PATH", f"{stubs}:{os.environ['PATH']}")
    out = runner(cfg).run_once()
    assert runner(cfg).verify_pending() == [{"ticket": out["ticket"], "outcome": "pending"}]
    # "merge + deploy": the heal commit lands on the repo's main
    sh("git", "-C", cfg.repo, "update-ref", "refs/heads/main", out["sha"])
    stubs, _ = _gh_stub(github_scratch, merged_sha=out["sha"])
    res = runner(cfg).verify_pending()
    assert res[0]["outcome"] == "healed"


def test_not_healed_twice_parks(local_scratch):
    cfg = local_scratch.cfg
    st = State(cfg, NOON.date())
    t = ticket()
    st.data["tickets"][t.id] = {"ticket": t.to_dict(), "status": "x"}
    st.save()
    r = runner(cfg)
    r._finish(st, t, "not_healed")
    assert st.fault(t.fault_key)["parked"] is False
    r._finish(st, t, "not_healed")
    assert st.fault(t.fault_key)["parked"] is True


# --- Multica board mirror (fake client; the lab never creates real issues) ------------------

class FakeBoard:
    def __init__(self, fail=False):
        self.created, self.notes, self.statuses, self.fail = [], [], [], fail

    async def create_issue(self, title, description="", **kw):
        if self.fail:
            raise ConnectionError("board down")
        self.created.append((title, kw))
        return {"id": f"issue-{len(self.created)}"}

    async def append_issue_note(self, issue_id, note):
        self.notes.append((issue_id, note))
        return {"id": issue_id}

    async def update_issue(self, issue_id, status=None, **kw):
        self.statuses.append((issue_id, status))
        return {"id": issue_id}


def test_board_mirror_one_issue_per_fault_never_todo(local_scratch):
    from selfheal.board import BoardMirror
    cfg = local_scratch.cfg
    cfg.dispatch, cfg.health_checks = "full", [["false"]]
    fake = FakeBoard()
    r = runner(cfg)
    r.board = BoardMirror(fake, r.audit)
    assert r.run_once()["outcome"] == "rolled_back"
    assert len(fake.created) == 1 and fake.created[0][1]["status"] == "backlog"
    assert "assignee_id" not in fake.created[0][1]
    assert fake.statuses == [("issue-1", "blocked")]                 # parked -> needs a human
    assert any("rolled_back" in n for _, n in fake.notes)


def test_board_mirror_done_on_heal(local_scratch):
    from selfheal.board import BoardMirror
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    fake = FakeBoard()
    r = runner(cfg)
    r.board = BoardMirror(fake, r.audit)
    assert r.run_once()["outcome"] == "healed" and fake.statuses == [("issue-1", "done")]


def test_board_outage_is_harmless(local_scratch):
    from selfheal.board import BoardMirror
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    r = runner(cfg)
    r.board = BoardMirror(FakeBoard(fail=True), r.audit)
    assert r.run_once()["outcome"] == "healed"
    assert any(a["step"] == "board" and a["outcome"] == "error" for a in audit(cfg))


def test_runs_with_multica_omnigent_and_github_all_absent(local_scratch, monkeypatch):
    """Multica unconfigured, no Omnigent, no gh, no network: thin engine + local landing still heal."""
    cfg = local_scratch.cfg
    cfg.dispatch, cfg.board = "full", "multica"
    for k in ("MULTICA_BASE_URL", "MULTICA_API_TOKEN", "MULTICA_WORKSPACE_ID"):
        monkeypatch.delenv(k, raising=False)
    stubs = local_scratch.root / "stubs"
    stubs.mkdir()
    marker = local_scratch.root / "called"
    for tool in ("gh", "docker", "omnigent", "curl"):
        (stubs / tool).write_text(f"#!/bin/bash\necho {tool} >> {marker}\nexit 1\n")
        (stubs / tool).chmod(0o755)
    monkeypatch.setenv("PATH", f"{stubs}:{os.environ['PATH']}")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    gitops.CMD_LOG.clear()
    r = runner(cfg, board="auto")
    assert r.board is None
    assert r.run_once()["outcome"] == "healed"
    assert not marker.exists()
    assert any(a["step"] == "board" and a["outcome"] == "absent" for a in audit(cfg))


# --- detector ----------------------------------------------------------------------------

JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="tests.test_calc" name="test_add"><failure message="assert -1 == 5">x</failure></testcase>
<testcase classname="tests.test_calc.TestX" name="test_y"><error message="ImportError: nope"/></testcase>
<testcase classname="tests.test_calc" name="test_ok"/>
<testcase classname="pkg.tests.test_known" name="test_flaky"><failure message="m"/></testcase>
</testsuite></testsuites>"""


def test_parse_junit():
    got = parse_junit(JUNIT)
    assert [g["nodeid"] for g in got] == ["tests/test_calc.py::test_add", "tests/test_calc.py::TestX::test_y",
                                          "pkg/tests/test_known.py::test_flaky"]
    assert got[0]["message"] == "assert -1 == 5"


def test_failing_test_tickets_ignore_and_stable_key():
    ts = failing_test_tickets(JUNIT, base_sha="a" * 40, python="python3",
                              ignore=["pkg/tests/test_known.py::test_flaky"], now=NOON)
    assert len(ts) == 2 and ts[0].repro_cmd[-1] == "tests/test_calc.py::test_add"
    again = failing_test_tickets(JUNIT, base_sha="b" * 40, python="python3", ignore=[], now=NOON)
    assert again[0].fault_key == ts[0].fault_key                     # same fault -> same key


def test_detector_on_clean_repo_finds_nothing(local_scratch):
    cfg = local_scratch.cfg
    out = runner(cfg, FakeEngine(fix_calc)).run_once(junit=str(_empty_junit(local_scratch.root)))
    assert out["outcome"] == "no_fault"


def _empty_junit(root):
    p = root / "empty.xml"
    p.write_text('<testsuites><testsuite><testcase classname="tests.test_calc" name="test_ok"/></testsuite></testsuites>')
    return p


# --- engines -----------------------------------------------------------------------------

def test_engine_adapters_argv(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    a = CodexEngine(cfg).argv("/wt", "P", tmp_path / "o")
    assert a[:6] == ["codex", "exec", "-C", "/wt", "-s", "workspace-write"] and a[-1] == "P"
    assert not any("dangerously" in x for x in a)
    c = ClaudeEngine(cfg).argv("/wt", "P")
    assert c[:4] == ["claude", "-p", "--permission-mode", "dontAsk"] and c[-2:] == ["--", "P"]
    assert not any("bypass" in x or "dangerously" in x for x in c)
    cfg.engine = "claude"
    assert make_engine(cfg).name == "claude"
    assert make_engine(cfg, "flue_executor").name == "flue_executor"
    cfg.engine = "gpt"
    with pytest.raises(ValueError):
        make_engine(cfg)


def _fake_cli(tmp, name, login_rc):
    p = tmp / name
    p.write_text(f'#!/bin/bash\nif [ "$1 $2" = "login status" ]; then exit {login_rc}; fi\n'
                 'if [ "$1 $2" = "auth status" ]; then echo \'{"loggedIn": false}\'; exit 0; fi\nexit 0\n')
    p.chmod(0o755)
    return str(p)


def test_executor_unavailable_falls_back_to_thin_adapter(tmp_path):
    cfg = Config(state_dir=str(tmp_path), engine="flue_executor", engine_fallbacks=["claude", "codex"],
                 codex_bin=_fake_cli(tmp_path, "codex", 0), claude_bin=_fake_cli(tmp_path, "claude", 0),
                 multica_kill_switch=str(tmp_path / "paused"))
    (tmp_path / "paused").touch()
    eng, skipped = select_engine(cfg)
    assert eng.name == "codex"
    assert [s["engine"] for s in skipped] == ["flue_executor", "claude"]
    assert "kill switch" in skipped[0]["why"] and "not logged in" in skipped[1]["why"]
    assert (tmp_path / "paused").exists()                            # read, never touched


def test_no_engine_linked_does_nothing_to_code(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch, cfg.engine = "full", "codex"
    cfg.codex_bin = _fake_cli(local_scratch.root, "codex", 1)       # installed, nobody logged in
    r = Runner(cfg, now=lambda: NOON)
    assert r.engine is None
    tip = gitops.rev(cfg.repo, "refs/heads/main")
    out = r.run_once()
    assert out["outcome"] == "no_engine" and "no engine linked" in out["why"]
    assert worktrees(cfg) == [] and gitops.rev(cfg.repo, "refs/heads/main") == tip
    st = state(cfg)
    assert st["heals_today"] == 0 and st["tickets"][out["ticket"]]["status"] == "no_engine"
    assert any(a["step"] == "engine" and a["outcome"] == "none" for a in audit(cfg))
    cfg.engine = "none"
    assert Runner(cfg, now=lambda: NOON).run_once()["outcome"] == "no_engine"


def test_engine_env_is_an_allow_list(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("MULTICA_API_TOKEN", "x")
    monkeypatch.setenv("POSTGRES_URL", "x")
    cfg = Config(state_dir=str(tmp_path), codex_bin=str(tmp_path / "fake-codex"), codex_args=[])
    out = tmp_path / "env.txt"
    (tmp_path / "fake-codex").write_text(f"#!/bin/bash\nenv > {out}\n")
    (tmp_path / "fake-codex").chmod(0o755)
    CodexEngine(cfg).run(ticket(), str(tmp_path))
    env = out.read_text()
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "MULTICA_API_TOKEN", "POSTGRES_URL"):
        assert f"{name}=" not in env
    assert "PATH=" in env


def test_engine_timeout_kills(tmp_path):
    cfg = Config(state_dir=str(tmp_path), codex_bin=str(tmp_path / "slow"), codex_args=[], engine_timeout_s=1)
    (tmp_path / "slow").write_text("#!/bin/bash\nsleep 30\n")
    (tmp_path / "slow").chmod(0o755)
    r = CodexEngine(cfg).run(ticket(), str(tmp_path))
    assert not r.ok and "timeout" in r.detail and r.seconds < 10


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
