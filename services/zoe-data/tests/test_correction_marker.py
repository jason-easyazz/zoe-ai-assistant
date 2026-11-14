"""The pending-correction marker (correction_marker.py, ZOE_CORRECTION_PENDING_MARKER): set, served, cleared, never leaked.

Synthetic members and words. ``chat_messages`` is a dict here (the two DB seams are replaced; one test pins the real read's SQL).
Each behaviour carries a negative control.
"""
from __future__ import annotations

import ast
import inspect
import logging
import re

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
CHAT: dict = {}                                                     # id -> {user, session, content, off}; insertion order = time
_REAL_DB = cm._db
ON_FETCH: list = []
FORGOTTEN: set = set()                                              # the member's forget ledger, as lower-case words                                                 # coroutines run INSIDE the text fetch (the race tests)


class _ChatDb:
    """``chat_messages`` in memory, answering the two query shapes correction_marker builds (the SQL itself is pinned below):
    owner = params[0], off-record rows excluded, then by id, or by session + exact content (COUNT over the whole session)."""

    async def execute(self, sql, params):
        rows = [(t, r) for t, r in reversed(CHAT.items()) if r["user"] == params[0] and not r.get("off")]
        if "cm.id IN" in sql:
            for hook in list(ON_FETCH):
                ON_FETCH.remove(hook)
                await hook()
            self.out = [(t, r["content"]) for t, r in rows if t in params[1:]]
        else:
            window = re.search(r"LIMIT (\d+)", sql)                 # a windowed query (the old bug) sees only the newest N
            rows = [(t, r) for t, r in rows if r["session"] == params[1]][: int(window.group(1)) if window else None]
            hits = [t for t, r in rows if r["content"] == params[2]]
            self.out = [(min(hits) if hits else None, len(hits))]
        return self

    async def fetchall(self):
        return self.out

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    import memory_forgotten
    import memory_tombstones

    cm.reset(), turn_forget.reset(), CHAT.clear(), ON_FETCH.clear(), FORGOTTEN.clear(), memory_tombstones.clear_all()

    async def ledger(user_id, text, **_):
        return user_id == MEMBER and bool(FORGOTTEN & set(re.findall(r"[a-z]+", text.lower())))
    monkeypatch.setattr(memory_forgotten, "matches", ledger)
    monkeypatch.setenv("ZOE_CORRECTION_PENDING_MARKER", "1")
    monkeypatch.setattr(cm, "_db", lambda: _ChatDb())          # only the marker's reads; every other store is untouched
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
    CHAT.clear()                                                                    # (one saved turn, or it is ambiguous)
    assert await _note(user=MEMBER, speaker_verified=True, tid="t9") is True       # control: a verified member


async def test_binds_only_the_one_exact_turn_of_that_member(caplog):
    caplog.set_level(logging.INFO)
    CHAT["t1"] = {"user": MEMBER, "session": "s-panel", "content": FIX + " and the venue moved too"}   # starts with the words
    CHAT["t2"] = {"user": OTHER, "session": "s-panel", "content": FIX}                              # another member's identical turn
    assert await cm.note_turn(FIX, MEMBER, "s-panel") is False and cm.pending(MEMBER) == []
    CHAT["t3"] = CHAT["t4"] = {"user": MEMBER, "session": "s-panel", "content": FIX}                 # two identical turns
    assert await cm.note_turn(FIX, MEMBER, "s-panel") is False and cm.pending(MEMBER) == []
    reasons = [r.getMessage().split()[2] for r in caplog.records if "PENDING_CORRECTION skipped" in r.getMessage()]
    assert reasons == ["reason=not_found", "reason=ambiguous"]
    del CHAT["t4"]                                                                                   # control: the one exact turn binds
    assert await cm.note_turn(FIX, MEMBER, "s-panel") is True and cm.pending(MEMBER)[0].turn_id == "t3"


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
    monkeypatch.setattr(cm, "_db", _REAL_DB)                                       # the real seam, through db_pool
    assert await cm._fetch_texts(MEMBER, ["t1"]) == {"t1": FIX}
    seen_fetch = seen[-1]
    _Db.fetchall = lambda self: _ret([("t1", 2)])
    assert await cm._exact_turns(MEMBER, "s-panel", FIX) == ("t1", 2)
    for (sql, params), tail in zip((seen_fetch, seen[-1]), ("cm.id IN (?)", "cm.session_id = ? AND cm.content = ?")):
        assert message_owner_expr() in sql and off_record_sql("cm") in sql and tail in sql and params[0] == MEMBER
    assert "COUNT(*)" in seen[-1][0] and "LIMIT" not in seen[-1][0] and seen[-1][1] == (MEMBER, "s-panel", FIX)


