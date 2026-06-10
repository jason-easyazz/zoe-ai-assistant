#!/usr/bin/env python3
"""Build a review-only Zoe proposal for Pi runtime adoption."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_runtime_probe import probe_pi_runtime  # noqa: E402
from zoe_evolution_runtime_intake import build_pi_runtime_install_proposal_intake  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an inert Pi runtime adoption proposal")
    parser.add_argument("--proposal-id", default="prop_pi_runtime_install")
    parser.add_argument("--user-id")
    parser.add_argument("--legacy-row", action="store_true", help="Print only the legacy proposal row")
    args = parser.parse_args()

    probe = probe_pi_runtime().to_dict()
    intake = build_pi_runtime_install_proposal_intake(
        proposal_id=args.proposal_id,
        user_id=args.user_id,
        probe_result=probe,
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
