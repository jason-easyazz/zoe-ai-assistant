#!/usr/bin/env python3
"""Probe Zoe's Graphiti graph backend without writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
sys.path.insert(0, str(DATA))

from graphiti_sidecar_probe import probe_graphiti_sidecar_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Zoe Graphiti backend readiness")
    parser.add_argument("--no-process-scan", action="store_true", help="skip ps/docker process discovery")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    result = probe_graphiti_sidecar_sync(include_process_scan=not args.no_process_scan)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"status={result['status']} ok={result['ok']} "
            f"acceptable={result['acceptable']} latency_ms={result['latency_ms']:.2f}"
        )
        if result.get("reason"):
            print(f"reason={result['reason']}")
    return 0 if result["acceptable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
