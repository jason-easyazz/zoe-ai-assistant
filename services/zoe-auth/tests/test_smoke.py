"""Active zoe-auth smoke tests for default CI gate."""

import tempfile

from fastapi.testclient import TestClient

from tests.sqlite_compat import SQLiteCompatConnection
from main import app
from models import database as db_module


def test_health_endpoint_ok(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        monkeypatch.setattr(
            db_module.auth_db,
            "get_connection",
            lambda: SQLiteCompatConnection(tmp.name),
        )
        client = TestClient(app)
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") in {"ok", "healthy"}

