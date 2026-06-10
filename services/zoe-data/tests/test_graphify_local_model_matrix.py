import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "graphify_local_model_matrix.py"
    spec = importlib.util.spec_from_file_location("graphify_local_model_matrix_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _status(model: str, *, accepted: bool, duration_ms: int, rss_kb: int = 1000):
    return {
        "model": model,
        "mode": "smoke",
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
            "nodes": 2 if accepted else None,
            "edges": 1 if accepted else None,
            "code_files": 1,
            "doc_files": 0,
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


def test_summarize_matrix_selects_fastest_accepted_model():
    module = _load_module()

    summary = module.summarize_matrix([
        _status("gemma-e2b", accepted=True, duration_ms=90, rss_kb=2000),
        _status("gemma-e4b", accepted=True, duration_ms=70, rss_kb=3000),
        _status("gemma-12b", accepted=False, duration_ms=120, rss_kb=4000),
    ])

    assert summary["model_count"] == 3
    assert summary["accepted_count"] == 2
    assert summary["rejected_count"] == 1
    assert summary["all_accepted"] is False
    assert summary["any_accepted"] is True
    assert summary["best_accepted_model_by_duration"] == "gemma-e4b"
    assert summary["median_duration_ms"] == 80
    assert summary["max_child_rss_kb"] == 4000
    assert summary["results"][2]["blockers"] == ["invalid_json_chunks"]


def test_run_model_matrix_rejects_repo_mode_without_explicit_allowance(tmp_path):
    module = _load_module()

    try:
        module.run_model_matrix(root=tmp_path, models=("gemma-e2b",), mode="repo")
    except ValueError as exc:
        assert "--allow-repo" in str(exc)
    else:
        raise AssertionError("repo matrix mode was accepted without allow_repo")


def test_run_model_matrix_uses_existing_probe_contract(monkeypatch, tmp_path):
    module = _load_module()
    calls = []

    def fake_run_probe(config):
        calls.append(config)
        return _status(config.model, accepted=True, duration_ms=50)

    monkeypatch.setattr(module, "run_probe", fake_run_probe)

    status = module.run_model_matrix(
        root=tmp_path,
        models=("gemma-e2b", "gemma-e4b"),
        mode="scope",
        include_paths=("services/zoe-data",),
        timeout_sec=12,
    )

    assert status["probe"] == "graphify_local_model_matrix"
    assert status["all_accepted"] is True
    assert status["include_paths"] == ["services/zoe-data"]
    assert [call.model for call in calls] == ["gemma-e2b", "gemma-e4b"]
    assert all(call.mode == "scope" for call in calls)
    assert all(call.keep_workdir is False for call in calls)
    assert all(call.include_paths == ("services/zoe-data",) for call in calls)
    assert all(call.timeout_sec == 12 for call in calls)


def test_main_writes_status_json_and_allows_partial(monkeypatch, tmp_path, capsys):
    module = _load_module()
    status_json = tmp_path / "matrix.json"

    def fake_run_probe(config):
        return _status(config.model, accepted=config.model == "gemma-e2b", duration_ms=33)

    monkeypatch.setattr(module, "run_probe", fake_run_probe)

    rc = module.main([
        "--root", str(tmp_path),
        "--model", "gemma-e2b",
        "--model", "gemma-e4b",
        "--allow-partial",
        "--status-json", str(status_json),
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "graphify_local_model_matrix" in captured.out
    written = json.loads(status_json.read_text(encoding="utf-8"))
    assert written["accepted_count"] == 1
    assert written["rejected_count"] == 1


def test_main_returns_1_when_all_models_are_rejected(monkeypatch, tmp_path):
    module = _load_module()

    def fake_run_probe(config):
        return _status(config.model, accepted=False, duration_ms=33)

    monkeypatch.setattr(module, "run_probe", fake_run_probe)

    rc = module.main([
        "--root", str(tmp_path),
        "--model", "gemma-e2b",
        "--model", "gemma-e4b",
    ])

    assert rc == 1


def test_main_blocks_repo_mode_before_probe(monkeypatch, tmp_path, capsys):
    module = _load_module()
    called = False

    def fake_run_probe(_config):
        nonlocal called
        called = True
        return _status("gemma-e2b", accepted=True, duration_ms=10)

    monkeypatch.setattr(module, "run_probe", fake_run_probe)

    rc = module.main(["--root", str(tmp_path), "--mode", "repo", "--model", "gemma-e2b"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "--allow-repo" in captured.err
    assert called is False
