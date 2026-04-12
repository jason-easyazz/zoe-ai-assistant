"""Conversation platform for Zoe agent."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal
from uuid import uuid4

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.intent import IntentResponse, IntentResponseErrorCode

from .const import (
    CONF_BRIDGE_URL,
    CONF_PANEL_ID,
    CONF_SOURCE,
    DEFAULT_BRIDGE_URL,
    DEFAULT_PANEL_ID,
    DEFAULT_SOURCE,
)

_LOGGER = logging.getLogger(__name__)


def _capabilities_fallback(text: str) -> str | None:
    """Return a local fallback for common capability prompts."""
    normalized = " ".join((text or "").lower().split())
    capability_prompts = (
        "what can you do",
        "what do you do",
        "help me",
        "how can you help",
    )
    if any(p in normalized for p in capability_prompts):
        return (
            "I can help with time, weather, calendar, lists, notes, and home control. "
            "You can ask me things like add milk to shopping list, what is on my calendar tomorrow, or turn on living room lights."
        )

    intro_prompts = (
        "tell me about yourself",
        "tell me about itself",
        "who are you",
        "what are you",
        "introduce yourself",
    )
    if any(p in normalized for p in intro_prompts):
        return (
            "I am Zoe, your home assistant. "
            "I can chat naturally and help with calendar, lists, notes, weather, and smart home tasks."
        )

    time_prompts = (
        "what time is it",
        "what's the time",
        "what is the time",
        "tell me the time",
    )
    if any(p in normalized for p in time_prompts):
        from datetime import datetime

        return f"It is currently {datetime.now().strftime('%-I:%M %p')}."
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Zoe conversation entity."""
    async_add_entities([ZoeConversationEntity(config_entry)])


class ZoeConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """Route conversational text to Zoe/Hermes bridge."""

    _attr_name = "Zoe Conversation"

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize Zoe conversation entity."""
        self.config_entry = config_entry

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register this entity as conversation agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.config_entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister conversation agent."""
        conversation.async_unset_agent(self.hass, self.config_entry)
        await super().async_will_remove_from_hass()

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process text by forwarding to Zoe bridge."""
        bridge_url = self.config_entry.data.get(CONF_BRIDGE_URL, DEFAULT_BRIDGE_URL).rstrip(
            "/"
        )
        panel_id = self.config_entry.data.get(CONF_PANEL_ID, DEFAULT_PANEL_ID)
        source = self.config_entry.data.get(CONF_SOURCE, DEFAULT_SOURCE)
        session_id = user_input.conversation_id or f"ha-zoe-{uuid4().hex[:8]}"

        response = IntentResponse(language=user_input.language)
        session = async_get_clientsession(self.hass)

        quick_reply = _capabilities_fallback(user_input.text)
        if quick_reply:
            response.async_set_speech(quick_reply)
            return conversation.ConversationResult(
                response=response,
                conversation_id=session_id,
                continue_conversation=False,
            )

        last_error = None
        for attempt in range(1, 3):
            try:
                # Keep the voice UX responsive: fail fast, then do one bounded retry.
                request_timeout = 20 if attempt == 1 else 30
                api_response = await session.post(
                    f"{bridge_url}/voice/turn",
                    json={
                        "panel_id": panel_id,
                        "source": source,
                        "session_id": session_id,
                        "transcript": user_input.text,
                    },
                    timeout=request_timeout,
                )
                data = await api_response.json(content_type=None)
                reply_text = (data or {}).get("reply") or (data or {}).get("text") or ""

                if api_response.ok and reply_text:
                    response.async_set_speech(str(reply_text))
                    last_error = None
                    break

                body_preview = str(data)[:300]
                last_error = (
                    f"HTTP {api_response.status} from Zoe bridge, body={body_preview}"
                )
                _LOGGER.warning("Zoe bridge reply missing/invalid: %s", last_error)
            except Exception as exc:  # noqa: BLE001
                last_error = repr(exc)
                _LOGGER.warning(
                    "Zoe bridge request failed on attempt %s: %s", attempt, last_error
                )

            # brief retry for transient network/container hiccups
            if attempt == 1:
                await asyncio.sleep(0.5)

        if last_error is not None:
            # Return a graceful spoken fallback instead of surfacing raw bridge failures.
            response.async_set_speech(
                "I had trouble reaching Zoe for that request. Please try again."
            )

        return conversation.ConversationResult(
            response=response,
            conversation_id=session_id,
            continue_conversation=False,
        )
