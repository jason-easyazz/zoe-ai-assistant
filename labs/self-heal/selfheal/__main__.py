"""CLI: python3 -m selfheal {run,verify,status} --config FILE [--dispatch dry|local|full]"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Config
from .runner import Runner
from .safety import Refused


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="selfheal")
    ap.add_argument("cmd", choices=["run", "verify", "status"])
    ap.add_argument("--config", help="JSON config (see self-heal.example.json)")
    ap.add_argument("--dispatch", choices=["dry", "local", "full"])
    ap.add_argument("--engine", choices=["codex", "claude"])
    ap.add_argument("--landing", choices=["github", "local"])
    ap.add_argument("--junit", help="use this pytest junit report instead of running the detector")
    a = ap.parse_args(argv)
    cfg = Config.load(a.config, dispatch=a.dispatch, engine=a.engine, landing=a.landing)
    if a.cmd == "status":
        from pathlib import Path
        p = Path(cfg.state_dir) / "state.json"
        print(p.read_text() if p.exists() else "{}")
        return 0
    runner = Runner(cfg)
    try:
        out = runner.run_once(junit=a.junit) if a.cmd == "run" else runner.verify_pending()
    except Refused as e:
        out = {"outcome": "refused", "rule": e.rule, "detail": e.detail}
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
