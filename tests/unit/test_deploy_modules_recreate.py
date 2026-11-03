"""deploy.yml recreates running module containers when docker-compose.modules.yml changes.

Before: a merged edit to docker-compose.modules.yml (loopback binds, mounts, image pins)
never reached the running containers, because no deploy step ran compose against that file.

These tests EXECUTE the real step script (docker shimmed, the live path rewritten to a
throwaway repo) and assert which services it asks compose to bring up.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"
STEP = "Recreate module containers (if docker-compose.modules.yml changed)"
_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}

# Defined in the modules file: a, b, c.  Running now: a, c, plus base-stack zoe-ui (which
# `ps` reports for the shared project but the modules file does not define).
SHIM = """#!/bin/sh
echo "$@" >> "$DOCKER_LOG"
case "$*" in
  *"config --services"*) printf 'a\\nb\\nc\\n' ;;
  *"ps --services"*) if [ -n "$DOCKER_A_DOWN" ]; then printf 'c\\nzoe-ui\\n'; else printf 'a\\nc\\nzoe-ui\\n'; fi ;;
  *"config -q"*) [ -n "$DOCKER_BAD_CONFIG" ] && exit 1 ;;
  *" up "*) [ -n "$DOCKER_UP_FAIL" ] && exit 1 ;;
esac
exit 0
"""


def _step_run() -> str:
    steps = yaml.safe_load(DEPLOY_YML.read_text())["jobs"]["deploy"]["steps"]
    return next(s for s in steps if s.get("name") == STEP)["run"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env={**os.environ, **_GIT_ENV}).stdout.strip()


def _commit(repo: Path, rel: str, text: str) -> str:
    (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"edit {rel}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path: Path):
    live = tmp_path / "live"
    subprocess.run(["git", "init", "-q", "-b", "main", str(live)], check=True)
    base = _commit(live, "docker-compose.modules.yml", "v1\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "docker"
    shim.write_text(SHIM)
    shim.chmod(0o755)
    return {"live": live, "base": base, "bin": bindir, "log": tmp_path / "docker.log", "state": tmp_path / "deploy-state"}


def _run(world, old_sha: str | None, bad_config: bool = False, pre_pull_sha: str | None = None, up_fail: bool = False, a_down: bool = False):
    script = _step_run().replace("cd /home/zoe/assistant", f"cd {world['live']}", 1)
    env = {**os.environ, "PATH": f"{world['bin']}:{os.environ['PATH']}",
           "DOCKER_LOG": str(world["log"]), "ZOE_DEPLOY_STATE_DIR": str(world["state"])}
    env.pop("DEPLOY_BASE_SHA", None); env.pop("OLD_SHA", None)
    env.pop("DOCKER_BAD_CONFIG", None); env.pop("DOCKER_UP_FAIL", None); env.pop("DOCKER_A_DOWN", None)
    if a_down:
        env["DOCKER_A_DOWN"] = "1"
    if up_fail:
        env["DOCKER_UP_FAIL"] = "1"
    if old_sha is not None:
        env["DEPLOY_BASE_SHA"] = old_sha
    if pre_pull_sha is not None:
        env["OLD_SHA"] = pre_pull_sha
    if bad_config:
        env["DOCKER_BAD_CONFIG"] = "1"
    world["log"].write_text("")
    r = subprocess.run(["bash", "-e", "-c", script], capture_output=True, text=True, env=env)
    return r, world["log"].read_text()


def _ups(log: str) -> list[str]:
    return [line for line in log.splitlines() if " up " in f" {line} "]


def test_unchanged_modules_file_leaves_containers_alone(world):
    _commit(world["live"], "README.md", "x\n")
    r, log = _run(world, world["base"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "left alone" in r.stdout
    assert log == "", "no docker call at all when the file is untouched"


def test_changed_modules_file_recreates_only_running_module_services(world):
    _commit(world["live"], "docker-compose.modules.yml", "v2\n")
    r, log = _run(world, world["base"])
    assert r.returncode == 0, r.stdout + r.stderr
    ups = _ups(log)
    assert len(ups) == 1, log
    assert ups[0].endswith("up -d --no-deps --wait --wait-timeout 180 a c"), ups  # b is stopped on purpose; zoe-ui is not a module


def test_unknown_old_sha_fails_closed_into_the_recreate_path(world):
    r, log = _run(world, None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ups(log), log


def test_invalid_new_modules_file_fails_before_any_recreate(world):
    _commit(world["live"], "docker-compose.modules.yml", "v2\n")
    r, log = _run(world, world["base"], bad_config=True)
    assert r.returncode != 0
    assert not _ups(log), "a bad edit must stop the step before touching a container"


@pytest.mark.parametrize("flag", ["--force-recreate", "--build", "--remove-orphans"])
def test_step_never_forces_or_builds_or_prunes(flag):
    assert flag not in _step_run()


def test_cloudflared_is_pinned_by_digest_not_latest():
    import re
    compose = (REPO / "docker-compose.yml").read_text()
    m = re.search(r"^\s*image:\s*(cloudflare/cloudflared\S*)", compose, re.M)
    assert m, "cloudflared service present"
    assert re.fullmatch(r"cloudflare/cloudflared@sha256:[0-9a-f]{64}", m.group(1)), m.group(1)


def test_a_rerun_after_a_failed_deploy_still_reconciles(world):
    # the first attempt reset the checkout to HEAD and then failed: OLD_SHA (pre-pull) == HEAD now, but the last SUCCESSFUL deploy
    # (DEPLOY_BASE_SHA) is still the old base, so the changed modules file must still be applied
    head = _commit(world["live"], "docker-compose.modules.yml", "v2\n")
    r, log = _run(world, world["base"], pre_pull_sha=head)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ups(log), "a re-run must compare against the last successful deploy, not the pre-pull HEAD"


def test_a_module_that_fails_startup_fails_the_deploy(world):
    # `up --wait` returns non-zero when a recreated module never becomes healthy; the step must fail with it, so the deploy
    # never advances last-deployed-sha past a broken module
    _commit(world["live"], "docker-compose.modules.yml", "v2\n")
    r, log = _run(world, world["base"], up_fail=True)
    assert r.returncode != 0, "a failed module startup must fail the step"
    assert "--wait" in _ups(log)[0], log


def test_a_module_left_down_by_a_failed_recreate_is_retried_on_the_rerun(world):
    # attempt 1: a and c are running, the recreate fails (a dies). attempt 2: a no longer shows as running, but it is still a target
    # (PENDING), so the re-run does not succeed with a still down; a clean recreate clears PENDING
    _commit(world["live"], "docker-compose.modules.yml", "v2\n")
    r1, _ = _run(world, world["base"], up_fail=True)
    assert r1.returncode != 0
    r2, log2 = _run(world, world["base"], a_down=True)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert _ups(log2)[0].endswith(" a c"), _ups(log2)
    assert not (world["state"] / "module-recreate-pending").exists(), "a healthy recreate clears the pending list"
