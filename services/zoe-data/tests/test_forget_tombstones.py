"""Forget tombstones (docs/IDEAS.md → implemented): a forget shadows the name
for a few minutes so in-flight/late extractor writes can't resurrect it, while
an EXPLICIT re-teach clears the shadow immediately.

Fakes only — no DB, no model, no live service.
"""

import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory_tombstones as mt  # noqa: E402

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _clean():
    mt.clear_all()
    yield
    mt.clear_all()


def test_tombstone_matches_whole_word_case_insensitive():
    mt.add("u1", "Delia")
    assert mt.matching_tombstone("u1", "User has a friend named delia.")
    assert mt.matching_tombstone("u1", "Delia: March 15")
    # whole-word only — no substring nuking
    assert mt.matching_tombstone("u1", "Cordelia loves gardening") is None
    # per-user isolation
    assert mt.matching_tombstone("u2", "Delia: March 15") is None


def test_tombstone_expires(monkeypatch):
    mt.add("u1", "Delia", ttl_s=10.0)
    assert mt.matching_tombstone("u1", "Delia is kind")
    real = time.monotonic
    monkeypatch.setattr(mt.time, "monotonic", lambda: real() + 11.0)
    assert mt.matching_tombstone("u1", "Delia is kind") is None


def test_explicit_reteach_clears():
    mt.add("u1", "Delia")
    assert mt.clear_matching("u1", "remember that Delia's birthday is March 25") == 1
    assert mt.matching_tombstone("u1", "Delia: March 25") is None
    # clearing an absent name is a no-op
    assert mt.clear_matching("u1", "remember that Karen loves gardening") == 0


def test_multiword_names():
    mt.add("u1", "Lindsay Cannon")
    assert mt.matching_tombstone("u1", "Lindsay Cannon: 26/10/1982")
    assert mt.matching_tombstone("u1", "met Lindsay yesterday") is None


@pytest.mark.asyncio
async def test_ingest_drops_tombstoned_candidates(monkeypatch):
    """MemoryService.ingest — the chokepoint every extractor lane funnels
    through — must drop a candidate mentioning a tombstoned name and store an
    unrelated one normally."""
    from memory_service import MemoryService

    stored: list[str] = []

    class _Probe(MemoryService):
        def __init__(self):
            self._seen_keys = {}
            self._user_locks = {}

        def _bump(self, *a, **k):
            pass

    svc = _Probe()

    # Stub everything past the tombstone check: reaching _idempotency_key
    # means the candidate survived the guard.
    def _record(user_id, user_turn_id, text, **kw):
        stored.append(text)
        raise RuntimeError("stop-after-guard")

    monkeypatch.setattr(svc, "_idempotency_key", _record)

    mt.add("u1", "Delia")
    assert await svc.ingest("Delia: March 15", user_id="u1", source="turn_digest") is None
    assert stored == [], "tombstoned candidate reached the write path"

    with pytest.raises(RuntimeError):
        await svc.ingest("Karen: loves gardening", user_id="u1", source="turn_digest")
    assert stored == ["Karen: loves gardening"]


@pytest.mark.asyncio
async def test_forget_intent_writes_tombstone(monkeypatch):
    """memory_forget_entity adds the tombstone once the archive ran."""
    import intent_router

    class _Row:
        def __init__(s, i, t):
            s.id, s.text = i, t
            s.metadata = {"user_id": "u1", "status": "approved"}

    class _Svc:
        async def search(self, *a, **k):
            return [_Row("m1", "Delia: March 15")]

        async def list_by_status(self, **k):
            return []

        async def review(self, mem_id, **kw):
            return None

    monkeypatch.setitem(sys.modules, "memory_service", types.SimpleNamespace(
        get_memory_service=lambda: _Svc(),
        is_guest_memory_user=lambda u: u in ("guest", ""),
    ))
    monkeypatch.setitem(sys.modules, "pending_suggestions", types.SimpleNamespace(
        resolve_person_offers_by_name=_noop_resolve))

    reply = await intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "Delia"}), "u1")
    assert "forgotten" in reply.lower()
    assert mt.matching_tombstone("u1", "Delia: March 15")


