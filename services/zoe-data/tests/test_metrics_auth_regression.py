import importlib
import sys

from fastapi.testclient import TestClient


def _load_main_with_internal_token(monkeypatch):
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "metrics-test-token")
    for module_name in ("auth", "main"):
        sys.modules.pop(module_name, None)
    import auth
    import main

    importlib.reload(auth)
    return importlib.reload(main)


def test_metrics_rejects_non_loopback_without_internal_token(monkeypatch):
    main = _load_main_with_internal_token(monkeypatch)

    resp = TestClient(main.app).get("/metrics")

    assert resp.status_code == 403
    assert "X-Internal-Token" in resp.json()["detail"]


def test_metrics_allows_non_loopback_with_valid_internal_token(monkeypatch):
    main = _load_main_with_internal_token(monkeypatch)
    import memory_metrics

    monkeypatch.setattr(memory_metrics, "snapshot_collection_sizes", lambda: None)

    resp = TestClient(main.app).get(
        "/metrics",
        headers={"X-Internal-Token": "metrics-test-token"},
    )

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "zoe_memory_write_count" in resp.text
