import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "graphify_shard_merge_validate.py"
    spec = importlib.util.spec_from_file_location("graphify_shard_merge_validate_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _graph(nodes, links):
    return {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": nodes,
        "links": links,
        "hyperedges": [],
        "built_at_commit": "fixture",
    }


def _node(node_id, *, label=None):
    return {
        "id": node_id,
        "label": label or node_id,
        "source_file": f"/home/zoe/assistant/{node_id}.py",
        "source_location": "L1",
        "file_type": "code",
    }


def _link(source, target, *, relation="contains"):
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "source_file": f"/home/zoe/assistant/{source}.py",
        "source_location": "L1",
        "confidence": "EXTRACTED",
    }


def _write_artifact(tmp_path, shard, graph):
    graphify_out = tmp_path / shard / "graphify-out"
    graphify_out.mkdir(parents=True)
    (graphify_out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    return graphify_out


def _result(shard, graphify_out):
    graph_json = graphify_out / "graph.json"
    return {
        "shard": shard,
        "include_paths": [f"fixture/{shard}"],
        "accepted": True,
        "artifact_path": str(graphify_out),
        "artifact_graph_json_exists": graph_json.exists(),
        "artifact_graph_json_bytes": graph_json.stat().st_size if graph_json.exists() else 0,
        "blockers": [],
        "warnings": [],
        "invalid_json_chunks": 0,
        "context_splits": 0,
        "truncated_chunks": 0,
        "nodes": 2,
        "edges": 1,
        "model_file_exists": True,
        "base_url_localhost": True,
        "offline_cloud_keys_scrubbed": True,
    }


def _status(results):
    return {
        "schema_version": 1,
        "probe": "graphify_local_shard_matrix",
        "ref": "origin/main",
        "model": "gemma-4-e4b-it-Q4_K_M.gguf",
        "artifact_dir": "/tmp/graphify-artifacts",
        "shard_count": len(results),
        "accepted_count": len(results),
        "rejected_count": 0,
        "all_accepted": True,
        "blocked_shards": [],
        "cluster": True,
        "results": results,
    }


def test_validate_shard_merge_accepts_clean_artifacts(tmp_path):
    data_graph = _graph([_node("data_file"), _node("data_func")], [_link("data_file", "data_func")])
    ops_graph = _graph([_node("ops_file"), _node("ops_func")], [_link("ops_file", "ops_func")])
    status = _status([
        _result("data-core", _write_artifact(tmp_path, "data-core", data_graph)),
        _result("operators", _write_artifact(tmp_path, "operators", ops_graph)),
    ])

    result = MODULE.validate_shard_merge(status)

    assert result.accepted is True
    assert result.status == "ready_for_merged_cluster_report"
    assert result.blockers == ()
    assert result.summary["node_count"] == 4
    assert result.summary["link_count"] == 2
    assert result.summary["write_ready"] is True
    assert [node["id"] for node in result.merged_graph["nodes"]] == ["data_file", "data_func", "ops_file", "ops_func"]
    assert result.merged_graph["merged_from_shards"]["shards"] == ["data-core", "operators"]


def test_validate_shard_merge_adds_provenance_for_duplicate_identical_nodes(tmp_path):
    shared = _node("shared_helper")
    data_graph = _graph([_node("data_file"), shared], [_link("data_file", "shared_helper")])
    ops_graph = _graph([_node("ops_file"), shared], [_link("ops_file", "shared_helper")])
    status = _status([
        _result("data-core", _write_artifact(tmp_path, "data-core", data_graph)),
        _result("operators", _write_artifact(tmp_path, "operators", ops_graph)),
    ])

    result = MODULE.validate_shard_merge(status)

    assert result.accepted is True
    shared_node = next(node for node in result.merged_graph["nodes"] if node["id"] == "shared_helper")
    assert shared_node["graphify_shards"] == ["data-core", "operators"]


def test_validate_shard_merge_is_deterministic_when_status_order_changes(tmp_path):
    data_graph = _graph([_node("data_file"), _node("data_func")], [_link("data_file", "data_func")])
    ops_graph = _graph([_node("ops_file"), _node("ops_func")], [_link("ops_file", "ops_func")])
    data_result = _result("data-core", _write_artifact(tmp_path, "data-core", data_graph))
    ops_result = _result("operators", _write_artifact(tmp_path, "operators", ops_graph))

    first = MODULE.validate_shard_merge(_status([data_result, ops_result]))
    second = MODULE.validate_shard_merge(_status([ops_result, data_result]))

    assert first.accepted is True
    assert second.accepted is True
    assert first.summary["deterministic_hash"] == second.summary["deterministic_hash"]
    assert first.merged_graph == second.merged_graph


def test_validate_shard_merge_blocks_conflicting_duplicate_nodes(tmp_path):
    data_graph = _graph([_node("shared_helper", label="Shared")], [])
    ops_graph = _graph([_node("shared_helper", label="Different")], [])
    status = _status([
        _result("data-core", _write_artifact(tmp_path, "data-core", data_graph)),
        _result("operators", _write_artifact(tmp_path, "operators", ops_graph)),
    ])

    result = MODULE.validate_shard_merge(status)

    assert result.accepted is False
    assert "node_conflict:shared_helper" in result.blockers
    assert result.merged_graph is None




def test_validate_shard_merge_blocks_conflicting_duplicate_links(tmp_path):
    first_link = _link("data_file", "shared_helper")
    second_link = _link("data_file", "shared_helper")
    second_link["confidence"] = "DIFFERENT"
    data_graph = _graph([_node("data_file"), _node("shared_helper")], [first_link])
    ops_graph = _graph([_node("data_file"), _node("shared_helper")], [second_link])
    status = _status([
        _result("data-core", _write_artifact(tmp_path, "data-core", data_graph)),
        _result("operators", _write_artifact(tmp_path, "operators", ops_graph)),
    ])

    result = MODULE.validate_shard_merge(status)

    assert result.accepted is False
    assert any(blocker.startswith("link_conflict:") for blocker in result.blockers)


def test_validate_shard_merge_blocks_links_with_missing_endpoints(tmp_path):
    graph = _graph([_node("data_file")], [_link("data_file", "missing_func")])
    status = _status([_result("data-core", _write_artifact(tmp_path, "data-core", graph))])

    result = MODULE.validate_shard_merge(status)

    assert result.accepted is False
    assert any(blocker.endswith(":missing_func") for blocker in result.blockers)


def test_main_writes_validation_and_merged_graph_json(tmp_path, capsys):
    graph = _graph([_node("data_file"), _node("data_func")], [_link("data_file", "data_func")])
    status = _status([_result("data-core", _write_artifact(tmp_path, "data-core", graph))])
    status_json = tmp_path / "status.json"
    result_json = tmp_path / "result.json"
    merged_json = tmp_path / "merged" / "graph.json"
    status_json.write_text(json.dumps(status), encoding="utf-8")

    rc = MODULE.main([str(status_json), "--output-json", str(result_json), "--merged-graph-json", str(merged_json)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "ready_for_merged_cluster_report" in captured.out
    written_result = json.loads(result_json.read_text(encoding="utf-8"))
    written_graph = json.loads(merged_json.read_text(encoding="utf-8"))
    assert written_result["accepted"] is True
    assert written_graph["merged_from_shards"]["probe"] == "graphify_local_shard_matrix"


def test_main_returns_1_for_blocked_merge(tmp_path):
    graph = _graph([_node("data_file")], [_link("data_file", "missing_func")])
    status_json = tmp_path / "status.json"
    status_json.write_text(json.dumps(_status([_result("data-core", _write_artifact(tmp_path, "data-core", graph))])), encoding="utf-8")

    assert MODULE.main([str(status_json)]) == 1


def test_main_returns_2_for_invalid_status_json(tmp_path, capsys):
    status_json = tmp_path / "status.json"
    status_json.write_text("not json", encoding="utf-8")

    rc = MODULE.main([str(status_json)])

    captured = capsys.readouterr()
    assert rc == 2
    assert "error:" in captured.err
