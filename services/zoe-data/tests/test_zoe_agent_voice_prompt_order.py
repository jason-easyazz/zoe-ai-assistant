"""Legacy-lane (zoe_agent) VOICE prompt is ordered for llama-server prefix reuse.

Gemma's chat template renders ``system text → tool block → history → latest
user``. llama-server (``--cache-ram``, single slot) reuses only a byte-identical
PREFIX. The old voice layout put the per-turn content — a datetime header that
changes every minute, the signed-in user line, and the portrait / facts / recall
/ offer extras — INSIDE the system prompt, i.e. ahead of the tool block and the
whole history, so every voice turn re-prefilled all of it.

``_build_voice_prompt`` keeps the system prompt byte-identical (``_ZOE_SOUL_VOICE``)
and moves that per-turn content, unchanged, into the latest user message just
ahead of the user's words. Pinned on the messages ``run_zoe_agent`` actually
sends for two consecutive voice turns of one session:

* the system prompt is byte-identical across turns and carries no per-turn text;
* the per-turn block sits in the LAST user message, BEFORE the user's words, and
  nothing follows the words;
* everything before the turn-1 user message (system + history) is a
  byte-identical prefix of turn 2's request.

Negative control: the pre-fix layout (reimplemented verbatim as
``_legacy_voice_prompt``) fails the same prefix check.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import zoe_agent  # noqa: E402

pytestmark = pytest.mark.ci_safe


def _legacy_voice_prompt(message, *, user_id, extras):
    """The pre-2026-09-27 voice layout, verbatim: header + extras in the SYSTEM prompt."""
    joined = "\n\n".join(filter(None, extras))
    soul = zoe_agent._zoe_soul(user_id=user_id, voice_mode=True)
    return (f"{soul}\n\n{joined}" if joined else soul), message


def _patch_agent(monkeypatch, calls, per_turn):
    """Stub every collaborator so run_zoe_agent only assembles and 'calls' the LLM."""
    async def none_async(*_a, **_k):
        return ""

    for name in ("_chat_capability_shortcut", "_build_memory_context", "_load_open_loops",
                 "_context_enhance"):
        monkeypatch.setattr(zoe_agent, name, none_async)

    async def facts(_uid):
        return per_turn["facts"]

    async def offers(_uid, _sid):
        return per_turn["offers"]

    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", facts)
    monkeypatch.setattr(zoe_agent, "_load_pending_suggestions", offers)
    monkeypatch.setattr(zoe_agent, "_soul_header", lambda **_k: per_turn["header"])
    monkeypatch.setattr(zoe_agent, "_check_fast_response", lambda *_: None)
    monkeypatch.setattr(zoe_agent, "_build_voice_tools", lambda *_: [])
    monkeypatch.setattr(zoe_agent, "_classify_tone", lambda *_: "")
    monkeypatch.setattr(zoe_agent, "_fire_memory_capture", lambda *_, **__: None)

    async def fake_llm_call(messages, **_kwargs):
        calls.append([dict(m) for m in messages])
        return "Sure thing.", None, None

    monkeypatch.setattr(zoe_agent, "_llm_call", fake_llm_call)


async def _two_turns(monkeypatch):
    calls: list = []
    per_turn = {"header": "[Saturday, 27 September 2026 — 10:31 AM]\nThe logged-in user_id is jason.",
                "facts": "[What you remember]\n- likes tea", "offers": ""}
    _patch_agent(monkeypatch, calls, per_turn)
    history = [
        {"role": "user", "content": "hey zoe"},
        {"role": "assistant", "content": "Hey Jason!"},
    ]
    await zoe_agent.run_zoe_agent(
        "what should I cook tonight", "voice-s1", user_id="jason", history=list(history),
        voice_mode=True, db_memory_context="[What you remember]\n- vegetarian",
        portrait="Jason is a night owl.",
    )
    # Next turn, a minute later, with different recall + an offer pending.
    per_turn.update(header="[Saturday, 27 September 2026 — 10:32 AM]\nThe logged-in user_id is jason.",
                    facts="[What you remember]\n- allergic to peanuts",
                    offers="Offer to save Emma as a contact")
    history2 = history + [
        {"role": "user", "content": "what should I cook tonight"},
        {"role": "assistant", "content": "Sure thing."},
    ]
    await zoe_agent.run_zoe_agent(
        "and for dessert?", "voice-s1", user_id="jason", history=history2,
        voice_mode=True, db_memory_context="[What you remember]\n- sweet tooth",
        portrait="Jason is a night owl.",
    )
    return calls


def _assert_cache_friendly(calls):
    t1, t2 = calls[0], calls[1]
    # 1. Stable block: byte-identical system prompt with no per-turn content.
    assert t1[0]["role"] == "system" and t2[0]["role"] == "system"
    assert t1[0]["content"] == t2[0]["content"], "system prompt varies per turn"
    assert "10:3" not in t1[0]["content"] and "remember" not in t1[0]["content"].lower()
    # 2. Volatile block: in the LAST user message, before the words, nothing after them.
    last = t2[-1]
    assert last["role"] == "user"
    assert last["content"].endswith("and for dessert?")
    words_at = last["content"].rindex("and for dessert?")
    for fragment in ("10:32 AM", "allergic to peanuts", "sweet tooth", "night owl", "Emma"):
        at = last["content"].find(fragment)
        assert 0 <= at < words_at, f"{fragment!r} must precede the user's words in the latest message"
    # 3. Prefix reuse: system + turn-1 history is a byte-identical prefix of turn 2.
    shared = len(t1) - 1  # everything except turn 1's (context-carrying) user message
    assert t2[:shared] == t1[:shared], "turn 2 must extend turn 1's system + history prefix"


@pytest.mark.asyncio
async def test_voice_prompt_keeps_stable_prefix_and_puts_per_turn_content_last(monkeypatch):
    calls = await _two_turns(monkeypatch)
    assert len(calls) == 2
    _assert_cache_friendly(calls)
    # Nothing was dropped in the move: the turn-1 content reached the model too.
    first_user = calls[0][-1]["content"]
    for fragment in ("10:31 AM", "The logged-in user_id is jason.", "likes tea", "vegetarian",
                     "night owl"):
        assert fragment in first_user, fragment


@pytest.mark.asyncio
async def test_negative_control_legacy_layout_breaks_the_prefix(monkeypatch):
    monkeypatch.setattr(zoe_agent, "_build_voice_prompt", _legacy_voice_prompt)
    calls = await _two_turns(monkeypatch)
    with pytest.raises(AssertionError):
        _assert_cache_friendly(calls)


def test_both_voice_entry_points_use_the_cache_friendly_builder():
    for fn in (zoe_agent.run_zoe_agent, zoe_agent.run_zoe_agent_streaming):
        src = inspect.getsource(fn)
        assert "_build_voice_prompt(" in src, fn.__name__
        assert "_zoe_soul(user_id=user_id, voice_mode=True)" not in src, fn.__name__
