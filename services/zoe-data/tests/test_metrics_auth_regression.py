import importlib
import asyncio
import sys
import types

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _restore_swapped_modules():
    """Put the ORIGINAL auth/main module objects back after each test.

    _load_main_with_internal_token pops + reloads both; without restoring the
    saved identities, every later-collected module that resolves ``auth``/
    ``main`` gets a different object than collection-time importers captured —
    the same dependency_overrides-never-match leak class bisected to
    test_auth_unauthenticated on 2026-07-06 (this file leaks identically on the
    self-hosted full-dir lane).
    """
    saved = {name: sys.modules.get(name) for name in ("auth", "main")}
    yield
    for name, mod in saved.items():
        if mod is not None:
            sys.modules[name] = mod
        else:
            sys.modules.pop(name, None)


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

    async def fake_snapshot_collection_sizes():
        return None

    monkeypatch.setattr(memory_metrics, "snapshot_collection_sizes", fake_snapshot_collection_sizes)

    resp = TestClient(main.app).get(
        "/metrics",
        headers={"X-Internal-Token": "metrics-test-token"},
    )

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "zoe_memory_write_count" in resp.text


@pytest.mark.asyncio
async def test_snapshot_collection_sizes_uses_async_service_wrapper_and_preserves_shape(monkeypatch):
    import memory_metrics

    class FakeService:
        def __init__(self):
            self.called = False

        def _collection_sizes_sync(self):  # pragma: no cover - must not run inline
            raise AssertionError("metrics snapshot must not block on sync MemoryService scan")

        async def collection_sizes_by_user(self):
            self.called = True
            await asyncio.sleep(0)
            return {"jason": 2, "family-admin": 1}

    fake_service = FakeService()
    fake_module = types.ModuleType("memory_service")
    fake_module.get_memory_service = lambda: fake_service
    monkeypatch.setitem(sys.modules, "memory_service", fake_module)

    await memory_metrics.snapshot_collection_sizes()

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


@pytest.mark.asyncio
async def test_snapshot_collection_sizes_timeout_clears_stale_gauge(monkeypatch):
    import memory_metrics

    class SlowService:
        async def collection_sizes_by_user(self):
            await asyncio.sleep(1)
            return {"fresh": 1}

    fake_module = types.ModuleType("memory_service")
    fake_module.get_memory_service = lambda: SlowService()
    monkeypatch.setitem(sys.modules, "memory_service", fake_module)

    memory_metrics.mempalace_collection_size.labels(user_id="stale").set(99)

    await memory_metrics.snapshot_collection_sizes(timeout_s=0.01)

    samples = [
        sample
        for metric in memory_metrics.REGISTRY.collect()
        if metric.name == "zoe_mempalace_collection_size"
        for sample in metric.samples
        if sample.name == "zoe_mempalace_collection_size"
    ]
    assert samples == []
