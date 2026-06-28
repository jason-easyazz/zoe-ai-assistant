import importlib
import sys
import types

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


def test_snapshot_collection_sizes_uses_sync_memory_snapshot(monkeypatch):
    import memory_metrics

    class FakeService:
        def __init__(self):
            self.called = False

        def _collection_sizes_sync(self):
            self.called = True
            return {"jason": 2, "family-admin": 1}

        async def collection_sizes_by_user(self):  # pragma: no cover - must not be used
            raise AssertionError("metrics snapshot must not await MemoryService")

    fake_service = FakeService()
    fake_module = types.ModuleType("memory_service")
    fake_module.get_memory_service = lambda: fake_service
    monkeypatch.setitem(sys.modules, "memory_service", fake_module)

    memory_metrics.snapshot_collection_sizes()

    assert fake_service.called is True
    samples = {
        (sample.labels.get("user_id"), sample.value)
        for metric in memory_metrics.REGISTRY.collect()
        if metric.name == "zoe_mempalace_collection_size"
        for sample in metric.samples
        if sample.name == "zoe_mempalace_collection_size"
    }
    assert ("jason", 2.0) in samples
    assert ("family-admin", 1.0) in samples