async def _noop_resolve(user_id, name):
    return 0


@pytest.mark.asyncio
async def test_no_match_forget_still_tombstones(monkeypatch):
    """A forget issued BEFORE the async extractor wrote any row ("I don't have
    anything saved…") must still shadow the name — that IS the in-flight race."""
    import intent_router

    class _EmptySvc:
        async def search(self, *a, **k):
            return []

        async def list_by_status(self, **k):
            return []

    monkeypatch.setitem(sys.modules, "memory_service", types.SimpleNamespace(
        get_memory_service=lambda: _EmptySvc(),
        is_guest_memory_user=lambda u: u in ("guest", ""),
    ))
    reply = await intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "Delia"}), "u1")
    assert "anything saved" in reply.lower()
    assert mt.matching_tombstone("u1", "Delia: March 15")


@pytest.mark.asyncio
async def test_explicit_source_bypasses_drop_but_async_sources_blocked(monkeypatch):
    from memory_service import MemoryService

    reached: list[str] = []

    class _Probe(MemoryService):
        def __init__(self):
            self._seen_keys = {}
            self._user_locks = {}

        def _bump(self, *a, **k):
            pass

    svc = _Probe()

    def _record(user_id, user_turn_id, text, **kw):
        reached.append(text)
        raise RuntimeError("stop-after-guard")

    monkeypatch.setattr(svc, "_idempotency_key", _record)
    mt.add("u1", "Delia")

    # async lane: dropped
    assert await svc.ingest("Delia: March 25", user_id="u1", source="turn_digest") is None
    assert reached == []
    # explicit teach lane: bypasses the shadow (handler clears after success)
    with pytest.raises(RuntimeError):
        await svc.ingest("Delia: March 25", user_id="u1", source="voice_fact")
    assert reached == ["Delia: March 25"]
    # bypass does NOT clear — only a successful teach handler clears
    assert mt.matching_tombstone("u1", "Delia: March 25")


@pytest.mark.asyncio
async def test_reteach_clears_regardless_of_answer_lane(monkeypatch):
    """An explicit 'remember that …' utterance clears the shadow even when the
    semantic router sends the turn to the note/brain lane (live repro: the
    re-teach was silently shadow-dropped because no teach lane ran)."""
    from routers import chat as chat_mod

    async def _noop(*a, **k):
        return 0

    for mod in ("memory_extractor", "memory_digest", "person_extractor",
                "person_extractor_llm", "latent_intent_detector"):
        monkeypatch.setitem(sys.modules, mod, types.SimpleNamespace(
            extract_and_ingest=_noop, run_turn_digest=_noop,
            process_text=_noop, process_text_llm=_noop, detect_and_store=_noop,
        ))

    mt.add("u1", "Delia")
    await chat_mod._persist_memory_candidates(
        "u1", "s1", "remember that Delia's birthday is March 25", "Noted.")
    assert mt.matching_tombstone("u1", "Delia") is None

    # a NON-explicit mention does not clear
    mt.add("u1", "Delia")
    await chat_mod._persist_memory_candidates(
        "u1", "s1", "I saw Delia at the shops today", "Nice!")
    assert mt.matching_tombstone("u1", "Delia")

    # REMINDER shapes ("… to <verb> …") are tasks, not re-teaches — they must
    # NOT clear the shadow (Greptile P1: "don't forget to invite Delia").
    for reminder in ("don't forget to invite Delia",
                     "remember to call Delia tomorrow"):
        await chat_mod._persist_memory_candidates("u1", "s1", reminder, "Okay.")
        assert mt.matching_tombstone("u1", "Delia"), reminder
