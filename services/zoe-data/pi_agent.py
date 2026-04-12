"""
Pi/Jetson Agent — fast, minimal agent loop for Zoe.

Model: Gemma 4 E2B (llama.cpp)
  Pi:     CPU, --reasoning off, 7 TPS, port 11435
  Jetson: GPU, --reasoning auto, 40+ TPS, port 11434

  - Fast path: time/date/status answered in <100ms (no LLM)
  - KV-cache warmup at startup so first real query skips re-processing the system prompt
  - Smart background memory: Gemma classifier fires AFTER response delivery (non-blocking)
  - Selective reasoning: hard queries (code, analysis) get more tokens
  - Escalation: complex tasks handed to OpenClaw via escalate_to_openclaw tool

Tools inside the loop:
  1. mempalace_search      — recall memories by semantic query
  2. mempalace_add         — store a fact/preference/name explicitly
  3. ha_control            — control Home Assistant entities (lights, switches, media)
  4. bash                  — safe self-extension (install packages, check system status)
  5. escalate_to_openclaw  — hand off to OpenClaw for complex agentic tasks

Fine-tuning target: once the Gemma LoRA checkpoint is trained on Zoe's voice,
the _PI_SOUL system prompt can be shrunk to ~10 tokens (saving ~500ms prefill).
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

# Suppress ChromaDB ONNX runtime GPU-discovery noise on Pi (no GPU)
os.environ.setdefault("ORT_DISABLE_GPU", "1")

import httpx

logger = logging.getLogger(__name__)

# ── Config (all overrideable via env / systemd unit) ─────────────────────────

_GEMMA_URL      = os.environ.get("GEMMA_SERVER_URL",   "http://127.0.0.1:11435/v1")
_HA_BRIDGE      = os.environ.get("ZOE_HA_BRIDGE_URL",  "http://127.0.0.1:8007")
_MEMPALACE_DATA = os.environ.get("MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace"))
_JETSON_MODE    = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"

_MAX_TOOL_ITERS   = int(os.environ.get("PI_AGENT_MAX_TOOL_ITERS", "5"))
_LLM_TIMEOUT      = float(os.environ.get("PI_AGENT_LLM_TIMEOUT", "120.0"))
_TOOL_TIMEOUT     = float(os.environ.get("PI_AGENT_TOOL_TIMEOUT", "10.0"))

# Simple queries answered without LLM (sub-millisecond)
_TIME_WORDS = frozenset({"time", "clock", "date", "today", "day"})
_DATE_WORDS = frozenset({"date", "today", "day of", "what day", "current date"})

# Safe Bash allowlist (commands Pi Agent can self-extend with)
_BASH_ALLOWED_PREFIXES = (
    "pip install", "python3 -c", "cat ", "ls ", "echo ", "date",
    "systemctl --user status", "df -h", "free -h", "uptime",
)


# ── SOUL.md system prompt for Pi Agent ───────────────────────────────────────

_PI_SOUL = """You are Zoe, a warm home assistant. Be concise — "Done!" not long confirmations. Natural speech, contractions OK. For hard questions, be thorough but efficient.

Tools (one JSON block when needed):
  {"tool":"mempalace_search","args":{"query":"<topic>","limit":5}}
  {"tool":"mempalace_add","args":{"summary":"<fact>","tags":["<tag>"]}}
  {"tool":"ha_control","args":{"entity_id":"<entity>","action":"<toggle|turn_on|turn_off>"}}
  {"tool":"bash","args":{"command":"<safe shell command>"}}
  {"tool":"escalate_to_openclaw","args":{"reason":"<why>","task":"<enriched task description>"}}

