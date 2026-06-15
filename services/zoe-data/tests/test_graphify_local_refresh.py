import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_dir = Path(__file__).resolve().parents[3] / "scripts" / "maintenance"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "graphify_local_refresh.py"
    spec = importlib.util.spec_from_file_location("graphify_local_refresh_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_status_is_syncable_requires_acceptance_and_report(tmp_path):
    module = _load_module()
    status = {
        "accepted": True,
        "blockers": [],
        "graph_json_exists": True,
        "graph_json_bytes": 128,
        "graph_report_exists": True,
        "workdir": str(tmp_path),
    }

    assert module.status_is_syncable(status) is True
    assert module.status_is_syncable({**status, "graph_report_exists": False}) is False
    assert module.status_is_syncable({**status, "accepted": False}) is False
    assert module.status_is_syncable({**status, "blockers": ["invalid_json_chunks"]}) is False


def test_sync_graphify_out_dry_run_requires_core_files(tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    workdir = tmp_path / "snapshot"
    (workdir / "graphify-out").mkdir(parents=True)
    (workdir / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")

    missing = module.sync_graphify_out(root=root, workdir=workdir, dry_run=True)
    assert missing == {"ok": False, "error": "missing_required_graphify_files", "missing": ["GRAPH_REPORT.md"]}

    (workdir / "graphify-out" / "GRAPH_REPORT.md").write_text("report", encoding="utf-8")
    accepted = module.sync_graphify_out(root=root, workdir=workdir, dry_run=True)
    assert accepted["ok"] is True
    assert accepted["dry_run"] is True


def test_rejected_refresh_writes_marker_and_does_not_sync(monkeypatch, tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    root.mkdir()
    config = module.GraphifyLocalProbeConfig(root=root, mode="repo", cluster=True, keep_workdir=True)

    def run_probe(config):
        return {
            "accepted": False,
            "blockers": ["invalid_json_chunks"],
            "graph_json_exists": True,
            "graph_json_bytes": 200,
            "graph_report_exists": True,
            "workdir": str(tmp_path / "snapshot"),
        }

    def sync_graphify_out(**kwargs):
        raise AssertionError("rejected probe must not sync graphify-out")

    monkeypatch.setattr(module, "run_probe", run_probe)
    monkeypatch.setattr(module, "sync_graphify_out", sync_graphify_out)
    monkeypatch.setattr(module, "cleanup_probe_workdir", lambda root, status: None)

    status = module.run_local_refresh(config, dry_run=False)

    assert status["refresh"]["accepted_for_sync"] is False
    assert status["refresh"]["blockers"] == ["invalid_json_chunks"]
    assert status["workdir"] is None
    marker = root / module.ERROR_MARKER
    assert marker.exists()
    assert "local Graphify refresh rejected" in marker.read_text(encoding="utf-8")


def test_accepted_refresh_syncs_and_clears_marker(monkeypatch, tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    root.mkdir()
    marker = root / module.ERROR_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("old failure", encoding="utf-8")
    config = module.GraphifyLocalProbeConfig(root=root, mode="repo", cluster=True, keep_workdir=True)
    calls = []

    def run_probe(config):
        return {
            "accepted": True,
            "blockers": [],
            "graph_json_exists": True,
            "graph_json_bytes": 200,
            "graph_report_exists": True,
            "workdir": str(tmp_path / "snapshot"),
        }

    def sync_graphify_out(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "source": "snapshot/graphify-out", "destination": "repo/graphify-out"}

    monkeypatch.setattr(module, "run_probe", run_probe)
    monkeypatch.setattr(module, "sync_graphify_out", sync_graphify_out)
    monkeypatch.setattr(module, "cleanup_probe_workdir", lambda root, status: None)

    status = module.run_local_refresh(config, dry_run=False)

    assert status["refresh"]["accepted_for_sync"] is True
    assert status["refresh"]["synced"] is True
    assert status["workdir"] is None
    assert calls
    assert not marker.exists()


def test_accepted_dry_run_keeps_existing_marker(monkeypatch, tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    root.mkdir()
    marker = root / module.ERROR_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("old failure", encoding="utf-8")
    config = module.GraphifyLocalProbeConfig(root=root, mode="repo", cluster=True, keep_workdir=True)

    def run_probe(config):
        return {
            "accepted": True,
            "blockers": [],
            "graph_json_exists": True,
            "graph_json_bytes": 200,
            "graph_report_exists": True,
            "workdir": str(tmp_path / "snapshot"),
        }

    monkeypatch.setattr(module, "run_probe", run_probe)
    monkeypatch.setattr(module, "sync_graphify_out", lambda **kwargs: {"ok": True, "dry_run": True})
    monkeypatch.setattr(module, "cleanup_probe_workdir", lambda root, status: None)

    status = module.run_local_refresh(config, dry_run=True)

    assert status["refresh"]["accepted_for_sync"] is True
    assert status["refresh"]["synced"] is False
    assert marker.read_text(encoding="utf-8") == "old failure"


def test_rsync_failure_writes_error_marker(monkeypatch, tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    root.mkdir()
    config = module.GraphifyLocalProbeConfig(root=root, mode="repo", cluster=True, keep_workdir=True)

    def run_probe(config):
        return {
            "accepted": True,
            "blockers": [],
            "graph_json_exists": True,
            "graph_json_bytes": 200,
            "graph_report_exists": True,
            "workdir": str(tmp_path / "snapshot"),
        }

    monkeypatch.setattr(module, "run_probe", run_probe)
    monkeypatch.setattr(module, "sync_graphify_out", lambda **kwargs: {"ok": False, "error": "rsync_failed"})
    monkeypatch.setattr(module, "cleanup_probe_workdir", lambda root, status: None)

    status = module.run_local_refresh(config, dry_run=False)

    assert status["refresh"]["accepted_for_sync"] is False
    marker = root / module.ERROR_MARKER
    assert marker.exists()
    assert "rsync_failed" in marker.read_text(encoding="utf-8")


def test_sync_graphify_out_reports_rsync_timeout(monkeypatch, tmp_path):
    module = _load_module()
    root = tmp_path / "repo"
    workdir = tmp_path / "snapshot"
    graphify_out = workdir / "graphify-out"
    graphify_out.mkdir(parents=True)
    (graphify_out / "graph.json").write_text("{}", encoding="utf-8")
    (graphify_out / "GRAPH_REPORT.md").write_text("report", encoding="utf-8")

    def run(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(args[0], timeout=kwargs.get("timeout"), output="partial")

    monkeypatch.setattr(module.subprocess, "run", run)

    result = module.sync_graphify_out(root=root, workdir=workdir, dry_run=False, rsync_timeout_sec=1)

    assert result == {"ok": False, "error": "rsync_timed_out", "output": "partial"}
