"""
OpenClaw gateway client: ACP-based communication with the OpenClaw agent.

Uses the Agent Client Protocol (ACP) over stdio instead of the brittle
--local subprocess approach.  This gives us:
  • Browser / CDP tool support (gateway manages Chromium CDP)
  • Proper session continuity
  • Clean JSON-RPC protocol — no stderr parsing hacks

Legacy helpers (chat_inject, openclaw_gateway_call) are retained as-is.
"""
import asyncio
import json
import logging
import os
import re

from zoe_acp_client import openclaw_acp, openclaw_acp_stream  # noqa: F401

logger = logging.getLogger(__name__)

_INJECT_ECHO = re.compile(r"\n?\[Intent: .+?\] User: .+$", re.DOTALL)

NVM_DIR = os.path.expanduser("~/.nvm")
NODE_BIN = os.path.expanduser("~/.nvm/versions/node/v22.22.0/bin")
OPENCLAW_CMD = os.path.join(NODE_BIN, "openclaw")
GATEWAY_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
# Align with nginx proxy_read_timeout for long browser/tool runs (seconds).
# Jetson + large local prompt prefill can exceed 120s; default matches zoe-ui proxy (900s).
OPENCLAW_AGENT_TIMEOUT_S = float(os.environ.get("OPENCLAW_AGENT_TIMEOUT_S", "900"))


def _zoe_context_prefix(
    user_id: str,
    *,
    user_role: str | None = None,
    username: str | None = None,
    memories: str | None = None,
) -> str:
    """Prepended so OpenClaw sees zoe-auth role, current datetime, and user facts.

    Includes datetime so OpenClaw always knows when it is running.
    Includes MemPalace facts so OpenClaw has long-term user context from the start.
    """
    import datetime
    role = user_role if user_role is not None else "unknown"
    name = (username or "").strip()
    now_str = datetime.datetime.now().strftime("%A, %d %B %Y — %I:%M %p")
    prefix = f"[CONTEXT: user_id={user_id}, role={role}, name={name}, datetime={now_str}]\n"
    if memories:
        prefix += f"{memories}\n"
    return prefix


async def openclaw_cli(
    message: str,
    session_id: str,
    user_id: str = "family-admin",
    *,
    user_role: str | None = None,
    username: str | None = None,
    skip_context_prefix: bool = False,
    memories: str | None = None,
) -> str:
    """Send message through OpenClaw agent via ACP (browser, tools, memory, personality).

    Each user_id maps to its own gateway session so memU scopes memories per
    family member.  The ACP bridge connects to the running openclaw-gateway,
    giving access to the browser tool and other gateway-managed resources.
    """
    gateway_session_key = f"agent:main:zoe_{user_id}_{session_id}"
    if not skip_context_prefix:
        message = _zoe_context_prefix(
            user_id, user_role=user_role, username=username, memories=memories
        ) + message

    text = await openclaw_acp(
        message,
        gateway_session_key,
        timeout=OPENCLAW_AGENT_TIMEOUT_S,
    )
    # Strip the injected echo prefix if it bled into the reply
    return _INJECT_ECHO.sub("", text).strip()


async def chat_inject(message: str, user_id: str = "family-admin", session_id: str = "web") -> bool:
    """Inject a note into the correct user's OpenClaw session transcript."""
    try:
        env = os.environ.copy()
        env["PATH"] = f"{NODE_BIN}:/home/zoe/bin:{env.get('PATH', '')}"
        env["NVM_DIR"] = NVM_DIR
        env["OPENCLAW_GATEWAY_TOKEN"] = GATEWAY_TOKEN
        session_key = f"agent:main:zoe_{user_id}_{session_id}"
        params = json.dumps({"sessionKey": session_key, "message": message})

        proc = await asyncio.create_subprocess_exec(
            OPENCLAW_CMD, "gateway", "call", "chat.inject",
            "--params", params,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        return True
    except Exception as e:
        logger.warning(f"chat.inject failed: {e}")
        return False


async def openclaw_gateway_call(method: str, params: dict | None = None, timeout_s: int = 20) -> dict:
    """Best-effort OpenClaw gateway method call."""
    params = params or {}
    try:
        env = os.environ.copy()
        env["PATH"] = f"{NODE_BIN}:/home/zoe/bin:{env.get('PATH', '')}"
        env["NVM_DIR"] = NVM_DIR
        env["OPENCLAW_GATEWAY_TOKEN"] = GATEWAY_TOKEN
        proc = await asyncio.create_subprocess_exec(
            OPENCLAW_CMD,
            "gateway",
            "call",
            method,
            "--params",
            json.dumps(params),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        raw = (stdout or b"").decode().strip()
        if not raw:
            return {"ok": False, "error": "empty_response"}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": True, "raw": raw}
        return {"ok": True, "payload": payload}
    except Exception as e:
        logger.warning("openclaw_gateway_call failed for %s: %s", method, e)
        return {"ok": False, "error": str(e)}


async def discover_openclaw_capabilities() -> dict:
    """Return live capability inventory if gateway exposes it."""
    attempts = [
        ("tools.list", {}),
        ("capabilities.list", {}),
        ("mcp.tools.list", {}),
    ]
    for method, params in attempts:
        res = await openclaw_gateway_call(method, params=params, timeout_s=10)
        if res.get("ok"):
            return {"source_method": method, **res}
    return {"source_method": None, "ok": False, "payload": {"tools": []}}
