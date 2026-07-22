import asyncio
import random
import base64
import contextvars
import importlib.util
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from auth import get_current_user
from database import get_db
from hermes_http import hermes_auth_headers
from stt_wake_strip import _strip_wake_word
from typed_env import env_bool, env_float, env_int, env_str
# Waterfall engine mechanics live in tts_waterfall; they are re-exported here so
# existing importers (main.py health detail, tests that monkeypatch this module,
# tests/replay_samples.py, scripts/perf/measure_tts.py) keep working unchanged.
from tts_waterfall import (
    _clean_for_speech,
    _has_espeak_ng,
    _kokoro_http_client,
    _stream_kokoro_sentence_wavs,
    _synthesize_edge_tts,
    _synthesize_espeak,
    _synthesize_kokoro,
    _synthesize_kokoro_sidecar,
    _synthesize_local_service,
    edge_tts_available,
    kokoro_configured,
    kokoro_ready,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Strong references to fire-and-forget background tasks. asyncio only keeps a
# WEAK reference to tasks created by ensure_future, so a GC mid-flight could
# cancel a DB-writing coroutine partway through — that's what corrupted the
# asyncpg connection ("another operation is in progress") and silently dropped
# chat_messages saves. Holding the task here until it finishes prevents that.
_BG_TASKS: set = set()


def _spawn_bg(coro) -> None:
    """Schedule a background coroutine with a strong reference (see _BG_TASKS)."""
    try:
        t = asyncio.ensure_future(coro)
    except Exception as exc:  # no running loop — run inline best-effort
        logger.warning("_spawn_bg could not schedule task: %s", exc)
        return
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)

# ── Voice session continuity ───────────────────────────────────────────────
# Persist one session_id per panel so follow-up voice commands have context.
# Pass 3 B3: also stores bound_user_id (resolved via daemon VID or PIN) with
# the same 5-min TTL so mid-conversation turns don't re-challenge.
# Dict: panel_id → {"session_id": str, "last_at": float,
#                   "bound_user_id": str|None, "context": ConversationContext}
_VOICE_SESSIONS: dict[str, dict] = {}
_VOICE_SESSION_TTL_S = 5 * 60  # Reset after 5 min silence

# Pass 3 B4: stash of voice turns awaiting PIN confirmation.
# Dict: panel_id → {pending_id, transcript, session_id, expire_at}
_PENDING_VOICE_IDENT: dict[str, dict] = {}

# Introduction flow state: panel_id → {person_id, person_name, step, expires}
# step 0 = greeted, awaiting job/interest answer
# step 1 = collected first answer, asking follow-up
_INTRO_STATE: dict[str, dict] = {}


def _get_or_create_voice_session(panel_id: str) -> str:
    """Return the existing session_id for this panel, or create a new one."""
    import uuid as _uuid
    now = time.monotonic()
    entry = _VOICE_SESSIONS.get(panel_id)
    if entry and (now - entry["last_at"]) < _VOICE_SESSION_TTL_S:
        entry["last_at"] = now
        return entry["session_id"]
    session_id = f"voice-panel-{panel_id}-{_uuid.uuid4().hex[:8]}"
    # Preserve bound identity across idle TTL rollovers so authenticated
    # panels do not get unnecessary repeat auth challenges.
    _next = {"session_id": session_id, "last_at": now}
    if entry and entry.get("bound_user_id"):
        _next["bound_user_id"] = entry["bound_user_id"]
    if entry and entry.get("context") and entry["context"].is_fresh():
        _next["context"] = entry["context"]
    _VOICE_SESSIONS[panel_id] = _next
    return session_id


