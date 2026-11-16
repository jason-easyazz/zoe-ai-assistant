"""No test inherits the CALLER's git config (tests/unit/conftest.py and services/zoe-data/tests/conftest.py).

Measured 2026-11-14 with the network blocked: 21 ``ci_safe`` files (279 tests) failed only because the box's global
git config named a commit-signing program that needs its server. A test's temp repo must behave the same on an
owner's box (signing key, credential helper, hooks path, default branch) as on a runner that has none of them.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _commit(repo: Path, env: dict) -> subprocess.CompletedProcess:
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env, capture_output=True)
    return subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"],
                          env=env, capture_output=True, text=True)


@pytest.fixture()
def hostile_home(tmp_path):
    """A HOME whose git config would break any commit: signing forced on, through a program that always fails."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Owner\n\temail = owner@example.invalid\n\tsigningkey = ABCDEF\n"
        "[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n[init]\n\tdefaultBranch = owner-branch\n",
        encoding="utf-8")
    return {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}


def test_a_commit_in_a_temp_repo_ignores_the_callers_config(hostile_home, tmp_path):
    r = _commit(tmp_path / "repo", hostile_home)
    assert r.returncode == 0, r.stderr
    who = subprocess.run(["git", "-C", str(tmp_path / "repo"), "log", "-1", "--format=%an <%ae>"],
                         env=hostile_home, capture_output=True, text=True).stdout.strip()
    assert who == "Zoe Test <zoe-test@example.invalid>"


def test_negative_control_without_the_pin_the_callers_config_breaks_the_commit(hostile_home, tmp_path):
    env = {k: v for k, v in hostile_home.items() if k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    assert _commit(tmp_path / "repo", env).returncode != 0


def test_the_pin_is_a_session_file_outside_the_repo_and_home():
    cfg = Path(os.environ["GIT_CONFIG_GLOBAL"]).resolve()
    assert cfg.is_file() and os.environ["GIT_CONFIG_NOSYSTEM"] == "1" and os.environ["GIT_TERMINAL_PROMPT"] == "0"
    assert REPO not in cfg.parents
    assert "signingkey" not in cfg.read_text(encoding="utf-8")


@pytest.mark.parametrize("conftest", ["tests/unit/conftest.py", "services/zoe-data/tests/conftest.py"])
def test_both_lane_conftests_pin_it(conftest):
    text = (REPO / conftest).read_text(encoding="utf-8")
    assert "GIT_CONFIG_GLOBAL=_GIT_CONFIG" in text and 'GIT_CONFIG_NOSYSTEM="1"' in text