async def _ret(v):
    return v


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
    src = inspect.getsource(memory_service.MemoryService.delete_user)              # delete_user: the AST pin, second line
    assert any(isinstance(n, ast.Attribute) and n.attr == "forget_user" and getattr(n.value, "id", "") == "correction_marker"
               for n in ast.walk(ast.parse("class _X:\n" + src)))


async def test_the_real_delete_user_clears_the_marker_and_the_block(monkeypatch, tmp_path):
    from test_memory_loss_class import _Col, _svc                                  # palace collections faked, the method is real

    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    await _note()
    cm.put(OTHER, "t7")
    assert HEAD in _packet(monkeypatch)["packet"]                                   # control: served before the forget
    await _svc(_Col(["zoe_wren_aa"]), _Col(), tmp_path).delete_user(MEMBER, actor="admin", reason="rtbf")
    assert cm.pending(MEMBER) == [] and len(cm.pending(OTHER)) == 1
    assert HEAD not in _packet(monkeypatch)["packet"]


async def test_an_identical_turn_anywhere_in_the_session_makes_it_ambiguous():
    CHAT["t00"] = {"user": MEMBER, "session": "s-panel", "content": FIX}          # oldest: outside any newest-50 window
    for i in range(1, 61):
        CHAT[f"t{i:02d}"] = {"user": MEMBER, "session": "s-panel", "content": f"filler turn {i}"}
    CHAT["t99"] = {"user": MEMBER, "session": "s-panel", "content": FIX}
    assert await cm.note_turn(FIX, MEMBER, "s-panel") is False and cm.pending(MEMBER) == []
    del CHAT["t00"]                                                                 # control: then it is the only one
    assert await cm.note_turn(FIX, MEMBER, "s-panel") is True and cm.pending(MEMBER)[0].turn_id == "t99"


async def test_a_turn_flagged_off_the_record_is_never_served(monkeypatch):
    await _note()
    assert HEAD in _packet(monkeypatch)["packet"]                                   # control
    CHAT["t1"]["off"] = True
    assert HEAD not in _packet(monkeypatch)["packet"] and cm.pending(MEMBER) == []


async def _forget_topic():
    import forget_topic

    await forget_topic.forget(object(), MEMBER, "the book club")


async def _forget_entity():
    import memory_forget_cascade as mfc

    await mfc.cascade_forget(MEMBER, "Wren")


async def _forget_member(tmp_path):
    from test_memory_loss_class import _Col, _svc

    await _svc(_Col(["zoe_wren_aa"]), _Col(), tmp_path).delete_user(MEMBER, actor="admin", reason="rtbf")


@pytest.mark.parametrize("which", ["topic", "entity", "delete"])
async def test_a_forget_landing_during_the_fetch_serves_nothing(monkeypatch, tmp_path, which):
    import memory_forget_cascade as mfc

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(mfc, "_clear", noop)
    monkeypatch.setattr(mfc, "retry_pending", noop)
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    forget = {"topic": _forget_topic, "entity": _forget_entity, "delete": lambda: _forget_member(tmp_path)}[which]
    await _note()
    assert HEAD in _packet(monkeypatch)["packet"]                                   # control: no forget, served
    ON_FETCH.append(forget)                                                         # the forget completes mid-fetch
    assert await cm.texts_for_packet(MEMBER) == [] and cm.pending(MEMBER) == []
    assert HEAD not in _packet(monkeypatch)["packet"]


async def test_topic_forget_clears_markers_after_its_body_flagged_the_transcript(monkeypatch):
    import forget_topic

    seen = []

    async def rows(svc, user_id, groups):                     # inside the body, before the transcript rows are flagged
        seen.append(len(cm.pending(user_id)))
        raise RuntimeError("store down")
    monkeypatch.setattr(forget_topic, "_owned_rows", rows)
    cm.put(MEMBER, "t1")
    await forget_topic.forget(object(), MEMBER, "the book club")
    assert seen == [1] and cm.pending(MEMBER) == []           # still there while the body ran; gone after (even on store_down)


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


