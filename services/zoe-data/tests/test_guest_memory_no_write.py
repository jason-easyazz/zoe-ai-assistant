"""A turn that identified nobody files NO durable memory - for anyone, by any writer.

Live 2026-10-14: since strict voice identity an unrecognised voice runs as ``guest``, and the post-turn digest stored that turn's
personal disclosures as approved ``user=guest`` rows (the shared guest bucket every guest session reads; "identity from the
account" broken). DISCARD, not hold: an unidentified turn's words cannot be attributed to a member later without proof.

Pinned, each with a member negative control (the same turn from an identified member IS written):

* ``user_filters.is_unidentified`` - every sentinel, case/space-insensitive; a member is not one;
* the write chokepoint: ``MemoryService.ingest`` / ``record_candidate`` / ``review(edit)`` of a guest-owned row;
* the voice lane: ``_run_voice_memory_passes`` runs no writer (fact, digest, both person extractors, suggestions);
* the chat lane: ``_persist_memory_candidates_impl`` runs no writer for ANY sentinel (it used to check ``"guest"`` only);
* the writers themselves: ``run_turn_digest`` (no model call), ``extract_and_ingest``, the person extractors, the suggestion
  store, the voice ``people_introduce`` contact.

Break-the-fix: drop the ingest wall -> ``test_ingest_refuses_every_sentinel`` goes red; drop the voice gate ->
``test_voice_passes_run_no_writer_for_a_guest`` goes red.

Invented names and facts only; fake Chroma; no network (ci_safe).
"""
from __future__ import annotations

import asyncio

import pytest

import memory_digest
import memory_extractor
import memory_service
import user_filters
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

SENTINELS = ["guest", "Guest", " guest ", "anonymous", "voice-guest", "voice-daemon", ""]
MEMBER = "demo_member_wren"
TURN = "I manage a little bakery in town and I swim at the lake most mornings"


def test_is_unidentified_covers_every_sentinel_and_no_member():
    for s in SENTINELS + [None]:
        assert user_filters.is_unidentified(s), s
    for m in (MEMBER, "jason", "guestbook-owner", "family-admin"):
        assert not user_filters.is_unidentified(m), m


# ── the chokepoint ───────────────────────────────────────────────────────────

@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-guest-no-write")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


@pytest.mark.parametrize("who", [s for s in SENTINELS if s.strip()])
def test_ingest_refuses_every_sentinel(svc, who, caplog):
    caplog.set_level("INFO", logger=memory_service.logger.name)
    ref = asyncio.run(svc.ingest("User manages a little bakery.", user_id=who, source="voice_turn_digest",
                                 status="approved", anchor_text=TURN))
    assert ref is None and not svc._col.rows
    assert "GUEST_MEMORY_DISCARD" in caplog.text


def test_control_a_member_ingest_is_written(svc):
    ref = asyncio.run(svc.ingest("User manages a little bakery.", user_id=MEMBER, source="voice_turn_digest",
                                 status="approved", anchor_text=TURN))
    assert ref is not None and len(svc._col.rows) == 1


def test_record_candidate_refuses_a_guest_and_keeps_a_member(svc):
    assert asyncio.run(svc.record_candidate("User's sister is Ines.", user_id="guest", writer="person_extractor")) is None
    assert not svc._col.rows
    assert asyncio.run(svc.record_candidate("User's sister is Ines.", user_id=MEMBER, writer="person_extractor"))


def test_an_edit_of_a_guest_owned_row_writes_nothing(svc):
    svc._col.rows["zoe_guest_x"] = ("User likes kites.", {"user_id": "guest", "wing": "guest", "status": "approved"})
    svc._get_sync = lambda mid: memory_service.MemoryRef(id=mid, text=svc._col.rows[mid][0], metadata=dict(svc._col.rows[mid][1])) \
        if mid in svc._col.rows else None
    before = dict(svc._col.rows)
    assert asyncio.run(svc.review("zoe_guest_x", decision="edit", edits="User likes box kites.", actor="turn_digest")) is None
    assert svc._col.rows == before


# ── the voice lane ───────────────────────────────────────────────────────────

def _voice_passes(monkeypatch, user):
    import latent_intent_detector
    import person_extractor
    import person_extractor_llm
    import routers.voice_tts as v

    called: list[str] = []

    def rec(name):
        async def fn(*_a, **_k):
            called.append(name)
            return 0
        return fn

    monkeypatch.setattr(memory_extractor, "extract_and_ingest", rec("extract"))
    monkeypatch.setattr(memory_digest, "run_turn_digest", rec("digest"))
    monkeypatch.setattr(person_extractor, "process_text", rec("person"))
    monkeypatch.setattr(person_extractor_llm, "process_text_llm", rec("person_llm"))
    monkeypatch.setattr(latent_intent_detector, "detect_and_store", rec("suggest"))
    monkeypatch.setattr(v, "_spawn_bg", lambda coro: asyncio.get_event_loop().create_task(coro))
    import struggle_detector   # its queue worker is not this test's subject (and outlives asyncio.run)
    monkeypatch.setattr(struggle_detector, "observe", lambda *a, **k: False)

    async def go():
        await v._run_voice_memory_passes(TURN, "Sounds lovely.", user, "voice-panel-s1")
        await asyncio.sleep(0)

    asyncio.run(go())
    return called


