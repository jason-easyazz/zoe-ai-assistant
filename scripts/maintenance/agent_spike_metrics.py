#!/usr/bin/env python3
"""Create repeatable metrics packets for local-agent spike evaluations.

The script is intentionally non-installing: it records availability and the test
plan for Forge, Caveman, Babysitter, or local-model experiments without making
those projects production dependencies.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATES = {
    "forge": {
        "role": "local model reliability middleware",
        "cost_policy": "Only use after native evidence gates exist; prefer local/free models.",
        "signals": ["tool-call recovery rate", "retry count", "invalid JSON repairs", "wall time"],
        "commands": ["python3 -m pytest services/zoe-data/tests/test_pipeline_evidence.py -q"],
    },
    "caveman": {
        "role": "token compression experiment",
        "cost_policy": "Use only to reduce agent output/context tokens; never as authority.",
        "signals": ["input tokens", "output tokens", "compression ratio", "lost requirements"],
        "commands": ["python3 tools/audit/validate_structure.py"],
    },
    "babysitter": {
        "role": "workflow/event-journal design reference",
        "cost_policy": "Borrow process patterns; do not install as Zoe's production orchestrator.",
        "signals": ["resume fidelity", "breakpoint quality", "event journal completeness"],
        "commands": ["python3 -m pytest services/zoe-data/tests/test_kanban_adapter.py -q"],
    },
    "local-model": {
        "role": "Spark-fit overnight model simulation",
        "cost_policy": "Prefer local/free overnight execution; latency is secondary.",
        "signals": ["pass rate", "tokens/sec", "first token latency", "cost", "evidence completeness"],
        "commands": ["curl -sf http://127.0.0.1:11434/health"],
    },
}


def tool_available(candidate: str) -> bool:
    command = {
        "forge": "forge",
        "caveman": "caveman",
        "babysitter": "babysitter",
        "local-model": "llama-server",
    }.get(candidate)
    if command is None:
        return False
    return shutil.which(command) is not None


def build_packet(candidate: str, *, task: str, pr_url: str | None = None) -> dict[str, Any]:
    if candidate not in CANDIDATES:
        raise ValueError(f"unknown candidate: {candidate}")
    spec = CANDIDATES[candidate]
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate": candidate,
        "task": task,
        "pr_url": pr_url,
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "available": tool_available(candidate),
        "role": spec["role"],
        "cost_policy": spec["cost_policy"],
        "signals": spec["signals"],
        "commands": spec["commands"],
        "required_evidence": [
            "commands_run",
            "pass_fail_result",
            "cost_or_locality_observation",
            "gotchas",
            "recommendation",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", choices=sorted(CANDIDATES))
    parser.add_argument("--task", required=True, help="Task or scenario being evaluated")
    parser.add_argument("--pr-url", default=None, help="Related PR URL, if any")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()

    packet = build_packet(args.candidate, task=args.task, pr_url=args.pr_url)
    text = json.dumps(packet, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

