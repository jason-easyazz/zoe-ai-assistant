"""
Channel Setup Orchestrator
===========================

Manages conversational setup of messaging channels (Telegram, Discord, WhatsApp).
Supports two paths:
  1. Auto-setup via Agent Zero browser automation (developer mode)
  2. Manual setup with step-by-step instructions

Stores channel credentials securely and manages webhook configuration.
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

BROWSER_TASKS = {
    "telegram": {
        "url": "https://t.me/BotFather",
        "actions": [
            "Send /newbot to BotFather",
            "Enter the bot name when prompted",
            "Enter the bot username when prompted",
            "Copy the bot token from BotFather's response",
        ],
        "extract_fields": ["bot_token", "bot_username"],
    },
    "discord": {
        "url": "https://discord.com/developers/applications",
        "actions": [
            "Click 'New Application'",
            "Enter the application name",
            "Navigate to the Bot section",
            "Click 'Add Bot'",
            "Click 'Reset Token' and copy the token",
            "Enable 'Message Content Intent' under Privileged Gateway Intents",
            "Copy the Application (Client) ID from the General Information page",
        ],
        "extract_fields": ["bot_token", "client_id", "bot_username"],
    },
    "whatsapp": {
        "url": "https://www.twilio.com/console/sms/whatsapp/sandbox",
        "actions": [
            "Navigate to the WhatsApp sandbox page",
            "Copy the Account SID",
            "Copy the Auth Token",
            "Copy the sandbox phone number",
        ],
        "extract_fields": ["account_sid", "auth_token", "from_number"],
    },
}

MANUAL_INSTRUCTIONS = {
    "telegram": [
        "Open Telegram and search for @BotFather",
        "Send /newbot and follow the prompts",
        "Copy the bot token (looks like 123456:ABC-DEF...)",
        "Come back here and tell me the token",
    ],
    "discord": [
        "Go to https://discord.com/developers/applications",
        "Click 'New Application' and give it a name",
        "Go to the 'Bot' section and click 'Add Bot'",
        "Click 'Reset Token' and copy the new token",
        "Enable 'Message Content Intent' under Privileged Gateway Intents",
        "Copy the Application ID from 'General Information'",
        "Come back here and tell me the bot token and client ID",
    ],
    "whatsapp": [
        "Go to https://www.twilio.com/console",
        "Navigate to Messaging > WhatsApp > Sandbox",
        "Copy your Account SID and Auth Token",
        "Note the sandbox phone number",
        "Come back here and tell me those values",
    ],
}


@dataclass
class SetupResult:
    """Result of a channel setup attempt."""
    success: bool
    channel: str
    method: str  # "auto" or "manual"
    message: str
    credentials: Dict[str, str] = field(default_factory=dict)
    next_steps: list = field(default_factory=list)
    error: Optional[str] = None


def init_channel_config_db():
    """Initialize channel_config table for storing credentials."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS channel_config (
            channel TEXT PRIMARY KEY,
            config_json TEXT NOT NULL,
            webhook_url TEXT,
            bot_username TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    logger.info("Channel config DB initialized")


def store_channel_config(channel: str, config: dict, bot_username: str = "", status: str = "active"):
    """Store channel configuration securely."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO channel_config (channel, config_json, bot_username, status, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
        """, (channel, json.dumps(config), bot_username, status))
        conn.commit()
        logger.info(f"Channel config stored: {channel} (status={status})")
    except Exception as e:
        logger.error(f"Failed to store channel config: {e}")
    finally:
        conn.close()


def get_channel_config(channel: str) -> Optional[dict]:
    """Get stored channel configuration."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM channel_config WHERE channel = ?", (channel,))
        row = cursor.fetchone()
        if row:
            result = dict(row)
            result["config"] = json.loads(result.get("config_json", "{}"))
            return result["config"]
        return None
    except Exception as e:
        logger.error(f"Failed to get channel config: {e}")
        return None
    finally:
        conn.close()


def get_all_channel_configs() -> list:
    """Get all channel configurations."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM channel_config ORDER BY channel")
        rows = cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["config"] = json.loads(d.get("config_json", "{}"))
            del d["config_json"]
            results.append(d)
        return results
    except Exception as e:
        logger.error(f"Failed to get all channel configs: {e}")
        return []
    finally:
        conn.close()