async def _load_voice_history(session_id: str, limit: int = 3) -> list[dict]:
    """Load last N chat turns for voice LLM context window (mirrors chat.py pattern)."""
    try:
        # get_db_ctx, not `async for db in get_db()`: returning from inside the
        # generator leaks the pooled connection (#953 / the 2026-07-03 pool
        # drain) — and this runs on EVERY voice turn.
        from db_pool import get_db_ctx
        async with get_db_ctx() as db:
            rows = await db.execute(
                "SELECT role, content FROM chat_messages "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            rows = await rows.fetchall()
            return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception:
        pass
    return []


# Query-relevant recall packet sizing. A handful of the most relevant facts is
# enough for the brain to answer a recall question; sending the whole metadata
# dump (~1264 chars) is both slow and noisy. Keep this lean — it sits on the
# brain-turn critical path.
_VOICE_RECALL_SEARCH_LIMIT = 8   # one semantic search; re-ranked by hotness
_VOICE_RECALL_MAX_FACTS = 6      # vector-recall facts that make it into the block
_VOICE_RECALL_FACT_CHARS = 160   # per-fact truncation so one long memory can't bloat it
# Overall line budget for the whole "[What you remember]" block (vector recall +
# the 2c relational lines combined). The compose module can return up to ~25
# relational lines; without a combined cap a fully-populated relational query
# would balloon the packet to 30+ lines and blow the "COMPACT" contract this
# block exists to keep. A little larger than _VOICE_RECALL_MAX_FACTS so a
# relational turn can carry a few cited people/date facts on top of vector recall.
_VOICE_RECALL_MAX_LINES = 10


async def _voice_recall_packet(text: str, user_id: str) -> Optional[str]:
    """Build a COMPACT, QUERY-RELEVANT "[What you remember]" block for a voice
    brain turn from the turn TEXT.

    Replaces the query-blind metadata dump on the read path: one
    `MemoryService.search` call (semantic + hotness re-ranking), deduped and
    truncated to a few hundred chars of only the facts relevant to what was asked.
    Falls back to the full for-prompt dump when search returns nothing (e.g. a
    chit-chat turn with no recall intent, or an embedder hiccup) so recall never
    silently degrades. Best-effort: guest-safe and never raises."""
    query = (text or "").strip()
    if not query:
        # No turn text (e.g. prewarm) — warm/return the fallback dump instead so
        # the underlying facts cache stays primed.
        return await _voice_recall_fallback(user_id)
    try:
        from memory_service import get_memory_service, is_guest_memory_user
        if is_guest_memory_user(user_id):
            return None
        svc = get_memory_service()
        refs = await svc.search(query, user_id=user_id, limit=_VOICE_RECALL_SEARCH_LIMIT)
    except Exception as exc:
        logger.debug("voice recall search failed (non-fatal): %s", exc)
        refs = None

    # Increment 2c: mirror chat — when ZOE_MEMORY_COMPOSE_ENABLED is ON and the
    # turn is a person/relationship/date query, fold the SAME cited relational
    # block chat's /for-prompt packet uses (people/relationships/dates + portrait,
    # from Postgres) into voice recall. Reuses the shared zoe_memory_compose
    # builder (same flag + router gate + per-user/approved-only scoping) — no
    # duplicated compose/gate logic. Flag OFF is a true no-op: compose_packet
    # cheap-gates and returns None before any DB read, so refs alone drive the
    # output exactly as pre-2c. (The guest early-return above means this only runs
    # for non-guest users.) Best-effort — never raises.
    relational_lines = await _voice_relational_lines(query, user_id)

    if not refs and not relational_lines:
        # Search found nothing relevant — fall back to the for-prompt dump so a
        # recall question still has facts to draw on.
        return await _voice_recall_fallback(user_id)

    seen: set[str] = set()
    lines: list[str] = []
    for ref in refs or []:
        fact = (getattr(ref, "text", "") or "").strip()
        if not fact:
            continue
        line = fact[:_VOICE_RECALL_FACT_CHARS].strip()
        # Dedup on the TRUNCATED line that actually gets emitted — two long facts
        # identical in their first _VOICE_RECALL_FACT_CHARS chars would otherwise
        # produce duplicate output lines.
        key = re.sub(r"\s+", " ", line.lower())
        if key in seen:
            continue
        seen.add(key)
        lines.append("- " + line)
        if len(lines) >= _VOICE_RECALL_MAX_FACTS:
            break
    for line in relational_lines:
        if len(lines) >= _VOICE_RECALL_MAX_LINES:
            break
        key = re.sub(r"\s+", " ", line.lower())
        if key in seen:
            continue
        seen.add(key)
        lines.append("- " + line)
    if not lines:
        return await _voice_recall_fallback(user_id)
    return "[What you remember]\n" + "\n".join(lines)


async def _voice_relational_lines(query: str, user_id: str) -> list[str]:
    """The cited relational lines chat folds into /for-prompt (2c mirror), or [].

    Delegates the gate + DB read + block build to the shared
    ``zoe_memory_compose.compose_packet`` so voice and chat share ONE composed
    memory source. Returns the plain (already-cited) fact strings — the caller
    prefixes each with ``- `` and dedups against the vector recall lines. When the
    flag is OFF or the query is non-relational, ``compose_packet`` returns None and
    this is ``[]`` (a true no-op). Best-effort: never raises.
    """
    try:
        from zoe_memory_compose import compose_packet

        block = await compose_packet(user_id, query)
    except Exception as exc:  # compose_packet already swallows; belt-and-braces
        logger.debug("voice relational compose failed (non-fatal): %s", exc)
        return []
    if not block:
        return []
    out: list[str] = []
    for raw in block.get("lines") or []:
        # The block lines already start with "- " (see zoe_memory_compose
        # ._build_lines); strip it so the caller's uniform "- " prefix applies.
        line = raw.lstrip()
        if line.startswith("- "):
            line = line[2:]
        line = line.strip()
        if line:
            out.append(line)
    return out


async def _voice_recall_fallback(user_id: str) -> Optional[str]:
    """Full for-prompt metadata dump (the previous behaviour). Used when the
    query-relevant search yields nothing, and to warm the facts cache on prewarm.
    Best-effort / never raises."""
    try:
        from zoe_agent import _mempalace_load_user_facts
        return (await _mempalace_load_user_facts(user_id)) or None
    except Exception as exc:
        logger.debug("voice recall fallback load failed (non-fatal): %s", exc)
        return None


async def _voice_brain_memory(
    user_id: str, text: Optional[str] = None
) -> tuple[Optional[str], Optional[str]]:
    """Load (db_memory_context, portrait) for a voice brain turn.

    Voice no longer fast-paths people/memory recall (it was slow and mis-stored
    questions as facts), so the brain must answer recall itself. Unlike chat —
    where the memory.ts extension injects the for-prompt packet — the voice path
    calls the brain directly, so we inject the user's facts + portrait here or the
    brain would have no memory to recall from.

    When `text` (the turn transcript) is given, db_memory is a COMPACT
    query-relevant recall packet built from that text (token-efficient; only the
    facts relevant to what was asked). When `text` is None — the wake prewarm
    path — it falls back to the full for-prompt dump, which also warms the shared
    facts cache for the real turn. Best-effort (guest-safe / never raises)."""
    db_memory: Optional[str] = None
    portrait: Optional[str] = None
    try:
        db_memory = await _voice_recall_packet(text or "", user_id)
    except Exception as exc:
        logger.debug("voice brain memory load failed (non-fatal): %s", exc)
    try:
        from user_portrait import load_portrait
        portrait = (await load_portrait(user_id)) or None
    except Exception as exc:
        logger.debug("voice brain portrait load failed (non-fatal): %s", exc)
    # The user's NAME is identity (who they authenticated as), NOT a memory fact —
    # so "what's my name" is answered from auth, never from recall. Ground every
    # brain turn in who's speaking by prepending it to the [About you] block.
    identity = await _voice_user_identity(user_id)
    if identity:
        line = f"You are speaking with {identity} (the signed-in user)."
        portrait = f"{line}\n{portrait}" if portrait else line
    return db_memory, portrait


async def _voice_user_identity(user_id: str) -> Optional[str]:
    """The signed-in user's display name from the identity (users) table — NOT a
    memory fact. Memory is per-user and the user authenticates as themselves, so
    their name is known from auth. Skips guest/daemon/admin. Best-effort/never raises."""
    if not user_id or user_id in ("guest", "voice-daemon", "family-admin", ""):
        return None
    try:
        from db_pool import get_db_ctx
        async with get_db_ctx() as db:
            # db_pool is asyncpg → $1 placeholder + fetchrow (the aiosqlite '?'
            # cursor style fails silently here).
            row = await db.fetchrow("SELECT name FROM users WHERE id = $1", user_id)
        name = ((row["name"] if row else "") or "").strip()
        if name and name.islower():   # "jason" -> "Jason" for natural read-back
            name = name.title()
        return name or None
    except Exception as exc:
        logger.debug("voice identity load failed (non-fatal): %s", exc)
        return None


_VOICE_BRAIN_DOMAIN_CONTEXT = {
    "calendar": ("calendar_show", {"qualifier": "this week"}, "Your calendar this week"),
    "lists": ("list_show", {}, "Your lists"),
    "reminders": ("reminder_list", {}, "Your reminders"),
}


async def _voice_domain_context(router_decision: Optional[dict], user_id: str) -> Optional[str]:
    """When a calendar/lists/reminders turn DEFERS to the brain (ambiguous fragment,
    follow-up), hand the brain that domain's current data so it answers instead of
    saying 'I don't have access to your calendar'. Scoped to those domains only —
    chat/people brain turns (the common case) pay nothing, preserving the fast path.
    Reuses the same read intents the fast path uses. Best-effort / never raises."""
    domain = (router_decision or {}).get("domain")
    spec = _VOICE_BRAIN_DOMAIN_CONTEXT.get(domain or "")
    if not spec:
        return None
    intent_name, slots, label = spec
    try:
        from intent_router import Intent, execute_intent
        summary = (await execute_intent(Intent(intent_name, dict(slots)), user_id) or "").strip()
        return f"[{label}]\n{summary}" if summary else None
    except Exception as exc:
        logger.debug("voice domain context (%s) failed (non-fatal): %s", domain, exc)
        return None


def _merge_brain_context(db_memory: Optional[str], domain_ctx: Optional[str]) -> Optional[str]:
    parts = [p for p in (db_memory, domain_ctx) if p]
    return "\n\n".join(parts) if parts else None


_VOICE_ESCALATION_MARKERS = ("__ESCALATE__:", "__ESCALATE_BG__:", "__ESCALATE_HERMES__:")


def _parse_voice_escalation_delta(delta: str, fallback_prompt: str) -> tuple[bool, str, str]:
    """Return whether the voice escalation should be queued plus reason and task text."""
    _, body = delta.split(":", 1)
    reason, _, oc_task = body.partition("|")
    return delta.startswith("__ESCALATE_BG__:"), reason, (oc_task or fallback_prompt).strip()


# ── Voice text pre-processor ───────────────────────────────────────────────
# Cleans LLM output for TTS synthesis: strips markdown, converts units,
# expands abbreviations.  Run before ANY synthesis call on voice paths.

_ABBREV = {
    r"\bDr\.": "Doctor",
    r"\bMr\.": "Mister",
    r"\bMrs\.": "Missus",
    r"\bMs\.": "Miss",
    r"\bSt\.": "Saint",
    r"\be\.g\.": "for example",
    r"\bi\.e\.": "that is",
    r"\betc\.": "et cetera",
    r"\bvs\.": "versus",
    r"\bApprox\.": "approximately",
}

_UNIT_RE = [
    (re.compile(r"(\d+)\s*°C\b"), r"\1 degrees Celsius"),
    (re.compile(r"(\d+)\s*°F\b"), r"\1 degrees Fahrenheit"),
    (re.compile(r"\$(\d+(?:\.\d{1,2})?)"), r"\1 dollars"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*%"), r"\1 percent"),
    (re.compile(r"(\d{1,2})\s*(am|pm)\b", re.IGNORECASE), r"\1 \2"),
    (re.compile(r"(\d+)\s*km/h\b"), r"\1 kilometres per hour"),
    (re.compile(r"(\d+)\s*mph\b"), r"\1 miles per hour"),
    (re.compile(r"(\d+)\s*kg\b"), r"\1 kilograms"),
    (re.compile(r"(\d+)\s*lbs?\b"), r"\1 pounds"),
]


def _voice_preprocess(text: str) -> str:
    """Strip markdown and normalise text so TTS sounds natural when spoken aloud."""
    if not text:
        return text

    # Remove markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Remove bullet/numbered list markers (keep the content)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove blockquotes
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    # Remove inline code and code blocks (replace with just the content)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove markdown links but keep link text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # Expand abbreviations
    for pattern, replacement in _ABBREV.items():
        text = re.sub(pattern, replacement, text)

    # Convert units and symbols
    for pattern, replacement in _UNIT_RE:
        text = pattern.sub(replacement, text)

    # Collapse multiple blank lines to single break
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


_VOICE_SYSTEM_PROMPT_SUFFIX = (
    "\n\n[VOICE MODE] Respond in 1-2 natural spoken sentences only. "
    "No markdown, no bullet points, no lists, no headers, no code blocks. "
    "Numbers and measurements in spoken form (e.g. '24 degrees' not '24°C'). "
    "Conversational tone. If asked for a list of more than 3 items, give the "
    "first 3 and offer to continue."
)


async def _validate_device_token(x_device_token: str = Header(default="")) -> Optional[dict]:
    """Validate raw X-Device-Token against panel_auth SHA-256 cache."""
    raw = (x_device_token or "").strip()
    if not raw:
        return None
    from routers.panel_auth import lookup_device_token

    info = lookup_device_token(raw)
    if not info:
        return None
    return {
        "panel_id": info.get("panel_id"),
        "user_id": "voice-daemon",
        "role": info.get("role") or "voice-daemon",
        "raw_token": raw,  # Preserved so voice_command can forward it to the chat endpoint
    }


async def _require_voice_auth(
    request: Request,
    device: Optional[dict] = Depends(_validate_device_token),
    user: dict = Depends(get_current_user),
) -> dict:
    """Allow voice endpoints if caller has a valid device token OR is authenticated."""
    if device:
        return {
            "source": "device",
            "panel_id": device.get("panel_id"),
            "user_id": device.get("user_id", "voice-daemon"),
            "raw_token": device.get("raw_token", ""),
        }
    if user.get("role") not in (None, "guest"):
        return {"source": "session", "user_id": user.get("user_id"), "role": user.get("role")}
    raise HTTPException(status_code=401, detail="Voice endpoints require authentication or a device token")

VOICE_PROFILES = {
    "zoe_au_natural_v1": {
        "edge_voice": "en-AU-NatashaNeural",
        "espeak_speed": 158,
        "espeak_pitch": 44,
        "espeak_volume": 150,
    }
}


def _should_supersede_voice_weather_action(row, nav_key: str, card_key: str) -> bool:
    if row["idempotency_key"] in {nav_key, card_key}:
        return False
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    url = str(payload.get("url") or "")
    base = url.split("?", 1)[0]
    # Legacy /touch/weather.html (retired) or the estate equivalent the weather
    # helper now navigates to (/touch/home.html?domain=weather). Anchor the base
    # path before testing the domain param, matching the skybridge superseder.
    nav_matches = base == "/touch/weather.html" or (
        base == "/touch/home.html" and "domain=weather" in url
    )
    return (
        row["action_type"] == "panel_navigate" and nav_matches
    ) or (
        row["action_type"] == "show_card"
        and payload.get("type") == "weather"
    )


def _should_supersede_voice_skybridge_action(row, nav_key: str, card_key: str) -> bool:
    if row["idempotency_key"] in {nav_key, card_key}:
        return False
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    action_type = row["action_type"]
    source = str(payload.get("source") or "").lower()
    if source == "voice:skybridge":
        return True
    if action_type == "panel_navigate":
        url = str(payload.get("url") or "")
        base = url.split("?", 1)[0]
        # Legacy per-domain pages (retired) plus the estate equivalents the voice
        # helpers now navigate to (/touch/home.html?domain=<domain>).
        if base in {
            "/touch/calendar.html",
            "/touch/weather.html",
            "/touch/lists.html",
            "/touch/chat.html",
        }:
            return True
        if base == "/touch/home.html":
            return any(
                f"domain={d}" in url
                for d in ("calendar", "weather", "lists", "chat", "reminders", "person")
            )
    if action_type == "show_card":
        return str(payload.get("type") or "").lower() in {
            "calendar",
            "weather",
            "list",
            "lists",
            "skybridge",
        }
    return False


def _split_sentences(text: str) -> list[str]:
    """Split text into spoken sentences for streaming TTS.

    Uses a regex that avoids splitting on common abbreviations like Dr., Mr., e.g.
    """
    # Protect common abbreviations from being treated as sentence ends.
    _ABBREVS_PROTECT = r"(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bSt)(?<!\be\.g)(?<!\bi\.e)(?<!\betc)"
    pattern = re.compile(_ABBREVS_PROTECT + r"(?<=[.!?])\s+(?=[A-Z])")
    raw = pattern.split(text.strip())
    # Filter empties, strip whitespace
    sentences = [s.strip() for s in raw if s.strip()]
    return sentences or [text.strip()]


def _extract_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Extract finished sentences from a streaming token buffer."""
    if not buffer:
        return [], ""
    complete: list[str] = []
    last_end = 0
    for m in re.finditer(r"(.+?[.!?])(?:\s+|$)", buffer, flags=re.DOTALL):
        sentence = m.group(1).strip()
        if sentence:
            complete.append(sentence)
        last_end = m.end()
    return complete, buffer[last_end:]


_FIRST_UNIT_MIN_CHARS = 12   # don't synthesize a tiny stub like "So,"
_FIRST_UNIT_CLAUSE_MIN = 60  # only clause-break (mid-sentence) once the opening is this long
_FIRST_UNIT_SOFT_CAP = 90    # word-boundary flush by here even without punctuation


def _fast_first_audio_enabled() -> bool:
    return env_bool("ZOE_VOICE_FAST_FIRST_AUDIO", default=True)


# The Pi brain surfaces "what Zoe is doing" as text sentinels riding alongside the
# spoken stream (see zoe_core_client._read_turn). They are NOT speech — the voice
# path must consume them, never synthesize them (otherwise Kokoro reads raw JSON).
_VOICE_TOOL_SENTINEL_PREFIXES = ("__TOOL__:", "__THINKING__:")


def _voice_tool_filler_enabled() -> bool:
    return env_bool("ZOE_VOICE_TOOL_FILLER", default=True)


# Short, generic, spoken acknowledgements keyed by tool. Tool turns can sit silent
# for several seconds while the brain calls a tool and then synthesizes the answer.
# When the brain emits a tool_call BEFORE saying anything itself, we speak ONE of
# these so first-audio comes fast — then the real answer follows unchanged. Kept
# deliberately generic: they never claim a result, so they're always truthful.
_VOICE_TOOL_FILLERS = {
    "calendar": "Let me check your calendar.",
    "lists": "Let me check your lists.",
    "list": "Let me check your list.",
    "reminders": "Let me check your reminders.",
    "reminder": "Let me check your reminders.",
    "memory": "Let me look that up.",
    "weather": "Let me check the weather.",
    "web": "Let me look that up.",
    "search": "Let me look that up.",
}
_VOICE_TOOL_FILLER_DEFAULT = "One sec."


def _voice_tool_filler(tool_name: str) -> str:
    """Pick a short spoken acknowledgement for a brain tool_call.

    Matches the tool name (or a prefix of it, so 'calendar_show' -> 'calendar')
    to a templated line; falls back to a generic 'One sec.' Never embeds the
    result — purely a leading filler so audio starts before the tool returns.
    """
    name = (tool_name or "").strip().lower()
    if name in _VOICE_TOOL_FILLERS:
        return _VOICE_TOOL_FILLERS[name]
    for key, line in _VOICE_TOOL_FILLERS.items():
        if name.startswith(key):
            return line
    return _VOICE_TOOL_FILLER_DEFAULT


def _voice_tool_name_from_sentinel(delta: str) -> "Optional[str]":
    """Extract the tool name from a ``__TOOL__:`` sentinel, or None.

    Only ``phase == 'start'`` sentinels trigger a filler (one per turn); args/result
    phases return None so we never speak twice for the same tool call. Malformed
    sentinels return None rather than raising — a bad frame must not break the turn.
    """
    if not delta.startswith("__TOOL__:"):
        return None
    try:
        import json as _json
        tc = _json.loads(delta[len("__TOOL__:"):])
    except Exception:
        return None
    if not isinstance(tc, dict) or tc.get("phase") != "start":
        return None
    name = tc.get("name")
    return str(name) if name else None


def _voice_activity_frame(delta: str, tool_names: "Optional[dict]" = None) -> "Optional[dict]":
    """Convert a brain ``__TOOL__:`` sentinel into a lightweight activity frame.

    The touch panel's live-activity strip shows "what Zoe is DOING" during brain
    turns from these frames: ``{"type": "activity", "phase": "start"|"result",
    "tool": "<name>"}``. Only the tool NAME and PHASE cross the wire — args and
    result payloads stay server-side (they can carry user data and the strip
    doesn't need them). ``tool_names`` is an optional caller-owned id→name map
    (one per turn) so the result phase — which often omits the name (see
    zoe_core_client._tool_result_sentinel) — resolves to the tool that started.
    Returns None for args phases, ``__THINKING__:`` markers, and malformed
    sentinels: a bad frame must never break the voice turn.
    """
    if not delta.startswith("__TOOL__:"):
        return None
    try:
        import json as _json
        tc = _json.loads(delta[len("__TOOL__:"):])
    except Exception:
        return None
    if not isinstance(tc, dict):
        return None
    phase = tc.get("phase")
    if phase not in ("start", "result"):
        return None
    tc_id = str(tc.get("id") or "")
    name = str(tc.get("name") or "")
    if tool_names is not None and tc_id:
        if phase == "start" and name:
            tool_names[tc_id] = name
        elif not name:
            name = str(tool_names.get(tc_id) or "")
    if phase == "start" and not name:
        return None
    return {"type": "activity", "phase": phase, "tool": name}


async def _forward_voice_activity(delta: str, send_json, tool_names: dict) -> bool:
    """Consume a brain sentinel delta on the ``/ws/voice/`` panel lane.

    Returns True when ``delta`` is a sentinel (``__TOOL__:`` / ``__THINKING__:``)
    — the caller must then skip it entirely (never buffer it toward TTS, where
    Kokoro would read raw JSON aloud). Tool start/result sentinels are
    additionally forwarded to the panel as lightweight ``activity`` frames via
    ``send_json`` (the websocket's send_json). Forwarding is best-effort: a
    send failure never breaks the spoken turn.
    """
    if not delta.startswith(_VOICE_TOOL_SENTINEL_PREFIXES):
        return False
    frame = _voice_activity_frame(delta, tool_names)
    if frame is not None:
        try:
            await send_json(frame)
        except Exception as exc:  # noqa: BLE001 - activity is cosmetic, speech is not
            logger.debug("voice activity frame send failed (non-fatal): %s", exc)
    return True


def _extract_first_unit(buffer: str) -> tuple[Optional[str], str]:
    """Pull the FIRST speakable unit out of a streaming buffer as early as possible.

    Each unit is synthesized as a STANDALONE Kokoro utterance, so every split point
    gets sentence-final prosody + padding — an audible pause and pitch reset. Splitting
    mid-sentence therefore makes a short reply sound broken ("It's currently 22
    degrees," <pause> "mostly clear…"), and matching punctuation at the end of a still-
    streaming buffer even split inside numbers ("The time is 8:" <pause> "05…"). Kokoro
    is now fast on GPU, so we no longer need a sub-sentence break for first-audio on
    short replies. Rules:
      * emit a COMPLETE sentence as soon as one closes (`.!?` + following space) —
        short weather/time replies play as one natural utterance;
      * only clause-break (`,;:—`) once the opening is long (`_FIRST_UNIT_CLAUSE_MIN`),
        so a genuine paragraph still starts fast;
      * word-boundary flush by the soft cap as the last resort.
    Every boundary requires a FOLLOWING SPACE, so a comma/period/colon inside a partial
    token or a number ("8:05", "12.4", "22,") can never trigger a split — the trailing
    unpunctuated remainder is emitted by the stream loop's end-of-turn flush.
    Returns (unit|None, remainder)."""
    stripped = buffer.lstrip()
    if len(stripped) < _FIRST_UNIT_MIN_CHARS:
        return None, buffer
    # 1. A complete sentence closed → emit it whole (never sub-split a short reply).
    m = re.search(r"(.{%d,}?[.!?])\s" % _FIRST_UNIT_MIN_CHARS, buffer)
    if m:
        return m.group(1).strip(), buffer[m.end():]
    # 2. Long opening with no sentence end yet → clause-break to keep first-audio
    #    snappy. The boundary itself must be at least _FIRST_UNIT_CLAUSE_MIN chars in
    #    (not just the buffer), so a short sentence with an early comma is never split
    #    ("It's 22 degrees, mostly clear…" stays whole) — only a genuinely long opening
    #    with a late clause boundary breaks early.
    if len(buffer) >= _FIRST_UNIT_CLAUSE_MIN:
        m = re.search(r"(.{%d,}?[,;:—–])\s" % _FIRST_UNIT_CLAUSE_MIN, buffer)
        if m:
            return m.group(1).strip(), buffer[m.end():]
    # 3. Very long opening, still no punctuation → flush at a word boundary.
    if len(buffer) >= _FIRST_UNIT_SOFT_CAP:
        cut = buffer.rfind(" ", _FIRST_UNIT_MIN_CHARS, _FIRST_UNIT_SOFT_CAP)
        if cut > 0:
            return buffer[:cut].strip(), buffer[cut:]
    return None, buffer


def _skybridge_only() -> bool:
    """When set, voice never navigates the panel to legacy domain pages.

    The Skybridge-first path (`_broadcast_skybridge_ui`) still renders real cards.
    The legacy `_broadcast_*_ui` helpers below navigate to per-domain pages
    (`/touch/weather.html`, etc.), which yanks the panel off Skybridge whenever the
    Skybridge resolver falls through. Gating them keeps the panel on Skybridge.
    """
    return env_bool("ZOE_SKYBRIDGE_ONLY", default=False)


def _status_card(title: str, summary: str) -> dict:
    """A panel-renderable card for the voice domain summaries.

    The panel's renderSkybridgeCardPayload only renders a card when the payload
    carries `card`/`cards` (the {type,data} shape alone falls back to plain text).
    A `status` card with NO specialized `source` renders a safe generic title+body
    card (a `source` like weather_current would call renderWeather, which needs
    structured props we don't have here and would render blank).
    """
    return {"component": "status", "props": {"title": title, "body": (summary or "")[:300], "level": "info"}}


async def _broadcast_weather_ui(
    panel_id: str,
    summary: str = "Fetching weather...",
    turn_key: Optional[str] = None,
) -> None:
    """Mirror chat weather navigation on voice path with durable delivery."""
    if _skybridge_only():
        return
    delivery_key = turn_key or str(time.monotonic_ns())
    nav_action = {
        "id": f"voice_weather_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            # Estate is the sole kiosk: land on home.html and let the estate open
            # the weather surface (DOMAIN_SCREEN['weather']). ?say= shows the spoken
            # summary in the dock, mirroring _broadcast_skybridge_ui.
            "url": "/touch/home.html?domain=weather" + (
                f"&say={quote_plus(str(summary or '')[:300])}" if summary else ""
            ),
            "label": "Opening weather",
            "panel_id": panel_id,
        },
    }
    _wcard = _status_card("Weather", summary)
    card_action = {
        "id": f"voice_weather_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "weather",
            "data": {"summary": summary[:200]},
            "card": _wcard,
            "cards": [_wcard],
            "panel_id": panel_id,
        },
    }
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            nav_key = f"voice_weather_nav_{panel_id}_{delivery_key}"
            card_key = f"voice_weather_card_{panel_id}_{delivery_key}"
            async with _db.transaction():
                await _enqueue_ui_action(
                    _db,
                    user_id=_panel_user_id,
                    panel_id=panel_id,
                    action_type="panel_navigate",
                    payload=nav_action["payload"],
                    requested_by="voice",
                    idempotency_key=nav_key,
                    commit=False,
                    broadcast=False,
                )
                await _enqueue_ui_action(
                    _db,
                    user_id=_panel_user_id,
                    panel_id=panel_id,
                    action_type="show_card",
                    payload=card_action["payload"],
                    requested_by="voice",
                    idempotency_key=card_key,
                    commit=False,
                    broadcast=False,
                )
                _cur = await _db.execute(
                    """SELECT id, action_type, payload, idempotency_key
                       FROM ui_actions
                       WHERE panel_id = ?
                         AND requested_by = 'voice'
                         AND status IN ('queued', 'running')""",
                    (panel_id,),
                )
                _rows = await _cur.fetchall()
                _superseded_at = datetime.now(timezone.utc).isoformat()
                for _row in _rows:
                    if not _should_supersede_voice_weather_action(_row, nav_key, card_key):
                        continue
                    await _db.execute(
                        """UPDATE ui_actions
                           SET status = 'skipped',
                               error_code = 'superseded',
                               error_message = 'Superseded by newer voice weather request',
                               updated_at = ?
                           WHERE id = ?""",
                        (_superseded_at, _row["id"]),
                    )
            try:
                from push import broadcaster
                await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
                await broadcaster.broadcast("all", "ui_action", {"action": card_action})
            except Exception as exc:
                logger.debug("voice weather ui broadcast failed (non-fatal): %s", exc)
            break
    except Exception as exc:
        logger.warning(
            "voice weather ui_actions enqueue failed for panel=%s — weather card/navigate dropped (non-fatal): %s",
            panel_id, exc,
        )


async def _broadcast_calendar_ui(
    panel_id: str,
    summary: str = "Opening your calendar...",
    turn_key: Optional[str] = None,
) -> None:
    """Mirror calendar intents on voice path with durable delivery."""
    if _skybridge_only():
        return
    delivery_key = turn_key or str(time.monotonic_ns())
    nav_action = {
        "id": f"voice_calendar_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            # Estate is the sole kiosk: land on home.html; the estate opens the
            # calendar surface (DOMAIN_SCREEN['calendar'] -> 'day').
            "url": "/touch/home.html?domain=calendar" + (
                f"&say={quote_plus(str(summary or '')[:300])}" if summary else ""
            ),
            "label": "Opening calendar",
            "panel_id": panel_id,
        },
    }
    _ccard = _status_card("Calendar", summary)
    card_action = {
        "id": f"voice_calendar_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "calendar",
            "data": {"summary": summary[:200]},
            "card": _ccard,
            "cards": [_ccard],
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        await broadcaster.broadcast("all", "ui_action", {"action": card_action})
    except Exception as exc:
        logger.debug("voice calendar ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_calendar_nav_{panel_id}_{delivery_key}",
            )
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload=card_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_calendar_card_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice calendar ui enqueue failed (non-fatal): %s", exc)


async def _broadcast_skybridge_ui(
    panel_id: str,
    skybridge_result: dict,
    *,
    utterance: str = "",
    turn_key: Optional[str] = None,
) -> None:
    """Display a Skybridge result on the touch panel without routing to a domain page."""
    if not skybridge_result or not skybridge_result.get("handled"):
        return
    delivery_key = turn_key or str(time.monotonic_ns())
    # Voice lands on the ESTATE home with display-only params (heard/say/domain):
    # the estate shows the transcript + summary and opens the matching screen.
    # It must NOT re-execute the command (?q= would double-run mutations).
    query = quote_plus(str(utterance or "")[:200])
    say = quote_plus(str(skybridge_result.get("spoken_summary") or "")[:300])
    domain = quote_plus(str((skybridge_result.get("intent") or {}).get("domain") or ""))
    params = "&".join(p for p in (f"heard={query}" if query else "", f"say={say}" if say else "", f"domain={domain}" if domain else "") if p)
    url = "/touch/home.html" + (f"?{params}" if params else "")
    cards = skybridge_result.get("cards") if isinstance(skybridge_result.get("cards"), list) else []
    summary = str(skybridge_result.get("spoken_summary") or "Showing this in Skybridge.")
    nav_payload = {
        "url": url,
        "label": "Showing on the panel",
        "panel_id": panel_id,
        "source": "voice:skybridge",
    }
    card_payload = {
        "type": "skybridge",
        "data": {"summary": summary[:300]},
        "card": cards[0] if cards else None,
        "cards": cards,
        "panel_id": panel_id,
        "source": "voice:skybridge",
    }
    try:
        from database import get_db as _get_db
        from push import broadcaster
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            nav_message = await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_payload,
                requested_by="voice",
                idempotency_key=f"voice_skybridge_nav_{panel_id}_{delivery_key}",
                broadcast=False,
                commit=False,
            )
            card_message = await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload={**card_payload, "result": skybridge_result},
                requested_by="voice",
                idempotency_key=f"voice_skybridge_card_{panel_id}_{delivery_key}",
                broadcast=False,
                commit=False,
            )
            nav_key = f"voice_skybridge_nav_{panel_id}_{delivery_key}"
            card_key = f"voice_skybridge_card_{panel_id}_{delivery_key}"
            try:
                _cur = await _db.execute(
                    """SELECT id, action_type, payload, idempotency_key
                       FROM ui_actions
                       WHERE panel_id = ?
                         AND user_id = ?
                         AND requested_by = 'voice'
                         AND status IN ('queued', 'running')""",
                    (panel_id, _panel_user_id),
                )
                _rows = await _cur.fetchall()
                _superseded_at = datetime.now(timezone.utc).isoformat()
                for _row in _rows:
                    if not _should_supersede_voice_skybridge_action(_row, nav_key, card_key):
                        continue
                    await _db.execute(
                        """UPDATE ui_actions
                           SET status = 'skipped',
                               error_code = 'superseded',
                               error_message = 'Superseded by newer Skybridge voice request',
                               updated_at = ?
                           WHERE id = ?""",
                        (_superseded_at, _row["id"]),
                    )
            except Exception as _sup_exc:
                logger.warning(
                    "voice skybridge stale ui_actions supersede UPDATE failed for panel=%s user=%s — old queued actions may replay (non-fatal): %s",
                    panel_id, _panel_user_id, _sup_exc,
                )
            await _db.commit()
            nav_delivered = await broadcaster.broadcast_to_panel(
                panel_id,
                "ui_action",
                {**nav_message, "payload": nav_payload},
            )
            # Keep the card queued. Navigating the old Skybridge page immediately
            # can unload the socket before a following show_card frame is handled;
            # the freshly loaded page will poll /api/ui/actions/pending and render it.
            if nav_delivered:
                await _db.execute(
                    """UPDATE ui_actions
                       SET status = 'success',
                           error_code = NULL,
                           error_message = 'Delivered over panel push',
                           updated_at = NOW(),
                           acked_at = NOW()
                       WHERE id = ?""",
                    (nav_message["action_id"],),
                )
                await _db.commit()
            break
    except Exception as exc:
        logger.warning(
            "voice skybridge ui_actions enqueue failed for panel=%s — skybridge card/navigate dropped (non-fatal): %s",
            panel_id, exc,
        )


async def _broadcast_lets_talk_ui(panel_id: str, turn_key: Optional[str] = None) -> None:
    """Navigate the touch panel browser to voice conversation mode.

    Navigation target is /touch/voice.html (no ?conv=1) so the executor's
    same-URL check always fires when already on the voice page, preventing
    re-navigation loops when the queued action isn't acked in time.

    The auto-start listening signal is sent as a separate transient push event
    (voice:start_conversation) which the voice page handles directly — it is
    never enqueued in the DB, so it cannot create a replay loop.
    """
    if _skybridge_only():
        return
    delivery_key = turn_key or str(time.monotonic_ns())
    # Navigation: no ?conv=1 so the action is skipped when panel is already on voice page.
    nav_action = {
        "id": f"voice_letstalk_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            "url": "/touch/voice.html",
            "label": "Opening voice",
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        # Broadcast nav action (handled by touch-ui-executor).
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        # Also broadcast a transient start-conversation signal — NOT enqueued in DB.
        # The voice page handles this to begin auto-listening after a short delay.
        await broadcaster.broadcast("all", "voice:start_conversation", {
            "panel_id": panel_id,
            "delay_ms": 2500,   # wait for TTS echo to settle before opening mic
        })
    except Exception as exc:
        logger.debug("voice lets_talk ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_letstalk_nav_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice lets_talk ui enqueue failed (non-fatal): %s", exc)


def _parse_voice_form_field(text: str, panel_type: str) -> dict:
    """Extract field updates from a voice utterance directed at the active action form.

    Returns a dict of {field_name: value} pairs, or {} if no match found.
    For shopping_list, field_name may be "add" or "remove" (list ops).
    """
    import re as _re
    t = text.lower().strip()
    result: dict = {}

    if panel_type == "shopping_list":
        # "add <item>", "put <item> on the list", "we also need <item>"
        m = _re.match(
            r"^(?:add|put|we need|also need|get|buy)\s+(.+?)(?:\s+(?:to|on)(?:\s+(?:the|my)\s+)?(?:list|shopping list))?$",
            t,
        )
        if m:
            result["add"] = m.group(1).strip().rstrip(".,;")
            return result
        # "remove <item>", "cross off <item>", "we got <item>"
        m = _re.match(
            r"^(?:remove|delete|take off|cross off|we got|got|we have)\s+(.+?)(?:\s+(?:from|off)(?:\s+(?:the|my)\s+)?(?:list))?$",
            t,
        )
        if m:
            result["remove"] = m.group(1).strip().rstrip(".,;")
            return result
        return result

    if panel_type == "calendar_event":
        # Title: "the title is X", "call it X", "name it X", "event title X"
        m = _re.match(r"^(?:the\s+)?(?:title|name|event)\s+(?:is\s+|should be\s+)?(?:called?\s+)?(.+)$", t)
        if m:
            result["title"] = m.group(1).strip().rstrip(".,;")
            return result
        m = _re.match(r"^(?:call|name)\s+it\s+(.+)$", t)
        if m:
            result["title"] = m.group(1).strip().rstrip(".,;")
            return result
        # Date: "on <date>", "the date is <date>", "change the date to <date>"
        m = _re.match(r"^(?:on|the\s+date\s+is|date\s+is|change(?:\s+the)?\s+date\s+to|set(?:\s+the)?\s+date\s+to)\s+(.+)$", t)
        if m:
            from intent_router import _parse_date as _pd
            _raw_date = m.group(1).strip().rstrip(".,;")
            _parsed = _pd(_raw_date)
            if _parsed:
                result["date"] = _parsed
                return result
        # Time: "at <time>", "the time is <time>", "change the time to <time>"
        m = _re.match(r"^(?:at|the\s+time\s+is|time\s+is|change(?:\s+the)?\s+time\s+to|set(?:\s+the)?\s+time\s+to)\s+(.+)$", t)
        if m:
            from intent_router import _parse_time as _pt
            _raw_time = m.group(1).strip().rstrip(".,;")
            _parsed_t = _pt(_raw_time)
            if _parsed_t:
                result["time"] = _parsed_t
                return result
        # Location: "at <location>", "the location is X", "in <location>"
        m = _re.match(r"^(?:(?:(?:the\s+)?location\s+(?:is|should be)|located?\s+at)\s+)(.+)$", t)
        if m:
            result["location"] = m.group(1).strip().rstrip(".,;")
            return result
        # Notes: "add a note X", "notes say X"
        m = _re.match(r"^(?:(?:add\s+a?\s+)?note[s]?\s+(?:saying|says?|is|:)\s+|add\s+notes?\s+)(.+)$", t)
        if m:
            result["notes"] = m.group(1).strip().rstrip(".,;")
            return result
        # Duration: "for <N> hours/minutes"
        m = _re.match(r"^(?:for|duration\s+is|lasts?)\s+(\d+\s*(?:hour|hr|minute|min)s?)$", t)
        if m:
            result["duration"] = m.group(1).strip()
            return result
        return result

    return result


async def _broadcast_action_form_panel(
    panel_id: str,
    panel_type: str,
    data: dict,
    title: str = "",
    turn_key: Optional[str] = None,
) -> None:
    """Broadcast a panel_show_action_form event so the touch overlay opens.

    panel_type: "calendar_event" | "shopping_list"
    data: pre-fill data dict (slots already parsed)
    """
    from panel_form_state import set_active_form
    set_active_form(panel_id, panel_type, data)

    delivery_key = turn_key or str(time.monotonic_ns())
    form_action = {
        "id": f"voice_action_form_{panel_id}_{delivery_key}",
        "action_type": "panel_show_action_form",
        "payload": {
            "panel_type": panel_type,
            "title": title,
            "data": data,
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": form_action})
    except Exception as exc:
        logger.debug("_broadcast_action_form_panel broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_show_action_form",
                payload=form_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_action_form_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("_broadcast_action_form_panel enqueue failed (non-fatal): %s", exc)


async def _broadcast_reminder_ui(
    panel_id: str,
    summary: str,
    turn_key: Optional[str] = None,
) -> None:
    if _skybridge_only():
        return
    delivery_key = turn_key or str(time.monotonic_ns())
    nav_action = {
        "id": f"voice_reminder_nav_{panel_id}_{delivery_key}",
        "action_type": "panel_navigate",
        "payload": {
            # Estate is the sole kiosk: land on home.html; the estate opens the
            # reminder surface (DOMAIN_SCREEN['reminders'] -> 'reminder').
            "url": "/touch/home.html?domain=reminders" + (
                f"&say={quote_plus(str(summary or '')[:300])}" if summary else ""
            ),
            "label": "Opening reminders",
            "panel_id": panel_id,
        },
    }
    _rcard = _status_card("Reminder", summary)
    card_action = {
        "id": f"voice_reminder_card_{panel_id}_{delivery_key}",
        "action_type": "show_card",
        "payload": {
            "type": "reminder",
            "data": {"summary": summary[:200]},
            "card": _rcard,
            "cards": [_rcard],
            "panel_id": panel_id,
        },
    }
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "ui_action", {"action": nav_action})
        await broadcaster.broadcast("all", "ui_action", {"action": card_action})
    except Exception as exc:
        logger.debug("voice reminder ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_navigate",
                payload=nav_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_reminder_nav_{panel_id}_{delivery_key}",
            )
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="show_card",
                payload=card_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_reminder_card_{panel_id}_{delivery_key}",
            )
            break
    except Exception as exc:
        logger.debug("voice reminder ui enqueue failed (non-fatal): %s", exc)


async def _request_auth_ui(panel_id: str, challenge_id: str, reason: str) -> bool:
    """Request panel auth UI via both websocket broadcast and durable queue."""
    if not challenge_id:
        return False
    auth_action = {
        "id": f"voice_auth_{panel_id}_{challenge_id}",
        "action_type": "panel_request_auth",
        "payload": {
            "panel_id": panel_id,
            "challenge_id": challenge_id,
            "action_context": reason,
            "summary": "Please authenticate on the touch panel to continue.",
            "title": "Confirm it is you",
            "message": "Zoe needs to know who is speaking before showing or changing personal data.",
            "domain": "Private data",
            "intent_action": "continue",
            "cta": "Continue",
        },
    }
    delivered = False
    try:
        from push import broadcaster as _bc_auth
        await _bc_auth.broadcast("all", "ui_action", {"action": auth_action})
        delivered = True
    except Exception as exc:
        logger.debug("voice auth ui broadcast failed (non-fatal): %s", exc)
    try:
        from database import get_db as _get_db
        from ui_orchestrator import enqueue_ui_action as _enqueue_ui_action
        async for _db in _get_db():
            _panel_user_id = "family-admin"
            try:
                _cur = await _db.execute(
                    "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                    (panel_id,),
                )
                _row = await _cur.fetchone()
                if _row and _row["user_id"]:
                    _panel_user_id = str(_row["user_id"])
            except Exception:
                pass
            await _enqueue_ui_action(
                _db,
                user_id=_panel_user_id,
                panel_id=panel_id,
                action_type="panel_request_auth",
                payload=auth_action["payload"],
                requested_by="voice",
                idempotency_key=f"voice_auth_{panel_id}_{challenge_id}",
            )
            delivered = True
            break
    except Exception as exc:
        logger.warning("voice auth ui enqueue failed: %s", exc)
    return delivered


async def _request_voice_identity_challenge(
    *,
    panel_id: str,
    text: str,
    session_id: str,
    caller: dict | None,
    reason: str = "Voice command needs identity. Enter your PIN to continue.",
) -> dict:
    """Hold a voice turn and ask the touch panel to identify the speaker."""
    import uuid as _uuid_mod

    _pending_id = _uuid_mod.uuid4().hex
    _PENDING_VOICE_IDENT[panel_id] = {
        "pending_id": _pending_id,
        "transcript": text,
        "session_id": session_id,
        "expire_at": time.monotonic() + 120,
    }
    _pin_phrase = "Please authenticate on the touch panel. Choose your profile and enter your PIN to continue."
    _pin_b64 = None
    _pin_content_type = "audio/wav"
    try:
        _pin_audio_resp = await synthesize({"text": _pin_phrase}, caller=caller)
        _pin_b64 = base64.b64encode(_pin_audio_resp.body).decode("ascii")
        _pin_content_type = _pin_audio_resp.media_type
    except Exception as _tts_exc:
        logger.warning("voice/command auth challenge TTS failed: %s", _tts_exc)
    _challenge_id = None
    try:
        from routers.panel_auth import create_pin_challenge_internal
        from database import get_db as _get_db
        async for _db in _get_db():
            _challenge = await create_pin_challenge_internal(
                panel_id=panel_id,
                user_id=None,
                action_context={
                    "kind": "voice_turn",
                    "pending_id": _pending_id,
                    "panel_id": panel_id,
                    "pending_transcript": text,
                    "pending_session_id": session_id,
                },
                db=_db,
            )
            _challenge_id = (_challenge or {}).get("challenge_id")
            break
    except Exception as _challenge_exc:
        logger.warning("voice/command PIN challenge failed: %s", _challenge_exc)
    if not _challenge_id:
        _fail_phrase = "I could not open the authentication screen just now. Please open the touch login page and try again."
        _fail_audio = await synthesize({"text": _fail_phrase}, caller=caller)
        return {
            "ok": True,
            "panel_id": panel_id,
            "reply": _fail_phrase,
            "status": "auth_unavailable",
            "audio_base64": base64.b64encode(_fail_audio.body).decode("ascii"),
            "content_type": _fail_audio.media_type,
        }

    _requested = await _request_auth_ui(
        panel_id=panel_id,
        challenge_id=_challenge_id,
        reason=reason,
    )
    if not _requested:
        logger.warning(
            "voice/command auth challenge created but panel auth action was not delivered: panel=%s challenge=%s",
            panel_id,
            _challenge_id,
        )
    try:
        from voice_metrics import voice_turn_count
        voice_turn_count.labels(outcome="ok", path="awaiting_pin").inc()
    except Exception:
        pass
    try:
        from push import broadcaster as _bc_auth_state
        await _bc_auth_state.broadcast("all", "voice:responding", {"panel_id": panel_id, "text": _pin_phrase[:200]})
        await _bc_auth_state.broadcast("all", "voice:done", {"panel_id": panel_id})
    except Exception:
        pass
    return {
        "ok": True,
        "panel_id": panel_id,
        "reply": _pin_phrase,
        "status": "awaiting_pin",
        "audio_base64": _pin_b64,
        "content_type": _pin_content_type,
    }


async def _resolve_panel_default_user(panel_id: str, db) -> Optional[str]:
    """Best-effort panel user resolution for voice fallback identity."""
    try:
        cur = await db.execute(
            "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
            (panel_id,),
        )
        row = await cur.fetchone()
        if row and row["user_id"]:
            return str(row["user_id"])
    except Exception:
        pass
    try:
        cur = await db.execute(
            "SELECT user_id FROM panel_user_bindings WHERE panel_id = ? AND binding_type = 'default' LIMIT 1",
            (panel_id,),
        )
        row = await cur.fetchone()
        if row and row["user_id"]:
            return str(row["user_id"])
    except Exception:
        pass
    return None


async def _touch_panel_session(panel_id: str, user_id: str) -> None:
    """Refresh the panel's ui_panel_sessions heartbeat for user_id (last_seen_at=NOW()).

    The session row is otherwise only written on touch login / UI actions, so a
    VOICE-only session silently expired after the trust window (~15 min) even while
    the user was still talking — dropping their identity to guest and blocking
    user-scoped actions. Calling this on each voice turn keeps an actively-used
    login alive (it only lapses after real inactivity). Best-effort on a dedicated
    connection (the request db is released on the detached streaming path); never
    raises. Does NOT create a session for guest/None.
    """
    if not panel_id or not user_id or user_id in _GUEST_SENTINEL_USERS:
        return
    try:
        from db_pool import get_db_ctx as _get_db_ctx
        async with _get_db_ctx() as _conn:
            await _conn.execute(
                """INSERT INTO ui_panel_sessions (panel_id, user_id, last_seen_at, updated_at)
                   VALUES (?, ?, NOW(), NOW())
                   ON CONFLICT(panel_id) DO UPDATE SET
                     user_id=excluded.user_id,
                     last_seen_at=NOW(),
                     updated_at=NOW()""",
                (panel_id, user_id),
            )
            await _conn.commit()
    except Exception as exc:
        logger.warning(
            "voice: ui_panel_sessions heartbeat UPSERT failed for panel=%s user=%s — session may lapse to guest (non-fatal): %s",
            panel_id, user_id, exc,
        )


_PANEL_IDLE_LOGOUT_KEY = "panel_idle_logout_s"
_PANEL_IDLE_MAX_S = 24 * 60 * 60
_panel_idle_cache: dict = {"value": None, "expires": 0.0}


def _clamp_idle_s(value: int) -> int:
    return max(0, min(int(value), _PANEL_IDLE_MAX_S))


def _panel_session_trust_window_s() -> int:
    """Env/default fallback for the idle-logout window (used when nothing is
    persisted in app_settings). The panel setting overrides this — see
    _panel_idle_logout_s()."""
    raw = str(os.environ.get("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "900")).strip()
    try:
        return _clamp_idle_s(int(raw))
    except Exception:
        return 900


async def _read_persisted_idle_logout_s() -> Optional[int]:
    """Panel idle-logout window persisted in system_preferences (settable from the
    panel settings screen) or None; never raises (fail-open to the env default)."""
    try:
        from database import get_db_ctx
        async with get_db_ctx() as db:
            cur = await db.execute(
                "SELECT value FROM system_preferences WHERE key = ?", (_PANEL_IDLE_LOGOUT_KEY,)
            )
            row = await cur.fetchone()
        if row and str(row["value"]).strip():
            return _clamp_idle_s(int(str(row["value"]).strip()))
    except Exception as exc:
        logger.debug("panel idle-logout read failed (fail-open to env): %s", exc)
    return None


async def _panel_idle_logout_s() -> int:
    """Effective idle-logout window: persisted panel setting → env → default,
    cached in-process (30s) so it isn't a DB read on every voice turn."""
    import time as _t
    if _panel_idle_cache["value"] is not None and _t.monotonic() < _panel_idle_cache["expires"]:
        return _panel_idle_cache["value"]
    persisted = await _read_persisted_idle_logout_s()
    value = persisted if persisted is not None else _panel_session_trust_window_s()
    _panel_idle_cache["value"] = value
    _panel_idle_cache["expires"] = _t.monotonic() + 30.0
    return value


async def _set_panel_idle_logout_s(seconds: int, updated_by: str = "panel-settings") -> int:
    """Persist the panel idle-logout window to system_preferences; invalidate cache."""
    seconds = _clamp_idle_s(seconds)
    from database import get_db_ctx
    async with get_db_ctx() as db:
        await db.execute(
            """INSERT INTO system_preferences (key, value, updated_by, updated_at)
               VALUES (?, ?, ?, NOW()::text)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                 updated_by = excluded.updated_by, updated_at = NOW()::text""",
            (_PANEL_IDLE_LOGOUT_KEY, str(seconds), updated_by),
        )
        await db.commit()
    _panel_idle_cache["value"] = None
    return seconds


async def _resolve_recent_panel_session_user(panel_id: str, db) -> Optional[str]:
    """
    Resolve panel user only when the panel session heartbeat is fresh enough
    to be considered actively authenticated.
    """
    trust_window_s = await _panel_idle_logout_s()
    if trust_window_s <= 0:
        return None
    try:
        cur = await db.execute(
            "SELECT user_id, last_seen_at FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
            (panel_id,),
        )
        row = await cur.fetchone()
        if not row or not row["user_id"] or not row["last_seen_at"]:
            return None

        raw_last_seen = str(row["last_seen_at"]).strip()
        # Normalise timezone suffixes before parsing:
        # "+00" (PostgreSQL shorthand) → "+00:00" required by fromisoformat.
        # Also handle "Z" → "+00:00".
        import re as _re
        normalised = _re.sub(r"([+-]\d{2})$", r"\1:00", raw_last_seen.replace("Z", "+00:00"))
        parsed: Optional[datetime] = None
        try:
            parsed = datetime.fromisoformat(normalised)
        except Exception:
            try:
                parsed = datetime.strptime(raw_last_seen, "%Y-%m-%d %H:%M:%S")
            except Exception:
                logger.debug("voice scope trust: unsupported last_seen_at format: %s", raw_last_seen)
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        age_s = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        if age_s <= trust_window_s:
            return str(row["user_id"])
    except Exception:
        pass
    return None


def _contains_decision_keyword(text: str, keywords: frozenset[str]) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return False
    for kw in keywords:
        phrase = re.sub(r"\s+", r"\\s+", re.escape(kw.strip().lower()))
        if re.search(rf"(?<![a-z0-9]){phrase}(?![a-z0-9])", normalized):
            return True
    return False


def _cap_voice_list_show_reply(text: str, *, max_items: int = 5) -> str:
    """Keep voice list reads short while leaving non-voice list responses untouched."""
    if max_items <= 0:
        return text
    lines = (text or "").splitlines()
    if not lines:
        return text
    capped: list[str] = []
    item_count = 0
    omitted = 0
    def flush_omitted() -> None:
        nonlocal omitted
        if omitted:
            capped.append(f"And {omitted} more.")
            omitted = 0

    for line in lines:
        if line.lstrip().startswith("- "):
            item_count += 1
            if item_count > max_items:
                omitted += 1
                continue
        elif item_count:
            flush_omitted()
            item_count = 0
        capped.append(line)
    flush_omitted()
    return "\n".join(capped)


def _should_handoff_calendar(user_text: str, reply_text: str, intent_name: Optional[str] = None) -> bool:
    if intent_name in {"calendar_show", "daily_briefing", "calendar_create"}:
        return True
    u = (user_text or "").lower()
    r = (reply_text or "").lower()
    user_hint = any(k in u for k in ("calendar", "schedule", "weekly", "week plan"))
    reply_hint = any(k in r for k in ("calendar", "schedule", "this week", "upcoming"))
    return user_hint and reply_hint


@router.post("/synthesize")
async def synthesize(payload: dict, caller: dict = Depends(_require_voice_auth)):
    text = (payload or {}).get("text", "")
    if not text or not str(text).strip():
        raise HTTPException(status_code=400, detail="text is required")

    # Pre-process: strip markdown and normalise units so TTS sounds natural.
    text = _clean_for_speech(_voice_preprocess(str(text).strip()))[:1200]
    mode = os.getenv("ZOE_TTS_MODE", "hybrid").lower()
    profile = str((payload or {}).get("profile") or os.getenv("ZOE_VOICE_PROFILE", "zoe_au_natural_v1"))
    prof = VOICE_PROFILES.get(profile, VOICE_PROFILES["zoe_au_natural_v1"])
    edge_voice = str((payload or {}).get("edge_voice") or os.getenv("ZOE_EDGE_TTS_VOICE", prof["edge_voice"]))
    speed = int(os.getenv("ZOE_ESPEAK_SPEED", str(prof["espeak_speed"])))
    pitch = int(os.getenv("ZOE_ESPEAK_PITCH", str(prof["espeak_pitch"])))
    volume = int(os.getenv("ZOE_ESPEAK_VOLUME", str(prof["espeak_volume"])))
    local_tts_url = os.getenv("ZOE_LOCAL_TTS_URL", "")

    audio_bytes = None
    content_type = "audio/wav"
    provider = "none"

    # ── TTS waterfall: Kokoro sidecar → Kokoro ONNX → local sidecar → Edge TTS → espeak-ng ──
    # Kokoro sidecar — GPU-accelerated natural af_sky voice (~150ms warm on Jetson).
    if mode != "cloud":
        audio_bytes = await _synthesize_kokoro_sidecar(text)
        if audio_bytes:
            provider = "kokoro-sidecar"
            content_type = "audio/wav"

    # Kokoro ONNX — offline fallback (CPU ~1.1s on Jetson, fast if CUDA available).
    if audio_bytes is None and mode != "cloud":
        audio_bytes = await _synthesize_kokoro(text)
        if audio_bytes:
            provider = "kokoro-onnx"
            content_type = "audio/wav"

    if audio_bytes is None and mode in {"hybrid", "local"}:
        audio_bytes = await _synthesize_local_service(text, profile=profile, base_url=local_tts_url)
        if audio_bytes:
            provider = "local-tts"
            if not audio_bytes.startswith(b"RIFF"):
                content_type = "audio/mpeg"

    if audio_bytes is None and mode in {"hybrid", "cloud", "edge"}:
        try:
            audio_bytes = await _synthesize_edge_tts(text, edge_voice)
            if audio_bytes:
                provider = "edge-tts"
                if not audio_bytes.startswith(b"RIFF"):
                    content_type = "audio/mpeg"
        except Exception:
            audio_bytes = None

    if audio_bytes is None and mode != "cloud":
        try:
            audio_bytes = await _synthesize_espeak(text, speed=speed, pitch=pitch, volume=volume)
            provider = "espeak-ng"
            content_type = "audio/wav"
        except Exception:
            audio_bytes = None

    if audio_bytes is None:
        raise HTTPException(status_code=503, detail="No speech provider available")

    headers = {"X-Zoe-TTS-Provider": provider}
    return Response(content=audio_bytes, media_type=content_type, headers=headers)


@router.post("/speak")
async def speak(payload: dict, caller: dict = Depends(_require_voice_auth)):
    response = await synthesize(payload, caller=caller)
    b64 = base64.b64encode(response.body).decode("ascii")
    return {
        "ok": True,
        "provider": response.headers.get("X-Zoe-TTS-Provider", "unknown"),
        "content_type": response.media_type,
        "audio_base64": b64,
    }


@router.get("/announcements")
async def voice_announcements(caller: dict = Depends(_require_voice_auth), db=Depends(get_db)):
    """P-W2.3: claim-and-return pending spoken announcements for the voice daemon.

    Device-token ONLY: `_require_voice_auth` already rejects guests, but a
    non-guest browser session must not be able to drain the speaker queue
    either — the daemon (the proven audio path) is the sole consumer, and it
    authenticates with the panel device token. Claims are atomic server-side
    (voice_announce.claim_announcements), so overlapping polls never
    double-speak; TTL-expired rows are marked expired and never returned.
    """
    if caller.get("source") != "device":
        raise HTTPException(status_code=403, detail="Announcement claim requires a device token")
    import voice_announce

    items = await voice_announce.claim_announcements(
        db, panel_id=str(caller.get("panel_id") or "")
    )
    return {"ok": True, "announcements": items}


_STREAM_TEXT_MAX = 2000  # character cap for streaming TTS to prevent runaway requests


@router.post("/stream")
async def voice_stream(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Streaming TTS endpoint: sentence-splits text and streams WAV chunks.

    Pi daemon plays each chunk as it arrives, so first audio starts within ~1.2s.
    Request: { "text": "...", "profile": "zoe_au_natural_v1" }
    Response: application/x-zoe-audio-stream — each chunk is a JSON header line
              followed by a base64-encoded WAV audio block.  Failed chunks yield
              an error JSON line (no audio block) so the client can skip silently.
    """
    from fastapi.responses import StreamingResponse as _StreamingResponse
    import json as _json

    raw_text = str((payload or {}).get("text", "")).strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="text is required")

    text = _voice_preprocess(raw_text)[:_STREAM_TEXT_MAX]

    mode = os.getenv("ZOE_TTS_MODE", "hybrid").lower()
    profile = str((payload or {}).get("profile") or os.getenv("ZOE_VOICE_PROFILE", "zoe_au_natural_v1"))
    prof = VOICE_PROFILES.get(profile, VOICE_PROFILES["zoe_au_natural_v1"])
    edge_voice = str((payload or {}).get("edge_voice") or os.getenv("ZOE_EDGE_TTS_VOICE", prof["edge_voice"]))
    local_tts_url = os.getenv("ZOE_LOCAL_TTS_URL", "")

    sentences = _split_sentences(text)

    async def _generate_chunks():
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue
            audio_bytes: Optional[bytes] = None
            provider = "none"
            error_msg: Optional[str] = None

            # Waterfall: Kokoro sidecar → Kokoro ONNX → local sidecar → Edge TTS → espeak
            if mode != "cloud":
                audio_bytes = await _synthesize_kokoro_sidecar(sentence)
                if audio_bytes:
                    provider = "kokoro-sidecar"

            if audio_bytes is None and mode != "cloud":
                audio_bytes = await _synthesize_kokoro(sentence)
                if audio_bytes:
                    provider = "kokoro-onnx"

            if audio_bytes is None and mode in {"hybrid", "local"} and local_tts_url:
                audio_bytes = await _synthesize_local_service(sentence, profile=profile, base_url=local_tts_url)
                if audio_bytes:
                    provider = "local-tts"

            if audio_bytes is None and mode in {"hybrid", "cloud", "edge"}:
                try:
                    audio_bytes = await _synthesize_edge_tts(sentence, edge_voice)
                    if audio_bytes:
                        provider = "edge-tts"
                except Exception as exc:
                    error_msg = str(exc)
                    audio_bytes = None

            if audio_bytes is None and mode != "cloud":
                try:
                    audio_bytes = await _synthesize_espeak(sentence, prof["espeak_speed"], prof["espeak_pitch"], prof["espeak_volume"])
                    if audio_bytes:
                        provider = "espeak-ng"
                except Exception as exc:
                    error_msg = str(exc)
                    audio_bytes = None

            if audio_bytes:
                header = _json.dumps({
                    "chunk": i,
                    "total": len(sentences),
                    "text": sentence[:80],
                    "provider": provider,
                })
                yield (header + "\n").encode()
                yield base64.b64encode(audio_bytes) + b"\n"
            else:
                # Yield an error object so the client knows a chunk failed.
                err_line = _json.dumps({
                    "chunk": i,
                    "total": len(sentences),
                    "error": error_msg or "all providers failed",
                    "text": sentence[:80],
                })
                yield (err_line + "\n").encode()

    return _StreamingResponse(
        _generate_chunks(),
        media_type="application/x-zoe-audio-stream",
        headers={"X-Chunk-Count": str(len(sentences)), "Cache-Control": "no-cache"},
    )


def _normalize_voice_command_text(text: str) -> str:
    """Correct common STT homophones that block deterministic voice intents."""
    normalized = (text or "").strip()
    if not normalized:
        return normalized
    replacements = [
        (r"\b(show|open|display|bring up|pull up)\s+(?:me\s+)?(?:the\s+)?whether\b", r"\1 weather"),
        (r"\bwhat(?:'s| is)\s+the\s+whether\b", "what is the weather"),
        (r"\bhow(?:'s| is)\s+the\s+whether\b", "how is the weather"),
        (r"^whether\b(?=\s+(?:today|tomorrow|forecast|this week|now)\b)", "weather"),
    ]
    for pattern, repl in replacements:
        normalized = re.sub(pattern, repl, normalized, flags=re.IGNORECASE)
    return normalized


def _env_float(name: str, default: float) -> float:
    # Delegates to typed_env (Wave-4 W4-T2). Same semantics as the old local
    # try/except (absent/empty/invalid -> default) + typed_env's warn-once on
    # invalid values, so a typo'd tunable is journal-visible.
    return env_float(name, default)


def _env_int(name: str, default: int) -> int:
    # Delegates to typed_env (Wave-4 W4-T2); see _env_float note.
    return env_int(name, default)


def _voice_stt_log_path() -> Path:
    configured = env_str("ZOE_VOICE_STT_LOG")
    if configured:
        return Path(configured)
    return Path.home() / ".zoe-voice" / "voice_stt.jsonl"


def _wav_duration_seconds(wav_path: str) -> float | None:
    try:
        with wave.open(wav_path, "rb") as wf:
            rate = wf.getframerate() or 0
            if rate <= 0:
                return None
            return round(wf.getnframes() / float(rate), 3)
    except Exception:
        return None


def _rotate_voice_stt_log(path: Path) -> None:
    max_bytes = _env_int("ZOE_VOICE_STT_LOG_MAX_BYTES", 5_000_000)
    if max_bytes <= 0 or not path.exists():
        return
    try:
        if path.stat().st_size < max_bytes:
            return
        rotated = path.with_name(path.name + ".1")
        if rotated.exists():
            rotated.unlink()
        path.replace(rotated)
    except OSError as exc:
        logger.debug("voice STT audit rotation failed: %s", exc)


def _log_voice_stt_sample(
    *,
    route: str,
    panel_id: str,
    audio_bytes: int,
    suffix: str,
    transcript: str = "",
    duration_seconds: float | None = None,
    stt_seconds: float | None = None,
    error: str | None = None,
) -> None:
    try:
        path = _voice_stt_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "route": route,
            "panel_id": panel_id,
            "audio_bytes": audio_bytes,
            "audio_suffix": suffix,
            "audio_duration_seconds": duration_seconds,
            "stt_seconds": round(stt_seconds, 3) if stt_seconds is not None else None,
            "transcript": transcript,
            "transcript_chars": len(transcript or ""),
            # Actual backend that produced this transcript (was hardcoded base.en).
            "model": _stt_backend_var.get() or env_str("ZOE_STT_BACKEND", "moonshine"),
            "moonshine_arch": moonshine_arch(),
        }
        if error:
            record["error"] = error[:500]
        _rotate_voice_stt_log(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("voice STT audit log failed: %s", exc)


# --- Moonshine ONNX STT (edge real-time; faster on short commands, CPU-only so
# it never competes with the brain's GPU). Model + tokenizer are cached once. ---
_moonshine_model = None  # moonshine_voice v2 Transcriber
_moonshine_load_error = None
_moonshine_lock = threading.Lock()
# Serializes Moonshine INFERENCE across executor threads: the wake-time warmup dummy
# inference and a real command transcription share one singleton transcriber, so they
# must not run concurrently (the model isn't guaranteed thread-safe per-inference).
_moonshine_infer_lock = threading.Lock()


def moonshine_arch() -> str:
    return env_str("ZOE_MOONSHINE_ARCH", "MEDIUM_STREAMING")


def moonshine_ready() -> bool:
    return _moonshine_model is not None


def moonshine_error() -> Optional[str]:
    return _moonshine_load_error


def _ensure_moonshine():
    """Lazily build the Moonshine v2 transcriber (MEDIUM_STREAMING by default —
    accurate on real room audio, ~0.5s/clip). Locked so a concurrent first call
    can't race the model load."""
    global _moonshine_model, _moonshine_load_error
    if _moonshine_model is not None:
        return _moonshine_model
    with _moonshine_lock:
        if _moonshine_model is None:
            try:
                import moonshine_voice as mv
                from moonshine_voice.transcriber import Transcriber

                archname = env_str("ZOE_MOONSHINE_ARCH", "MEDIUM_STREAMING")
                arch = getattr(mv.ModelArch, archname, mv.ModelArch.MEDIUM_STREAMING)
                model_path, resolved_arch = mv.get_model_for_language("en", arch)
                _moonshine_model = Transcriber(model_path, resolved_arch)
                _moonshine_load_error = None
            except Exception as exc:
                _moonshine_load_error = exc.__class__.__name__
                raise
    return _moonshine_model


_MOONSHINE_SAMPLE_RATE = 16000


def _prepare_audio_for_moonshine(audio, sample_rate: int):
    """Guarantee Moonshine gets the mono, 16 kHz float audio it expects.

    Moonshine's own ``load_wav_file`` mixes to mono but does NOT resample — it
    hands the file's native rate straight to ``transcribe_without_streaming``,
    which silently degrades accuracy if a panel ever captures at something other
    than 16 kHz. This helper closes that gap.

    Deliberately NOT a denoiser/normaliser: replaying the operator's real corpus
    (see tests + the wake-strip note) showed Moonshine is extremely sensitive to
    *any* per-sample edit — peak/RMS gain, DC-offset removal, and leading-silence
    / wake-audio trimming each regressed as many clips as they fixed (a clean
    "What time is it?" flipped to "Mom is it"). So for audio that is already mono
    16 kHz — every live panel turn today — this returns the samples unchanged
    (zero regression by construction). It only edits the samples to resample when
    the input rate genuinely differs.

    Returns ``(audio, rate)`` where ``rate`` is 16000 whenever the input rate was
    known. An unknown/invalid rate (``<= 0``) cannot be resampled, so the samples
    pass through and that rate is returned for the caller to surface.
    """
    import numpy as np

    try:
        sr = int(sample_rate)
    except (TypeError, ValueError):
        sr = 0

    a = np.asarray(audio, dtype=np.float32)
    needs_downmix = a.ndim > 1
    if needs_downmix:
        # Defensive mono downmix (load_wav_file already mixes, but a caller handing
        # us a 2-D array must not crash the C transcribe call).
        a = a.mean(axis=1).astype(np.float32)

    if sr == _MOONSHINE_SAMPLE_RATE and not needs_downmix:
        # Fast path: already mono at the target rate -> return UNCHANGED samples
        # (identity for the live 16 kHz path; no decode perturbation).
        return audio, _MOONSHINE_SAMPLE_RATE
    if a.size == 0:
        return [], (_MOONSHINE_SAMPLE_RATE if sr > 0 else sr)
    if sr <= 0:
        # Unknown/invalid native rate — we can't honestly resample. Don't pretend
        # it's 16 kHz; hand the (mono) samples back with the rate as-is so the
        # caller doesn't mistake malformed metadata for valid 16 kHz audio.
        return (audio if not needs_downmix else a.tolist()), sr
    if sr == _MOONSHINE_SAMPLE_RATE:
        # Was a 2-D 16 kHz array we just downmixed; no resample needed.
        return a.tolist(), _MOONSHINE_SAMPLE_RATE

    # Only reached when capture-format drifts off 16 kHz. Linear interpolation
    # resample (numpy-only — scipy is NOT a declared zoe-data dependency, so the
    # off-rate path must not import it). Adequate for 16 kHz speech STT and it
    # can't make a 16 kHz clip worse because this branch never runs for 16 kHz.
    n_out = max(1, int(round(a.shape[0] * _MOONSHINE_SAMPLE_RATE / sr)))
    src_idx = np.arange(a.shape[0], dtype=np.float64)
    dst_idx = np.linspace(0.0, a.shape[0] - 1, n_out)
    resampled = np.interp(dst_idx, src_idx, a).astype(np.float32)
    return resampled.tolist(), _MOONSHINE_SAMPLE_RATE


async def _run_moonshine(wav_path: str) -> str:
    from moonshine_voice.utils import load_wav_file

    def _work() -> str:
        tr = _ensure_moonshine()
        audio, sr = load_wav_file(wav_path)
        # Moonshine wants mono 16 kHz; load_wav_file doesn't resample. This is an
        # identity for the live 16 kHz path and only resamples off-rate capture.
        audio, sr = _prepare_audio_for_moonshine(audio, sr)
        if not isinstance(sr, int) or sr <= 0:
            # Corrupt/malformed WAV metadata (rate 0/None): _prepare_audio... hands
            # the rate back for us to surface — never feed it into the C transcribe
            # call. Treat the clip as unusable -> empty transcript (the caller's
            # empty_transcript handling re-prompts).
            logger.warning("Moonshine: unusable sample rate %r for %s — skipping clip", sr, wav_path)
            return ""
        with _moonshine_infer_lock:
            out = tr.transcribe_without_streaming(audio, sr)
        # Moonshine segments speech into lines and emits the wake phrase ("Hey Zoe.")
        # as its own leading line. Strip the wake word from those lines so the
        # leading "Hey Zoe" can't corrupt the command transcript (wake-word bleed).
        lines = [getattr(ln, "text", "") or "" for ln in getattr(out, "lines", [])]
        if lines:
            text = _strip_wake_word(lines)
            if text:
                return text
        # Fallback: some builds expose a flat .text; strip the wake prefix from it.
        flat = getattr(out, "text", None)
        if isinstance(flat, str) and flat.strip():
            return _strip_wake_word([flat])
        return ""

    return await asyncio.get_running_loop().run_in_executor(None, _work)


async def warm_moonshine() -> bool:
    """Pre-load the Moonshine STT model+tokenizer so the first panel turn isn't
    cold (saves the ~1-2s ONNX session load on the first utterance). Moonshine is
    the only live STT engine, so this ALWAYS warms regardless of ZOE_STT_BACKEND —
    a stale whisper-era value must not skip the warmup the live path depends on."""
    started = time.monotonic()
    try:
        await asyncio.get_running_loop().run_in_executor(None, _ensure_moonshine)
        logger.info("Moonshine STT warmup completed in %.2fs", time.monotonic() - started)
        return True
    except Exception as exc:
        logger.warning("Moonshine STT warmup failed (non-fatal): %s", exc)
        return False


async def _maybe_capture_stt(wav_path: str, primary: str) -> None:
    """When ZOE_VOICE_SAVE_AUDIO is set, save the real utterance to the operator's
    permanent regression corpus (~/.zoe-voice-samples) and log the Moonshine
    transcript. This is corpus capture only — there is NO whisper A/B; whisper
    must never run on a live turn (Moonshine is the only STT engine)."""
    if not env_bool("ZOE_VOICE_SAVE_AUDIO", default=False):
        return
    try:
        import shutil
        import time as _t

        d = os.environ.get("ZOE_VOICE_SAMPLE_DIR") or "/home/zoe/.zoe-voice-samples"
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, f"{_t.strftime('%H%M%S')}_{int(_t.time()*1000)%1000:03d}.wav")
        # Copy synchronously (fast) before the caller unlinks the original.
        shutil.copyfile(wav_path, dst)
    except Exception as exc:
        logger.warning("STT capture failed: %s", exc)
        return

    logger.info("STT_CAPTURE file=%s moonshine=%r", dst, (primary or "")[:90])


async def _transcribe_audio(wav_path: str) -> str:
    text = await _transcribe_audio_impl(wav_path)
    await _maybe_capture_stt(wav_path, text)
    return text


# Records which backend produced the transcript for the current turn, so the STT
# audit log reflects reality instead of a hardcoded "base.en". A ContextVar (not a
# module global) keeps this per-asyncio-task, so overlapping voice turns / an A/B
# capture running beside a live turn can't clobber each other's value.
_stt_backend_var: contextvars.ContextVar[str] = contextvars.ContextVar("stt_backend", default="")


async def _transcribe_audio_impl(wav_path: str) -> str:
    """Transcribe with Moonshine v2 — the ONLY live STT engine.

    Moonshine is the rock: there is no whisper fallback in the live path (it
    cold-loaded onto a memory-starved GPU and corrupted accuracy). A genuinely
    empty transcription returns "" (silence — callers re-prompt / no-op). A
    Moonshine BACKEND failure (missing model, OOM, runtime dep) RAISES, so callers
    can tell a real failure apart from silence instead of masking it as success.
    """
    try:
        text = await _run_moonshine(wav_path)
    except Exception as exc:
        logger.warning("Moonshine STT failed (backend error, surfacing): %s", exc)
        raise
    if text:
        _stt_backend_var.set("moonshine:" + env_str("ZOE_MOONSHINE_ARCH", "v2"))
        return text
    logger.info("Moonshine STT returned empty transcript")
    return ""


@router.post("/transcribe")
async def voice_transcribe(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """
    Transcribe base64 WAV/PCM audio using Moonshine v2 on the Jetson (Pi voice daemon).
    Request: { \"audio_base64\": \"...\", \"panel_id\": \"...\" }
    """
    b64 = str((payload or {}).get("audio_base64") or "").strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    suffix = ".wav"
    if len(raw) >= 4 and raw[:4] == b"RIFF":
        suffix = ".wav"
    elif len(raw) >= 2 and raw[:2] == b"\xff\xfb":
        suffix = ".mp3"

    duration_s: float | None = None
    stt_s: float | None = None
    t_stt_start: float | None = None
    try:
        text = ""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            wav_path = tmp.name
        duration_s = _wav_duration_seconds(wav_path) if suffix == ".wav" else None
        t_stt_start = time.monotonic()
        try:
            text = await _transcribe_audio(wav_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        stripped = text.strip()
        stt_s = time.monotonic() - t_stt_start
        _log_voice_stt_sample(
            route="transcribe",
            panel_id=panel_id,
            audio_bytes=len(raw),
            suffix=suffix,
            transcript=stripped,
            duration_seconds=duration_s,
            stt_seconds=stt_s,
        )
        logger.info(
            "voice/transcribe panel=%s audio=%.2fs STT=%.2fs chars=%d",
            panel_id, duration_s or 0.0, stt_s, len(stripped),
        )
        # Broadcast the transcribed user text so the UI shows what was heard.
        if stripped:
            try:
                from push import broadcaster
                await broadcaster.broadcast("all", "voice:transcript", {
                    "panel_id": panel_id,
                    "text": stripped,
                })
            except Exception:
                pass
        # Broadcast thinking state — STT done, LLM processing next.
        try:
            from push import broadcaster
            await broadcaster.broadcast("all", "voice:thinking", {"panel_id": panel_id})
        except Exception:
            pass
        return {"ok": True, "panel_id": panel_id, "text": stripped}
    except asyncio.TimeoutError as exc:
        if t_stt_start is not None:
            stt_s = time.monotonic() - t_stt_start
        _log_voice_stt_sample(
            route="transcribe",
            panel_id=panel_id,
            audio_bytes=len(raw),
            suffix=suffix,
            duration_seconds=duration_s,
            stt_seconds=stt_s,
            error=str(exc) or "Transcription timed out",
        )
        logger.warning("voice/transcribe timeout panel=%s", panel_id)
        raise HTTPException(status_code=504, detail="Transcription timed out") from None
    except RuntimeError as exc:
        if t_stt_start is not None:
            stt_s = time.monotonic() - t_stt_start
        _log_voice_stt_sample(
            route="transcribe",
            panel_id=panel_id,
            audio_bytes=len(raw),
            suffix=suffix,
            duration_seconds=duration_s,
            stt_seconds=stt_s,
            error=str(exc),
        )
        logger.warning("voice/transcribe unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        if t_stt_start is not None:
            stt_s = time.monotonic() - t_stt_start
        _log_voice_stt_sample(
            route="transcribe",
            panel_id=panel_id,
            audio_bytes=len(raw),
            suffix=suffix,
            duration_seconds=duration_s,
            stt_seconds=stt_s,
            error=str(exc),
        )
        logger.error("voice/transcribe error: %s", exc)
        raise HTTPException(status_code=500, detail="Transcription failed") from exc


async def _handle_introduce_intent(
    name: str, user_id: str, panel_id: str, session_id: str, turn_key: str, db
) -> str:
    """Create or find a person record, navigate the touch panel to their card, return person_id."""
    person_id: str = ""
    try:
        # Try to find existing person
        cursor = await db.execute(
            "SELECT id FROM people WHERE user_id=? AND deleted=0 AND lower(name) LIKE lower(?) LIMIT 1",
            (user_id, f"%{name}%"),
        )
        row = await cursor.fetchone()
        if row:
            person_id = row[0]
        else:
            # Create a new contact
            import uuid as _uuid
            person_id = str(_uuid.uuid4())
            await db.execute(
                "INSERT INTO people (id, user_id, name, relationship, circle, context, visibility) VALUES (?,?,?,?,?,?,?)",
                (person_id, user_id, name, "contact", "circle", "personal", "family"),
            )
            await db.commit()
    except Exception as exc:
        logger.warning("_handle_introduce_intent: DB error: %s", exc)
        import uuid as _uuid2
        person_id = str(_uuid2.uuid4())

    # Navigate touch panel to their people card
    try:
        from push import broadcaster as _bc_intro
        await _bc_intro.broadcast("all", "ui_action", {
            "action": {
                "action": "panel_navigate",
                # Estate is the sole kiosk: land on home.html; the estate opens the
                # person surface (DOMAIN_SCREEN falls through to FULL['person']).
                "url": f"/touch/home.html?domain=person&person={person_id}&intro=1",
            },
            "panel_id": panel_id,
            "turn_key": turn_key,
        })
    except Exception:
        pass

    return person_id


# Identity sentinels that are not real user accounts. Chat persistence must
# never attribute a turn to one of them: "voice-guest" has no `users` row, so
# the chat_messages save dies on the FK — and that failure used to be swallowed
# silently, which is why NO panel voice conversation ever persisted (P-F6,
# live-diagnosed 2026-07-07). A panel actually bound to a real user in
# ui_panel_sessions always takes precedence over these sentinels.
_GUEST_SENTINEL_USERS = frozenset({"", "guest", "voice-guest", "voice-daemon"})


async def _schedule_voice_chat_save(
    session_id: str, user_text: str, reply: str, user_id: str
) -> None:
    """Fire-and-forget: persist both turns of a voice exchange to chat_messages.

    Called from every exit path in voice_command so the full transcript ends up
    in the DB regardless of which fast-path handled the turn. The nightly digest
    and _load_voice_history both read from chat_messages, so this is the single
    fix that unblocks multi-turn context, transcript search, and nightly extraction.
    """
    if not session_id or user_id in _GUEST_SENTINEL_USERS:
        return
    try:
        # lazy — avoids circular import (routers.chat lazily imports us back)
        from routers.chat import _ensure_user_and_chat_session, _save_chat_message as _svc

        async def _persist() -> None:
            # Voice sessions are minted server-side and never go through
            # POST /sessions/, so the chat_sessions parent row must be created
            # here or every insert dies on the session FK (the silent zero-
            # voice-rows outage behind W0).
            await _ensure_user_and_chat_session(session_id, user_id)
            if user_text:
                await _svc(session_id, "user", user_text, user_id=user_id)
            if reply:
                await _svc(session_id, "assistant", reply, user_id=user_id)

        _spawn_bg(_persist())
    except Exception as exc:
        logger.warning(
            "voice chat save scheduling failed for user %s (session %s): %s",
            user_id, session_id, exc,
        )


async def _run_voice_memory_passes(
    user_text: str, reply: str, user_id: str, session_id: str
) -> None:
    """Run both memory extraction passes for a completed voice exchange.

    Standalone (non-nested) version so it can be called from any early-return
    path — not just the main LLM path at the bottom of voice_command.
    """
    try:
        # Mirror of the chat-lane guard: an EXPLICIT "remember/note that …"
        # spoken turn clears any forget tombstone it names, whichever lane
        # answers (see routers/chat.py::_persist_memory_candidates).
        try:
            from memory_tombstones import clear_matching as _tomb_clear, is_explicit_teach
            if is_explicit_teach(user_text):
                _tomb_clear(user_id, user_text)
        except Exception:
            pass
        from memory_extractor import extract_and_ingest as _mi
        from memory_digest import run_turn_digest as _td
        from person_extractor import process_text as _person_extract
        from person_extractor_llm import process_text_llm as _person_extract_llm
        from latent_intent_detector import detect_and_store as _detect_suggestions
        _mx_results = await asyncio.gather(
            _mi(user_text, reply, user_id=user_id, session_id=session_id,
                source="voice_regex", auto_approve=True),
            _td(user_id, user_text, reply, session_id=session_id,
                source="voice_turn_digest"),
            # USER TEXT ONLY — never mine the assistant reply for facts
            # (poisoned-store bug 2026-07-07: Zoe's own sentences were stored
            # as approved user memories; see the matching comment in
            # routers/chat.py and tests/test_memory_extractor_purity.py).
            _person_extract(
                user_text,
                user_id=user_id,
                source="voice",
                session_id=session_id,
            ),
            _person_extract_llm(
                user_text,
                user_id=user_id,
                source="voice",
                session_id=session_id,
            ),
            return_exceptions=True,
        )
        # QA review F13 / #1261: name-and-shame each failed pass at WARNING and
        # count it, so silent fact loss behind the instant "Got it" reply is
        # visible in ops (mirrors routers/chat.py::_persist_memory_candidates).
        for _mx_name, _mx_res in zip(
            ("extract_and_ingest", "run_turn_digest",
             "person_extract", "person_extract_llm"),
            _mx_results,
        ):
            if isinstance(_mx_res, BaseException):
                logger.warning(
                    "voice memory pass %s FAILED for user=%s (fact loss possible): %s",
                    _mx_name, user_id, _mx_res,
                )
                try:
                    from memory_metrics import memory_async_extract_fail_count
                    memory_async_extract_fail_count.labels(
                        lane="voice", pass_name=_mx_name).inc()
                except Exception:
                    pass
        _spawn_bg(_detect_suggestions(
            user_text,
            user_id=user_id,
            session_id=session_id,
        ))
    except Exception as exc:
        logger.warning("voice memory passes failed (non-fatal): %s", exc)


async def _run_hermes_voice_escalation(prompt: str, session_id: str, user_id: str) -> str:
    """Use Hermes for foreground voice escalation; OpenClaw is manual-only."""
    hermes_url = os.environ.get(
        "HERMES_API_URL",
        "http://127.0.0.1:8642/v1/chat/completions",
    )
    payload = {
        "model": os.environ.get("HERMES_MODEL", "hermes"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Hermes acting as Zoe's escalation agent for voice. "
                    "Be concise, complete the requested task, and avoid asking the user "
                    "to switch surfaces unless absolutely necessary."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User id: {user_id}\n"
                    f"Session id: {session_id}\n\n"
                    f"{prompt}"
                ),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 900,
        "stream": False,
    }
    timeout_s = float(os.environ.get("ZOE_VOICE_HERMES_TIMEOUT_S", "45"))
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(
            hermes_url,
            json=payload,
            headers=hermes_auth_headers(session_id=session_id),
        )
        resp.raise_for_status()
        data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


@router.post("/command")
async def voice_command(
    payload: dict,
    caller: dict = Depends(_require_voice_auth),
    stream: bool = Query(False, description="When true, stream audio chunks as they are produced."),
    db=Depends(get_db),
):
    """
    Receive a transcribed voice command from the Pi daemon (after wake word + STT).

    The Pi daemon POSTs: { "text": "...", "panel_id": "...", "session_id": "..." }
    Zoe routes the text through the chat pipeline and returns a JSON response
    with the reply text and synthesised audio (base64) so the Pi can play it back.
    """
    text = str((payload or {}).get("text", "")).strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    identified_user_id: Optional[str] = (payload or {}).get("identified_user_id") or None
    if not identified_user_id:
        # Panels that match speaker profiles locally send a claim + score; the
        # threshold decision stays server-side so a panel can never lower it,
        # and only device-token callers may claim at all. The claimed user must
        # STILL hold consent in the DB — a panel whose profile cache predates a
        # revocation must not keep identifying that user (fail closed).
        _claimed = _accept_panel_voice_claim(payload, caller)
        if _claimed and await _voice_claim_consented(_claimed):
            identified_user_id = _claimed
    # Forwarded by /voice/turn so end-to-end total can be recorded from the
    # true start of the request (audio upload). Falls back to command start.
    _t_turn_start = (payload or {}).get("_t_turn_start")
    _t_cmd_start = time.monotonic()
    _turn_key = str(time.monotonic_ns())
    _quick_intent_name: Optional[str] = None
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    normalized_text = _normalize_voice_command_text(text)
    if normalized_text != text:
        logger.info("voice/command normalized transcript %r -> %r", text[:80], normalized_text[:80])
        text = normalized_text

    # Tier-1 semantic router (SHADOW by default): log what it would classify this
    # utterance as, so we can validate routing accuracy on live traffic before it
    # ever changes behavior. Logged at WARNING so it's visible past the log level.
    _router_decision: Optional[dict] = None
    try:
        import semantic_router as _sr
        if _sr.is_enabled():
            _rr = _sr.route(text)
            _router_decision = _rr
            logger.warning("ROUTER_SHADOW text=%r -> routed=%s (best=%s %.2f) %.1fms scores=%s",
                           text[:70], _rr["routed"], _rr["domain"], _rr["score"], _rr["ms"], _rr["scores"])
    except Exception as _rexc:
        logger.debug("semantic router shadow failed: %s", _rexc)

    # Classify identity source for Pass 3 observability (Pass 1 is measurement-only).
    try:
        from voice_metrics import voice_identity_source_count
        if identified_user_id:
            voice_identity_source_count.labels(source="daemon_vid").inc()
        elif caller.get("panel_id"):
            # device-token path resolves to family-admin today (Pass 0 finding).
            voice_identity_source_count.labels(source="fallback_family_admin").inc()
        else:
            voice_identity_source_count.labels(source="fallback_guest").inc()
    except Exception:
        pass

    # Session continuity: reuse the same session for follow-up commands within 5 min.
    # Caller can override with an explicit session_id (e.g. for HA bridge routing).
    explicit_session = str((payload or {}).get("session_id", "")).strip()
    session_id = explicit_session if explicit_session else _get_or_create_voice_session(panel_id)

    # Pass 3 B3: if daemon supplied an identified_user_id, bind it to the session
    # so the next turn from the same panel doesn't need to re-verify.
    if identified_user_id:
        _ses = _VOICE_SESSIONS.get(panel_id)
        if _ses:
            _ses["bound_user_id"] = identified_user_id

    # Resolve effective user once so all downstream branches (intent fast path,
    # scope checks, Zoe Agent, and Hermes escalation) share the same identity.
    #
    # P-F8: identity lookups MUST use a dedicated connection, not the shared request
    # `db`. On the streaming panel path (voice_turn_stream) voice_command is launched
    # via asyncio.ensure_future(..., db=db) and runs CONCURRENTLY with turn_stream's
    # use of that same pooled asyncpg connection. asyncpg forbids concurrent operations
    # on one connection, so a resolver query on the shared `db` raises mid-flight and
    # the resolvers' best-effort `except: pass` swallows it → None → effective_user
    # ='guest' → every panel voice turn was silently dropped from memory. A private
    # short-lived connection removes the race (reproduced: solo→jason, concurrent→None).
    _bound_user = (_VOICE_SESSIONS.get(panel_id) or {}).get("bound_user_id")
    _panel_recent_user = None
    _panel_default_user = None
    try:
        from db_pool import get_db_ctx as _get_db_ctx
        async with _get_db_ctx() as _idconn:
            _panel_recent_user = await _resolve_recent_panel_session_user(panel_id, _idconn)
            _panel_default_user = await _resolve_panel_default_user(panel_id, _idconn)
    except Exception:
        # Dedicated connection unavailable (e.g. pool momentarily exhausted). Fall back
        # to the request `db`: on the awaited /turn path it is still valid → correct
        # identity even under pool pressure; on the detached streaming/PIN-replay path it
        # is released/racy so this query just fails → we keep None. Either way, never a
        # WRONG attribution (the dedicated conn is always TRIED first, so the detached
        # path uses it in the normal case — this fallback only runs on acquire failure).
        logger.warning("voice identity: dedicated conn unavailable for panel=%s; trying request db", panel_id)
        try:
            _panel_recent_user = await _resolve_recent_panel_session_user(panel_id, db)
            _panel_default_user = await _resolve_panel_default_user(panel_id, db)
        except Exception:
            _panel_recent_user = None
            _panel_default_user = None
    # Keep the panel's login alive while it's actively in use. The session tracks
    # "whoever is logged into the panel" and should lapse only after real inactivity
    # — but ui_panel_sessions is written only on touch/login, so a VOICE-only session
    # expired mid-conversation (~15 min trust window), dropping the user to guest and
    # blocking their scoped commands. The heartbeat below is that fix (#1349): a user
    # whose session is STILL FRESH keeps it fresh by talking.
    #
    # A lapsed session must NOT be revived here. `_panel_recent_user` is the
    # freshness-gated signal, and it feeds `_scope_identity_user` → the skybridge
    # user and every `user_scoped` PIN gate. `_resolve_panel_default_user` has NO
    # freshness filter — it returns the newest `ui_panel_sessions` row, i.e. "whoever
    # last signed in here", not an operator-declared owner. Promoting it into
    # `_panel_recent_user` therefore re-trusts a logged-OUT user indefinitely, so
    # anyone at the panel reads their lists/calendar/reminders with no PIN. It is
    # also self-perpetuating: the heartbeat would refresh that expired session on
    # every turn — including a guest's — so idle logout could never fire and #1348's
    # stale-owner reclaim could never arm. Freshness confers trust (#1348); a stale
    # owner is reclaimable. Attribution is unaffected: `_panel_default_user` is
    # already the last resort in `effective_user` below.
    if _panel_recent_user and _panel_recent_user not in _GUEST_SENTINEL_USERS:
        await _touch_panel_session(panel_id, _panel_recent_user)
    if not _bound_user and _panel_recent_user:
        _ses = _VOICE_SESSIONS.get(panel_id)
        if _ses is not None:
            _ses["bound_user_id"] = _panel_recent_user
        _bound_user = _panel_recent_user
    _scope_identity_user = identified_user_id or _bound_user or _panel_recent_user
    _has_scope_identity = bool(_scope_identity_user)
    effective_user = (
        identified_user_id
        or _bound_user
        or _panel_recent_user
        or _panel_default_user
        or caller.get("user_id")
        or "guest"
    )
    if effective_user == "voice-daemon":
        effective_user = _panel_default_user or "guest"
    if effective_user in _GUEST_SENTINEL_USERS:
        # P-F6: a guest-sentinel identity (e.g. a client-supplied
        # identified_user_id="voice-guest") must not out-rank a panel that is
        # actually bound to a real user in ui_panel_sessions — the turn would
        # be mis-attributed and its chat_messages save would die on the users
        # FK. Reuse the panel lookups already resolved above. Persistence
        # attribution only: the PIN/sensitive-scope gate reads
        # _scope_identity_user/_has_scope_identity (set above) and is untouched.
        _panel_bound_user = _panel_recent_user or _panel_default_user
        if _panel_bound_user and _panel_bound_user not in _GUEST_SENTINEL_USERS:
            logger.info(
                "voice/command guest-sentinel identity %r replaced by panel-bound user %s (panel=%s)",
                effective_user, _panel_bound_user, panel_id,
            )
            effective_user = _panel_bound_user

    logger.info(
        "voice/command panel=%s session=%s user=%s len=%d "
        "[identity: identified=%s bound=%s panel_recent=%s panel_default=%s scope_user=%s has_scope=%s]",
        panel_id, session_id, effective_user, len(text),
        identified_user_id, _bound_user, _panel_recent_user, _panel_default_user,
        _scope_identity_user, _has_scope_identity,
    )

    # Persist user turn to chat_messages immediately so all downstream paths
    # (nightly digest, _load_voice_history, multi-turn context) have the transcript.
    if text and effective_user not in _GUEST_SENTINEL_USERS:
        try:
            from routers.chat import (
                _ensure_user_and_chat_session as _ensure_sess_user_turn,
                _save_chat_message as _svc_user_turn,
            )

            async def _persist_user_turn() -> None:
                # Same FK guard as _schedule_voice_chat_save: voice session ids
                # never pass through POST /sessions/, so mint the parent row.
                await _ensure_sess_user_turn(session_id, effective_user)
                await _svc_user_turn(session_id, "user", text, user_id=effective_user)

            _spawn_bg(_persist_user_turn())
        except Exception as exc:
            logger.warning(
                "voice user-turn save scheduling failed for user %s (session %s): %s",
                effective_user, session_id, exc,
            )

    # ── Active action-form panel: route voice to field-filling ─────────────
    # When the touch panel has an action form open (calendar_event or shopping_list),
    # incoming voice utterances are interpreted as field updates rather than new
    # chat commands. Simple NLU extracts the field/value and emits panel_update_field
    # or panel_list_update events. "Cancel" / "dismiss" closes the form.
    try:
        from panel_form_state import get_active_form, clear_active_form
        _active_form = get_active_form(panel_id)
        if _active_form:
            _form_panel_type = _active_form.get("panel_type", "")
            _lc_voice = text.lower().strip().rstrip(".!?")
            _cancel_words = {"cancel", "dismiss", "close", "never mind", "nevermind", "forget it", "stop"}
            if any(w in _lc_voice for w in _cancel_words):
                # User said cancel — dismiss the panel
                clear_active_form(panel_id)
                try:
                    from push import broadcaster as _bc_af
                    await _bc_af.broadcast("all", "ui_action", {
                        "action": {
                            "id": f"voice_form_close_{panel_id}_{_turn_key}",
                            "action_type": "panel_close_action_form",
                            "payload": {"panel_id": panel_id},
                        }
                    })
                except Exception:
                    pass
                _cancel_reply = "Okay, cancelled."
                _cancel_audio = await synthesize({"text": _cancel_reply}, caller=caller)
                return {
                    "ok": True, "panel_id": panel_id, "reply": _cancel_reply,
                    "audio_base64": base64.b64encode(_cancel_audio.body).decode("ascii"),
                    "content_type": _cancel_audio.media_type,
                }
            # Confirm words: submit the active form without requiring a tap on the button.
            _FORM_CONFIRM_EXACT = {
                "confirm", "save", "yes", "submit", "done", "accept",
                "ok", "okay", "perfect", "correct",
            }
            _FORM_CONFIRM_PHRASES = {
                "that's correct", "thats correct", "looks good", "looks right",
                "sounds good", "go ahead", "yes please", "yes do it", "save it",
            }
            _is_form_confirm = (
                _lc_voice in _FORM_CONFIRM_EXACT
                or any(phrase in _lc_voice for phrase in _FORM_CONFIRM_PHRASES)
                or any(_lc_voice.startswith(w + " ") or _lc_voice.endswith(" " + w)
                       for w in _FORM_CONFIRM_EXACT)
            )
            if _is_form_confirm:
                _form_data = _active_form.get("slots") or {}
                _confirm_reply = "Event saved." if _form_panel_type == "calendar_event" else "List saved."
                try:
                    if _form_panel_type == "calendar_event":
                        from intent_router import execute_intent as _exec_cal_confirm
                        from dataclasses import dataclass as _dc_cal

                        @_dc_cal
                        class _SynthIntentVCal:
                            name: str
                            slots: dict

                        _synth_cal = _SynthIntentVCal(name="calendar_create", slots=_form_data)
                        _cal_result = await _exec_cal_confirm(_synth_cal, effective_user)
                        _confirm_reply = _cal_result or "Event saved."
                    elif _form_panel_type == "shopping_list":
                        from intent_router import execute_intent as _exec_list_confirm
                        from dataclasses import dataclass as _dc_list

                        @_dc_list
                        class _SynthIntentVList:
                            name: str
                            slots: dict

                        # Prefer new_item when panel was prefilled with existing list.
                        _new_item_save = _form_data.get("new_item") or ""
                        if _new_item_save:
                            _items_to_save = [_new_item_save]
                        else:
                            _items_to_save = _form_data.get("items") or (
                                [_form_data["item"]] if _form_data.get("item") else []
                            )
                        _list_name_save = _form_data.get("list_name", "shopping")
                        _save_results = []
                        for _it_save in _items_to_save:
                            _sy = _SynthIntentVList(
                                name="list_add",
                                slots={"item": str(_it_save), "list_name": _list_name_save,
                                       "list_type": _list_name_save},
                            )
                            _r_save = await _exec_list_confirm(_sy, effective_user)
                            if _r_save:
                                _save_results.append(_r_save)
                        _confirm_reply = "; ".join(_save_results) if _save_results else "List saved."
                except Exception as _conf_exc:
                    logger.warning("voice form voice-confirm failed: %s", _conf_exc)
                clear_active_form(panel_id)
                try:
                    from push import broadcaster as _bc_conf
                    await _bc_conf.broadcast("all", "ui_action", {
                        "action": {
                            "id": f"voice_form_close_{panel_id}_{_turn_key}",
                            "action_type": "panel_close_action_form",
                            "payload": {"panel_id": panel_id},
                        }
                    })
                except Exception:
                    pass
                _confirm_audio = await synthesize({"text": _confirm_reply}, caller=caller)
                return {
                    "ok": True, "panel_id": panel_id, "reply": _confirm_reply,
                    "audio_base64": base64.b64encode(_confirm_audio.body).decode("ascii"),
                    "content_type": _confirm_audio.media_type,
                }
            # Try to extract a field update from the utterance.
            _field_updates = _parse_voice_form_field(text, _form_panel_type)
            if _field_updates:
                try:
                    from push import broadcaster as _bc_af2
                    for _fname, _fval in _field_updates.items():
                        _af_action_type = (
                            "panel_list_update"
                            if _form_panel_type == "shopping_list" and _fname in ("add", "remove")
                            else "panel_update_field"
                        )
                        _af_payload: dict = {"panel_id": panel_id}
                        if _af_action_type == "panel_list_update":
                            _af_payload["op"] = _fname
                            _af_payload["item"] = _fval
                        else:
                            _af_payload["field"] = _fname
                            _af_payload["value"] = _fval
                        await _bc_af2.broadcast("all", "ui_action", {
                            "action": {
                                "id": f"voice_field_{panel_id}_{_fname}_{_turn_key}",
                                "action_type": _af_action_type,
                                "payload": _af_payload,
                            }
                        })
                except Exception as _afe:
                    logger.debug("voice form field broadcast failed (non-fatal): %s", _afe)
                # Persist field changes into in-memory slots so a subsequent voice
                # "confirm" command uses the latest values.
                try:
                    _slots_ref = _active_form.setdefault("slots", {})
                    for _usf_name, _usf_val in _field_updates.items():
                        if _form_panel_type == "shopping_list":
                            _slot_items = _slots_ref.setdefault("items", [])
                            if _usf_name == "add" and _usf_val not in _slot_items:
                                _slot_items.append(_usf_val)
                            elif _usf_name == "remove" and _usf_val in _slot_items:
                                _slot_items.remove(_usf_val)
                        else:
                            _slots_ref[_usf_name] = _usf_val
                except Exception:
                    pass
                _field_reply_parts = []
                for _fn, _fv in _field_updates.items():
                    if _fn == "add":
                        _field_reply_parts.append(f"Added {_fv} to the list.")
                    elif _fn == "remove":
                        _field_reply_parts.append(f"Removed {_fv}.")
                    elif _fn == "title":
                        _field_reply_parts.append(f"Title set to {_fv}.")
                    elif _fn == "date":
                        _field_reply_parts.append(f"Date updated.")
                    elif _fn == "time":
                        _field_reply_parts.append(f"Time set to {_fv}.")
                    elif _fn == "location":
                        _field_reply_parts.append(f"Location set to {_fv}.")
                    else:
                        _field_reply_parts.append("Updated.")
                _field_reply = " ".join(_field_reply_parts) or "Got it."
                _field_audio = await synthesize({"text": _field_reply}, caller=caller)
                return {
                    "ok": True, "panel_id": panel_id, "reply": _field_reply,
                    "audio_base64": base64.b64encode(_field_audio.body).decode("ascii"),
                    "content_type": _field_audio.media_type,
                }
            # Utterance didn't match a known field — let it fall through to normal chat.
    except Exception as _af_outer_exc:
        logger.debug("voice form routing failed (non-fatal): %s", _af_outer_exc)

    # ── Voice confirmation: check if we're waiting for yes/no ─────────────
    pending = _PENDING_CONFIRMATIONS.get(panel_id)
    if pending:
        now = time.monotonic()
        if now > pending.get("expire_at", 0):
            del _PENDING_CONFIRMATIONS[panel_id]
            pending = None
        else:
            lc = text.lower().strip().rstrip(".!?")
            if _contains_decision_keyword(lc, _CONFIRM_KEYWORDS):
                # User confirmed — execute the intent now.
                del _PENDING_CONFIRMATIONS[panel_id]
                from intent_router import execute_intent, Intent
                confirmed_intent = Intent(
                    name=pending["intent_name"],
                    slots=pending["slots"],
                    confidence=1.0,
                )
                try:
                    from auth import get_current_user as _gcu
                    _uid = identified_user_id or pending.get("user_id", "voice-daemon")
                    result = await execute_intent(confirmed_intent, _uid)
                    reply_text = result or "Done!"
                    if confirmed_intent.name in {"calendar_create", "calendar_show", "daily_briefing"}:
                        await _broadcast_calendar_ui(panel_id, reply_text, turn_key=_turn_key)
                except Exception as exc:
                    logger.error("Confirmed intent execution failed: %s", exc)
                    reply_text = "Sorry, that failed. Please try again."
                try:
                    audio_resp = await synthesize({"text": reply_text}, caller=caller)
                    audio_b64_conf = base64.b64encode(audio_resp.body).decode("ascii")
                    ct_conf = audio_resp.media_type
                except Exception:
                    audio_b64_conf = None
                    ct_conf = "audio/wav"
                try:
                    from push import broadcaster as _bc_conf
                    await _bc_conf.broadcast("all", "voice:done", {"panel_id": panel_id})
                except Exception:
                    pass
                await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
                _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))
                return {"ok": True, "panel_id": panel_id, "reply": reply_text,
                        "audio_base64": audio_b64_conf, "content_type": ct_conf}
            elif _contains_decision_keyword(lc, _CANCEL_KEYWORDS):
                del _PENDING_CONFIRMATIONS[panel_id]
                reply_text = "Okay, cancelled."
                try:
                    audio_resp = await synthesize({"text": reply_text}, caller=caller)
                    audio_b64_can = base64.b64encode(audio_resp.body).decode("ascii")
                    ct_can = audio_resp.media_type
                except Exception:
                    audio_b64_can = None
                    ct_can = "audio/wav"
                try:
                    from push import broadcaster as _bc_can
                    await _bc_can.broadcast("all", "voice:done", {"panel_id": panel_id})
                except Exception:
                    pass
                return {"ok": True, "panel_id": panel_id, "reply": reply_text,
                        "audio_base64": audio_b64_can, "content_type": ct_can}
            # Not a clear yes/no — fall through to normal processing (treat as new command)

    # Show the user's words in the voice overlay before processing.
    try:
        from push import broadcaster as _bc
        await _bc.broadcast("all", "voice:transcript", {"panel_id": panel_id, "text": text})
    except Exception:
        pass

    # Broadcast thinking state so orb shows processing animation.
    try:
        from push import broadcaster as _bc
        await _bc.broadcast("all", "voice:thinking", {"panel_id": panel_id})
    except Exception:
        pass

    _voice_processing_ack_sent = False
    if not stream:
        try:
            from pi_hybrid_production import (
                PiHybridProductionConfig,
                pi_hybrid_production_eligible,
                processing_cue_packet,
                try_pi_hybrid_production,
            )

            _pi_hybrid_config = PiHybridProductionConfig.from_env()
            _pi_hybrid_eligible, _pi_hybrid_reason = pi_hybrid_production_eligible(text, config=_pi_hybrid_config)
            if _pi_hybrid_eligible:
                _pi_cue = await processing_cue_packet(text=text)
                _ack_text = str(_pi_cue.get("text") or "").strip()
                if _ack_text:
                    try:
                        from push import broadcaster as _bc_pi_hybrid

                        await _bc_pi_hybrid.broadcast("all", "voice:responding", {
                            "panel_id": panel_id,
                            "text": _ack_text,
                            "processing_ack": True,
                            "source": "pi_hybrid_production",
                        })
                        _voice_processing_ack_sent = True
                    except Exception:
                        pass
                _pi_hybrid = await try_pi_hybrid_production(
                    text,
                    user_id=effective_user,
                    context_turns="",
                    config=_pi_hybrid_config,
                )
                if _pi_hybrid.get("accepted") and _pi_hybrid.get("response_text"):
                    reply_text = str(_pi_hybrid.get("response_text") or "")
                    if str(_pi_hybrid.get("intent") or "") == "list_show":
                        reply_text = _cap_voice_list_show_reply(reply_text)
                    _pi_audio = await synthesize({"text": reply_text}, caller=caller)
                    try:
                        from push import broadcaster as _bc_pi_done

                        await _bc_pi_done.broadcast("all", "voice:responding", {
                            "panel_id": panel_id,
                            "text": reply_text[:200],
                            "pi_hybrid": True,
                        })
                        if str(_pi_hybrid.get("intent") or "") == "weather":
                            await _broadcast_weather_ui(panel_id, reply_text, turn_key=_turn_key)
                        await _bc_pi_done.broadcast("all", "voice:done", {"panel_id": panel_id})
                    except Exception:
                        pass
                    await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
                    _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_pi_audio.body).decode("ascii"),
                        "content_type": _pi_audio.media_type,
                        "intent": str(_pi_hybrid.get("intent") or "pi_hybrid"),
                        "pi_hybrid": {
                            "accepted": True,
                            "reason": _pi_hybrid.get("reason"),
                            "intent_group": _pi_hybrid.get("intent_group"),
                            "agreement_kind": _pi_hybrid.get("agreement_kind"),
                            "processing_cue": {"available": bool(_pi_cue.get("available")), "text": _ack_text},
                        },
                    }
            elif _pi_hybrid_config.enabled:
                logger.debug("voice/command Pi hybrid production skipped: %s", _pi_hybrid_reason)
        except Exception as _pi_hybrid_exc:
            logger.debug("voice/command Pi hybrid failed open to existing voice route: %s", _pi_hybrid_exc)

    if not stream and not _voice_processing_ack_sent:
        try:
            from pi_hybrid_production import processing_cue_packet
            from push import broadcaster as _bc_voice_fallback

            _fallback_cue = await processing_cue_packet(text=text)
            _fallback_ack_text = str(_fallback_cue.get("text") or "").strip()
            if _fallback_ack_text:
                await _bc_voice_fallback.broadcast("all", "voice:responding", {
                    "panel_id": panel_id,
                    "text": _fallback_ack_text,
                    "processing_ack": True,
                    "source": "voice_command_fallback",
                })
        except Exception as _fallback_ack_exc:
            logger.debug("voice/command fallback processing acknowledgement failed: %s", _fallback_ack_exc)

    # Skybridge-first touch prototype: supported visual domains render as cards
    # on the single Skybridge surface instead of navigating to domain pages.
    try:
        from skybridge_service import resolve_skybridge_request

        _voice_entry = _VOICE_SESSIONS.get(panel_id, {})
        _skybridge_context = _voice_entry.get("skybridge_context") or {}
        _skybridge_user = _scope_identity_user or "guest"
        _sky_t0 = time.monotonic()
        # No db= here: voice_command runs as a detached task on the turn_stream
        # lane (raced since #1106), so the request-scoped connection can already
        # be back in the pool by now — asyncpg then rejects every query with
        # "connection has been released back to the pool" and the fast path
        # silently dies. resolve_skybridge_request acquires a fresh pooled
        # connection itself when db is omitted.
        _skybridge_result = await resolve_skybridge_request(
            text,
            _skybridge_user,
            context=_skybridge_context,
        )
        _sky_t_resolve = time.monotonic() - _sky_t0
        if _skybridge_result and _skybridge_result.get("auth_required"):
            _intent = _skybridge_result.get("intent") or {}
            try:
                from guest_policy import record_policy_decision as _record_guest_policy
                _record_guest_policy(
                    "auth_required",
                    surface="voice",
                    resource="skybridge",
                    action=f"{_intent.get('domain', 'unknown')}:{_intent.get('action', 'show')}",
                )
            except Exception as _policy_exc:
                logger.warning("voice/command skybridge auth policy record failed: %s", _policy_exc)
            try:
                return await _request_voice_identity_challenge(
                    panel_id=panel_id,
                    text=text,
                    session_id=session_id,
                    caller=caller,
                    reason="Skybridge needs to know who is speaking before showing personal data.",
                )
            except Exception as _auth_exc:
                logger.warning("voice/command skybridge auth challenge failed: %s", _auth_exc)
                _auth_fail_phrase = "Authentication is required before I can show that. I could not open the touch panel authentication screen just now."
                try:
                    _auth_fail_audio = await synthesize({"text": _auth_fail_phrase}, caller=caller)
                    _auth_fail_b64 = base64.b64encode(_auth_fail_audio.body).decode("ascii")
                    _auth_fail_type = _auth_fail_audio.media_type
                except Exception:
                    _auth_fail_b64 = None
                    _auth_fail_type = "audio/wav"
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _auth_fail_phrase,
                    "status": "auth_unavailable",
                    "audio_base64": _auth_fail_b64,
                    "content_type": _auth_fail_type,
                }
        if _skybridge_result and _skybridge_result.get("handled"):
            _VOICE_SESSIONS.setdefault(panel_id, {})["skybridge_context"] = (
                _skybridge_result.get("skybridge_context") or {}
            )
            _skybridge_reply = str(_skybridge_result.get("spoken_summary") or "Showing that in Skybridge.")
            _bcast_t0 = time.monotonic()
            await _broadcast_skybridge_ui(
                panel_id,
                _skybridge_result,
                utterance=text,
                turn_key=_turn_key,
            )
            _bcast_dt = time.monotonic() - _bcast_t0
            _synth_t0 = time.monotonic()
            # In stream mode, /turn_stream synthesizes the reply sentence-by-
            # sentence for fast first-audio, so skip the blocking full synth here
            # and hand back just the text (audio_base64=None).
            _skybridge_audio_b64: Optional[str] = None
            _skybridge_ct = "audio/wav"
            if not stream:
                _skybridge_audio = await synthesize({"text": _skybridge_reply}, caller=caller)
                _skybridge_audio_b64 = base64.b64encode(_skybridge_audio.body).decode("ascii")
                _skybridge_ct = _skybridge_audio.media_type
            logger.info("SKYBRIDGE TIMING resolve=%.2fs broadcast=%.2fs synth=%.2fs stream=%s reply=%r",
                        _sky_t_resolve, _bcast_dt, time.monotonic() - _synth_t0, stream, _skybridge_reply[:50])
            try:
                from push import broadcaster as _bc_sky
                await _bc_sky.broadcast("all", "voice:responding", {"panel_id": panel_id, "text": _skybridge_reply[:200]})
                await _bc_sky.broadcast("all", "voice:done", {"panel_id": panel_id})
            except Exception:
                pass
            await _schedule_voice_chat_save(session_id, text, _skybridge_reply, _skybridge_user)
            _spawn_bg(_run_voice_memory_passes(text, _skybridge_reply, _skybridge_user, session_id))
            return {
                "ok": True,
                "panel_id": panel_id,
                "reply": _skybridge_reply,
                "audio_base64": _skybridge_audio_b64,
                "content_type": _skybridge_ct,
                "intent": f"skybridge:{(_skybridge_result.get('intent') or {}).get('domain', 'unknown')}",
                "skybridge": True,
            }
        _VOICE_SESSIONS.setdefault(panel_id, {})["skybridge_context"] = {}
    except Exception as _skybridge_exc:
        # Surfaced at warning (was debug): when this fires the turn falls through to
        # the legacy intent path, which is the silent bounce-to-domain-page behavior.
        logger.warning("voice/command skybridge fast path failed (non-fatal): %s", _skybridge_exc)

    # Fallback audio used when any part of the pipeline fails — panel never goes silent.
    _FALLBACK_PHRASE = "Sorry, something went wrong. Please try again."

    async def _make_fallback_audio() -> tuple[Optional[str], str]:
        try:
            fb_resp = await synthesize({"text": _FALLBACK_PHRASE}, caller=caller)
            return base64.b64encode(fb_resp.body).decode("ascii"), fb_resp.media_type
        except Exception:
            return None, "audio/wav"

    # ── Voice confirmation gate: intercept write intents before sending to LLM ──
    # Detect intent locally and if it's a write intent, speak back parsed data and
    # wait for 'yes/confirm' from the user before actually executing.
    try:
        from intent_router import detect_and_extract_intent as _detect_async
        from conversation_context import ConversationContext as _CC
        _t_scope_start = time.monotonic()
        _voice_entry = _VOICE_SESSIONS.get(panel_id, {})
        _ctx = _voice_entry.get("context") or _CC()
        _voice_entry["context"] = _ctx
        if panel_id not in _VOICE_SESSIONS:
            _VOICE_SESSIONS[panel_id] = _voice_entry
        _quick_intent = await _detect_async(text, effective_user, context=_ctx)
        if _quick_intent:
            _ctx.activate(_quick_intent.name, getattr(_quick_intent, "slots", {}), text)
        # (Removed Tier-0.5 LLM classifier.) It produced 0 hits across 7 days of
        # live logs and 0 non-None returns over 41 real missed utterances, while
        # costing a ~1.5s LLM call on every short missed utterance. The Tier-1
        # semantic_router + Tier-1.5 expert_dispatch now cover this ground, so a
        # detect_intent miss falls straight through to them / the brain.
        _quick_intent_name = _quick_intent.name if _quick_intent else None
        try:
            from voice_metrics import voice_stage_seconds, voice_intent_hit_count
            voice_stage_seconds.labels(stage="scope").observe(time.monotonic() - _t_scope_start)
            voice_intent_hit_count.labels(
                intent=(_quick_intent.name if _quick_intent else "none"),
            ).inc()
        except Exception:
            pass
        if _quick_intent and _quick_intent.name in _CONFIRM_INTENTS:
            # User-scoped intents must go through PIN challenge first.
            try:
                from voice_scope import classify as _vscope_pre
                _quick_scope = _vscope_pre(text, _quick_intent)
                if _quick_scope.scope == "user_scoped" and not _has_scope_identity:
                    _quick_intent = None
            except Exception:
                pass


        if _quick_intent and _quick_intent.name == "people_introduce":
            try:
                intro_name = (_quick_intent.slots or {}).get("name", "").strip()
                if intro_name:
                    person_id = await _handle_introduce_intent(
                        intro_name, effective_user, panel_id, session_id, _turn_key, db
                    )
                    reply_text = (
                        f"Hi {intro_name}, I'm Zoe. So nice to meet you! "
                        f"What do you do for work, or what are you passionate about?"
                    )
                    _INTRO_STATE[panel_id] = {
                        "person_id": person_id,
                        "person_name": intro_name,
                        "step": 0,
                        "expires": time.monotonic() + 120,
                    }
                    _intro_audio = await synthesize({"text": reply_text}, caller=caller)
                    await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
                    _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_intro_audio.body).decode("ascii"),
                        "content_type": _intro_audio.media_type,
                        "intent": "people_introduce",
                        "person_id": person_id,
                    }
            except Exception as _intro_exc:
                logger.warning("voice/command people_introduce failed: %s", _intro_exc)

        if _quick_intent and _quick_intent.name == "list_add":
            # Show the shopping list action-form panel with the item pre-filled.
            # The panel's Done button POSTs to /api/ui/panel/form/confirm which
            # then adds all confirmed items to the list.
            _allow_quick_list = True
            try:
                from voice_scope import classify as _vscope_list
                _list_scope = _vscope_list(text, _quick_intent)
                if _list_scope.scope == "user_scoped" and not _has_scope_identity:
                    _allow_quick_list = False
            except Exception:
                pass
            if _allow_quick_list:
                try:
                    slots = _quick_intent.slots or {}
                    _item_text = str(slots.get("item", "")).strip()
                    _list_type = str(slots.get("list_type", "shopping"))
                    _list_title = "Shopping List" if _list_type == "shopping" else f"{_list_type.title()} List"

                    # Pre-load existing non-completed items so the panel shows the full list.
                    _existing_list_items: list = []
                    try:
                        from database import get_db as _get_db_prefill
                        _norm_lt = _list_type.replace("_todos", "") if "_todos" in _list_type else _list_type
                        async for _pdb in _get_db_prefill():
                            _lc = await _pdb.execute(
                                """SELECT id FROM lists
                                   WHERE list_type = ? AND deleted = 0
                                     AND (visibility = 'family' OR user_id = ?)
                                   ORDER BY updated_at DESC LIMIT 1""",
                                (_norm_lt, effective_user),
                            )
                            _lr = await _lc.fetchone()
                            if _lr:
                                _ic = await _pdb.execute(
                                    """SELECT text FROM list_items
                                       WHERE list_id = ? AND deleted = 0 AND completed = 0
                                       ORDER BY sort_order, created_at""",
                                    (_lr["id"],),
                                )
                                _irs = await _ic.fetchall()
                                _existing_list_items = [
                                    r["text"] for r in _irs if r and r["text"]
                                ]
                            break
                    except Exception as _pf_exc:
                        logger.debug("voice list prefill fetch failed (non-fatal): %s", _pf_exc)

                    # Place the new item first, then existing items (deduped).
                    if _item_text:
                        _prefill_items = [_item_text] + [
                            i for i in _existing_list_items if i != _item_text
                        ]
                    else:
                        _prefill_items = list(_existing_list_items)

                    _list_form_data = {
                        "list_name": _list_type,
                        "items": _prefill_items,
                        "item": _item_text,
                        # Flag so confirm endpoint knows which item is the new addition.
                        "new_item": _item_text,
                    }
                    reply_text = (
                        f"I've added {_item_text} to your list." if _item_text
                        else "Here's your shopping list."
                    ) + " Check the panel and tap Done when you're finished."
                    await _broadcast_action_form_panel(
                        panel_id=panel_id,
                        panel_type="shopping_list",
                        data=_list_form_data,
                        title=_list_title,
                        turn_key=_turn_key,
                    )
                    _list_audio = await synthesize({"text": reply_text}, caller=caller)
                    await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
                    _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_list_audio.body).decode("ascii"),
                        "content_type": _list_audio.media_type,
                        "intent": "list_add",
                        "action_panel": "shopping_list",
                    }
                except Exception as _list_exc:
                    logger.warning("voice/command list_add panel failed: %s", _list_exc)
        if _quick_intent and _quick_intent.name == "calendar_create":
            # Show the interactive action-form panel so the user can verify / edit
            # the extracted slots before the event is created. The panel's Confirm
            # button POSTs to /api/ui/panel/form/confirm which executes the intent.
            _allow_quick_calendar = True
            try:
                from voice_scope import classify as _vscope_cal
                _cal_scope = _vscope_cal(text, _quick_intent)
                if _cal_scope.scope == "user_scoped" and not _has_scope_identity:
                    _allow_quick_calendar = False
            except Exception:
                pass
            if _allow_quick_calendar:
                try:
                    import datetime as _dt
                    from intent_router import _parse_date, _parse_time
                    slots = _quick_intent.slots or {}
                    _date_raw = slots.get("date", "")
                    _time_raw = slots.get("time", "")
                    _parsed_date = (_parse_date(_date_raw) if _date_raw else None) or _dt.date.today().isoformat()
                    _parsed_time = (_parse_time(_time_raw) if _time_raw else "") or ""
                    _cal_form_data = {
                        "title": slots.get("title") or slots.get("event") or "",
                        "date": _parsed_date,
                        "time": _parsed_time,
                        "duration": slots.get("duration") or "",
                        "category": slots.get("category") or "general",
                        "location": slots.get("location") or "",
                        "notes": slots.get("notes") or "",
                    }
                    reply_text = "Here's your calendar event — check the details and tap Confirm to save."
                    await _broadcast_action_form_panel(
                        panel_id=panel_id,
                        panel_type="calendar_event",
                        data=_cal_form_data,
                        title="New Calendar Event",
                        turn_key=_turn_key,
                    )
                    _cal_audio = await synthesize({"text": reply_text}, caller=caller)
                    await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
                    _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_cal_audio.body).decode("ascii"),
                        "content_type": _cal_audio.media_type,
                        "intent": "calendar_create",
                        "action_panel": "calendar_event",
                    }
                except Exception as _cal_exc:
                    logger.warning("voice/command calendar_create panel failed: %s", _cal_exc)
        if _quick_intent and _quick_intent.name == "reminder_create":
            _allow_quick_reminder = True
            try:
                from voice_scope import classify as _vscope_rem
                _rem_scope = _vscope_rem(text, _quick_intent)
                if _rem_scope.scope == "user_scoped" and not _has_scope_identity:
                    _allow_quick_reminder = False
            except Exception:
                pass
            if _allow_quick_reminder:
                try:
                    from intent_router import execute_intent as _exec_rem
                    _rem_reply = await _exec_rem(_quick_intent, effective_user)
                    reply_text = _rem_reply or "I set that reminder."
                    await _broadcast_reminder_ui(panel_id=panel_id, summary=reply_text, turn_key=_turn_key)
                    _rem_audio = await synthesize({"text": reply_text}, caller=caller)
                    await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
                    _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))
                    return {
                        "ok": True,
                        "panel_id": panel_id,
                        "reply": reply_text,
                        "audio_base64": base64.b64encode(_rem_audio.body).decode("ascii"),
                        "content_type": _rem_audio.media_type,
                        "intent": "reminder_create",
                    }
                except Exception as _rem_exc:
                    logger.warning("voice/command quick reminder_create failed: %s", _rem_exc)
        if _quick_intent and _quick_intent.name in _CONFIRM_INTENTS:
            slots = _quick_intent.slots or {}
            # Build a human-readable confirmation phrase.
            _phrases = {
                "calendar_create": lambda s: f"Add a calendar event: {s.get('title', 'untitled')}"
                    + (f" on {s.get('date', '')}" if s.get("date") else "")
                    + (f" at {s.get('time', '')}" if s.get("time") else "")
                    + ". Shall I confirm?",
                "list_add": lambda s: f"Add '{s.get('item', 'item')}' to "
                    + ("your shopping list" if s.get("list_type") == "shopping"
                       else f"your {s.get('list_type', 'shopping').replace('_', ' ')} list")
                    + ". Shall I confirm?",
                "reminder_create": lambda s: f"Set a reminder: {s.get('title', s.get('text', 'reminder'))}. Shall I confirm?",
                "note_create": lambda s: "Create a new note. Shall I confirm?",
                "journal_create": lambda s: "Create a journal entry. Shall I confirm?",
                "transaction_create": lambda s: f"Record a transaction of {s.get('amount', '')}. Shall I confirm?",
                "people_create": lambda s: f"Add contact {s.get('name', '')}. Shall I confirm?",
            }
            _gen = _phrases.get(_quick_intent.name)
            confirm_phrase = _gen(slots) if _gen else f"Confirm this action: {_quick_intent.name}?"
            _PENDING_CONFIRMATIONS[panel_id] = {
                "intent_name": _quick_intent.name,
                "slots": slots,
                "expire_at": time.monotonic() + _CONFIRM_TIMEOUT_S,
                "session_id": session_id,
                "user_id": effective_user,
            }
            try:
                audio_resp = await synthesize({"text": confirm_phrase}, caller=caller)
                audio_b64_confirm = base64.b64encode(audio_resp.body).decode("ascii")
                ct_confirm = audio_resp.media_type
            except Exception:
                audio_b64_confirm = None
                ct_confirm = "audio/wav"
            try:
                from push import broadcaster as _bc_pend
                await _bc_pend.broadcast("all", "voice:responding", {
                    "panel_id": panel_id,
                    "text": confirm_phrase[:200],
                })
                await _bc_pend.broadcast("all", "voice:done", {"panel_id": panel_id})
            except Exception:
                pass
            return {"ok": True, "panel_id": panel_id, "reply": confirm_phrase,
                    "pending_confirmation": True,
                    "audio_base64": audio_b64_confirm, "content_type": ct_confirm}
    except Exception as exc:
        logger.debug("voice confirmation pre-check failed (non-fatal): %s", exc)

    # ── Pass 2e A7: public-intent short-circuit ─────────────────────────────
    # If intent_router already has a direct answer (time, date, timer, recipe)
    # we skip the LLM entirely — TTS the answer straight from execute_intent.
    # This gives <=500ms first-audio-byte for the most common "quick query" phrases.
    from guest_policy import (
        PUBLIC_HOUSEHOLD_INTENTS as _PUBLIC_VOICE_INTENTS,
        can_use_voice_intent as _can_use_voice_intent,
        record_policy_decision as _record_guest_policy,
    )
    _voice_policy_user = {
        "user_id": _scope_identity_user or "guest",
        "role": "user" if _has_scope_identity else "guest",
    }
    try:
        from intent_router import detect_intent as _detect_pub, execute_intent as _exec_pub
        _pub_intent = _detect_pub(text, log_miss=False)
        if _pub_intent and _pub_intent.name in _PUBLIC_VOICE_INTENTS:
            # Detached-task safety (see identity block above): run the auth check on a
            # dedicated connection, never the released request `db`. Fail closed.
            try:
                from db_pool import get_db_ctx as _get_db_ctx
                async with _get_db_ctx() as _authconn:
                    _pub_allowed = await _can_use_voice_intent(_authconn, _voice_policy_user, _pub_intent.name)
            except Exception:
                # Dedicated conn unavailable: on the awaited path the request db is valid;
                # on the detached path it fails → fail closed (deny).
                try:
                    _pub_allowed = await _can_use_voice_intent(db, _voice_policy_user, _pub_intent.name)
                except Exception:
                    logger.warning("voice auth (public) failed on both conns intent=%s", _pub_intent.name)
                    _pub_allowed = False
            if not _pub_allowed:
                _record_guest_policy(
                    "blocked",
                    surface="voice",
                    resource="intent_fast",
                    action=_pub_intent.name,
                )
                _blocked_phrase = "That request is not available in the current role."
                _blocked_audio = await synthesize({"text": _blocked_phrase}, caller=caller)
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _blocked_phrase,
                    "audio_base64": base64.b64encode(_blocked_audio.body).decode("ascii"),
                    "content_type": _blocked_audio.media_type,
                    "status": "role_blocked",
                }
            _pub_reply = await _exec_pub(_pub_intent, effective_user if effective_user != "family-admin" else "guest")
            if _pub_reply:
                if _pub_intent.name == "weather":
                    await _broadcast_weather_ui(panel_id, _pub_reply, turn_key=_turn_key)
                if _pub_intent.name in {"calendar_show", "daily_briefing"}:
                    await _broadcast_calendar_ui(panel_id, _pub_reply, turn_key=_turn_key)
                if _pub_intent.name == "lets_talk":
                    await _broadcast_lets_talk_ui(panel_id, turn_key=_turn_key)
                try:
                    from voice_metrics import voice_stage_seconds, voice_turn_count, voice_intent_hit_count
                    voice_stage_seconds.labels(stage="llm_first_token").observe(0.0)
                    voice_intent_hit_count.labels(intent=_pub_intent.name).inc()
                    voice_turn_count.labels(outcome="ok", path="intent_fast").inc()
                except Exception:
                    pass
                try:
                    from push import broadcaster as _bc_pub
                    await _bc_pub.broadcast("all", "voice:responding", {"panel_id": panel_id, "text": _pub_reply[:200]})
                except Exception:
                    pass
                _pub_audio_resp = await synthesize({"text": _pub_reply}, caller=caller)
                _pub_audio_b64 = base64.b64encode(_pub_audio_resp.body).decode("ascii")
                try:
                    from push import broadcaster as _bc_pub2
                    await _bc_pub2.broadcast("all", "voice:done", {"panel_id": panel_id})
                except Exception:
                    pass
                try:
                    from voice_metrics import voice_stage_seconds
                    voice_stage_seconds.labels(stage="tts_first_byte").observe(time.monotonic() - _t_cmd_start)
                    voice_stage_seconds.labels(stage="total").observe(time.monotonic() - _t_cmd_start)
                except Exception:
                    pass
                _record_guest_policy(
                    "guest_allowed" if not _has_scope_identity else "auth_ok",
                    surface="voice",
                    resource="intent_fast",
                    action=_pub_intent.name,
                )
                await _schedule_voice_chat_save(session_id, text, _pub_reply, effective_user)
                _spawn_bg(_run_voice_memory_passes(text, _pub_reply, effective_user, session_id))
                return {
                    "ok": True, "panel_id": panel_id,
                    "reply": _pub_reply,
                    "audio_base64": _pub_audio_b64,
                    "content_type": _pub_audio_resp.media_type,
                    "intent": _pub_intent.name,
                }
    except Exception as _pub_exc:
        logger.warning("voice/command public-intent check failed: %s", _pub_exc)

    # Weather fallback: if public-intent short-circuit failed for any reason,
    # still resolve weather deterministically instead of dropping to generic LLM text.
    try:
        from intent_router import detect_intent as _detect_weather_fb, execute_intent as _exec_weather_fb
        _weather_fb_intent = _detect_weather_fb(text, log_miss=False)
        if _weather_fb_intent and _weather_fb_intent.name == "weather":
            _weather_fb_reply = await _exec_weather_fb(
                _weather_fb_intent,
                effective_user if effective_user != "family-admin" else "guest",
            )
            if _weather_fb_reply:
                await _broadcast_weather_ui(panel_id, _weather_fb_reply, turn_key=_turn_key)
                _weather_fb_audio = await synthesize({"text": _weather_fb_reply}, caller=caller)
                await _schedule_voice_chat_save(session_id, text, _weather_fb_reply, effective_user)
                _spawn_bg(_run_voice_memory_passes(text, _weather_fb_reply, effective_user, session_id))
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _weather_fb_reply,
                    "audio_base64": base64.b64encode(_weather_fb_audio.body).decode("ascii"),
                    "content_type": _weather_fb_audio.media_type,
                    "intent": "weather",
                }
    except Exception as _weather_fb_exc:
        logger.warning("voice/command weather fallback failed: %s", _weather_fb_exc)

    # ── Tier-1.5: router → domain-expert dispatch ────────────────────────────
    # Every deterministic fast-path above MISSED. If the semantic router was
    # confident about a domain, fulfill it via the existing handlers instead of
    # dropping to the slow general brain. Shadow by default (logs, returns None);
    # only ZOE_EXPERT_MODE=active + an allow-listed domain actually fulfills.
    try:
        import fast_tiers as _fp
        # Channel-agnostic deterministic core (shared with chat/LiveKit/Telegram).
        # channel="voice" runs the shared Tier-0 read shortcut, but only for the
        # public/idempotent reads (weather/time/date/list/calendar); user-scoped
        # reads are deferred (profile `tier0_defer_intents`) so the B3/B4 scope gate
        # below stays authoritative. The richer public-intent path above already
        # caught the household-safe reads, so Tier-0 here mainly backstops them.
        # Reuse the router decision from the shadow log and keep the dispatch ctx
        # identical via extra_ctx (db/panel_id).
        # NOTE: extra_ctx["db"] is inert — fast_tiers.dispatch never reads it and the
        # downstream execute_intent/store_fact open their own connections (audited
        # 2026-07-09). Left as-is (harmless dead-pass); do NOT rely on it for queries.
        _xresult = await _fp.resolve(
            text, effective_user, session_id,
            channel="voice",
            router_decision=_router_decision,
            extra_ctx={"db": db, "panel_id": panel_id},
        )
        if _xresult is not None:
            reply_text = _xresult.reply
            _ui = (_xresult.ui or {}).get("kind")
            try:
                if _ui == "weather":
                    await _broadcast_weather_ui(panel_id, reply_text, turn_key=_turn_key)
                elif _ui == "calendar":
                    await _broadcast_calendar_ui(panel_id, reply_text, turn_key=_turn_key)
                elif _ui == "reminder":
                    await _broadcast_reminder_ui(panel_id=panel_id, summary=reply_text, turn_key=_turn_key)
            except Exception:
                pass
            _xaudio_b64: Optional[str] = None
            _xct = "audio/wav"
            if not stream:
                _xaudio = await synthesize({"text": reply_text}, caller=caller)
                _xaudio_b64 = base64.b64encode(_xaudio.body).decode("ascii")
                _xct = _xaudio.media_type
            try:
                from push import broadcaster as _bc_xd
                await _bc_xd.broadcast("all", "voice:responding", {"panel_id": panel_id, "text": reply_text[:200]})
                await _bc_xd.broadcast("all", "voice:done", {"panel_id": panel_id})
            except Exception:
                pass
            await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
            _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))
            return {
                "ok": True, "panel_id": panel_id, "reply": reply_text,
                "audio_base64": _xaudio_b64, "content_type": _xct,
                "intent": f"expert:{_xresult.domain}:{_xresult.intent}",
            }
    except Exception as _xd_exc:
        logger.warning("voice/command expert dispatch failed (non-fatal): %s", _xd_exc)

    # ── Pass 3 B3/B4: scope gate + PIN challenge ─────────────────────────────
    # Check if this utterance needs a known user. If so, and we have none,
    # challenge via panel_auth rather than leaking personal data to guest.
    _ident_for_scope = _scope_identity_user
    if os.environ.get("ZOE_VOICE_IDENT", "").strip() in ("1", "true"):
        try:
            from voice_scope import classify as _vscope, ScopeDecision as _SD
            from intent_router import detect_intent as _detect_scope
            _scope_intent = _detect_scope(text) if "_pub_intent" not in dir() else locals().get("_pub_intent")
            _scope: _SD = _vscope(text, _scope_intent)
            _scope_action = _scope.intent_name or "text"
            _scope_allowed = True
            if _scope.intent_name:
                # Detached-task safety: dedicated connection, not the released request db.
                try:
                    from db_pool import get_db_ctx as _get_db_ctx
                    async with _get_db_ctx() as _scopeconn:
                        _scope_allowed = await _can_use_voice_intent(_scopeconn, _voice_policy_user, _scope.intent_name)
                except Exception:
                    # Dedicated conn unavailable: request db (valid on the awaited path);
                    # else fail closed.
                    try:
                        _scope_allowed = await _can_use_voice_intent(db, _voice_policy_user, _scope.intent_name)
                    except Exception:
                        logger.warning("voice auth (scope) failed on both conns intent=%s", _scope.intent_name)
                        _scope_allowed = False
            logger.info(
                "voice/scope panel=%s scope=%s intent=%s allowed=%s ident=%s policy_role=%s",
                panel_id, _scope.scope, _scope.intent_name, _scope_allowed,
                _ident_for_scope, _voice_policy_user.get("role"),
            )
            if _scope.intent_name and not _scope_allowed and not (_scope.scope == "user_scoped" and not _ident_for_scope):
                _record_guest_policy(
                    "blocked",
                    surface="voice",
                    resource="scope_gate",
                    action=_scope_action,
                )
                _blocked_phrase = "That request is blocked for this role. Please sign in with an allowed profile."
                _blocked_audio = await synthesize({"text": _blocked_phrase}, caller=caller)
                return {
                    "ok": True,
                    "panel_id": panel_id,
                    "reply": _blocked_phrase,
                    "status": "role_blocked",
                    "audio_base64": base64.b64encode(_blocked_audio.body).decode("ascii"),
                    "content_type": _blocked_audio.media_type,
                }
            if _scope.scope == "user_scoped" and not _ident_for_scope:
                _record_guest_policy(
                    "auth_required",
                    surface="voice",
                    resource="scope_gate",
                    action=_scope_action,
                )
                return await _request_voice_identity_challenge(
                    panel_id=panel_id,
                    text=text,
                    session_id=session_id,
                    caller=caller,
                    reason="Voice command needs identity. Enter your PIN to continue.",
                )
            _record_guest_policy(
                "guest_allowed" if not _ident_for_scope else "auth_ok",
                surface="voice",
                resource="scope_gate",
                action=_scope_action,
            )
        except Exception as _scope_exc:
            logger.debug("voice/command scope gate failed (non-fatal): %s", _scope_exc)

    # Forward to chat pipeline.
    reply_text = ""
    audio_b64: Optional[str] = None
    content_type = "audio/wav"

    try:
        t_chat_start = time.monotonic()

        # ── A4 (Pass 2c): in-process streaming + F3 identity fix ──────────
        # Previously we did an httpx.post -> /api/chat/?stream=false, which
        #   (a) paid TCP+JSON serialization cost twice,
        #   (b) silently dropped `identified_user_id` because chat.py never
        #       reads that field (Pass 0 finding), causing every identified
        #       turn to resolve to the device-token's user (family-admin).
        # We now call run_zoe_agent_streaming directly:
        #   * user_id is passed explicitly -> memory writes, MemPalace reads,
        #     transaction rows, etc. all land under the right user.
        #   * token_budget is the same tight voice budget.
        #   * Streaming tokens are accumulated so that Pass 2b can hand the
        #     sentence-buffered stream to Kokoro.create_stream() and bring
        #     first-audio-byte latency down further.
        from brain_dispatch import brain_streaming  # zoe-core by default

        voice_timeout = float(os.environ.get("ZOE_VOICE_CHAT_TIMEOUT_S", "20"))
        try:
            _hermes_cap = float(os.environ.get("ZOE_VOICE_HERMES_TIMEOUT_S", str(voice_timeout)))
        except Exception:
            _hermes_cap = voice_timeout
        hermes_voice_timeout = max(5.0, min(voice_timeout, _hermes_cap))
        _t_first_token: Optional[float] = None

        if stream:
            # Stream mode: emit chunks as soon as sentence boundaries appear.
            async def _generate_voice_stream():
                from push import broadcaster as _bc_stream
                import json as _json

                nonlocal _t_first_token

                chunk_index = 0
                token_buf = ""
                full_reply_parts: list[str] = []
                _t_first_audio: Optional[float] = None
                _filler_emitted = False  # at most one tool-turn filler per turn
                # The processing-ack path yields audio bytes directly (without going
                # through _emit_sentence), so _t_first_audio stays None even though
                # the user already heard audio — track it separately.
                _processing_ack_audio_sent = False

                try:
                    async def _emit_sentence(sentence: str, *, record: bool = True):
                        nonlocal chunk_index, _t_first_audio
                        s = sentence.strip()
                        if not s:
                            return
                        # record=False for the leading tool filler: it's spoken but
                        # must NOT join the persisted/displayed answer (it's an
                        # acknowledgement, not part of what Zoe actually answered).
                        if record:
                            full_reply_parts.append(s)
                        await _bc_stream.broadcast("all", "voice:responding", {
                            "panel_id": panel_id,
                            "text": s[:200],
                        })

                        # Prefer kokoro-sidecar (natural GPU voice); fall back to Kokoro stream
                        wav_bytes = await _synthesize_kokoro_sidecar(s)
                        _tts_provider = "kokoro-sidecar"
                        if wav_bytes:
                            if _t_first_audio is None:
                                _t_first_audio = time.monotonic() - t_chat_start
                                try:
                                    from voice_metrics import voice_stage_seconds
                                    voice_stage_seconds.labels(stage="tts_first_byte").observe(_t_first_audio)
                                except Exception:
                                    pass
                            header = _json.dumps({
                                "chunk": chunk_index,
                                "text": s[:80],
                                "provider": _tts_provider,
                            })
                            yield (header + "\n").encode()
                            yield base64.b64encode(wav_bytes) + b"\n"
                            chunk_index += 1
                            return

                        sent_any = False
                        async for wav_bytes in _stream_kokoro_sentence_wavs(s):
                            if _t_first_audio is None:
                                _t_first_audio = time.monotonic() - t_chat_start
                                try:
                                    from voice_metrics import voice_stage_seconds
                                    voice_stage_seconds.labels(stage="tts_first_byte").observe(_t_first_audio)
                                except Exception:
                                    pass
                            header = _json.dumps({
                                "chunk": chunk_index,
                                "text": s[:80],
                                "provider": "kokoro-onnx-stream",
                            })
                            yield (header + "\n").encode()
                            yield base64.b64encode(wav_bytes) + b"\n"
                            chunk_index += 1
                            sent_any = True

                        if sent_any:
                            return
                        # Fallback if stream synthesis unavailable.
                        audio_resp = await synthesize({"text": s}, caller=caller)
                        if _t_first_audio is None:
                            _t_first_audio = time.monotonic() - t_chat_start
                            try:
                                from voice_metrics import voice_stage_seconds
                                voice_stage_seconds.labels(stage="tts_first_byte").observe(_t_first_audio)
                            except Exception:
                                pass
                        header = _json.dumps({
                            "chunk": chunk_index,
                            "text": s[:80],
                            "provider": audio_resp.headers.get("X-Zoe-TTS-Provider", "fallback"),
                        })
                        yield (header + "\n").encode()
                        yield base64.b64encode(audio_resp.body) + b"\n"
                        chunk_index += 1

                    async def _emit_line(line: dict):
                        yield (_json.dumps(line) + "\n").encode()

                    try:
                        from voice_presence import processing_ack_event

                        ack_event = processing_ack_event()
                        if ack_event:
                            ack_text = str(ack_event.get("text") or "").strip()
                            if ack_text:
                                await _bc_stream.broadcast("all", "voice:responding", {
                                    "panel_id": panel_id,
                                    "text": ack_text,
                                    "processing_ack": True,
                                })
                            if ack_event.get("audio_base64"):
                                header = _json.dumps({
                                    "chunk": chunk_index,
                                    "text": ack_text[:80],
                                    "provider": str(ack_event.get("audio_source") or "cached-processing-ack"),
                                    "processing_ack": True,
                                })
                                yield (header + "\n").encode()
                                yield str(ack_event["audio_base64"]).encode() + b"\n"
                                chunk_index += 1
                                _processing_ack_audio_sent = True
                            else:
                                async for out_line in _emit_line({"processing_ack": True, "text": ack_text, "panel_id": panel_id}):
                                    yield out_line
                    except Exception as ack_exc:
                        logger.debug("voice/command stream processing acknowledgement failed: %s", ack_exc)

                    _voice_history = await _load_voice_history(session_id, limit=3)
                    _v_db_memory, _v_portrait = await _voice_brain_memory(effective_user, text)
                    _v_domain_ctx = await _voice_domain_context(_router_decision, effective_user)
                    async for delta in brain_streaming(
                        text, session_id, user_id=effective_user,
                        voice_mode=True, history=_voice_history or None,
                        db_memory_context=_merge_brain_context(_v_db_memory, _v_domain_ctx),
                        portrait=_v_portrait,
                    ):
                        if not delta:
                            continue
                        # Brain "what I'm doing" sentinels ride alongside the spoken
                        # stream — they must NEVER be synthesized. On the FIRST
                        # tool_call (phase=start), if the brain hasn't spoken anything
                        # yet, emit ONE short generic filler so first-audio comes fast
                        # instead of sitting silent while the tool runs + the answer
                        # synthesizes. Skip the filler when the user already heard (or
                        # is about to hear) real content: audio was synthesized
                        # (_t_first_audio), brain text is buffering toward a sentence
                        # boundary (token_buf), or the cached processing-ack already
                        # played audio — any of those would make it a double lead-in.
                        if delta.startswith(_VOICE_TOOL_SENTINEL_PREFIXES):
                            if (
                                not _filler_emitted
                                and _t_first_audio is None
                                and not token_buf
                                and not _processing_ack_audio_sent
                                and _voice_tool_filler_enabled()
                            ):
                                _tool_name = _voice_tool_name_from_sentinel(delta)
                                if _tool_name is not None:
                                    _filler_emitted = True
                                    async for out_chunk in _emit_sentence(
                                        _voice_tool_filler(_tool_name), record=False
                                    ):
                                        yield out_chunk
                            continue
                        if delta.startswith(_VOICE_ESCALATION_MARKERS):
                            try:
                                is_bg, reason, hermes_prompt = _parse_voice_escalation_delta(delta, text)
                                logger.info("voice/command stream escalation -> Hermes background=%s reason=%s", is_bg, reason or "unspecified")
                                if is_bg:
                                    from background_runner import enqueue_background_task
                                    _spawn_bg(enqueue_background_task(hermes_prompt, effective_user, session_id))
                                    delta = "I'll work on that in the background and let you know when it's done."
                                else:
                                    # Voice foreground turns should answer aloud when possible;
                                    # long-running work still uses the explicit background marker.
                                    try:
                                        await _bc_stream.broadcast("all", "voice:responding", {
                                            "panel_id": panel_id,
                                            "text": "Give me a second - this one may take a little longer. I will come back with the result.",
                                        })
                                    except Exception:
                                        pass
                                    delta = (
                                        await asyncio.wait_for(
                                            _run_hermes_voice_escalation(hermes_prompt, session_id, effective_user),
                                            timeout=hermes_voice_timeout,
                                        )
                                    ).strip()
                                    if not delta:
                                        continue
                            except Exception as esc_exc:
                                logger.warning("voice/command Hermes escalation failed: %s", esc_exc)
                                delta = "I couldn't complete that advanced request right now. Please try again."
                        if _t_first_token is None:
                            _t_first_token = time.monotonic() - t_chat_start
                            try:
                                from voice_metrics import voice_stage_seconds
                                voice_stage_seconds.labels(stage="llm_first_token").observe(_t_first_token)
                            except Exception:
                                pass
                        token_buf += delta
                        # Snap the FIRST audio out on a short clause so the reply
                        # starts almost immediately instead of after a full sentence.
                        if _t_first_audio is None and _fast_first_audio_enabled():
                            first_unit, token_buf = _extract_first_unit(token_buf)
                            if first_unit:
                                async for out_chunk in _emit_sentence(first_unit):
                                    yield out_chunk
                        ready, token_buf = _extract_complete_sentences(token_buf)
                        for sentence in ready:
                            async for out_chunk in _emit_sentence(sentence):
                                yield out_chunk

                    if token_buf.strip():
                        async for out_chunk in _emit_sentence(token_buf):
                            yield out_chunk
                        token_buf = ""

                    final_reply = " ".join(part.strip() for part in full_reply_parts if part.strip()).strip()
                    try:
                        await _bc_stream.broadcast("all", "ui_action", {
                            "action": {
                                "id": f"voice_card_{panel_id}",
                                "action_type": "show_card",
                                "payload": {
                                    "type": "answer",
                                    "data": {"text": final_reply[:300]},
                                    "panel_id": panel_id,
                                },
                            }
                        })
                    except Exception:
                        pass
                    try:
                        await _bc_stream.broadcast("all", "voice:done", {"panel_id": panel_id})
                    except Exception:
                        pass
                    try:
                        from voice_metrics import voice_stage_seconds, voice_turn_count
                        if _t_turn_start is None:
                            voice_stage_seconds.labels(stage="total").observe(time.monotonic() - _t_cmd_start)
                        voice_turn_count.labels(outcome="ok", path="command").inc()
                    except Exception:
                        pass
                    async for out_line in _emit_line({"done": True, "reply": final_reply, "panel_id": panel_id}):
                        yield out_line
                except asyncio.TimeoutError:
                    async for out_line in _emit_line({"error": "voice command stream timeout"}):
                        yield out_line
                except Exception as exc:
                    logger.warning("voice/command stream error: %s", exc)
                    async for out_line in _emit_line({"error": "voice command stream failure"}):
                        yield out_line
                finally:
                    # Persist whatever the user actually HEARD — the streaming
                    # lane never reaches voice_command's tail save, and a
                    # client disconnect (GeneratorExit) or mid-stream error
                    # must not drop sentences that were already spoken. Empty
                    # user_text: the user turn is saved pre-stream.
                    heard_reply = " ".join(
                        part.strip() for part in full_reply_parts if part.strip()
                    ).strip()
                    if heard_reply:
                        # Safe during GeneratorExit unwind: neither call suspends
                        # (_schedule_voice_chat_save only spawns a bg task).
                        await _schedule_voice_chat_save(session_id, "", heard_reply, effective_user)
                        _spawn_bg(_run_voice_memory_passes(text, heard_reply, effective_user, session_id))

            return StreamingResponse(
                _generate_voice_stream(),
                media_type="application/x-zoe-audio-stream",
                headers={"Cache-Control": "no-cache"},
            )

        collected: list[str] = []

        _voice_history_nc = await _load_voice_history(session_id, limit=3)
        _v_db_memory_nc, _v_portrait_nc = await _voice_brain_memory(effective_user, text)
        _v_domain_ctx_nc = await _voice_domain_context(_router_decision, effective_user)
        _v_db_memory_nc = _merge_brain_context(_v_db_memory_nc, _v_domain_ctx_nc)

        async def _stream_collect() -> None:
            nonlocal _t_first_token
            async for delta in brain_streaming(
                text,
                session_id,
                user_id=effective_user,
                voice_mode=True,
                history=_voice_history_nc or None,
                db_memory_context=_v_db_memory_nc,
                portrait=_v_portrait_nc,
            ):
                if not delta:
                    continue
                # Drop brain "what I'm doing" sentinels — they ride alongside the
                # stream and must never be collected into the spoken answer (the
                # non-stream path synthesizes the whole reply at once, so there's no
                # leading-audio gap to fill; we only strip the noise here).
                if delta.startswith(_VOICE_TOOL_SENTINEL_PREFIXES):
                    continue
                if delta.startswith(_VOICE_ESCALATION_MARKERS):
                    try:
                        from push import broadcaster as _bc_escalate
                        is_bg, reason, hermes_prompt = _parse_voice_escalation_delta(delta, text)
                        logger.info("voice/command escalation -> Hermes background=%s reason=%s", is_bg, reason or "unspecified")
                        if is_bg:
                            from background_runner import enqueue_background_task
                            _spawn_bg(enqueue_background_task(hermes_prompt, effective_user, session_id))
                            delta = "I'll work on that in the background and let you know when it's done."
                        else:
                            # Voice foreground turns should answer aloud when possible;
                            # long-running work still uses the explicit background marker.
                            try:
                                await _bc_escalate.broadcast("all", "voice:responding", {
                                    "panel_id": panel_id,
                                    "text": "Give me a second - this one may take a little longer. I will come back with the result.",
                                })
                            except Exception:
                                pass
                            delta = (
                                await asyncio.wait_for(
                                    _run_hermes_voice_escalation(hermes_prompt, session_id, effective_user),
                                    timeout=hermes_voice_timeout,
                                )
                            ).strip()
                            if not delta:
                                continue
                    except Exception as esc_exc:
                        logger.warning("voice/command Hermes escalation failed: %s", esc_exc)
                        delta = "I couldn't complete that advanced request right now. Please try again."
                if _t_first_token is None:
                    _t_first_token = time.monotonic() - t_chat_start
                    try:
                        from voice_metrics import voice_stage_seconds
                        voice_stage_seconds.labels(stage="llm_first_token").observe(_t_first_token)
                    except Exception:
                        pass
                collected.append(delta)

        _llm_timed_out = False
        try:
            await asyncio.wait_for(_stream_collect(), timeout=voice_timeout)
        except asyncio.TimeoutError:
            logger.warning("voice/command LLM stream timeout after %.1fs", voice_timeout)
            _llm_timed_out = True

        reply_text = "".join(collected).strip()
        _t_llm_total = time.monotonic() - t_chat_start

        logger.info(
            "voice/command LLM first_token=%.2fs total=%.2fs reply=%d chars user=%s",
            (_t_first_token if _t_first_token is not None else -1.0),
            _t_llm_total, len(reply_text), effective_user,
        )

        # ── Fix 3: If an action form is still open, try to extract field-fill
        # events from the LLM response text and broadcast them to the panel.
        # This handles utterances that fell through field-NLU but whose intent
        # the LLM successfully interpreted (e.g. "change the time to three pm").
        if reply_text:
            try:
                from panel_form_state import get_active_form as _get_af_llm
                _llm_active_form = _get_af_llm(panel_id)
                if _llm_active_form:
                    _llm_form_type = _llm_active_form.get("panel_type", "")
                    _llm_field_updates = _parse_voice_form_field(reply_text, _llm_form_type)
                    if _llm_field_updates:
                        try:
                            from push import broadcaster as _bc_llm_ff
                            for _lf_name, _lf_val in _llm_field_updates.items():
                                _lf_action_type = (
                                    "panel_list_update"
                                    if _llm_form_type == "shopping_list"
                                    and _lf_name in ("add", "remove")
                                    else "panel_update_field"
                                )
                                _lf_payload: dict = {"panel_id": panel_id}
                                if _lf_action_type == "panel_list_update":
                                    _lf_payload["op"] = _lf_name
                                    _lf_payload["item"] = _lf_val
                                else:
                                    _lf_payload["field"] = _lf_name
                                    _lf_payload["value"] = _lf_val
                                await _bc_llm_ff.broadcast("all", "ui_action", {
                                    "action": {
                                        "id": f"llm_field_{panel_id}_{_lf_name}_{_turn_key}",
                                        "action_type": _lf_action_type,
                                        "payload": _lf_payload,
                                    }
                                })
                        except Exception as _llm_ff_exc:
                            logger.debug("LLM form field broadcast failed (non-fatal): %s", _llm_ff_exc)
                        # Persist field changes into in-memory slots.
                        try:
                            _llm_slots_ref = _llm_active_form.setdefault("slots", {})
                            for _lsf_n, _lsf_v in _llm_field_updates.items():
                                if _llm_form_type == "shopping_list":
                                    _llm_slot_items = _llm_slots_ref.setdefault("items", [])
                                    if _lsf_n == "add" and _lsf_v not in _llm_slot_items:
                                        _llm_slot_items.append(_lsf_v)
                                    elif _lsf_n == "remove" and _lsf_v in _llm_slot_items:
                                        _llm_slot_items.remove(_lsf_v)
                                else:
                                    _llm_slots_ref[_lsf_n] = _lsf_v
                        except Exception:
                            pass
                        # Replace spoken reply with concise field-update confirmation.
                        _llm_ff_parts = []
                        for _lf_n, _lf_v in _llm_field_updates.items():
                            if _lf_n == "add":
                                _llm_ff_parts.append(f"Added {_lf_v} to the list.")
                            elif _lf_n == "remove":
                                _llm_ff_parts.append(f"Removed {_lf_v}.")
                            elif _lf_n == "title":
                                _llm_ff_parts.append(f"Title set to {_lf_v}.")
                            elif _lf_n == "date":
                                _llm_ff_parts.append(f"Date updated.")
                            elif _lf_n == "time":
                                _llm_ff_parts.append(f"Time set to {_lf_v}.")
                            elif _lf_n == "location":
                                _llm_ff_parts.append(f"Location set to {_lf_v}.")
                            else:
                                _llm_ff_parts.append("Got it, updated.")
                        if _llm_ff_parts:
                            reply_text = " ".join(_llm_ff_parts)
            except Exception as _fix3_outer:
                logger.debug("LLM form field routing failed (non-fatal): %s", _fix3_outer)

    except Exception as exc:
        logger.error("voice/command chat error: %s", exc)
        try:
            from voice_metrics import voice_failure_reason_count
            voice_failure_reason_count.labels(path="command", reason="llm_error").inc()
        except Exception:
            pass
        audio_b64, content_type = await _make_fallback_audio()
        try:
            from push import broadcaster as _bc_err
            await _bc_err.broadcast("all", "voice:done", {"panel_id": panel_id})
        except Exception:
            pass
        return {
            "ok": False,
            "panel_id": panel_id,
            "reply": _FALLBACK_PHRASE,
            "audio_base64": audio_b64,
            "content_type": content_type,
        }

    degraded_reason: Optional[str] = None
    if not reply_text:
        degraded_reason = "llm_timeout" if _llm_timed_out else "llm_empty"
        reply_text = _FALLBACK_PHRASE
        audio_b64, content_type = await _make_fallback_audio()
        logger.warning("voice/command degraded fallback reason=%s panel=%s", degraded_reason, panel_id)

    # Fire broadcasts and TTS concurrently — TTS is the slow path (~1-2s).
    if reply_text:
        async def _broadcast_response():
            try:
                from push import broadcaster as _bc2
                await _bc2.broadcast("all", "voice:responding", {
                    "panel_id": panel_id,
                    "text": reply_text[:200],
                })
                await _bc2.broadcast("all", "ui_action", {
                    "action": {
                        "id": f"voice_card_{panel_id}",
                        "action_type": "show_card",
                        "payload": {
                            "type": "answer",
                            "data": {"text": reply_text[:300]},
                            "panel_id": panel_id,
                        },
                    }
                })
                if _should_handoff_calendar(text, reply_text, _quick_intent_name):
                    await _broadcast_calendar_ui(panel_id, reply_text, turn_key=_turn_key)
            except Exception:
                pass

        async def _run_tts():
            nonlocal audio_b64, content_type, degraded_reason
            if degraded_reason and audio_b64:
                return
            try:
                t0 = time.monotonic()
                audio_resp = await synthesize({"text": reply_text}, caller=caller)
                audio_b64 = base64.b64encode(audio_resp.body).decode("ascii")
                content_type = audio_resp.media_type
                _tts_elapsed = time.monotonic() - t0
                try:
                    # Pre-streaming: this is full TTS duration. Post-A3 it becomes
                    # first-audio-byte latency.
                    from voice_metrics import voice_stage_seconds
                    voice_stage_seconds.labels(stage="tts_first_byte").observe(_tts_elapsed)
                except Exception:
                    pass
                logger.info("voice/command TTS %.1fs for %d chars", _tts_elapsed, len(reply_text))
            except Exception as exc:
                logger.warning("voice/command TTS failed: %s", exc)
                audio_b64, content_type = await _make_fallback_audio()
                if not degraded_reason:
                    degraded_reason = "tts_failed"

        await asyncio.gather(_broadcast_response(), _run_tts())

    # Broadcast done so orb returns to idle.
    try:
        from push import broadcaster as _bc3
        await _bc3.broadcast("all", "voice:done", {"panel_id": panel_id})
    except Exception:
        pass

    # Record command-path total. If /voice/turn forwarded _t_turn_start we use
    # that so "total" is true end-to-end including STT; otherwise we bound it
    # to the command path only so the two labels stay comparable.
    try:
        from voice_metrics import voice_stage_seconds, voice_turn_count, voice_failure_reason_count
        if _t_turn_start is None:
            voice_stage_seconds.labels(stage="total").observe(time.monotonic() - _t_cmd_start)
        voice_turn_count.labels(outcome="degraded" if degraded_reason else "ok", path="command").inc()
        if degraded_reason:
            voice_failure_reason_count.labels(path="command", reason=degraded_reason).inc()
    except Exception:
        pass

    # Persist assistant turn + extract facts from this voice exchange.
    # _schedule_voice_chat_save handles the DB write; _run_voice_memory_passes
    # handles both regex and LLM extraction passes in the background.
    if reply_text and effective_user and effective_user not in ("guest", "voice-daemon"):
        await _schedule_voice_chat_save(session_id, text, reply_text, effective_user)
        _spawn_bg(_run_voice_memory_passes(text, reply_text, effective_user, session_id))

    return {
        "ok": True,
        "panel_id": panel_id,
        "reply": reply_text,
        "audio_base64": audio_b64,
        "content_type": content_type,
    }


