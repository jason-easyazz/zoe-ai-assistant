"""KV-prefix stability tests for the Zoe Agent history window.

``zoe_agent`` keeps its system prompt byte-identical every turn so llama.cpp can
reuse the cached KV for it. For this SWA model exact common-prefix matching is
the ONLY reuse path (``--cache-reuse`` is dropped from ``llama-server.service``
because KV shifting is unsupported for Gemma's shared-KV + SWA attention), so
the message immediately after the system prompt has to be stable too: if the
window HEAD moves, every token past the system prompt is re-prefilled.

The old selector walked newest → oldest and kept "the newest N that fit", which
moves the head on every single turn. ``_compact_history`` pins the head to a
content-defined anchor instead. These tests pin that invariant:

* the serialized prompt prefix survives a window slide that drops messages,
* the message-count cap and the token budget are still respected,
* and the pre-fix algorithm — reimplemented verbatim below as ``_legacy_trim``
  — fails the same assertions, so the tests cannot pass vacuously.
"""

from __future__ import annotations

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import zoe_agent as zoe_agent  # noqa: E402

pytestmark = pytest.mark.ci_safe

SYSTEM_PROMPT = "You are Zoe. " * 200
BUDGET = 4000
# Small enough that the token walk itself drops messages, not just the caller slide.
TIGHT_BUDGET = 250


# ── Fixtures and helpers ──────────────────────────────────────────────────────

def _session(turns: int, seed: int = 11) -> list[dict]:
    """A realistic alternating user/assistant transcript of `turns` exchanges."""
    rnd = random.Random(seed)
    words = ["calendar", "light", "weather", "remind", "music", "note", "kitchen"]
    msgs: list[dict] = []
    for i in range(turns):
        msgs.append({
            "role": "user",
            "content": f"q{i} " + " ".join(rnd.choice(words) for _ in range(rnd.randint(3, 12))),
        })
        msgs.append({
            "role": "assistant",
            "content": f"a{i} " + " ".join(rnd.choice(words) for _ in range(rnd.randint(5, 40))),
        })
    return msgs


def _as_caller_sees_it(session: list[dict], turn: int) -> list[dict]:
    """What routers/chat.py hands the agent: the newest 12 messages of the session."""
    return session[max(0, 2 * turn - zoe_agent._HISTORY_MAX_MSGS):2 * turn]


def _serialize(messages: list[dict]) -> str:
    """Chat-template-shaped serialization — what the model actually tokenizes."""
    return "".join(
        f"<start_of_turn>{m.get('role')}\n{m.get('content')}<end_of_turn>\n" for m in messages
    )


def _prompt_prefix(kept: list[dict]) -> str:
    return _serialize([{"role": "system", "content": SYSTEM_PROMPT}] + kept)


def _est_tokens(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "")) // 4 + 10 for m in messages)


def _legacy_trim(history: list[dict], budget: int) -> list[dict]:
    """The pre-fix selector, copied verbatim from zoe_agent.py before this change.

    Kept here as the negative control: the tests below assert it FAILS the
    prefix-stability invariant that _compact_history satisfies.
    """
    remaining = budget
    trimmed: list[dict] = []
    for msg in reversed(history[-12:]):
        msg_tokens = len(str(msg.get("content") or "")) // 4 + 10
        if remaining - msg_tokens < 0:
            break
        trimmed.insert(0, msg)
        remaining -= msg_tokens
    return trimmed


def _reused_prefix_len(prev: list[dict], curr: list[dict]) -> int:
    """Bytes of prompt llama.cpp can reuse — the common prefix of the two prompts."""
    return len(os.path.commonprefix([_prompt_prefix(prev), _prompt_prefix(curr)]))


def _reuse_reaches_history(prev: list[dict], curr: list[dict]) -> bool:
    """True when the reused prefix covers the whole first retained history message.

    Not merely "longer than the system prompt": the chat template's
    ``<start_of_turn>user\\n`` opener is shared even when the message that
    follows it differs, so a few bytes of overlap prove nothing.
    """
    if not prev or not curr:
        return False
    return _reused_prefix_len(prev, curr) >= len(_prompt_prefix(prev[:1]))