def remove_channel_config(channel: str) -> bool:
    """Remove channel configuration."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM channel_config WHERE channel = ?", (channel,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to remove channel config: {e}")
        return False
    finally:
        conn.close()


def update_channel_webhook(channel: str, webhook_url: str):
    """Update webhook URL for a channel."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE channel_config SET webhook_url = ?, updated_at = datetime('now')
            WHERE channel = ?
        """, (webhook_url, channel))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to update webhook: {e}")
    finally:
        conn.close()


class ChannelSetupOrchestrator:
    """Orchestrates channel setup via conversation or automation."""

    def __init__(self):
        init_channel_config_db()

    async def auto_setup(self, channel: str, bot_name: str = "Zoe", user_id: str = "default") -> SetupResult:
        """Attempt automated setup via Agent Zero browser automation.
        
        Falls back to manual instructions if Agent Zero is unavailable.
        """
        logger.info(f"Auto-setup requested for {channel}")

        if channel not in BROWSER_TASKS:
            return SetupResult(
                success=False,
                channel=channel,
                method="auto",
                message=f"Auto-setup not available for {channel}",
                error=f"Unknown channel: {channel}",
            )

        if not await self._agent_zero_available():
            logger.info(f"Agent Zero unavailable, falling back to manual for {channel}")
            return await self.manual_configure(channel)

        try:
            result = await self._auto_create_bot(channel, bot_name, user_id)
            return result
        except Exception as e:
            logger.error(f"Auto-setup failed for {channel}: {e}")
            manual = await self.manual_configure(channel)
            manual.error = f"Auto-setup failed ({e}), providing manual instructions"
            return manual

    async def manual_configure(self, channel: str, credentials: dict = None) -> SetupResult:
        """Provide manual setup instructions or store provided credentials."""
        if credentials:
            store_channel_config(channel, credentials, status="active")

            webhook_url = os.getenv("ZOE_BASE_URL", "https://your-zoe-instance.com")
            webhook_url = f"{webhook_url}/api/channels/{channel}/webhook"
            update_channel_webhook(channel, webhook_url)

            if channel == "telegram" and credentials.get("bot_token"):
                await self._set_webhook(channel, credentials["bot_token"], webhook_url)

            return SetupResult(
                success=True,
                channel=channel,
                method="manual",
                message=f"{channel.title()} configured successfully!",
                credentials={"stored": True},
                next_steps=[
                    f"Webhook URL: {webhook_url}",
                    "You can now link your account by generating a verification code",
                ],
            )

        instructions = MANUAL_INSTRUCTIONS.get(channel, [f"Setup instructions not available for {channel}"])

        return SetupResult(
            success=False,
            channel=channel,
            method="manual",
            message=f"Here's how to set up {channel.title()}:",
            next_steps=instructions,
        )

    async def get_status(self, channel: str) -> dict:
        """Get current setup status for a channel."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM channel_config WHERE channel = ?", (channel,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                config = json.loads(d.get("config_json", "{}"))
                return {
                    "channel": channel,
                    "configured": True,
                    "status": d.get("status", "unknown"),
                    "bot_username": d.get("bot_username", ""),
                    "webhook_url": d.get("webhook_url", ""),
                    "last_error": d.get("last_error"),
                    "has_token": bool(config.get("bot_token") or config.get("account_sid")),
                    "updated_at": d.get("updated_at"),
                }
            return {"channel": channel, "configured": False, "status": "not_configured"}
        except Exception as e:
            return {"channel": channel, "configured": False, "error": str(e)}
        finally:
            conn.close()

    async def disconnect(self, channel: str) -> SetupResult:
        """Disconnect and remove channel configuration."""
        if channel == "telegram":
            config = get_channel_config(channel)
            if config and config.get("bot_token"):
                await self._remove_telegram_webhook(config["bot_token"])

        removed = remove_channel_config(channel)
        return SetupResult(
            success=removed,
            channel=channel,
            method="manual",
            message=f"{channel.title()} disconnected" if removed else f"{channel.title()} was not configured",
        )

    async def _agent_zero_available(self) -> bool:
        """Check if Agent Zero is available for browser automation."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("http://zoe-agent0-bridge:8101/health")
                data = resp.json()
                return data.get("agent_zero_connected", False) and "browser_automation" in data.get("capabilities", [])
        except Exception:
            return False

    async def _auto_create_bot(self, channel: str, bot_name: str, user_id: str) -> SetupResult:
        """Use Agent Zero to create a bot automatically."""
        import httpx

        task = BROWSER_TASKS[channel]

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                "http://zoe-agent0-bridge:8101/tools/browse",
                json={
                    "url": task["url"],
                    "actions": [a.replace("the bot name", bot_name).replace("the application name", bot_name) for a in task["actions"]],
                    "extract_fields": task["extract_fields"],
                    "user_id": user_id,
                },
            )
            result = resp.json()

        if result.get("success") and result.get("extracted"):
            extracted = result["extracted"]
            store_channel_config(channel, extracted, bot_username=extracted.get("bot_username", ""), status="active")

            webhook_url = os.getenv("ZOE_BASE_URL", "https://your-zoe-instance.com")
            webhook_url = f"{webhook_url}/api/channels/{channel}/webhook"
            update_channel_webhook(channel, webhook_url)

            if channel == "telegram" and extracted.get("bot_token"):
                await self._set_webhook(channel, extracted["bot_token"], webhook_url)

            return SetupResult(
                success=True,
                channel=channel,
                method="auto",
                message=f"{channel.title()} bot created automatically!",
                credentials={"bot_username": extracted.get("bot_username", "created")},
                next_steps=[
                    f"Bot created: @{extracted.get('bot_username', 'your-bot')}",
                    f"Webhook configured: {webhook_url}",
                    "Generate a verification code to link your account",
                ],
            )

        return SetupResult(
            success=False,
            channel=channel,
            method="auto",
            message="Auto-setup couldn't extract credentials. Please provide them manually.",
            next_steps=MANUAL_INSTRUCTIONS.get(channel, []),
            error=result.get("raw_response", "Unknown error")[:200],
        )

    async def _set_webhook(self, channel: str, token: str, webhook_url: str):
        """Set webhook for Telegram bot."""
        if channel != "telegram":
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/setWebhook",
                    json={"url": webhook_url},
                )
                result = resp.json()
                if result.get("ok"):
                    logger.info(f"Telegram webhook set: {webhook_url}")
                else:
                    logger.error(f"Failed to set Telegram webhook: {result}")
        except Exception as e:
            logger.error(f"Webhook setup error: {e}")

    async def _remove_telegram_webhook(self, token: str):
        """Remove Telegram webhook."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/deleteWebhook",
                )
                logger.info("Telegram webhook removed")
        except Exception as e:
            logger.error(f"Failed to remove webhook: {e}")

    async def _test_connection(self, channel: str) -> dict:
        """Test connectivity with configured channel."""
        config = get_channel_config(channel)
        if not config:
            return {"success": False, "error": "Channel not configured"}

        try:
            import httpx
            if channel == "telegram":
                token = config.get("bot_token")
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                    data = resp.json()
                    if data.get("ok"):
                        return {"success": True, "bot": data["result"]}
                    return {"success": False, "error": data.get("description")}

            elif channel == "discord":
                token = config.get("bot_token")
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        "https://discord.com/api/v10/users/@me",
                        headers={"Authorization": f"Bot {token}"},
                    )
                    if resp.status_code == 200:
                        return {"success": True, "bot": resp.json()}
                    return {"success": False, "error": f"HTTP {resp.status_code}"}

            elif channel == "whatsapp":
                return {"success": True, "note": "WhatsApp connectivity depends on Twilio account status"}

            return {"success": False, "error": f"Test not implemented for {channel}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Singleton
channel_orchestrator = ChannelSetupOrchestrator()
