"""The open-form cancel check must match whole cancel commands, not substrings (safety: distress must reach its tiers)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.voice_tts import _is_form_cancel_command, _is_form_confirm_command  # noqa: E402

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize("text", [
    "I can't stop the bleeding",
    "the fire is close to the house",
    "don't stop believing playing",
    "stop the car",
    "close to the house",
    "dismiss the guests from the kitchen",
    "",
])
def test_not_a_cancel(text):
    assert _is_form_cancel_command(text) is False


@pytest.mark.parametrize("text", [
    "stop", "cancel", "never mind", "nevermind", "stop that", "cancel it", "close it", "close the form",
    "forget it", "dismiss", "okay cancel", "cancel please", "Stop.",
])
def test_cancel(text):
    assert _is_form_cancel_command(text.lower().rstrip(".!?")) is True


@pytest.mark.parametrize("text", [
    "I'm not okay", "yes I'm bleeding", "yes help me", "ok I've fallen", "save the dog", "don't save it",
    "okay but wait", "", "my wife is not ok",
])
def test_not_a_confirm(text):
    assert _is_form_confirm_command(text) is False


@pytest.mark.parametrize("text", [
    "yes", "save it", "looks good", "yes please", "ok save it", "that's correct", "thats correct", "go ahead",
    "confirm", "done", "okay", "yes do it", "save", "perfect",
])
def test_confirm(text):
    assert _is_form_confirm_command(text) is True


@pytest.mark.parametrize("text", [
    "can you cancel it", "could you close that", "would you cancel the form", "please stop", "will you stop",
])
def test_polite_cancel(text):
    assert _is_form_cancel_command(text) is True


@pytest.mark.parametrize("text", ["can you stop the bleeding", "I can't stop", "I can’t stop the bleeding"])
def test_polite_or_curly_distress_is_not_cancel(text):
    assert _is_form_cancel_command(text) is False


@pytest.mark.parametrize("text", [
    "sure go ahead", "sure", "yeah save it", "yep", "alright", "all right", "yes go ahead", "that’s correct",
])
def test_more_confirms(text):
    assert _is_form_confirm_command(text) is True


@pytest.mark.parametrize("text", ["yeah I'm not okay", "yeah I’m not okay", "sure I can’t breathe"])
def test_negated_affirmative_is_not_confirm(text):
    assert _is_form_confirm_command(text) is False


# -- the endpoint: the branch ordering that actually matters --

import asyncio  # noqa: E402
import types  # noqa: E402

import routers.voice_tts as v  # noqa: E402
from panel_form_state import clear_active_form, get_active_form, set_active_form  # noqa: E402
from test_voice_identity_persistence import PANEL_ID, SESSION_ID, _wire_voice_command_fakes  # noqa: E402

_CALLER = {"source": "device", "user_id": "voice-daemon", "panel_id": PANEL_ID}


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["I can't stop the bleeding", "yes I'm bleeding", "I’m not okay"])
async def test_distress_with_an_open_form_reaches_the_conversation_tiers(monkeypatch, utterance):
    _chat_calls, spawned = _wire_voice_command_fakes(monkeypatch, panel_user="casey")
    tier_calls = []

    async def resolve(text, *a, **kw):
        tier_calls.append(text)
        return None

    monkeypatch.setitem(sys.modules, "fast_tiers", types.SimpleNamespace(resolve=resolve))
    set_active_form(PANEL_ID, "calendar_event", {"title": "Dentist"})
    try:
        resp = await v.voice_command(
            {"text": utterance, "panel_id": PANEL_ID, "session_id": SESSION_ID},
            caller=_CALLER, stream=False, db=object(),
        )
        assert resp["reply"] not in ("Okay, cancelled.", "Event saved.")
        assert tier_calls == [utterance], "the conversation tiers never saw the distress utterance"
        assert get_active_form(PANEL_ID) is not None, "the open form was cleared by a non-cancel utterance"
    finally:
        clear_active_form(PANEL_ID)
        if spawned:
            await asyncio.gather(*spawned)


@pytest.mark.asyncio
async def test_a_genuine_cancel_still_closes_the_form(monkeypatch):
    _wire_voice_command_fakes(monkeypatch, panel_user="casey")
    set_active_form(PANEL_ID, "calendar_event", {})
    try:
        resp = await v.voice_command(
            {"text": "cancel it", "panel_id": PANEL_ID, "session_id": SESSION_ID},
            caller=_CALLER, stream=False, db=object(),
        )
        assert resp["reply"] == "Okay, cancelled."
        assert get_active_form(PANEL_ID) is None
    finally:
        clear_active_form(PANEL_ID)
