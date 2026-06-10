#!/usr/bin/env python3
"""Validate and merge captured Zoe Graphify shard artifacts outside the repo.

This is an artifact-only bridge: it reads `graphify_local_shard_matrix`
evidence plus captured per-shard `graphify-out/graph.json` files, proves a
namespaced merge is deterministic, and optionally writes the merged graph to an
explicit output path. It never updates the repository `graphify-out` directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from graphify_local_shard_matrix import validate_artifact_shard_name  # noqa: E402
from graphify_shard_sync_plan import build_shard_sync_plan  # noqa: E402

GRAPH_KEYS = ("nodes", "edges", "hyperedges")


@dataclass(frozen=True)
class GraphifyArtifactMergeResult:
    accepted: bool
    status: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: dict[str, Any]
    graph: dict[str, Any] | None = None

    def to_dict(self, *, include_graph: bool = False) -> dict[str, Any]:
        payload = {
            "accepted": self.accepted,
            "status": self.status,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "summary": self.summary,
        }
        if include_graph and self.graph is not None:
            payload["graph"] = self.graph
        return payload


def build_artifact_merge_result(status: Mapping[str, Any]) -> GraphifyArtifactMergeResult:
    plan = build_shard_sync_plan(status)
    blockers = list(plan.blockers)
    warnings = list(plan.warnings)
    artifact_dir = status.get("artifact_dir")
    if not isinstance(artifact_dir, str) or not artifact_dir.strip():
        blockers.append("missing_artifact_dir")
        artifact_root = None
    else:
        artifact_root = Path(artifact_dir).resolve()
        if not artifact_root.exists():
            blockers.append("artifact_dir_missing")

    results = status.get("results") if isinstance(status.get("results"), list) else []
    if not results:
        blockers.append("missing_shard_results")

    merged_nodes: list[dict[str, Any]] = []
    merged_edges: list[dict[str, Any]] = []
    merged_hyperedges: list[dict[str, Any]] = []
    shard_summaries: list[dict[str, Any]] = []
    original_node_ids: dict[str, str] = {}
    namespace_collision_count = 0
    duplicate_node_count = 0
    external_endpoint_count = 0
    input_tokens = 0
    output_tokens = 0

    for index, raw_result in enumerate(sorted(_mapping_results(results), key=lambda item: str(item.get("shard") or ""))):
        shard = str(raw_result.get("shard") or "").strip()
        label = shard or str(index)
        if raw_result.get("accepted") is not True:
            blockers.append(f"{label}_not_accepted")
            continue
        try:
            validate_artifact_shard_name(shard)
        except ValueError:
            blockers.append(f"{label}_invalid_artifact_shard_name")
            continue
        try:
            graph_path = _artifact_graph_path(raw_result, artifact_root, shard)
        except ValueError as exc:
            blockers.append(f"{label}_artifact_path_invalid:{exc}")
            continue
        if graph_path is None:
            blockers.append(f"{label}_artifact_path_unavailable")
            continue
        try:
            graph = _load_graph_json(graph_path)
        except (OSError, ValueError) as exc:
            blockers.append(f"{label}_artifact_graph_unreadable:{exc}")
            continue

        shard_nodes = graph["nodes"]
        shard_edges = graph["edges"]
        shard_hyperedges = graph["hyperedges"]
        shard_node_ids: set[str] = set()
        emitted_node_ids: set[str] = set()
        for node_index, node in enumerate(shard_nodes):
            node_id = _string_field(node, "id")
            if not node_id:
                blockers.append(f"{label}_node_{node_index}_missing_id")
                continue
            if node_id in shard_node_ids:
                duplicate_node_count += 1
                warnings.append(f"{label}_duplicate_node_id:{node_id}")
                continue
            if node_id in original_node_ids and original_node_ids[node_id] != shard:
                namespace_collision_count += 1
            original_node_ids[node_id] = shard
            shard_node_ids.add(node_id)
            emitted_node_ids.add(node_id)
            merged_nodes.append(_with_provenance(node, shard, {"id": _namespaced_id(shard, node_id), "original_id": node_id}))

        for edge_index, edge in enumerate(shard_edges):
            source = _string_field(edge, "source")
            target = _string_field(edge, "target")
            if not source or not target:
                blockers.append(f"{label}_edge_{edge_index}_missing_endpoint")
                continue
            if source not in emitted_node_ids:
                merged_nodes.append(_external_node(shard, source))
                emitted_node_ids.add(source)
                external_endpoint_count += 1
            if target not in emitted_node_ids:
                merged_nodes.append(_external_node(shard, target))
                emitted_node_ids.add(target)
                external_endpoint_count += 1
            merged_edges.append(
                _with_provenance(
                    edge,
                    shard,
                    {
                        "source": _namespaced_id(shard, source),
                        "target": _namespaced_id(shard, target),
                        "original_source": source,
                        "original_target": target,
                    },
                )
            )

        for hyperedge_index, hyperedge in enumerate(shard_hyperedges):
            if not isinstance(hyperedge, Mapping):
                blockers.append(f"{label}_hyperedge_{hyperedge_index}_not_object")
                continue
            merged_hyperedges.append(_with_provenance(hyperedge, shard, {}))

        input_tokens += int(graph.get("input_tokens") or 0)
        output_tokens += int(graph.get("output_tokens") or 0)
        shard_summaries.append(
            {
                "shard": shard,
                "artifact_graph_json": str(graph_path),
                "nodes": len(shard_nodes),
                "edges": len(shard_edges),
                "hyperedges": len(shard_hyperedges),
            }
        )

    merged_graph = {
        "nodes": merged_nodes,
        "edges": merged_edges,
        "hyperedges": merged_hyperedges,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "zoe_merge": {
            "schema_version": 1,
            "source_probe": status.get("probe"),
            "source_ref": status.get("ref"),
            "source_model": status.get("model"),
            "artifact_dir": str(artifact_root) if artifact_root else artifact_dir,
            "id_namespace": "{shard}::{original_id}",
            "shards": shard_summaries,
        },
    }
    summary = {
        "artifact_dir": str(artifact_root) if artifact_root else artifact_dir,
        "source_ref": status.get("ref"),
        "source_model": status.get("model"),
        "shard_count": len(shard_summaries),
        "merged_nodes": len(merged_nodes),
        "merged_edges": len(merged_edges),
        "merged_hyperedges": len(merged_hyperedges),
        "namespace_collision_count": namespace_collision_count,
        "duplicate_node_count": duplicate_node_count,
        "external_endpoint_count": external_endpoint_count,
        "deterministic_order": "shard_name_then_artifact_order",
        "repo_graphify_out_updated": False,
        "plan_status": plan.status,
        "plan_artifact_capture_ready": plan.summary.get("artifact_capture_ready"),
        "plan_artifact_sync_ready": plan.summary.get("artifact_sync_ready"),
    }
    accepted = not blockers and bool(shard_summaries) and bool(merged_nodes)
    return GraphifyArtifactMergeResult(
        accepted=accepted,
        status="ready_for_temp_cluster_report" if accepted else "blocked",
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        summary=summary,
        graph=merged_graph if accepted else None,
    )


def _mapping_results(results: Sequence[Any]) -> list[Mapping[str, Any]]:
    return [result for result in results if isinstance(result, Mapping)]


def _artifact_graph_path(result: Mapping[str, Any], artifact_root: Path | None, shard: str) -> Path | None:
    if artifact_root is None:
        return None
    raw_path = result.get("artifact_path")
    if isinstance(raw_path, str) and raw_path.strip():
        graphify_out = Path(raw_path).resolve()
    else:
        graphify_out = (artifact_root / shard / "graphify-out").resolve()
    if not _is_relative_to(graphify_out, artifact_root):
        raise ValueError(f"artifact path escapes artifact dir for shard {shard!r}")
    return graphify_out / "graph.json"


def _load_graph_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid graph JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"graph JSON must be an object at {path}")
    for key in GRAPH_KEYS:
        if not isinstance(payload.get(key), list):
            raise ValueError(f"graph JSON {key!r} must be a list at {path}")
        if any(not isinstance(item, Mapping) for item in payload[key]):
            raise ValueError(f"graph JSON {key!r} entries must be objects at {path}")
    return payload


def _string_field(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _namespaced_id(shard: str, original_id: str) -> str:
    return f"{shard}::{original_id}"


def _with_provenance(payload: Mapping[str, Any], shard: str, overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    merged.update(overrides)
    merged["zoe_shard"] = shard
    return merged


def _external_node(shard: str, original_id: str) -> dict[str, Any]:
    return {
        "id": _namespaced_id(shard, original_id),
        "original_id": original_id,
        "label": original_id,
        "file_type": "external",
        "zoe_shard": shard,
        "zoe_external_endpoint": True,
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def load_status(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON status file: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("status file must contain a JSON object")
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and merge captured Graphify shard artifacts outside the repo.")
    parser.add_argument("status_json", type=Path, help="graphify_local_shard_matrix status JSON with artifact metadata")
    parser.add_argument("--output-json", type=Path, help="Write compact merge validation result here")
    parser.add_argument("--merged-graph-json", type=Path, help="Write merged graph JSON here")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_artifact_merge_result(load_status(args.status_json))
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.merged_graph_json and result.graph is not None:
        args.merged_graph_json.parent.mkdir(parents=True, exist_ok=True)
        args.merged_graph_json.write_text(json.dumps(result.graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text = json.dumps(result.to_dict(include_graph=False), indent=2, sort_keys=True)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
