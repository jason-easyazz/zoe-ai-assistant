#!/usr/bin/env python3
"""Cheap, deterministic contract evaluation for Hermes engineering prompts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from executors.kanban_adapter import KanbanAdapter  # noqa: E402


REQUIRED = {
    "scout": ("IMPLEMENTATION_REQUIRED=true|false", "kanban_complete", "SCOUT_BUDGET"),
    "implement": ("PR_URL=", "NEEDS_SPLIT=1", "IMPLEMENT_BUDGET", "kanban_block"),
    "verify": ("TESTS", "VALIDATORS", "VERIFY_BUDGET", "kanban_complete"),
    "review": ("mark-reviewed", "REVIEW=<approved or blocked>", "REVIEW_BUDGET"),
    "closeout": ("run_greploop_guard.sh", "AUDIT_ONLY=", "kanban_complete"),
    "retro": ("FOLLOW_UP_TITLE=", "RETRO=", "kanban_complete"),
}
FORBIDDEN = (
    "create all six",
    "pre-create the full chain",
    "Hermes decides the next phase",
)


def evaluate() -> dict:
    adapter = KanbanAdapter()
    scenarios = {
        "code": {
            "id": "eval-code",
            "identifier": "ZOE-EVAL",
            "title": "Small code fix",
            "description": "Change one helper with focused tests.",
        },
        "audit": {
            "id": "eval-audit",
            "identifier": "ZOE-EVAL-AUDIT",
            "title": "Audit existing behavior",
            "description": "evidence_profile: audit\nNo code changes.",
        },
    }
    checks = []
    for scenario, issue in scenarios.items():
        for phase, markers in REQUIRED.items():
            body = adapter._build_body(phase, issue, str(issue["identifier"]))
            missing = [marker for marker in markers if marker not in body]
            forbidden = [marker for marker in FORBIDDEN if marker.lower() in body.lower()]
            checks.append(
                {
                    "scenario": scenario,
                    "phase": phase,
                    "passed": not missing and not forbidden and len(body) <= 12_000,
                    "missing": missing,
                    "forbidden": forbidden,
                    "chars": len(body),
                }
            )
    passed = sum(1 for check in checks if check["passed"])
    return {
        "ok": passed == len(checks),
        "passed": passed,
        "total": len(checks),
        "pass_rate": passed / len(checks),
        "checks": checks,
    }


def main() -> int:
    report = evaluate()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
