"""
Pi Agent — minimal Python agent loop for Raspberry Pi 5.

Replaces both Hermes (Bonsai fast path) and OpenClaw (full agent) on the Pi branch.
Uses two local models:
  - BitNet b1.58 on :11434  — fast, 1-bit, for short/conversational turns
  - Gemma 4 E2B on  :11435  — richer, for complex/personality-heavy turns

Four tools available inside the loop:
  1. mempalace_search     — recall memories by query
  2. mempalace_add        — store a new memory (summary, preference, fact)
  3. ha_control           — control Home Assistant entities
  4. bash                 — run safe shell commands for self-extension

Memory context is injected into every system prompt from MemPalace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

# ── Config (all overrideable via env / systemd unit) ─────────────────────────

_BITNET_URL   = os.environ.get("BITNET_SERVER_URL",  "http://127.0.0.1:11434/v1")
_GEMMA_URL    = os.environ.get("GEMMA_SERVER_URL",   "http://127.0.0.1:11435/v1")
_HA_BRIDGE    = os.environ.get("ZOE_HA_BRIDGE_URL",  "http://127.0.0.1:8007")
_MEMPALACE_DATA = os.environ.get("MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace"))

_MAX_TOOL_ITERS   = int(os.environ.get("PI_AGENT_MAX_TOOL_ITERS", "5"))
_LLM_TIMEOUT      = float(os.environ.get("PI_AGENT_LLM_TIMEOUT", "120.0"))
_TOOL_TIMEOUT     = float(os.environ.get("PI_AGENT_TOOL_TIMEOUT", "10.0"))

# Heuristic thresholds for model selection
_GEMMA_LENGTH_THRESHOLD = int(os.environ.get("PI_AGENT_GEMMA_THRESHOLD_LEN", "120"))
_GEMMA_KEYWORDS = frozenset({
    "write", "create", "explain", "analyze", "analyse", "research", "debug",
    "code", "plan", "compare", "design", "build", "fix", "generate",
    "summarise", "summarize", "story", "poem", "joke", "essay", "letter",
    "why does", "how does", "what if", "tell me about", "help me",
    "difference between", "pros and cons", "should i",
})

# Simple queries answered without LLM (sub-millisecond)
_TIME_WORDS = frozenset({"time", "clock", "date", "today", "day"})
_DATE_WORDS = frozenset({"date", "today", "day of", "what day", "current date"})

# Safe Bash allowlist (commands Pi Agent can self-extend with)
_BASH_ALLOWED_PREFIXES = (
    "pip install", "python3 -c", "cat ", "ls ", "echo ", "date",
    "systemctl --user status", "df -h", "free -h", "uptime",
)


# ── SOUL.md system prompt for Pi Agent ───────────────────────────────────────

_PI_SOUL = """You are Zoe, a warm, capable home assistant running locally on a Raspberry Pi 5.

## Personality
- Warm but efficient. "Done!" beats "I have successfully completed your request."
- Curious and attentive. Notice patterns, connect the dots.
- Natural and conversational. Contractions, no corporate speak.
- Multi-step thinker. "Add dentist Thursday" means: pick the date, create the event, offer a reminder.

## How You Talk
- Concise by default. One sentence if one sentence works.
- Use markdown for lists/structure when showing multiple items.
- When confirming actions, be brief: "Added milk to shopping."

## Tools Available
You have these tools. Call them as JSON when you need them:

  {"tool":"mempalace_search","args":{"query":"<topic>","limit":5}}
  {"tool":"mempalace_add","args":{"summary":"<text>","tags":["<tag>"]}}
  {"tool":"ha_control","args":{"entity_id":"<entity>","action":"<toggle|turn_on|turn_off|...>"}}
  {"tool":"bash","args":{"command":"<safe shell command>"}}

## When to Use Tools
- mempalace_search: When user preferences, past facts, or names are relevant. Always search at session start for context.
- mempalace_add: When user explicitly asks you to remember something, or you learn a clear preference/fact.
- ha_control: When user asks to control lights, switches, scenes, or media players.
- bash: Self-extension only — installing a new Python package you need, checking system status.

## Memory Context (injected below)
User preferences and recent memories are injected into every prompt from MemPalace.
Do not assume any specific user details — they will appear above if known.

## Response Format
Think step by step. If you need a tool, output EXACTLY one JSON tool call block:
```tool
{"tool":"<name>","args":{...}}
```
After the tool result is injected, continue your response. When done, write your final answer naturally.
Do NOT output tool JSON in your final answer — only plain prose."""


# ── Model routing ─────────────────────────────────────────────────────────────

def _check_fast_response(message: str) -> str | None:
    """Return an instant response for simple queries that don't need an LLM."""
    import datetime
    msg = message.lower().strip(" ?.")
    # Time query
    if any(w in msg for w in ("time", "clock")) and "?" not in message or msg.startswith("what") and "time" in msg:
        now = datetime.datetime.now()
        return f"It's {now.strftime('%-I:%M %p')} on {now.strftime('%A, %d %B %Y')}."
    # Date query
    if msg in ("what day is it", "what's the date", "whats the date", "what is today", "what date is it"):
        now = datetime.datetime.now()
        return f"Today is {now.strftime('%A, %d %B %Y')}."
    return None


