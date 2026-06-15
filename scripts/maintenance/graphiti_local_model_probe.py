#!/usr/bin/env python3
"""Run Zoe's explicit local-model structured-output probe for Graphiti."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
sys.path.insert(0, str(DATA))

from graphiti_local_model_probe import probe_graphiti_local_model_contract_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local structured-output readiness for Graphiti")
    parser.add_argument("--run", action="store_true", help="actually call the configured local model endpoint")
    args = parser.parse_args()

    result = probe_graphiti_local_model_contract_sync(run=args.run)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.run and not result.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
