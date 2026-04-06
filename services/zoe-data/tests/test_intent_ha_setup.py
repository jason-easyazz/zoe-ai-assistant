"""HA full-setup shorthand intent and OpenClaw message expansion."""
import asyncio

import pytest

from intent_router import (
    HA_FULL_SETUP_OPENCLAW_MESSAGE,
    detect_intent,
    execute_intent,
    openclaw_user_message,
)


@pytest.mark.parametrize(
    "text",
    [
        "setup home automation",
        "Setup home automation",
        "please set up home assistant",
        "home assistant setup",
        "configure home automation",
    ],
)
def test_detect_ha_full_setup(text: str):
    intent = detect_intent(text)
    assert intent is not None
    assert intent.name == "ha_full_setup"


def test_execute_ha_full_setup_returns_none():
    intent = detect_intent("setup home assistant")
    assert intent is not None
    out = asyncio.run(execute_intent(intent, "family-admin"))
    assert out is None


def test_openclaw_user_message_expands_without_intent():
    msg = openclaw_user_message(None, "setup home automation")
    assert msg == HA_FULL_SETUP_OPENCLAW_MESSAGE
    assert "browser" in msg.lower() or "OpenClaw" in msg or "HA" in msg


def test_openclaw_user_message_expands_with_intent():
    intent = detect_intent("set up home assistant")
    msg = openclaw_user_message(intent, "set up home assistant")
    assert msg == HA_FULL_SETUP_OPENCLAW_MESSAGE
