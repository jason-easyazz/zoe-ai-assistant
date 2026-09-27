"""A tool round must still fit the brain's single 8192-token slot (B6.6, PR #1716).

The initial messages are windowed to ZOE_CONTEXT_TOKEN_BUDGET (5500, chars/4), but
each tool round then APPENDS a result (deep_web_research / web_browse capped at 6000
chars) and asks for max_tokens more. That envelope can exceed 8192, and llama-server
then refuses the whole request, so the appended tool result is fitted to what the
slot has left (tool text counted at chars/2, fail closed).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import zoe_agent

pytestmark = pytest.mark.ci_safe

SLOT = 8192


def _envelope_tokens(messages, max_tokens):
    """Independent fail-closed bound: chars/4 for windowed text, chars/2 for tool text."""
    total = 0
    for m in messages:
        c = m.get("content") or ""
        total += len(c) // 2 if m.get("role") == "tool" else len(c) // 4
    return total + max_tokens


def test_small_context_leaves_tool_result_untouched():
    messages = [{"role": "system", "content": "s" * 2000}, {"role": "user", "content": "hi"}]
    result = "r" * 6000
    assert zoe_agent._fit_tool_result_to_slot(result, messages, 1024) == result


def test_near_budget_context_trims_tool_result_with_marker():
    messages = [{"role": "system", "content": "s" * 22000}, {"role": "user", "content": "find prices"}]
    fitted = zoe_agent._fit_tool_result_to_slot("r" * 6000, messages, 1024)
    assert len(fitted) < 6000
    assert fitted.endswith(zoe_agent._TRUNCATION_MARKER)
    assert _envelope_tokens(messages + [{"role": "tool", "content": fitted}], 1024) <= SLOT


def _patch_context(monkeypatch, tools):
    async def none_async(*args, **kwargs):
        return ""

    for name in ("_chat_capability_shortcut", "_load_user_portrait", "_mempalace_load_user_facts",
                 "_build_memory_context", "_load_open_loops", "_load_pending_suggestions", "_context_enhance"):
        monkeypatch.setattr(zoe_agent, name, none_async)
    monkeypatch.setattr(zoe_agent, "_check_fast_response", lambda *_: None)
    monkeypatch.setattr(zoe_agent, "_select_skills", lambda *_: set())
    monkeypatch.setattr(zoe_agent, "_build_tools", lambda *_: tools)
    monkeypatch.setattr(zoe_agent, "_classify_tone", lambda *_: "")
    monkeypatch.setattr(zoe_agent, "_fire_memory_capture", lambda *_, **__: None)

    async def fake_dispatch(name, args, user_id=""):
        return "x" * 20000  # per-tool cap brings this to 6000 first

    monkeypatch.setattr(zoe_agent, "_dispatch_tool", fake_dispatch)


def _fake_llm(calls, tool, rounds):
    async def fake_llm_call(messages, *, max_tokens=256, tools_override=None, **kwargs):
        calls.append(([dict(m) for m in messages], max_tokens, tools_override))
        if len(calls) <= rounds:
            return "", tool, {"query": f"prices {len(calls)}"}
        return "Here is what I found.", None, None
    return fake_llm_call


def _history(n_msgs, chars):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "w" * chars}
            for i in range(n_msgs)]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["deep_web_research", "web_browse"])
async def test_second_round_of_a_near_budget_tool_turn_fits_the_slot(monkeypatch, tool):
    _patch_context(monkeypatch, [])
    calls = []
    monkeypatch.setattr(zoe_agent, "_llm_call", _fake_llm(calls, tool, rounds=1))
    # A long conversation: the history window fills its 5500-token budget.
    out = await zoe_agent.run_zoe_agent("find prices", "test-session", user_id="test-user",
                                        history=_history(40, 1500), max_tokens_override=1024)

    assert out.endswith("Here is what I found.")
    second_messages, max_tokens, _ = calls[1]
    tool_msg = second_messages[-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"].endswith(zoe_agent._TRUNCATION_MARKER)
    assert _envelope_tokens(second_messages, max_tokens) <= SLOT


@pytest.mark.asyncio
async def test_two_large_tool_rounds_on_near_budget_history_fit_the_slot(monkeypatch):
    """The FIRST tool result was admitted at chars/2; the second round must count it
    at chars/2 too, or it over-allocates the second result."""
    _patch_context(monkeypatch, [])
    calls = []
    monkeypatch.setattr(zoe_agent, "_llm_call", _fake_llm(calls, "deep_web_research", rounds=2))
    await zoe_agent.run_zoe_agent("find prices", "test-session", user_id="test-user",
                                  history=_history(40, 1500), max_tokens_override=1024)

    assert len(calls) == 3
    for msgs, max_tokens, _ in calls[1:]:
        assert _envelope_tokens(msgs, max_tokens) <= SLOT  # ALL tool text at chars/2


def _tools_tokens(tools):
    import json
    return len(json.dumps(tools)) // 4 if tools else 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True], ids=["run_zoe_agent", "streaming-budget"])
async def test_full_tool_set_and_full_history_fit_before_and_after_the_first_tool_round(monkeypatch, stream):
    """The initial history window reserves the serialized schemas too, so a full tool
    set + a history that fills the window still fits the slot on the FIRST request."""
    full = list(zoe_agent._TOOLS)
    assert _tools_tokens(full) > 2500  # the full serialized schema set really is large
    _patch_context(monkeypatch, full)
    history = _history(40, 1500)  # fills the 5500-token window

    if stream:
        # run_zoe_agent_streaming computes the same initial window; check the
        # history it keeps leaves room for the schemas.
        import inspect
        src = inspect.getsource(zoe_agent.run_zoe_agent_streaming)
        assert "_tools_est_tokens(active_tools)" in src
        return

    calls = []
    monkeypatch.setattr(zoe_agent, "_llm_call", _fake_llm(calls, "web_browse", rounds=1))
    await zoe_agent.run_zoe_agent("find prices", "test-session", user_id="test-user",
                                  history=history, max_tokens_override=1024)

    for msgs, max_tokens, tools in calls[:2]:  # before AND after the first tool round
        assert tools == full
        assert _envelope_tokens(msgs, max_tokens) + _tools_tokens(tools) <= SLOT


def test_both_tool_append_sites_fit_the_slot():
    # run_zoe_agent AND run_zoe_agent_streaming append tool results; both must fit.
    src = open(zoe_agent.__file__, encoding="utf-8").read()
    assert src.count("_fit_tool_result_to_slot(\n                    _cap_tool_result(tool_name, tool_result)") == 2
    assert src.count("messages, budget, active_tools)") == 1
    assert src.count("messages, token_budget, active_tools)") == 1
