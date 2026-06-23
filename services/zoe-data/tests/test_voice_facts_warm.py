"""Voice first-turn speed: the user's facts cache must be warmed on wake and live
long enough to survive until the turn fires.

The cold Chroma facts read is ~1.4s and sits on the brain turn's critical path. A
2s cache TTL expired during the wake→speak→STT gap, so every turn re-paid it. These
guard the fix: a long TTL + warming the cache on wake (concurrent with the brain
spawn) so the first turn pays neither.
"""
import asyncio

import pytest

import routers.voice_tts as v


def test_facts_cache_ttl_survives_wake_to_turn():
    import zoe_agent
    # Must outlive wake → speak → STT (several seconds) or the warm is pointless and
    # every turn re-pays the cold read. Writes invalidate the cache, so long is safe.
    assert zoe_agent._USER_FACTS_TTL_S >= 30, "facts cache TTL too short to survive to the turn"


def test_wake_prewarm_warms_facts_cache(monkeypatch):
    panel = "panel-test"
    monkeypatch.setattr(v, "_brain_prewarm_on_wake_enabled", lambda: True)
    monkeypatch.setattr(v, "_get_or_create_voice_session", lambda p: "sess-1")
    monkeypatch.setattr(v, "_VOICE_SESSIONS", {panel: {"bound_user_id": "jason"}})

    warmed = {"brain": False, "facts": False}

    async def _fake_prewarm(user_id, session_id):
        warmed["brain"] = (user_id, session_id) == ("jason", "sess-1")
        return True

    async def _fake_mem(user_id):
        warmed["facts"] = user_id == "jason"
        return ("facts", None)

    import zoe_core_client
    monkeypatch.setattr(zoe_core_client, "prewarm", _fake_prewarm)
    monkeypatch.setattr(v, "_voice_brain_memory", _fake_mem)

    asyncio.run(v._prewarm_brain_for_panel(panel))
    assert warmed["brain"], "wake did not spawn the brain worker"
    assert warmed["facts"], "wake did not warm the facts cache (first turn still pays ~1.4s)"
