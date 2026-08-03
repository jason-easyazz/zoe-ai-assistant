"""The core lane injects the memory packet ONCE, and never on this seam.

Context: `services/zoe-core/extensions/memory.ts` used to compose the
`/api/memories/for-prompt` packet onto the SYSTEM PROMPT every turn. Because the
packet is keyed on the user's message it changed on essentially every turn, so
the reusable llama.cpp prefix ended at the last byte of SOUL.md and the whole
conversation was re-prefilled. The fix moves the packet out of the system prompt
and into the tail of the conversation (a Pi `custom` message, appended after the
user message), leaving only the static usage directive on the system prompt.

The obvious alternative was to move the packet to THIS side — into
`zoe_core_client._compose_message`, next to the `[About you]` / `[What you
remember]` blocks. It was rejected, and these tests pin that decision so it does
not get re-litigated by accident:

* The extension is the single fetch site for the core lane. `routers/chat.py`
  already passes `db_memory_context=None` by default *because* the extension
  injects the packet (`_CHAT_INJECT_DB_MEMORY`, default OFF, added when that
  double-injection was removed). A second fetch here would double-inject the same
  facts the moment that flag was turned on.
* Every non-zoe-data caller of the agent (the `pi` CLI, `services/zoe-core/bench`,
  `services/zoe-core/test`) would silently lose memory entirely.
* Placement on this seam buys nothing for the KV prefix anyway: the whole composed
  message is the tail of the request, so the packet is already past every
  cacheable byte either way.

So `_compose_message` stays exactly as it was — it folds in only what the CALLER
hands it. These are contract tests for that boundary, not tests of new behaviour.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

from pathlib import Path

_ZOE_DATA = Path(__file__).resolve().parents[1]
_MEMORY_EXT = _ZOE_DATA.parent / "zoe-core" / "extensions" / "memory.ts"

_FOR_PROMPT = "/api/memories/for-prompt"


def test_compose_message_never_fetches_the_for_prompt_packet():
    """The seam folds in caller-supplied context only — it has no memory fetch.

    A `for-prompt` reference appearing in this module would mean the packet is
    injected on BOTH sides of the seam (here and in memory.ts) for every core
    turn: the same facts twice, and double the prefill they cost.
    """
    source = (_ZOE_DATA / "zoe_core_client.py").read_text(encoding="utf-8")
    # Comments may (and do) discuss the endpoint; only executable references count.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert _FOR_PROMPT not in code, (
        "zoe_core_client fetches the for-prompt packet — extensions/memory.ts "
        "already does, so the core lane would inject it twice"
    )


def test_compose_message_emits_only_caller_supplied_blocks():
    import zoe_core_client as zc

    composed = zc._compose_message(
        "what's on tomorrow",
        history=None,
        db_memory_context=None,
        portrait=None,
    )
    assert composed == "what's on tomorrow"
    assert "[What you remember]" not in composed
    assert "[About you]" not in composed


def test_caller_supplied_memory_context_is_still_honoured():
    """The `db_memory_context` escape hatch (expert dispatch, and chat.py under
    ZOE_CHAT_INJECT_DB_MEMORY=1) must keep working — it is explicit, not automatic."""
    import zoe_core_client as zc

    composed = zc._compose_message(
        "what's on tomorrow",
        history=[{"role": "user", "content": "hi"}],
        db_memory_context="- the dog is named Pixel",
        portrait="Jason, lives in Geraldton",
    )
    assert "[About you]\nJason, lives in Geraldton" in composed
    assert "[What you remember]\n- the dog is named Pixel" in composed
    assert "[Recent conversation]\nuser: hi" in composed
    # The user's actual message stays LAST — it is the tail of the request.
    assert composed.endswith("what's on tomorrow")


def test_memory_extension_keeps_the_packet_off_the_system_prompt():
    """Guards the TS-side placement from the python lane too.

    The node suite (services/zoe-core/test/prefix_stability.test.ts) proves the
    behaviour; this is a cheap structural tripwire that runs in the slim GitHub
    lane, where node may be too old to execute the TypeScript at all.
    """
    source = _MEMORY_EXT.read_text(encoding="utf-8")
    assert "memorySystemPrompt" in source, "the static system-prompt split is gone"
    assert "MEMORY_PACKET_CUSTOM_TYPE" in source, "the packet is no longer a tail message"
    # The pre-fix composition: the fetched packet concatenated into the prompt.
    assert "${MEMORY_USAGE_DIRECTIVE}\\n\\n${packet}" not in source, (
        "the volatile packet is composed onto the system prompt again — that is "
        "the exact construction that made KV prefix reuse impossible"
    )
