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

_GUARDED_CALLEES = frozenset({
    "_person_extract", "_person_extract_llm", "process_text", "process_text_llm",
})

# The text parameter is positional-or-keyword named `text` in both
# person_extractor.process_text and person_extractor_llm.process_text_llm.
_TEXT_KWARG = "text"


def _person_extract_text_args(source: str) -> list[tuple[str, "ast.expr | None"]]:
    """(callee_name, text_argument) for every person-extract call.

    Captures the text argument whether it is passed positionally OR as the
    ``text=`` keyword — a kwarg call site must not silently escape the
    lockdown. ``None`` means the call passes no recognisable text argument.
    """
    calls: list[tuple[str, "ast.expr | None"]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", getattr(func, "attr", ""))
        if name not in _GUARDED_CALLEES:
            continue
        if node.args:
            arg: "ast.expr | None" = node.args[0]
        else:
            arg = next(
                (kw.value for kw in node.keywords if kw.arg == _TEXT_KWARG),
                None,
            )
        calls.append((name, arg))
    return calls


def _assert_user_text_only(calls, allowed_arg: str, where: str) -> None:
    assert calls, f"no person-extract calls found in {where} — lockdown is stale"
    for name, arg in calls:
        assert arg is not None, (
            f"{where}: {name}() call passes no recognisable text argument — "
            "extend the lockdown before changing the call shape"
        )
        assert isinstance(arg, ast.Name) and arg.id == allowed_arg, (
            f"{where}: {name}() must be fed the plain `{allowed_arg}` variable, "
            f"got {ast.dump(arg)[:120]} — never combine in the assistant reply "
            "(poisoned-store bug 2026-07-07)"
        )


@pytest.mark.parametrize("router,allowed_arg", [
    ("routers/chat.py", "user_message"),
    ("routers/voice_tts.py", "user_text"),
])
def test_person_extract_call_sites_pass_user_text_only(router, allowed_arg):
    source = (_SERVICE_DIR / router).read_text(encoding="utf-8")
    _assert_user_text_only(_person_extract_text_args(source), allowed_arg, router)


# ── negative cases: the lockdown itself must catch bad call shapes ──────────

@pytest.mark.parametrize("bad_call", [
    # positional combined f-string (the original live bug)
    '_person_extract(f"{user_message}\\n{assistant_response}", user_id=u)',
    # keyword-argument form must NOT silently escape the AST guard
    '_person_extract_llm(text=f"{user_message}\\n{assistant_response}", user_id=u)',
    # keyword-argument passing the wrong variable
    "process_text(text=assistant_response, user_id=u)",
    # positional wrong variable
    "process_text_llm(combined, user_id=u)",
    # no recognisable text argument at all
    "process_text(user_id=u)",
])
def test_lockdown_rejects_assistant_fed_call_shapes(bad_call):
    calls = _person_extract_text_args(bad_call)
    assert calls, "guard failed to even find the call — lockdown is broken"
    with pytest.raises(AssertionError):
        _assert_user_text_only(calls, "user_message", "<synthetic>")


def test_lockdown_accepts_kwarg_user_text():
    # A compliant kwarg call site is captured AND passes.
    calls = _person_extract_text_args("process_text(text=user_message, user_id=u)")
    assert calls and calls[0][1] is not None
    _assert_user_text_only(calls, "user_message", "<synthetic>")
