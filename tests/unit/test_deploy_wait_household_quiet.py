"""deploy waits for a quiet household before restarting zoe-data (owner 2026-11-02).

scripts/maintenance/wait_household_quiet.sh reads zoe-data's /health `last_voice_turn_age_s`.
The REAL script is executed here against a file:// "health" URL and a `sleep` shim that
(a) records the wait and (b) advances the fake signal, so bounds are measured, not mocked.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "maintenance" / "wait_household_quiet.sh"
DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"
DEPLOY_LIVE = REPO / "scripts" / "maintenance" / "deploy_live.sh"


def _run(tmp_path, ages, *, env_extra=None, health_missing=False, garbage=False):
    """ages: the signal at poll 0, 1, ...(last repeats; each shim sleep advances one poll)."""
    health = tmp_path / "health.json"
    ctr = tmp_path / "ctr"
    sleeps = tmp_path / "sleeps"
    ctr.write_text("0")
    sleeps.write_text("")
    seq = tmp_path / "seq.json"
    seq.write_text(json.dumps(ages))
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    # sleep shim: no real waiting; log it and rewrite health.json for the next poll
    (shim / "sleep").write_text(
        "#!/bin/bash\n"
        f"echo \"$1\" >> {sleeps}\n"
        f"[ -e {tmp_path}/noadvance ] && exit 0\n"
        f"python3 - <<'PY'\n"
        "import json\n"
        f"n = int(open('{ctr}').read()) + 1\n"
        f"open('{ctr}','w').write(str(n))\n"
        f"seq = json.load(open('{seq}'))\n"
        "age = seq[min(n, len(seq) - 1)]\n"
        f"open('{health}','w').write(json.dumps({{'status':'ok','last_voice_turn_age_s':age}}))\n"
        "PY\n"
    )
    (shim / "sleep").chmod((shim / "sleep").stat().st_mode | stat.S_IXUSR)
    if garbage or health_missing:
        (tmp_path / "noadvance").write_text("")  # the signal stays broken for the whole run
    if garbage:
        health.write_text("<html>not json</html>")
    elif not health_missing:
        health.write_text(json.dumps({"status": "ok", "last_voice_turn_age_s": ages[0]}))
    env = {
        "PATH": f"{shim}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "ZOE_DEPLOY_HEALTH_URL": f"file://{health}",
        "ZOE_NIGHT_BUSY_SCRIPT": str(tmp_path / "no-such-night-busy.sh"),
        "ZOE_DEPLOY_QUIET_POLL_S": "30",
    }
    env.update(env_extra or {})
    p = subprocess.run(["bash", str(SCRIPT)], env=env, cwd=REPO, capture_output=True, text=True, timeout=60)
    n_sleeps = len([x for x in sleeps.read_text().splitlines() if x])
    return p, n_sleeps


def test_recent_turn_waits_until_quiet(tmp_path):
    # turn 60s ago, then 300s, then 700s (>= 10 min): two waits, then proceeds
    p, n = _run(tmp_path, [60, 300, 700])
    assert p.returncode == 0, p.stdout + p.stderr
    assert n == 2
    assert "household not quiet" in p.stdout and "quiet, deploying" in p.stdout


def test_already_quiet_proceeds_without_waiting(tmp_path):
    p, n = _run(tmp_path, [4000])
    assert p.returncode == 0 and n == 0, p.stdout
    assert "quiet, deploying" in p.stdout


def test_no_turn_since_start_is_quiet(tmp_path):
    p, n = _run(tmp_path, [None])
    assert p.returncode == 0 and n == 0, p.stdout
    assert "no panel voice turn" in p.stdout


def test_max_wait_fails_open_with_a_log_line(tmp_path):
    # always busy; max wait 2 min with a 30 s poll -> exactly 4 sleeps then deploys anyway
    p, n = _run(tmp_path, [5], env_extra={"ZOE_DEPLOY_QUIET_MAX_WAIT_MIN": "2"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert n == 4
    assert "failing open, deploying anyway" in p.stdout


def test_signal_unreadable_fails_open_after_a_short_bound(tmp_path):
    p, n = _run(tmp_path, [5], health_missing=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert n <= 2, "default unreadable tolerance is 60 s = 2 polls, not the 60 min max wait"
    assert "unreadable" in p.stdout and "failing open" in p.stdout


def test_garbage_signal_fails_open_too(tmp_path):
    p, n = _run(tmp_path, [5], garbage=True)
    assert p.returncode == 0 and n <= 2 and "failing open" in p.stdout


def test_field_missing_from_old_health_body_is_unreadable_not_quiet(tmp_path):
    p, n = _run(tmp_path, [5], health_missing=True)
    assert "unreadable" in p.stdout
    (tmp_path / "health.json").write_text(json.dumps({"status": "ok"}))
    p = subprocess.run(
        ["bash", str(SCRIPT)],
        env={"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "HOME": str(tmp_path),
             "ZOE_DEPLOY_HEALTH_URL": f"file://{tmp_path / 'health.json'}",
             "ZOE_NIGHT_BUSY_SCRIPT": "/nonexistent", "ZOE_DEPLOY_QUIET_UNREADABLE_S": "30"},
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "unreadable" in p.stdout and "quiet, deploying" not in p.stdout


def test_disabled_flag_skips(tmp_path):
    p, n = _run(tmp_path, [5], env_extra={"ZOE_DEPLOY_QUIET_WAIT": "0"})
    assert p.returncode == 0 and n == 0 and "disabled" in p.stdout


def _git(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _repo_with_change(tmp_path, path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    return repo, base


def _run_in_repo(tmp_path, repo, base):
    health = tmp_path / "h.json"
    health.write_text(json.dumps({"last_voice_turn_age_s": 5}))  # busy household
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    (shim / "sleep").write_text("#!/bin/bash\nexit 0\n")
    (shim / "sleep").chmod(0o755)
    env = {"PATH": f"{shim}:{os.environ['PATH']}", "HOME": str(tmp_path), "DEPLOY_BASE_SHA": base,
           "ZOE_DEPLOY_HEALTH_URL": f"file://{health}", "ZOE_NIGHT_BUSY_SCRIPT": "/nonexistent",
           "ZOE_DEPLOY_QUIET_MAX_WAIT_MIN": "1"}
    return subprocess.run(["bash", str(SCRIPT)], env=env, cwd=repo, capture_output=True, text=True, timeout=60)


def test_docs_only_deploy_does_not_wait_even_when_busy(tmp_path):
    repo, base = _repo_with_change(tmp_path, "docs/x.md")
    p = _run_in_repo(tmp_path, repo, base)
    assert p.returncode == 0 and "docs-only deploy" in p.stdout
    assert "household not quiet" not in p.stdout


def test_code_deploy_waits_when_busy_negative_control(tmp_path):
    repo, base = _repo_with_change(tmp_path, "services/zoe-data/x.py")
    p = _run_in_repo(tmp_path, repo, base)
    assert p.returncode == 0
    assert "docs-only" not in p.stdout and "household not quiet" in p.stdout


def test_unknown_base_sha_waits_fail_closed(tmp_path):
    repo, _ = _repo_with_change(tmp_path, "docs/x.md")
    p = _run_in_repo(tmp_path, repo, "0" * 40)
    assert "household not quiet" in p.stdout


def test_night_window_rules_are_never_failed_open(tmp_path):
    busy = tmp_path / "night_busy.sh"
    busy.write_text("#!/bin/bash\nexit 0\n")  # 0 = must pause
    p, n = _run(tmp_path, [4000], env_extra={"ZOE_NIGHT_BUSY_SCRIPT": str(busy), "ZOE_DEPLOY_NIGHT_WAIT_MAX_MIN": "1"})
    assert p.returncode == 1 and n == 2, p.stdout
    assert "::error::night window rules" in p.stdout
    free = tmp_path / "free.sh"
    free.write_text("#!/bin/bash\nexit 1\n")  # 1 = may run
    p, n = _run(tmp_path, [4000], env_extra={"ZOE_NIGHT_BUSY_SCRIPT": str(free)})
    assert p.returncode == 0 and "quiet, deploying" in p.stdout


def test_deploy_yml_step_sits_right_before_the_zoe_data_restart_and_uses_the_script():
    steps = yaml.safe_load(DEPLOY_YML.read_text())["jobs"]["deploy"]["steps"]
    names = [s.get("name") for s in steps]
    i = names.index("Wait for a quiet household (do not restart mid-conversation)")
    assert names[i + 1] == "Restart zoe-data (user service)"
    assert "wait_household_quiet.sh" in steps[i]["run"]
    assert i > names.index("Pull latest main")
    # the bound the script loops for (60 min + 90 min night cap worst case) fits the step timeout
    assert steps[i]["timeout-minutes"] >= 150


def test_deploy_live_calls_it_before_taking_the_deploy_lock():
    text = DEPLOY_LIVE.read_text()
    assert text.index("wait_household_quiet.sh") < text.index('exec 9>"$DEPLOY_LOCK"')
