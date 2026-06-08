"""Backend contract tests for the Skybridge surface."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routers.skybridge import router  # noqa: E402


def test_skybridge_status_endpoint_returns_runtime_contract():
    app = FastAPI()
    app.include_router(router)

    resp = TestClient(app).get("/api/skybridge/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["surface"] == "skybridge"
    assert data["status"] == "ready"
    assert data["entrypoint"] == "/touch/skybridge.html"
    assert data["card_contract"] == "ag-ui-compatible"
    assert data["transports"]["local_ws"] is True
    assert "livekit" in data["transports"]
    assert data["capabilities"]["settings"] == 22
