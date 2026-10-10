"""A waiting spoken write is confirmed by a short yes, never by a question or a negated "correct".

Live shape (found by the lane-parity rig): "Add my brother Percival" -> "Add Percival, your brother, to your contacts?" -> "are you
sure?" CONFIRMED the write (the old test was "any keyword anywhere in the sentence"), and so did "no, that's not correct, it's Percy".

Break-the-fix: restore the old any-keyword test in ``_pending_decision`` -> ``test_not_an_answer`` and ``test_no`` go red.
"""
import pytest

from routers.voice_tts import _pending_decision


@pytest.mark.parametrize("text", [
    "yes", "Yes.", "yeah", "yep please", "sure", "OK", "okay then", "go ahead", "do it", "confirm", "that's right", "correct",
    "yes please add him", "sure, go ahead and add him",
])
def test_yes(text):
    assert _pending_decision(text) == "yes"


@pytest.mark.parametrize("text", ["no", "No.", "nope", "cancel", "never mind", "stop", "that's not right", "no thanks", "don't"])
def test_no(text):
    assert _pending_decision(text) == "no"


@pytest.mark.parametrize("text", [
    "Are you sure?", "are you sure", "really?", "what did you say", "why", "who is that", "how many contacts do I have",
    "what's the weather", "play some music", "ok so what time is it?", "is that correct?", "",
])
def test_not_an_answer(text):
    assert _pending_decision(text) is None


@pytest.mark.parametrize("text", [
    "no, that's not correct, it's Percy", "no it's spelled with a y", "yes but make it Percy", "ok actually call him Percy",
])
def test_corrected_or_moved_on_drops_the_waiting_write(text):
    assert _pending_decision(text) == "drop"
