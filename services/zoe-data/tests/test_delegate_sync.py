"""Tests for /api/system/delegate-sync (Phase 2.5 — zoe-core brain delegation)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
from routers.system import router as system_router

pytestmark = pytest.mark.ci_safe


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(system_router)
    return app


def test_requires_internal_token(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/delegate-sync", json={"user_id": "u", "task": "hi"}
    )
    assert resp.status_code == 403


def test_requires_user_id(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/delegate-sync",
        json={"user_id": "  ", "task": "hi"},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 400
    assert "user_id" in resp.json()["detail"]


def test_requires_task(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/delegate-sync",
        json={"user_id": "jason", "task": "   "},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 400
    assert "task" in resp.json()["detail"]


def test_rejects_non_sync_target(monkeypatch):
    """OpenClaw is explicit-opt-in via the async A2A path, not sync-delegatable."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/delegate-sync",
        json={"user_id": "jason", "task": "do it", "target": "openclaw"},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 400
    assert "not sync-delegatable" in resp.json()["detail"]


def test_happy_path_calls_hermes(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    captured = {}

    async def fake_hermes(message, session_id, user_id, **kwargs):
        captured.update(message=message, session_id=session_id, user_id=user_id)
        return "Hermes says: sunny with a high of 24."

    import routers.chat as chat
    async def _empty(*a, **k):
        return ""
    monkeypatch.setattr(chat, "_safe_load_portrait", _empty)
    monkeypatch.setattr(chat, "_mempalace_load_user_facts", _empty)
    monkeypatch.setattr(chat, "_hermes_completion", fake_hermes)

    resp = TestClient(_app()).post(
        "/api/system/delegate-sync",
        json={"user_id": "jason", "task": "what's the weather?", "target": "hermes"},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target"] == "hermes"
    assert body["ok"] is True
    assert body["result"] == "Hermes says: sunny with a high of 24."
    assert captured["message"] == "what's the weather?"
    assert captured["user_id"] == "jason"


def test_target_defaults_to_hermes(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")

    async def fake_hermes(message, session_id, user_id, **kwargs):
        return "ok"

    import routers.chat as chat
    async def _empty(*a, **k):
        return ""
    monkeypatch.setattr(chat, "_safe_load_portrait", _empty)
    monkeypatch.setattr(chat, "_mempalace_load_user_facts", _empty)
    monkeypatch.setattr(chat, "_hermes_completion", fake_hermes)
    resp = TestClient(_app()).post(
        "/api/system/delegate-sync",
        json={"user_id": "jason", "task": "anything"},  # no target -> hermes
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    assert resp.json()["target"] == "hermes"


def test_hermes_failure_surfaces_502(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")

    async def boom(*args, **kwargs):
        raise RuntimeError("hermes down")

    import routers.chat as chat
    async def _empty(*a, **k):
        return ""
    monkeypatch.setattr(chat, "_safe_load_portrait", _empty)
    monkeypatch.setattr(chat, "_mempalace_load_user_facts", _empty)
    monkeypatch.setattr(chat, "_hermes_completion", boom)
    resp = TestClient(_app()).post(
        "/api/system/delegate-sync",
        json={"user_id": "jason", "task": "x"},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 502
