import importlib
import sys

from fastapi.testclient import TestClient


def _load_main_with_internal_token(monkeypatch):
    monkeypatch.delenv("ZOE_INTERNAL_TOKEN", raising=False)
    for module_name in ("auth", "main"):
        sys.modules.pop(module_name, None)
    import auth

    auth = importlib.reload(auth)
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "metrics-test-token")

    import main

    return importlib.reload(main)


def test_metrics_rejects_non_loopback_without_internal_token(monkeypatch):
    main = _load_main_with_internal_token(monkeypatch)

    resp = TestClient(main.app).get("/metrics")

    assert resp.status_code == 403
    assert "X-Internal-Token" in resp.json()["detail"]


def test_metrics_rejects_non_loopback_with_wrong_internal_token(monkeypatch):
    main = _load_main_with_internal_token(monkeypatch)

    resp = TestClient(main.app).get(
        "/metrics",
        headers={"X-Internal-Token": "wrong-token"},
    )

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
