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
