"""The pending-correction marker (correction_marker.py, ZOE_CORRECTION_PENDING_MARKER): set, served, cleared, never leaked.

Synthetic members and words. ``chat_messages`` is a dict here (the two DB seams are replaced; one test pins the real read's SQL).
Each behaviour carries a negative control.
"""
from __future__ import annotations

import ast
import inspect
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import correction_marker as cm
import memory_service
import routers.memories as memories_mod
import turn_forget
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe

MEMBER, OTHER = "member-wren", "member-ober"
FIX = "No, the book club is on the 19th, not the 12th"            # synthetic, correction-shaped
OLD_ROW = "The book club meets on the 12th"
HEAD = "## A newer correction from the member"
CHAT: dict = {}                                                     # id -> {user, session, content}
_REAL_FETCH = cm._fetch_texts


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    cm.reset(), turn_forget.reset(), CHAT.clear()
    monkeypatch.setenv("ZOE_CORRECTION_PENDING_MARKER", "1")

    async def find(user_id, session_id, words):
        return next((t for t, r in reversed(CHAT.items()) if (r["user"], r["session"], r["content"]) == (user_id, session_id, words)), None)

    async def fetch(user_id, ids):
        return {t: CHAT[t]["content"] for t in ids if t in CHAT and CHAT[t]["user"] == user_id}

    monkeypatch.setattr(cm, "_find_turn_id", find)
    monkeypatch.setattr(cm, "_fetch_texts", fetch)
    yield
    cm.reset(), turn_forget.reset()


async def _note(words=FIX, user=MEMBER, tid="t1", **kw):
    CHAT[tid] = {"user": user, "session": "s-panel", "content": words}
    return await cm.note_turn(words, user, "s-panel", **kw)


class _Svc:
    async def load_for_prompt(self, user_id, *, limit=20):
        return [MemoryRef(id="old12345", text=OLD_ROW, metadata={}, score=0.0)]

    async def search(self, q, *, user_id, limit=10, **_):
        return []


def _packet(monkeypatch, user=MEMBER, session="s-app"):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(memories_mod, "_svc", lambda: _Svc())
    app = FastAPI()
    app.include_router(memories_mod.router)
    resp = TestClient(app).get("/api/memories/for-prompt", headers={"X-Internal-Token": "tok"},
                               params={"user_id": user, "message": "when is book club", "session_id": session})
    assert resp.status_code == 200
    return resp.json()


async def test_flag_off_sets_nothing_and_the_packet_is_byte_identical(monkeypatch):
    baseline = _packet(monkeypatch)                                 # no marker
    await _note()
    monkeypatch.setenv("ZOE_CORRECTION_PENDING_MARKER", "0")
    assert _packet(monkeypatch) == baseline                         # a marker exists, the flag is off: same bytes
    cm.reset()
    assert await _note() is False and cm.pending(MEMBER) == []
    monkeypatch.setenv("ZOE_CORRECTION_PENDING_MARKER", "1")        # control: on, the same marker changes the packet
    cm.put(MEMBER, "t1")
    assert _packet(monkeypatch)["packet"] != baseline["packet"]


async def test_set_for_a_member_correction_only_and_holds_no_words():
    assert await _note("The book club has a new reading list", tid="t0") is False   # control: not correction-shaped
    assert await _note() is True
    [m] = cm.pending(MEMBER)
    assert m.turn_id == "t1" and m.keys and "book" not in repr(m) and "19th" not in repr(m)


@pytest.mark.parametrize("user,verified", [("guest", None), ("guest-abc123", None), (MEMBER, False)])
async def test_never_set_for_a_guest_or_a_rejected_speaker(user, verified):
    assert await _note(user=user, speaker_verified=verified) is False and cm.pending(user) == []
    assert await _note(user=MEMBER, speaker_verified=True, tid="t9") is True       # control: a verified member


async def test_a_turn_already_retracted_is_not_marked():
    turn_forget.note_user_turn(MEMBER, FIX)
    turn_forget.retract(MEMBER, turn_forget.previous_turn(MEMBER))
    assert await _note() is False


@pytest.mark.parametrize("off_record,rewrote,marked", [(True, 0, 0), (False, 2, 0), (False, 0, 1)])   # last row = the control
async def test_extractor_hook_skips_off_record_and_rewritten_turns(monkeypatch, off_record, rewrote, marked):
    import exact_words
    import memory_extractor
    import memory_provenance
    import value_correction

    async def ret(v):
        return v
    monkeypatch.setattr(memory_provenance, "is_off_record", lambda u, t, **k: off_record)
    monkeypatch.setattr(value_correction, "apply", lambda *a, **k: ret(rewrote))
    monkeypatch.setattr(exact_words, "index_turn", lambda *a, **k: ret(False))
    monkeypatch.setattr(memory_extractor, "_memory_opted_out", lambda u: ret(False))
    monkeypatch.setattr(memory_extractor, "_extract_with_drops", lambda *a, **k: ([], []))
    CHAT["t1"] = {"user": MEMBER, "session": "s-panel", "content": FIX}
    await memory_extractor.extract_and_ingest(FIX, user_id=MEMBER, session_id="s-panel", prev_user_message="")
    assert len(cm.pending(MEMBER)) == marked


