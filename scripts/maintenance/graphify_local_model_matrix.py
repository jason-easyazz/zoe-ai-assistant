#!/usr/bin/env python3
"""Run Zoe's offline Graphify probe across local model candidates.

This wrapper is observe-only. It delegates every extraction to
graphify_local_probe.run_probe(), which strips cloud API keys and runs in a
fixture, scoped copy, or detached worktree. The matrix exists to answer whether
Zoe's local models are viable for Graphify before changing the sync-capable
refresh lane.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from graphify_local_probe import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_GRAPHIFY_BIN,
    DEFAULT_MODEL,
    DEFAULT_ROOT,
    GraphifyLocalProbeConfig,
    run_probe,
    utc_now,
)

DEFAULT_MATRIX_MODELS = (
    DEFAULT_MODEL,
    "gemma-4-e4b-it-Q4_K_M.gguf",
    "gemma-4-12B-it-Q4_K_M.gguf",
)


def _duration_ms(status: dict[str, object]) -> int | None:
    evidence = status.get("command_evidence")
    if not isinstance(evidence, dict):
        return None
    total = 0
    found = False
    for value in evidence.values():
        if not isinstance(value, dict):
            continue
        duration = value.get("duration_ms")
        if isinstance(duration, int):
            total += duration
            found = True
    return total if found else None


def _max_rss_kb(status: dict[str, object]) -> int | None:
    evidence = status.get("command_evidence")
    if not isinstance(evidence, dict):
        return None
    values: list[int] = []
    for value in evidence.values():
        if not isinstance(value, dict):
            continue
        rss = value.get("child_max_rss_kb")
        if isinstance(rss, int):
            values.append(rss)
    return max(values) if values else None


def compact_probe_result(status: dict[str, object]) -> dict[str, object]:
    metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else {}
    model_fit = status.get("model_fit") if isinstance(status.get("model_fit"), dict) else {}
    return {
        "model": status.get("model"),
        "mode": status.get("mode"),
        "accepted": bool(status.get("accepted")),
        "blockers": list(status.get("blockers") or []),
        "warnings": list(status.get("warnings") or []),
        "duration_ms": _duration_ms(status),
        "child_max_rss_kb": _max_rss_kb(status),
        "nodes": metrics.get("nodes"),
        "edges": metrics.get("edges"),
        "code_files": metrics.get("code_files"),
        "doc_files": metrics.get("doc_files"),
        "invalid_json_chunks": metrics.get("invalid_json_chunks"),
        "context_splits": metrics.get("context_splits"),
        "truncated_chunks": metrics.get("truncated_chunks"),
        "model_file_exists": bool(model_fit.get("model_file_exists")),
        "model_file": model_fit.get("model_file"),
        "model_file_bytes": model_fit.get("model_file_bytes"),
        "base_url_localhost": bool(model_fit.get("base_url_localhost")),
        "offline_cloud_keys_scrubbed": bool(model_fit.get("offline_cloud_keys_scrubbed")),
    }


def summarize_matrix(results: Sequence[dict[str, object]]) -> dict[str, object]:
    compact = [compact_probe_result(result) for result in results]
    accepted = [result for result in compact if result["accepted"]]
    durations = [result["duration_ms"] for result in accepted if isinstance(result.get("duration_ms"), int)]
    rss_values = [result["child_max_rss_kb"] for result in compact if isinstance(result.get("child_max_rss_kb"), int)]
    best = None
    if accepted:
        best = min(
            accepted,
            key=lambda item: item["duration_ms"] if isinstance(item.get("duration_ms"), int) else 10**18,
        )["model"]
    return {
        "model_count": len(compact),
        "accepted_count": len(accepted),
        "rejected_count": len(compact) - len(accepted),
        "all_accepted": len(accepted) == len(compact) and bool(compact),
        "any_accepted": bool(accepted),
        "best_accepted_model_by_duration": best,
        "median_duration_ms": median(durations) if durations else None,
        "max_child_rss_kb": max(rss_values) if rss_values else None,
        "results": compact,
    }


def run_model_matrix(
    *,
    root: Path = DEFAULT_ROOT,
    graphify_bin: Path = DEFAULT_GRAPHIFY_BIN,
    base_url: str = DEFAULT_BASE_URL,
    models: Sequence[str] = DEFAULT_MATRIX_MODELS,
    ref: str = "origin/main",
    mode: str = "smoke",
    timeout_sec: int = 180,
    cluster: bool = False,
    include_paths: Sequence[str] = (),
    allow_repo: bool = False,
) -> dict[str, object]:
    if mode == "repo" and not allow_repo:
        raise ValueError("repo mode is long-running; pass --allow-repo to run a full-repo matrix")
    started_at = utc_now()
    results: list[dict[str, object]] = []
    for model in models:
        config = GraphifyLocalProbeConfig(
            root=root,
            graphify_bin=graphify_bin,
            base_url=base_url,
            model=model,
            ref=ref,
            mode=mode,
            timeout_sec=timeout_sec,
            cluster=cluster,
            keep_workdir=False,
            include_paths=tuple(include_paths),
        )
        results.append(run_probe(config))
    summary = summarize_matrix(results)
    return {
        "schema_version": 1,
        "probe": "graphify_local_model_matrix",
        "started_at": started_at,
        "ended_at": utc_now(),
        "mode": mode,
        "ref": ref,
        "base_url": base_url,
        "include_paths": list(include_paths),
        "cluster": cluster,
        **summary,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Zoe's offline Graphify probe across local model candidates.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--graphify-bin", type=Path, default=DEFAULT_GRAPHIFY_BIN)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", action="append", dest="models", help="Local model name to test. May be repeated.")
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--mode", choices=("smoke", "scope", "repo"), default="smoke")
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--cluster", action="store_true")
    parser.add_argument("--include-path", action="append", default=[], help="Relative path to copy for scope mode. May be repeated.")
    parser.add_argument("--allow-repo", action="store_true", help="Allow long-running full-repo matrix mode.")
    parser.add_argument("--allow-partial", action="store_true", help="Exit 0 when at least one model is accepted.")
    parser.add_argument("--status-json", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        status = run_model_matrix(
            root=args.root,
            graphify_bin=args.graphify_bin,
            base_url=args.base_url,
            models=tuple(args.models or DEFAULT_MATRIX_MODELS),
            ref=args.ref,
            mode=args.mode,
            timeout_sec=args.timeout_sec,
            cluster=args.cluster,
            include_paths=tuple(args.include_path),
            allow_repo=args.allow_repo,
        )
    except ValueError as exc:
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