Use ha_control for smart home. Use escalate_to_openclaw for: multi-step automation, browser research, financial queries, complex calendar changes, anything needing web access or long agentic loops. Only use mempalace_search when asked about past conversations."""

# After Gemma LoRA fine-tuning on Zoe's voice, this shrinks to ~10 tokens:
# _PI_SOUL = "You are Zoe. Tools: mempalace_search, mempalace_add, ha_control, bash, escalate_to_openclaw."

# ── Hard query detection ──────────────────────────────────────────────────────

_HARD_QUERY_WORDS = frozenset({
    "debug", "code", "algorithm", "step by step", "explain how", "why does",
    "analyse", "analyze", "research", "implement", "compare and contrast",
    "write a program", "script", "function", "class", "calculate", "derive",
    "pros and cons", "in detail", "comprehensive", "thorough", "deeply",
})


def _is_hard_query(message: str) -> bool:
    """Return True if this query warrants a larger token budget."""
    msg_lower = message.lower()
    return (
        len(message) > 250
        or any(kw in msg_lower for kw in _HARD_QUERY_WORDS)
    )


def _token_budget(message: str) -> int:
    """Choose max_tokens based on query complexity and hardware tier."""
    if _JETSON_MODE:
        return 1024 if _is_hard_query(message) else 512
    return 512 if _is_hard_query(message) else 256


# ── Fast path ─────────────────────────────────────────────────────────────────

def _check_fast_response(message: str) -> str | None:
    """Return an instant response for simple queries that don't need an LLM."""
    import datetime
    msg = message.lower().strip(" ?.")
    now = datetime.datetime.now()

    # Time queries
    if ("what" in msg or msg.startswith("what")) and "time" in msg:
        return f"It's {now.strftime('%-I:%M %p')} on {now.strftime('%A, %d %B %Y')}."
    if msg in ("time", "the time", "current time", "what time", "clock"):
        return f"It's {now.strftime('%-I:%M %p')}."

    # Date queries
    if msg in ("what day is it", "what's the date", "whats the date",
               "what is today", "what date is it", "today's date", "today"):
        return f"Today is {now.strftime('%A, %d %B %Y')}."

    # Uptime / status
    if msg in ("status", "are you running", "are you there", "you there", "ping"):
        return "I'm here and running on your Pi 5. How can I help?"

    return None


def _model_url() -> str:
    return _GEMMA_URL


def _model_name() -> str:
    return "google_gemma-4-E2B-it-Q4_K_M"


# ── MemPalace integration (Python API — no subprocess) ───────────────────────

async def _mempalace_search(query: str, limit: int = 5, timeout_s: float = 2.0) -> list[dict]:
    """Search MemPalace for relevant memories. Returns list of hit dicts.

    Enforces a hard timeout so a slow ONNX embedding run never blocks inference.
    """
    try:
        from mempalace.searcher import search_memories  # type: ignore[import]
        raw = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: search_memories(query, _MEMPALACE_DATA, n_results=limit),
            ),
            timeout=timeout_s,
        )
        return raw.get("results", []) if isinstance(raw, dict) else []
    except asyncio.TimeoutError:
        logger.debug("mempalace_search timed out after %.1fs — skipping", timeout_s)
        return []
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


# Keywords that suggest the message benefits from memory context retrieval.
# Queries without these skip MemPalace (saves 1-25s of ONNX inference time).
_MEMORY_TRIGGER_WORDS = frozenset({
    "remember", "recall", "did i", "have i", "last time", "before",
    "you said", "we talked", "my name", "my preference", "i told you",
    "favourite", "favorite", "usually", "always", "never", "often",
    "who is", "what is my", "family", "remind me",
})


def _message_needs_memory(message: str) -> bool:
    """Return True only if this message is likely to benefit from MemPalace context."""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _MEMORY_TRIGGER_WORDS)


async def _build_memory_context(message: str) -> str:
    """Search MemPalace and build a context block for the system prompt.

    Only runs for messages that look memory-relevant (saves 1-25s per query on CPU).
    The `mempalace_search` tool is always available for explicit lookups.
    """
    if not _message_needs_memory(message):
        return ""
    memories = await _mempalace_search(message, limit=5, timeout_s=3.0)
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

    if tool_name == "escalate_to_openclaw":
        reason = args.get("reason", "complex task")
        task = args.get("task", "")
        return f"__ESCALATE__:{reason}|{task}"

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

# ── KV cache warmup ───────────────────────────────────────────────────────────

