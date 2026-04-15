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

_PI_SOUL_STATIC = """You are Zoe, a warm home assistant. Be concise and natural. Use contractions. For simple tasks say what you did, not "Done!". For questions, just answer. For hard problems be thorough.

Answer everyday questions — recipes, cooking, how-to, science, history, maths, general knowledge — directly from your own knowledge. Only defer to a tool or say you can't help when the task genuinely requires live data (weather, news, prices) or system access.

When asked to run, execute, or use bash — or when the user gives you a shell command or python3 invocation to run — always call the bash tool and report its actual output. Never simulate or guess what a command would output.

Use tools via the function-call mechanism — never write tool JSON in your response text.

VISUAL TOOLS — call these instead of describing the result in text:
- show_map: any request about a place, location, address, directions, or "show on a map". Use your knowledge of lat/lng for cities and landmarks to populate markers directly.
- show_chart: any request for a chart, graph, or when the user gives you data to visualise (e.g. "Mon 5mm, Tue 12mm"). Do not describe the chart — render it.
- show_action_menu: when you want to offer the user 2-5 distinct next steps or choices.
- setup_telegram: any request to set up, connect, or configure Telegram.
- list_openclaw_plugins: any request about plugins, add-ons, or extensions.
- list_openclaw_skills: any request about skills, workspace abilities, or what Zoe can do.
  When you cannot do something and a skill would enable it, ALWAYS call this with
  highlight set to the skill name — do not omit highlight in the proactive case.
  Example: user asks "send me a Discord notification" →
  call list_openclaw_skills(highlight="discord"), say "I can't do that yet — installing
  the Discord skill would enable it.\""""

# Legacy alias — code that imports _PI_SOUL directly still works
_PI_SOUL = _PI_SOUL_STATIC


def _pi_soul(username: str = "", user_id: str = "") -> str:
    """Build the Pi Agent system prompt with live datetime and user identity stamped in."""
    import datetime
    now = datetime.datetime.now()
    dt_line = now.strftime("%A, %d %B %Y — %I:%M %p")
    user_line = f"The logged-in user is {username} (user_id: {user_id})." if username else (
        f"The logged-in user_id is {user_id}." if user_id else ""
    )
    header = f"[{dt_line}]\n{user_line}".strip()
    return f"{header}\n\n{_PI_SOUL_STATIC}"

