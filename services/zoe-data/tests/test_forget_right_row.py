""""Forget that" forgets the PREVIOUS TURN - never the newest memory write (QA round 3b, 2026-11-07).

Live, a synthetic demo user said "i secretly hate the colour of our kitchen" -> "forget that", and Zoe answered "Done - I forgot:
<an unrelated, important row about a relative's fall>" and erased it. The forget retracted the newest memory WRITE of the last
10 minutes, but the post-turn capture runs 10-30 minutes behind a chat, so the newest write was another turn's - and the kitchen
fact landed later anyway. "no, don't remember that, forget that" answered by the brain left the capture running too.

Each promise has a negative control: drop the wall and the old failure comes back.

Neutral synthetic fixtures, fake Chroma (``ci_safe``).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import exact_words
import intent_router
import memory_service
import turn_forget
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_0000f0r9"
OTHER = "demo_bar_0000f0r8"
FALL = "my uncle had a fall last week and I keep worrying about him"
FALL_ROW = "User's uncle had a fall last week."
SHED = "i secretly hate the colour of our garden shed"
SHED_ROW = "User secretly dislikes the colour of their garden shed."


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    s = MemoryService(data_dir="/nonexistent/zoe-test-forget-right-row")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    exact_words.set_backend(exact_words.MemoryBackend())
    turn_forget.reset()
    yield s
    turn_forget.reset()
    exact_words.set_backend(None)


def run(coro):
    return asyncio.run(coro)


def texts(svc, user=UID):
    return sorted(r.text for r in run(svc.list_by_status(user_id=user, status="approved")))


def capture(svc, turn_text, fact, *, source="turn_digest"):
    """What the post-turn capture writes for ``turn_text`` (routers.chat runs it inside the turn's capture scope)."""
    async def _go():
        with turn_forget.capture_scope(UID, turn_text):
            return await svc.ingest(fact, user_id=UID, source=source, status="approved", source_excerpt=turn_text)
    return run(_go())


def forget():
    return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))


# ── the round-3b failure ──────────────────────────────────────────────────────────────────────────────────────────

def test_forget_that_never_erases_another_turns_row(svc):
    turn_forget.note_user_turn(UID, FALL, now=time.time() - 600)
    assert capture(svc, FALL, FALL_ROW) is not None              # the earlier turn's capture has landed
    turn_forget.note_user_turn(UID, SHED)                        # its capture is still minutes away
    turn_forget.note_user_turn(UID, "forget that")
    out = forget()
    assert FALL_ROW in texts(svc), "a row from a DIFFERENT turn was erased"
    assert "uncle" not in out and "shed" in out, out
    # ... and the shed turn's capture, landing late, never stores anything
    assert capture(svc, SHED, SHED_ROW) is None
    assert texts(svc) == [FALL_ROW]


def test_negative_control_without_the_wall_the_late_capture_lands(svc, monkeypatch):
    monkeypatch.setattr(turn_forget, "blocks_write", lambda *a, **k: False)
    turn_forget.note_user_turn(UID, SHED)
    forget()
    assert capture(svc, SHED, SHED_ROW) is not None, "the wall is what refuses the late capture"


def test_rows_already_written_from_that_turn_are_forgotten(svc):
    turn_forget.note_user_turn(UID, FALL, now=time.time() - 600)
    capture(svc, FALL, FALL_ROW)
    turn_forget.note_user_turn(UID, SHED)
    capture(svc, SHED, SHED_ROW)
    capture(svc, SHED, "User has a garden shed.", source="chat_regex")
    out = forget()
    assert out.startswith("Done — I forgot") and "and 1 more" in out, out
    assert texts(svc) == [FALL_ROW]


def test_a_row_written_before_the_turn_is_never_its_row(svc):
    # the same words said a day earlier made their own row: "forget that" now is about TODAY's saying
    run(svc.ingest(SHED_ROW, user_id=UID, source="turn_digest", status="approved", source_excerpt=SHED))
    meta = {"source_excerpt": SHED, "added_ts": time.time() - 86400}
    assert not turn_forget.row_is_from_turn(meta, SHED, time.time())
    assert turn_forget.row_is_from_turn({"source_excerpt": SHED, "added_ts": time.time()}, SHED, time.time() - 1)


