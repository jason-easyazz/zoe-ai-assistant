"""The docs-only zoe-data restart skip in deploy.yml reads a durable last-deployed marker.

Incident 2026-10-05 (sig #43): the "Restart zoe-data" step had no `cd`, so its git
commands ran in the runner's WORKSPACE clone (a stale second repo under _work/),
not the live checkout the OLD_SHA came from. OLD_SHA was a perfectly good commit in
the live tree and "bad object" in the workspace one, so the skip could never
trigger (and, before #1878, a failed diff printed nothing and read as "docs-only").

The fix: the step cd's into the live checkout, and its baseline is
DEPLOY_BASE_SHA - the SHA the last SUCCESSFUL deploy stamped into
~/.zoe/deploy/last-deployed-sha (bootstrapped from the live HEAD once).

These tests EXECUTE the real step scripts from deploy.yml (systemctl shimmed, the
live path rewritten to a throwaway repo) rather than grepping for strings.
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

_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _steps() -> list[dict]:
    return yaml.safe_load(DEPLOY_YML.read_text())["jobs"]["deploy"]["steps"]


def _step(name: str) -> dict:
    return next(s for s in _steps() if s.get("name") == name)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env={**os.environ, **_GIT_ENV}).stdout.strip()


def _commit(repo: Path, rel: str, text: str) -> str:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"edit {rel}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path: Path):
    """live/ = the live checkout; decoy/ = the runner workspace clone, an unrelated
    repo that has NONE of live's commits (the real one sat at a stale SHA)."""
    live = tmp_path / "live"
    subprocess.run(["git", "init", "-q", "-b", "main", str(live)], check=True)
    base = _commit(live, "services/zoe-data/app.py", "v1\n")
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", "-b", "main", str(decoy)], check=True)
    _commit(decoy, "README.md", "workspace clone\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "systemctl.log"
    shim = bindir / "systemctl"
    shim.write_text(f'#!/bin/sh\necho "$@" >> {log}\n'
                    '[ "$2" = show ] && echo 0\nexit 0\n')
    shim.chmod(0o755)
    return {"live": live, "decoy": decoy, "base": base, "bin": bindir, "log": log,
            "state": tmp_path / "state"}


def _restart(world, base_sha: str | None, run: str | None = None) -> tuple[bool, str]:
    """Run the Restart step from the DECOY cwd (as the runner does). -> (restarted, out)."""
    script = run if run is not None else _step("Restart zoe-data (user service)")["run"]
    env = {**os.environ, "PATH": f"{world['bin']}:{os.environ['PATH']}",
           "ZOE_CHECKOUT": str(world["live"])}
    env.pop("DEPLOY_BASE_SHA", None)
    if base_sha is not None:
        env["DEPLOY_BASE_SHA"] = base_sha
    world["log"].write_text("")
    r = subprocess.run(["bash", "-e", "-c", script], cwd=str(world["decoy"]),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    restarted = "restart zoe-data.service" in world["log"].read_text()
    return restarted, r.stdout + r.stderr


def test_docs_only_merge_since_the_marker_sha_skips_the_restart(world):
    _commit(world["live"], "docs/knowledge/x.md", "note\n")
    _commit(world["live"], "README.md", "readme\n")
    restarted, out = _restart(world, world["base"])
    assert not restarted, out
    assert "docs-only merge since" in out


def test_code_change_since_the_marker_sha_restarts(world):
    _commit(world["live"], "docs/x.md", "note\n")
    _commit(world["live"], "services/zoe-data/app.py", "v2\n")
    restarted, out = _restart(world, world["base"])
    assert restarted, out


@pytest.mark.parametrize("bad", [None, "", "0" * 40, "not-a-sha"])
def test_unknown_or_bad_base_fails_closed(world, bad):
    _commit(world["live"], "docs/x.md", "note\n")
    restarted, out = _restart(world, bad)
    assert restarted, out
    assert "fail closed" in out


def test_base_equal_to_head_restarts_conservatively(world):
    restarted, out = _restart(world, world["base"])
    assert restarted, out


def test_negative_control_without_the_cd_the_skip_cannot_trigger(world):
    """The instrument check: strip the `cd` (the pre-fix script) and the very same
    docs-only scenario falls to a restart because git runs in the decoy clone."""
    _commit(world["live"], "docs/x.md", "note\n")
    run = "\n".join(ln for ln in _step("Restart zoe-data (user service)")["run"].splitlines()
                    if not ln.strip().startswith("cd "))
    assert "cd " not in run.replace("# ", "")  # the control really removed the cd
    restarted, out = _restart(world, world["base"], run=run)
    assert restarted, out
    # ...and WITH the cd the identical scenario skips (the fix).
    restarted, out = _restart(world, world["base"])
    assert not restarted, out


def _pull_marker_snippet() -> str:
    lines = _step("Pull latest main")["run"].splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("DEPLOY_STATE_DIR="))
    end = next(i for i, ln in enumerate(lines)
               if ln.strip() == 'echo "DEPLOY_BASE_SHA=${DEPLOY_BASE_SHA}" >> "$GITHUB_ENV"')
    return "\n".join(lines[start:end + 1])


def _read_base(world, tmp_path: Path) -> str:
    gh_env = tmp_path / "github_env"
    gh_env.write_text("")
    env = {**os.environ, "ZOE_DEPLOY_STATE_DIR": str(world["state"]),
           "GITHUB_ENV": str(gh_env)}
    subprocess.run(["bash", "-e", "-c", _pull_marker_snippet()], cwd=str(world["live"]),
                   check=True, capture_output=True, text=True, env=env)
    return dict(ln.split("=", 1) for ln in gh_env.read_text().splitlines())["DEPLOY_BASE_SHA"]


def test_pull_step_bootstraps_from_live_head_when_no_marker(world, tmp_path):
    assert _read_base(world, tmp_path) == world["base"]


def test_pull_step_prefers_the_marker_over_the_live_head(world, tmp_path):
    marked = world["base"]
    _commit(world["live"], "services/zoe-data/app.py", "v2\n")  # live HEAD moved on
    world["state"].mkdir()
    (world["state"] / "last-deployed-sha").write_text(marked + "\n")
    assert _read_base(world, tmp_path) == marked


def test_record_step_is_last_and_writes_the_marker_atomically(world):
    assert _steps()[-1]["name"] == "Record deployed SHA"
    assert "if" not in _steps()[-1]  # default success(): a failed deploy never advances it
    run = _steps()[-1]["run"]
    env = {**os.environ, "ZOE_DEPLOY_STATE_DIR": str(world["state"])}
    env.pop("DEPLOY_TARGET_SHA", None)
    subprocess.run(["bash", "-e", "-c", run], check=True, capture_output=True, env=env)
    assert not (world["state"] / "last-deployed-sha").exists()  # no target -> untouched
    env["DEPLOY_TARGET_SHA"] = world["base"]
    subprocess.run(["bash", "-e", "-c", run], check=True, capture_output=True, env=env)
    assert (world["state"] / "last-deployed-sha").read_text().strip() == world["base"]
    assert not (world["state"] / "last-deployed-sha.tmp").exists()


def test_target_sha_is_recorded_after_the_reset():
    run = _step("Pull latest main")["run"]
    assert run.index('git reset --hard "$target"') < run.index('echo "DEPLOY_TARGET_SHA=${target}"')