def _route_to_model(message: str) -> str:
    """Return 'bitnet' or 'gemma' based on message complexity heuristics.

    NOTE: BitNet i2_s inference quality is poor (ternary weights need TL1 format).
    For now, all queries route to Gemma. When TL1 conversion is fixed and validated,
    re-enable BitNet for short/simple queries.
    """
    # TODO: Re-enable BitNet once TL1 GGUF conversion supports BitNetForCausalLM
    return "gemma"

    # Future routing logic (currently unreachable):
    if len(message) > _GEMMA_LENGTH_THRESHOLD:
        return "gemma"
    msg_lower = message.lower()
    if any(kw in msg_lower for kw in _GEMMA_KEYWORDS):
        return "gemma"
    return "bitnet"


def _model_url(model_key: str) -> str:
    return _GEMMA_URL if model_key == "gemma" else _BITNET_URL


def _model_name(model_key: str) -> str:
    return "gemma-4-e2b" if model_key == "gemma" else "bitnet-b1.58-2b"


# ── MemPalace integration (Python API — no subprocess) ───────────────────────

async def _mempalace_search(query: str, limit: int = 5) -> list[dict]:
    """Search MemPalace for relevant memories. Returns list of hit dicts."""
    try:
        from mempalace.searcher import search_memories  # type: ignore[import]
        raw = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: search_memories(query, _MEMPALACE_DATA, n_results=limit),
        )
        return raw.get("results", []) if isinstance(raw, dict) else []
    except ImportError:
        logger.warning("MemPalace not installed — memory search skipped")
        return []
    except Exception as exc:
        logger.warning("mempalace_search failed (non-fatal): %s", exc)
        return []


async def _mempalace_add(summary: str, tags: list[str] | None = None) -> bool:
    """Add a memory drawer to MemPalace via direct ChromaDB write."""
    import hashlib

    def _write() -> None:
        from mempalace.palace import get_collection  # type: ignore[import]
        col = get_collection(_MEMPALACE_DATA)
        drawer_id = f"zoe_{hashlib.md5(summary.encode()).hexdigest()[:20]}"
        col.add(
            ids=[drawer_id],
            documents=[summary],
            metadatas=[{
                "wing": "zoe",
                "room": "conversations",
                "added_by": "pi_agent",
                "tags": ",".join(tags or []),
            }],
        )

    try:
        await asyncio.get_event_loop().run_in_executor(None, _write)
        return True
    except ImportError:
        logger.warning("MemPalace not installed — memory add skipped")
        return False
    except Exception as exc:
        logger.warning("mempalace_add failed (non-fatal): %s", exc)
        return False


async def _build_memory_context(message: str) -> str:
    """Search MemPalace and build a context block for the system prompt."""
    memories = await _mempalace_search(message, limit=6)
    if not memories:
        return ""
    lines = ["## Memory Context (from MemPalace)"]
    for m in memories:
        content = m.get("text") or m.get("content") or m.get("summary") or str(m)
        lines.append(f"- {content[:200]}")
    return "\n".join(lines)


# ── HA control ───────────────────────────────────────────────────────────────

async def _ha_control(entity_id: str, action: str, data: dict | None = None) -> dict:
    """Call /devices/control on the HA bridge."""
    body = {"entity_id": entity_id, "action": action, "data": data or {}}
    try:
        async with httpx.AsyncClient(timeout=_TOOL_TIMEOUT) as client:
            r = await client.post(f"{_HA_BRIDGE}/devices/control", json=body)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        return {"error": "HA bridge offline — is homeassistant-mcp-bridge running?"}
    except Exception as exc:
        return {"error": str(exc)}


# ── Bash self-extension ───────────────────────────────────────────────────────

async def _bash(command: str) -> str:
    """Run an allowed bash command and return stdout (max 2000 chars)."""
    # Safety check — only whitelisted prefixes allowed
    cmd_stripped = command.strip()
    if not any(cmd_stripped.startswith(pfx) for pfx in _BASH_ALLOWED_PREFIXES):
        return f"[bash blocked: '{cmd_stripped[:40]}' is not in the allowed command list]"
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                cmd_stripped,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            ),
            timeout=15.0,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="replace")[:2000]
        return output or "(no output)"
    except asyncio.TimeoutError:
        return "[bash timeout after 15s]"
    except Exception as exc:
        return f"[bash error: {exc}]"


