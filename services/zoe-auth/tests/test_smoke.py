"""Active zoe-auth smoke tests for default CI gate."""

import tempfile

from fastapi.testclient import TestClient

from main import app
from models import database as db_module


def test_health_endpoint_ok():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_module.auth_db.db_path = tmp.name
        # main.health_check reads main.db_module.auth_db, same singleton object.
        # A basic sqlite file is enough because endpoint only checks SELECT 1.
        client = TestClient(app)
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") in {"ok", "healthy"}

