"""Voice replies must carry a brevity directive to the brain.

The Pi brain is a separate subprocess that never sees `voice_mode`, and SOUL has no
voice rule — so the directive has to be injected into the composed message. Without
it the panel path got no brevity signal and over-answered.
"""
import zoe_core_client as zc


def test_voice_mode_injects_brevity_directive():
    out = zc._compose_message(
        "what's the weather like",
        history=None, db_memory_context=None, portrait=None, voice_mode=True,
    )
    assert "[VOICE MODE]" in out
    assert "1-2 complete sentences" in out
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