@router.post("/turn")
async def voice_turn(payload: dict, caller: dict = Depends(_require_voice_auth), db=Depends(get_db)):
    """Combined STT + LLM + TTS in a single HTTP call.

    Accepts raw audio (base64 WAV), transcribes it, sends the transcript through
    the chat pipeline, synthesizes the reply, and returns everything in one response.
    Eliminates the extra network round-trip of separate /transcribe + /command calls.

    Request: { "audio_base64": "...", "panel_id": "...", "identified_user_id": "..." }
    """
    b64 = str((payload or {}).get("audio_base64") or "").strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    t_turn_start = time.monotonic()

    # t_upload: base64 decode cost is our proxy for payload unpack time.
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc
    t_upload = time.monotonic() - t_turn_start

    suffix = ".wav" if (len(raw) >= 4 and raw[:4] == b"RIFF") else ".raw"

    # ── Phase 1: STT ──
    t_stt_start = time.monotonic()
    duration_s: float | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            wav_path = tmp.name
        duration_s = _wav_duration_seconds(wav_path) if suffix == ".wav" else None
        try:
            transcript = await _transcribe_audio(wav_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
    except Exception as exc:
        t_stt = time.monotonic() - t_stt_start
        _log_voice_stt_sample(
            route="turn",
            panel_id=panel_id,
            audio_bytes=len(raw),
            suffix=suffix,
            duration_seconds=duration_s,
            stt_seconds=t_stt,
            error=str(exc),
        )
        logger.error("voice/turn STT failed: %s", exc)
        try:
            from voice_metrics import voice_turn_count, voice_failure_reason_count
            voice_turn_count.labels(outcome="error", path="turn").inc()
            voice_failure_reason_count.labels(path="turn", reason="stt_error").inc()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc

    t_stt = time.monotonic() - t_stt_start

    # Record upload + STT histograms right away so they're observable even
    # if the turn aborts later.
    try:
        from voice_metrics import voice_stage_seconds
        voice_stage_seconds.labels(stage="upload").observe(t_upload)
        voice_stage_seconds.labels(stage="stt").observe(t_stt)
    except Exception:
        pass

    transcript = transcript.strip()
    _log_voice_stt_sample(
        route="turn",
        panel_id=panel_id,
        audio_bytes=len(raw),
        suffix=suffix,
        transcript=transcript,
        duration_seconds=duration_s,
        stt_seconds=t_stt,
    )
    if not transcript:
        try:
            from voice_metrics import voice_turn_count, voice_failure_reason_count
            voice_turn_count.labels(outcome="empty_transcript", path="turn").inc()
            voice_failure_reason_count.labels(path="turn", reason="stt_empty").inc()
        except Exception:
            pass
        return {"ok": True, "panel_id": panel_id, "text": "", "reply": "", "audio_base64": None}

    logger.info("voice/turn panel=%s STT=%.1fs transcript=%r", panel_id, t_stt, transcript[:80])

    # Broadcast transcript so UI shows what was heard.
    try:
        from push import broadcaster as _bc_t
        await _bc_t.broadcast("all", "voice:transcript", {"panel_id": panel_id, "text": transcript})
    except Exception:
        pass

    # ── Phase 2+3: Delegate to /command handler (LLM + TTS) ──
    command_payload = {
        "text": transcript,
        "panel_id": panel_id,
        "_t_turn_start": t_turn_start,  # forwarded so voice_command can record end-to-end total
    }
    if (payload or {}).get("identified_user_id"):
        command_payload["identified_user_id"] = payload["identified_user_id"]
    for _claim_key in ("voice_user_id", "voice_score"):
        if (payload or {}).get(_claim_key) is not None:
            command_payload[_claim_key] = payload[_claim_key]

    result = await voice_command(command_payload, caller=caller, stream=False, db=db)
    result["text"] = transcript
    t_total = time.monotonic() - t_turn_start
    try:
        from voice_metrics import voice_stage_seconds, voice_turn_count
        voice_stage_seconds.labels(stage="total").observe(t_total)
        voice_turn_count.labels(
            outcome="ok" if result.get("ok") else "error",
            path="turn",
        ).inc()
    except Exception:
        pass
    logger.info("voice/turn total=%.1fs (STT=%.1fs LLM+TTS=%.1fs)", t_total, t_stt, t_total - t_stt)
    return result


@router.post("/turn_stream")
async def voice_turn_stream(payload: dict, caller: dict = Depends(_require_voice_auth), db=Depends(get_db)):
    """Streaming variant of /turn: STT, then stream brain→sentence→TTS audio
    chunks as they are produced, so the panel starts speaking the answer ~1.3s
    in instead of waiting for the whole reply to synthesize.

    Wire format (media type application/x-zoe-audio-stream):
      line 1:        {"transcript": "..."}
      per chunk:     {"chunk": N, "text": "...", "provider": "..."}\n<base64 wav>\n
      final:         {"done": true, "reply": "...", "panel_id": "..."}
    Reuses voice_command(stream=True) for the LLM+TTS pipeline.
    """
    b64 = str((payload or {}).get("audio_base64") or "").strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    t_turn_start = time.monotonic()
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc
    t_upload = time.monotonic() - t_turn_start
    suffix = ".wav" if (len(raw) >= 4 and raw[:4] == b"RIFF") else ".raw"

    # ── STT (Moonshine, same waterfall as /turn) ──
    t_stt_start = time.monotonic()
    duration_s: float | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            wav_path = tmp.name
        duration_s = _wav_duration_seconds(wav_path) if suffix == ".wav" else None
        try:
            transcript = await _transcribe_audio(wav_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
    except Exception as exc:
        logger.error("voice/turn_stream STT failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc

    t_stt = time.monotonic() - t_stt_start
    try:
        from voice_metrics import voice_stage_seconds
        voice_stage_seconds.labels(stage="upload").observe(t_upload)
        voice_stage_seconds.labels(stage="stt").observe(t_stt)
    except Exception:
        pass
    transcript = (transcript or "").strip()
    _log_voice_stt_sample(
        route="turn_stream",
        panel_id=panel_id,
        audio_bytes=len(raw),
        suffix=suffix,
        transcript=transcript,
        duration_seconds=duration_s,
        stt_seconds=t_stt,
    )

    import json as _json

    if not transcript:
        try:
            from voice_metrics import voice_turn_count
            voice_turn_count.labels(outcome="empty_transcript", path="turn_stream").inc()
        except Exception:
            pass
        async def _empty():
            yield (_json.dumps({"transcript": "", "done": True, "reply": ""}) + "\n").encode()
        return StreamingResponse(
            _empty(), media_type="application/x-zoe-audio-stream", headers={"Cache-Control": "no-cache"}
        )

    logger.info("voice/turn_stream panel=%s STT=%.2fs transcript=%r", panel_id, t_stt, transcript[:80])
    try:
        from push import broadcaster as _bc_t
        await _bc_t.broadcast("all", "voice:transcript", {"panel_id": panel_id, "text": transcript})
    except Exception:
        pass

    # ── Conversation opener / ender fast-path (the REAL panel lane) ──
    # "hey zoe, let's talk" → instant warm ack + {"conversation_mode": true} on
    # the done frame; the panel daemon then holds an open conversation (long
    # no-wake-word listen windows). An ender ("that's all" / "goodbye") is
    # honoured ONLY when the daemon reports an active conversation in its
    # payload, and closes it with {"conversation_end": true}. Both skip the
    # brain entirely (voice_presence-style fast-path). Flag-gated OFF.
    try:
        from conversation_opener import (
            conversation_opener_enabled,
            is_conversation_ender,
            maybe_conversation_opener,
            next_ender_ack,
        )
        _fast_ack = None
        _fast_flags: dict = {}
        if conversation_opener_enabled():
            _in_conv = bool((payload or {}).get("conversation"))
            _opener = maybe_conversation_opener(transcript)
            if _opener:
                _fast_ack = str(_opener.get("phrase") or "I'm listening.")
                _fast_flags = {"conversation_mode": True}
            elif _in_conv and is_conversation_ender(transcript):
                _fast_ack = next_ender_ack()
                _fast_flags = {"conversation_end": True}
    except Exception as _conv_exc:  # never let the fast-path break a turn
        logger.debug("voice/turn_stream conversation fast-path skipped: %s", _conv_exc)
        _fast_ack = None
        _fast_flags = {}
    if _fast_ack:
        logger.info(
            "voice/turn_stream panel=%s conversation %s: %r",
            panel_id, "OPENED" if _fast_flags.get("conversation_mode") else "ENDED", transcript[:60],
        )
        _ack_audio = None
        try:
            _ack_audio = await _synthesize_kokoro_sidecar(_fast_ack)
            if not _ack_audio:
                _ack_audio = await _synthesize_kokoro(_fast_ack)
        except Exception as _tts_exc:
            logger.warning("voice/turn_stream conversation ack TTS failed: %s", _tts_exc)

        async def _fast_stream():
            yield (_json.dumps({"transcript": transcript}) + "\n").encode()
            if _ack_audio:
                yield (_json.dumps({"chunk": 0, "text": _fast_ack, "provider": "kokoro"}) + "\n").encode()
                yield base64.b64encode(_ack_audio) + b"\n"
            yield (_json.dumps({"done": True, "reply": _fast_ack, "panel_id": panel_id, **_fast_flags}) + "\n").encode()

        try:
            from voice_metrics import voice_turn_count
            voice_turn_count.labels(outcome="ok", path="turn_stream").inc()
        except Exception:
            pass
        return StreamingResponse(
            _fast_stream(), media_type="application/x-zoe-audio-stream", headers={"Cache-Control": "no-cache"}
        )

    command_payload = {
        "text": transcript,
        "panel_id": panel_id,
        "_t_turn_start": t_turn_start,
    }
    if (payload or {}).get("identified_user_id"):
        command_payload["identified_user_id"] = payload["identified_user_id"]
    for _claim_key in ("voice_user_id", "voice_score"):
        if (payload or {}).get(_claim_key) is not None:
            command_payload[_claim_key] = payload[_claim_key]

    # Delegate LLM + per-sentence TTS to the existing streaming pipeline.
    # (voice_command's stream generator records the downstream llm_first_token /
    # tts_first_byte / total stage metrics + a path="command" turn count.)
    # Launch the brain WITHOUT awaiting it here: voice_command does its tier
    # routing / brain work BEFORE returning (measured live: 5-11s on chat turns,
    # with the stream only starting afterwards). Racing it as a task lets the response
    # begin immediately — and lets the thinking filler actually speak while the
    # brain is still working, instead of timing an already-finished wait.
    sub_task = asyncio.ensure_future(voice_command(command_payload, caller=caller, stream=True, db=db))

    def _filler_enabled() -> bool:
        return env_bool("ZOE_VOICE_FILLER_ENABLED", default=False)

    async def _wrapped():
      # try/finally: if the client disconnects mid-stream, Starlette closes this
      # generator — without the cancel, sub_task (brain+TTS) would keep burning
      # resources for a dead connection.
      _first_task = None
      try:
        # Lead with the transcript so the panel can show/log what was heard
        # before the first audio chunk arrives.
        yield (_json.dumps({"transcript": transcript}) + "\n").encode()
        # ── First-turn-of-day greeting (flag-gated, default off) ─────────────
        # Emit the time-of-day greeting as its OWN leading spoken chunk so it fires
        # once per local day, ahead of the answer and decoupled from the reply text
        # (no UI-card pollution). The phrase is pre-warmed in the Kokoro sidecar, so
        # this is ~instant. Keyed by `panel_id` ONLY — a single stable namespace:
        # "first turn of the day at this panel". Mixing in a per-turn speaker id
        # would double-greet the same person across the two key spaces.
        try:
            from voice_greeting import greeting_prefix
            _greet = greeting_prefix(panel_id)
            if _greet:
                _greet_audio = await _synthesize_kokoro_sidecar(f"{_greet}.")
                if _greet_audio:
                    yield (_json.dumps({"chunk": -2, "text": f"{_greet}.", "provider": "greeting"}) + "\n").encode()
                    yield base64.b64encode(_greet_audio) + b"\n"
        except Exception as _greet_exc:
            logger.debug("voice/turn_stream greeting skipped: %s", _greet_exc)
        # ── Thinking filler: if the first REAL audio isn't ready within
        # ZOE_VOICE_FILLER_AFTER_S (1.6s), speak a short ack NOW. The wait has
        # two lazy boundaries: voice_command returning (some tiers do all their
        # routing/brain work before returning) AND the first body chunk (the
        # chat tier returns its StreamingResponse instantly and does the brain
        # work inside the generator) — the filler must race BOTH. ──
        _t_stream0 = time.monotonic()
        try:
            _after = float(os.environ.get("ZOE_VOICE_FILLER_AFTER_S", "1.6"))
        except (TypeError, ValueError):
            _after = 1.6

        async def _filler_lines():
            _fillers = [p for p in os.environ.get(
                "ZOE_VOICE_FILLER_PHRASES", "Let me check.|One sec.|Hmm, let me look."
            ).split("|") if p.strip()]
            _phrase = random.choice(_fillers) if _fillers else "One sec."
            try:
                _fill_audio = await _synthesize_kokoro_sidecar(_phrase)
                if not _fill_audio:
                    _fill_audio = await _synthesize_kokoro(_phrase)
                if _fill_audio:
                    logger.info("voice/turn_stream filler spoken (%r) while brain works", _phrase)
                    return [
                        (_json.dumps({"chunk": -1, "text": _phrase, "provider": "filler"}) + "\n").encode(),
                        base64.b64encode(_fill_audio) + b"\n",
                    ]
            except Exception as _f_exc:
                logger.debug("voice filler skipped: %s", _f_exc)
            return []

        sub = None
        # One filler attempt per turn: gate on attempted (not spoken), or a
        # failed synthesis in the first race would let the second race fire a
        # too-late filler on its 0.1s floor budget.
        _filler_attempted = False
        if _filler_enabled():
            try:
                sub = await asyncio.wait_for(asyncio.shield(sub_task), timeout=_after)
            except asyncio.TimeoutError:
                pass  # brain still working — speak the filler below
            except Exception:
                sub = None  # brain failed fast — the unified error frame below reports it
            if sub is None and not sub_task.done():
                _filler_attempted = True
                for _line in await _filler_lines():
                    yield _line
        if sub is None:
            try:
                sub = await sub_task
            except Exception as _sub_exc:
                # Errors used to raise before the StreamingResponse existed; now
                # the stream has started, so surface them as an error frame the
                # daemon already understands (logs + aborts the turn).
                logger.error("voice/turn_stream brain failed: %s", _sub_exc)
                yield (_json.dumps({"error": str(_sub_exc)[:200], "done": True, "reply": ""}) + "\n").encode()
                return
        try:
            from voice_metrics import voice_turn_count
            voice_turn_count.labels(outcome="ok", path="turn_stream").inc()
        except Exception:
            pass
        def _frame_has_audio(_frame: bytes) -> bool:
            # A "chunk" or "full_audio" header means audio is on the wire (the
            # b64 line follows immediately); a non-JSON line IS a b64 audio line.
            try:
                _o = _json.loads(_frame)
            except Exception:
                return True
            return isinstance(_o, dict) and ("chunk" in _o or "full_audio" in _o)

        if hasattr(sub, "body_iterator"):
            _body = sub.body_iterator
            if _filler_enabled() and not _filler_attempted:
                # voice_command returned fast, but its generator is lazy — the
                # brain work happens on the first pull. Race until the first
                # frame that actually CARRIES AUDIO, forwarding text-only frames
                # (processing_ack, status lines) as they arrive: a frame the
                # panel can't play must not silence the filler (live: the
                # text-only processing_ack landed at 0.6s and defeated the
                # race while first audio was still 4-6s away).
                _deadline = _t_stream0 + _after
                _ended = False
                while True:
                    _remaining = _deadline - time.monotonic()
                    if _remaining <= 0:
                        _filler_attempted = True
                        for _line in await _filler_lines():
                            yield _line
                        break
                    _first_task = asyncio.ensure_future(_body.__anext__())
                    try:
                        _frame = await asyncio.wait_for(asyncio.shield(_first_task), timeout=_remaining)
                        _first_task = None
                    except asyncio.TimeoutError:
                        _filler_attempted = True
                        for _line in await _filler_lines():
                            yield _line
                        try:
                            _frame = await _first_task
                        except StopAsyncIteration:
                            _ended = True
                        _first_task = None
                        if not _ended:
                            yield _frame
                        break
                    except StopAsyncIteration:
                        _first_task = None
                        _ended = True
                        break
                    yield _frame
                    if _frame_has_audio(_frame):
                        break
                if _ended:
                    return
            async for chunk in _body:
                yield chunk
            return
        # voice_command returns a plain dict (not a StreamingResponse) on the
        # skybridge / confirmation-dialog / pi-direct paths regardless of the
        # stream flag. Those already hold the full reply + synthesized audio, so
        # forward it as a one-shot full_audio frame the panel plays via its
        # robust player (handles wav AND mp3) instead of crashing on the missing
        # body_iterator.
        result = sub if isinstance(sub, dict) else {}
        reply_text = str(result.get("reply") or "")
        audio_b64 = result.get("audio_base64")
        if audio_b64:
            # Pre-synthesized (confirmation / pi-direct paths) — one-shot frame.
            yield (_json.dumps({
                "full_audio": str(audio_b64),
                "content_type": result.get("content_type") or "audio/wav",
                "reply": reply_text[:200],
            }) + "\n").encode()
        elif reply_text:
            # Instant segment-stitch for known TEMPLATED replies (weather/time):
            # assemble the audio from cached, finite-vocabulary phrase segments
            # (~7ms) instead of a ~1-2.5s full synth. Flag-gated + fully fallback-
            # safe — a non-match / out-of-vocab / synth miss returns None and we
            # drop straight into the normal per-sentence synth below unchanged.
            _stitched = None
            try:
                from voice_stitch import stitch_reply
                _stitched = await stitch_reply(reply_text, _synthesize_kokoro_sidecar)
            except Exception as _st_exc:
                logger.debug("voice/turn_stream stitch skipped: %s", _st_exc)
                _stitched = None
            if _stitched:
                yield (_json.dumps({"chunk": 0, "text": reply_text[:80], "provider": "stitch"}) + "\n").encode()
                yield base64.b64encode(_stitched) + b"\n"
            # Skybridge fast-path returned text only (stream mode) — synthesize it
            # sentence-by-sentence so the panel starts speaking in ~0.6s instead of
            # waiting for the whole reply to synthesize. (Skipped when stitched.)
            _chunk = 0
            for _sentence in (_split_sentences(reply_text) if not _stitched else []):
                _sentence = _sentence.strip()
                if not _sentence:
                    continue
                _provider = "kokoro-sidecar"
                try:
                    _wav = await _synthesize_kokoro_sidecar(_sentence)
                except Exception:
                    _wav = None
                if not _wav:
                    # Sidecar down — use the full synth fallback chain (kokoro-onnx
                    # -> edge-tts -> espeak) so the panel is never left silent.
                    try:
                        _resp = await synthesize({"text": _sentence}, caller=caller)
                        _wav = _resp.body
                        _provider = _resp.headers.get("X-Zoe-TTS-Provider", "fallback")
                    except Exception:
                        _wav = None
                if not _wav:
                    continue
                yield (_json.dumps({"chunk": _chunk, "text": _sentence[:80],
                                    "provider": _provider}) + "\n").encode()
                yield base64.b64encode(_wav) + b"\n"
                _chunk += 1
        yield (_json.dumps({"done": True, "reply": reply_text}) + "\n").encode()

      finally:
        if _first_task is not None and not _first_task.done():
            _first_task.cancel()
        if not sub_task.done():
            sub_task.cancel()

    return StreamingResponse(
        _wrapped(), media_type="application/x-zoe-audio-stream", headers={"Cache-Control": "no-cache"}
    )


def _brain_prewarm_on_wake_enabled() -> bool:
    return env_bool("ZOE_BRAIN_PREWARM_ON_WAKE", default=True)


async def _prewarm_brain_for_panel(panel_id: str) -> None:
    """On wake-word, spawn the panel's likely (user, session) brain worker so the
    first turn doesn't pay the Pi subprocess cold-start. Fully best-effort and
    backgrounded — must never delay or break the wake acknowledgement.

    Resolves the user the same way voice_command will (bound > recent > default);
    if the user is unknown the worker key would miss the real turn, so we skip
    rather than spawn a wasted process.
    """
    try:
        if not _brain_prewarm_on_wake_enabled():
            return
        session_id = _get_or_create_voice_session(panel_id)
        user_id = (_VOICE_SESSIONS.get(panel_id) or {}).get("bound_user_id") or ""
        if not user_id:
            try:
                from db_pool import get_db_ctx
                async with get_db_ctx() as db:
                    user_id = (
                        await _resolve_recent_panel_session_user(panel_id, db)
                        or await _resolve_panel_default_user(panel_id, db)
                        or ""
                    )
            except Exception:
                user_id = ""
        if not user_id:
            return
        import zoe_core_client
        _t0 = time.monotonic()
        # Warm the brain worker (subprocess spawn) AND the user's facts cache
        # concurrently, during the speech window. The cold facts read is ~1.4s and
        # otherwise lands on the turn's critical path before the brain's first token;
        # warming it here (cache TTL now outlives wake→turn) hides it on the first turn.
        _pw = await asyncio.gather(
            # voice_mode=True: warm the SAME (voice-capped) worker the voice turn
            # will use, so prewarm actually hides the cold subprocess boot.
            zoe_core_client.prewarm(user_id, session_id, voice_mode=True),
            _voice_brain_memory(user_id),
            return_exceptions=True,
        )
        warmed = _pw[0] if not isinstance(_pw[0], BaseException) else False
        _ms = int((time.monotonic() - _t0) * 1000)
        # INFO (not debug): the only way to know on-device whether the spawn
        # actually overlaps the speech window — if this logs AFTER the turn's
        # first token, prewarm bought nothing and the cost is elsewhere (prefill).
        logger.info(
            "voice/wake brain prewarm panel=%s user=%s warmed=%s spawn_ms=%d",
            panel_id, user_id, warmed, _ms,
        )
    except Exception as exc:  # never let prewarm affect the wake path
        # debug, not warning: a boot-time race (wake fires before zoe-core is up)
        # would otherwise warn on every turn until the core settles. The INFO
        # success line above (with spawn_ms) is the real signal — its ABSENCE
        # already tells us prewarm isn't completing.
        logger.debug("voice/wake brain prewarm failed (non-fatal): %s", exc)


def _stt_prewarm_on_wake_enabled() -> bool:
    return env_bool("ZOE_STT_PREWARM_ON_WAKE", default=True)


async def _prewarm_stt_on_wake() -> None:
    """On wake-word, warm Moonshine STT so the FIRST command after idle isn't decoded
    on a cold/swapped-out inference path. Symmetric to _prewarm_brain_for_panel: the
    brain is already prewarmed on wake, but Moonshine is warmed only ONCE at startup,
    and its (un-mlock'd) pages get swapped out during idle on this memory-tight box —
    so the first utterance after a gap hit a cold STT and mis-decoded ("first command
    after a while" mishears), then was clean once warm. Run a tiny dummy inference in
    the ~1-2s wake->command window (faults the model pages back in + primes the
    onnxruntime path) so the real command runs warm. Best-effort + backgrounded;
    never delays or breaks the wake acknowledgement.
    """
    try:
        if not _stt_prewarm_on_wake_enabled():
            return
        loop = asyncio.get_running_loop()

        def _warm() -> None:
            import numpy as _np
            tr = _ensure_moonshine()
            # ~1s of very-low-amplitude noise @ 16kHz — NOT pure silence (which Moonshine
            # may VAD-short-circuit without running the encoder, leaving the pages cold);
            # real low-level input forces the inference path so the model faults back in.
            # Serialized against real transcriptions so the warmup can't race a command.
            buf = _np.random.default_rng(0).standard_normal(16000).astype(_np.float32) * 0.003
            with _moonshine_infer_lock:
                tr.transcribe_without_streaming(buf, 16000)

        await loop.run_in_executor(None, _warm)
    except Exception as exc:
        logger.debug("voice/wake STT prewarm failed (non-fatal): %s", exc)


@router.post("/wake")
async def voice_wake(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """
    Signal from the Pi daemon that the wake word was detected.
    Used to update panel state (show orb animation, open mic indicator).
    Returns cached or fallback TTS audio for the wake acknowledgement.
    """
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    ack_phrase = env_str("ZOE_WAKE_ACK_PHRASE")
    try:
        from voice_presence import wake_ack_variant

        ack_variant = wake_ack_variant()
        ack_phrase = str(ack_variant.get("phrase") or ack_phrase).strip()
    except Exception as exc:
        logger.warning("voice/wake ack variant lookup failed: %s", exc)
        ack_variant = {"audio_path": ""}

    logger.info("voice/wake panel=%s", panel_id)

    # Broadcast voice state so the touch UI orb shows the listening state.
    try:
        from push import broadcaster
        await broadcaster.broadcast("all", "voice:listening_started", {
            "panel_id": panel_id,
            "source": "wake_word",
        })
    except Exception as exc:
        logger.warning("voice/wake broadcast failed: %s", exc)

    # Start the brain worker now (non-blocking) so it's warm by the time the user
    # finishes speaking — hides the first-turn-of-session subprocess cold start.
    try:
        asyncio.ensure_future(_prewarm_brain_for_panel(panel_id))
        # Warm Moonshine STT too — the brain was prewarmed but the (un-mlock'd) STT
        # swaps out during idle, so the FIRST command after a gap was decoded cold.
        asyncio.ensure_future(_prewarm_stt_on_wake())
    except Exception as exc:
        logger.debug("voice/wake prewarm dispatch failed (non-fatal): %s", exc)

    audio_b64: Optional[str] = None
    content_type = "audio/wav"
    try:
        from voice_presence import wake_ack_audio_payload

        cached_audio = wake_ack_audio_payload(audio_path=str(ack_variant.get("audio_path") or ""))
    except Exception as exc:
        logger.warning("voice/wake cached ack lookup failed: %s", exc)
        cached_audio = None

    if cached_audio:
        audio_b64 = str(cached_audio.get("audio_base64") or "") or None
        content_type = str(cached_audio.get("content_type") or "audio/wav")
    elif ack_phrase:
        try:
            audio_resp = await synthesize({"text": ack_phrase}, caller=caller)
            audio_b64 = base64.b64encode(audio_resp.body).decode("ascii")
            content_type = audio_resp.media_type
        except Exception as exc:
            logger.warning("voice/wake TTS ack failed: %s", exc)

    return {
        "ok": True,
        "panel_id": panel_id,
        "state": "listening",
        "ack_text": ack_phrase or None,
        "ack_audio_base64": audio_b64,
        "content_type": content_type,
    }


@router.post("/ambient")
async def voice_ambient(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Receive an ambient audio segment from the Pi daemon, transcribe it, and store in DB.

    The Pi daemon's always-on VAD posts speech segments here.
    Raw audio is transcribed by Moonshine, then stored in ambient_memory.
    Raw audio bytes are discarded after transcription — only text is kept.
    """
    from database import get_db

    b64 = str((payload or {}).get("audio_base64", "")).strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    room = str((payload or {}).get("room", "")).strip() or None
    duration_s = float((payload or {}).get("duration_seconds", 0.0))

    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    # Transcribe via Moonshine — the only STT engine (no whisper anywhere).
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    import uuid as _uuid
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        wav_path = tmp.name
    transcript = ""
    try:
        transcript = await _transcribe_audio_impl(wav_path)
    except Exception as exc:
        logger.debug("ambient transcription failed: %s", exc)
        return {"ok": False, "panel_id": panel_id, "transcript": ""}
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    transcript = transcript.strip()
    if not transcript:
        return {"ok": True, "panel_id": panel_id, "transcript": ""}

    # Store in ambient_memory table — always scoped to the panel's resolved
    # user. If no user is resolvable for the panel, SKIP the insert entirely:
    # ownerless ambient audio must never be stored (P-F4).
    stored = False
    try:
        from db_compat import get_compat_db as _get_compat_db

        async with _get_compat_db() as db:
            ambient_user_id = await _resolve_panel_default_user(panel_id, db)
            if not ambient_user_id:
                logger.warning(
                    "ambient_memory: no user resolvable for panel=%s — transcript NOT stored",
                    panel_id,
                )
            else:
                await db.execute(
                    """INSERT INTO ambient_memory (panel_id, room, transcript, duration_seconds, source, user_id)
                       VALUES (?, ?, ?, ?, 'ambient', ?)""",
                    (panel_id, room, transcript, duration_s, ambient_user_id),
                )
                await db.commit()
                stored = True
                logger.debug(
                    "ambient_memory: panel=%s user=%s chars=%d",
                    panel_id, ambient_user_id, len(transcript),
                )
    except Exception as exc:
        logger.warning("ambient_memory insert failed: %s", exc)

    return {"ok": True, "panel_id": panel_id, "transcript": transcript, "stored": stored}


# ── Speaker identification ─────────────────────────────────────────────────

def _compute_resemblyzer_embedding(wav_path: str) -> Optional[bytes]:
    """Compute a 256-dim resemblyzer voice embedding from a WAV file.

    Returns raw float32 bytes or None if resemblyzer is not installed.
    """
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
        import numpy as np
        encoder = VoiceEncoder()
        wav = preprocess_wav(wav_path)
        embedding = encoder.embed_utterance(wav)  # shape: (256,)
        return embedding.astype(np.float32).tobytes()
    except ImportError:
        logger.debug("resemblyzer not installed; speaker ID unavailable")
        return None
    except Exception as exc:
        logger.warning("resemblyzer embedding failed: %s", exc)
        return None


def _speaker_id_threshold() -> float:
    """Resemblyzer cosine acceptance threshold, read per call so .env flips apply."""
    try:
        return float(os.environ.get("ZOE_SPEAKER_ID_THRESHOLD", "0.82"))
    except ValueError:
        return 0.82


def _accept_panel_voice_claim(payload: dict, caller: dict) -> Optional[str]:
    """Gate a panel-computed speaker-ID claim (voice_user_id + voice_score).

    The panel matches embeddings against its synced profile cache and sends
    only a claim; acceptance is decided HERE against the server's threshold,
    so a panel cannot make itself more trusted than the server allows. Claims
    are honoured from DEVICE-TOKEN callers only — a logged-in browser session
    must not be able to assert an arbitrary speaker identity. A missing/
    unparseable score or a below-threshold claim is ignored (the turn
    proceeds with the panel-binding fallbacks, exactly as with no claim).
    """
    if (caller or {}).get("source") != "device":
        return None
    payload = payload or {}
    user = str(payload.get("voice_user_id") or "").strip()
    if not user:
        return None
    try:
        score = float(payload.get("voice_score"))
    except (TypeError, ValueError):
        return None
    threshold = _speaker_id_threshold()
    if score >= threshold:
        return user
    logger.info(
        "voice claim rejected: user=%s score=%.4f < threshold=%.2f", user, score, threshold
    )
    return None


async def _voice_claim_consented(user_id: str) -> bool:
    """True iff the user still has a consented speaker profile.

    Guards the panel-claim path against a stale panel cache: consent
    revocation drops the row from /profiles/sync and /identify, and this
    check closes the third door. Fails CLOSED — if the DB can't answer,
    the claim is dropped (the turn falls back to panel-binding identity,
    which is the same outcome as no claim).
    """
    try:
        from db_compat import get_compat_db as _get_compat_db
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT 1 FROM speaker_profiles WHERE user_id=? AND consent_at IS NOT NULL LIMIT 1",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("voice claim consent check failed for %s (dropping claim): %s", user_id, exc)
        return False


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine similarity between two float32 byte blobs."""
    try:
        import numpy as np
        va = np.frombuffer(a, dtype=np.float32)
        vb = np.frombuffer(b, dtype=np.float32)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return 0.0


@router.post("/enroll")
async def voice_enroll(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Enroll a speaker voice profile using a WAV audio sample.

    Request: { "audio_base64": "...", "user_id": "...", "display_name": "...", "panel_id": "..." }
    Computes a resemblyzer 256-dim embedding and stores it in speaker_profiles.
    """
    from database import get_db
    import uuid as _uuid
    from db_compat import get_compat_db as _get_compat_db

    from biometric_scope import resolve_enroll_target

    b64 = str((payload or {}).get("audio_base64", "")).strip()
    # A session caller may only enrol ITSELF (admins excepted). Taking the
    # payload's user_id verbatim let any household member enrol their own voice
    # under someone else's id — identity takeover, not just a bad row. A device
    # token still enrols on behalf of the person at the panel; when it names
    # nobody the pre-existing caller fallback stands.
    _target = resolve_enroll_target((payload or {}).get("user_id"), caller)
    user_id = str(_target or caller.get("user_id", "unknown"))
    display_name = str((payload or {}).get("display_name", user_id)).strip() or user_id
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or ""))
    # Biometric enrollment is opt-in (W6): the enroll UI sends consent=true after
    # an explicit checkbox. Without recorded consent the profile is stored but
    # never matched against or synced to a panel (identify/sync filter on it).
    # Tri-state: True stamps consent_at, False REVOKES it (SET NULL — the user
    # changed their mind), absent leaves the existing consent untouched.
    _consent_raw = (payload or {}).get("consent")
    consent: Optional[bool] = None if _consent_raw is None else bool(_consent_raw)

    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        wav_path = tmp.name

    try:
        embedding_bytes = _compute_resemblyzer_embedding(wav_path)
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    if embedding_bytes is None:
        raise HTTPException(status_code=503, detail="resemblyzer not available; install resemblyzer")

    profile_id = str(_uuid.uuid4())
    try:
        async with _get_compat_db() as db:
            # Check if user already has a profile — update sample_count and average embedding.
            async with db.execute(
                "SELECT id, embedding_blob, sample_count FROM speaker_profiles WHERE user_id=?",
                (user_id,)
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                import numpy as np
                old_id, old_blob, old_count = existing
                old_emb = np.frombuffer(old_blob, dtype=np.float32)
                new_emb = np.frombuffer(embedding_bytes, dtype=np.float32)
                # Weighted average of embeddings.
                n = old_count or 1
                averaged = (old_emb * n + new_emb) / (n + 1)
                averaged_norm = averaged / (np.linalg.norm(averaged) + 1e-9)
                await db.execute(
                    """UPDATE speaker_profiles SET embedding_blob=?, sample_count=sample_count+1,
                       display_name=? WHERE id=?""",
                    (averaged_norm.astype(np.float32).tobytes(), display_name, old_id),
                )
                if consent is True:
                    await db.execute(
                        "UPDATE speaker_profiles SET consent_at=CURRENT_TIMESTAMP WHERE id=?",
                        (old_id,),
                    )
                elif consent is False:
                    # Explicit revocation: drop out of the match pool + sync feed.
                    await db.execute(
                        "UPDATE speaker_profiles SET consent_at=NULL WHERE id=?",
                        (old_id,),
                    )
                profile_id = old_id
            else:
                if consent:
                    await db.execute(
                        """INSERT INTO speaker_profiles (id, user_id, display_name, embedding_blob, panel_id, consent_at)
                           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (profile_id, user_id, display_name, embedding_bytes, panel_id or None),
                    )
                else:
                    await db.execute(
                        """INSERT INTO speaker_profiles (id, user_id, display_name, embedding_blob, panel_id)
                           VALUES (?, ?, ?, ?, ?)""",
                        (profile_id, user_id, display_name, embedding_bytes, panel_id or None),
                    )
            await db.commit()
    except Exception as exc:
        logger.error("voice/enroll DB error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to store profile") from exc

    return {"ok": True, "profile_id": profile_id, "user_id": user_id, "display_name": display_name}


@router.post("/identify")
async def voice_identify(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """Identify speaker by comparing to enrolled profiles.

    Accepts two request formats:
    - { "embedding_base64": "...", "panel_id": "..." }  — pre-computed float32 embedding bytes
      (sent by zoe_voice_daemon.py which computes resemblyzer locally on Pi/Jetson)
    - { "audio_base64": "...", "panel_id": "..." }  — raw WAV bytes; server computes embedding
    Returns best-match profile with confidence score.
    """
    from db_compat import get_compat_db as _get_compat_db

    payload = payload or {}

    # Fast path: pre-computed embedding from the voice daemon
    emb_b64 = str(payload.get("embedding_base64", "")).strip()
    if emb_b64:
        try:
            query_emb = base64.b64decode(emb_b64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid base64 embedding") from exc
    else:
        # Fallback: raw WAV bytes — compute embedding server-side
        b64 = str(payload.get("audio_base64", "")).strip()
        if not b64:
            raise HTTPException(status_code=400, detail="embedding_base64 or audio_base64 is required")

        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid base64 audio") from exc

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(raw)
            wav_path = tmp.name

        try:
            query_emb = _compute_resemblyzer_embedding(wav_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

        if query_emb is None:
            raise HTTPException(status_code=503, detail="resemblyzer not available")

    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT id, user_id, display_name, embedding_blob FROM speaker_profiles "
                "WHERE consent_at IS NOT NULL"
            ) as cur:
                profiles = await cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="DB error") from exc

    if not profiles:
        return {"ok": True, "identified": False, "reason": "no_profiles"}

    best_id = None
    best_user_id = None
    best_name = None
    best_score = -1.0
    for row in profiles:
        pid, uid, dname, emb_blob = row
        score = _cosine_similarity(query_emb, emb_blob)
        if score > best_score:
            best_score = score
            best_id = pid
            best_user_id = uid
            best_name = dname

    # Resemblyzer cosine similarity > 0.82 is typically a match.
    threshold = _speaker_id_threshold()
    if best_score >= threshold:
        return {
            "ok": True,
            "identified": True,
            "profile_id": best_id,
            "user_id": best_user_id,
            "display_name": best_name,
            "confidence": round(best_score, 4),
        }
    return {
        "ok": True,
        "identified": False,
        "best_confidence": round(best_score, 4),
        "threshold": threshold,
    }


@router.get("/profiles/sync")
async def voice_profiles_sync(caller: dict = Depends(_require_voice_auth)):
    """Panel-facing profile feed for ON-DEVICE speaker matching.

    Returns consented profiles (embedding + user_id) plus the server's
    acceptance threshold so the panel daemon can cosine-match locally and
    send back only {voice_user_id, voice_score} per turn. Device-token
    callers only: this hands out biometric embeddings and must never be
    reachable from a browser session.
    """
    if caller.get("source") != "device":
        raise HTTPException(status_code=403, detail="device token required")
    from db_compat import get_compat_db as _get_compat_db

    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT user_id, display_name, embedding_blob, sample_count FROM speaker_profiles "
                "WHERE consent_at IS NOT NULL ORDER BY user_id"
            ) as cur:
                rows = await cur.fetchall()
    except Exception as exc:
        logger.error("voice/profiles/sync DB error: %s", exc)
        raise HTTPException(status_code=500, detail="DB error") from exc

    return {
        "ok": True,
        "threshold": _speaker_id_threshold(),
        "profiles": [
            {
                "user_id": r[0],
                "display_name": r[1],
                "embedding_base64": base64.b64encode(bytes(r[2])).decode("ascii"),
                "sample_count": r[3] or 1,
            }
            for r in rows
        ],
    }


@router.get("/profiles")
async def voice_profiles(caller: dict = Depends(_require_voice_auth)):
    """List the CALLER'S OWN enrolled speaker profiles (never embeddings).

    Used by the settings page Voice Identity section to show the signed-in
    member what they have enrolled. Household-wide only for an admin: who is
    enrolled, and whether they consented, is itself biometric metadata, so the
    scope filter is in the SQL rather than a post-filter.
    """
    from db_compat import get_compat_db as _get_compat_db
    from biometric_scope import require_person_scope

    caller_id, is_admin = require_person_scope(caller)
    if is_admin:
        sql = (
            "SELECT id, user_id, display_name, sample_count, panel_id, consent_at "
            "FROM speaker_profiles ORDER BY display_name"
        )
        params: tuple = ()
    else:
        sql = (
            "SELECT id, user_id, display_name, sample_count, panel_id, consent_at "
            "FROM speaker_profiles WHERE user_id=? ORDER BY display_name"
        )
        params = (caller_id,)

    try:
        async with _get_compat_db() as db:
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return {
            "ok": True,
            "profiles": [
                {"id": r[0], "user_id": r[1], "display_name": r[2],
                 "sample_count": r[3] or 1, "panel_id": r[4],
                 "consented": r[5] is not None}
                for r in rows
            ]
        }
    except Exception as exc:
        logger.error("voice/profiles DB error: %s", exc)
        raise HTTPException(status_code=500, detail="DB error") from exc


@router.delete("/profiles/{profile_id}")
async def voice_profile_delete(profile_id: str, caller: dict = Depends(_require_voice_auth)):
    """Delete an enrolled speaker profile — the caller's own, or any for an admin.

    Ownership is checked against the row BEFORE the delete. `_require_voice_auth`
    only proves the caller may reach the voice surface, so an unscoped
    `DELETE ... WHERE id=?` let any signed-in household member wipe anyone
    else's voiceprint.
    """
    from db_compat import get_compat_db as _get_compat_db
    from biometric_scope import authorize_profile_access, require_person_scope

    caller_id, is_admin = require_person_scope(caller)
    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT user_id FROM speaker_profiles WHERE id=?", (profile_id,)
            ) as cur:
                row = await cur.fetchone()
            authorize_profile_access(
                row[0] if row else None, caller_id, is_admin, kind="speaker"
            )
            await db.execute("DELETE FROM speaker_profiles WHERE id=?", (profile_id,))
            await db.commit()
        return {"ok": True, "deleted": profile_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("voice/profiles delete error: %s", exc)
        raise HTTPException(status_code=500, detail="DB error") from exc


@router.get("/chatgpt-auth-status")
async def chatgpt_auth_status():
    """Stub: ChatGPT voice auth status (not connected)."""
    return {"status": "not_connected"}


@router.get("/livekit-token")
async def get_livekit_token(request: Request, user: dict = Depends(get_current_user)):
    """Mint a LiveKit join token for the voice page.

    Returns {"token": "<jwt>", "url": "<ws url>"}.  The token is a standard
    HS256 JWT with LiveKit video-grant claims — no livekit-api package required.

    The LiveKit URL is derived from the browser's request origin so LAN clients
    get ws(s)://<lan-host>/livekit/ and remote clients get the configured LIVEKIT_URL.
    Nginx proxies /livekit/ → host.docker.internal:7880 on both HTTP and HTTPS.
    """
    import time as _time
    import uuid as _uuid
    import jwt as _jwt

    api_key = env_str("LIVEKIT_API_KEY")
    api_secret = env_str("LIVEKIT_API_SECRET")

    if not api_key or not api_secret:
        raise HTTPException(status_code=503, detail="LiveKit credentials not configured")

    # On-demand: spin up the LiveKit container + agent before handing back a token,
    # so the browser's WebRTC connect succeeds.  Best-effort and bounded — never
    # blocks token issuance if the container is slow or docker is unavailable.
    livekit_ready: bool = True
    try:
        from routers.voice_livekit import ensure_livekit_started, _ondemand_enabled
        if _ondemand_enabled():
            livekit_ready = await ensure_livekit_started()
    except Exception as _lk_exc:  # pragma: no cover - defensive
        logger.warning("livekit on-demand start failed (non-fatal): %s", _lk_exc)

    # Derive the LiveKit URL from the browser's request host.
    # nginx passes the original Host header and X-Forwarded-Proto so we can reconstruct
    # the correct ws(s)://<browser-host>/livekit/ URL.  This works for LAN IPs, the
    # Cloudflare domain, and any other hostname without extra config.
    env_url = env_str("LIVEKIT_URL")
    # Prefer Origin header (sent by browsers on cross-origin fetches; not sent same-origin)
    origin = request.headers.get("origin", "")
    if origin:
        ws_scheme = "wss" if origin.startswith("https") else "ws"
        host = origin.split("://", 1)[-1].rstrip("/")
        livekit_url = f"{ws_scheme}://{host}/livekit/"
    else:
        # For same-origin fetches use the Host + X-Forwarded-Proto headers that
        # nginx forwards from the browser.  Falls back to env_url if no Host.
        fwd_proto = request.headers.get("x-forwarded-proto", "")
        host_hdr = request.headers.get("host", "")
        if host_hdr and host_hdr not in ("localhost:8000", "127.0.0.1:8000"):
            ws_scheme = "wss" if fwd_proto == "https" else "ws"
            livekit_url = f"{ws_scheme}://{host_hdr}/livekit/"
        elif env_url:
            livekit_url = env_url.rstrip("/") + "/"
        else:
            scheme = "wss" if request.url.scheme == "https" else "ws"
            livekit_url = f"{scheme}://{request.url.netloc}/livekit/"

    user_id = user.get("user_id", "voice-guest")
    now = int(_time.time())
    payload = {
        "exp": now + 3600,
        "iss": api_key,
        "sub": user_id,
        "jti": _uuid.uuid4().hex,
        "video": {
            "roomJoin": True,
            "room": "zoe-voice",
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    token = _jwt.encode(payload, api_secret, algorithm="HS256")

    # Tell the client whether LiveKit WebRTC is likely reachable.
    # Without a TURN server, only LAN clients can connect. We detect "LAN" as
    # requests coming from a private-range IP in the X-Forwarded-For chain.
    import ipaddress as _ip
    _cf_ip = (
        request.headers.get("cf-connecting-ip", "")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "")
    )
    def _is_private(addr: str) -> bool:
        try:
            return _ip.ip_address(addr).is_private
        except Exception:
            return False
    livekit_lan_only = not bool(os.environ.get("LIVEKIT_TURN_URL", ""))
    livekit_available = ((not livekit_lan_only) or _is_private(_cf_ip)) and livekit_ready

    logger.debug("livekit-token: user=%s url=%s origin=%s lan_ip=%s lk_avail=%s",
                 user_id, livekit_url, origin, _cf_ip, livekit_available)
    return {"token": token, "url": livekit_url, "livekit_available": livekit_available}


# ── Voice confirmation state ───────────────────────────────────────────────
# Track pending confirmations per panel: panel_id → {intent_name, slots, expire_at, session_id}
_PENDING_CONFIRMATIONS: dict[str, dict] = {}
_CONFIRM_TIMEOUT_S = 30  # seconds to wait for "yes/confirm" before expiring

# Intents that require confirmation before execution (irreversible writes).
_CONFIRM_INTENTS = frozenset({
    "note_create", "journal_create",
    "transaction_create", "people_create",
})

_CONFIRM_KEYWORDS = frozenset({
    "yes", "yeah", "yep", "confirm", "do it", "go ahead", "sure", "ok", "okay",
    "correct", "that's right", "sounds good", "proceed",
})
_CANCEL_KEYWORDS = frozenset({
    "no", "nope", "cancel", "stop", "don't", "abort", "never mind", "nevermind",
})
