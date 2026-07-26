"""Voice replies must carry a brevity directive to the brain.

The Pi brain is a separate subprocess that never sees `voice_mode`, and SOUL has no
voice rule — so the directive has to be injected into the composed message. Without
it the panel path got no brevity signal and over-answered.
"""
import pytest
import zoe_core_client as zc

pytestmark = pytest.mark.ci_safe


def test_voice_mode_injects_brevity_directive():
    out = zc._compose_message(
        "what's the weather like",
        history=None, db_memory_context=None, portrait=None, voice_mode=True,
    )
    assert "[VOICE MODE]" in out
    assert "1-2 complete sentences" in out
    # Pin the two functionally-significant directives so a future wording tweak
    # can't silently drop them (Greptile #820):
    assert "Lead with the answer" in out            # the brevity behaviour
    assert "skip preamble" in out
    assert "use tools fully as normal" in out        # the capability guarantee — shapes
    assert "never what you do" in out                # what she SAYS, not what she does
    # The user's message is still present and last.
    assert out.strip().endswith("what's the weather like")


def test_non_voice_has_no_directive():
    out = zc._compose_message(
        "what's the weather like",
        history=None, db_memory_context=None, portrait=None, voice_mode=False,
    )
    assert "[VOICE MODE]" not in out
    assert out == "what's the weather like"


def test_directive_leads_and_context_preserved():
    out = zc._compose_message(
        "remind me",
        history=[{"role": "user", "content": "hi"}],
        db_memory_context="likes tea",
        portrait="a person",
        voice_mode=True,
    )
    # Directive first, then context blocks, message last.
    assert out.index("[VOICE MODE]") < out.index("[About you]") < out.index("[What you remember]")
    assert out.index("[What you remember]") < out.index("[Recent conversation]")
    assert out.strip().endswith("remind me")
