"""Hermes / OpenClaw are retired: the guards that keep them gone.

The chat-side Hermes stream, the forced-agent lanes and the zoe_agent escalation
tools were deleted (OpenClaw/Hermes removal, slice 2). What remains pinned here:
the voice escalation-marker parser (voice path, untouched), and the A2A / system
delegation surfaces that must refuse a retired agent.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mcp_server
from routers import system, voice_tts

pytestmark = pytest.mark.ci_safe


def test_voice_hermes_marker_stays_foreground():
    is_background, reason, prompt = voice_tts._parse_voice_escalation_delta(
        "__ESCALATE_HERMES__:deep work|Use Hermes",
        "fallback prompt",
    )

    assert is_background is False
    assert reason == "deep work"
    assert prompt == "Use Hermes"


def test_voice_background_marker_queues_work():
    is_background, reason, prompt = voice_tts._parse_voice_escalation_delta(
        "__ESCALATE_BG__:long task|Run later",
        "fallback prompt",
    )

    assert is_background is True
    assert reason == "long task"
    assert prompt == "Run later"


@pytest.mark.asyncio
async def test_mcp_a2a_delegate_hermes_is_refused_and_nothing_is_queued(monkeypatch):
    """Hermes is retired: delegating to it must not queue anything (the old path ran an
    auto-approve agent as the zoe user - deep dive S0-7)."""
    monkeypatch.setitem(
        sys.modules,
        "a2a_client",
        types.SimpleNamespace(get_a2a_client=lambda: (_ for _ in ()).throw(AssertionError("A2A should not be used for Hermes"))),
    )
    result = await mcp_server._execute_tool(
        db=None,
        name="a2a_delegate",
        args={"_user_id": "test-user", "agent_name": "hermes", "task": "Investigate routing", "session_id": "session-1"},
    )
    assert "retired" in result["error"]
    assert "task_id" not in str(result) and "queued" not in str(result)


def test_mcp_agents_registry_missing_file_fails_softly(monkeypatch):
    def fake_open(*args, **kwargs):
        raise FileNotFoundError("missing registry")

    monkeypatch.setattr("builtins.open", fake_open)

    assert mcp_server._load_agents_registry() == {"agents": {}, "squads": {}}


@pytest.mark.asyncio
async def test_system_delegate_to_hermes_is_gone(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        system,
        "_load_registry",
        lambda: (_ for _ in ()).throw(AssertionError("Hermes must be refused before any registry lookup")),
    )
    with pytest.raises(HTTPException) as ei:
        await system.delegate_to_agent(
            {"agent_name": "hermes", "task": "Investigate routing", "session_id": "session-2"},
            user={"user_id": "test-user"},
        )
    assert ei.value.status_code == 410