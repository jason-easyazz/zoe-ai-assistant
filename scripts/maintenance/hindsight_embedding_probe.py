#!/usr/bin/env python3
"""Probe Zoe's offline Hindsight embedding dependency without writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
sys.path.insert(0, str(DATA))

from hindsight_embedding_probe import probe_hindsight_embeddings_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Zoe Hindsight embedding readiness")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    result = probe_hindsight_embeddings_sync()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"status={result['status']} ok={result['ok']} acceptable={result['acceptable']} "
            f"provider={result['provider']} model={result['model']} latency_ms={result['latency_ms']:.2f}"
        )
        if result.get("base_url"):
            print(f"base_url={result['base_url']}")
        if result.get("reason"):
            print(f"reason={result['reason']}")
    return 0 if result["acceptable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
