"""Active zoe-auth smoke tests for default CI gate."""

import sqlite3
import tempfile

from fastapi.testclient import TestClient

from main import app
from models import database as db_module


class SQLiteCompatConnection:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self._last_rowcount = 0

    def execute(self, sql: str, params=()):
        cursor = self.conn.execute(sql, params or ())
        self._last_rowcount = cursor.rowcount if cursor.rowcount >= 0 else 0
        return cursor

    @property
    def total_changes(self) -> int:
        return self._last_rowcount

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.conn.close()
        return False


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

