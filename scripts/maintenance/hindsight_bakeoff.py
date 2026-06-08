#!/usr/bin/env python3
"""Run Zoe's Hindsight bake-off against a configured sidecar.

This script is intentionally read/measure-oriented. It retains only the synthetic
bake-off events and requires HINDSIGHT_ENABLED=true plus explicit
--retain-synthetic to write to Hindsight.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))

from hindsight_bakeoff import EVAL_QUERIES, SYNTHETIC_EVENTS, score_recall_response  # noqa: E402
from hindsight_memory import HindsightConfig, HindsightMemoryClient  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Zoe Hindsight synthetic bake-off")
    parser.add_argument("--retain-synthetic", action="store_true", help="write synthetic events before recall")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    client = HindsightMemoryClient(HindsightConfig.from_env())
    output: dict[str, object] = {"status": client.enabled_status(), "retained": [], "scores": []}

    if args.retain_synthetic:
        retained = []
        for event in SYNTHETIC_EVENTS:
            retained.append(await client.retain_event(event, allow_auto=True))
        output["retained"] = retained

    scores = []
    for query in EVAL_QUERIES:
        response = await client.recall(
            user_id=query.user_id,
            scope=query.scope,
            query=query.query,
            budget=query.budget,
            max_tokens=1024,
        )
        scores.append(score_recall_response(response, query))
    output["scores"] = scores

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(json.dumps(output["status"], indent=2, sort_keys=True))
        for item in scores:
            print(f"{item['name']}: score={item['score']:.2f} missing={item['missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
