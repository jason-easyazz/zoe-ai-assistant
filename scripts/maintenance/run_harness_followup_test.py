#!/usr/bin/env python3
"""Run the exact focused test for a Zoe engineering blocker follow-up."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_TEST_BY_BLOCKER = {
    "ITERATION_BUDGET": "services/zoe-data/tests/test_main_multica_poll.py::test_record_blocked_multica_chain_creates_iteration_budget_followup",
    "IMPLEMENT_BUDGET": "services/zoe-data/tests/test_main_multica_poll.py::test_record_blocked_multica_chain_creates_budget_followup_once",
    "IMPLEMENT_HANDOFF_DRIFT": "services/zoe-data/tests/test_main_multica_poll.py::test_record_blocked_multica_chain_creates_budget_followup_once",
    "PROTOCOL_VIOLATION": "services/zoe-data/tests/test_main_multica_poll.py::test_record_blocked_multica_chain_creates_protocol_followup",
}
_DEFAULT_TEST = "services/zoe-data/tests/test_main_multica_poll.py"


def test_target_for_blocker(blocker: str) -> str:
    return _TEST_BY_BLOCKER.get((blocker or "").strip().upper(), _DEFAULT_TEST)


def run(blocker: str, *, cwd: Path | None = None) -> int:
    root = cwd or Path.cwd()
    target = test_target_for_blocker(blocker)
    env = dict(os.environ)
    env["PYTHONPATH"] = "services/zoe-data"
    cmd = [sys.executable, "-m", "pytest", "-q", target]
    print("Running focused harness follow-up test:", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=root, env=env).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blocker", nargs="?", default="", help="Source blocker token, e.g. IMPLEMENT_BUDGET")
    args = parser.parse_args(argv)
    return run(args.blocker)


if __name__ == "__main__":
    raise SystemExit(main())
