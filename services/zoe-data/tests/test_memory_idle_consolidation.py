"""Idle-triggered consolidation: when a conversation goes idle, the WHOLE exchange
is consolidated once through the write-quality gate, and the watermark advances.
"""
import asyncio

import pytest

import memory_idle_consolidation as mic


def _run(c):
    return asyncio.run(c)


class _Row(dict):
    """Behaves like an asyncpg Record for our subscript access."""


class _FakeConn:
    def __init__(self, turns):
        self._turns = turns
        self.executed = []

    async def fetch(self, q, *a):
        return self._turns

    async def execute(self, q, *a):
        self.executed.append((q, a))


def test_fact_text_parsing():
    assert mic._fact_text({"fact": "x"}) == "x"
    assert mic._fact_text({"text": "y"}) == "y"
    assert mic._fact_text("z") == "z"
    assert mic._fact_text({}) == ""


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_IDLE_CONSOLIDATION_ENABLED", raising=False)
    assert mic._enabled() is False
    for on in ("1", "true", "YES"):
        monkeypatch.setenv("ZOE_IDLE_CONSOLIDATION_ENABLED", on)
        assert mic._enabled() is True


def test_sweep_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("ZOE_IDLE_CONSOLIDATION_ENABLED", raising=False)
    assert _run(mic.run_idle_consolidation_sweep()) == {"enabled": False}


def test_consolidate_whole_conversation_gates_and_stores(monkeypatch):
    turns = [
        _Row(role="user", content="My dad's name is Neil", at="2026-06-23T09:00:00+00:00"),
        _Row(role="assistant", content="Got it.", at="2026-06-23T09:00:05+00:00"),
        _Row(role="user", content="do you remember my mum's name?", at="2026-06-23T09:00:09+00:00"),
    ]
    conn = _FakeConn(turns)

    import memory_digest

    async def _fake_extract(text):
        # sees the WHOLE conversation
        assert "Neil" in text and "mum" in text
        return [{"fact": "My dad's name is Neil"},
                {"fact": "Do you remember my mum's name?"}]  # a question slips through extraction

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _fake_extract)

    import memory_quality
    monkeypatch.setattr(memory_quality, "is_storable_fact",
                        lambda t: (False, "question") if t.rstrip().endswith("?") else (True, ""))

    import memory_service
    import expert_dispatch
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: object())
    ingested = []

    async def _fake_ingest(svc, text, **kw):
        ingested.append((text, kw.get("user_id"), kw.get("source")))

    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", _fake_ingest)

    stored = _run(mic.consolidate_session(conn, "sess-1", "jason"))

    assert stored == 1, "the question must be gated out; only the real fact stored"
    assert ingested == [("My dad's name is Neil", "jason", "idle_consolidation")]
    assert conn.executed, "consolidation watermark (state row) must be advanced"


def test_consolidate_skips_too_few_turns(monkeypatch):
    conn = _FakeConn([_Row(role="user", content="hi", at="2026-06-23T09:00:00+00:00")])
    assert _run(mic.consolidate_session(conn, "s", "jason")) == 0


# ── Increment 1b: per-turn user is persisted + resolved from metadata ──────────

def test_saved_message_carries_user_id_in_metadata(monkeypatch):
    """_save_chat_message stamps the resolved user into chat_messages.metadata as
    JSON {"user_id": ...}; guest/empty users leave metadata NULL."""
    import json as _json
    import sys
    import types

    # Stub db_pool so chat._save_chat_message can run without a real Postgres pool.
    captured = []

    class _FakeDB:
        async def execute(self, sql, params=None):
            captured.append((sql, params))

        async def execute_fetchall(self, sql, params=None):
            return []

        async def commit(self):
            pass

    class _Ctx:
        async def __aenter__(self):
            return _FakeDB()

        async def __aexit__(self, *a):
            return False

    fake_db_pool = sys.modules.get("db_pool") or types.ModuleType("db_pool")
    monkeypatch.setattr(fake_db_pool, "get_db_ctx", lambda: _Ctx(), raising=False)
    monkeypatch.setitem(sys.modules, "db_pool", fake_db_pool)

    from routers.chat import _save_chat_message

    # Real user → metadata holds the user_id.
    captured.clear()
    _run(_save_chat_message("sess-1", "user", "hello", user_id="jason"))
    insert = next(p for sql, p in captured if "INSERT INTO chat_messages" in sql)
    meta = insert[4]
    assert _json.loads(meta) == {"user_id": "jason"}

    # Guest → metadata NULL (we don't know whose memory it is).
    captured.clear()
    _run(_save_chat_message("sess-1", "user", "hello", user_id="guest"))
    insert = next(p for sql, p in captured if "INSERT INTO chat_messages" in sql)
    assert insert[4] is None

    # No user_id passed → metadata NULL (back-compat).
    captured.clear()
    _run(_save_chat_message("sess-1", "user", "hello"))
    insert = next(p for sql, p in captured if "INSERT INTO chat_messages" in sql)
    assert insert[4] is None


def test_resolve_owner_from_metadata_most_recent_nonguest():
    rows = [
        _Row(metadata=None),
        _Row(metadata='{"user_id": "jason"}'),
        _Row(metadata='{"user_id": "jason"}'),
    ]
    assert mic._resolve_owner(rows, session_user_id="guest") == "jason"


def test_resolve_owner_falls_back_to_real_session_user():
    rows = [_Row(metadata=None), _Row(metadata=None)]
    assert mic._resolve_owner(rows, session_user_id="alice") == "alice"
    # but not to a guest session user
    assert mic._resolve_owner(rows, session_user_id="guest") is None
    assert mic._resolve_owner(rows, session_user_id="") is None


def test_consolidate_resolves_user_from_metadata(monkeypatch):
    """When turns carry metadata user_id, consolidation writes under THAT user even
    if the caller passed 'guest' (the session fallback)."""
    turns = [
        _Row(role="user", content="My dog is named Rex",
             metadata='{"user_id": "jason"}', at="2026-06-23T09:00:00+00:00"),
        _Row(role="assistant", content="Nice.",
             metadata='{"user_id": "jason"}', at="2026-06-23T09:00:05+00:00"),
    ]
    conn = _FakeConn(turns)

    import memory_digest

    async def _fake_extract(text):
        return [{"fact": "Jason's dog is named Rex"}]

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _fake_extract)

    import memory_quality
    monkeypatch.setattr(memory_quality, "is_storable_fact", lambda t: (True, ""))

    import memory_service
    import expert_dispatch
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: object())
    ingested = []

    async def _fake_ingest(svc, text, **kw):
        ingested.append((text, kw.get("user_id")))

    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", _fake_ingest)

    # caller passes 'guest' (session fallback) but metadata says jason
    stored = _run(mic.consolidate_session(conn, "sess-1", "guest"))
    assert stored == 1
    assert ingested == [("Jason's dog is named Rex", "jason")]


def test_consolidate_skips_guest_only_session(monkeypatch):
    """No metadata user and a guest session user → nothing is written."""
    turns = [
        _Row(role="user", content="hello there", metadata=None, at="2026-06-23T09:00:00+00:00"),
        _Row(role="assistant", content="hi", metadata=None, at="2026-06-23T09:00:05+00:00"),
    ]
    conn = _FakeConn(turns)

    import memory_digest
    called = {"extract": False}

    async def _fake_extract(text):
        called["extract"] = True
        return []

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _fake_extract)

    stored = _run(mic.consolidate_session(conn, "sess-1", "guest"))
    assert stored == 0
    assert called["extract"] is False, "must skip before extraction when no real user"
    assert not conn.executed, "no watermark advance for a skipped session"
