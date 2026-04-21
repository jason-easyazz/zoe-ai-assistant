"""
Skill Executor
===============

Executes matched skills by calling their API endpoints directly,
instead of relying on the LLM to generate text about the action.

When a Tier 3 skill match occurs, the executor:
1. Determines the correct endpoint to call based on the message
2. Extracts parameters from the user's natural language
3. Calls the endpoint internally via the FastAPI app
4. Returns a formatted result for the user

This closes the gap between "skill matched" and "action executed."
"""

import re
import logging
import time
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


# Mapping of skill names to action handlers.
# Each handler takes (message, user_id, context) and returns (response_text, metadata).
# If a handler returns None, execution falls back to the LLM.

CHANNEL_NAMES = ["telegram", "discord", "whatsapp"]


def _extract_channel(message: str) -> Optional[str]:
    """Extract channel name from message."""
    msg = message.lower()
    for ch in CHANNEL_NAMES:
        if ch in msg:
            return ch
    return None


async def execute_skill(
    skill_name: str,
    message: str,
    user_id: str,
    context: dict,
) -> Optional[Dict[str, Any]]:
    """
    Attempt to execute a matched skill directly.

    Returns a dict with:
        - response: str (the text to show the user)
        - executed: bool (whether a real action was performed)
        - skill: str (skill name)
        - action: str (what was done)
        - data: dict (raw result data)

    Returns None if the skill can't be executed directly
    (falls back to LLM).
    """
    handler = SKILL_HANDLERS.get(skill_name)
    if not handler:
        return None

    try:
        start = time.time()
        result = await handler(message, user_id, context)
        elapsed_ms = (time.time() - start) * 1000

        if result is None:
            return None

        result["skill"] = skill_name
        result["execution_time_ms"] = round(elapsed_ms, 1)
        logger.info(
            f"Skill executed: {skill_name} -> {result.get('action', '?')} "
            f"in {elapsed_ms:.0f}ms"
        )
        return result

    except Exception as e:
        logger.error(f"Skill execution failed ({skill_name}): {e}")
        return None


# ============================================================
# Channel Setup Skill Handler
# ============================================================

async def _handle_channel_setup(message: str, user_id: str, context: dict) -> Optional[dict]:
    """Handle channel-setup skill actions."""
    from channels.setup_orchestrator import channel_orchestrator

    msg = message.lower().strip()
    channel = _extract_channel(message)

    # Disconnect flow
    if any(word in msg for word in ["disconnect", "remove", "unlink", "delete"]):
        if not channel:
            return {
                "response": "Which channel would you like to disconnect? (Telegram, Discord, or WhatsApp)",
                "executed": False,
                "action": "ask_channel",
            }
        result = await channel_orchestrator.disconnect(channel)
        return {
            "response": result.message,
            "executed": result.success,
            "action": f"disconnect_{channel}",
            "data": {"channel": channel},
        }

    # Status check
    if any(word in msg for word in ["status", "check", "is it working", "connected"]):
        if not channel:
            from channels.setup_orchestrator import get_all_channel_configs
            configs = get_all_channel_configs()
            if configs:
                lines = ["Here's the status of your channels:\n"]
                for c in configs:
                    status_icon = "connected" if c.get("status") == "active" else c.get("status", "unknown")
                    lines.append(f"- **{c['channel'].title()}**: {status_icon}")
                return {
                    "response": "\n".join(lines),
                    "executed": True,
                    "action": "list_channel_status",
                    "data": {"configs": configs},
                }
            return {
                "response": "No channels are configured yet. Would you like to connect one? (Telegram, Discord, or WhatsApp)",
                "executed": True,
                "action": "no_channels",
            }

        status = await channel_orchestrator.get_status(channel)
        if status.get("configured"):
            return {
                "response": (
                    f"**{channel.title()}** is {status.get('status', 'configured')}."
                    + (f" Bot: @{status['bot_username']}" if status.get("bot_username") else "")
                ),
                "executed": True,
                "action": f"status_{channel}",
                "data": status,
            }
        return {
            "response": f"{channel.title()} is not configured yet. Would you like to set it up?",
            "executed": True,
            "action": f"status_{channel}_not_configured",
        }

    # Setup / connect flow
    if any(word in msg for word in ["connect", "setup", "set up", "add", "link", "configure"]):
        if not channel:
            return {
                "response": "Which channel would you like to connect? I support **Telegram**, **Discord**, and **WhatsApp**.",
                "executed": False,
                "action": "ask_channel",
            }

        result = await channel_orchestrator.auto_setup(
            channel=channel,
            bot_name="Zoe",
            user_id=user_id,
        )

        if result.success:
            steps = "\n".join(f"- {s}" for s in result.next_steps) if result.next_steps else ""
            return {
                "response": f"{result.message}\n\n{steps}".strip(),
                "executed": True,
                "action": f"setup_{channel}",
                "data": result.credentials,
            }
        else:
            steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(result.next_steps)) if result.next_steps else ""
            response = result.message
            if steps:
                response += f"\n\n{steps}"
            if result.error:
                response += f"\n\nOnce you have the credentials, just tell me and I'll configure it."
            return {
                "response": response,
                "executed": False,
                "action": f"setup_{channel}_manual",
                "data": {"next_steps": result.next_steps},
            }

    return None


# ============================================================
# Agent Zero Research Skill Handler
# ============================================================

