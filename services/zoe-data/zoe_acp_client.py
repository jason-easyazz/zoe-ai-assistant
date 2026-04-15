"""
ZoeAcpClient — Zoe as a first-class OpenClaw ACP channel.

Uses the Agent Client Protocol (ACP) over stdio to talk to the OpenClaw
gateway, replacing the brittle --local subprocess workaround.

Benefits over --local mode:
  • Browser / CDP tools work (gateway manages Chromium)
  • Proper session continuity (gateway maintains transcript)
  • Streaming: yields text chunks as they arrive (live mode)
  • Clean JSON-RPC protocol — no stderr parsing hacks
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_NVM_DIR = os.path.expanduser("~/.nvm")
_NODE_BIN = os.path.expanduser("~/.nvm/versions/node/v22.22.0/bin")
_OPENCLAW_CMD = os.path.join(_NODE_BIN, "openclaw")
_GATEWAY_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
_DEFAULT_TIMEOUT = float(os.environ.get("OPENCLAW_AGENT_TIMEOUT_S", "900"))

# ACP sends the full response as a single final chunk by default.
# Set to "live" to get incremental chunks as Gemma generates tokens.
_ACP_DELIVERY_MODE = os.environ.get("ZOE_ACP_DELIVERY_MODE", "live")


def _build_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{_NODE_BIN}:/home/zoe/bin:{env.get('PATH', '')}"
    env["NVM_DIR"] = _NVM_DIR
    env["OPENCLAW_GATEWAY_TOKEN"] = _GATEWAY_TOKEN
    env["OPENCLAW_HIDE_BANNER"] = "1"
    env["OPENCLAW_SUPPRESS_NOTES"] = "1"
    return env


class _AcpBridge:
    """
    Single ACP bridge subprocess (one per call).
    Handles the JSON-RPC-over-stdio handshake, session creation, and prompt.
    """

    def __init__(self, gateway_session_key: str):
        self._session_key = gateway_session_key
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._rpc_id = 0

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            _OPENCLAW_CMD, "acp",
            "--token", _GATEWAY_TOKEN,
            "--session", self._session_key,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_build_env(),
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    # ── low-level I/O ──────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            async for line in self._proc.stdout:
                line = line.strip()
                if line:
                    try:
                        await self._queue.put(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

    async def _send(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    async def _rpc(self, method: str, params: dict, timeout: float = 10.0) -> dict:
        """Send a JSON-RPC request and return the result, passing through notifications."""
        req_id = self._next_id()
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        pending: list[dict] = []
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(f"ACP {method} timed out after {timeout}s")
                try:
                    msg = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise TimeoutError(f"ACP {method} timed out after {timeout}s")
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(f"ACP error in {method}: {msg['error']}")
                    return msg.get("result", {})
                pending.append(msg)
        finally:
            for p in pending:
                await self._queue.put(p)

    async def _notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ── ACP handshake ──────────────────────────────────────────────────────

    async def initialize(self) -> None:
        await self._rpc("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "zoe-channel", "version": "1.0"},
        })
        await self._notify("initialized")

    async def new_session(self) -> str:
        """Create an ACP session (maps to the gateway session key). Returns session UUID."""
        result = await self._rpc("session/new", {"cwd": "/home/zoe", "mcpServers": []})
        return result["sessionId"]

    # ── prompt (full response) ─────────────────────────────────────────────

    async def prompt(self, text: str, session_id: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
        """Send a prompt and return the full response text."""
        chunks = [c async for c in self.stream_prompt(text, session_id, timeout=timeout)]
        return "".join(chunks)

    # ── prompt (streaming) ─────────────────────────────────────────────────

    async def stream_prompt(
        self, text: str, session_id: str, timeout: float = _DEFAULT_TIMEOUT
    ) -> AsyncIterator[str]:
        prompt_id = self._next_id()
        await self._send({
            "jsonrpc": "2.0",
            "id": prompt_id,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        })

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("ACP stream_prompt timed out")
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError("ACP stream_prompt timed out")

            if msg.get("id") == prompt_id:
                return  # done

            if msg.get("method") == "session/update":
                upd = msg.get("params", {}).get("update", {})
                if upd.get("sessionUpdate") == "agent_message_chunk":
                    content = upd.get("content", {})
                    if content.get("type") == "text" and content.get("text"):
                        yield content["text"]


# ── Public API ─────────────────────────────────────────────────────────────


async def openclaw_acp(
    message: str,
    gateway_session_key: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """
    Send *message* to OpenClaw via ACP and return the complete response text.

    Args:
        message: The user message to send (already prefixed with context if needed).
        gateway_session_key: OpenClaw session key, e.g. "agent:main:zoe_family-admin_main".
        timeout: Seconds before giving up.
    """
    bridge = _AcpBridge(gateway_session_key)
    try:
        await bridge.start()
        await bridge.initialize()
        session_id = await bridge.new_session()
        # Drain the initial session_info / available_commands notifications
        await asyncio.sleep(0.15)
        while not bridge._queue.empty():
            bridge._queue.get_nowait()
        return await bridge.prompt(message, session_id, timeout=timeout)
    except TimeoutError:
        logger.warning("openclaw_acp: timed out for session %s", gateway_session_key)
        return "Sorry, that took too long. Could you try again?"
    except Exception as exc:
        logger.warning("openclaw_acp: error for session %s: %s", gateway_session_key, exc)
        return "I'm having trouble right now. Please try again in a moment."
    finally:
        await bridge.close()


async def openclaw_acp_stream(
    message: str,
    gateway_session_key: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> AsyncIterator[str]:
    """
    Send *message* to OpenClaw via ACP and yield text chunks as they arrive.

    This is the streaming version — yields each token/chunk from agent_message_chunk
    events so the caller can forward them to the SSE stream immediately.
    """
    bridge = _AcpBridge(gateway_session_key)
    try:
        await bridge.start()
        await bridge.initialize()
        session_id = await bridge.new_session()
        await asyncio.sleep(0.15)
        while not bridge._queue.empty():
            bridge._queue.get_nowait()
        async for chunk in bridge.stream_prompt(message, session_id, timeout=timeout):
            yield chunk
    except TimeoutError:
        logger.warning("openclaw_acp_stream: timed out for session %s", gateway_session_key)
        yield "Sorry, that took too long. Could you try again?"
    except Exception as exc:
        logger.warning("openclaw_acp_stream: error for session %s: %s", gateway_session_key, exc)
        yield "I'm having trouble right now. Please try again in a moment."
    finally:
        await bridge.close()
