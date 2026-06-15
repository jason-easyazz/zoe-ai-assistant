import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "graphify_shard_sync_plan.py"
    spec = importlib.util.spec_from_file_location("graphify_shard_sync_plan_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _status():
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
        "median_accepted_duration_ms": 46704,
        "max_observed_rss_kb": 86408,
        "artifact_dir": None,
        "cluster": False,
        "results": [
            _result("data-core", ["services/zoe-data"]),
            _result("operators", ["scripts", "tools"]),
        ],
    }


def _result(name, include_paths):
    return {
        "shard": name,
        "include_paths": include_paths,
        "accepted": True,
        "blockers": [],
        "warnings": [],
        "duration_ms": 43000,
        "child_max_rss_kb": 86000,
        "nodes": 10,
        "edges": 9,
        "code_files": 2,
        "doc_files": 1,
        "invalid_json_chunks": 0,
        "context_splits": 0,
        "truncated_chunks": 0,
        "model_file_exists": True,
        "base_url_localhost": True,
        "offline_cloud_keys_scrubbed": True,
    }


def test_build_shard_sync_plan_accepts_clean_current_shards():
    plan = MODULE.build_shard_sync_plan(_status())

    assert plan.accepted is True
    assert plan.status == "ready_for_artifact_merge_design"
    assert plan.blockers == ()
    assert plan.summary["artifact_capture_ready"] is False
    assert plan.summary["artifact_sync_ready"] is False
    assert plan.summary["shards"] == ["data-core", "operators"]
    assert "--artifact-dir" in plan.required_next_steps[0]


def test_build_shard_sync_plan_reports_artifact_capture_and_sync_readiness():
    status = _status()
    status["artifact_dir"] = "/tmp/artifacts"
    status["cluster"] = True
    for result in status["results"]:
        result["artifact_graph_json_exists"] = True
        result["artifact_graph_report_exists"] = True

    plan = MODULE.build_shard_sync_plan(status)

    assert plan.accepted is True
    assert plan.summary["artifact_capture_ready"] is True
    assert plan.summary["artifact_report_ready"] is True
    assert plan.summary["artifact_sync_ready"] is True


def test_build_shard_sync_plan_never_sets_sync_ready_when_blocked():
    status = _status()
    status["all_accepted"] = False
    status["artifact_dir"] = "/tmp/artifacts"
    status["cluster"] = True
    for result in status["results"]:
        result["artifact_graph_json_exists"] = True
        result["artifact_graph_report_exists"] = True

    plan = MODULE.build_shard_sync_plan(status)

    assert plan.accepted is False
    assert plan.summary["artifact_capture_ready"] is True
    assert plan.summary["artifact_report_ready"] is True
    assert plan.summary["artifact_sync_ready"] is False


def test_build_shard_sync_plan_rejects_partial_or_blocked_matrix():
    status = _status()
    status["all_accepted"] = False
    status["rejected_count"] = 1
    status["blocked_shards"] = ["ui"]

    plan = MODULE.build_shard_sync_plan(status)

    assert plan.accepted is False
    assert "not_all_shards_accepted" in plan.blockers
    assert "rejected_shards_present" in plan.blockers
    assert "blocked_shards_present" in plan.blockers


def test_build_shard_sync_plan_rejects_bad_shard_metrics():
    status = _status()
    status["results"][0]["invalid_json_chunks"] = 1
    status["results"][0]["nodes"] = 0
    status["results"][0]["base_url_localhost"] = False

    plan = MODULE.build_shard_sync_plan(status)

    assert plan.accepted is False
    assert "data-core_invalid_json_chunks" in plan.blockers
    assert "data-core_missing_nodes" in plan.blockers
    assert "data-core_nonlocal_base_url" in plan.blockers


def test_build_shard_sync_plan_rejects_duplicate_names_and_paths():
    status = _status()
    status["results"][1]["shard"] = "data-core"
    status["results"][1]["include_paths"] = ["services/zoe-data"]

    plan = MODULE.build_shard_sync_plan(status)

    assert plan.accepted is False
    assert "duplicate_shard_name:data-core" in plan.blockers
    assert "duplicate_include_path:services/zoe-data" in plan.blockers


def test_main_writes_output_json_for_accepted_plan(tmp_path, capsys):
    status_json = tmp_path / "status.json"
    output_json = tmp_path / "plan.json"
    status_json.write_text(json.dumps(_status()), encoding="utf-8")

    rc = MODULE.main([str(status_json), "--output-json", str(output_json)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "ready_for_artifact_merge_design" in captured.out
    written = json.loads(output_json.read_text(encoding="utf-8"))
    assert written["accepted"] is True


def test_main_returns_1_for_blocked_plan(tmp_path):
    status = _status()
    status["results"][0]["accepted"] = False
    status_json = tmp_path / "status.json"
    status_json.write_text(json.dumps(status), encoding="utf-8")

    assert MODULE.main([str(status_json)]) == 1


def test_main_returns_2_for_truthy_nonnumeric_malformed_evidence(tmp_path, capsys):
    status = _status()
    status["rejected_count"] = ["malformed"]
    status_json = tmp_path / "status.json"
    status_json.write_text(json.dumps(status), encoding="utf-8")

    rc = MODULE.main([str(status_json)])

    captured = capsys.readouterr()
    assert rc == 2
    assert "error:" in captured.err


def test_main_returns_2_for_invalid_json(tmp_path, capsys):
    status_json = tmp_path / "status.json"
    status_json.write_text("not json", encoding="utf-8")

    rc = MODULE.main([str(status_json)])

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid JSON" in captured.err
