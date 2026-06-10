#!/usr/bin/env python3
"""Run Zoe's offline Graphify probe across bounded repo shards.

Full-repo local Graphify currently exceeds Zoe's reliable structured-output
budget. This observe-only wrapper keeps each extraction scoped, records compact
per-shard evidence, and never syncs generated graph output back to the repo.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from graphify_local_model_matrix import compact_probe_result  # noqa: E402
from graphify_local_probe import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_GRAPHIFY_BIN,
    DEFAULT_ROOT,
    GraphifyLocalProbeConfig,
    run_probe,
    utc_now,
    validate_scope_path,
)

DEFAULT_SHARD_MODEL = "gemma-4-e4b-it-Q4_K_M.gguf"
DEFAULT_SHARDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data-core", ("services/zoe-data",)),
    ("operators", ("scripts", "tools")),
)


@dataclass(frozen=True)
class GraphifyShard:
    name: str
    include_paths: tuple[str, ...]


def parse_shard(value: str) -> GraphifyShard:
    if "=" not in value:
        raise ValueError("shards must use name=path[,path] format")
    name, raw_paths = value.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError("shard name is required")
    paths = tuple(path.strip() for path in raw_paths.split(",") if path.strip())
    if not paths:
        raise ValueError(f"shard {name!r} requires at least one path")
    for path in paths:
        validate_scope_path(path)
    return GraphifyShard(name=name, include_paths=paths)


def default_shards() -> tuple[GraphifyShard, ...]:
    return tuple(GraphifyShard(name, paths) for name, paths in DEFAULT_SHARDS)


def compact_shard_result(shard: GraphifyShard, status: dict[str, object]) -> dict[str, object]:
    compact = compact_probe_result(status)
    compact["shard"] = shard.name
    compact["include_paths"] = list(shard.include_paths)
    return compact


def summarize_shards(results: Sequence[dict[str, object]]) -> dict[str, object]:
    accepted = [result for result in results if result.get("accepted")]
    durations = [result["duration_ms"] for result in accepted if isinstance(result.get("duration_ms"), int)]
    rss_values = [result["child_max_rss_kb"] for result in results if isinstance(result.get("child_max_rss_kb"), int)]
    blocked = [result["shard"] for result in results if not result.get("accepted")]
    return {
        "shard_count": len(results),
        "accepted_count": len(accepted),
        "rejected_count": len(results) - len(accepted),
        "all_accepted": len(accepted) == len(results) and bool(results),
        "any_accepted": bool(accepted),
        "blocked_shards": blocked,
        "median_accepted_duration_ms": median(durations) if durations else None,
        "max_observed_rss_kb": max(rss_values) if rss_values else None,
        "results": list(results),
    }


def run_shard_matrix(
    *,
    root: Path = DEFAULT_ROOT,
    graphify_bin: Path = DEFAULT_GRAPHIFY_BIN,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_SHARD_MODEL,
    ref: str = "origin/main",
    shards: Sequence[GraphifyShard] = (),
    timeout_sec: int = 600,
    cluster: bool = False,
) -> dict[str, object]:
    selected = tuple(shards or default_shards())
    started_at = utc_now()
    compact_results: list[dict[str, object]] = []
    for shard in selected:
        config = GraphifyLocalProbeConfig(
            root=root,
            graphify_bin=graphify_bin,
            base_url=base_url,
            model=model,
            ref=ref,
            mode="scope",
            timeout_sec=timeout_sec,
            cluster=cluster,
            keep_workdir=False,
            include_paths=shard.include_paths,
        )
        compact_results.append(compact_shard_result(shard, run_probe(config)))
    summary = summarize_shards(compact_results)
    return {
        "schema_version": 1,
        "probe": "graphify_local_shard_matrix",
        "started_at": started_at,
        "ended_at": utc_now(),
        "mode": "scope",
        "ref": ref,
        "base_url": base_url,
        "model": model,
        "cluster": cluster,
        **summary,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Zoe's offline Graphify probe across bounded repo shards.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--graphify-bin", type=Path, default=DEFAULT_GRAPHIFY_BIN)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_SHARD_MODEL)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--timeout-sec", type=int, default=600)
    parser.add_argument("--cluster", action="store_true")
    parser.add_argument("--shard", action="append", default=[], help="Shard spec as name=path[,path]. May be repeated.")
    parser.add_argument("--allow-partial", action="store_true", help="Exit 0 when at least one shard is accepted.")
    parser.add_argument("--status-json", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        shards = tuple(parse_shard(value) for value in args.shard) if args.shard else default_shards()
        status = run_shard_matrix(
            root=args.root,
            graphify_bin=args.graphify_bin,
            base_url=args.base_url,
            model=args.model,
            ref=args.ref,
            shards=shards,
            timeout_sec=args.timeout_sec,
            cluster=args.cluster,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(status, indent=2, sort_keys=True)
    if args.status_json:
        args.status_json.parent.mkdir(parents=True, exist_ok=True)
        args.status_json.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    if status.get("all_accepted"):
        return 0
    if args.allow_partial and status.get("any_accepted"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
