"""Scratch-repo fixtures for the self-heal lab tests. Nothing here touches the real repo,
the live checkout, GitHub or the network: every repo is a throwaway under tmp_path."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selfheal.config import Config  # noqa: E402
from selfheal.engines import EngineResult  # noqa: E402

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def sh(*argv, cwd=None):
    return subprocess.run(list(argv), cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def seed_repo(path: Path) -> None:
    path.mkdir(parents=True)
    sh("git", "init", "-q", "-b", "main", str(path))
    (path / "conftest.py").write_text("")
    (path / "calc.py").write_text(BUGGY)
    (path / "tests").mkdir()
    (path / "tests" / "test_calc.py").write_text(TEST)
    sh("git", "add", "-A", cwd=path)
    sh("git", "commit", "-q", "-m", "seed: a deliberately failing test", cwd=path)


class FakeEngine:
    """Applies `edit(worktree)` instead of calling a model."""
    name = "fake"

    def __init__(self, edit=None, ok=True):
        self.edit, self.ok, self.calls = edit, ok, 0

    def run(self, ticket, wt):
        self.calls += 1
        if self.edit:
            self.edit(Path(wt))
        return EngineResult(self.ok, "rc=0 fake edit", 0.01)


def fix_calc(wt: Path):
    (wt / "calc.py").write_text(FIXED)


@dataclass
class Scratch:
    root: Path
    live: Path
    repo: Path
    cfg: Config


def make_cfg(tmp: Path, *, repo: Path, live: Path, **kw) -> Config:
    base = dict(
        repo=str(repo), live_checkout=str(live), worktree_root=str(tmp / "worktrees"),
        state_dir=str(tmp / "state"), kill_switches=[str(tmp / "paused")],
        dispatch="local", landing="local", engine="codex",
        voice_lander_pattern=r"^self-heal-test-never-matches-\d{40}$", night_busy_script="",
        detect_cmd=[sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests"],
        offline_lane_cmd=[sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests"],
        python=sys.executable, test_lock="", gate_timeout_s=120, detect_timeout_s=120,
    )
    base.update(kw)
    cfg = Config(**base)
    cfg.validate()
    return cfg


@pytest.fixture
def local_scratch(tmp_path) -> Scratch:
    """A 'live checkout' (never written) plus a bare clone that holds the integration branch."""
    live = tmp_path / "live"
    seed_repo(live)
    repo = tmp_path / "repo.git"
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    return Scratch(tmp_path, live, repo, make_cfg(tmp_path, repo=repo, live=live))


@pytest.fixture
def github_scratch(tmp_path) -> Scratch:
    """A local bare 'origin' (no network) and a clone of it as the heal repo."""
    seed = tmp_path / "seed"
    seed_repo(seed)
    origin = tmp_path / "origin.git"
    sh("git", "clone", "-q", "--bare", str(seed), str(origin))
    repo = tmp_path / "base"
    sh("git", "clone", "-q", str(origin), str(repo))
    live = tmp_path / "live"
    live.mkdir()
    return Scratch(tmp_path, live, repo, make_cfg(tmp_path, repo=repo, live=live, landing="github"))


def stub_bin(tmp: Path, name: str, script: str) -> Path:
    d = tmp / "stubs"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/bash\n" + script)
    p.chmod(0o755)
    return d


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False
