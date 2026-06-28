import pytest

import db_pool
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


class _FakeCtx:
    """Async-context-manager stand-in for db_pool.get_db_ctx()."""

    def __init__(self, db):
        self.db = db
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self.db

    async def __aexit__(self, *exc):
        self.exited += 1
        return False


@pytest.mark.asyncio
async def test_load_todays_messages_uses_postgres_timestamp_cast():
    db = _FakeDb([("I like quiet mornings",), ("I prefer tea",)])

    text = await memory_digest._load_todays_messages("user-1", db=db)

    assert text == "I like quiet mornings\nI prefer tea"
    assert "(cm.created_at::timestamptz AT TIME ZONE ?)::date" in db.sql[0]
    assert "(now() AT TIME ZONE ?)::date" in db.sql[0]
    assert "NULLIF(cm.metadata, '')::jsonb->>'user_id'" in db.sql[0]
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
    assert "NULLIF(cm.metadata, '')::jsonb->>'user_id'" in db.sql[0]
    assert "CURRENT_DATE" not in db.sql[0]
    assert "DATE('now'" not in db.sql[0]


@pytest.mark.asyncio
async def test_run_digest_for_all_active_users_uses_message_metadata_owner(monkeypatch):
    db = _FakeDb([("jason",)])
    seen = []

    async def fake_run_memory_digest(user_id, db=None):
        seen.append(user_id)
        return {"user_id": user_id, "stored": 0}

    monkeypatch.setattr(memory_digest, "run_memory_digest", fake_run_memory_digest)

    results = await memory_digest.run_digest_for_all_active_users(db=db)

    assert [item["user_id"] for item in results] == ["jason"]
    assert seen == ["jason"]
    assert "NULLIF(cm.metadata, '')::jsonb->>'user_id'" in db.sql[0]
    assert "cs.user_id" in db.sql[0]


@pytest.mark.asyncio
async def test_run_digest_for_all_active_users_db_none_uses_get_db_ctx(monkeypatch):
    """db=None must acquire via the context manager, not the suspended-generator
    `async for db in get_db(): break` form that closed the connection mid-query."""
    db = _FakeDb([("user-1",), ("user-2",)])
    ctx = _FakeCtx(db)
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: ctx)

    seen = []

    async def fake_run_memory_digest(user_id, db=None):
        seen.append((user_id, db))
        return {"user_id": user_id}

    monkeypatch.setattr(memory_digest, "run_memory_digest", fake_run_memory_digest)

    results = await memory_digest.run_digest_for_all_active_users()  # db defaults to None

    assert ctx.entered == 1 and ctx.exited == 1  # pooled connection acquired + released
    assert [r["user_id"] for r in results] == ["user-1", "user-2"]
    # per-user digests self-acquire (db=None passed through) — we don't hold the
    # listing connection across the LLM loop.
    assert seen == [("user-1", None), ("user-2", None)]


@pytest.mark.asyncio
async def test_run_weekly_consolidation_for_all_fallback_uses_get_db_ctx(monkeypatch):
    """When MemoryService has no list_users(), the chat_sessions fallback must
    acquire via get_db_ctx (the previous suspended-generator form logged
    'could not list users: connection was closed in the middle of operation')."""
    import memory_service

    class _SvcNoListUsers:
        pass  # no list_users attr → AttributeError → fallback path

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _SvcNoListUsers())

    db = _FakeDb([("alice",), ("bob",), (None,)])  # None filtered out
    ctx = _FakeCtx(db)
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: ctx)

    consolidated = []

    async def fake_run_weekly_consolidation(user_id):
        consolidated.append(user_id)
        return {"user_id": user_id}

    monkeypatch.setattr(memory_digest, "run_weekly_consolidation", fake_run_weekly_consolidation)

    results = await memory_digest.run_weekly_consolidation_for_all()  # db=None

    assert ctx.entered == 1 and ctx.exited == 1
    assert consolidated == ["alice", "bob"]
    assert [r["user_id"] for r in results] == ["alice", "bob"]
    assert "NULLIF(cm.metadata, '')::jsonb->>'user_id'" in db.sql[0]


@pytest.mark.asyncio
async def test_run_dreaming_for_all_fallback_db_none_uses_get_db_ctx(monkeypatch):
    """The chat_sessions fallback (MemoryService has no list_users) must acquire
    via get_db_ctx, not the suspended-generator `async for db in get_db(): break`
    form that logged 'dreaming: could not list users'."""
    import memory_service

    class _SvcNoListUsers:
        pass  # no list_users attr → AttributeError → fallback path

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _SvcNoListUsers())

    db = _FakeDb([("alice",), ("bob",), (None,)])  # None filtered out
    ctx = _FakeCtx(db)
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: ctx)

    seen = []

    async def fake_run_dreaming_cycle(user_id, db=None, run_agent_sync_phase=True):
        seen.append((user_id, db))
        return {"user_id": user_id}

    monkeypatch.setattr(memory_digest, "run_dreaming_cycle", fake_run_dreaming_cycle)

    results = await memory_digest.run_dreaming_for_all()  # db=None

    assert ctx.entered == 1 and ctx.exited == 1  # pooled connection acquired + released
    assert [r["user_id"] for r in results] == ["alice", "bob"]
    # per-user cycles self-acquire (db=None passed through) — the listing
    # connection isn't held across per-user work.
    assert seen == [("alice", None), ("bob", None)]
    assert "NULLIF(cm.metadata, '')::jsonb->>'user_id'" in db.sql[0]


@pytest.mark.asyncio
async def test_run_music_taste_digest_for_all_db_none_uses_get_db_ctx(monkeypatch):
    """The music-events listing must acquire via get_db_ctx, not the
    suspended-generator form that logged 'music_taste_digest: could not list
    users: connection was closed in the middle of operation'."""
    db = _FakeDb([("alice",), ("bob",), (None,)])  # None filtered out
    ctx = _FakeCtx(db)
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: ctx)

    seen = []

    async def fake_run_music_taste_digest(user_id):
        seen.append(user_id)
        return {"user_id": user_id}

    monkeypatch.setattr(memory_digest, "run_music_taste_digest", fake_run_music_taste_digest)

    results = await memory_digest.run_music_taste_digest_for_all()  # db=None

    assert ctx.entered == 1 and ctx.exited == 1
    assert [r["user_id"] for r in results] == ["alice", "bob"]
    assert seen == ["alice", "bob"]
    assert "SELECT DISTINCT user_id FROM music_listening_events" in db.sql[0]