def test_no_turn_to_bind_to_asks_and_erases_nothing(svc):
    capture(svc, FALL, FALL_ROW)
    assert forget() == intent_router.FORGET_WHICH_REPLY
    assert texts(svc) == [FALL_ROW]


def test_a_question_turn_asks_which_thing(svc):
    capture(svc, FALL, FALL_ROW)
    turn_forget.note_user_turn(UID, "what's on tomorrow?")
    assert forget() == intent_router.FORGET_WHICH_REPLY
    assert texts(svc) == [FALL_ROW]


def test_a_second_forget_never_walks_back_to_an_older_turn(svc):
    turn_forget.note_user_turn(UID, FALL, now=time.time() - 60)
    capture(svc, FALL, FALL_ROW)
    turn_forget.note_user_turn(UID, SHED)
    forget()
    turn_forget.note_user_turn(UID, "forget that")
    assert forget() == intent_router.FORGET_ALREADY_REPLY
    assert texts(svc) == [FALL_ROW]


def test_a_stale_turn_is_no_target(svc):
    turn_forget.note_user_turn(UID, SHED, now=time.time() - turn_forget.TURN_TTL_S - 5)
    assert forget() == intent_router.FORGET_WHICH_REPLY


def test_the_wall_is_owner_scoped_and_leaves_other_turns_alone(svc):
    turn_forget.note_user_turn(UID, SHED)
    forget()
    assert capture(svc, FALL, FALL_ROW) is not None              # another turn's capture is untouched

    async def other():
        with turn_forget.capture_scope(OTHER, SHED):
            return await svc.ingest(SHED_ROW, user_id=OTHER, source="turn_digest", status="approved",
                                    source_excerpt=SHED)
    assert run(other()) is not None                              # another member saying the same words


def test_an_explicit_reteach_after_the_forget_is_stored(svc):
    turn_forget.note_user_turn(UID, SHED)
    forget()
    import ask_to_remember as atr
    out = run(atr.handle("Remember that I secretly hate the colour of our garden shed.", UID, "s1", svc=svc))
    assert out and "remember" in out.lower(), out
    assert any("shed" in t for t in texts(svc))


def test_a_digest_supersede_from_the_retracted_turn_is_refused(svc):
    ref = run(svc.ingest("User's garden shed is green.", user_id=UID, source="chat_regex", status="approved"))
    turn_forget.note_user_turn(UID, SHED)
    forget()

    async def edit():
        with turn_forget.capture_scope(UID, SHED):
            return await svc.review(ref.id, decision="edit", edits=SHED_ROW, actor="turn_digest",
                                    source_excerpt=SHED)
    assert run(edit()) is None
    assert texts(svc) == ["User's garden shed is green."]


# ── "no, don't remember that ... its not a real plan" ─────────────────────────────────────────────────────────────

def test_a_forget_with_a_short_reason_is_still_a_forget_command():
    import memory_quality
    assert memory_quality.is_forget_command("actually no, don't remember that, forget that please. its not a real plan")
    assert not memory_quality.is_forget_command("oh forget that, what's the weather")
    assert not memory_quality.is_forget_command("forget that, it's time to add milk to my list")
    intent = intent_router.detect_intent("actually no, don't remember that, forget that please. its not a real plan",
                                         log_miss=False)
    assert intent is not None and intent.name == "memory_forget_last"


def test_a_forget_the_brain_answered_still_retracts_the_turn(svc):
    from routers import chat
    plan = "random thing, i'm thinking of leaving my job to retrain as a florist"
    turn_forget.note_user_turn(UID, plan)
    capture(svc, plan, "User is thinking of retraining as a florist.", source="chat_regex")
    said = "no wait, don't remember that, forget that"
    turn_forget.note_user_turn(UID, said)
    assert run(chat._persist_memory_candidates_impl(UID, "s1", said, "Big ideas come and go.")) is True
    assert texts(svc) == []
    assert capture(svc, plan, "User is thinking of leaving their job.") is None   # the digest, landing late


def test_turn_notes_keep_no_words_once_retracted(svc):
    turn_forget.note_user_turn(UID, SHED)
    forget()
    with turn_forget._LOCK:
        held = [t.text for t in turn_forget._turns[UID]]
    assert held == [""], "the retracted turn's words stayed in the notes"
