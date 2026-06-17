import asyncio
import sys
import types

import pytest

from routers import chat


@pytest.mark.asyncio
async def test_chat_pi_hybrid_action_form_broadcasts_without_tool_memory_side_effects(monkeypatch):
    module = types.ModuleType("pi_hybrid_production")

    class PiHybridProductionConfig:
        @classmethod
        def from_env(cls):
            return cls()

    def pi_hybrid_production_eligible(text, *, config=None):
        return True, "eligible"

    async def processing_cue_packet(*, text=""):
        return {"available": True, "text": "Let me set that up.", "event": {"type": "voice:processing_ack"}}

    async def try_pi_hybrid_production(text, *, user_id, context_turns="", config=None):
        return {
            "accepted": True,
            "reason": "accepted",
            "intent": "timer_create",
            "intent_group": "timers",
            "agreement_kind": "zoe_router",
            "execution_scope": "action_form_prefill",
            "response_text": "Timer is ready to confirm.",
            "action_form": {"component": "timer_create_form", "prefill": {"minutes": 10}},
        }

    module.PiHybridProductionConfig = PiHybridProductionConfig
    module.pi_hybrid_production_eligible = pi_hybrid_production_eligible
    module.processing_cue_packet = processing_cue_packet
    module.try_pi_hybrid_production = try_pi_hybrid_production
    monkeypatch.setitem(sys.modules, "pi_hybrid_production", module)

    broadcasts = []

    async def fake_broadcast(intent, panel_id=None):
        broadcasts.append({"intent": intent.name, "slots": intent.slots, "panel_id": panel_id})

    async def fail_background(*args, **kwargs):
        raise AssertionError("action-form Pi hybrid should not inject background tool results")

    async def fail_memory(*args, **kwargs):
        raise AssertionError("action-form Pi hybrid should not persist fulfillment memory")

    saved = []

    async def fake_save(session_id, role, content):
        saved.append({"session_id": session_id, "role": role, "content": content})

    run_states = []

    async def fake_record_run_state(*args, **kwargs):
        run_states.append(kwargs)

    monkeypatch.setattr(chat, "_broadcast_intent_nav", fake_broadcast)
    monkeypatch.setattr(chat, "chat_inject_background", fail_background)
    monkeypatch.setattr(chat, "_persist_memory_candidates", fail_memory)
    monkeypatch.setattr(chat, "_save_chat_message", fake_save)
    monkeypatch.setattr(chat, "_record_run_state", fake_record_run_state)

    result = await chat._run_chat_pi_hybrid_lane(
        "set a ten minute timer",
        user_id="jason",
        session_id="session-1",
        panel_id="panel-1",
        record_run_state=True,
        run_id="run-1",
    )

    assert result["accepted"] is True
    assert result["action_form"] == {"component": "timer_create_form", "prefill": {"minutes": 10}}
    await asyncio.sleep(0)
    assert broadcasts == [{"intent": "timer_create", "slots": {"minutes": 10}, "panel_id": "panel-1"}]
    assert saved == [{"session_id": "session-1", "role": "assistant", "content": "Timer is ready to confirm."}]
    assert run_states[0]["metadata"]["pi_hybrid"]["execution_scope"] == "action_form_prefill"
    assert run_states[0]["metadata"]["pi_hybrid"]["action_form"] is True



def test_timer_create_has_touch_panel_action_form_payload():
    class Intent:
        name = "timer_create"
        slots = {"minutes": 10, "label": "Tea"}

    assert "timer_create" in chat._ACTION_FORM_INTENTS
    assert chat._intent_action_form_payload(Intent(), panel_id="panel-1") == {
        "panel_type": "timer",
        "title": "New Timer",
        "data": {"minutes": 10, "label": "Tea"},
        "panel_id": "panel-1",
    }
