"""Tests for Multica→Zoe webhook emitter."""
import pytest

import multica_webhook_emitter as mwe

pytestmark = pytest.mark.ci_safe


@pytest.mark.asyncio
async def test_emit_skips_without_secret(monkeypatch):
    monkeypatch.delenv("MULTICA_WEBHOOK_SECRET", raising=False)
    out = await mwe.emit_issue_assigned({"id": "1", "identifier": "ZOE-1"})
    assert out["ok"] is False
    assert out.get("skipped") is True


@pytest.mark.asyncio
async def test_emit_posts_with_secret(monkeypatch):
    monkeypatch.setenv("MULTICA_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("MULTICA_WEBHOOK_TARGET_URL", "http://127.0.0.1:9/board/webhook")
    calls = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "dispatched": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, headers=None):
            calls.append((url, json, headers))
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient())
    out = await mwe.emit_issue_assigned({"id": "u", "identifier": "ZOE-2"})
    assert out["ok"] is True
    assert calls[0][1]["event"] == "issue.assigned"
    assert calls[0][2]["Authorization"] == "Bearer test-secret"