async def _handle_research(message: str, user_id: str, context: dict) -> Optional[dict]:
    """Handle agent-zero research/analysis/deep-dive actions."""
    import httpx

    msg = message.lower().strip()

    # Extract the research topic (remove trigger words)
    topic = message
    for prefix in ["research", "investigate", "deep dive into", "deep dive", "find out about", "find out", "look into", "look up", "what do you know about"]:
        if msg.startswith(prefix):
            topic = message[len(prefix):].strip()
            break

    if not topic or len(topic) < 3:
        return None  # Fall back to LLM

    # Determine depth
    depth = "thorough"
    if any(w in msg for w in ["quick", "brief", "short"]):
        depth = "quick"
    elif any(w in msg for w in ["comprehensive", "exhaustive", "detailed", "in-depth"]):
        depth = "comprehensive"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "http://agent-zero-bridge:8101/tools/research",
                json={"query": topic, "depth": depth, "user_id": user_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    summary = data.get("summary", "")
                    details = data.get("details", "")
                    sources = data.get("sources", [])

                    response = summary or details[:500]
                    if sources:
                        response += "\n\n**Sources:**\n" + "\n".join(f"- {s}" for s in sources[:5])

                    return {
                        "response": response,
                        "executed": True,
                        "action": "research",
                        "data": {"topic": topic, "depth": depth, "sources": sources},
                    }
    except Exception as e:
        logger.debug(f"Agent Zero research unavailable: {e}")

    return None  # Fall back to LLM


# ============================================================
# Agent Zero Comparison Skill Handler
# ============================================================

async def _handle_comparison(message: str, user_id: str, context: dict) -> Optional[dict]:
    """Handle comparison requests via Agent Zero."""
    import httpx

    msg = message.lower().strip()

    # Try to extract "X vs Y" or "compare X and Y"
    vs_match = re.search(r'(.+?)\s+(?:vs\.?|versus|or)\s+(.+)', message, re.IGNORECASE)
    compare_match = re.search(r'compare\s+(.+?)\s+(?:and|with|to|against)\s+(.+)', message, re.IGNORECASE)

    match = compare_match or vs_match
    if not match:
        return None

    item_a = match.group(1).strip()
    item_b = match.group(2).strip()

    if len(item_a) < 2 or len(item_b) < 2:
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "http://agent-zero-bridge:8101/tools/compare",
                json={"item_a": item_a, "item_b": item_b, "user_id": user_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    response = data.get("comparison", "")
                    if data.get("recommendation"):
                        response += f"\n\n**Recommendation:** {data['recommendation']}"
                    return {
                        "response": response,
                        "executed": True,
                        "action": "compare",
                        "data": {"item_a": item_a, "item_b": item_b},
                    }
    except Exception as e:
        logger.debug(f"Agent Zero comparison unavailable: {e}")

    return None


# ============================================================
# Agent Zero Planning Skill Handler
# ============================================================

async def _handle_planning(message: str, user_id: str, context: dict) -> Optional[dict]:
    """Handle planning requests via Agent Zero."""
    import httpx

    msg = message.lower().strip()

    # Extract the task
    task = message
    for prefix in ["plan", "create a plan for", "help me plan", "break down", "steps to", "how should i", "strategy for"]:
        if msg.startswith(prefix):
            task = message[len(prefix):].strip()
            break

    if not task or len(task) < 5:
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "http://agent-zero-bridge:8101/tools/plan",
                json={"task": task, "user_id": user_id},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    steps = data.get("steps", [])
                    response = f"Here's a plan for: **{task}**\n\n"
                    if steps:
                        response += "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
                    else:
                        response += data.get("details", "Plan created.")
                    if data.get("estimated_time"):
                        response += f"\n\n**Estimated time:** {data['estimated_time']}"
                    return {
                        "response": response,
                        "executed": True,
                        "action": "plan",
                        "data": {"task": task, "steps": steps},
                    }
    except Exception as e:
        logger.debug(f"Agent Zero planning unavailable: {e}")

    return None


# ============================================================
# Smart Home Skill Handler
# ============================================================

async def _handle_smart_home(message: str, user_id: str, context: dict) -> Optional[dict]:
    """Handle smart home commands via Home Assistant.
    
    Note: Most smart home commands are already handled by the intent system
    (Tier 0/1). This handler is a fallback for messages that match the
    smart-home skill triggers but weren't caught by the intent system.
    """
    # The intent system already handles smart home well.
    # Return None to let the LLM handle edge cases with the skill context.
    return None


# ============================================================
# Handler Registry
# ============================================================

SKILL_HANDLERS = {
    "channel-setup": _handle_channel_setup,
    "agent-zero-research": _handle_research,
    "agent-zero-deep-research": _handle_research,
    "agent-zero-comparison": _handle_comparison,
    "agent-zero-planning": _handle_planning,
    "smart-home": _handle_smart_home,
}


# ============================================================
# Singleton executor with call log (for audit API)
# ============================================================

class SkillExecutor:
    """Tracks skill execution calls for auditing."""

    def __init__(self):
        self._call_log: list = []
        self._max_log_size = 200

    async def run(self, skill_name: str, message: str, user_id: str, context: dict) -> Optional[Dict[str, Any]]:
        """Execute a skill and log the call."""
        result = await execute_skill(skill_name, message, user_id, context)
        entry = {
            "skill": skill_name,
            "message": message[:100],
            "user_id": user_id,
            "executed": result.get("executed", False) if result else False,
            "action": result.get("action", "none") if result else "no_handler",
            "timestamp": time.time(),
        }
        self._call_log.append(entry)
        if len(self._call_log) > self._max_log_size:
            self._call_log = self._call_log[-self._max_log_size:]
        return result

    def get_call_log(self) -> list:
        """Get recent skill execution audit log."""
        return list(reversed(self._call_log))


skill_executor = SkillExecutor()
