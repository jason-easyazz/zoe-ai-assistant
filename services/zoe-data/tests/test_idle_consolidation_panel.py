"""Panel-shaped (voice-panel-*) sessions reach the idle-consolidation extractor and store, and an all-zero pass is LOUD.

Finding 2026-11-11: 25 passes over real panel turns logged stored=0 rejected=0 skipped=0 dropped=0. These tests pin that the
code path does NOT exclude panel sessions (same entry point, same owner resolution as a harness session) and that a pass
which does nothing says why. Synthetic data only.
"""
import asyncio
import json
import logging

import pytest

import memory_idle_consolidation as mic

pytestmark = pytest.mark.ci_safe


def _run(c):
    return asyncio.run(c)


class _Row(dict):
    pass


class _FakeConn:
    def __init__(self, turns):
        self._turns = turns

    async def fetch(self, q, *a):
        return self._turns

    async def execute(self, q, *a):
        pass


def _ctx(conn):
    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    return lambda: _Ctx()


def _panel_rows(lines=None):
    """Lowercase unpunctuated speech with per-turn metadata, as routers.chat._save_chat_message writes it."""
    meta = json.dumps({"user_id": "member-synth", "traffic_class": "household"})
    lines = lines or [("user", "turn on the kitchen light"), ("assistant", "Done."),
                      ("user", "my sister's name is ana and she lives in lisbon"), ("assistant", "Lovely.")]
    return [_Row(role=r, content=c, metadata=meta, at=f"2026-11-11T09:00:0{i}+00:00")
            for i, (r, c) in enumerate(lines)]


def _wire(monkeypatch, seen):
    import expert_dispatch
    import memory_digest
    import memory_forgotten
    import memory_service

    async def _keep(uid, turns, strict=False):
        return turns, 0

    async def _extract(text):
        seen.append(text)
        if "ana" in text.lower():
            return [{"fact": "The user has a sister named Ana who lives in Lisbon.",
                     "quote": "my sister's name is ana and she lives in lisbon"}]
        return []

    async def _ingest(svc, text, **kw):
        seen.append(("ingest", kw.get("user_id"), kw.get("session_id")))
        return "stored"

    monkeypatch.setattr(memory_forgotten, "keep_unforgotten", _keep)
    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _extract)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: object())
    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", _ingest)


@pytest.mark.parametrize("sid", ["voice-panel-zoe-touch-pi-ab12cd34", "bar-a-123456"])
def test_panel_and_harness_sessions_both_store_a_durable_fact(monkeypatch, sid):
    seen: list = []
    _wire(monkeypatch, seen)
    stored = _run(mic.consolidate_session(sid, "guest", get_ctx=_ctx(_FakeConn(_panel_rows()))))
    assert stored == 1, "a panel-shaped session must reach the extractor and store, same as a harness one"
    assert ("ingest", "member-synth", sid) in seen


def test_all_zero_pass_is_a_warning_naming_the_reason(monkeypatch, caplog):
    _wire(monkeypatch, [])
    mic._zero_streak = 0
    cmds = _panel_rows(lines=[("user", "turn on the kitchen light"), ("assistant", "Done."),
                              ("user", "what time is it"), ("assistant", "Nine.")])
    with caplog.at_level(logging.INFO, logger="memory_idle_consolidation"):
        for _ in range(2):
            _run(mic.consolidate_session("voice-panel-zoe-touch-pi-ab12cd34", "guest",
                                         get_ctx=_ctx(_FakeConn(cmds))))
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "MEMORY_IDLE_ALLZERO" in r.getMessage()]
    assert len(warns) == 2
    msg = warns[-1].getMessage()
    assert "reason=extractor_returned_no_facts" in msg and "zero_streak=2" in msg and "user_turns=2->2" in msg
    assert "kitchen" not in msg, "counts only, never turn text"


def test_pass_with_a_store_is_not_a_zero_warning_and_resets_the_streak(monkeypatch, caplog):
    """Negative control: the same entry point with a durable fact must NOT warn."""
    _wire(monkeypatch, [])
    mic._zero_streak = 5
    with caplog.at_level(logging.INFO, logger="memory_idle_consolidation"):
        _run(mic.consolidate_session("voice-panel-zoe-touch-pi-ab12cd34", "guest",
                                     get_ctx=_ctx(_FakeConn(_panel_rows()))))
    assert not [r for r in caplog.records if "MEMORY_IDLE_ALLZERO" in r.getMessage()]
    assert mic._zero_streak == 0


def test_assistant_only_pass_names_no_user_turns(monkeypatch, caplog):
    _wire(monkeypatch, [])
    rows = _panel_rows(lines=[("assistant", "Good morning."), ("assistant", "Anything else?")])
    with caplog.at_level(logging.WARNING, logger="memory_idle_consolidation"):
        _run(mic.consolidate_session("voice-panel-zoe-touch-pi-ab12cd34", "guest", get_ctx=_ctx(_FakeConn(rows))))
    assert any("reason=no_user_turns" in r.getMessage() for r in caplog.records)
