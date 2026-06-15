#!/usr/bin/env python3
"""Print Zoe Pi runtime readiness as JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from pi_runtime_probe import probe_pi_runtime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Pi runtime readiness without running Pi tasks")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    result = probe_pi_runtime().to_dict()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Pi runtime: {result['status']} ({result.get('reason') or 'ok'})")
    return 0 if result["acceptable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
