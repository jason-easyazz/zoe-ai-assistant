"""Shared zoe-auth test helpers."""

from __future__ import annotations

import sqlite3


class SQLiteCompatConnection:
    """Small sqlite-backed stand-in for zoe-auth's connection wrapper."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._last_rowcount = 0

    def execute(self, sql: str, params=()):
        cursor = self.conn.execute(sql, params or ())
        self._last_rowcount = cursor.rowcount if cursor.rowcount >= 0 else 0
        return cursor

    @property
    def total_changes(self) -> int:
        return self._last_rowcount

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False