# OpenAI-compatible tool definitions sent in the API request.
# llama.cpp routes these through delta.tool_calls, completely separate from text content.
# This prevents the ``` leakage bug where text tool blocks partially render before interception.
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "mempalace_search",
            "description": "Search the user's long-term memory for facts, preferences, names, or past conversations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mempalace_add",
            "description": "Store a fact, preference, or personal detail in the user's long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "The fact to remember"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags"},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ha_control",
            "description": "Control a Home Assistant device (lights, switches, media players).",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "HA entity ID, e.g. light.kitchen"},
                    "action": {"type": "string", "enum": ["turn_on", "turn_off", "toggle"], "description": "Action to perform"},
                    "data": {"type": "object", "description": "Optional extra data (brightness, color, etc.)"},
                },
                "required": ["entity_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a safe shell command to check system status or install packages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_openclaw",
            "description": "Hand off to OpenClaw for tasks requiring web search, browser access, multi-step automation, financial queries, or anything needing internet access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why this needs OpenClaw"},
                    "task": {"type": "string", "description": "Full enriched task description for OpenClaw"},
                    "background": {"type": "boolean", "description": "Set true if user wants the task done in background and to be notified when complete"},
                },
                "required": ["reason", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_map",
            "description": "Show an interactive map with one or more named locations. Use when answering questions about places, addresses, or directions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Map title, e.g. 'Bottle shops in Geraldton'"},
                    "markers": {
                        "type": "array",
                        "description": "List of locations to pin on the map",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Display name for the pin"},
                                "lat": {"type": "number", "description": "Latitude"},
                                "lng": {"type": "number", "description": "Longitude"},
                            },
                            "required": ["label", "lat", "lng"],
                        },
                    },
                    "zoom": {"type": "integer", "description": "Initial zoom level (1-19)", "default": 13},
                },
                "required": ["title", "markers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_chart",
            "description": "Render a bar, line, or pie chart to visualise data. Use when comparing numbers, showing trends, or presenting stats.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "doughnut"], "description": "Chart type"},
                    "title": {"type": "string", "description": "Chart title"},
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "X-axis labels or pie slice names"},
                    "datasets": {
                        "type": "array",
                        "description": "One or more data series",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "data": {"type": "array", "items": {"type": "number"}},
                            },
                            "required": ["data"],
                        },
                    },
                },
                "required": ["chart_type", "title", "labels", "datasets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_action_menu",
            "description": "Present the user with a set of large clickable options (2-5 choices). Use when you want to offer follow-up actions or let the user choose a direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Short question or instruction above the options"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "icon": {"type": "string", "description": "Emoji icon"},
                                "label": {"type": "string", "description": "Button label"},
                                "message": {"type": "string", "description": "Message to send when clicked"},
                            },
                            "required": ["label", "message"],
                        },
                    },
                },
                "required": ["prompt", "options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_openclaw_plugins",
            "description": "Show the OpenClaw plugin manager so the user can install, remove, or browse available plugins.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_openclaw_skills",
            "description": (
                "Show the OpenClaw skills manager (browse, install, update, remove). "
                "When the user wants something you cannot do and installing a skill would enable it, "
                "you MUST call this with highlight='<skill-name>' (e.g. highlight='discord'). "
                "Never omit highlight in the proactive case. "
                "Also call when the user asks about capabilities, skills, or what Zoe can do."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "highlight": {
                        "type": "string",
                        "description": "Optional skill name to pre-select/highlight in the manager (e.g. 'discord')",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_telegram",
            "description": "Show the Telegram setup wizard in chat so the user can connect their Telegram bot to Zoe. Use when the user asks to set up, connect, or configure Telegram.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# After Gemma LoRA fine-tuning on Zoe's voice, _PI_SOUL shrinks to ~10 tokens.

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
    words = set(msg.split())  # whole-word matching to avoid "times" → "time"

    # Time queries — only if "time" is an actual word (not inside "times", "sometimes" etc.)
    if "time" in words or "clock" in words:
        if any(w in msg for w in ("what", "tell", "current", "now")):
            return f"It's {now.strftime('%-I:%M %p')} on {now.strftime('%A, %d %B %Y')}."
    if msg in ("time", "the time", "current time", "what time", "clock"):
        return f"It's {now.strftime('%-I:%M %p')}."

    # Date queries — match several natural phrasings
    _date_triggers = {
        "what day is it", "what's the date", "whats the date",
        "what is today", "what date is it", "today's date", "today",
        "what day is it today", "what is the date today", "what is todays date",
        "what is today's date", "what day is today",
    }
    if msg in _date_triggers or ("date" in words and "what" in words) or \
       ("day" in words and "today" in words):
        return f"Today is {now.strftime('%A, %d %B %Y')}."

    # Uptime / status
    _status_triggers = {
        "status", "are you running", "are you there", "you there", "ping",
        "are you online", "are you up", "you online", "are you working",
    }
    tier = "Jetson GPU" if _JETSON_MODE else "Pi 5"
    if msg in _status_triggers:
        return f"I'm here and running on your {tier}. How can I help?"

    return None


def _model_url() -> str:
    return _GEMMA_URL


def _model_name() -> str:
    return "google_gemma-4-E2B-it-Q4_K_M"


# ── MemPalace integration (Python API — no subprocess) ───────────────────────

async def _mempalace_search(
    query: str,
    user_id: str = "family-admin",
    limit: int = 5,
    timeout_s: float = 2.0,
) -> list[dict]:
    """Semantic search of this user's MemPalace facts (wing-filtered, ONNX-based).

    Enforces a hard timeout so a slow ONNX embedding run never blocks inference.
    """
    try:
        from mempalace.searcher import search_memories  # type: ignore[import]
        raw = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: search_memories(query, _MEMPALACE_DATA, wing=user_id, n_results=limit),
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


async def _mempalace_add(
    summary: str,
    user_id: str = "family-admin",
    tags: list[str] | None = None,
    added_by: str = "pi_agent",
) -> bool:
    """Upsert a memory fact into MemPalace scoped to user_id.

    Uses upsert (not add) so updating an existing fact works correctly.
    Stores added_at ISO timestamp so _mempalace_load_user_facts can sort by recency.
    """
    import hashlib
    import datetime

    def _write() -> None:
        from mempalace.palace import get_collection  # type: ignore[import]
        col = get_collection(_MEMPALACE_DATA)
        # Include user_id in hash so each user gets their own record for same fact
        drawer_id = f"zoe_{user_id}_{hashlib.md5(summary.encode()).hexdigest()[:16]}"
        col.upsert(
            ids=[drawer_id],
            documents=[summary],
            metadatas=[{
                "wing": user_id,            # wing=user_id enables per-user filter
                "room": "conversations",
                "added_by": added_by,
                "tags": ",".join(tags or []),
                "added_at": datetime.datetime.now().isoformat(),
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


async def _mempalace_load_user_facts(user_id: str, limit: int = 20) -> str:
    """Load a user's MemPalace facts sorted by recency. No ONNX — metadata filter only.

    Uses collection.get(where={"wing": user_id}) which is a pure metadata filter
    with no vector/ONNX cost — safe to call on every Bonsai/OpenClaw/Pi turn.
    Sorts by added_at timestamp in application code (Chroma get() has no sort).
    """
    def _get() -> list[str]:
        from mempalace.palace import get_collection  # type: ignore[import]
        col = get_collection(_MEMPALACE_DATA)
        result = col.get(
            where={"wing": user_id},
            include=["documents", "metadatas"],
        )
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        # Sort most-recent first using added_at, fall back to "" (sorts last)
        pairs = sorted(zip(docs, metas), key=lambda x: x[1].get("added_at", ""), reverse=True)
        return [doc for doc, _ in pairs[:limit]]

    try:
        docs = await asyncio.get_event_loop().run_in_executor(None, _get)
        if not docs:
            return ""
        lines = ["## What I know about you:"]
        for doc in docs:
            lines.append(f"- {doc[:200]}")
        return "\n".join(lines)
    except ImportError:
        logger.warning("MemPalace not installed — skipping user facts load")
        return ""
    except Exception as exc:
        logger.debug("_mempalace_load_user_facts failed (non-fatal): %s", exc)
        return ""


# Keywords that suggest the message benefits from memory context retrieval.
# Queries without these skip MemPalace semantic search (saves 1-25s of ONNX inference time).
# Note: _mempalace_load_user_facts() (fast metadata filter) runs on EVERY turn regardless.
_MEMORY_TRIGGER_WORDS = frozenset({
    "remember", "recall", "did i", "have i", "last time", "before",
    "you said", "we talked", "my name", "my preference", "i told you",
    "favourite", "favorite", "prefer", "like", "usually", "always", "never", "often",
    "who is", "what is my", "what do i", "what's my", "family", "remind me",
    "do i have", "my favourite", "my favorite", "my usual", "i usually", "i like",
    "i prefer", "i love", "i hate", "i enjoy", "do you know my",
    # Added: common personal-fact retrieval phrases
    "born", "age", "years old", "my age", "how old", "my birthday", "birthday",
    "my full name", "called", "known as", "allerg", "condition", "medical",
})


def _message_needs_memory(message: str) -> bool:
    """Return True only if this message is likely to benefit from MemPalace semantic search."""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _MEMORY_TRIGGER_WORDS)


async def _build_memory_context(message: str, user_id: str = "family-admin") -> str:
    """Semantic search of user's MemPalace facts — keyword-gated to avoid ONNX cost on every turn.

    Only runs for messages containing _MEMORY_TRIGGER_WORDS.
    The fast _mempalace_load_user_facts() is called separately and always runs.
    """
    if not _message_needs_memory(message):
        return ""
    _mp_timeout = 5.0 if _JETSON_MODE else 3.0
    memories = await _mempalace_search(message, user_id=user_id, limit=5, timeout_s=_mp_timeout)
    if not memories:
        return ""
    lines = ["## Relevant memories (semantic match):"]
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

async def _dispatch_tool(tool_name: str, args: dict, user_id: str = "family-admin") -> str:
    """Dispatch a tool call and return result as string."""
    if tool_name == "mempalace_search":
        results = await _mempalace_search(
            args.get("query", ""), user_id=user_id, limit=int(args.get("limit", 5))
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
            user_id=user_id,
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
        background = args.get("background", False)
        if background:
            return f"__ESCALATE_BG__:{reason}|{task}"
        return f"__ESCALATE__:{reason}|{task}"

    if tool_name == "show_map":
        payload = {
            "component": "map_embed",
            "props": {
                "title": args.get("title", "Map"),
                "markers": args.get("markers", []),
                "zoom": args.get("zoom", 13),
            },
        }
        return f"__UI__:{json.dumps(payload)}"

    if tool_name == "show_chart":
        payload = {
            "component": "chart",
            "props": {
                "chart_type": args.get("chart_type", "bar"),
                "title": args.get("title", ""),
                "labels": args.get("labels", []),
                "datasets": args.get("datasets", []),
            },
        }
        return f"__UI__:{json.dumps(payload)}"

    if tool_name == "show_action_menu":
        payload = {
            "component": "action_menu",
            "props": {
                "prompt": args.get("prompt", ""),
                "options": args.get("options", []),
            },
        }
        return f"__UI__:{json.dumps(payload)}"

    if tool_name == "setup_telegram":
        payload = {
            "component": "telegram_setup",
            "props": {},
        }
        return f"__UI__:{json.dumps(payload)}"

    if tool_name == "list_openclaw_plugins":
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get("http://localhost:8000/api/openclaw/plugins")
                plugin_data = r.json() if r.status_code == 200 else {"plugins": []}
        except Exception:
            plugin_data = {"plugins": []}
        payload = {
            "component": "openclaw_manager",
            "props": plugin_data,
        }
        return f"__UI__:{json.dumps(payload)}"

    if tool_name == "list_openclaw_skills":
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get("http://localhost:8000/api/openclaw/skills")
                skill_data = r.json() if r.status_code == 200 else {"skills": []}
        except Exception:
            skill_data = {"skills": []}
        highlight = args.get("highlight", "")
        payload = {
            "component": "skills_manager",
            "props": {**skill_data, "highlight": highlight},
        }
        return f"__UI__:{json.dumps(payload)}"

    return f"[unknown tool: {tool_name}]"


# ── LLM call / KV warmup ─────────────────────────────────────────────────────

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
                use_tools=False,
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
    use_tools: bool = True,
) -> tuple[str, str | None, dict | None]:
    """Make a non-streaming chat completion request to the local model.

    Returns:
        (text, tool_name, tool_args) — tool_name/args are None if no tool call.
        Uses the OpenAI tools API so tool calls come through delta.tool_calls,
        never through text content (prevents the ``` leak bug).
    """
    url = f"{_model_url()}/chat/completions"
    payload: dict = {
        "model": _model_name(),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "thinking_budget": 0,
    }
    if use_tools:
        payload["tools"] = _TOOLS
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
    data = r.json()
    choice = data["choices"][0]["message"]

    # Check for tool call via the proper API channel (never leaks to text)
    tool_calls = choice.get("tool_calls") or []
    if tool_calls:
        tc = tool_calls[0]
        tool_name = tc.get("function", {}).get("name")
        try:
            tool_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
        except json.JSONDecodeError:
            tool_args = {}
        return "", tool_name, tool_args

    raw = choice.get("content") or ""
    return _strip_thinking(raw), None, None


# ── Main Pi Agent entry point ─────────────────────────────────────────────────

async def run_pi_agent(
    message: str,
    session_id: str,
    user_id: str = "family-admin",
    *,
    history: list[dict] | None = None,
    db_memory_context: str | None = None,
    max_tokens_override: int = 0,
) -> str:
    """
    Run the Pi Agent loop for a single turn.

    Args:
        message:           The user's message.
        session_id:        Session identifier for logging.
        user_id:           Authenticated user id.
        history:           Optional prior messages for context window.
        db_memory_context: Pre-loaded approved memory facts from memory_items (fast DB query).

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

    # Load user facts from MemPalace (fast metadata filter — no ONNX) + optional semantic hit
    mp_facts = await _mempalace_load_user_facts(user_id)
    memory_ctx = await _build_memory_context(message, user_id=user_id)
    extras = "\n\n".join(filter(None, [mp_facts, db_memory_context, memory_ctx]))
    system_prompt = f"{_pi_soul(user_id=user_id)}\n\n{extras}" if extras else _pi_soul(user_id=user_id)

    # Build initial messages list
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        # Use full window loaded from DB (matches Hermes full-session approach)
        messages.extend(history[-12:])
    messages.append({"role": "user", "content": message})

    # Tool loop — tool calls come through the API's tool_calls channel (never text)
    for iteration in range(_MAX_TOOL_ITERS + 1):
        try:
            budget = max_tokens_override if max_tokens_override > 0 else _token_budget(message)
            response_text, tool_name, tool_args = await _llm_call(
                messages, max_tokens=budget
            )
        except httpx.ConnectError:
            logger.error("pi_agent: Gemma server unreachable at %s", _model_url())
            return (
                "I'm having trouble connecting to my local AI (Gemma). "
                "Please check that the inference server is running."
            )
        except Exception as exc:
            logger.exception("pi_agent: LLM call failed (iter %d): %s", iteration, exc)
            return "Something went wrong — I couldn't generate a response. Please try again."

        if tool_name and iteration < _MAX_TOOL_ITERS:
            logger.info(
                "pi_agent: iter=%d tool=%s args=%s",
                iteration, tool_name, json.dumps(tool_args)[:120],
            )
            tool_result = await _dispatch_tool(tool_name, tool_args or {}, user_id=user_id)
            logger.debug("pi_agent: tool_result=%s", tool_result[:200])

            # Escalation signal — return immediately for chat.py to handle
            if tool_result.startswith("__ESCALATE__:"):
                logger.info("pi_agent: escalation triggered — %s", tool_result[13:80])
                _fire_memory_capture(message, response_text, user_id=user_id)
                return tool_result

            # Append tool call + result in OpenAI tool_calls format for context
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": f"call_{iteration}", "type": "function",
                                "function": {"name": tool_name, "arguments": json.dumps(tool_args or {})}}],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{iteration}",
                "content": tool_result,
            })
        else:
            # No tool call (or max iterations reached) — final response
            elapsed = time.monotonic() - t0
            logger.info(
                "pi_agent: done session=%s iters=%d elapsed=%.1fs",
                session_id, iteration, elapsed,
            )
            _fire_memory_capture(message, response_text, user_id=user_id)
            return response_text

    return response_text


# User message patterns that will NEVER contain memorable personal facts
# Rule-based memory extraction patterns — pure Python, zero GPU cost.
# Replaces the _smart_memory_capture Gemma classifier to eliminate GPU contention
# on the next chat request. Modelled on what the Gemma classifier was catching,
# plus broader coverage for locations, relationships, ages, and identifiers.
_MEM_EXTRACT_PATTERNS: list[tuple[str, str]] = [
    # Explicit storage requests (always save verbatim)
    (r"(?:please\s+)?remember\s+(?:that\s+|this\s+)?(.{10,300})", "User asked to remember: {0}"),
    (r"don'?t\s+forget\s+(?:that\s+)?(.{10,200})", "Note: {0}"),
    (r"store\s+this[:\s]+(.{10,200})", "Stored fact: {0}"),
    # Personal identity
    (r"my\s+(?:full\s+)?name\s+is\s+(.{2,60})", "User's name is {0}"),
    (r"i(?:'m|\s+am)\s+(?:called|named|known\s+as)\s+([A-Za-z][A-Za-z\s]{1,40})", "User's name is {0}"),
    (r"call\s+me\s+([A-Za-z][A-Za-z\s]{1,30})", "User goes by: {0}"),
    # Birth / age
    (r"i\s+was\s+born\s+(?:on\s+)?(.{3,50})", "User was born on: {0}"),
    (r"my\s+(?:birthday|birth\s+date)\s+is\s+(.{3,40})", "User's birthday is {0}"),
    (r"i(?:'m|\s+am)\s+(\d{1,3})\s+years?\s+old", "User is {0} years old"),
    # Health / allergies
    (r"i(?:'m|\s+am)\s+allergic\s+to\s+(.{3,80})", "User is allergic to: {0}"),
    (r"i\s+have\s+(diabetes|asthma|coeliac|celiac|hypertension|[a-z]+\s+allergy)", "User has: {0}"),
    # Preferences
    (r"i\s+prefer\s+(.{3,80})\s+over\s+(.{3,80})", "User prefers {0} over {1}"),
    (r"i\s+(?:prefer|like|love|enjoy|adore)\s+(.{3,120})", "User prefers/likes: {0}"),
    (r"i\s+(?:don'?t\s+like|dislike|hate|can'?t\s+stand|detest)\s+(.{3,80})", "User dislikes: {0}"),
    (r"my\s+favou?rite\s+(?:\w+\s+)?is\s+(.{3,80})", "User's favourite: {0}"),
    # Family / relationships
    (r"my\s+(?:wife|husband|partner|girlfriend|boyfriend)'?s?\s+(?:name\s+)?is\s+(?:called\s+|named\s+)?([A-Za-z][A-Za-z\s]{1,40})", "User's partner is {0}"),
    (r"my\s+(?:son|daughter|kid|child)'?s?\s+(?:name\s+)?is\s+(?:called\s+|named\s+)?([A-Za-z][A-Za-z\s]{1,40})", "User's child is {0}"),
    (r"my\s+(?:dog|cat|pet)'?s?\s+(?:name\s+)?is\s+(?:called\s+|named\s+)?([A-Za-z][A-Za-z\s]{1,30})", "User's pet is called {0}"),
    (r"my\s+(?:mum|mom|dad|father|mother|brother|sister)'?s?\s+(?:name\s+)?is\s+(.{2,50})", "User's family member: {0}"),
    # Numbers / identifiers
    (r"my\s+(?:lucky\s+)?number\s+is\s+(\d+)", "User's lucky number is {0}"),
    # Location / work
    (r"i\s+live\s+in\s+(.{3,60})", "User lives in {0}"),
    (r"i\s+work\s+(?:at|for)\s+(.{3,80})", "User works at/for: {0}"),
    (r"i(?:'m|\s+am)\s+from\s+(.{3,60})", "User is from {0}"),
    (r"i\s+grew\s+up\s+in\s+(.{3,60})", "User grew up in {0}"),
    # Habits / routines
    (r"i\s+usually\s+(.{5,100})", "User's habit: {0}"),
    (r"i\s+always\s+(.{5,100})", "User always: {0}"),
    (r"every\s+(?:morning|evening|night|day|week)\s+i\s+(.{5,100})", "User's routine: {0}"),
]

_MEM_SKIP_PREFIXES = frozenset({
    "what is", "what are", "how do", "how does", "explain", "tell me about",
    "what time", "what day", "what date", "what year", "what month",
    "hello", "hi ", "hey", "good morning", "good night", "good evening",
    "thanks", "thank you", "ok", "okay", "never mind", "never",
    "tell me a joke", "write me", "can you write", "can you give me",
})


def _fast_memory_extract(user_msg: str, _assistant_reply: str = "") -> list[str]:
    """Extract memorable facts from a message using pure-Python regex rules.

    Zero GPU cost. Returns a list of short fact strings ready to store in MemPalace.
    """
    if len(user_msg) < 8:
        return []
    lc = user_msg.lower().strip()
    # Quick skip for clearly non-personal messages
    if any(lc.startswith(p) for p in _MEM_SKIP_PREFIXES):
        return []

    facts: list[str] = []
    for pattern, template in _MEM_EXTRACT_PATTERNS:
        m = re.search(pattern, user_msg, re.IGNORECASE)
        if m:
            groups = [g.strip() if g else "" for g in m.groups()]
            try:
                fact = template.format(*groups).strip()
                if fact and len(fact) > 8:
                    facts.append(fact[:300])
            except (IndexError, KeyError):
                pass

    # Deduplicate: remove facts where >70% words overlap with an already-included fact
    deduped: list[str] = []
    for fact in facts:
        fact_words = set(fact.lower().split())
        if not any(
            len(fact_words & set(f.lower().split())) / max(len(fact_words), 1) > 0.7
            for f in deduped
        ):
            deduped.append(fact)
    return deduped


async def _background_memory_save(
    user_msg: str, assistant_reply: str, user_id: str = "family-admin"
) -> None:
    """Async background task: extract facts and write to MemPalace with dedup check.

    Runs entirely on CPU — no llama-server calls. ChromaDB writes are fast (~5ms).
    Dedup is user-scoped so we don't skip writes based on another user's facts.
    """
    facts = _fast_memory_extract(user_msg, assistant_reply)
    for fact in facts:
        try:
            # Dedup: search within THIS user's facts only
            existing = await _mempalace_search(fact, user_id=user_id, limit=1, timeout_s=1.5)
            if existing:
                top_text = existing[0].get("text") or existing[0].get("content") or ""
                fact_words = set(fact.lower().split())
                top_words = set(top_text.lower().split())
                overlap = len(fact_words & top_words) / max(len(fact_words), 1)
                if overlap > 0.65:
                    logger.debug("pi_agent: memory dedup skip (%.0f%% overlap): %s", overlap * 100, fact[:60])
                    continue
            await _mempalace_add(fact, user_id=user_id, tags=["auto_rule"])
            logger.info("pi_agent: rule-captured memory for %s: %s", user_id, fact[:80])
        except Exception as exc:
            logger.debug("background_memory_save: skipped (%s)", exc)


def _fire_memory_capture(
    user_msg: str, assistant_reply: str, user_id: str = "family-admin"
) -> None:
    """Schedule rule-based memory extraction as a background task (non-blocking, zero GPU)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_background_memory_save(user_msg, assistant_reply, user_id))
    except Exception:
        pass


# ── Streaming version for SSE chat endpoint ───────────────────────────────────

async def run_pi_agent_streaming(
    message: str,
    session_id: str,
    user_id: str = "family-admin",
    *,
    history: list[dict] | None = None,
    db_memory_context: str | None = None,
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

    # Load user facts (fast metadata filter) + optional semantic search hit
    mp_facts = await _mempalace_load_user_facts(user_id)
    memory_ctx = await _build_memory_context(message, user_id=user_id)
    extras = "\n\n".join(filter(None, [mp_facts, db_memory_context, memory_ctx]))
    system_prompt = f"{_pi_soul(user_id=user_id)}\n\n{extras}" if extras else _pi_soul(user_id=user_id)

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        # Use the full window loaded from DB (matches Hermes full-session approach)
        messages.extend(history[-12:])
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
            "thinking_budget": 0,
            # Tool calls arrive via delta.tool_calls — completely separate from text.
            # This eliminates the ``` leak: the model never writes tool JSON into content.
            "tools": _TOOLS,
            "tool_choice": "auto",
        }

    payload = _make_payload(messages)

    for iteration in range(_MAX_TOOL_ITERS + 1):
        collected = ""
        # Accumulate streaming tool_calls deltas (function name + arguments)
        streaming_tool_name: str | None = None
        streaming_tool_args_buf = ""

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
                            delta = chunk["choices"][0]["delta"]

                            # Regular text content — yield immediately, no buffering needed
                            text_delta = delta.get("content") or ""
                            if text_delta:
                                collected += text_delta
                                yield text_delta

                            # Tool call delta — accumulate silently, nothing reaches the user
                            tc_deltas = delta.get("tool_calls") or []
                            for tc in tc_deltas:
                                fn = tc.get("function", {})
                                if fn.get("name"):
                                    streaming_tool_name = fn["name"]
                                streaming_tool_args_buf += fn.get("arguments", "")

                        except (json.JSONDecodeError, KeyError):
                            pass

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

        # Tool call came through the API channel — parse and dispatch
        tool_name = streaming_tool_name
        tool_args: dict | None = None
        if tool_name:
            try:
                tool_args = json.loads(streaming_tool_args_buf) if streaming_tool_args_buf else {}
            except json.JSONDecodeError:
                tool_args = {}

        if tool_name and iteration < _MAX_TOOL_ITERS:
            if on_tool_start:
                try:
                    await on_tool_start(tool_name, tool_args or {})
                except Exception:
                    pass

            logger.info("pi_agent streaming: iter=%d tool=%s args=%s",
                        iteration, tool_name, json.dumps(tool_args)[:120])
            tool_result = await _dispatch_tool(tool_name, tool_args or {}, user_id=user_id)

            # Escalation signal — yield marker and stop; chat.py handles routing
            if tool_result.startswith("__ESCALATE__:"):
                logger.info("pi_agent streaming: escalation triggered — %s", tool_result[13:80])
                _fire_memory_capture(message, collected, user_id=user_id)
                yield tool_result
                return

            # UI component — yield marker so chat.py can emit zoe.ui_component event;
            # fall through to append tool messages and let the model produce follow-up text
            if tool_result.startswith("__UI__:"):
                logger.info("pi_agent streaming: UI component — %s", tool_result[7:60])
                yield tool_result

            if on_tool_end:
                try:
                    await on_tool_end(tool_name, tool_result)
                except Exception:
                    pass

            # Append in OpenAI tool_calls format so context stays consistent
            messages.append({
                "role": "assistant",
                "content": collected or None,
                "tool_calls": [{"id": f"call_{iteration}", "type": "function",
                                "function": {"name": tool_name,
                                             "arguments": json.dumps(tool_args or {})}}],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{iteration}",
                "content": tool_result,
            })
            payload = _make_payload(messages)
        else:
            # Done — no tool call in this iteration
            elapsed = time.monotonic() - t0
            logger.info(
                "pi_agent streaming done: session=%s iters=%d elapsed=%.1fs",
                session_id, iteration, elapsed,
            )
            _fire_memory_capture(message, collected, user_id=user_id)
            return


# ── One-time MemPalace legacy migration ──────────────────────────────────────

_MIGRATION_DONE_FLAG = os.path.join(
    os.environ.get("MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace")),
    ".migration_v1_done",
)


def migrate_mempalace_legacy_records(default_user_id: str = "family-admin") -> None:
    """One-time migration: re-tag legacy records (wing='zoe') to wing=default_user_id.

    Also stamps added_at on any records missing it.
    Safe to call at startup — exits immediately if already done.
    """
    if os.path.exists(_MIGRATION_DONE_FLAG):
        return
    try:
        import datetime
        from mempalace.palace import get_collection  # type: ignore[import]
        col = get_collection(_MEMPALACE_DATA)
        old = col.get(where={"wing": "zoe"}, include=["documents", "metadatas"])
        ids = old.get("ids") or []
        docs = old.get("documents") or []
        metas = old.get("metadatas") or []
        if ids:
            now_iso = datetime.datetime.now().isoformat()
            updated_metas = []
            for m in metas:
                m = dict(m)
                m["wing"] = default_user_id
                if "added_at" not in m:
                    m["added_at"] = now_iso
                updated_metas.append(m)
            col.upsert(ids=ids, documents=docs, metadatas=updated_metas)
            logger.info("mempalace: migrated %d legacy records → wing=%s", len(ids), default_user_id)
        # Mark done
        os.makedirs(os.path.dirname(_MIGRATION_DONE_FLAG), exist_ok=True)
        with open(_MIGRATION_DONE_FLAG, "w") as f:
            f.write(f"migrated {len(ids)} records\n")
    except ImportError:
        pass  # MemPalace not installed — nothing to migrate
    except Exception as exc:
        logger.warning("mempalace migration failed (non-fatal): %s", exc)