# ── Tool dispatch ─────────────────────────────────────────────────────────────

async def _dispatch_tool(tool_name: str, args: dict) -> str:
    """Dispatch a tool call and return result as string."""
    if tool_name == "mempalace_search":
        results = await _mempalace_search(
            args.get("query", ""), limit=int(args.get("limit", 5))
        )
        if not results:
            return "No matching memories found."
        lines = []
        for r in results:
            content = r.get("text") or r.get("content") or r.get("summary") or str(r)
            lines.append(f"- {content[:300]}")
        return "\n".join(lines)

    if tool_name == "mempalace_add":
        ok = await _mempalace_add(
            summary=args.get("summary", ""),
            tags=args.get("tags"),
        )
        return "Memory stored." if ok else "Memory storage failed (MemPalace unavailable)."

    if tool_name == "ha_control":
        result = await _ha_control(
            entity_id=args.get("entity_id", ""),
            action=args.get("action", "toggle"),
            data=args.get("data"),
        )
        return json.dumps(result)

    if tool_name == "bash":
        return await _bash(args.get("command", ""))

    return f"[unknown tool: {tool_name}]"


# ── Tool call extraction ───────────────────────────────────────────────────────

_TOOL_BLOCK_RE = re.compile(r"```tool\s*\n(.*?)\n```", re.DOTALL)


def _extract_tool_call(text: str) -> tuple[str | None, dict | None, str]:
    """
    Extract the first ```tool ... ``` block from the LLM response.
    Returns (tool_name, args, text_without_block) or (None, None, text).
    """
    m = _TOOL_BLOCK_RE.search(text)
    if not m:
        return None, None, text
    try:
        payload = json.loads(m.group(1))
        tool_name = payload.get("tool")
        args = payload.get("args", {})
        cleaned = text[: m.start()].rstrip() + text[m.end() :].lstrip()
        return tool_name, args, cleaned
    except json.JSONDecodeError:
        return None, None, text


# ── LLM call ─────────────────────────────────────────────────────────────────

async def _llm_call(
    model_key: str,
    messages: list[dict],
    *,
    max_tokens: int = 768,
    temperature: float = 0.6,
) -> str:
    """Make a non-streaming chat completion request to the local model."""
    url = f"{_model_url(model_key)}/chat/completions"
    payload = {
        "model": _model_name(model_key),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


# ── Main Pi Agent entry point ─────────────────────────────────────────────────

async def run_pi_agent(
    message: str,
    session_id: str,
    user_id: str = "family-admin",
    *,
    history: list[dict] | None = None,
) -> str:
    """
    Run the Pi Agent loop for a single turn.

    Args:
        message:    The user's message.
        session_id: Session identifier for logging.
        user_id:    Authenticated user id.
        history:    Optional prior messages for context window.

    Returns:
        The final assistant response as a plain string.
    """
    t0 = time.monotonic()

    # Fast path: answer trivial queries instantly without LLM
    fast = _check_fast_response(message)
    if fast:
        logger.info("pi_agent: fast response for session=%s in <1ms", session_id)
        return fast

    model_key = _route_to_model(message)
    logger.info("pi_agent: session=%s model=%s msg_len=%d", session_id, model_key, len(message))

    # Build memory context block
    memory_ctx = await _build_memory_context(message)
    system_prompt = _PI_SOUL
    if memory_ctx:
        system_prompt = f"{_PI_SOUL}\n\n{memory_ctx}"

    # Build initial messages list
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        # Include last N turns for context (keep context window manageable)
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})

    # Tool loop
    for iteration in range(_MAX_TOOL_ITERS + 1):
        try:
            response_text = await _llm_call(model_key, messages)
        except httpx.ConnectError:
            server_name = "Gemma" if model_key == "gemma" else "BitNet"
            logger.error("pi_agent: %s server unreachable at %s", server_name, _model_url(model_key))
            return (
                f"I'm having trouble connecting to my local AI ({server_name}). "
                "Please check that the inference server is running."
            )
        except Exception as exc:
            logger.exception("pi_agent: LLM call failed (iter %d): %s", iteration, exc)
            return "Something went wrong — I couldn't generate a response. Please try again."

        # Check for tool call
        tool_name, tool_args, text_before_tool = _extract_tool_call(response_text)

        if tool_name and iteration < _MAX_TOOL_ITERS:
            logger.info(
                "pi_agent: iter=%d tool=%s args=%s",
                iteration, tool_name, json.dumps(tool_args)[:120],
            )
            tool_result = await _dispatch_tool(tool_name, tool_args or {})
            logger.debug("pi_agent: tool_result=%s", tool_result[:200])

            # Add the assistant's (partial) response + tool result to conversation
            messages.append({"role": "assistant", "content": response_text})
            messages.append({
                "role": "user",
                "content": f"[Tool result for {tool_name}]\n{tool_result}\n\nNow continue your response.",
            })
        else:
            # No tool call (or max iterations reached) — this is the final response
            elapsed = time.monotonic() - t0
            logger.info(
                "pi_agent: done session=%s model=%s iters=%d elapsed=%.1fs",
                session_id, model_key, iteration, elapsed,
            )
            # Auto-store any explicit "remember this" patterns
            _maybe_auto_store(message, response_text)
            return response_text

    # Should not reach here
    return response_text


