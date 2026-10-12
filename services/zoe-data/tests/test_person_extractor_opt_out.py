"""The memory opt-out reaches the person extractors, which write people / relationship rows BEFORE any MemoryService ingest.

Review finding on the voice-lane opt-out PR: `MEMORY_OPT_OUT_SOURCES` only guards the ingest chokepoint, but
`person_extractor.process_text` / `apply_person_fact` (source "voice" / "conversation") create `people` and
`person_relationships` rows first. Real extractor entry points on a real in-memory SQLite graph; not-opted-out is the
negative control (the same call DOES write).
"""
import pytest

import person_extractor as pe
import person_extractor_llm as pel
import user_prefs
from test_named_relations import SAID, USER, _close_dbs, _names, _open_db, svc  # noqa: F401  (fixtures: svc, autouse _close_dbs)

pytestmark = pytest.mark.ci_safe


@pytest.fixture
def opted(monkeypatch):
    state = {"out": True}

    async def is_out(user_id, *, db=None):
        return state["out"] and user_id == USER

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", is_out)
    return state


@pytest.mark.parametrize("source", ["voice", "conversation", "voice_regex", "chat_regex"])
async def test_opted_out_member_gets_no_people_rows_from_process_text(svc, opted, source):  # noqa: F811
    db = await _open_db()
    assert await pe.process_text(SAID, user_id=USER, source=source, db=db) == 0
    assert await _names(db) == []


async def test_not_opted_out_the_same_call_writes(svc, opted):  # noqa: F811
    opted["out"] = False
    db = await _open_db()
    assert await pe.process_text(SAID, user_id=USER, source="voice", db=db) >= 1
    assert "Dana Whitfield" in await _names(db)


async def test_journal_and_notes_keep_their_recorded_exemption(svc, opted):  # noqa: F811
    db = await _open_db()
    assert await pe.process_text(SAID, user_id=USER, source="journal", db=db) >= 1
    assert "Dana Whitfield" in await _names(db)


async def test_apply_person_fact_refuses_for_an_opted_out_member(svc, opted):  # noqa: F811
    db = await _open_db()
    assert await pe.apply_person_fact("Dana Whitfield", "relationship", "friend", user_id=USER, source="voice", db=db) is False
    assert await _names(db) == []
    opted["out"] = False
    assert await pe.apply_person_fact("Dana Whitfield", "relationship", "friend", user_id=USER, source="voice", db=db) is True


async def test_the_llm_extractor_does_not_even_call_the_model_when_opted_out(svc, opted, monkeypatch):  # noqa: F811
    import httpx

    def boom(*a, **k):
        raise AssertionError("the model was called for an opted-out member")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    assert await pel.process_text_llm("My friend Dana Whitfield is coming to dinner tonight", user_id=USER, source="voice") == 0
