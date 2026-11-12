"""Idle consolidation must not mine turns the voice speaker gate REJECTED as the member's own words."""
import asyncio

import pytest

import memory_idle_consolidation as mic

pytestmark = pytest.mark.ci_safe


class _Row(dict):
    pass


class _FakeConn:
    def __init__(self, turns):
        self._turns = turns
        self.executed = []

    async def fetch(self, q, *a):
        return self._turns

    async def execute(self, q, *a):
        self.executed.append((q, a))


def _case(monkeypatch, turns):
    conn = _FakeConn(turns)
    import expert_dispatch
    import memory_digest
    import memory_quality
    import memory_service
    seen = {"extract": [], "ingest": []}

    async def _extract(text):
        seen["extract"].append(text)
        return [{"fact": "My dad's name is Neil"}] if "Neil" in text else []

    async def _ingest(svc, text, **kw):
        seen["ingest"].append((text, kw.get("user_id"), kw.get("anchor_text")))
        return "stored"

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", _extract)
    monkeypatch.setattr(memory_quality, "is_storable_fact", lambda t: (True, ""))
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: object())
    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", _ingest)
    stored = asyncio.run(mic.consolidate_session("sess-sv", "jason", get_ctx=lambda: _Ctx()))
    return stored, seen, conn


def test_speaker_rejected_turn_is_never_mined_as_the_members_words(monkeypatch):
    """NEGATIVE CONTROL: a turn the speaker gate rejected must not become a member fact."""
    turns = [
        _Row(role="user", content="My dad's name is Neil", at="t1",
             metadata='{"user_id": "jason", "speaker_verified": false}'),
        _Row(role="assistant", content="Hi.", at="t2", metadata='{"user_id": "jason"}'),
        _Row(role="user", content="what time is it", at="t3", metadata='{"user_id": "jason"}'),
    ]
    stored, seen, conn = _case(monkeypatch, turns)
    assert stored == 0 and seen["ingest"] == []
    assert all("Neil" not in t for t in seen["extract"])
    wm = [a for q, a in conn.executed if "memory_consolidation_state" in q]
    assert wm and wm[0][0] == "sess-sv" and wm[0][3] == 3, "the watermark still advances over all rows"


def test_transcript_query_projects_metadata_so_the_gate_mark_reaches_the_filter():
    """The filter reads r['metadata']; the real row query must select it (a fake conn would hide its absence)."""
    seen = {}

    class _C:
        async def fetch(self, q, *a):
            seen["q"] = q
            return []

    asyncio.run(mic._fetch_transcript_rows(_C(), "s", None))
    assert "metadata" in seen["q"].split("FROM")[0]


def test_verified_or_unmarked_turn_still_becomes_a_fact(monkeypatch):
    """Positive control: no-verdict / verified rows are mined exactly as before."""
    turns = [
        _Row(role="user", content="My dad's name is Neil", at="t1", metadata='{"user_id": "jason"}'),
        _Row(role="assistant", content="Hi.", at="t2", metadata='{"user_id": "jason"}'),
        _Row(role="user", content="and I like tea", at="t3",
             metadata={"user_id": "jason", "speaker_verified": True}),
    ]
    stored, seen, _ = _case(monkeypatch, turns)
    assert stored == 1
    assert seen["ingest"][0][0] == "My dad's name is Neil"


def test_rejected_turn_dropped_but_neighbour_kept(monkeypatch):
    turns = [
        _Row(role="user", content="My dad's name is Neil", at="t1", metadata='{"user_id": "jason"}'),
        _Row(role="user", content="my secret is Zorg", at="t2",
             metadata='{"user_id": "jason", "speaker_verified": false}'),
    ]
    _, seen, _ = _case(monkeypatch, turns)
    assert "Zorg" not in seen["extract"][0] and "Neil" in seen["extract"][0]
    assert "Zorg" not in (seen["ingest"][0][2] or "")