def test_mark_forgotten_clears_markers_again_after_the_rows_are_flagged():
    """``turn_forget.retract`` clears the member's markers BEFORE the transcript rows are flagged; a marker set in between
    must not outlive the forget, so ``mark_forgotten`` (called once the rows are gone) clears again. Another member's stays."""
    import turn_forget

    cm.reset()
    turn = turn_forget.Turn(text="x", ts=0.0, h="h", tid=7)
    turn_forget.retract(MEMBER, turn)
    cm.put(MEMBER, "turn-set-in-between")
    cm.put(OTHER, "their-turn")
    assert cm.pending(MEMBER)
    turn_forget.mark_forgotten(MEMBER, turn)
    assert not cm.pending(MEMBER)
    assert cm.pending(OTHER)
    cm.reset()


# ── ordering against the forgets, and a forgotten subject ─────────────────────────────────────────────────────────────

async def test_a_marker_set_during_the_entity_cascade_does_not_outlive_it(monkeypatch):
    import memory_forget_cascade as mfc

    async def step(user_id, rx, mutes, res, **k):              # a cascade step while the forget runs
        cm.put(MEMBER, "t-during")
    monkeypatch.setattr(mfc, "_clear", step)
    monkeypatch.setattr(mfc, "retry_pending", lambda *a, **k: _ret(None))
    cm.put(OTHER, "t7")
    await mfc.cascade_forget(MEMBER, "Wren")
    assert cm.pending(MEMBER) == [] and len(cm.pending(OTHER)) == 1


async def test_a_marker_set_during_delete_user_does_not_outlive_it(monkeypatch, tmp_path):
    from test_memory_loss_class import _Col, _svc

    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: cm.put(MEMBER, "t-during"))
    cm.put(OTHER, "t7")
    await _svc(_Col(["zoe_wren_aa"]), _Col(), tmp_path).delete_user(MEMBER, actor="admin", reason="rtbf")
    assert cm.pending(MEMBER) == [] and len(cm.pending(OTHER)) == 1


async def test_the_same_turn_re_marked_during_the_fetch_is_not_served():
    import time

    await _note()

    async def forget_and_re_add():
        cm.forget_user(MEMBER, reason="forget_entity")
        cm.put(MEMBER, "t1", now=time.time() + 1)               # a NEW marker for the same turn id
    ON_FETCH.append(forget_and_re_add)
    assert await cm.texts_for_packet(MEMBER) == []
    assert len(await cm.texts_for_packet(MEMBER)) == 1         # control: the new marker itself is served on the next read


async def test_topic_forget_flags_the_transcript_before_its_final_clear(monkeypatch):
    import conversation_notes
    import forget_topic

    order, real_clear = [], cm.forget_user

    async def erase_words(user_id, groups):
        order.append("flag")
        return 1, 1

    def clear(user_id, reason="forget"):
        order.append("clear")
        return real_clear(user_id, reason)
    monkeypatch.setattr(forget_topic, "_owned_rows", lambda *a: _ret([]))
    monkeypatch.setattr(forget_topic, "_index_hits", lambda *a: _ret({}))
    monkeypatch.setattr(forget_topic, "_withdraw_offers", lambda *a: _ret(0))
    monkeypatch.setattr(forget_topic, "_erase_words", erase_words)
    monkeypatch.setattr(conversation_notes, "erase_topic", lambda *a: _ret(0))
    monkeypatch.setattr(cm, "forget_user", clear)
    await forget_topic.forget(object(), MEMBER, "the book club")
    assert "flag" in order and order[-1] == "clear" and order.index("flag") < len(order) - 1


async def test_entity_forget_redacts_and_cascades_before_its_final_clear(monkeypatch):
    import intent_router
    import memory_forget_cascade as mfc

    order, real_clear = [], cm.forget_user

    async def step(*a, **k):
        order.append("cascade")

    def clear(user_id, reason="forget"):
        order.append("clear")
        return real_clear(user_id, reason)
    monkeypatch.setattr(mfc, "_clear", step)
    monkeypatch.setattr(mfc, "retry_pending", lambda *a, **k: _ret(None))
    monkeypatch.setattr(cm, "forget_user", clear)
    await mfc.cascade_forget(MEMBER, "Wren")
    assert order[-1] == "clear" and order.index("cascade") < len(order) - 1
    # the entity forget redacts the transcript (``_erase_named`` -> forget_redact.on_forget) before EACH cascade call
    src = inspect.getsource(intent_router.execute_intent)
    erase = [m.start() for m in re.finditer(r"await _erase_named\(", src)]
    cascade = [m.start() for m in re.finditer(r"await _forget_cascade_note\(", src)]
    assert src.index("forget_redact.on_forget") < erase[0] and len(erase) == len(cascade) == 2
    assert all(e < c for e, c in zip(erase, cascade))


