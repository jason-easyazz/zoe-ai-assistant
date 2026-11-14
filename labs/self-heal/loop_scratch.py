"""The complete loop on a SYNTHETIC fault, in scratch repos the script creates under a temp directory:
never this project's repo or any of its worktrees, never GitHub, never a real engine.

    python3 labs/self-heal/loop_scratch.py --scenario good                    # sold unit: no remote at all
    python3 labs/self-heal/loop_scratch.py --scenario retry --shape this_unit # also PRINTS the PR commands
    python3 labs/self-heal/loop_scratch.py --scenario rollback                # main moves; the merged tree fails
    python3 labs/self-heal/loop_scratch.py --engine dryrun-codex              # prints the CLI command, runs nothing

Prints ONE JSON object: the loop result with the measures (fault_to_verified_fix_s, attempts, rollbacks,
refusals_by_reason), the ledger and where the scratch lives.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from canned import CANNED, FAULT_JUNIT, SCENARIOS  # noqa: E402
from conftest import make_cfg, seed_repo, sh  # noqa: E402
from selfheal import gitops  # noqa: E402
from selfheal.ledger import Ledger  # noqa: E402
from selfheal.locallander import SHAPES  # noqa: E402
from selfheal.loop import HealLoop  # noqa: E402
from selfheal.proposer import DryRunProposer, ScriptedProposer  # noqa: E402

LEGACY = "from calc import add\n\n\ndef test_legacy():\n    assert add(5, 3) == 2\n"


def build(tmp: Path, shape: str):
    """A seeded 'product' repo and the bare repo that holds the local `main` (no remote for a sold unit)."""
    live, repo = tmp / "live", tmp / "repo.git"
    seed_repo(live)
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    if shape == "sold_unit":
        sh("git", "-C", str(repo), "remote", "remove", "origin")
    (tmp / "fault.xml").write_text(FAULT_JUNIT)
    return make_cfg(tmp, repo=repo, live=live, dispatch="full", unit_shape=shape), str(tmp / "fault.xml")


def move_main(cfg, files: dict) -> None:
    wt = str(Path(cfg.worktree_root) / "advance")
    gitops.add_worktree(cfg.repo, wt, base="main")
    try:
        for rel, text in files.items():
            (Path(wt) / rel).write_text(text)
        gitops.git(wt, "add", "-A")
        gitops.git(cfg.repo, "update-ref", "refs/heads/main", gitops.commit(wt, "another landing"))
    finally:
        gitops.remove_worktree(cfg.repo, wt)


def main(argv=None, now=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=[*SCENARIOS, "rollback"], default="good")
    ap.add_argument("--engine", choices=["fake", "dryrun-codex", "dryrun-claude"], default="fake")
    ap.add_argument("--shape", choices=sorted(SHAPES), default="sold_unit")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="self-heal-loop-"))
    try:
        cfg, junit = build(tmp, a.shape)
        if a.engine != "fake":
            engine = DryRunProposer(cfg, a.engine.split("-")[1])
        elif a.scenario == "rollback":   # a correct fix; meanwhile main gains a test that relies on the bug
            engine = ScriptedProposer(lambda p: (move_main(cfg, {"tests/test_legacy.py": LEGACY}), CANNED["good"](p))[1])
        else:
            engine = ScriptedProposer(*[CANNED[s] for s in SCENARIOS[a.scenario]])
        result = HealLoop(cfg, engine, now=now).run(junit)
        result.update(scenario=a.scenario, scratch=str(tmp) if a.keep else "(removed)",
                      main=gitops.rev(cfg.repo, "main"),
                      ledger_events=[[e["event"], e["reason"]] for e in Ledger(str(Path(cfg.state_dir) / "ledger.jsonl")).entries()])
        print(json.dumps(result, indent=1, sort_keys=True))
        return 0
    finally:
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
