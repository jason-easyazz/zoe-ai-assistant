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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "services" / "zoe-data"
sys.path.insert(0, str(DATA))

from hindsight_bakeoff import (  # noqa: E402
    eval_queries_for_user,
    score_recall_response,
    summarize_bakeoff_scores,
    summarize_recall_latency,
    synthetic_events_for_user,
)
from hindsight_memory import HindsightConfig, HindsightMemoryClient  # noqa: E402


def _user_id_arg(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise argparse.ArgumentTypeError("user_id must not be blank")
    return normalized


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Zoe Hindsight synthetic bake-off")
    parser.add_argument("--retain-synthetic", action="store_true", help="write synthetic events before recall")
    parser.add_argument("--no-wait-retain", action="store_true", help="do not wait for async retain operations before recall")
    parser.add_argument("--retain-timeout-seconds", type=float, default=180.0, help="max seconds to wait for each async retain operation")
    parser.add_argument("--retain-poll-seconds", type=float, default=1.0, help="seconds between async retain status polls")
    parser.add_argument("--recall-latency-budget-ms", type=float, default=600.0, help="p95 recall latency budget for hot-path eligibility")
    parser.add_argument("--user-id", type=_user_id_arg, help="override the synthetic bake-off user_id for multi-user runs")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    client = HindsightMemoryClient(HindsightConfig.from_env())
    synthetic_events = synthetic_events_for_user(args.user_id)
    eval_queries = eval_queries_for_user(args.user_id)
    effective_user_id = eval_queries[0].user_id
    output: dict[str, object] = {
        "status": client.enabled_status(),
        "user_id": effective_user_id,
        "retained": [],
        "operations": [],
        "scores": [],
        "summary": {},
    }

    if args.retain_synthetic:
        retained = []
        for event in synthetic_events:
            retained.append(await client.retain_event(event, allow_auto=True))
        output["retained"] = retained
        if not args.no_wait_retain:
            output["operations"] = await client.wait_for_retain_results(
                retained,
                timeout_seconds=args.retain_timeout_seconds,
                poll_seconds=args.retain_poll_seconds,
            )

    scores = []
    for query in eval_queries:
        started = time.perf_counter()
        response = await client.recall(
            user_id=query.user_id,
            scope=query.scope,
            query=query.query,
            budget=query.budget,
            max_tokens=1024,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        score = score_recall_response(response, query)
        score["latency_ms"] = latency_ms
        score["bank_id"] = response.get("bank_id")
        score["enabled"] = response.get("enabled", client.config.enabled)
        score["latency_budget_ms"] = args.recall_latency_budget_ms
        score["within_latency_budget"] = bool(score["enabled"]) and latency_ms <= args.recall_latency_budget_ms
        score["reason"] = response.get("reason")
        scores.append(score)
    output["scores"] = scores
    output["summary"] = summarize_bakeoff_scores(scores)
    output["latency"] = summarize_recall_latency(scores, budget_ms=args.recall_latency_budget_ms)

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(json.dumps(output["status"], indent=2, sort_keys=True))
        print(json.dumps(output["summary"], indent=2, sort_keys=True))
        print(json.dumps(output["latency"], indent=2, sort_keys=True))
        for item in scores:
            print(f"{item['name']}: score={item['score']:.2f} latency_ms={item['latency_ms']:.2f} missing={item['missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
