"""No deploy path may resolve its target from FETCH_HEAD; the lander must not write it.

Incident 2026-10-30 01:43 AWST (deploy run 40924021148): deploy.yml's "Pull latest
main" step did `git fetch origin main` and then `git rev-parse FETCH_HEAD` on the
box's SHARED /home/zoe/assistant/.git. FETCH_HEAD is ONE file there; the voice
lander's `git fetch origin fix/retire-resemblyzer` rewrote it in between, so the
deploy targeted an UNMERGED branch commit (fa8952ef9) instead of main. Only the
voice replay gate's fail-closed refusal saved the live tree - a non-voice branch
would have been reset onto and restarted.

Pinned here, each with a negative control proving the check can fail:
  * deploy.yml / deploy_live.sh never read FETCH_HEAD in executable lines, fetch
    main by an explicit refspec into a deploy-only ref, verify it against
    `git ls-remote origin refs/heads/main`, and refuse a non-descendant target;
  * the real gate snippet, EXECUTED, ignores a poisoned FETCH_HEAD and refuses a
    target that is not a fast-forward of the live HEAD;
  * the lander's fetches use explicit refspecs into refs/zoe-landing/ and
    --no-write-fetch-head, and (executed) leave FETCH_HEAD untouched.
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
DEPLOY_LIVE = REPO / "scripts" / "maintenance" / "deploy_live.sh"
LANDER = REPO / "scripts" / "landing" / "land_voice_pr.sh"
LIVE_PATH = "/home/zoe/assistant"
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
RESOLVE = '"refs/zoe-deploy/main^{commit}"'
ANCESTOR = 'git merge-base --is-ancestor "$prev" "$target"'


def _code(text: str) -> str:
    """Executable lines only - comments may (and do) explain the incident."""
    return "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))


def reads_fetch_head(text: str) -> bool:
    return "FETCH_HEAD" in _code(text)


def _deploy_steps() -> list[dict]:
    return yaml.safe_load(DEPLOY_YML.read_text())["jobs"]["deploy"]["steps"]


def _pull_run() -> str:
    return next(s for s in _deploy_steps() if s.get("name") == "Pull latest main")["run"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=GIT_ENV).stdout.strip()


# ---------------------------------------------------------------- static pins
def test_no_deploy_step_reads_fetch_head():
    offenders = [s.get("name") for s in _deploy_steps() if reads_fetch_head(s.get("run", ""))]
    assert not offenders, f"deploy.yml steps resolving a target from FETCH_HEAD: {offenders}"
    assert not reads_fetch_head(DEPLOY_LIVE.read_text()), "deploy_live.sh reads FETCH_HEAD"


def test_pull_step_fetches_by_refspec_verifies_remote_and_requires_descendant():
    run = _pull_run()
    assert 'git fetch --no-write-fetch-head origin "+refs/heads/main:${deploy_ref}"' in run
    assert 'deploy_ref="refs/zoe-deploy/main"' in run
    assert "git ls-remote origin refs/heads/main" in run
    assert ANCESTOR in run
    assert f'target="$(git rev-parse --verify {RESOLVE})"' in run
    live = DEPLOY_LIVE.read_text()
    assert "refs/zoe-deploy/main" in live and "ls-remote origin refs/heads/main" in live
    assert "merge-base --is-ancestor" in live


def test_negative_control_the_detector_flags_the_incident_shape():
    # the pre-fix line, verbatim, must be caught; a comment mentioning it must not be
    assert reads_fetch_head('git fetch origin main\ntarget="$(git rev-parse FETCH_HEAD)" || exit 1')
    assert not reads_fetch_head("# not FETCH_HEAD, which a concurrent fetch could advance\nx=1")
    assert reads_fetch_head(_pull_run().replace(RESOLVE, "FETCH_HEAD"))


# ------------------------------------------------- executed gate (deploy.yml)
def _gate_snippet(repo: Path, run: str | None = None) -> str:
    lines = (run or _pull_run()).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith('prev="$(git rev-parse HEAD)"'))
    end = next(i for i, ln in enumerate(lines) if ln.strip() == 'git reset --hard "$target"')
    return "\n".join(lines[start:end]).replace(LIVE_PATH, str(repo))


def _rig(tmp_path: Path):
    repo = tmp_path / "live"
    (repo / "scripts" / "maintenance").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    # stub checker: records argv; mentions --expect-tree-of so the feature-detect passes
    (repo / "scripts" / "maintenance" / "voice_gate_check.py").write_text(
        "# --expect-tree-of\nimport json, sys, os\n"
        "json.dump(sys.argv[1:], open(os.environ['ARGV_LOG'], 'w'))\n")
    (repo / "f.txt").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "prev")
    prev = _git(repo, "rev-parse", "HEAD")
    (repo / "f.txt").write_text("main\n")
    _git(repo, "commit", "-qam", "next main")
    main_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "-q", "--hard", prev)
    # an UNMERGED branch commit that does NOT descend from the live HEAD
    root = _git(repo, "commit-tree", "-m", "orphan", _git(repo, "rev-parse", f"{prev}^{{tree}}"))
    return repo, prev, main_tip, root


def _run_gate(repo: Path, tmp_path: Path, snippet: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ARGV_LOG": str(tmp_path / "argv.json")}
    return subprocess.run(["bash", "-e", "-c", snippet], cwd=str(repo), capture_output=True, text=True, env=env)


def test_gate_targets_the_deploy_ref_even_when_fetch_head_is_poisoned(tmp_path):
    repo, prev, main_tip, branch_commit = _rig(tmp_path)
    _git(repo, "update-ref", "refs/zoe-deploy/main", main_tip)
    (repo / ".git" / "FETCH_HEAD").write_text(f"{branch_commit}\t\tbranch 'fix/retire-resemblyzer' of origin\n")
    r = _run_gate(repo, tmp_path, _gate_snippet(repo))
    assert r.returncode == 0, r.stdout + r.stderr
    argv = (tmp_path / "argv.json").read_text()
    assert main_tip in argv and branch_commit not in argv, argv
    # NEGATIVE CONTROL: the pre-fix resolution (FETCH_HEAD, no ancestor check) really picks the branch commit
    (tmp_path / "argv.json").unlink()
    old = _pull_run().replace(RESOLVE, "FETCH_HEAD").replace(ANCESTOR, "true")
    r2 = _run_gate(repo, tmp_path, _gate_snippet(repo, old))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert branch_commit in (tmp_path / "argv.json").read_text()


def test_gate_refuses_a_target_that_is_not_a_descendant_of_live_head(tmp_path):
    repo, prev, main_tip, branch_commit = _rig(tmp_path)
    _git(repo, "update-ref", "refs/zoe-deploy/main", branch_commit)
    r = _run_gate(repo, tmp_path, _gate_snippet(repo))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not a descendant of the live HEAD" in r.stdout
    assert not (tmp_path / "argv.json").exists(), "the voice gate must not even run for a non-descendant"
    # NEGATIVE CONTROL: without the ancestor check the same target sails through
    weak = _pull_run().replace(ANCESTOR, "true")
    assert _run_gate(repo, tmp_path, _gate_snippet(repo, weak)).returncode == 0


# ------------------------------------------------------------------ the lander
def _lander_fetches() -> list[str]:
    code = _code(LANDER.read_text())
    found = re.findall(r"git (?:-C \S+ )?fetch [^;&|]*", code)
    # every `fetch` word in executable code must be inside a match, so a differently-formatted fetch cannot hide
    assert len(re.findall(r"(?<![-\w])fetch(?![-\w])", code)) == len(found), "a lander fetch is not covered by the pattern"
    return found


def _explicit(fetch: str) -> bool:
    return "--no-write-fetch-head" in fetch and bool(re.search(r'"\+refs/heads/[^"]+:(refs/zoe-landing/|\$LREF)', fetch))


def test_lander_fetches_use_explicit_refspecs_into_its_own_namespace():
    fetches = _lander_fetches()
    assert len(fetches) >= 3, fetches
    for f in fetches:
        assert _explicit(f), f
    # NEGATIVE CONTROL: the pre-fix forms fail the same predicate
    for old in ('git fetch -q origin "$BR"', "git fetch -q origin main", 'git -C "$WT" fetch -q origin'):
        assert not _explicit(old), old


def _run_fetch(tmp_path: Path, fetch_cmd: str) -> tuple[str, str]:
    """Execute a fetch line against a throwaway origin; return (FETCH_HEAD before, after)."""
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    (origin / "a").write_text("1\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "m")
    main_sha = _git(origin, "rev-parse", "HEAD")
    _git(origin, "checkout", "-qb", "fix/x")
    (origin / "a").write_text("2\n")
    _git(origin, "commit", "-qam", "branch")
    _git(origin, "checkout", "-q", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    fh = clone / ".git" / "FETCH_HEAD"
    fh.write_text(f"{main_sha}\t\tbranch 'main' of origin\n")
    before = fh.read_text()
    env = {**GIT_ENV, "BR": "fix/x", "LREF": "refs/zoe-landing/fix/x"}
    subprocess.run(["bash", "-c", fetch_cmd], cwd=clone, env=env, check=True, capture_output=True)
    return before, fh.read_text()


def test_lander_fetch_leaves_fetch_head_untouched(tmp_path):
    cmd = next(f for f in _lander_fetches() if "refs/heads/$BR" in f and "-C" not in f)
    before, after = _run_fetch(tmp_path, cmd)
    assert before == after
    assert _git(tmp_path / "clone", "rev-parse", "refs/zoe-landing/fix/x")


def test_negative_control_the_old_plain_fetch_clobbers_fetch_head(tmp_path):
    before, after = _run_fetch(tmp_path, 'git fetch -q origin "$BR"')
    assert before != after, "instrument check: a plain branch fetch must rewrite FETCH_HEAD"


# ------------------------------- executed: remote verification + retry loop
VERIFY = '[ "$(git rev-parse "${deploy_ref}")" = "$remote_main" ]'
LIVE_VERIFY = '[[ -z "$remote_main" || "$target" != "$remote_main" ]]'
HOOK = (  # advance origin's main just before `ls-remote` answers (a push landing mid-deploy)
    'git() { if [ "$1" = ls-remote ] && { [ -n "$ALWAYS" ] || [ ! -e "$MARK" ]; }; then touch "$MARK";'
    ' ( cd "$ORIGIN" && command git -c user.name=t -c user.email=t@e commit -q --allow-empty -m adv ); fi;'
    ' command git "$@"; }\n')


def _loop_snippet(run: str | None = None) -> str:
    lines = (run or _pull_run()).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith('deploy_ref="refs/zoe-deploy/main"'))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "done")
    return "sleep() { :; }\nAUTH_HEADER=x\n" + "\n".join(lines[start:end + 1])


def _origin_and_clone(tmp_path: Path):
    tmp_path.mkdir(exist_ok=True)
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    (origin / "a").write_text("1\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "m")
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return origin, clone


def _run_loop(tmp_path: Path, snippet: str, always: bool):
    origin, clone = _origin_and_clone(tmp_path)
    env = {**GIT_ENV, "ORIGIN": str(origin), "MARK": str(tmp_path / "mark"), "ALWAYS": "1" if always else ""}
    r = subprocess.run(["bash", "-c", HOOK + snippet], cwd=clone, env=env, capture_output=True, text=True)
    return r, origin, clone


def test_loop_retries_when_main_advances_mid_verification_then_succeeds(tmp_path):
    r, origin, clone = _run_loop(tmp_path / "real", _loop_snippet(), always=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "retrying" in r.stdout
    assert _git(clone, "rev-parse", "refs/zoe-deploy/main") == _git(origin, "rev-parse", "main")
    # NEGATIVE CONTROL: with the equality check gutted there is no retry and a STALE ref is accepted
    _, origin2, clone2 = _run_loop(tmp_path / "ctl", _loop_snippet(_pull_run().replace(VERIFY, "true")), always=False)
    assert _git(clone2, "rev-parse", "refs/zoe-deploy/main") != _git(origin2, "rev-parse", "main")


def test_loop_exits_1_with_a_clear_message_when_verification_never_matches(tmp_path):
    r, _, _ = _run_loop(tmp_path / "real", _loop_snippet(), always=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "did not match the remote's main after 5 attempts" in r.stdout
    assert r.stdout.count("retrying") == 4
    r2, _, _ = _run_loop(tmp_path / "ctl", _loop_snippet(_pull_run().replace(VERIFY, "true")), always=True)
    assert r2.returncode == 0, "negative control: with the check gutted the stale target is accepted"


def _live_snippet(clone: Path, text: str | None = None) -> str:
    lines = (text or DEPLOY_LIVE.read_text()).splitlines()
    start = next(i for i, ln in enumerate(lines) if "fetch --quiet --no-write-fetch-head" in ln)
    end = next(i for i, ln in enumerate(lines) if ln.strip().startswith('bind_flag="--expect-tree-of"'))
    return f'LIVE={clone}\nprev="$(git -C "$LIVE" rev-parse HEAD)"\n' + "\n".join(lines[start:end])


def _run_live(tmp_path: Path, text: str | None, fake_remote: str = "", ahead: bool = False):
    origin, clone = _origin_and_clone(tmp_path)
    if ahead:  # live HEAD has a commit origin/main lacks -> origin main is NOT a descendant of it
        (clone / "b").write_text("x\n")
        _git(clone, "add", "-A")
        _git(clone, "commit", "-qm", "local")
    hook = (f'git() {{ for a in "$@"; do if [ "$a" = ls-remote ]; then printf "%s\\trefs/heads/main\\n" {fake_remote}; return; fi; done;'
            ' command git "$@"; }\n') if fake_remote else ""
    return subprocess.run(["bash", "-c", "set -eu\n" + hook + _live_snippet(clone, text)],
                          cwd=clone, env=GIT_ENV, capture_output=True, text=True)


BOGUS = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def test_deploy_live_accepts_a_matching_descendant_target(tmp_path):
    r = _run_live(tmp_path / "real", None)
    assert r.returncode == 0, r.stdout + r.stderr


def test_deploy_live_refuses_a_mismatched_remote_main(tmp_path):
    r = _run_live(tmp_path / "real", None, fake_remote=BOGUS)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "!= the remote's main" in r.stderr
    weak = DEPLOY_LIVE.read_text().replace(LIVE_VERIFY, "false")
    assert weak != DEPLOY_LIVE.read_text()
    assert _run_live(tmp_path / "ctl", weak, fake_remote=BOGUS).returncode == 0, "negative control"


def test_deploy_live_refuses_a_non_descendant_target(tmp_path):
    r = _run_live(tmp_path / "real", None, ahead=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not a descendant of the live HEAD" in r.stderr
    weak = DEPLOY_LIVE.read_text().replace('merge-base --is-ancestor "$prev" "$target"', "version")
    assert weak != DEPLOY_LIVE.read_text()
    assert _run_live(tmp_path / "ctl", weak, ahead=True).returncode == 0, "negative control"
