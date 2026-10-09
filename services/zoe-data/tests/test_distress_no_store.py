"""The no-store guarantee of the distress hand-off, end to end: an enforced turn is never a memory row, never digested, never reflected on by the night mind, never
indexed, and its saved transcript row (and Zoe's reply) carries ``off_record``. Each wall has its control (flag off: the SAME turn goes through). Real code: fast_tiers,
memory_provenance, memory_extractor, MemoryService, exact_words, memory_digest, night_mind; fake Chroma / chat table / model (``ci_safe``). Synthetic user only."""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

import distress_handoff as dh
import exact_words as xw
import memory_provenance as mp
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col
from test_night_mind import lab, life_transcript, night  # noqa: F401 - `lab` is the night-mind fixture

pytestmark = pytest.mark.ci_safe
UID = "demo_bar_00000001"
SAY = "I live in Perth and I want to die"            # a storable fact AND the cue: the extractor WOULD keep the first half
ORDINARY = "I live in Perth and I love hiking"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in (dh.ENV, dh.ENV_GENTLE, mp.ENV, "ZOE_MEMORY_AUTHORITY", "ZOE_DISTRESS_CONTACT_USER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "0")
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    mp.reset()
    dh._NOTIFIED.clear()
    yield
    mp.reset()


@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-distress")
    col = _Col()
    s._collection = lambda: col

    async def none(*_a, **_k):
        return None

    async def not_opted_out(_uid):
        return False
    s._append_audit = none
    monkeypatch.setattr(memory_service, "_user_opted_out", not_opted_out)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    return s


def rows(svc):
    return [r.text for st in ("approved", "pending", "rejected", "archived") for r in run(svc.list_by_status(user_id=UID, status=st, limit=500))]


def turn(text, user=UID):
    """One turn as the live lanes run it: the save (first sight), the tiers, then the post-turn memory writers."""
    import fast_tiers
    import memory_extractor
    mp.claim_turn(user, text)
    res = run(fast_tiers.resolve(text, user, "s1", channel="voice"))
    run(memory_extractor.extract_and_ingest(text, "ok", user_id=user, session_id="s1"))
    run(xw.index_turn(user, text))
    return res


def test_a_distress_turn_is_answered_by_the_pointer_and_stored_nowhere_and_the_same_fact_in_an_ordinary_turn_is_stored(svc):
    assert turn(ORDINARY) is None                                                     # the control: the same fact, no cue
    assert any("Perth" in t for t in rows(svc)) and run(xw.get_backend().count(UID)) == 1
    before = rows(svc)
    res = turn(SAY)
    assert res.tier == "distress_handoff" and rows(svc) == before and run(xw.get_backend().count(UID)) == 1
    assert mp.is_off_record(UID, SAY) and mp.reply_is_off_record(UID)
    assert run(dh.handle(SAY, "guest")) and rows(svc) == before                       # a guest is answered too (and has no account to store to)


def test_the_flag_off_is_the_control_and_shadow_logs_and_changes_nothing(svc, monkeypatch, caplog):
    monkeypatch.setenv(dh.ENV, "off")
    assert turn(SAY) is None and any("Perth" in t for t in rows(svc)) and not mp.is_off_record(UID, SAY)
    monkeypatch.setenv(dh.ENV, "shadow")
    with caplog.at_level("INFO"):
        assert turn(SAY) is None
    assert f"DISTRESS_HANDOFF user={UID} tier=handoff lang=en mode=shadow" in caplog.text


@pytest.mark.parametrize("removed,stored", [("both", True), ("choke_point", False), ("hook", False)])
def test_each_wall_alone_holds_and_with_both_gone_the_turn_is_stored(svc, monkeypatch, removed, stored):
    """The per-turn hook (``is_off_record``) and the ingest choke point (``blocks_write``) are two walls in series."""
    import memory_extractor
    mp.mark_turn(UID, SAY)
    if removed in ("both", "choke_point"):
        monkeypatch.setattr(mp, "blocks_write", lambda *a, **k: False)
    if removed in ("both", "hook"):
        monkeypatch.setattr(mp, "is_off_record", lambda *a, **k: False)
    run(memory_extractor.extract_and_ingest(SAY, "ok", user_id=UID, session_id="s1"))
    assert bool(rows(svc)) is stored


@pytest.mark.parametrize("provenance", ["1", "0"])
def test_the_choke_point_refuses_a_row_built_from_the_turn_with_provenance_on_or_off(svc, monkeypatch, provenance):
    monkeypatch.setenv(mp.ENV, provenance)                                           # a floor: it holds with the provenance feature off too
    mp.mark_turn(UID, SAY)
    run(svc.ingest("User wants to die", user_id=UID, source="chat_regex", source_excerpt=SAY, confidence=0.9, session_id="s1"))
    assert rows(svc) == []
    run(svc.ingest("User's dog is called Juniper", user_id=UID, source="chat_regex", source_excerpt="my dog is Juniper", confidence=0.9, session_id="s1"))
    assert len(rows(svc)) == 1


def test_the_seam_marks_the_turn_and_a_turn_nobody_claimed_is_still_off_the_record(svc):
    import memory_extractor
    assert run(dh.handle(SAY, UID, dry=True)) and not mp.reply_is_off_record(UID)      # a dry run marks nothing
    assert run(dh.handle(SAY, UID)) and mp.reply_is_off_record(UID) and mp.blocks_write(UID, "User wants to die", SAY)
    mp.reset()                                                                        # a restart: no state, the text test still holds
    assert mp.is_off_record(UID, SAY) and not mp.is_off_record(UID, ORDINARY)
    assert run(memory_extractor.extract_and_ingest(SAY, "ok", user_id=UID, session_id="s1")) == 0 and rows(svc) == []


def test_the_digest_reader_and_the_night_mind_skip_it_by_text_even_with_no_flag_on_the_row(lab, monkeypatch):
    import memory_digest as md
    in_rows = [(ORDINARY, "m1", "2026-10-09T01:00:00Z"), (SAY, "m2", "2026-10-09T01:05:00Z")]
    assert [t for _i, t in run(md._transcript_from_rows(UID, in_rows)).turns] == [ORDINARY]    # the row has NO off_record flag: the text test caught it
    day = life_transcript()
    extra = [("t900", SAY), ("t901", "and then I took too many pills yesterday")]
    tr = md.Transcript(str(day) + "\n" + "\n".join(t for _i, t in extra), tuple(day.turns) + tuple(extra), tuple(day.times) + (day.times[-1],) * 2)
    res, rec = night(tr)
    assert res["status"] == "ran" and not any("want to die" in p or "too many pills" in p for p in rec.prompts)
    assert not any("die" in str(o) or "pills" in str(o) for o in lab.obs_rows.values())
    monkeypatch.setenv(dh.ENV, "off")                                                 # control: the same day without the floor feeds it to the pass
    assert len(run(md._transcript_from_rows(UID, in_rows)).turns) == 2
    assert any("want to die" in p for p in night(tr)[1].prompts)


def test_the_saved_transcript_rows_for_the_turn_and_for_zoes_reply_carry_off_record(monkeypatch):
    import contextlib
    from unittest.mock import AsyncMock, MagicMock

    import db_pool
    from routers import chat
    db = MagicMock(execute=AsyncMock(), commit=AsyncMock())

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setenv(mp.ENV, "0")                                                   # even with the provenance feature OFF: a floor, not a feature
    for role, text, user in (("user", "what time is it", UID), ("user", SAY, UID), ("assistant", "I'm really glad you told me.", UID), ("user", SAY, "guest")):
        run(chat._save_chat_message("s1", role, text, user_id=user))
    inserts = [c.args[1] for c in db.execute.await_args_list if c.args[0].startswith("INSERT INTO chat_messages")]
    assert [(json.loads(p[4]) if p[4] else {}).get("off_record") for p in inserts] == [None, True, True, True]


def test_every_reader_that_rebuilds_memory_from_the_transcript_text_names_the_distress_check():
    """The class: a new reader that skips the text test is the next leak - extend this table (the SQL flag is pinned in test_provenance_answers)."""
    import memory_digest as md
    import night_mind as nm
    for fn in (md._transcript_from_rows, md._extract_open_loops, nm.turns_of):
        assert "guarded_text" in inspect.getsource(fn)
