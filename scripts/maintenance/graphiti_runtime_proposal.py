#!/usr/bin/env python3
"""Build a review-only Zoe proposal for a Graphiti runtime trial."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
sys.path.insert(0, str(DATA))

from graphiti_runtime_probe import probe_graphiti_runtime_sync  # noqa: E402
from zoe_evolution_runtime_intake import build_graphiti_runtime_trial_proposal_intake  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an inert Graphiti runtime trial proposal")
    parser.add_argument("--proposal-id", default="prop_graphiti_runtime_trial")
    parser.add_argument("--user-id")
    parser.add_argument("--no-process-scan", action="store_true", help="skip ps/docker process discovery")
    parser.add_argument("--legacy-row", action="store_true", help="Print only the legacy proposal row")
    args = parser.parse_args()

    probe = probe_graphiti_runtime_sync(include_process_scan=not args.no_process_scan)
    intake = build_graphiti_runtime_trial_proposal_intake(
        proposal_id=args.proposal_id,
        user_id=args.user_id,
        runtime_probe_result=probe,
    )
    payload = intake.to_legacy_row() if args.legacy_row else {
        "probe": probe,
        "proposal": intake.to_legacy_row(),
        "multica_payload": intake.multica_payload,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
