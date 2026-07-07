"""Extractor purity — assistant-authored sentences must NEVER become user facts.

Live bug (2026-07-07, parity-gate-user): the per-turn person-extraction passes
were fed ``f"{user_message}\n{assistant_response}"``, so Zoe's OWN replies were
stored as approved memories, e.g.:

    "that you like pizza, but I don't have a specific favorite recipe noted
     for you right now."
    "mentioned you like hiking on weekends, but I don't have any notes about
     your coffee preferences."

Those rows then surfaced in the recall packet and reinforced the wrong answer
forever (Zoe recalled her own past denial even though the true fact sat in the
same packet). These tests pin the fix at two levels:

  * ``memory_extractor.extract_candidates`` mines the USER message only —
    the ``assistant_response`` argument is dead by contract.
  * the chat + voice call sites pass USER text only into the person
    extractors (AST lockdown, no heavy router import needed).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # pure regex + stdlib ast, slim-dep

from memory_extractor import extract_candidates

_SERVICE_DIR = Path(__file__).resolve().parents[1]

# Zoe's own replies, captured verbatim from the poisoned live store.
POISONED_ASSISTANT_SENTENCES = [
    "I remember that you like pizza, but I don't have a specific favorite "
    "recipe noted for you right now.",
    "You mentioned you like hiking on weekends, but I don't have any notes "
    "about your coffee preferences.",
]


# ── extract_candidates never mines the assistant reply ──────────────────────

@pytest.mark.parametrize("assistant_reply", POISONED_ASSISTANT_SENTENCES)
def test_poisoned_assistant_reply_produces_zero_candidates(assistant_reply):
    # Neutral user message (not in the skip list, contains no fact patterns):
    # every candidate would have to come from the assistant reply — there
    # must be none.
    out = extract_candidates("can you check on that for me", assistant_reply)
    assert out == []


@pytest.mark.parametrize("assistant_reply", POISONED_ASSISTANT_SENTENCES)
def test_user_fact_still_extracts_alongside_poisoned_reply(assistant_reply):
    out = extract_candidates("my favourite recipe is lasagna", assistant_reply)
    assert any("lasagna" in c.text.lower() for c in out)
    # Nothing from the assistant reply leaked into any candidate.
    for c in out:
        low = c.text.lower()
        assert "pizza" not in low
        assert "hiking" not in low
        assert "i don't have" not in low
        assert "noted for you" not in low


def test_assistant_first_person_frames_never_become_facts():
    # First-person-assistant frames from the bug report, passed as the reply.
    for reply in (
        "I've noted that your favourite recipe is lasagna.",
        "I don't have any notes about your coffee preferences.",
        "you mentioned you like hiking but I have nothing on coffee.",
    ):
        assert extract_candidates("okay sounds good", reply) == []


# ── call-site lockdown: person extractors get USER text only ────────────────

def _person_extract_first_args(path: Path) -> list[tuple[str, ast.expr]]:
    """(callee_name, first_positional_arg) for every person-extract call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", getattr(func, "attr", ""))
        if name in {"_person_extract", "_person_extract_llm",
                    "process_text", "process_text_llm"} and node.args:
            calls.append((name, node.args[0]))
    return calls


@pytest.mark.parametrize("router,allowed_arg", [
    ("routers/chat.py", "user_message"),
    ("routers/voice_tts.py", "user_text"),
])
def test_person_extract_call_sites_pass_user_text_only(router, allowed_arg):
    calls = _person_extract_first_args(_SERVICE_DIR / router)
    assert calls, f"no person-extract calls found in {router} — lockdown is stale"
    for name, arg in calls:
        assert isinstance(arg, ast.Name) and arg.id == allowed_arg, (
            f"{router}: {name}() must be fed the plain `{allowed_arg}` variable, "
            f"got {ast.dump(arg)[:120]} — never combine in the assistant reply "
            "(poisoned-store bug 2026-07-07)"
        )
