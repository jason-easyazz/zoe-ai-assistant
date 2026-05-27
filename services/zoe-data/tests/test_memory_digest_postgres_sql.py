import pytest

import memory_digest


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    async def execute(self, sql, params=()):
        self.sql.append(sql)
        return _Cursor(self.rows)


@pytest.mark.asyncio
async def test_load_todays_messages_uses_postgres_timestamp_cast():
    db = _FakeDb([("I like quiet mornings",), ("I prefer tea",)])

    text = await memory_digest._load_todays_messages("user-1", db=db)

    assert text == "I like quiet mornings\nI prefer tea"
    assert "(cm.created_at::timestamptz AT TIME ZONE ?)::date" in db.sql[0]
    assert "(now() AT TIME ZONE ?)::date" in db.sql[0]
    assert "CURRENT_DATE" not in db.sql[0]
    assert "DATE('now'" not in db.sql[0]


@pytest.mark.asyncio
async def test_run_digest_for_all_active_users_uses_postgres_timestamp_cast(monkeypatch):
    db = _FakeDb([("user-1",), ("user-2",)])
    seen = []

    async def fake_run_memory_digest(user_id, db=None):
        seen.append(user_id)
        return {"user_id": user_id, "stored": 0}

    monkeypatch.setattr(memory_digest, "run_memory_digest", fake_run_memory_digest)

    results = await memory_digest.run_digest_for_all_active_users(db=db)

    assert [item["user_id"] for item in results] == ["user-1", "user-2"]
    assert seen == ["user-1", "user-2"]
    assert "(cm.created_at::timestamptz AT TIME ZONE ?)::date" in db.sql[0]
    assert "(now() AT TIME ZONE ?)::date" in db.sql[0]
    assert "CURRENT_DATE" not in db.sql[0]
    assert "DATE('now'" not in db.sql[0]
