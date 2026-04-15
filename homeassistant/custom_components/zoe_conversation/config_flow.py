"""Config flow for Zoe conversation."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries

from .const import (
    CONF_BRIDGE_URL,
    CONF_PANEL_ID,
    CONF_SOURCE,
    DEFAULT_BRIDGE_URL,
    DEFAULT_PANEL_ID,
    DEFAULT_SOURCE,
    DOMAIN,
)


class ZoeConversationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zoe conversation."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle first step."""
        if user_input is not None:
            await self.async_set_unique_id("zoe_conversation_agent")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Zoe Conversation", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_BRIDGE_URL, default=DEFAULT_BRIDGE_URL): str,
                vol.Required(CONF_PANEL_ID, default=DEFAULT_PANEL_ID): str,
                vol.Required(CONF_SOURCE, default=DEFAULT_SOURCE): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
