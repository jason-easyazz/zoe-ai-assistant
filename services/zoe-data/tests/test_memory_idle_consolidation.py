"""Idle-triggered consolidation: when a conversation goes idle, the WHOLE exchange
is consolidated once through the write-quality gate, and the watermark advances.
"""
import asyncio

import pytest

import memory_idle_consolidation as mic

pytestmark = pytest.mark.ci_safe


def _run(c):
    return asyncio.run(c)


class _Row(dict):
    """Behaves like an asyncpg Record for our subscript access."""


class _FakeConn:
    def __init__(self, turns):
        self._turns = turns
        self.executed = []
        self.fetched = []

    async def fetch(self, q, *a):
        self.fetched.append((q, a))
        return self._turns

    async def execute(self, q, *a):
        self.executed.append((q, a))

    async def fetchval(self, q, *a):
        raise AssertionError(
            "consolidate_session must not issue a per-session fetchval — "
            "`since` now comes from find_idle_sessions"
        )


def _ctx_factory(conn, *, checkout_probe=None):
    """Build a get_ctx() replacement that hands out `conn` for each `async with`.

    `checkout_probe`, if given, is a dict tracking how many connections are
    currently checked out (`live`) and the max ever concurrently held (`max`).
    A pooled connection must be released (live back to 0) before the slow Gemma
    extraction runs — so this lets a test assert the pool is not pinned.
    """
    class _Ctx:
        async def __aenter__(self):
            if checkout_probe is not None:
                checkout_probe["live"] += 1
                checkout_probe["max"] = max(checkout_probe["max"], checkout_probe["live"])
            return conn

        async def __aexit__(self, *a):
            if checkout_probe is not None:
                checkout_probe["live"] -= 1
            return False

    def _get_ctx():
        return _Ctx()

    return _get_ctx


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

    stored = _run(mic.consolidate_session("sess-1", "jason", get_ctx=_ctx_factory(conn)))

    assert stored == 1, "the question must be gated out; only the real fact stored"
    assert ingested == [("My dad's name is Neil", "jason", "idle_consolidation")]
    assert conn.executed, "consolidation watermark (state row) must be advanced"


def test_consolidate_skips_too_few_turns(monkeypatch):
    conn = _FakeConn([_Row(role="user", content="hi", at="2026-06-23T09:00:00+00:00")])
    assert _run(mic.consolidate_session("s", "jason", get_ctx=_ctx_factory(conn))) == 0


# ── Increment 1b: per-turn user is persisted + resolved from metadata ──────────

def test_saved_message_carries_user_id_in_metadata(monkeypatch):
    """_save_chat_message stamps the resolved user into chat_messages.metadata as
    JSON {"user_id": ...}; guest/empty users leave metadata NULL."""
    import json as _json

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

    # Import the real module chain FIRST (routers.chat -> database -> db_pool), then
    # patch only get_db_ctx on the already-loaded real db_pool. Replacing the whole
    # db_pool module in sys.modules *before* this import shadowed `database`'s
    # `from db_pool import get_db` and broke the import chain.
    import db_pool
    from routers.chat import _save_chat_message

    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _Ctx())

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
    stored = _run(mic.consolidate_session("sess-1", "guest", get_ctx=_ctx_factory(conn)))
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

    stored = _run(mic.consolidate_session("sess-1", "guest", get_ctx=_ctx_factory(conn)))
    assert stored == 0
    assert called["extract"] is False, "must skip before extraction when no real user"
    assert not conn.executed, "no watermark advance for a skipped session"


# ── Pool discipline: no pooled connection is held across the Gemma call ─────────