@pytest.mark.parametrize("who", ["guest", "voice-guest", "anonymous", "voice-daemon", ""])
def test_voice_passes_run_no_writer_for_a_guest(monkeypatch, who):
    assert _voice_passes(monkeypatch, who) == []


def test_control_voice_passes_run_every_writer_for_a_member(monkeypatch):
    assert set(_voice_passes(monkeypatch, MEMBER)) == {"extract", "digest", "person", "person_llm", "suggest"}


# ── the chat lane ────────────────────────────────────────────────────────────

def _chat_passes(monkeypatch, user):
    import latent_intent_detector
    import person_extractor
    import person_extractor_llm
    from routers import chat

    called: list[str] = []

    def rec(name):
        async def fn(*_a, **_k):
            called.append(name)
            return 0
        return fn

    monkeypatch.setattr(memory_extractor, "extract_and_ingest", rec("extract"))
    monkeypatch.setattr(memory_digest, "run_turn_digest", rec("digest"))
    monkeypatch.setattr(person_extractor, "process_text", rec("person"))
    monkeypatch.setattr(person_extractor_llm, "process_text_llm", rec("person_llm"))
    monkeypatch.setattr(latent_intent_detector, "detect_and_store", rec("suggest"))
    asyncio.run(chat._persist_memory_candidates_impl(user, "chat-s1", TURN, "Sounds lovely."))
    return called


@pytest.mark.parametrize("who", ["guest", "voice-guest", "anonymous", "voice-daemon", ""])
def test_chat_passes_run_no_writer_for_any_sentinel(monkeypatch, who):
    assert _chat_passes(monkeypatch, who) == []


def test_control_chat_passes_run_the_writers_for_a_member(monkeypatch):
    assert {"extract", "digest"} <= set(_chat_passes(monkeypatch, MEMBER))


# ── the writers themselves ───────────────────────────────────────────────────

def test_the_turn_digest_makes_no_model_call_for_a_guest(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the digest reached the model for an unidentified turn")

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", boom)
    for who in ("guest", "voice-guest", "anonymous", "voice-daemon"):
        res = asyncio.run(memory_digest.run_turn_digest(who, TURN, session_id="s1"))
        assert res["new"] == 0 and res["skipped_reason"] == "unidentified"


def test_extract_and_ingest_writes_nothing_for_a_guest(svc):
    for who in ("guest", "voice-guest", "anonymous"):
        assert asyncio.run(memory_extractor.extract_and_ingest(
            "I live in Port Fairy", "", user_id=who, session_id="s1", source="voice_regex", prev_user_message="")) == 0
    assert not svc._col.rows
    # control: the same words from a member are a fact
    assert asyncio.run(memory_extractor.extract_and_ingest(
        "I live in Port Fairy", "", user_id=MEMBER, session_id="s1", source="voice_regex", prev_user_message="")) == 1


@pytest.mark.parametrize("who", ["voice-guest", "anonymous", "Guest"])
def test_person_extractors_and_suggestions_refuse_every_sentinel(who):
    import latent_intent_detector
    import pending_suggestions
    import person_extractor
    import person_extractor_llm

    text = "My sister Ines just started a new job at the hospital"
    assert asyncio.run(person_extractor.process_text(text, user_id=who, source="voice", session_id="s1")) == 0
    assert asyncio.run(person_extractor_llm.process_text_llm(text, user_id=who, source="voice", session_id="s1")) == 0
    assert asyncio.run(latent_intent_detector.detect(text, user_id=who, session_id="s1")) == []
    assert asyncio.run(pending_suggestions.store_suggestions(
        user_id=who, session_id="s1", suggestions=[{"kind": "x"}])) == 0


def test_a_guest_introduction_files_no_contact():
    import routers.voice_tts as v

    class _DB:
        def __init__(self):
            self.sql: list[str] = []

        async def execute(self, sql, *_a):
            self.sql.append(sql)
            raise AssertionError("a guest introduction touched the people table")

    db = _DB()
    assert asyncio.run(v._handle_introduce_intent("Ines", "guest", "panel-1", "s1", "k1", db)) == ""
    assert db.sql == []


def test_no_portrait_is_saved_for_a_guest():
    import user_portrait

    class _DB:
        async def execute(self, *_a, **_k):
            raise AssertionError("a guest portrait reached user_portraits")

    for who in ("guest", "voice-guest", "anonymous"):
        assert asyncio.run(user_portrait._save_portrait(who, "A portrait.", 12, db=_DB())) is None
