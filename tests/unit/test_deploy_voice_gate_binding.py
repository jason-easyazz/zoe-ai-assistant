"""The DEPLOY paths bind the voice replay artifact to the tree they deploy.

Incident 2026-09-28 08:14: #1745 (chromadb 1.5.9 pins in requirements-py312.txt,
a voice-path file) was expected to be refused at deploy until a replay bound to
that commit existed. It was NOT: deploy.yml's gate checked only that
~/.cache/zoe/voice_regression_last.json was fresh and passing, and a replay from
an UNRELATED landing a few hours earlier satisfied it. The venv refreshed while
the memory store was still on the 0.6 format -> ~7 min of degraded memory.

Both deploy paths now pass `--expect-tree-of "$target"` (same commit, or a clean
run on a byte-identical tree — the PR head of an up-to-date squash merge). This
suite pins that at the WORKFLOW level, by EXECUTING the real "Pull latest main"
gate snippet from deploy.yml against a throwaway repo, rather than grepping for a
flag a comment could also contain:

  * the incident's artifact (another landing's tree)   -> the step exits 1
  * the PR head's artifact, squash tree-identical       -> the step exits 0
  * an old live checker without --expect-tree-of        -> falls back to the
    STRICTER --expect-revision, never to an unbound call (bootstrap safety: the
    checker runs from the live tree at $prev, which predates this change for
    exactly one deploy — the one carrying it)

The binding logic itself is unit-tested in test_voice_gate_check.py.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"
DEPLOY_LIVE = REPO / "scripts" / "maintenance" / "deploy_live.sh"
CHECKER = REPO / "scripts" / "maintenance" / "voice_gate_check.py"
LIVE_PATH = "/home/zoe/assistant"


def _pull_step_run() -> str:
    spec = yaml.safe_load(DEPLOY_YML.read_text())
    steps = spec["jobs"]["deploy"]["steps"]
    step = next(s for s in steps if s.get("name") == "Pull latest main")
    return step["run"]


def _gate_snippet(repo: Path) -> str:
    """The real gate: from resolving prev/target up to (not incl.) the reset —
    the lines between the fetch and the tree-advance. The live-checkout path is
    rewritten to the throwaway repo; nothing else is altered."""
    lines = _pull_step_run().splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.strip().startswith('prev="$(git rev-parse HEAD)"'))
    end = next(i for i, ln in enumerate(lines) if ln.strip() == 'git reset --hard "$target"')
    assert start < end
    return "\n".join(lines[start:end]).replace(LIVE_PATH, str(repo))


def _executed(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _git(repo: Path, *args: str, env=None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True, env=env).stdout.strip()


def _squash_landing(tmp_path: Path, checker_src: str) -> tuple[Path, dict[str, str]]:
    """Live tree at `prev`; FETCH_HEAD = the squash merge of a voice-path PR
    (new sha, PR head's exact tree). `other` is an unrelated landing."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    repo = tmp_path / "live"
    (repo / "services" / "zoe-data").mkdir(parents=True)
    (repo / "scripts" / "maintenance").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "scripts" / "maintenance" / "voice_gate_check.py").write_text(checker_src)
    req = repo / "services" / "zoe-data" / "requirements-py312.txt"
    req.write_text("chromadb==0.6.3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "prev", env=env)
    prev = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "pr")
    req.write_text("chromadb==1.5.9\n")
    _git(repo, "commit", "-qam", "pr head", env=env)
    head = _git(repo, "rev-parse", "HEAD")
    merge = _git(repo, "commit-tree", f"{head}^{{tree}}", "-p", prev, "-m", "squash", env=env)
    _git(repo, "checkout", "-q", "main")
    (repo / "unrelated.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated", env=env)
    other = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "-q", "--hard", prev)
    (repo / ".git" / "FETCH_HEAD").write_text(f"{merge}\t\tbranch 'main' of origin\n")
    return repo, {"prev": prev, "head": head, "merge": merge, "other": other}


def _artifact(tmp_path: Path, repo: Path, commit: str, dirty: bool = False) -> Path:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    art = {
        "status": "pass", "timestamp": ts, "created_at": ts, "reason": "",
        "baseline_ref": {"created_at": None},
        "summary": {"n_samples": 20},
        "revision": {"commit": commit, "tree": _git(repo, "rev-parse", f"{commit}^{{tree}}"),
                     "dirty": dirty, "clean_verified": True,
                     "service_dir": f"{repo}/services/zoe-data"},
    }
    p = tmp_path / f"artifact-{commit[:8]}-{int(dirty)}.json"
    p.write_text(json.dumps(art))
    return p


