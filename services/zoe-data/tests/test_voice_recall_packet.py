"""_voice_recall_packet builds the COMPACT, QUERY-RELEVANT recall block injected
into the voice brain turn (token-efficient mem0-style read path).

Replaces the old query-blind metadata dump (~1264 chars): it must search with the
turn TEXT, format a small "[What you remember]" block of only the relevant facts,
dedupe, and fall back to the full for-prompt dump when search returns nothing so a
recall question never silently degrades to "I don't know".
"""
import asyncio
from dataclasses import dataclass, field

import pytest

import routers.voice_tts as v

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _Ref:
    text: str
    id: str = "id"
    metadata: dict = field(default_factory=dict)
    score: float = 0.0


def _patch_search(monkeypatch, refs, capture=None):
    """Install a fake MemoryService whose .search records the query and returns refs."""
    import memory_service

    class _FakeSvc:
        async def search(self, query, *, user_id, limit=10, timeout_s=2.0):
            if capture is not None:
                capture["query"] = query
                capture["user_id"] = user_id
                capture["limit"] = limit
            return list(refs)

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _FakeSvc())
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: u == "guest")


def test_searches_with_turn_text(monkeypatch):
    cap: dict = {}
    _patch_search(monkeypatch, [_Ref("My dad's name is Neil")], capture=cap)
    block = _run(v._voice_recall_packet("What is my dad's name?", "jason"))
    assert cap["query"] == "What is my dad's name?"
    assert cap["user_id"] == "jason"
    assert block and "Neil" in block
    assert block.startswith("[What you remember]")


def test_block_is_compact(monkeypatch):
    # Many long facts must be capped (count + per-fact length) so the packet stays
    # a few hundred chars, not the ~1264-char dump it replaces.
    refs = [_Ref(f"Fact number {i} " + ("x" * 400)) for i in range(20)]
    _patch_search(monkeypatch, refs)
    block = _run(v._voice_recall_packet("tell me everything", "jason"))
    assert block is not None
    # At most _VOICE_RECALL_MAX_FACTS bullet lines.
    assert block.count("\n- ") <= v._VOICE_RECALL_MAX_FACTS
    assert len(block) < 1264, "packet not smaller than the dump it replaces"


def test_dedupes_repeated_facts(monkeypatch):
    refs = [_Ref("My mum's name is Janice"), _Ref("my mum's name is Janice  "), _Ref("I live in Geraldton")]
    _patch_search(monkeypatch, refs)
    block = _run(v._voice_recall_packet("who is my mum?", "jason"))
    assert block is not None
    assert block.lower().count("janice") == 1
    assert "Geraldton" in block


def test_falls_back_to_dump_when_search_empty(monkeypatch):
    _patch_search(monkeypatch, [])  # search finds nothing relevant
    import zoe_agent

    async def _dump(user_id, limit=20):
        return "## What I know about you:\n- My dad's name is Neil"

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", _dump)
    block = _run(v._voice_recall_packet("how's the weather", "jason"))
    assert block and "Neil" in block  # fallback dump used, recall preserved


def test_falls_back_when_search_raises(monkeypatch):
    import memory_service
    import zoe_agent

    class _BoomSvc:
        async def search(self, *a, **k):
            raise RuntimeError("embedder down")

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _BoomSvc())
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: False)

    async def _dump(user_id, limit=20):
        return "## What I know about you:\n- My dad's name is Neil"

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", _dump)
    block = _run(v._voice_recall_packet("anything", "jason"))
    assert block and "Neil" in block  # never raises; degrades to the dump


def test_no_text_uses_fallback_dump(monkeypatch):
    # Prewarm path: no turn text yet — warm/return the for-prompt dump so the
    # shared facts cache stays primed for the real turn.
    import zoe_agent

    async def _dump(user_id, limit=20):
        return "## What I know about you:\n- prewarm fact"

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", _dump)
    block = _run(v._voice_recall_packet("", "jason"))
    assert block and "prewarm fact" in block


def test_guest_user_returns_none(monkeypatch):
    _patch_search(monkeypatch, [_Ref("should not be read")])
    block = _run(v._voice_recall_packet("what do you know", "guest"))
    assert block is None


def test_guest_prewarm_empty_text_is_guest_safe(monkeypatch):
    # The wake-prewarm path calls with empty text. A guest must still leak nothing:
    # the is_guest_memory_user guard runs before search, and _voice_brain_memory
    # must return no db_memory for a guest on the empty-text path.
    _patch_search(monkeypatch, [_Ref("should not be read")])
    assert _run(v._voice_recall_packet("", "guest")) is None
    db_memory, _portrait = _run(v._voice_brain_memory("guest", None))
    assert db_memory is None


def test_voice_brain_memory_uses_query_relevant_packet(monkeypatch):
    # The public entry point used by voice_command must thread the turn text into
    # the query-relevant packet.
    cap: dict = {}
    _patch_search(monkeypatch, [_Ref("My dad's name is Neil")], capture=cap)
    import user_portrait

    async def _portrait(user_id):
        return "Warm."

    monkeypatch.setattr(user_portrait, "load_portrait", _portrait)
    db_memory, portrait = _run(v._voice_brain_memory("jason", "What is my dad's name?"))
    assert cap["query"] == "What is my dad's name?"
    assert db_memory and "Neil" in db_memory and db_memory.startswith("[What you remember]")
    assert portrait == "Warm."
