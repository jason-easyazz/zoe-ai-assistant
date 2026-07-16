"""Tests for the executor adapter registry routing."""
import pytest

import executor_registry as reg

pytestmark = pytest.mark.ci_safe


@pytest.mark.asyncio
async def test_routes_hermes_assignee_to_kanban(monkeypatch):
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-xyz")
    captured = {}

    async def _fake_dispatch(issue):
        captured["issue"] = issue
        return {"ok": True, "external_ref": "multica:1", "chain": {}, "created": []}

    monkeypatch.setattr(reg._kanban, "dispatch", _fake_dispatch)
    out = await reg.dispatch_issue({"id": "1", "assignee_id": "hermes-xyz", "title": "t"})
    assert out["ok"] is True
    assert captured["issue"]["id"] == "1"


@pytest.mark.asyncio
async def test_skips_non_hermes_assignee(monkeypatch):
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-xyz")
    out = await reg.dispatch_issue({"id": "2", "assignee_id": "someone-else", "title": "t"})
    assert out["ok"] is False
    assert out.get("skipped") is True


@pytest.mark.asyncio
async def test_skips_unassigned(monkeypatch):
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-xyz")
    out = await reg.dispatch_issue({"id": "3", "assignee_id": None, "title": "t"})
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_skips_autopilot_wrapper_titles(monkeypatch):
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-xyz")
    out = await reg.dispatch_issue(
        {"id": "4", "assignee_id": "hermes-xyz", "title": "Autopilot: Platform Health Check"}
    )
    assert out["ok"] is False
    assert out.get("skipped") is True


@pytest.mark.asyncio
async def test_poll_unknown_backend():
    out = await reg.poll_ref("multica:1", backend="nope")
    assert out["found"] is False
