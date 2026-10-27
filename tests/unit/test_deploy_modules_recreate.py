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
  *"ps --services"*) printf 'a\\nc\\nzoe-ui\\n' ;;
  *"config -q"*) [ -n "$DOCKER_BAD_CONFIG" ] && exit 1 ;;
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
    return {"live": live, "base": base, "bin": bindir, "log": tmp_path / "docker.log"}


def _run(world, old_sha: str | None, bad_config: bool = False):
    script = _step_run().replace("cd /home/zoe/assistant", f"cd {world['live']}", 1)
    env = {**os.environ, "PATH": f"{world['bin']}:{os.environ['PATH']}",
           "DOCKER_LOG": str(world["log"])}
    env.pop("OLD_SHA", None)
    env.pop("DOCKER_BAD_CONFIG", None)
    if old_sha is not None:
        env["OLD_SHA"] = old_sha
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
    assert ups[0].endswith("up -d --no-deps a c"), ups  # b is stopped on purpose; zoe-ui is not a module


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
