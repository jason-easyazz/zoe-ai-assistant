import asyncio
import sys
import types

import pytest

from routers import chat

pytestmark = pytest.mark.ci_safe


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

    async def fake_save(session_id, role, content, user_id=None):
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



def test_chat_stream_generator_accepts_req_panel_id():
    """Regression for aea6456a: chat_stream_generator's intent fast-path calls
    the Pi hybrid lane with panel_id=req_panel_id, but the parameter was never
    threaded into the function — so every fast-path chat raised
    `NameError: name 'req_panel_id' is not defined`. The existing lane test
    exercised _run_chat_pi_hybrid_lane directly and missed the caller bug."""
    import inspect

    sig = inspect.signature(chat.chat_stream_generator)
    assert "req_panel_id" in sig.parameters, (
        "chat_stream_generator must accept req_panel_id or the fast-path NameErrors"
    )


def test_chat_stream_generator_forwards_panel_id_to_hybrid_lane():
    """Stronger guard (per review): accepting req_panel_id isn't enough — the
    fast-path must actually forward it to the Pi hybrid lane. A refactor that
    keeps the parameter but drops it before the call would silently reintroduce
    the NameError-era bug; this asserts the call site still wires it through."""
    import inspect
    import re

    src = inspect.getsource(chat.chat_stream_generator)
    assert re.search(
        r"_run_chat_pi_hybrid_lane\([^)]*panel_id\s*=\s*req_panel_id", src, re.S
    ), "fast-path must forward req_panel_id into _run_chat_pi_hybrid_lane(panel_id=...)"


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
