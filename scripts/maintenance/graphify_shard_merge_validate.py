#!/usr/bin/env python3
"""Validate deterministic merging of Zoe Graphify shard artifacts.

This is a no-write-to-repo gate. It consumes the observe-only shard matrix
status JSON plus captured per-shard `graphify-out/graph.json` artifacts and
proves that they can be merged with deterministic ordering, endpoint integrity,
and shard provenance before any future `graphify-out` replacement PR.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

REQUIRED_PROBE = "graphify_local_shard_matrix"
REQUIRED_SCHEMA_VERSION = 1
MERGE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GraphifyShardMergeValidation:
    accepted: bool
    status: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: dict[str, Any]
    merged_graph: dict[str, Any] | None = None

    def to_dict(self, *, include_graph: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "accepted": self.accepted,
            "status": self.status,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "summary": self.summary,
        }
        if include_graph and self.merged_graph is not None:
            payload["merged_graph"] = self.merged_graph
        return payload


def validate_shard_merge(status: Mapping[str, Any]) -> GraphifyShardMergeValidation:
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

    merged_nodes: dict[str, dict[str, Any]] = {}
    node_fingerprints: dict[str, str] = {}
    merged_links: dict[str, dict[str, Any]] = {}
    link_fingerprints: dict[str, str] = {}
    merged_hyperedges: dict[str, dict[str, Any]] = {}
    hyperedge_fingerprints: dict[str, str] = {}
    graph_flags: tuple[bool, bool] | None = None
    shard_names: list[str] = []
    source_paths: dict[str, str] = {}

    for index, raw_result in enumerate(results):
        if not isinstance(raw_result, Mapping):
            blockers.append(f"shard_{index}_not_object")
            continue
        shard = str(raw_result.get("shard") or "").strip()
        if not shard:
            blockers.append(f"shard_{index}_missing_name")
            shard = f"shard_{index}"
        if shard in shard_names:
            blockers.append(f"duplicate_shard_name:{shard}")
        shard_names.append(shard)

        if raw_result.get("accepted") is not True:
            blockers.append(f"{shard}_not_accepted")
        if raw_result.get("artifact_graph_json_exists") is not True:
            blockers.append(f"{shard}_missing_artifact_graph_json")
            continue

        graph_path = _graph_path_for_result(raw_result, shard, blockers)
        if graph_path is None:
            continue
        source_paths[shard] = str(graph_path)
        try:
            graph = _load_graph(graph_path)
        except (OSError, TypeError, ValueError) as exc:
            blockers.append(f"{shard}_invalid_graph_json:{exc}")
            continue

        directed = graph.get("directed")
        multigraph = graph.get("multigraph")
        if not isinstance(directed, bool) or not isinstance(multigraph, bool):
            blockers.append(f"{shard}_missing_graph_flags")
        else:
            flags = (directed, multigraph)
            if graph_flags is None:
                graph_flags = flags
            elif graph_flags != flags:
                blockers.append(f"{shard}_graph_flags_conflict")

        nodes = graph.get("nodes")
        links = graph.get("links")
        hyperedges = graph.get("hyperedges", [])
        if not isinstance(nodes, list):
            blockers.append(f"{shard}_nodes_not_list")
            nodes = []
        if not isinstance(links, list):
            blockers.append(f"{shard}_links_not_list")
            links = []
        if not isinstance(hyperedges, list):
            blockers.append(f"{shard}_hyperedges_not_list")
            hyperedges = []
        if not nodes:
            blockers.append(f"{shard}_empty_nodes")
        if not links:
            warnings.append(f"{shard}_empty_links")

        for node_index, node in enumerate(nodes):
            if not isinstance(node, Mapping):
                blockers.append(f"{shard}_node_{node_index}_not_object")
                continue
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                blockers.append(f"{shard}_node_{node_index}_missing_id")
                continue
            fingerprint = _fingerprint(_without_merge_only_fields(dict(node)))
            if node_id in merged_nodes:
                if node_fingerprints[node_id] != fingerprint:
                    blockers.append(f"node_conflict:{node_id}")
                    continue
                _add_provenance(merged_nodes[node_id], shard)
            else:
                merged = copy.deepcopy(dict(node))
                merged["graphify_shards"] = [shard]
                merged_nodes[node_id] = merged
                node_fingerprints[node_id] = fingerprint

        for link_index, link in enumerate(links):
            if not isinstance(link, Mapping):
                blockers.append(f"{shard}_link_{link_index}_not_object")
                continue
            source = str(link.get("source") or "").strip()
            target = str(link.get("target") or "").strip()
            if not source or not target:
                blockers.append(f"{shard}_link_{link_index}_missing_endpoint")
                continue
            key = _edge_key(link)
            fingerprint = _fingerprint(_without_merge_only_fields(dict(link)))
            if key in merged_links:
                if link_fingerprints[key] != fingerprint:
                    blockers.append(f"link_conflict:{key}")
                    continue
                _add_provenance(merged_links[key], shard)
            else:
                merged = copy.deepcopy(dict(link))
                merged["graphify_shards"] = [shard]
                merged_links[key] = merged
                link_fingerprints[key] = fingerprint

        for hyperedge_index, hyperedge in enumerate(hyperedges):
            if not isinstance(hyperedge, Mapping):
                blockers.append(f"{shard}_hyperedge_{hyperedge_index}_not_object")
                continue
            key = _hyperedge_key(hyperedge)
            fingerprint = _fingerprint(_without_merge_only_fields(dict(hyperedge)))
            if key in merged_hyperedges:
                if hyperedge_fingerprints[key] != fingerprint:
                    blockers.append(f"hyperedge_conflict:{key}")
                    continue
                _add_provenance(merged_hyperedges[key], shard)
            else:
                merged = copy.deepcopy(dict(hyperedge))
                merged["graphify_shards"] = [shard]
                merged_hyperedges[key] = merged
                hyperedge_fingerprints[key] = fingerprint

    for key, link in merged_links.items():
        source = str(link.get("source") or "")
        target = str(link.get("target") or "")
        if source not in merged_nodes:
            blockers.append(f"link_missing_source:{key}:{source}")
        if target not in merged_nodes:
            blockers.append(f"link_missing_target:{key}:{target}")

    merged_graph = _build_merged_graph(
        status=status,
        graph_flags=graph_flags,
        nodes=merged_nodes,
        links=merged_links,
        hyperedges=merged_hyperedges,
        shard_names=shard_names,
        source_paths=source_paths,
    )
    deterministic_hash = _fingerprint(merged_graph)
    accepted = not blockers
    summary = {
        "schema_version": MERGE_SCHEMA_VERSION,
        "source_ref": status.get("ref"),
        "model": status.get("model"),
        "artifact_dir": status.get("artifact_dir"),
        "shard_count": len(shard_names),
        "shards": sorted(shard_names),
        "source_graph_paths": source_paths,
        "node_count": len(merged_nodes),
        "link_count": len(merged_links),
        "hyperedge_count": len(merged_hyperedges),
        "deterministic_hash": deterministic_hash,
        "write_ready": accepted,
    }
    return GraphifyShardMergeValidation(
        accepted=accepted,
        status="ready_for_merged_cluster_report" if accepted else "blocked",
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        summary=summary,
        merged_graph=merged_graph if accepted else None,
    )


def _graph_path_for_result(result: Mapping[str, Any], shard: str, blockers: list[str]) -> Path | None:
    artifact_path = str(result.get("artifact_path") or "").strip()
    if artifact_path:
        path = Path(artifact_path) / "graph.json"
    else:
        blockers.append(f"{shard}_missing_artifact_path")
        return None
    if not path.exists():
        blockers.append(f"{shard}_artifact_graph_json_not_found")
        return None
    return path


def _load_graph(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("graph JSON must contain an object")
    return payload


def _without_merge_only_fields(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("graphify_shards", None)
    return payload


def _add_provenance(payload: dict[str, Any], shard: str) -> None:
    existing = payload.get("graphify_shards")
    shards = list(existing) if isinstance(existing, list) else []
    if shard not in shards:
        shards.append(shard)
    payload["graphify_shards"] = sorted(shards)


def _edge_key(link: Mapping[str, Any]) -> str:
    key = {
        "source": link.get("source"),
        "target": link.get("target"),
        "relation": link.get("relation"),
        "source_file": link.get("source_file"),
        "source_location": link.get("source_location"),
    }
    return _fingerprint(key)


def _hyperedge_key(hyperedge: Mapping[str, Any]) -> str:
    candidate = hyperedge.get("id") or hyperedge.get("key") or hyperedge
    return _fingerprint(candidate)


def _build_merged_graph(
    *,
    status: Mapping[str, Any],
    graph_flags: tuple[bool, bool] | None,
    nodes: Mapping[str, dict[str, Any]],
    links: Mapping[str, dict[str, Any]],
    hyperedges: Mapping[str, dict[str, Any]],
    shard_names: Sequence[str],
    source_paths: Mapping[str, str],
) -> dict[str, Any]:
    directed, multigraph = graph_flags if graph_flags is not None else (True, False)
    return {
        "directed": directed,
        "multigraph": multigraph,
        "graph": {},
        "nodes": sorted((copy.deepcopy(node) for node in nodes.values()), key=lambda item: str(item.get("id") or "")),
        "links": sorted((copy.deepcopy(link) for link in links.values()), key=_edge_key),
        "hyperedges": sorted((copy.deepcopy(edge) for edge in hyperedges.values()), key=_hyperedge_key),
        "built_at_commit": status.get("ref"),
        "merged_from_shards": {
            "schema_version": MERGE_SCHEMA_VERSION,
            "probe": REQUIRED_PROBE,
            "source_ref": status.get("ref"),
            "model": status.get("model"),
            "shards": sorted(shard_names),
            "source_graph_paths": dict(sorted(source_paths.items())),
        },
    }


def _fingerprint(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def load_status(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("status file must contain a JSON object")
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate deterministic merging of captured Graphify shard artifacts.")
    parser.add_argument("status_json", type=Path, help="graphify_local_shard_matrix status JSON with artifact paths")
    parser.add_argument("--output-json", type=Path, help="Write validation result JSON")
    parser.add_argument("--merged-graph-json", type=Path, help="Write merged graph JSON when validation is accepted")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = validate_shard_merge(load_status(args.status_json))
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.merged_graph_json and result.accepted and result.merged_graph is not None:
        args.merged_graph_json.parent.mkdir(parents=True, exist_ok=True)
        args.merged_graph_json.write_text(
            json.dumps(result.merged_graph, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    output = result.to_dict(include_graph=False)
    text = json.dumps(output, indent=2, sort_keys=True)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