def test_no_pooled_connection_held_across_extraction(monkeypatch):
    """The pool-starvation fix: consolidate_session must RELEASE its pooled
    connection before the (slow, ~45s) Gemma extraction runs, and only re-acquire
    a fresh short-lived one to write the watermark. We prove this two ways:
      1. a checkout probe: at most ONE connection is ever checked out at a time,
         and it is fully released (live == 0) at the moment Gemma is invoked;
      2. the fetch (transcript read) and execute (watermark write) each happen
         inside their own short-lived `async with`, not one long-held conn.
    """
    turns = [
        _Row(role="user", content="My cat is named Mochi", metadata='{"user_id": "jason"}',
             at="2026-06-23T09:00:00+00:00"),
        _Row(role="assistant", content="Cute.", metadata='{"user_id": "jason"}',
             at="2026-06-23T09:00:05+00:00"),
    ]
    conn = _FakeConn(turns)
    probe = {"live": 0, "max": 0}

    import memory_digest
    live_during_extract = {"value": None}

    async def _fake_extract(text):
        # At the instant Gemma runs, the pooled connection MUST be released.
        live_during_extract["value"] = probe["live"]
        return [{"fact": "Jason's cat is named Mochi"}]

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _fake_extract)

    import memory_quality
    monkeypatch.setattr(memory_quality, "is_storable_fact", lambda t: (True, ""))

    import memory_service
    import expert_dispatch
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: object())

    async def _fake_ingest(svc, text, **kw):
        # And the connection must also be released during ingest.
        assert probe["live"] == 0, "pooled connection held across svc ingest"

    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", _fake_ingest)

    stored = _run(mic.consolidate_session(
        "sess-1", "jason", get_ctx=_ctx_factory(conn, checkout_probe=probe)))

    assert stored == 1
    assert live_during_extract["value"] == 0, \
        "pooled connection was still checked out when Gemma extraction ran"
    assert probe["max"] == 1, "at most one pooled connection may be checked out at a time"
    assert probe["live"] == 0, "connection leaked — not released after the sweep"
    # Transcript read and watermark write each ran under their own short-lived conn.
    assert conn.fetched, "transcript rows must be read under a short-lived conn"
    assert conn.executed, "watermark must be written under a fresh short-lived conn"


# ── P2: `since` comes from find_idle_sessions, not a per-session fetchval ───────

def test_sweep_threads_since_from_find_idle_sessions(monkeypatch):
    """The sweep must NOT issue a per-session `fetchval` for the watermark — the
    `since` value is folded into find_idle_sessions and threaded straight through
    to consolidate_session (eliminating the N+1 round-trip)."""
    monkeypatch.setenv("ZOE_IDLE_CONSOLIDATION_ENABLED", "1")

    listing_conn = _FakeConn([])  # find_idle_sessions/ensure-table share this conn

    async def _fake_ensure(conn):
        return None

    monkeypatch.setattr(mic, "_ensure_state_table", _fake_ensure)

    sentinel_since = "2026-06-23T08:00:00+00:00"

    async def _fake_find(conn):
        # `since` is provided by the batch query (folded LEFT JOIN watermark).
        return [{"session_id": "sess-1", "user_id": "jason",
                 "last_at": "2026-06-23T09:00:00+00:00", "n": 3, "since": sentinel_since}]

    monkeypatch.setattr(mic, "find_idle_sessions", _fake_find)

    seen = {}

    async def _fake_consolidate(session_id, user_id, since, **kw):
        seen["args"] = (session_id, user_id, since)
        return 2

    monkeypatch.setattr(mic, "consolidate_session", _fake_consolidate)

    # get_db_ctx is only used for the short-lived listing conn now.
    import db_pool
    monkeypatch.setattr(db_pool, "get_db_ctx", _ctx_factory(listing_conn))

    res = _run(mic.run_idle_consolidation_sweep())

    assert res == {"enabled": True, "sessions": 1, "stored": 2}
    # `since` was threaded from find_idle_sessions, NOT re-fetched per session.
    assert seen["args"] == ("sess-1", "jason", sentinel_since)
    assert not listing_conn.executed, "sweep must not issue a per-session fetchval/execute"