def _maybe_auto_store(user_message: str, response: str) -> None:
    """Fire-and-forget: store an explicit memory if user said 'remember ...'."""
    lc = user_message.lower()
    if not any(kw in lc for kw in ("remember that", "remember this", "don't forget", "store this")):
        return
    asyncio.ensure_future(
        _mempalace_add(
            summary=f"User asked to remember: {user_message[:300]}",
            tags=["explicit_request"],
        )
    )


# ── Streaming version for SSE chat endpoint ───────────────────────────────────

async def run_pi_agent_streaming(
    message: str,
    session_id: str,
    user_id: str = "family-admin",
    *,
    history: list[dict] | None = None,
    on_tool_start: "asyncio.coroutines.Coroutine | None" = None,
    on_tool_end: "asyncio.coroutines.Coroutine | None" = None,
    on_heartbeat: "asyncio.coroutines.Coroutine | None" = None,
) -> AsyncIterator[str]:
    """
    Streaming variant — yields text chunks and dispatches callbacks for SSE.

    Yields:
        Text delta strings as the model generates them.
    Callbacks (all optional, called as coroutines):
        on_tool_start(tool_name, args) — before tool execution
        on_tool_end(tool_name, result) — after tool execution
        on_heartbeat(elapsed_s)        — every ~4s while waiting
    """
    t0 = time.monotonic()
    model_key = _route_to_model(message)
    logger.info("pi_agent streaming: session=%s model=%s", session_id, model_key)

    memory_ctx = await _build_memory_context(message)
    system_prompt = f"{_PI_SOUL}\n\n{memory_ctx}" if memory_ctx else _PI_SOUL

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})

    url = f"{_model_url(model_key)}/chat/completions"
    payload = {
        "model": _model_name(model_key),
        "messages": messages,
        "max_tokens": 768,
        "temperature": 0.6,
        "stream": True,
    }

    for iteration in range(_MAX_TOOL_ITERS + 1):
        collected = ""
        heartbeat_task = None

        try:
            async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    last_hb = time.monotonic()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                collected += delta
                                # Yield text only if we're not in a tool block
                                if "```tool" not in collected:
                                    yield delta
                                elif collected.count("```") % 2 == 0:
                                    # Tool block just closed — hold back (will replay after)
                                    pass
                        except (json.JSONDecodeError, KeyError):
                            pass

                        # Heartbeat every 4s
                        now = time.monotonic()
                        if on_heartbeat and (now - last_hb) >= 4.0:
                            last_hb = now
                            try:
                                await on_heartbeat(int(now - t0))
                            except Exception:
                                pass

        except httpx.ConnectError:
            yield "\n[Pi Agent: inference server offline — please check bitnet-server / gemma-server]"
            return
        except Exception as exc:
            logger.exception("pi_agent streaming: LLM error iter=%d: %s", iteration, exc)
            yield f"\n[Error: {exc}]"
            return

        # Check for tool call in collected output
        tool_name, tool_args, pre_tool_text = _extract_tool_call(collected)

        if tool_name and iteration < _MAX_TOOL_ITERS:
            if on_tool_start:
                try:
                    await on_tool_start(tool_name, tool_args or {})
                except Exception:
                    pass

            tool_result = await _dispatch_tool(tool_name, tool_args or {})

            if on_tool_end:
                try:
                    await on_tool_end(tool_name, tool_result)
                except Exception:
                    pass

            # Update messages and payload for next iteration
            messages.append({"role": "assistant", "content": collected})
            messages.append({
                "role": "user",
                "content": f"[Tool result for {tool_name}]\n{tool_result}\n\nNow continue your response.",
            })
            payload = {
                "model": _model_name(model_key),
                "messages": messages,
                "max_tokens": 768,
                "temperature": 0.6,
                "stream": True,
            }
            # Yield any text that appeared before the tool block
            if pre_tool_text.strip():
                yield pre_tool_text
        else:
            # Done
            elapsed = time.monotonic() - t0
            logger.info(
                "pi_agent streaming done: session=%s model=%s iters=%d elapsed=%.1fs",
                session_id, model_key, iteration, elapsed,
            )
            _maybe_auto_store(message, collected)
            return
