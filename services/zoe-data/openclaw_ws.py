"""
OpenClaw gateway client: CLI-based communication with the OpenClaw agent
and gateway for chat and injection.
"""
import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_INJECT_ECHO = re.compile(r"\n?\[Intent: .+?\] User: .+$", re.DOTALL)

NVM_DIR = os.path.expanduser("~/.nvm")
NODE_BIN = os.path.expanduser("~/.nvm/versions/node/v22.22.0/bin")
OPENCLAW_CMD = os.path.join(NODE_BIN, "openclaw")
GATEWAY_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")


async def openclaw_cli(message: str, session_id: str, user_id: str = "family-admin") -> str:
    """Send message through OpenClaw agent with full memory, tools, and personality.
    Each user_id gets an isolated session so memU scopes memories per family member."""
    user_session = f"zoe_{user_id}_{session_id}"

    env = os.environ.copy()
    env["PATH"] = f"{NODE_BIN}:/home/zoe/bin:{env.get('PATH', '')}"
    env["NVM_DIR"] = NVM_DIR
    env["OPENCLAW_GATEWAY_TOKEN"] = GATEWAY_TOKEN

    proc = await asyncio.create_subprocess_exec(
        OPENCLAW_CMD, "agent",
        "--agent", "main",
        "--session-id", user_session,
        "--message", message,
        "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        return "Sorry, that took too long. Could you try again?"

    raw = stdout.decode().strip()
    if not raw:
        return "I'm having trouble right now. Please try again in a moment."

    try:
        result = json.loads(raw)
        if result.get("status") == "ok":
            payloads = result.get("result", {}).get("payloads", [])
            if payloads:
                text = payloads[0].get("text", "I couldn't process that request.")
                return _INJECT_ECHO.sub("", text).strip()
        if isinstance(result, dict) and "text" in result:
            return result["text"]
        if isinstance(result, dict) and "result" in result:
            r = result["result"]
            if isinstance(r, str):
                return r
            if isinstance(r, dict) and "text" in r:
                return r["text"]
        return raw if len(raw) < 2000 else "Something went wrong. Please try again."
    except json.JSONDecodeError:
        return raw if len(raw) < 2000 else "I'm having trouble right now. Please try again."


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
