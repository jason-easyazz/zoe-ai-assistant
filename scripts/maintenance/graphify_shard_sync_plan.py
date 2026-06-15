#!/usr/bin/env python3
"""Build a no-write sync plan from Zoe Graphify shard evidence.

The local shard matrix is observe-only today: it proves bounded shards can be
extracted locally, but it intentionally discards generated artifacts. This
planner preserves that safety boundary. It validates the evidence packet and
emits the guarded next steps required before any future sharded graph sync can
replace committed `graphify-out` files.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

REQUIRED_PROBE = "graphify_local_shard_matrix"
REQUIRED_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ShardSyncPlan:
    accepted: bool
    status: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: dict[str, Any]
    required_next_steps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "summary": self.summary,
            "required_next_steps": list(self.required_next_steps),
        }


def build_shard_sync_plan(status: Mapping[str, Any]) -> ShardSyncPlan:
    blockers: list[str] = []
    warnings: list[str] = []

    if status.get("schema_version") != REQUIRED_SCHEMA_VERSION:
        blockers.append("unsupported_schema_version")
    if status.get("probe") != REQUIRED_PROBE:
        blockers.append("unexpected_probe_type")
    if status.get("all_accepted") is not True:
        blockers.append("not_all_shards_accepted")
    if int(status.get("rejected_count") or 0) != 0:
        blockers.append("rejected_shards_present")
    if status.get("blocked_shards"):
        blockers.append("blocked_shards_present")

    results = status.get("results")
    if not isinstance(results, list) or not results:
        blockers.append("missing_shard_results")
        results = []

    shard_names: set[str] = set()
    include_paths: set[str] = set()
    for index, result in enumerate(results):
        if not isinstance(result, Mapping):
            blockers.append(f"shard_{index}_not_object")
            continue
        name = str(result.get("shard") or "").strip()
        if not name:
            blockers.append(f"shard_{index}_missing_name")
        elif name in shard_names:
            blockers.append(f"duplicate_shard_name:{name}")
        else:
            shard_names.add(name)
        paths = result.get("include_paths")
        if not isinstance(paths, list) or not paths:
            blockers.append(f"{name or index}_missing_include_paths")
        else:
            for raw_path in paths:
                path = str(raw_path or "").strip()
                if not path:
                    blockers.append(f"{name or index}_empty_include_path")
                elif path in include_paths:
                    blockers.append(f"duplicate_include_path:{path}")
                else:
                    include_paths.add(path)
        _validate_shard_result(result, name or str(index), blockers, warnings)

    artifact_capture_ready = bool(results) and all(
        isinstance(result, Mapping) and result.get("artifact_graph_json_exists") is True for result in results
    )
    artifact_report_ready = artifact_capture_ready and all(
        isinstance(result, Mapping) and result.get("artifact_graph_report_exists") is True for result in results
    )

    accepted = not blockers

    summary = {
        "ref": status.get("ref"),
        "model": status.get("model"),
        "shard_count": status.get("shard_count"),
        "accepted_count": status.get("accepted_count"),
        "median_accepted_duration_ms": status.get("median_accepted_duration_ms"),
        "max_observed_rss_kb": status.get("max_observed_rss_kb"),
        "shards": sorted(shard_names),
        "include_paths": sorted(include_paths),
        "artifact_dir": status.get("artifact_dir"),
        "artifact_capture_ready": artifact_capture_ready,
        "artifact_report_ready": artifact_report_ready,
        "artifact_sync_ready": accepted and bool(status.get("cluster")) and artifact_report_ready,
    }
    required_next_steps = (
        "run shard matrix with --artifact-dir when artifact_capture_ready is false",
        "validate each artifact directory has graph.json and GRAPH_REPORT.md when clustering is enabled",
        "merge graph JSON with deterministic namespace/conflict handling and provenance per shard",
        "run cluster/report generation on the merged graph in a temporary output directory",
        "compare merged report against current inventory before any graphify-out replacement PR",
    )
    return ShardSyncPlan(
        accepted=accepted,
        status="ready_for_artifact_merge_design" if accepted else "blocked",
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        summary=summary,
        required_next_steps=required_next_steps,
    )


def _validate_shard_result(
    result: Mapping[str, Any],
    label: str,
    blockers: list[str],
    warnings: list[str],
) -> None:
    if result.get("accepted") is not True:
        blockers.append(f"{label}_not_accepted")
    if result.get("blockers"):
        blockers.append(f"{label}_has_blockers")
    for metric in ("invalid_json_chunks", "truncated_chunks"):
        if int(result.get(metric) or 0) != 0:
            blockers.append(f"{label}_{metric}")
    if int(result.get("context_splits") or 0) != 0:
        blockers.append(f"{label}_context_splits")
    if int(result.get("nodes") or 0) <= 0:
        blockers.append(f"{label}_missing_nodes")
    if int(result.get("edges") or 0) <= 0:
        blockers.append(f"{label}_missing_edges")
    if result.get("model_file_exists") is not True:
        blockers.append(f"{label}_model_file_missing")
    if result.get("base_url_localhost") is not True:
        blockers.append(f"{label}_nonlocal_base_url")
    if result.get("offline_cloud_keys_scrubbed") is not True:
        blockers.append(f"{label}_cloud_keys_not_scrubbed")
    if result.get("warnings"):
        warnings.append(f"{label}_warnings_present")
    if not isinstance(result.get("duration_ms"), int):
        warnings.append(f"{label}_duration_missing")
    if not isinstance(result.get("child_max_rss_kb"), int):
        warnings.append(f"{label}_rss_missing")


def load_status(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON status file: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("status file must contain a JSON object")
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a no-write Graphify shard sync plan from matrix evidence.")
    parser.add_argument("status_json", type=Path, help="graphify_local_shard_matrix status JSON")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_shard_sync_plan(load_status(args.status_json))
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(plan.to_dict(), indent=2, sort_keys=True)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    return 0 if plan.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
