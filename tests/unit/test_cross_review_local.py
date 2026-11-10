"""cross_review.sh --local <base>..<head> (2026-11-10): pre-push review, read-only on every checkout, no Claude, exit 2 = not reviewed.
Backends are test_cross_review_fallback.py's stubs; a logging `git` wrapper goes first on PATH."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_cross_review_fallback import _SCRIPT, _STUB_CODEX, _STUB_GH, _STUB_PI, _STUB_POLLY, _git, _make_live_checkout

pytestmark = pytest.mark.ci_safe

_GIT_SPY = r"""#!/usr/bin/env bash
echo "$*" >> "$GIT_SPY_LOG"
exec "$REAL_GIT" "$@"
"""

MUTATING = ("checkout", "switch", "reset", "fetch", "worktree add", "update-ref", "commit", "merge", "rebase", "stash", "pull", "push",
            "branch", "tag", "restore", "clean", "apply", "am", "cherry-pick")


@pytest.fixture()
def rig(tmp_path):
    live = _make_live_checkout(tmp_path)
    src = tmp_path / "author-worktree"                       # where the author runs --local from: two commits, base..head
    _git("init", "-q", str(src))
    (src / "a.py").write_text("x = 1\n")
    _git("add", "a.py", cwd=src)
    _git("commit", "-q", "-m", "base", cwd=src)
    (src / "a.py").write_text("x = 2  # the change under review\n")
    _git("commit", "-q", "-am", "the change", cwd=src)
    (src / "untracked.txt").write_text("the author's work in progress\n")   # must survive untouched
    bindir, wt_root, tmpdir = tmp_path / "bin", tmp_path / "worktrees", tmp_path / "tmp"
    bindir.mkdir(); tmpdir.mkdir()
    (bindir / "git").write_text(_GIT_SPY); (bindir / "git").chmod(0o755)

    def stub(name: str, body: str) -> Path:
        p = tmp_path / name
        p.write_text(body)
        p.chmod(0o755)
        return p

    log, gitlog = tmp_path / "calls.log", tmp_path / "git.log"
    log.write_text(""); gitlog.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "REAL_GIT": subprocess.run(["bash", "-c", "command -v git"], capture_output=True, text=True).stdout.strip(),
        "GIT_SPY_LOG": str(gitlog),
        "TMPDIR": str(tmpdir),
        "STUB_LOG": str(log), "STUB_DIR": str(tmp_path), "STUB_LIVE": str(live),
        "CROSS_REVIEW_POLLY_SCRIPT": str(stub("polly.sh", _STUB_POLLY)),
        "CROSS_REVIEW_CODEX_BIN": str(stub("codex", _STUB_CODEX)),
        "CROSS_REVIEW_PI_BIN": str(stub("pi", _STUB_PI)),
        "CROSS_REVIEW_GH_BIN": str(stub("gh", _STUB_GH)),
        "CROSS_REVIEW_LIVE_CHECKOUT": str(live),
        "CROSS_REVIEW_WORKTREE_ROOT": str(wt_root),
    }

    class R:
        source, live_dir, review_root, tmp = src, live, wt_root, tmpdir

        def run(self, rng="HEAD~1..HEAD", **overrides):
            e = {**env, **{k: str(v) for k, v in overrides.items()}}
            return subprocess.run([str(_SCRIPT), "--local", rng, "Must not break X."], cwd=src, env=e,
                                  capture_output=True, text=True, timeout=120, check=False)

        def run_pr(self, **overrides):
            e = {**env, **{k: str(v) for k, v in overrides.items()}}
            return subprocess.run([str(_SCRIPT), "123", "Must not break X."], cwd=src, env=e,
                                  capture_output=True, text=True, timeout=120, check=False)

        def calls(self):
            return [ln.split()[0] for ln in log.read_text().splitlines() if ln.strip() and not ln.startswith(("polly-saw", "codex-saw"))]

        def git_calls(self):
            return [ln for ln in gitlog.read_text().splitlines() if ln]

        def stdin_of(self, who):
            return (tmp_path / f"{who}.stdin").read_text()

        def snapshot(self):
            def st(d):
                return (_git("-C", str(d), "rev-parse", "HEAD"), _git("-C", str(d), "status", "--porcelain", "--untracked-files=all"),
                        _git("-C", str(d), "for-each-ref"), _git("-C", str(d), "worktree", "list", "--porcelain"))
            return st(src), st(live)

    return R()


def test_local_review_goes_to_codex_with_the_diff_and_never_polly(rig):
    r = rig.run()
    assert r.returncode == 0, r.stderr
    assert rig.calls() == ["codex"], "polly needs a PR; gh must not be asked for a PR diff"
    prompt = rig.stdin_of("codex")
    assert "x = 2  # the change under review" in prompt and "Must not break X." in prompt
    assert "the local diff HEAD~1..HEAD" in prompt and "PRE-PUSH review" in prompt and "pr-head" not in prompt
    assert r.stdout.startswith("REVIEWER-VENDOR: openai/codex")
    assert "polly unavailable — needs a PR number" in r.stderr


def test_local_review_runs_no_mutating_git_and_leaves_every_checkout_as_it_was(rig):
    before = rig.snapshot()
    r = rig.run(STUB_CODEX="fail")      # codex fails -> pi: every backend path that can run in --local is exercised
    assert r.returncode == 0 and rig.calls() == ["codex", "pi"], r.stderr
    assert rig.snapshot() == before, "the author's worktree or the live checkout changed"
    bad = [c for c in rig.git_calls() if any((" " + c + " ").find(" " + m + " ") >= 0 for m in MUTATING)]
    # the stale-review sweep may `worktree remove` / `update-ref -d` ITS OWN leftovers: here there are none, so nothing at all
    assert bad == [], bad
    assert [c for c in rig.git_calls() if " diff " in f" {c} "], "the diff was not read with git diff"
    assert list(rig.review_root.glob("review-*")) == [], "the scratch review dir was not removed"
    assert list(rig.tmp.iterdir()) == [], "something was left in TMPDIR"


def test_every_backend_failing_is_exit_2_not_reviewed(rig):
    r = rig.run(STUB_CODEX="quota", STUB_PI="quota")
    assert r.returncode == 2, r.stderr
    assert "cross-review unavailable" in r.stderr and "the local diff HEAD~1..HEAD" in r.stderr
    assert "NOT a clean review" in r.stderr and r.stdout == ""


def test_polly_only_in_local_mode_is_not_reviewed(rig):
    r = rig.run(CROSS_REVIEW_BACKENDS="polly")
    assert r.returncode == 2 and rig.calls() == [], r.stderr


@pytest.mark.parametrize("rng", ["--output=/tmp/x..HEAD", "HEAD", "HEAD~1..-p", "a b..HEAD", "HEAD;id..HEAD", ""])
def test_a_range_that_is_not_two_plain_revisions_is_refused(rig, rng):
    r = rig.run(rng=rng)
    assert r.returncode == 1 and rig.calls() == [] and not [c for c in rig.git_calls() if " diff " in f" {c} "], r.stderr


def test_an_empty_diff_is_not_reviewed(rig):
    r = rig.run(rng="HEAD..HEAD")
    assert r.returncode == 2 and rig.calls() == [] and "non-empty local diff" in r.stderr


def test_the_spy_sees_mutating_git_on_the_pr_path(rig):
    # positive control for the spy above: the PR path DOES fetch the head and add a worktree (on the throwaway live checkout)
    r = rig.run_pr(STUB_POLLY="authfail")
    assert r.returncode == 0, r.stderr
    assert any(" fetch " in f" {c} " for c in rig.git_calls()) and any("worktree add" in c for c in rig.git_calls())
