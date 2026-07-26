"""_voice_brain_memory feeds the voice brain the facts/portrait it recalls from.

Voice defers people/memory to the brain (fast_tiers voice defer_domains), so this
loader is the ONLY thing giving the Gemma brain facts to recall on a voice turn —
if it returns nothing, every recall question degrades to "I don't know". It must
forward the facts/portrait, normalise empty → None, and be best-effort (a memory
backend hiccup must never break the turn).
"""
import asyncio

import pytest

import routers.voice_tts as v

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


def test_forwards_facts_and_portrait(monkeypatch):
    import zoe_agent
    import user_portrait

    async def _facts(user_id, limit=20):
        return "## What I know about you:\n- My dad's name is Neil"

    async def _portrait(user_id):
        return "Warm, dry sense of humour."

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", _facts)
    monkeypatch.setattr(user_portrait, "load_portrait", _portrait)
    db_memory, portrait = _run(v._voice_brain_memory("jason"))
    assert db_memory and "Neil" in db_memory
    assert portrait == "Warm, dry sense of humour."


def test_empty_results_become_none(monkeypatch):
    import zoe_agent
    import user_portrait

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", _empty)
    monkeypatch.setattr(user_portrait, "load_portrait", _empty)
    db_memory, portrait = _run(v._voice_brain_memory("guest"))
    assert db_memory is None and portrait is None


def test_best_effort_on_backend_error(monkeypatch):
    import zoe_agent
    import user_portrait

    async def _boom(*a, **k):
        raise RuntimeError("memory backend down")

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", _boom)
    monkeypatch.setattr(user_portrait, "load_portrait", _boom)
    # Must not raise — a recall turn still proceeds (brain just gets no context).
    db_memory, portrait = _run(v._voice_brain_memory("jason"))
    assert db_memory is None and portrait is None