async def test_a_new_correction_about_a_forgotten_subject_waits_for_a_re_teach(monkeypatch):
    said = "No, Wren is 34, not 33"
    FORGOTTEN.add("wren")                                       # "forget Wren" finished: the ledger names it
    assert await _note(said) is False and cm.pending(MEMBER) == []
    assert await _note("No, Juno is 34, not 33", tid="t2") is True   # control: an unforgotten subject is marked
    cm.reset(), CHAT.clear()                                    # (the new turn is the only one with those words)
    FORGOTTEN.clear()                                           # an explicit re-teach released the ledger entry
    assert await _note(said, tid="t3") is True and said in _packet(monkeypatch)["packet"]
    FORGOTTEN.add("wren")                                       # forgotten again after the marker was set: not served
    assert HEAD not in _packet(monkeypatch)["packet"] and cm.pending(MEMBER) == []


# ── a forget that finishes while note_turn awaits; a failing clear never masks a forget ─────────────────────────────────

async def _forget_turn_both_ends():
    turn = turn_forget.Turn(text="x", ts=0.0, h="h", tid=7)
    turn_forget.retract(MEMBER, turn)
    turn_forget.mark_forgotten(MEMBER, turn)


@pytest.mark.parametrize("which", ["turn", "topic", "entity", "delete", "none"])   # "none" = the control
async def test_a_forget_finishing_while_note_turn_awaits_leaves_no_marker(monkeypatch, tmp_path, which):
    import asyncio

    import memory_forget_cascade as mfc
    import memory_forgotten

    monkeypatch.setattr(mfc, "_clear", lambda *a, **k: _ret(None))
    monkeypatch.setattr(mfc, "retry_pending", lambda *a, **k: _ret(None))
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    reached, release = asyncio.Event(), asyncio.Event()

    async def held_ledger(user_id, text, **_):              # note_turn suspended inside _names_forgotten (its call only)
        if not reached.is_set():
            reached.set()
            await release.wait()
        return False
    monkeypatch.setattr(memory_forgotten, "matches", held_ledger)
    CHAT["t1"] = {"user": MEMBER, "session": "s-panel", "content": FIX}
    task = asyncio.ensure_future(cm.note_turn(FIX, MEMBER, "s-panel"))
    await reached.wait()
    forget = {"turn": _forget_turn_both_ends, "topic": _forget_topic, "entity": _forget_entity,
              "delete": lambda: _forget_member(tmp_path), "none": lambda: _ret(None)}[which]
    await forget()                                           # runs to completion, final clear included
    release.set()
    assert await task is (which == "none")
    assert len(cm.pending(MEMBER)) == (1 if which == "none" else 0)


def _boom(*a, **k):
    raise RuntimeError("marker store down")


async def test_a_failing_marker_clear_never_replaces_a_forgets_result(monkeypatch, tmp_path):
    import forget_topic
    import memory_forget_cascade as mfc

    monkeypatch.setattr(mfc, "_clear", lambda *a, **k: _ret(None))
    monkeypatch.setattr(mfc, "retry_pending", lambda *a, **k: _ret(None))
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    monkeypatch.setattr(cm, "forget_user", _boom)
    assert isinstance(await forget_topic.forget(object(), MEMBER, "the book club"), forget_topic.Result)
    assert (await mfc.cascade_forget(MEMBER, "Wren")).complete
    from test_memory_loss_class import _Col, _svc

    assert await _svc(_Col(["zoe_wren_aa"]), _Col(), tmp_path).delete_user(MEMBER, actor="admin") == 1


async def test_a_forgets_own_exception_still_propagates(monkeypatch):
    import forget_topic
    import memory_forget_cascade as mfc

    async def body(*a, **k):
        raise ValueError("the forget's own failure")
    monkeypatch.setattr(cm, "forget_user", _boom)
    monkeypatch.setattr(forget_topic, "_forget", body)
    monkeypatch.setattr(mfc, "_cascade", body)
    with pytest.raises(ValueError, match="own failure"):
        await forget_topic.forget(object(), MEMBER, "the book club")
    with pytest.raises(ValueError, match="own failure"):
        await mfc.cascade_forget(MEMBER, "Wren")