async def warmup_kv_cache() -> None:
    """Pre-process the system prompt so the KV cache is hot before the first user query.

    Without this, the first real query pays the full prefill cost (~500ms for the
    system prompt). After warmup, the prompt tokens are cached and subsequent queries
    only pay for their unique user message tokens (~1s vs ~2s for uncached first query).

    Called from the zoe-data startup lifespan event.
    """
    await asyncio.sleep(8)  # Give Gemma time to finish loading the 3.5GB model (~6s)
    for attempt in range(3):
        try:
            await _llm_call(
                [
                    {"role": "system", "content": _PI_SOUL},
                    {"role": "user", "content": "ready"},
                ],
                max_tokens=3,
                temperature=0.0,
            )
            logger.info(
                "pi_agent: ✅ Gemma KV cache warmed (attempt %d) — first query will be fast",
                attempt + 1,
            )
            return
        except Exception as exc:
            logger.warning("pi_agent: KV warmup attempt %d failed: %s — retrying in 5s", attempt + 1, exc)
            await asyncio.sleep(5)
    logger.warning("pi_agent: KV warmup failed after 3 attempts (non-fatal — first query will be slower)")


def _strip_thinking(text: str) -> str:
    """Remove Gemma 4 interleaved thinking tokens, keeping only the response."""
    # If model generates <|channel>thought...content...<|channel>response...answer
    # extract only the response part
    for marker in ("<|channel|>response", "<|channel>response", "</thought>", "<response>"):
        if marker in text:
            return text.split(marker, 1)[-1].strip()
    # Fallback: remove any <|...|> special-token-like blocks
    cleaned = re.sub(r"<\|[^|>]+\|?>?[^<]*", "", text)
    return cleaned.strip() or text.strip()


