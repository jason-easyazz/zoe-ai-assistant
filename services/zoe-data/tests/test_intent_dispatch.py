"""Tests for /api/system/intent-dispatch (Brick 4b — zoe-core ability dispatch)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import intent_router
from routers.system import router as system_router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(system_router)
    return app


def test_requires_internal_token(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch", json={"user_id": "u", "intent": "list_show", "slots": {}}
    )
    assert resp.status_code == 403


def test_rejects_non_allowlisted_intent(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch",
        json={"user_id": "u", "intent": "rm_rf", "slots": {}},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 400
    assert "not dispatchable" in resp.json()["detail"]


def test_requires_user_id(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch",
        json={"user_id": "", "intent": "list_show", "slots": {}},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 400


def test_happy_path_runs_allowlisted_intent(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    captured = {}

    async def fake_execute_intent(intent, user_id="family-admin"):
        captured["intent"] = intent.name
        captured["slots"] = dict(intent.slots)
        captured["user_id"] = user_id
        return "Here's your shopping list: milk, eggs."

    monkeypatch.setattr(intent_router, "execute_intent", fake_execute_intent)
    resp = TestClient(_app()).post(
        "/api/system/intent-dispatch",
        json={"user_id": "family-admin", "intent": "list_show", "slots": {"list_type": "shopping"}},
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "shopping list" in body["result"]
    assert captured == {
        "intent": "list_show",
        "slots": {"list_type": "shopping"},
        "user_id": "family-admin",
    }
