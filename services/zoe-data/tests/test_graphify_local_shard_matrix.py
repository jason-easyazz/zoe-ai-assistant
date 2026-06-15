import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "graphify_local_shard_matrix.py"
    spec = importlib.util.spec_from_file_location("graphify_local_shard_matrix_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _status(model="gemma-e4b", *, accepted=True, duration_ms=40, rss_kb=1000):
    return {
        "model": model,
        "mode": "scope",
        "accepted": accepted,
        "blockers": [] if accepted else ["invalid_json_chunks"],
        "warnings": [],
        "command_evidence": {
            "extract": {
                "duration_ms": duration_ms,
                "child_max_rss_kb": rss_kb,
            }
        },
        "metrics": {
            "nodes": 4 if accepted else None,
            "edges": 3 if accepted else None,
            "code_files": 2,
            "doc_files": 1,
            "invalid_json_chunks": 0 if accepted else 1,
            "context_splits": 0,
            "truncated_chunks": 0,
        },
        "model_fit": {
            "model_file_exists": True,
            "model_file": f"/models/{model}",
            "model_file_bytes": 123,
            "base_url_localhost": True,
            "offline_cloud_keys_scrubbed": True,
        },
    }


def test_parse_shard_validates_name_and_paths():
    module = _load_module()

    shard = module.parse_shard("ops=scripts,tools")

    assert shard.name == "ops"
    assert shard.include_paths == ("scripts", "tools")

    for value in ("missing_equals", "=scripts", "ops=", "ops=../secret"):
        try:
            module.parse_shard(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid shard accepted: {value}")

    for value in (".", "..", "bad/name"):
        try:
            module.validate_artifact_shard_name(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid artifact shard accepted: {value}")



def test_default_shards_include_only_accepted_local_slices():
    module = _load_module()

    defaults = module.default_shards()
    flattened = [path for shard in defaults for path in shard.include_paths]
    names = [shard.name for shard in defaults]

    assert ".zoe" not in flattened
    assert "harness-docs" not in names
    assert "ui" not in names
    assert "services/zoe-ui" not in flattened
    assert [shard.name for shard in defaults] == ["data-core", "operators"]
    assert [shard.include_paths for shard in defaults] == [("services/zoe-data",), ("scripts", "tools")]


def test_summarize_shards_reports_blocked_shards_and_accepted_median():
    module = _load_module()
    results = [
        {"shard": "data", "accepted": True, "duration_ms": 30, "child_max_rss_kb": 100},
        {"shard": "ui", "accepted": False, "duration_ms": 600000, "child_max_rss_kb": 200},
        {"shard": "docs", "accepted": True, "duration_ms": 50, "child_max_rss_kb": 150},
    ]

    summary = module.summarize_shards(results)

    assert summary["shard_count"] == 3
    assert summary["accepted_count"] == 2
    assert summary["rejected_count"] == 1
    assert summary["blocked_shards"] == ["ui"]
    assert summary["median_accepted_duration_ms"] == 40
    assert summary["max_observed_rss_kb"] == 200


def test_run_shard_matrix_uses_scope_probe_per_shard(monkeypatch, tmp_path):
    module = _load_module()
    calls = []

    def fake_run_probe(config):
        calls.append(config)
        return _status(model=config.model, accepted=True, duration_ms=25)

    monkeypatch.setattr(module, "run_probe", fake_run_probe)
    shards = (
        module.GraphifyShard("data", ("services/zoe-data",)),
        module.GraphifyShard("ops", ("scripts", "tools")),
    )

    status = module.run_shard_matrix(root=tmp_path, model="gemma-e4b", shards=shards, timeout_sec=12)

    assert status["probe"] == "graphify_local_shard_matrix"
    assert status["all_accepted"] is True
    assert [result["shard"] for result in status["results"]] == ["data", "ops"]
    assert [call.include_paths for call in calls] == [("services/zoe-data",), ("scripts", "tools")]
    assert all(call.mode == "scope" for call in calls)
    assert all(call.keep_workdir is False for call in calls)
    assert all(call.timeout_sec == 12 for call in calls)


def test_run_shard_matrix_copies_artifacts_and_cleans_probe_workdir(monkeypatch, tmp_path):
    module = _load_module()
    calls = []
    temp_parents = []

    def fake_run_probe(config):
        calls.append(config)
        temp_parent = tmp_path / f"zoe-graphify-local-probe.{len(temp_parents)}"
        temp_parent.mkdir()
        temp_parents.append(temp_parent)
        workdir = temp_parent / "graphify-local-scope"
        graphify_out = workdir / "graphify-out"
        graphify_out.mkdir(parents=True)
        (graphify_out / "graph.json").write_text("{}", encoding="utf-8")
        status = _status(model=config.model, accepted=True, duration_ms=25)
        status["workdir"] = str(workdir)
        return status

    monkeypatch.setattr(module, "run_probe", fake_run_probe)
    artifact_dir = tmp_path / "artifacts"
    shards = (module.GraphifyShard("data-core", ("services/zoe-data",)),)

    status = module.run_shard_matrix(root=tmp_path, model="gemma-e4b", shards=shards, artifact_dir=artifact_dir)

    result = status["results"][0]
    assert calls[0].keep_workdir is True
    assert status["artifact_dir"] == str(artifact_dir)
    assert result["artifact_copied"] is True
    assert result["artifact_graph_json_exists"] is True
    assert result["artifact_graph_json_bytes"] == 2
    assert (artifact_dir / "data-core" / "graphify-out" / "graph.json").exists()
    assert not temp_parents[0].exists()


def test_cleanup_kept_probe_workdir_only_removes_parent_for_probe_temp(tmp_path):
    module = _load_module()
    parent = tmp_path / "repo-owned"
    workdir = parent / "graphify-local-scope"
    workdir.mkdir(parents=True)

    module.cleanup_kept_probe_workdir({"workdir": str(workdir)})

    assert parent.exists()
    assert not workdir.exists()


def test_run_shard_matrix_rejects_unsafe_artifact_shard_name(monkeypatch, tmp_path):
    module = _load_module()

    def fake_run_probe(config):
        raise AssertionError("unsafe artifact shard name should be rejected before probing")

    monkeypatch.setattr(module, "run_probe", fake_run_probe)

    try:
        module.run_shard_matrix(
            root=tmp_path,
            model="gemma-e4b",
            shards=(module.GraphifyShard("bad/name", ("services/zoe-data",)),),
            artifact_dir=tmp_path / "artifacts",
        )
    except ValueError as exc:
        assert "invalid artifact shard name" in str(exc)
    else:
        raise AssertionError("unsafe artifact shard name accepted")


def test_main_writes_status_json_and_allows_partial(monkeypatch, tmp_path, capsys):
    module = _load_module()
    status_json = tmp_path / "shards.json"

    def fake_run_probe(config):
        return _status(model=config.model, accepted=config.include_paths == ("services/zoe-data",), duration_ms=33)

    monkeypatch.setattr(module, "run_probe", fake_run_probe)

    rc = module.main([
        "--root", str(tmp_path),
        "--shard", "data=services/zoe-data",
        "--shard", "ops=scripts",
        "--allow-partial",
        "--status-json", str(status_json),
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "graphify_local_shard_matrix" in captured.out
    written = json.loads(status_json.read_text(encoding="utf-8"))
    assert written["accepted_count"] == 1
    assert written["rejected_count"] == 1
    assert written["blocked_shards"] == ["ops"]


def test_main_returns_1_when_all_shards_are_rejected(monkeypatch, tmp_path):
    module = _load_module()

    monkeypatch.setattr(module, "run_probe", lambda config: _status(model=config.model, accepted=False, duration_ms=33))

    rc = module.main([
        "--root", str(tmp_path),
        "--shard", "data=services/zoe-data",
    ])

    assert rc == 1


def test_main_returns_2_for_invalid_shard(tmp_path, capsys):
    module = _load_module()

    rc = module.main(["--root", str(tmp_path), "--shard", "bad=../secret"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid scoped Graphify path" in captured.err