def _stable_turn_fraction(selector, session: list[dict], budget: int = BUDGET) -> float:
    """Fraction of consecutive turn pairs whose reuse reaches into the history."""
    prev = None
    stable = total = 0
    for turn in range(6, len(session) // 2):
        kept = selector(_as_caller_sees_it(session, turn), budget)
        if prev is not None:
            total += 1
            stable += _reuse_reaches_history(prev, kept)
        prev = kept
    return stable / total


# ── (a) prefix stability across a window slide ────────────────────────────────

@pytest.mark.parametrize("budget", [BUDGET, TIGHT_BUDGET])
def test_prefix_survives_a_slide_that_drops_messages(budget):
    """A slide that drops messages must not move the head byte-for-byte."""
    session = _session(30)

    found = 0
    for turn in range(6, 15):
        window_before = _as_caller_sees_it(session, turn)
        window_after = _as_caller_sees_it(session, turn + 1)
        before = zoe_agent._compact_history(window_before, budget)
        after = zoe_agent._compact_history(window_after, budget)
        # Only a slide that actually dropped messages off the head is interesting.
        if not before or not after or window_before[0] == window_after[0]:
            continue
        if before[0] != after[0]:
            continue  # a legitimate anchor jump; stability is asserted in aggregate below
        found += 1
        # The serialized prompt prefix through the whole first retained message
        # is byte-identical — that is exactly what llama.cpp can reuse.
        head = _serialize([{"role": "system", "content": SYSTEM_PROMPT}, after[0]])
        assert _prompt_prefix(before).startswith(head)
        assert _prompt_prefix(after).startswith(head)
        assert _reused_prefix_len(before, after) >= len(head)
    assert found, "fixture produced no qualifying slide — test would be vacuous"


@pytest.mark.parametrize("budget", [BUDGET, TIGHT_BUDGET])
def test_head_is_stable_across_many_turns(budget):
    """The head advances in jumps, not on every turn."""
    session = _session(40)
    fraction = _stable_turn_fraction(zoe_agent._compact_history, session, budget)
    assert fraction >= 0.3, f"expected the head to survive many slides, got {fraction:.0%}"


@pytest.mark.parametrize("budget", [BUDGET, TIGHT_BUDGET])
def test_legacy_newest_n_walk_is_the_negative_control(budget):
    """NEGATIVE CONTROL: the pre-fix selector moves the head on ~every turn.

    It is not exactly 0% under budget pressure: when the kept-count grows by two
    on the same turn the window slides by two, the head lands on the same message
    by coincidence. That accident is rare (~3%) and is not prefix stability — the
    fix must beat it by an order of magnitude.
    """
    session = _session(40)
    legacy = _stable_turn_fraction(_legacy_trim, session, budget)
    fixed = _stable_turn_fraction(zoe_agent._compact_history, session, budget)
    assert legacy <= 0.05, (
        "the old newest-N walk is supposed to move the head on essentially every "
        f"turn; got {legacy:.0%} stable — the control is no longer controlling"
    )
    assert fixed >= 10 * legacy or (legacy == 0.0 and fixed > 0.3), (
        f"fix={fixed:.0%} vs legacy={legacy:.0%} — not a meaningful improvement"
    )


def test_stride_one_reproduces_the_legacy_selection_exactly():
    """Stride 1 disables anchoring, so it must match the old algorithm message-for-message."""
    session = _session(40)
    for turn in range(6, 20):
        history = _as_caller_sees_it(session, turn)
        assert zoe_agent._compact_history(history, BUDGET, stride=1) == _legacy_trim(history, BUDGET)


# ── (b) budget and cap contracts ──────────────────────────────────────────────

def test_message_cap_and_token_budget_respected():
    rnd = random.Random(3)
    for _ in range(500):
        history = [
            {"role": rnd.choice(["user", "assistant"]), "content": "z" * rnd.randint(0, 600)}
            for _ in range(rnd.randint(0, 30))
        ]
        budget = rnd.randint(0, 3000)
        kept = zoe_agent._compact_history(history, budget)
        assert len(kept) <= zoe_agent._HISTORY_MAX_MSGS
        assert _est_tokens(kept) <= budget
        # Always a contiguous suffix — never a reordered or gapped window.
        assert kept == history[len(history) - len(kept):] if kept else True


@pytest.mark.parametrize("budget", [BUDGET, TIGHT_BUDGET])
def test_never_keeps_more_than_the_legacy_selector(budget):
    """Anchoring may drop extra messages, never keep messages the budget can't afford."""
    session = _session(40)
    for turn in range(6, 20):
        history = _as_caller_sees_it(session, turn)
        assert len(zoe_agent._compact_history(history, budget)) <= len(_legacy_trim(history, budget))


def test_window_opens_on_a_user_turn_when_an_anchor_exists():
    session = _session(40)
    for turn in range(6, 20):
        kept = zoe_agent._compact_history(_as_caller_sees_it(session, turn), BUDGET)
        if kept and any(zoe_agent._is_history_anchor(m, zoe_agent._HISTORY_ANCHOR_STRIDE) for m in kept):
            assert kept[0]["role"] == "user"


@pytest.mark.parametrize("history,budget", [
    ([], 4000),
    ([{"role": "user", "content": "hi"}], 0),
    ([{"role": "user", "content": "hi"}], -500),
    ([{"role": "user", "content": "x" * 100_000}], 100),
    ([{"role": "user", "content": None}, {"role": "assistant"}], 4000),
])
def test_degenerate_inputs_do_not_raise(history, budget):
    kept = zoe_agent._compact_history(history, budget)
    assert _est_tokens(kept) <= max(budget, 0)


def test_anchorless_history_falls_back_to_the_budget_window():
    """No user turns → no anchors → the old affordable window, not an empty one."""
    history = [{"role": "assistant", "content": f"a{i}"} for i in range(5)]
    assert zoe_agent._compact_history(history, BUDGET) == history
