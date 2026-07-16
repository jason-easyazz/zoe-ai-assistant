"""Backend contract tests for the Auto Research status surface."""

from __future__ import annotations

import pytest
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routers.autoresearch import router  # noqa: E402

pytestmark = pytest.mark.ci_safe


def test_autoresearch_status_reports_idle_when_no_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_AUTORESEARCH_RUN_ROOT", str(tmp_path / "missing"))
    app = FastAPI()
    app.include_router(router)

    resp = TestClient(app).get("/api/autoresearch/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["surface"] == "autoresearch"
    assert data["status"] == "idle"
    assert data["run_count"] == 0
    assert data["latest"] is None
    assert data["runs"] == []


def test_autoresearch_status_reports_latest_results(tmp_path, monkeypatch):
    run_dir = tmp_path / "night-run"
    run_dir.mkdir()
    (run_dir / "results.tsv").write_text(
        "round\tcommit\tscore\tstatus\tdescription\n"
        "1\tabc123\t10\tbaseline\tbaseline prompt\n"
        "2\tdef456\t12\tkeep\ttighten copy\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_AUTORESEARCH_RUN_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(router)

    resp = TestClient(app).get("/api/autoresearch/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running_or_recorded"
    assert data["run_count"] == 1
    assert data["latest"]["id"] == "night-run"
    assert data["latest"]["rounds"] == 2
    assert data["latest"]["latest_round"] == "2"
    assert data["latest"]["latest_change"] == "tighten copy"
    assert data["latest"]["latest_after"] == "12"
    assert data["runs"][0]["latest_decision"] == "keep"


def test_autoresearch_status_reports_unavailable_with_stable_schema(tmp_path, monkeypatch):
    from routers import autoresearch

    monkeypatch.setenv("ZOE_AUTORESEARCH_RUN_ROOT", str(tmp_path))

    def _raise_oserror():
        raise OSError("permission denied")

    monkeypatch.setattr(autoresearch.Path, "iterdir", lambda self: _raise_oserror())
    app = FastAPI()
    app.include_router(autoresearch.router)

    resp = TestClient(app).get("/api/autoresearch/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "unavailable"
    assert data["run_count"] == 0
    assert data["latest"] is None
    assert data["runs"] == []


def test_autoresearch_status_skips_run_removed_mid_request(tmp_path, monkeypatch):
    from routers import autoresearch

    run_dir = tmp_path / "vanishing-run"
    run_dir.mkdir()
    monkeypatch.setenv("ZOE_AUTORESEARCH_RUN_ROOT", str(tmp_path))

    original_summary = autoresearch._run_summary

    def _vanish(path):
        path.rmdir()
        return original_summary(path)

    monkeypatch.setattr(autoresearch, "_run_summary", _vanish)
    app = FastAPI()
    app.include_router(autoresearch.router)

    resp = TestClient(app).get("/api/autoresearch/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "idle"
    assert data["run_count"] == 0
