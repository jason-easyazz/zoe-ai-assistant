"""The voice lander's RE-ARM reuses probe evidence only where the checker allows it (2026-11-11).

land_voice_pr.sh probes head P, then - before this change - exited 12 (a fresh 20-30 min probe)
whenever `strict` made the PR go BEHIND, even if what came in from main was two zoe-auth files.
Now its post-probe loop updates the branch and `rebind`s to the new head ONLY when
landing_evidence_reusable (-> voice_gate_check.py --allow-reuse) accepts the probe of P there.

These tests EXECUTE the real post-probe section of land_voice_pr.sh (from `PROBED_SHA=` to the
end) against a throwaway origin + clone and the fake `gh` (landing_fakes.py), with the REAL
checker and a synthetic artifact. Synthetic data only.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.unit.landing_fakes import LANDING, NEEDS_TOOLS, REPO, Rig

pytestmark = [pytest.mark.ci_safe, NEEDS_TOOLS]

CHECKER = REPO / "scripts" / "maintenance" / "voice_gate_check.py"
GENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def g(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=GENV).stdout.strip()


def put(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)


def ci(repo: Path, msg: str) -> str:
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", msg)
    return g(repo, "rev-parse", "HEAD")


class World:
    def __init__(self, tmp: Path):
        self.rig = Rig(tmp)
        self.origin = tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True, env=GENV)
        self.clone = tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True, env=GENV,
                       capture_output=True)
        put(self.clone, "services/zoe-data/fast_tiers.py", "TIER = 1\n")
        put(self.clone, "services/zoe-auth/requirements.txt", "fastapi\n")
        self.m0 = ci(self.clone, "m0")
        g(self.clone, "push", "-q", "origin", "HEAD:refs/heads/main")
        g(self.clone, "checkout", "-q", "-b", "feat")
        put(self.clone, "services/zoe-data/fast_tiers.py", "TIER = 2\n")
        self.p = ci(self.clone, "voice change")
        g(self.clone, "push", "-q", "origin", "feat")
        self.h_ok = self._updated("services/zoe-auth/requirements.txt", "fastapi\nuvicorn\n", "ok")
        self.h_voice = self._updated("services/zoe-data/new_module.py", "X = 1\n", "voice")
        self.artifact = tmp / "voice_regression_last.json"
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
        self.artifact.write_text(json.dumps({
            "status": "pass", "timestamp": ts, "created_at": ts, "reason": "", "summary": {"n_samples": 20},
            "revision": {"commit": self.p, "tree": g(self.clone, "rev-parse", f"{self.p}^{{tree}}"),
                         "dirty": False, "clean_verified": True, "service_dir": "/x"}}))

    def _updated(self, rel: str, text: str, tag: str) -> str:
        """A main commit touching `rel`, and the PR branch updated with it by a merge (what
        `gh pr update-branch` makes). Both pushed to a side ref; feat itself stays at P."""
        g(self.clone, "checkout", "-q", "-b", f"main-{tag}", self.m0)
        put(self.clone, rel, text)
        m1 = ci(self.clone, f"main moves ({tag})")
        g(self.clone, "checkout", "-q", "-b", f"upd-{tag}", self.p)
        g(self.clone, "merge", "-q", "--no-ff", "-m", "Merge main", m1)
        h = g(self.clone, "rev-parse", "HEAD")
        g(self.clone, "push", "-q", "origin", f"{h}:refs/side/{tag}")
        g(self.clone, "checkout", "-q", "feat")
        return h

    def moves_to(self, sha: str) -> str:
        return f"git -C {self.origin} update-ref refs/heads/feat {sha}"

    def run(self, **extra: str) -> subprocess.CompletedProcess:
        code = (LANDING / "land_voice_pr.sh").read_text()
        tail = code[code.index("PROBED_SHA=$HEAD_SHA"):]
        script = (f'. "{LANDING}/landing_lib.sh"\nset -u\n'
                  f'PR=7; BR=feat; WT="{self.clone}"; LREF=refs/zoe-landing/feat; HEAD_SHA={self.p}; ARMED=0\n'
                  'log(){ echo "[t] #$PR $*"; }\nleft(){ echo 99999; }\n' + tail)
        env = self.rig.env(**{"ZOE_LANDING_REPO": str(self.clone), "ZOE_VOICE_ARTIFACT": str(self.artifact),
                              "ZOE_VOICE_BASELINE": str(self.rig.tmp / "no-baseline.json"),
                              "ZOE_LANDING_GATE_CHECK": str(CHECKER), "ZOE_LANDING_UPDATE_POLL_S": "0",
                              "ZOE_LANDING_WAIT_STEP_S": "0", "ZOE_LANDING_VOICE_HOLD_SWEEP": "0",
                              **GENV_KEYS, **extra})
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def arms(self) -> list[str]:
        return [c for c in self.rig.gh_calls() if c.startswith("pr merge 7 --squash --auto")]


GENV_KEYS = {k: GENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                   "GIT_COMMITTER_EMAIL", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def behind_then(world: World, sha: str, **pr) -> None:
    world.rig.set_state({"threads": {}, "prs": {"7": {
        "headRefOid": world.p, "mergeStateStatus": "BEHIND", "merge_on_arm": True,
        "on_update": {"headRefOid": sha, "mergeStateStatus": "CLEAN", "_shell": world.moves_to(sha)}, **pr}}})


def test_behind_with_a_non_voice_update_is_rebound_and_armed_at_the_new_head(world):
    behind_then(world, world.h_ok)
    r = world.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REUSED at" in r.stdout and "evidence reuse ACCEPTED" in r.stdout, r.stdout
    assert world.arms() == [f"pr merge 7 --squash --auto --match-head-commit {world.h_ok}"], world.rig.gh_calls()
    assert any(c.startswith("pr update-branch 7") for c in world.rig.gh_calls())


def test_behind_with_a_voice_update_is_never_armed_and_goes_back_for_a_probe(world):
    behind_then(world, world.h_voice)
    r = world.run()
    assert r.returncode == 12, r.stdout + r.stderr
    assert "NOT reusable" in r.stdout and "new_module.py" in r.stdout, r.stdout
    assert world.arms() == []


def test_negative_control_without_the_reuse_check_a_voice_update_would_be_armed(world):
    """The refusal above is landing_evidence_reusable's doing: stub it to 'yes' and the same
    voice update gets armed. (So the test would go red if the lander stopped asking.)"""
    behind_then(world, world.h_voice)
    code = (LANDING / "land_voice_pr.sh").read_text()
    assert "landing_evidence_reusable" in code[code.index("rebind(){"):code.index("while :; do")]
    (world.rig.tmp / "always_yes.py").write_text(
        "import sys\n"
        f"print('{world.p}' if '--print-artifact-commit' in sys.argv else 'voice-gate: OK - stub')\n")
    r = world.run(ZOE_LANDING_GATE_CHECK=str(world.rig.tmp / "always_yes.py"))
    assert r.returncode == 0 and world.arms() == [f"pr merge 7 --squash --auto --match-head-commit {world.h_voice}"], r.stdout


def test_a_non_voice_push_on_top_of_the_probed_head_is_rebound(world):
    g(world.clone, "checkout", "-q", "feat")
    put(world.clone, "services/zoe-auth/requirements.txt", "fastapi\nhttpx\n")
    pushed = ci(world.clone, "follow-up, non-voice")
    g(world.clone, "push", "-q", "origin", "feat")
    world.rig.set_state({"threads": {}, "prs": {"7": {"headRefOid": pushed, "mergeStateStatus": "CLEAN",
                                                       "merge_on_arm": True}}})
    r = world.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert world.arms() == [f"pr merge 7 --squash --auto --match-head-commit {pushed}"]


def test_the_rebind_cap_ends_the_loop_with_a_fresh_probe(world):
    behind_then(world, world.h_ok)
    r = world.run(ZOE_LANDING_REBIND_MAX="0")
    assert r.returncode == 12 and "rebind cap (0)" in r.stdout, r.stdout
    assert world.arms() == []


def test_an_overwritten_artifact_slot_is_not_this_probes_evidence(world):
    art = json.loads(world.artifact.read_text())
    art["revision"]["commit"] = world.m0
    world.artifact.write_text(json.dumps(art))
    behind_then(world, world.h_ok)
    r = world.run()
    assert r.returncode == 12 and "no longer records the probed" in r.stdout, r.stdout
    assert world.arms() == []


def test_unchanged_head_and_clean_state_arms_the_probed_head_exactly(world):
    world.rig.set_state({"threads": {}, "prs": {"7": {"headRefOid": world.p, "mergeStateStatus": "CLEAN",
                                                       "merge_on_arm": True}}})
    r = world.run()
    assert r.returncode == 0 and "REUSED" not in r.stdout, r.stdout
    assert world.arms() == [f"pr merge 7 --squash --auto --match-head-commit {world.p}"]


def test_dirty_is_still_exit_12_with_no_update():
    code = (LANDING / "land_voice_pr.sh").read_text()
    dirty = next(ln for ln in code.splitlines() if ln.strip().startswith("DIRTY)"))
    assert "exit 12" in dirty and "update-branch" not in dirty
