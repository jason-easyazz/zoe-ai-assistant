"""deploy.yml waits out an open 12B night window instead of deploying beside it.

Open problem 2026-10-11 'Deploy x night window': while the nightly window is open it
has STOPPED zoe-data, the 4B, Kokoro and the router (it writes
~/.zoe/night-window/WINDOW_OPEN). A deploy landing then either false-failed the Health
check or restarted zoe-data next to the loaded 12B (RAM). The first step now polls the
marker every 30 s for up to 75 min and FAILS LOUDLY after that.

Two kinds of test, as in test_deploy_docs_only_skip.py:
  * structural: the step exists, is FIRST, and so sits before every pull / reset /
    restart; it names WINDOW_OPEN; its own timeout covers the bound it loops for;
  * behavioural: the real step script is EXECUTED against a throwaway HOME with a
    `sleep` shim (no real waiting), so the bound, the log and the failure are measured.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"
STEP_NAME = "Wait out an open night window (do not deploy beside the 12B)"


def _job() -> dict:
    return yaml.safe_load(DEPLOY_YML.read_text())["jobs"]["deploy"]


def _steps() -> list[dict]:
    return _job()["steps"]


def _index(pred) -> int:
    return next(i for i, s in enumerate(_steps()) if pred(s))


def test_the_wait_step_exists_names_the_marker_and_comes_first():
    steps = _steps()
    i = _index(lambda s: s.get("name") == STEP_NAME)
    assert "WINDOW_OPEN" in steps[i]["run"] and ".zoe/night-window" in steps[i]["run"]
    assert i == 0, "nothing may run (not even the memory gate) before the night-window wait"


def test_it_precedes_every_pull_reset_and_restart_step():
    steps = _steps()
    wait = _index(lambda s: s.get("name") == STEP_NAME)
    mutating = [i for i, s in enumerate(steps)
                if re.search(r"git (fetch|reset|pull)|systemctl[^\n]*(restart|start)|docker compose|alembic", s.get("run", ""))]
    assert mutating, "precondition: deploy.yml still has steps that pull / restart"
    assert all(wait < i for i in mutating), (wait, mutating)
    pull = _index(lambda s: s.get("name") == "Pull latest main")
    restart = _index(lambda s: s.get("name") == "Restart zoe-data (user service)")
    assert wait < pull < restart


def test_its_timeout_covers_the_window_cap_and_the_loop_bound():
    step = next(s for s in _steps() if s.get("name") == STEP_NAME)
    # the window's cap is ~65 min (end-by 03:55); the loop waits 75 min; the step must not be killed first
    assert step["timeout-minutes"] >= 75
    # the JOB has no timeout of its own that could undercut it (default 360 min)
    assert _job().get("timeout-minutes", 360) >= step["timeout-minutes"] + 60


@pytest.fixture
def world(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".zoe" / "night-window").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "sleep.log"
    # `sleep` shim: record the argument, never wait; if CLOSE_AFTER is set, the 'window' ends
    # (the marker is removed) after that many sleeps.
    shim = bindir / "sleep"
    shim.write_text(
        "#!/bin/sh\n"
        f'echo "$1" >> {log}\n'
        f"n=$(wc -l < {log})\n"
        '[ -n "$CLOSE_AFTER" ] && [ "$n" -ge "$CLOSE_AFTER" ] && rm -f "$HOME/.zoe/night-window/WINDOW_OPEN"\n'
        "exit 0\n")
    shim.chmod(0o755)
    return {"home": home, "bin": bindir, "log": log, "marker": home / ".zoe" / "night-window" / "WINDOW_OPEN"}


def _run(world, **env_extra) -> subprocess.CompletedProcess:
    script = next(s for s in _steps() if s.get("name") == STEP_NAME)["run"]
    base = {k: v for k, v in os.environ.items() if k != "CLOSE_AFTER"}
    env = {**base, "HOME": str(world["home"]), "PATH": f"{world['bin']}:{os.environ['PATH']}", **env_extra}
    world["log"].write_text("")
    return subprocess.run(["bash", "-e", "-c", script], capture_output=True, text=True, env=env)


def _sleeps(world) -> list[str]:
    return [ln for ln in world["log"].read_text().splitlines() if ln]


def test_no_window_open_deploys_immediately_without_sleeping(world):
    r = _run(world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _sleeps(world) == [] and "No night window open" in r.stdout


def test_an_open_window_is_waited_out_polling_every_30s_and_logging_why(world):
    world["marker"].write_text('{"pid": 1, "run_id": "20261011-020000", "mode": "night"}')
    r = _run(world, CLOSE_AFTER="3")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _sleeps(world) == ["30", "30", "30"]
    assert "Night window is OPEN" in r.stdout and "20261011-020000" in r.stdout and "deploying" in r.stdout.splitlines()[-1]
    assert "(3/150)" in r.stdout                                  # 75 min / 30 s


def test_a_window_that_never_closes_fails_loudly_after_75_minutes_having_touched_nothing(world):
    world["marker"].write_text('{"pid": 1, "run_id": "stuck"}')
    r = _run(world)
    assert r.returncode == 1
    assert len(_sleeps(world)) == 150 and set(_sleeps(world)) == {"30"}          # 150 x 30 s = 75 min
    assert "::error::night window still open after 75 min: nothing deployed" in r.stdout
    assert "--restore-only" in r.stdout


def test_a_window_that_closes_on_the_last_poll_still_deploys(world):
    world["marker"].write_text("{}")
    r = _run(world, CLOSE_AFTER="150")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_sleeps(world)) == 150 and "nothing deployed" not in r.stdout
