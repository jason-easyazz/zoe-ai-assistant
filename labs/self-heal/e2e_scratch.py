"""End-to-end proof on a SYNTHETIC fault in a throwaway repo: never the real repo, never GitHub.

    python3 labs/self-heal/e2e_scratch.py --engine codex --scenario local       # dispatch local
    python3 labs/self-heal/e2e_scratch.py --engine codex --scenario land        # full + local landing
    python3 labs/self-heal/e2e_scratch.py --engine fake  --scenario rollback    # full + local, health fails

Prints the outcome, the wall time and the audit log. One attempt, engine hard timeout 600 s.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from conftest import FakeEngine, fix_calc, make_cfg, seed_repo, sh  # noqa: E402
from selfheal.landing import NoopApplier  # noqa: E402
from selfheal.runner import Runner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["codex", "claude", "fake"], default="codex")
    ap.add_argument("--scenario", choices=["local", "land", "rollback"], default="local")
    ap.add_argument("--codex-bin", default="codex")
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="self-heal-e2e-"))
    live = tmp / "live"
    seed_repo(live)
    repo = tmp / "repo.git"
    sh("git", "clone", "-q", "--bare", str(live), str(repo))
    cfg = make_cfg(tmp, repo=repo, live=live, engine=a.engine if a.engine != "fake" else "codex",
                   codex_bin=a.codex_bin, claude_bin=a.claude_bin, engine_timeout_s=600,
                   dispatch="local" if a.scenario == "local" else "full",
                   health_checks=[["false"]] if a.scenario == "rollback" else [])
    engine = FakeEngine(fix_calc) if a.engine == "fake" else None
    applier = NoopApplier(cfg)
    t0 = time.monotonic()
    out = Runner(cfg, engine=engine, applier=applier).run_once()
    wall = time.monotonic() - t0
    print(json.dumps({"scratch": str(tmp), "wall_s": round(wall, 1), "result": out}, indent=1, default=str))
    print("--- audit.jsonl ---")
    print((Path(cfg.state_dir) / "audit.jsonl").read_text())
    if out.get("sha"):
        print("--- heal commit diff ---")
        print(sh("git", "-C", str(repo), "show", "--stat", "--format=%H%n%B", out["sha"]))
        print(sh("git", "-C", str(repo), "show", "--format=", out["sha"], "--", "calc.py"))
    print(f"integration main = {sh('git', '-C', str(repo), 'rev-parse', 'main')}")
    print(f"applier calls = {applier.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
