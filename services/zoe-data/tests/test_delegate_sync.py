"""Tests for /api/system/delegate-sync: validated, then an honest 503 (Hermes is retired)."""
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


@pytest.mark.parametrize("target", ["hermes", "openclaw", None])
def test_valid_request_is_answered_503_not_available(monkeypatch, target):
    """Hermes (the only target) is retired: a valid request gets an honest 503, never a
    fake completion, and nothing is dialled (the Hermes client module is gone)."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    body = {"user_id": "jason", "task": "weather in Perth"}
    if target:
        body["target"] = target
    resp = TestClient(_app()).post(
        "/api/system/delegate-sync", json=body, headers={"X-Internal-Token": "tok"}
    )
    assert resp.status_code == 503
    assert "not available" in resp.json()["detail"]


def test_the_hermes_client_is_gone():
    import importlib.util

    import routers.chat as chat

    assert importlib.util.find_spec("chat_hermes_stream") is None
    assert not hasattr(chat, "_hermes_completion")