async def _llm_call(
    messages: list[dict],
    *,
    max_tokens: int = 256,
    temperature: float = 0.7,
) -> str:
    """Make a non-streaming chat completion request to the local model."""
    url = f"{_model_url()}/chat/completions"
    payload = {
        "model": _model_name(),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        # Disable Gemma 4 thinking mode — without this, the model exhausts all
        # tokens on internal reasoning (<|channel|>thought) with no visible reply.
        "thinking_budget": 0,
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
    data = r.json()
    raw = data["choices"][0]["message"]["content"] or ""
    return _strip_thinking(raw)


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

    logger.info("pi_agent: session=%s jetson=%s msg_len=%d", session_id, _JETSON_MODE, len(message))

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
            response_text = await _llm_call(messages, max_tokens=_token_budget(message))
        except httpx.ConnectError:
            logger.error("pi_agent: Gemma server unreachable at %s", _model_url())
            return (
                "I'm having trouble connecting to my local AI (Gemma). "
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

            # Escalation signal — return immediately for chat.py to handle
            if tool_result.startswith("__ESCALATE__:"):
                logger.info("pi_agent: escalation triggered — %s", tool_result[13:80])
                _fire_memory_capture(message, response_text)
                return tool_result

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
                "pi_agent: done session=%s iters=%d elapsed=%.1fs",
                session_id, iteration, elapsed,
            )
            # Smart background memory capture (fires after response is returned)
            _fire_memory_capture(message, response_text)
            return response_text

    # Should not reach here
    return response_text


# User message patterns that will NEVER contain memorable personal facts
_SKIP_MEMORY_PATTERNS = frozenset({
    "joke", "jok", "funny", "pun", "riddle", "poem", "story", "story",
    "what is", "whats", "how do", "explain", "define", "tell me about",
    "what time", "what day", "what date", "hello", "hi ", "hey", "morning",
    "good night", "thanks", "thank you", "ok", "okay", "never mind",
})


def _should_classify_for_memory(user_msg: str) -> bool:
    """Return True only if this message might contain memorable personal facts."""
    msg_lower = user_msg.lower()
    # Skip creative/informational/greeting requests
    if any(p in msg_lower for p in _SKIP_MEMORY_PATTERNS):
        return False
    # Only classify if there's a personal-info signal
    personal_signals = (
        "my ", "i am", "i'm", "i have", "i like", "i love", "i hate",
        "i prefer", "i usually", "we ", "our ", "remember", "name is",
        "called ", "born ", "birthday", "favourite", "favorite",
    )
    return any(s in msg_lower for s in personal_signals)


async def _smart_memory_capture(user_msg: str, assistant_reply: str) -> None:
    """Background task: ask Gemma if this conversation turn is worth remembering.

    Fires AFTER the main response is returned (non-blocking from user perspective).
    Uses a tiny Gemma call (max_tokens=40) to decide whether to store the fact.

    Examples of what gets saved:
      - "My daughter's name is Emma" → fact: "User's daughter is named Emma"
      - "I prefer the lights at 50% in the evenings" → preference saved
      - "hello" / "tell me a joke" / "what is X" → skipped immediately (no LLM call)
    """
    # Fast pre-filter: skip if message can't contain personal facts
    if len(user_msg) < 12 or not _should_classify_for_memory(user_msg):
        return

    prompt = (
        f"Conversation:\nUser: {user_msg[:300]}\nAssistant: {assistant_reply[:200]}\n\n"
        "Does the USER'S message reveal a personal fact, name, preference, or habit "
        "about themselves or their family that's worth remembering? "
        "Ignore jokes, stories, or explanations — only save real personal facts. "
        "Reply with JSON ONLY: "
        '{"save":true,"fact":"brief memorable fact"} or {"save":false}'
    )
    try:
        raw = await _llm_call(
            [{"role": "user", "content": prompt}],
            max_tokens=40,
            temperature=0.1,
        )
        m = re.search(r'\{[^}]+\}', raw)
        if not m:
            return
        data = json.loads(m.group())
        if data.get("save") and data.get("fact"):
            fact = str(data["fact"]).strip()[:300]
            await _mempalace_add(fact, tags=["auto_captured"])
            logger.info("pi_agent: auto-saved memory: %s", fact[:80])
    except Exception as exc:
        logger.debug("smart_memory_capture: skipped (%s)", exc)


def _fire_memory_capture(user_msg: str, assistant_reply: str) -> None:
    """Schedule smart memory capture as a background task (non-blocking)."""
    lc = user_msg.lower()
    # Always check for explicit "remember this" requests
    if any(kw in lc for kw in ("remember that", "remember this", "don't forget", "store this")):
        # Explicit request — use a simple direct save, no LLM classification needed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(
                    _mempalace_add(
                        f"User asked to remember: {user_msg[:300]}",
                        tags=["explicit_request"],
                    )
                )
        except Exception:
            pass
        return

    # Smart background classification for everything else
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_smart_memory_capture(user_msg, assistant_reply))
    except Exception:
        pass


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
    # Fast path — instant replies for time/date/status, no LLM needed
    fast = _check_fast_response(message)
    if fast:
        logger.info("pi_agent streaming: fast-path hit for session=%s", session_id)
        yield fast
        return

    t0 = time.monotonic()
    logger.info("pi_agent streaming: session=%s jetson=%s", session_id, _JETSON_MODE)

    memory_ctx = await _build_memory_context(message)
    system_prompt = f"{_PI_SOUL}\n\n{memory_ctx}" if memory_ctx else _PI_SOUL

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})

    url = f"{_model_url()}/chat/completions"
    token_budget = _token_budget(message)

    def _make_payload(msgs: list[dict]) -> dict:
        return {
            "model": _model_name(),
            "messages": msgs,
            "max_tokens": token_budget,
            "temperature": 0.7,
            "stream": True,
            "thinking_budget": 0,  # Belt+suspenders: server already has --reasoning off
        }

    payload = _make_payload(messages)

    for iteration in range(_MAX_TOOL_ITERS + 1):
        collected = ""

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
            yield "\n[Pi Agent: Gemma server offline — please check gemma-server / llama-server]"
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

            # Escalation signal — yield marker and stop; chat.py handles routing
            if tool_result.startswith("__ESCALATE__:"):
                logger.info("pi_agent streaming: escalation triggered — %s", tool_result[13:80])
                _fire_memory_capture(message, collected)
                yield tool_result
                return

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
            payload = _make_payload(messages)
            # Yield any text that appeared before the tool block
            if pre_tool_text.strip():
                yield pre_tool_text
        else:
            # Done
            elapsed = time.monotonic() - t0
            logger.info(
                "pi_agent streaming done: session=%s iters=%d elapsed=%.1fs",
                session_id, iteration, elapsed,
            )
            _fire_memory_capture(message, collected)
            return
