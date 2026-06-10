import importlib.util
import pytest
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "graphify_artifact_merge_validate.py"
    spec = importlib.util.spec_from_file_location("graphify_artifact_merge_validate_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    MODULE = _load_module()
except Exception as exc:  # pragma: no cover - collection-time dependency guard
    pytest.skip(f"graphify artifact merge validator is not importable: {exc}", allow_module_level=True)


def _write_graph(root: Path, shard: str, nodes, edges, hyperedges=None):
    graphify_out = root / shard / "graphify-out"
    graphify_out.mkdir(parents=True)
    graph = {
        "nodes": nodes,
        "edges": edges,
        "hyperedges": hyperedges or [],
        "input_tokens": 3,
        "output_tokens": 5,
    }
    (graphify_out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return graphify_out


def _status(artifact_dir: Path):
    _write_graph(
        artifact_dir,
        "alpha",
        [
            {"id": "shared", "label": "alpha shared"},
            {"id": "alpha_only", "label": "alpha only"},
        ],
        [{"source": "shared", "target": "alpha_only", "relation": "uses"}],
    )
    _write_graph(
        artifact_dir,
        "beta",
        [
            {"id": "shared", "label": "beta shared"},
            {"id": "beta_only", "label": "beta only"},
        ],
        [{"source": "shared", "target": "beta_only", "relation": "uses"}],
    )
    return {
        "schema_version": 1,
        "probe": "graphify_local_shard_matrix",
        "ref": "origin/main",
        "model": "gemma-4-e4b-it-Q4_K_M.gguf",
        "shard_count": 2,
        "accepted_count": 2,
        "rejected_count": 0,
        "all_accepted": True,
        "blocked_shards": [],
        "median_accepted_duration_ms": 100,
        "max_observed_rss_kb": 200,
        "artifact_dir": str(artifact_dir),
        "cluster": False,
        "results": [
            _result("beta", ["services/beta"]),
            _result("alpha", ["services/alpha"]),
        ],
    }


def _result(name, include_paths):
    return {
        "shard": name,
        "include_paths": include_paths,
        "accepted": True,
        "blockers": [],
        "warnings": [],
        "duration_ms": 100,
        "child_max_rss_kb": 200,
        "nodes": 2,
        "edges": 1,
        "code_files": 1,
        "doc_files": 0,
        "invalid_json_chunks": 0,
        "context_splits": 0,
        "truncated_chunks": 0,
        "model_file_exists": True,
        "base_url_localhost": True,
        "offline_cloud_keys_scrubbed": True,
        "artifact_graph_json_exists": True,
        "artifact_graph_json_bytes": 100,
        "artifact_graph_report_exists": False,
    }


def test_build_artifact_merge_result_namespaces_nodes_and_edges(tmp_path):
    status = _status(tmp_path)

    result = MODULE.build_artifact_merge_result(status)

    assert result.accepted is True
    assert result.status == "ready_for_temp_cluster_report"
    assert result.summary["repo_graphify_out_updated"] is False
    assert result.summary["namespace_collision_count"] == 1
    assert result.summary["merged_nodes"] == 4
    assert result.summary["merged_edges"] == 2
    assert result.graph["nodes"][0]["id"] == "alpha::shared"
    assert result.graph["nodes"][0]["original_id"] == "shared"
    assert result.graph["nodes"][0]["zoe_shard"] == "alpha"
    assert result.graph["edges"][0]["source"] == "alpha::shared"
    assert result.graph["edges"][0]["target"] == "alpha::alpha_only"
    assert result.graph["edges"][0]["original_source"] == "shared"
    assert result.graph["zoe_merge"]["id_namespace"] == "{shard}::{original_id}"


def test_build_artifact_merge_result_adds_external_endpoint_stubs(tmp_path):
    status = _status(tmp_path)
    graph_path = tmp_path / "alpha" / "graphify-out" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["edges"][0]["target"] = "missing"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")

    result = MODULE.build_artifact_merge_result(status)

    assert result.accepted is True
    assert result.summary["external_endpoint_count"] == 1
    assert any(node["id"] == "alpha::missing" and node["zoe_external_endpoint"] is True for node in result.graph["nodes"])


def test_build_artifact_merge_result_rejects_blank_edge_endpoint(tmp_path):
    status = _status(tmp_path)
    graph_path = tmp_path / "alpha" / "graphify-out" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["edges"][0]["target"] = ""
    graph_path.write_text(json.dumps(graph), encoding="utf-8")

    result = MODULE.build_artifact_merge_result(status)

    assert result.accepted is False
    assert "alpha_edge_0_missing_endpoint" in result.blockers
    assert result.graph is None


def test_build_artifact_merge_result_rejects_artifact_path_escape(tmp_path):
    status = _status(tmp_path)
    status["results"][0]["artifact_path"] = str(tmp_path.parent / "outside" / "graphify-out")

    result = MODULE.build_artifact_merge_result(status)

    assert result.accepted is False
    assert any(blocker.startswith("beta_artifact_path_invalid:") for blocker in result.blockers)


def test_build_artifact_merge_result_keeps_plan_blockers(tmp_path):
    status = _status(tmp_path)
    status["all_accepted"] = False

    result = MODULE.build_artifact_merge_result(status)

    assert result.accepted is False
    assert "not_all_shards_accepted" in result.blockers


def test_build_artifact_merge_result_rejects_unaccepted_shard_result(tmp_path):
    status = _status(tmp_path)
    status["results"][0]["accepted"] = False

    result = MODULE.build_artifact_merge_result(status)

    assert result.accepted is False
    assert "beta_not_accepted" in result.blockers


def test_build_artifact_merge_result_does_not_read_paths_without_artifact_root(tmp_path):
    status = _status(tmp_path / "artifacts")
    outside = tmp_path / "outside" / "graphify-out"
    outside.mkdir(parents=True)
    (outside / "graph.json").write_text("not json", encoding="utf-8")
    status["artifact_dir"] = None
    status["results"][0]["artifact_path"] = str(outside)

    result = MODULE.build_artifact_merge_result(status)

    assert result.accepted is False
    assert "missing_artifact_dir" in result.blockers
    assert "beta_artifact_path_unavailable" in result.blockers
    assert not any("invalid graph JSON" in blocker for blocker in result.blockers)


def test_main_writes_compact_result_and_merged_graph(tmp_path, capsys):
    status = _status(tmp_path / "artifacts")
    status_json = tmp_path / "status.json"
    output_json = tmp_path / "merge-result.json"
    merged_graph_json = tmp_path / "merged-graph.json"
    status_json.write_text(json.dumps(status), encoding="utf-8")

    rc = MODULE.main([
        str(status_json),
        "--output-json",
        str(output_json),
        "--merged-graph-json",
        str(merged_graph_json),
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "ready_for_temp_cluster_report" in captured.out
    written = json.loads(output_json.read_text(encoding="utf-8"))
    merged = json.loads(merged_graph_json.read_text(encoding="utf-8"))
    assert written["accepted"] is True
    assert "graph" not in written
    assert len(merged["nodes"]) == 4


def test_main_returns_2_for_invalid_status_json(tmp_path, capsys):
    status_json = tmp_path / "status.json"
    status_json.write_text("nope", encoding="utf-8")

    rc = MODULE.main([str(status_json)])

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid JSON" in captured.err