def _run_gate(repo: Path, artifact: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "ZOE_VOICE_RESULTS": str(artifact),
           "ZOE_VOICE_BASELINE": str(tmp_path / "no-baseline.json")}
    return subprocess.run(["bash", "-e", "-c", _gate_snippet(repo)], cwd=str(repo),
                          capture_output=True, text=True, env=env)


def _need_git():
    if not shutil.which("git"):
        pytest.skip("git not available")


@pytest.fixture
def landing(tmp_path):
    _need_git()
    return _squash_landing(tmp_path, CHECKER.read_text())


def test_deploy_step_refuses_the_incident_artifact(landing, tmp_path):
    """#1745's exact shape: a fresh passing replay of ANOTHER landing's tree."""
    repo, s = landing
    r = _run_gate(repo, _artifact(tmp_path, repo, s["other"]), tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "REFUSING TO DEPLOY" in r.stdout
    assert "DIFFERENT" in r.stderr
    # the printed unwedge recipe replays a checkout of the TARGET, not the live tree
    assert f"voice-gate-deploy {s['merge']}" in r.stdout


def test_deploy_step_accepts_the_tree_identical_pr_head_artifact(landing, tmp_path):
    repo, s = landing
    r = _run_gate(repo, _artifact(tmp_path, repo, s["head"]), tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tree-identical" in r.stdout


def test_deploy_step_refuses_a_dirty_run(landing, tmp_path):
    repo, s = landing
    r = _run_gate(repo, _artifact(tmp_path, repo, s["head"], dirty=True), tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr


def _stub_checker(argv_log: Path, knows_tree_flag: bool) -> str:
    head = "# supports --expect-tree-of\n" if knows_tree_flag else ""
    return head + ("import json, sys\n"
                   f"json.dump(sys.argv[1:], open({str(argv_log)!r}, 'w'))\n")


def test_old_live_checker_falls_back_to_the_stricter_commit_binding(tmp_path):
    """Bootstrap: the checker runs from the live tree at $prev. A copy predating
    --expect-tree-of must get --expect-revision "$target" — never the bare,
    unbound call that caused the incident, and never an unknown flag that would
    wedge every deploy."""
    _need_git()
    argv_log = tmp_path / "argv.json"
    repo, s = _squash_landing(tmp_path, _stub_checker(argv_log, knows_tree_flag=False))
    r = _run_gate(repo, tmp_path / "unused.json", tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    argv = json.loads(argv_log.read_text())
    assert argv[-2:] == ["--expect-revision", s["merge"]], argv
    assert "--expect-tree-of" not in argv
    assert "::warning::" in r.stdout


def test_current_checker_gets_the_tree_binding(tmp_path):
    """Converse of the fallback: a checker that knows the flag gets it, bound to
    the gate-checked $target."""
    _need_git()
    argv_log = tmp_path / "argv.json"
    repo, s = _squash_landing(tmp_path, _stub_checker(argv_log, knows_tree_flag=True))
    r = _run_gate(repo, tmp_path / "unused.json", tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    argv = json.loads(argv_log.read_text())
    assert argv[-2:] == ["--expect-tree-of", s["merge"]], argv
    assert f"{s['prev']}..{s['merge']}" in argv


def test_every_deploy_yml_checker_call_is_bound():
    """No executed voice_gate_check.py invocation in the pull step may be unbound
    (comments excluded — they name the flags too)."""
    lines = _executed(_pull_step_run())
    calls = [i for i, ln in enumerate(lines)
             if "python3 scripts/maintenance/voice_gate_check.py" in ln]
    assert len(calls) == 1, calls
    call = " ".join(lines[calls[0]:calls[0] + 3])
    assert '"${bind_flag}" "${target}"' in call, call
    assert 'bind_flag="--expect-tree-of"' in lines


def test_deploy_live_sh_is_bound_the_same_way():
    """The manual path shares the checker and the incident; it gets the same
    binding and the same fail-closed fallback."""
    lines = _executed(DEPLOY_LIVE.read_text())
    assert 'bind_flag="--expect-tree-of"' in lines
    assert 'bind_flag="--expect-revision"' in lines
    calls = [i for i, ln in enumerate(lines)
             if 'python3 "$SCRIPT_DIR/voice_gate_check.py"' in ln]
    assert len(calls) == 1, calls
    call = " ".join(lines[calls[0]:calls[0] + 2])
    assert '"$bind_flag" "$target"' in call, call
