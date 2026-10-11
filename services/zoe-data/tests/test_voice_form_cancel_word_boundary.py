"""The open-form cancel check must match whole cancel commands, not substrings (safety: distress must reach its tiers)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.voice_tts import _is_form_cancel_command  # noqa: E402

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