async def test_served_first_next_turn_and_in_a_new_session(monkeypatch):
    await _note()
    for session in ("s-panel", "s-phone-app"):
        body = _packet(monkeypatch, session=session)
        assert body["packet"].startswith(HEAD) and f'- "{FIX}"' in body["packet"] and body["pending_corrections"] == 1
        assert body["packet"].index(FIX) < body["packet"].index(OLD_ROW)


async def test_never_in_another_members_or_a_guest_packet(monkeypatch):
    await _note()
    cm.put("guest", "t1")
    assert FIX not in _packet(monkeypatch, user=OTHER)["packet"] and _packet(monkeypatch, user="guest")["packet"] == ""
    assert FIX in _packet(monkeypatch)["packet"]                                    # control: the member's own


async def test_text_is_fetched_by_turn_id_at_read_time(monkeypatch):
    await _note()
    CHAT["t1"]["content"] = "No, the book club is on the 26th"                    # the store, not the marker, holds the words
    assert "26th" in _packet(monkeypatch)["packet"]
    del CHAT["t1"]
    assert HEAD not in _packet(monkeypatch)["packet"] and cm.pending(MEMBER) == []   # a gone turn drops the marker


async def test_at_most_three_newest_each_capped():
    for i in range(5):
        CHAT[f"t{i}"] = {"user": MEMBER, "session": "s", "content": FIX + f" {i}" + " x" * 200}
        cm.put(MEMBER, f"t{i}", now=1000.0 + i)
    texts = await cm.texts_for_packet(MEMBER, now=1010.0)
    assert len(texts) == 3 and texts[0].startswith(FIX + " 4") and all(len(t) <= cm.TEXT_CAP for t in texts)


async def test_the_real_read_filters_by_owner_and_off_record(monkeypatch):
    import db_pool
    from memory_provenance import off_record_sql
    from user_filters import message_owner_expr

    seen = []

    class _Db:
        async def execute(self, sql, params):
            seen.append((sql, params))
            return self

        async def fetchall(self):
            return [("t1", FIX)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _Db())
    assert await _REAL_FETCH(MEMBER, ["t1"]) == {"t1": FIX}
    sql, params = seen[-1]
    assert message_owner_expr() in sql and off_record_sql("cm") in sql and "cm.id IN (?)" in sql and params == (MEMBER, "t1")


async def test_every_forget_path_clears_only_that_member(monkeypatch):
    import forget_topic
    import memory_forget_cascade as mfc

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(mfc, "_clear", noop)
    monkeypatch.setattr(mfc, "retry_pending", noop)

    async def turn():
        turn_forget.note_user_turn(MEMBER, "something else entirely")
        turn_forget.retract(MEMBER, turn_forget.previous_turn(MEMBER))
    for forget in (turn, lambda: forget_topic.forget(object(), MEMBER, "the book club"), lambda: mfc.cascade_forget(MEMBER, "Wren")):
        cm.put(MEMBER, "t1"), cm.put(OTHER, "t7")
        await mfc.cascade_forget(MEMBER, "")                                        # control: a no-op forget clears nothing
        assert len(cm.pending(MEMBER)) == 1
        await forget()
        assert cm.pending(MEMBER) == [] and len(cm.pending(OTHER)) == 1
    src = inspect.getsource(memory_service.MemoryService.delete_user)              # delete_user: too heavy to run here
    assert any(isinstance(n, ast.Attribute) and n.attr == "forget_user" and getattr(n.value, "id", "") == "correction_marker"
               for n in ast.walk(ast.parse("class _X:\n" + src)))


async def test_clear_by_turn_ttl_and_bound():
    cm.put(MEMBER, "t1", now=1000.0)
    assert cm.clear(MEMBER, "nope") is False and len(cm.pending(MEMBER, now=1000.0)) == 1   # control: unknown turn
    assert cm.clear(MEMBER, "t1") is True and cm.pending(MEMBER, now=1000.0) == []
    cm.put(MEMBER, "t2", now=1000.0)
    assert len(cm.pending(MEMBER, now=1000.0 + cm.TTL_S - 1)) == 1                 # control: inside the TTL
    assert cm.pending(MEMBER, now=1000.0 + cm.TTL_S + 1) == []
    for i in range(cm.MAX_PER_USER + 4):
        cm.put(MEMBER, f"b{i}", now=5000.0 + i)
    live = cm.pending(MEMBER, now=5100.0)
    assert len(live) == cm.MAX_PER_USER and live[-1].turn_id == "b4" and live[0].turn_id == f"b{cm.MAX_PER_USER + 3}"


async def test_log_lines_carry_no_text(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    await _note()
    _packet(monkeypatch)
    cm.forget_user(MEMBER, reason="forget_turn")
    msgs = [r.getMessage() for r in caplog.records if "PENDING_CORRECTION" in r.getMessage()]
    assert [m.split()[1] for m in msgs] == ["set", "served", "cleared"] and "reason=forget_turn" in msgs[-1]
    assert "book" in FIX and not [m for m in msgs if "book" in m or "19th" in m]   # control: the planted words exist
