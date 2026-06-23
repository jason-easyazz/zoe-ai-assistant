import asyncio
import base64
import contextvars
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import wave
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
        from database import get_db
        async for db in get_db():
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
_VOICE_RECALL_MAX_FACTS = 6      # facts that actually make it into the block
_VOICE_RECALL_FACT_CHARS = 160   # per-fact truncation so one long memory can't bloat it


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

    if not refs:
        # Search found nothing relevant — fall back to the for-prompt dump so a
        # recall question still has facts to draw on.
        return await _voice_recall_fallback(user_id)

    seen: set[str] = set()
    lines: list[str] = []
    for ref in refs:
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
    if not lines:
        return await _voice_recall_fallback(user_id)
    return "[What you remember]\n" + "\n".join(lines)


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


# ── Kokoro ONNX model singleton ────────────────────────────────────────────
# Loaded once at module level to avoid ~500ms per-call initialisation.
_kokoro_instance = None
_kokoro_lock = asyncio.Lock()
_kokoro_model_path_loaded: str = ""


async def _get_kokoro_instance():
    """Return cached Kokoro instance, loading lazily on first call."""
    global _kokoro_instance, _kokoro_model_path_loaded
    model_path = os.environ.get("ZOE_KOKORO_MODEL", "").strip()
    if not model_path or not os.path.isfile(model_path):
        return None
    async with _kokoro_lock:
        if _kokoro_instance is not None and _kokoro_model_path_loaded == model_path:
            return _kokoro_instance
        try:
            from kokoro_onnx import Kokoro  # type: ignore
            voices_path = os.environ.get("ZOE_KOKORO_VOICES", "").strip() or None
            _kokoro_instance = Kokoro(model_path, voices_path=voices_path)
            _kokoro_model_path_loaded = model_path
            logger.info("Kokoro ONNX model loaded from %s", model_path)
            return _kokoro_instance
        except ImportError:
            logger.debug("kokoro-onnx not installed; Kokoro TTS unavailable")
            return None
        except Exception as exc:
            logger.warning("Kokoro ONNX model load failed: %s", exc)
            return None


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


def _has_espeak_ng() -> bool:
    return shutil.which("espeak-ng") is not None


async def _synthesize_espeak(text: str, speed: int, pitch: int, volume: int) -> bytes:
    if not _has_espeak_ng():
        raise RuntimeError("espeak-ng is not installed")

    with tempfile.TemporaryDirectory(prefix="zoe-tts-") as td:
        mono_path = Path(td) / "mono.wav"
        stereo_path = Path(td) / "stereo.wav"

        proc = await asyncio.create_subprocess_exec(
            "espeak-ng",
            "-v",
            "en-au",
            "-s",
            str(speed),
            "-p",
            str(pitch),
            "-a",
            str(volume),
            "-w",
            str(mono_path),
            text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"espeak-ng failed: {err.decode(errors='ignore').strip()}")

        # Duplicate mono samples to stereo for better USB speaker compatibility.
        import wave

        with wave.open(str(mono_path), "rb") as src:
            fr = src.getframerate()
            sw = src.getsampwidth()
            n = src.getnframes()
            data = src.readframes(n)

        out = bytearray()
        step = sw
        for i in range(0, len(data), step):
            s = data[i : i + step]
            if len(s) < step:
                break
            out.extend(s)
            out.extend(s)

        with wave.open(str(stereo_path), "wb") as dst:
            dst.setnchannels(2)
            dst.setsampwidth(sw)
            dst.setframerate(fr)
            dst.writeframes(bytes(out))

        return stereo_path.read_bytes()


async def _synthesize_edge_tts(text: str, voice: str) -> Optional[bytes]:
    try:
        import edge_tts
    except Exception:
        return None

    with tempfile.TemporaryDirectory(prefix="zoe-edge-tts-") as td:
        mp3_path = Path(td) / "speech.mp3"
        wav_path = Path(td) / "speech.wav"
        communicate = edge_tts.Communicate(text=text, voice=voice)
        edge_timeout = float(os.environ.get("ZOE_EDGE_TTS_TIMEOUT_S", "5"))
        await asyncio.wait_for(communicate.save(str(mp3_path)), timeout=edge_timeout)

        # Convert mp3 to wav if ffmpeg exists, else return mp3 bytes.
        if shutil.which("ffmpeg") is None:
            return mp3_path.read_bytes()

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(mp3_path),
            "-ac",
            "2",
            "-ar",
            "22050",
            str(wav_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0:
            return mp3_path.read_bytes()
        return wav_path.read_bytes()


async def _synthesize_local_service(text: str, profile: str, base_url: str) -> Optional[bytes]:
    if not base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/synthesize",
                json={"text": text, "profile": profile},
            )
            if r.status_code >= 400 or not r.content:
                return None
            return r.content
    except Exception:
        return None


_MONTHS_SPOKEN = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]")


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _humanize_iso_date(m: "re.Match") -> str:
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"the {_ordinal(d)} of {_MONTHS_SPOKEN[mo - 1]} {y}"
    except Exception:
        pass
    return m.group(0)


def _clean_for_speech(text: str) -> str:
    """Normalize reply text so Kokoro doesn't read junk aloud: strip a leaked
    JSON object, markdown (* _ ` # ~ |), emoji; speak ISO dates as words; turn
    em/en dashes and ° into spoken forms. Applied at every TTS entry point."""
    if not text:
        return text
    t = str(text)
    t = re.sub(r"^\s*\{[^{}]*\}\s*", "", t)            # drop leading JSON leak
    t = _ISO_DATE_RE.sub(_humanize_iso_date, t)         # 2026-06-22 -> the 22nd of June 2026
    t = t.replace("°C", " degrees").replace("°F", " degrees").replace("°", " degrees")
    t = t.replace("—", ", ").replace("–", ", ")
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"[*_`#~|>]+", "", t)                    # markdown
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)              # no space before punctuation
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


# Pooled client for the Kokoro sidecar, reused across sentences so each spoken
# sentence doesn't pay a fresh TCP/connection setup (the sidecar is hit once per
# sentence on the streaming voice path — per-call AsyncClient added fixed latency
# to every inter-sentence boundary).
_KOKORO_HTTP: "Optional[httpx.AsyncClient]" = None


def _kokoro_http_client() -> "httpx.AsyncClient":
    global _KOKORO_HTTP
    if _KOKORO_HTTP is None or _KOKORO_HTTP.is_closed:
        _KOKORO_HTTP = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0),
        )
    return _KOKORO_HTTP


async def _synthesize_kokoro_sidecar(text: str) -> Optional[bytes]:
    """Synthesize via Kokoro PyTorch sidecar (GPU, natural af_sky voice).

    Calls the local FastAPI sidecar on port 10201 which keeps the Kokoro
    model warm in CUDA memory.  Sub-200ms warm latency on Jetson Orin.
    Set ZOE_KOKORO_SIDECAR_URL to override (default http://127.0.0.1:10201).
    Falls through silently if the sidecar is unavailable.
    """
    text = _clean_for_speech(text)
    sidecar_url = os.environ.get("ZOE_KOKORO_SIDECAR_URL", "http://127.0.0.1:10201").rstrip("/")
    voice = os.environ.get("ZOE_KOKORO_VOICE", "af_sky").strip() or "af_sky"
    try:
        client = _kokoro_http_client()
        r = await client.post(
            f"{sidecar_url}/synthesize",
            json={"text": text, "voice": voice},
        )
        if r.status_code >= 400 or not r.content:
            logger.debug("kokoro-sidecar HTTP %s", r.status_code)
            return None
        return r.content
    except httpx.TransportError as exc:
        # A pooled client does NOT auto-close on a transport error the way the old
        # per-call `async with` did, so a timed-out / reset connection would be
        # re-checked-out for the next sentence and fail again. Recycle the pooled
        # client so the next call reconnects cleanly.
        global _KOKORO_HTTP
        logger.debug("kokoro-sidecar transport error, recycling pooled client: %s", exc)
        try:
            if _KOKORO_HTTP is not None:
                await _KOKORO_HTTP.aclose()
        except Exception:
            pass
        _KOKORO_HTTP = None
        return None
    except Exception as exc:
        logger.debug("kokoro-sidecar unavailable: %s", exc)
        return None


async def _synthesize_kokoro(text: str, voice: str = "af_sky") -> Optional[bytes]:
    """Synthesize using Kokoro ONNX (thewh1teagle/kokoro-onnx).

    ~82M param ONNX model — sub-100ms first chunk on Jetson CUDA, natural AU voice.
    Install: pip install kokoro-onnx
    Model: download from https://github.com/thewh1teagle/kokoro-onnx/releases
    Set ZOE_KOKORO_MODEL to the path of the kokoro-v1.0.onnx file.
    Set ZOE_KOKORO_VOICE to override the default voice (af_sky = AU female).
    Uses module-level cached instance to avoid ~500ms model load per call.
    """
    kokoro = await _get_kokoro_instance()
    if kokoro is None:
        return None
    voice = os.environ.get("ZOE_KOKORO_VOICE", voice).strip() or voice
    try:
        import numpy as np
        import wave
        import io

        def _kokoro_sync():
            kokoro_speed = float(os.environ.get("ZOE_KOKORO_SPEED", "1.15"))
            samples, sample_rate = kokoro.create(text, voice=voice, speed=kokoro_speed, lang="en-us")
            samples_int16 = (samples * 32767).astype(np.int16)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(samples_int16.tobytes())
            return buf.getvalue()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _kokoro_sync)
    except Exception as exc:
        logger.warning("Kokoro TTS failed: %s", exc)
        return None


def _should_supersede_voice_weather_action(row, nav_key: str, card_key: str) -> bool:
    if row["idempotency_key"] in {nav_key, card_key}:
        return False
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    return (
        row["action_type"] == "panel_navigate"
        and payload.get("url") == "/touch/weather.html"
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
        url = str(payload.get("url") or "").split("?", 1)[0]
        return url in {
            "/touch/calendar.html",
            "/touch/weather.html",
            "/touch/lists.html",
            "/touch/chat.html",
        }
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


_FIRST_UNIT_MIN_CHARS = 12  # don't synthesize a tiny stub like "So,"
_FIRST_UNIT_SOFT_CAP = 40   # flush at a word boundary by here even without punctuation


def _fast_first_audio_enabled() -> bool:
    return os.environ.get("ZOE_VOICE_FAST_FIRST_AUDIO", "1").strip().lower() in ("1", "true", "yes", "on")


def _extract_first_unit(buffer: str) -> tuple[Optional[str], str]:
    """Pull the FIRST speakable unit out of a streaming buffer as early as possible.

    Time-to-first-audio dominates how fast a voice reply *feels*. Waiting for a full
    sentence (`.!?`) before the first synth adds ~1-2s of silence at 22 tok/s. For
    the first chunk only, break on the first clause boundary (, ; : — or sentence
    end) once we have a few words, or at a word boundary by the soft cap — so audio
    starts almost immediately. Punctuation must be followed by space/end so decimals
    ('twelve point four') and 'a.m.' don't split. Returns (unit|None, remainder)."""
    stripped = buffer.lstrip()
    if len(stripped) < _FIRST_UNIT_MIN_CHARS:
        return None, buffer
    m = re.search(r"(.{%d,}?[,;:.!?—–])(?:\s|$)" % _FIRST_UNIT_MIN_CHARS, buffer, re.DOTALL)
    if m:
        return m.group(1).strip(), buffer[m.end():]
    # No clause break yet but the buffer is getting long — flush at a word boundary
    # so the first audio doesn't stall behind a long opening clause.
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
    return os.environ.get("ZOE_SKYBRIDGE_ONLY", "").strip().lower() in ("1", "true", "yes", "on")


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
            "url": "/touch/weather.html",
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
        logger.debug("voice weather ui enqueue failed (non-fatal): %s", exc)


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
            "url": "/touch/calendar.html",
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
    query = quote_plus(str(utterance or skybridge_result.get("spoken_summary") or ""))
    url = "/touch/skybridge.html" + (f"?q={query}" if query else "")
    cards = skybridge_result.get("cards") if isinstance(skybridge_result.get("cards"), list) else []
    summary = str(skybridge_result.get("spoken_summary") or "Showing this in Skybridge.")
    nav_payload = {
        "url": url,
        "label": "Opening Skybridge",
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
                logger.debug("voice skybridge stale-action cleanup failed (non-fatal): %s", _sup_exc)
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
        logger.debug("voice skybridge ui enqueue failed (non-fatal): %s", exc)


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
            "url": "/touch/dashboard.html",
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


def _panel_session_trust_window_s() -> int:
    """Seconds that an active panel session is trusted for voice scope gating."""
    raw = str(os.environ.get("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "900")).strip()
    try:
        value = int(raw)
    except Exception:
        return 900
    return max(0, min(value, 24 * 60 * 60))


async def _resolve_recent_panel_session_user(panel_id: str, db) -> Optional[str]:
    """
    Resolve panel user only when the panel session heartbeat is fresh enough
    to be considered actively authenticated.
    """
    trust_window_s = _panel_session_trust_window_s()
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


def _wav_bytes_from_float32_samples(samples, sample_rate: int) -> bytes:
    """Convert float32 [-1,1] samples to mono WAV bytes."""
    import io
    import wave
    import numpy as np

    clipped = np.clip(samples, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


async def _stream_kokoro_sentence_wavs(sentence: str, voice: str = "af_sky"):
    """Yield WAV chunks from Kokoro create_stream() for one sentence."""
    kokoro = await _get_kokoro_instance()
    if kokoro is None or not hasattr(kokoro, "create_stream"):
        return
    voice = os.environ.get("ZOE_KOKORO_VOICE", voice).strip() or voice
    kokoro_speed = float(os.environ.get("ZOE_KOKORO_SPEED", "1.15"))
    try:
        async for samples, sample_rate in kokoro.create_stream(
            sentence, voice=voice, speed=kokoro_speed, lang="en-us"
        ):
            if samples is None:
                continue
            try:
                yield _wav_bytes_from_float32_samples(samples, sample_rate)
            except Exception:
                continue
    except Exception as exc:
        logger.warning("Kokoro create_stream failed: %s", exc)
        return


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

    # ── TTS waterfall: kokoro-sidecar → local sidecar → Kokoro ONNX → Edge TTS → espeak-ng ──
    if mode in {"hybrid", "local"}:
        audio_bytes = await _synthesize_local_service(text, profile=profile, base_url=local_tts_url)
        if audio_bytes:
            provider = "local-tts"
            if not audio_bytes.startswith(b"RIFF"):
                content_type = "audio/mpeg"

    # Kokoro sidecar — GPU-accelerated natural af_sky voice (~150ms warm on Jetson).
    if audio_bytes is None and mode != "cloud":
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

            # Waterfall: kokoro-sidecar → local sidecar → Kokoro ONNX → Edge TTS → espeak
            if mode in {"hybrid", "local"} and local_tts_url:
                audio_bytes = await _synthesize_local_service(sentence, profile=profile, base_url=local_tts_url)
                if audio_bytes:
                    provider = "local-tts"

            if audio_bytes is None and mode != "cloud":
                audio_bytes = await _synthesize_kokoro_sidecar(sentence)
                if audio_bytes:
                    provider = "kokoro-sidecar"

            if audio_bytes is None and mode != "cloud":
                audio_bytes = await _synthesize_kokoro(sentence)
                if audio_bytes:
                    provider = "kokoro-onnx"

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


def _whisper_cpp_binary() -> Optional[str]:
    explicit = (os.environ.get("ZOE_WHISPER_CPP_BIN") or "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    for name in ("whisper-cli", "whisper.cpp", "main"):
        p = shutil.which(name)
        if p:
            return p
    return None


async def _run_whisper_cpp(wav_path: str) -> str:
    """Run whisper.cpp CLI; return transcript text (may be empty)."""
    bin_path = _whisper_cpp_binary()
    model = (os.environ.get("ZOE_WHISPER_MODEL") or "").strip()
    if not bin_path:
        raise RuntimeError(
            "whisper.cpp binary not found; set ZOE_WHISPER_CPP_BIN or install whisper-cli on PATH"
        )
    if not model or not os.path.isfile(model):
        raise RuntimeError(
            "whisper model not found; set ZOE_WHISPER_MODEL to a .bin file (e.g. ggml-base.en.bin)"
        )
    lang = (os.environ.get("ZOE_WHISPER_LANG") or "en").strip()
    extra = os.environ.get("ZOE_WHISPER_EXTRA_ARGS", "-nt").strip()
    extra_parts = extra.split() if extra else []
    cmd = [bin_path, "-m", model, "-f", wav_path, "-l", lang] + extra_parts
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=float(os.environ.get("ZOE_WHISPER_TIMEOUT_S", "120")))
    if proc.returncode != 0:
        msg = err.decode(errors="ignore").strip() or out.decode(errors="ignore").strip()
        raise RuntimeError(f"whisper.cpp failed (exit {proc.returncode}): {msg[:500]}")
    text = out.decode(errors="ignore").strip()
    return text


# Cached faster-whisper model instance to avoid ~1s reload per call
_faster_whisper_model = None
_faster_whisper_model_name: str = ""
_faster_whisper_lock = asyncio.Lock()
_faster_whisper_worker: "_FasterWhisperWorker | None" = None


def _use_in_process_faster_whisper() -> bool:
    return (os.environ.get("ZOE_WHISPER_IN_PROCESS") or "").strip().lower() in {"1", "true", "yes", "on"}


def _use_persistent_faster_whisper_worker() -> bool:
    return (os.environ.get("ZOE_WHISPER_PERSISTENT_WORKER") or "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


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


async def _get_faster_whisper_model():
    """Lazy-load and cache a faster-whisper WhisperModel instance."""
    global _faster_whisper_model, _faster_whisper_model_name
    model_name = (os.environ.get("ZOE_WHISPER_MODEL") or "base.en").strip()
    async with _faster_whisper_lock:
        if _faster_whisper_model is not None and _faster_whisper_model_name == model_name:
            return _faster_whisper_model
        try:
            from faster_whisper import WhisperModel  # type: ignore
            device = "cuda" if os.environ.get("ZOE_WHISPER_DEVICE", "").lower() == "cuda" else "cpu"
            # int8_float16 = int8-quantized weights + float16 compute: best speed/memory for Jetson
            # CPU defaults to float32; ctranslate2 int8 is not supported on Jetson ARM.
            default_compute = "int8_float16" if device == "cuda" else "float32"
            compute_type = os.environ.get("ZOE_WHISPER_COMPUTE_TYPE", default_compute).strip() or default_compute
            logger.info("Loading faster-whisper model=%s device=%s", model_name, device)
            _faster_whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _faster_whisper_model_name = model_name
            logger.info("faster-whisper model loaded: %s", model_name)
            return _faster_whisper_model
        except ImportError:
            logger.debug("faster-whisper not installed")
            return None
        except Exception as exc:
            logger.warning("faster-whisper model load failed: %s", exc)
            return None


async def _run_faster_whisper_subprocess(wav_path: str) -> str:
    """Transcribe with faster-whisper in a child process so native crashes cannot kill the API worker."""
    model_name = (os.environ.get("ZOE_WHISPER_MODEL") or "base.en").strip()
    device = "cuda" if os.environ.get("ZOE_WHISPER_DEVICE", "").lower() == "cuda" else "cpu"
    timeout = _env_float("ZOE_WHISPER_TIMEOUT_S", 20.0)

    code = r"""
import json
import os
import sys

from faster_whisper import WhisperModel

wav_path = sys.argv[1]
model_name = os.environ.get("ZOE_WHISPER_MODEL") or "base.en"
device = "cuda" if os.environ.get("ZOE_WHISPER_DEVICE", "").lower() == "cuda" else "cpu"
default_compute = "int8_float16" if device == "cuda" else "float32"
compute_type = (os.environ.get("ZOE_WHISPER_COMPUTE_TYPE") or default_compute).strip() or default_compute
lang = (os.environ.get("ZOE_WHISPER_LANG") or "en").strip()

def _env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default

def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default

model = WhisperModel(model_name, device=device, compute_type=compute_type)
segments, _info = model.transcribe(
    wav_path,
    language=lang,
    beam_size=_env_int("ZOE_WHISPER_BEAM_SIZE", 5),
    vad_filter=True,
    vad_parameters={
        "threshold": _env_float("ZOE_WHISPER_VAD_THRESHOLD", 0.50),
        "min_speech_duration_ms": _env_int("ZOE_WHISPER_MIN_SPEECH_MS", 120),
        "min_silence_duration_ms": _env_int("ZOE_WHISPER_MIN_SILENCE_MS", 350),
        "speech_pad_ms": _env_int("ZOE_WHISPER_SPEECH_PAD_MS", 220),
    },
)
text = " ".join(seg.text.strip() for seg in segments).strip()
print(json.dumps({"text": text}), flush=True)
"""

    logger.info("Running faster-whisper subprocess model=%s device=%s", model_name, device)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        wav_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"faster-whisper timed out after {timeout:.1f}s") from exc

    stderr = err.decode(errors="ignore").strip()
    stdout = out.decode(errors="ignore").strip()
    if proc.returncode != 0:
        if proc.returncode and proc.returncode < 0:
            reason = f"signal {-proc.returncode}"
        else:
            reason = f"exit {proc.returncode}"
        detail = stderr or stdout or "no stderr"
        raise RuntimeError(f"faster-whisper subprocess failed ({reason}): {detail[:500]}")

    try:
        payload = json.loads(stdout.splitlines()[-1] if stdout else "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"faster-whisper subprocess returned invalid output: {stdout[:500]}") from exc
    return str(payload.get("text") or "").strip()


class _FasterWhisperWorker:
    """Long-lived isolated faster-whisper process with one loaded model."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.model_key = ""

    def _current_model_key(self) -> str:
        model_name = (os.environ.get("ZOE_WHISPER_MODEL") or "base.en").strip()
        device = "cuda" if os.environ.get("ZOE_WHISPER_DEVICE", "").lower() == "cuda" else "cpu"
        default_compute = "int8_float16" if device == "cuda" else "float32"
        compute_type = (os.environ.get("ZOE_WHISPER_COMPUTE_TYPE") or default_compute).strip() or default_compute
        return "|".join((model_name, device, compute_type))

    async def stop(self) -> None:
        proc = self.proc
        self.proc = None
        self.model_key = ""
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    async def _stderr_snippet(self, limit: int = 1200) -> str:
        if not self.proc or not self.proc.stderr:
            return ""
        try:
            data = await asyncio.wait_for(self.proc.stderr.read(limit), timeout=0.2)
        except Exception:
            return ""
        return data.decode(errors="ignore").strip()

    async def _ensure_started(self) -> None:
        key = self._current_model_key()
        if self.proc and self.proc.returncode is None and self.model_key == key:
            return
        await self.stop()
        startup_timeout = _env_float("ZOE_WHISPER_WORKER_START_TIMEOUT_S", 30.0)
        code = r"""
import json
import os
import sys

from faster_whisper import WhisperModel

model_name = os.environ.get("ZOE_WHISPER_MODEL") or "base.en"
device = "cuda" if os.environ.get("ZOE_WHISPER_DEVICE", "").lower() == "cuda" else "cpu"
default_compute = "int8_float16" if device == "cuda" else "float32"
compute_type = (os.environ.get("ZOE_WHISPER_COMPUTE_TYPE") or default_compute).strip() or default_compute
lang = (os.environ.get("ZOE_WHISPER_LANG") or "en").strip()

def _env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default

def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default

model = WhisperModel(model_name, device=device, compute_type=compute_type)
print(json.dumps({"ready": True, "model": model_name, "device": device, "compute_type": compute_type}), flush=True)

for line in sys.stdin:
    try:
        payload = json.loads(line)
        wav_path = str(payload.get("wav_path") or "")
        segments, _info = model.transcribe(
            wav_path,
            language=lang,
            beam_size=_env_int("ZOE_WHISPER_BEAM_SIZE", 5),
            vad_filter=True,
            vad_parameters={
                "threshold": _env_float("ZOE_WHISPER_VAD_THRESHOLD", 0.50),
                "min_speech_duration_ms": _env_int("ZOE_WHISPER_MIN_SPEECH_MS", 120),
                "min_silence_duration_ms": _env_int("ZOE_WHISPER_MIN_SILENCE_MS", 350),
                "speech_pad_ms": _env_int("ZOE_WHISPER_SPEECH_PAD_MS", 220),
            },
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        print(json.dumps({"text": text}), flush=True)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
"""
        logger.info("Starting faster-whisper worker key=%s", key)
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        if not self.proc.stdout:
            await self.stop()
            raise RuntimeError("faster-whisper worker stdout unavailable")
        try:
            ready_raw = await asyncio.wait_for(self.proc.stdout.readline(), timeout=startup_timeout)
        except asyncio.TimeoutError as exc:
            detail = await self._stderr_snippet()
            await self.stop()
            suffix = f": {detail[:500]}" if detail else ""
            raise RuntimeError(f"faster-whisper worker startup timed out after {startup_timeout:.1f}s{suffix}") from exc
        if self.proc.returncode is not None:
            detail = await self._stderr_snippet()
            suffix = f": {detail[:500]}" if detail else ""
            raise RuntimeError(f"faster-whisper worker exited during startup ({self.proc.returncode}){suffix}")
        try:
            ready = json.loads(ready_raw.decode(errors="ignore"))
        except json.JSONDecodeError as exc:
            detail = await self._stderr_snippet()
            await self.stop()
            suffix = f": {detail[:500]}" if detail else ""
            raise RuntimeError(f"faster-whisper worker returned invalid startup output: {ready_raw[:300]!r}{suffix}") from exc
        if not ready.get("ready"):
            await self.stop()
            raise RuntimeError(f"faster-whisper worker did not become ready: {ready}")
        self.model_key = key

    async def transcribe(self, wav_path: str) -> str:
        async with self.lock:
            await self._ensure_started()
            if not self.proc or not self.proc.stdin or not self.proc.stdout or self.proc.returncode is not None:
                raise RuntimeError("faster-whisper worker is not running")
            timeout = _env_float("ZOE_WHISPER_TIMEOUT_S", 20.0)
            request = json.dumps({"wav_path": wav_path}) + "\n"
            try:
                self.proc.stdin.write(request.encode())
                await self.proc.stdin.drain()
                raw = await asyncio.wait_for(self.proc.stdout.readline(), timeout=timeout)
            except Exception:
                await self.stop()
                raise
            if self.proc.returncode is not None:
                code = self.proc.returncode
                await self.stop()
                raise RuntimeError(f"faster-whisper worker exited ({code})")
            try:
                payload = json.loads(raw.decode(errors="ignore"))
            except json.JSONDecodeError as exc:
                await self.stop()
                raise RuntimeError(f"faster-whisper worker returned invalid output: {raw[:300]!r}") from exc
            if payload.get("error"):
                raise RuntimeError(f"faster-whisper worker failed: {str(payload['error'])[:500]}")
            return str(payload.get("text") or "").strip()


async def _run_faster_whisper_worker(wav_path: str) -> str:
    global _faster_whisper_worker
    if _faster_whisper_worker is None:
        _faster_whisper_worker = _FasterWhisperWorker()
    return await _faster_whisper_worker.transcribe(wav_path)


async def _reset_faster_whisper_worker() -> None:
    global _faster_whisper_worker
    worker = _faster_whisper_worker
    _faster_whisper_worker = None
    if worker is not None:
        await worker.stop()


def _voice_stt_warmup_enabled() -> bool:
    return (os.environ.get("ZOE_WHISPER_WARMUP") or "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _available_memory_mb() -> Optional[int]:
    """Return roughly available system memory in MB, or None if undetectable.

    Reads /proc/meminfo (Linux-only) so we don't add a new dependency. This is
    used as a soft guard for the optional faster-whisper startup warmup so it
    can be skipped when the host is under memory pressure.
    """
    try:
        meminfo = Path("/proc/meminfo")
        if not meminfo.is_file():
            return None
        available_kb: Optional[int] = None
        total_kb: Optional[int] = None
        free_kb: Optional[int] = None
        buffers_kb: Optional[int] = None
        cached_kb: Optional[int] = None
        for raw in meminfo.read_text(errors="ignore").splitlines():
            if raw.startswith("MemAvailable:"):
                available_kb = int(raw.split()[1])
            elif raw.startswith("MemTotal:"):
                total_kb = int(raw.split()[1])
            elif raw.startswith("MemFree:"):
                free_kb = int(raw.split()[1])
            elif raw.startswith("Buffers:"):
                buffers_kb = int(raw.split()[1])
            elif raw.startswith("Cached:"):
                cached_kb = int(raw.split()[1])
        if available_kb is not None:
            return available_kb // 1024
        if total_kb is not None and free_kb is not None:
            extras = (buffers_kb or 0) + (cached_kb or 0)
            return (free_kb + extras) // 1024
        return None
    except (OSError, ValueError, IndexError):
        return None


def _should_skip_warmup_for_low_memory() -> Optional[str]:
    """Return a skip-reason string if warmup should be skipped for low memory, else None.

    Configurable via ZOE_WHISPER_WARMUP_MIN_AVAIL_MB (default 1024 MB). Set to 0
    to disable the guard. Returns None when memory is healthy or undetectable —
    we never block the warmup solely because we couldn't read /proc/meminfo.
    """
    threshold_mb = _env_float("ZOE_WHISPER_WARMUP_MIN_AVAIL_MB", 1024.0)
    if threshold_mb <= 0:
        return None
    avail_mb = _available_memory_mb()
    if avail_mb is None:
        return None
    if avail_mb < threshold_mb:
        return f"available memory {avail_mb}MB below threshold {int(threshold_mb)}MB"
    return None


def _write_warmup_silence_wav(path: str, *, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    frame_count = max(1, int(sample_rate * seconds))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frame_count)


def _create_warmup_wav_path() -> str:
    """Create an empty temp wav file and return its path. Sync helper.

    Runs in a worker thread via ``asyncio.to_thread`` so it never blocks the
    event loop while allocating a temp file on disk.
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        return tmp.name


async def warm_faster_whisper_worker() -> bool:
    """Prime the persistent faster-whisper worker without blocking API startup.

    This coroutine is scheduled as a background task during lifespan startup
    (see ``main.py``). To guarantee it can never stall zoe-data's :8000 bind
    we (1) early-return when available system memory is below the configured
    threshold and (2) offload every synchronous I/O step (tempfile creation,
    silence-wav write) to a worker thread via ``asyncio.to_thread``. The
    subprocess model load itself already runs in a child process, not on the
    event-loop thread.
    """
    if not _voice_stt_warmup_enabled():
        logger.info("faster-whisper warmup disabled by ZOE_WHISPER_WARMUP")
        return False
    if _use_in_process_faster_whisper() or not _use_persistent_faster_whisper_worker():
        logger.info("faster-whisper warmup skipped; persistent worker is not active")
        return False
    skip_reason = _should_skip_warmup_for_low_memory()
    if skip_reason is not None:
        logger.warning("faster-whisper warmup skipped (%s); will warm lazily on first use", skip_reason)
        return False
    timeout = _env_float("ZOE_WHISPER_WARMUP_TIMEOUT_S", 45.0)
    tmp_path = ""
    started = time.monotonic()
    try:
        # Offload all sync I/O to a worker thread so this coroutine is pure async I/O.
        tmp_path = await asyncio.to_thread(_create_warmup_wav_path)
        await asyncio.to_thread(_write_warmup_silence_wav, tmp_path)
        await asyncio.wait_for(_run_faster_whisper_worker(tmp_path), timeout=timeout)
        logger.info("faster-whisper worker warmup completed in %.2fs", time.monotonic() - started)
        return True
    except asyncio.CancelledError:
        await _reset_faster_whisper_worker()
        raise
    except Exception as exc:
        await _reset_faster_whisper_worker()
        logger.warning("faster-whisper worker warmup failed: %s", exc)
        return False
    finally:
        if tmp_path:
            try:
                # ``os.unlink`` is sync but tiny; safe to run inline.
                os.unlink(tmp_path)
            except OSError:
                pass


async def _run_faster_whisper_in_process(wav_path: str) -> str:
    """Transcribe using the faster-whisper Python library in the API worker."""
    model = await _get_faster_whisper_model()
    if model is None:
        raise RuntimeError("faster-whisper not available; install with: pip install faster-whisper")
    lang = (os.environ.get("ZOE_WHISPER_LANG") or "en").strip()
    vad_threshold = _env_float("ZOE_WHISPER_VAD_THRESHOLD", 0.50)
    min_speech_ms = _env_int("ZOE_WHISPER_MIN_SPEECH_MS", 120)
    min_silence_ms = _env_int("ZOE_WHISPER_MIN_SILENCE_MS", 350)
    speech_pad_ms = _env_int("ZOE_WHISPER_SPEECH_PAD_MS", 220)
    timeout = _env_float("ZOE_WHISPER_TIMEOUT_S", 20.0)

    _MAX_STT_OOM_RETRIES = 2

    def _transcribe_sync():
        for attempt in range(_MAX_STT_OOM_RETRIES + 1):
            try:
                # Release any cached CUDA blocks before inference to reduce peak pressure.
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                segments, _info = model.transcribe(
                    wav_path,
                    language=lang,
                    beam_size=int(os.environ.get("ZOE_WHISPER_BEAM_SIZE", "5")),
                    vad_filter=True,
                    vad_parameters={
                        "threshold": vad_threshold,
                        "min_speech_duration_ms": min_speech_ms,
                        "min_silence_duration_ms": min_silence_ms,
                        "speech_pad_ms": speech_pad_ms,
                    },
                )
                return " ".join(seg.text.strip() for seg in segments).strip()
            except RuntimeError as exc:
                msg = str(exc)
                if ("memory" in msg.lower() or "allocation" in msg.lower()) and attempt < _MAX_STT_OOM_RETRIES:
                    logger.warning("STT CUDA OOM attempt %d/%d — retrying in 500ms: %s", attempt + 1, _MAX_STT_OOM_RETRIES, msg[:120])
                    import time as _time
                    _time.sleep(0.5)
                    continue
                raise

    loop = asyncio.get_event_loop()
    text = await asyncio.wait_for(
        loop.run_in_executor(None, _transcribe_sync),
        timeout=timeout,
    )
    return text


async def _run_faster_whisper(wav_path: str) -> str:
    """Transcribe using faster-whisper when whisper.cpp is unavailable."""
    if _use_in_process_faster_whisper():
        return await _run_faster_whisper_in_process(wav_path)
    if _use_persistent_faster_whisper_worker():
        return await _run_faster_whisper_worker(wav_path)
    return await _run_faster_whisper_subprocess(wav_path)



def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _voice_stt_log_path() -> Path:
    configured = (os.environ.get("ZOE_VOICE_STT_LOG") or "").strip()
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
            "model": _stt_backend_var.get() or (os.environ.get("ZOE_STT_BACKEND") or "moonshine").strip(),
            "device": (os.environ.get("ZOE_WHISPER_DEVICE") or "").strip(),
            "compute_type": (os.environ.get("ZOE_WHISPER_COMPUTE_TYPE") or "").strip(),
            "vad_threshold": _env_float("ZOE_WHISPER_VAD_THRESHOLD", 0.50),
            "min_speech_ms": _env_int("ZOE_WHISPER_MIN_SPEECH_MS", 120),
            "min_silence_ms": _env_int("ZOE_WHISPER_MIN_SILENCE_MS", 350),
            "speech_pad_ms": _env_int("ZOE_WHISPER_SPEECH_PAD_MS", 220),
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
_moonshine_lock = threading.Lock()


def _ensure_moonshine():
    """Lazily build the Moonshine v2 transcriber (MEDIUM_STREAMING by default —
    accurate on real room audio, ~0.5s/clip). Locked so a concurrent first call
    can't race the model load."""
    global _moonshine_model
    if _moonshine_model is not None:
        return _moonshine_model
    with _moonshine_lock:
        if _moonshine_model is None:
            import moonshine_voice as mv
            from moonshine_voice.transcriber import Transcriber

            archname = (os.environ.get("ZOE_MOONSHINE_ARCH") or "MEDIUM_STREAMING").strip()
            arch = getattr(mv.ModelArch, archname, mv.ModelArch.MEDIUM_STREAMING)
            model_path, resolved_arch = mv.get_model_for_language("en", arch)
            _moonshine_model = Transcriber(model_path, resolved_arch)
    return _moonshine_model


async def _run_moonshine(wav_path: str) -> str:
    from moonshine_voice.utils import load_wav_file

    def _work() -> str:
        tr = _ensure_moonshine()
        audio, sr = load_wav_file(wav_path)
        out = tr.transcribe_without_streaming(audio, sr)
        text = getattr(out, "text", None)
        if not (isinstance(text, str) and text.strip()):
            try:
                text = " ".join(line.text for line in out.lines)
            except Exception:
                text = str(out)
        return (text or "").strip()

    return await asyncio.get_running_loop().run_in_executor(None, _work)


async def warm_moonshine() -> bool:
    """Pre-load the Moonshine STT model+tokenizer so the first panel turn isn't
    cold (saves the ~1-2s ONNX session load on the first utterance)."""
    if (os.environ.get("ZOE_STT_BACKEND") or "moonshine").strip().lower() != "moonshine":
        logger.info("Moonshine warmup skipped; ZOE_STT_BACKEND is not moonshine")
        return False
    started = time.monotonic()
    try:
        await asyncio.get_running_loop().run_in_executor(None, _ensure_moonshine)
        logger.info("Moonshine STT warmup completed in %.2fs", time.monotonic() - started)
        return True
    except Exception as exc:
        logger.warning("Moonshine STT warmup failed (non-fatal): %s", exc)
        return False


async def _maybe_capture_stt(wav_path: str, primary: str) -> None:
    """Diagnostic A/B: when ZOE_VOICE_SAVE_AUDIO is set, save the real utterance
    and log the primary (Moonshine) result alongside whisper's, so we can tell —
    on the user's actual room audio — which is more accurate and whether the
    pre-roll is corrupting it. Logs at WARNING so it's visible past the level."""
    if (os.environ.get("ZOE_VOICE_SAVE_AUDIO") or "").strip().lower() not in ("1", "true", "yes", "on"):
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

    # Run the whisper A/B on the COPY fire-and-forget, so it never adds latency to
    # the live transcription path (the whole point of the Moonshine v2 win).
    async def _ab() -> None:
        try:
            alt = await _run_faster_whisper(dst)
        except Exception as exc:
            alt = f"<whisper err: {exc}>"
        logger.warning("STT_AB file=%s moonshine=%r whisper=%r", dst, (primary or "")[:90], (alt or "")[:90])

    _spawn_bg(_ab())


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
    """
    Transcription waterfall:
    0. Moonshine ONNX (default; edge real-time, CPU) — falls through on error
    1. whisper.cpp CLI (if binary + ggml model configured)
    2. faster-whisper Python (auto-downloaded model, GPU/CPU)
    """
    if (os.environ.get("ZOE_STT_BACKEND") or "moonshine").strip().lower() == "moonshine":
        try:
            text = await _run_moonshine(wav_path)
            if text:
                _stt_backend_var.set("moonshine:" + (os.environ.get("ZOE_MOONSHINE_ARCH") or "v2").strip())
                return text
            logger.warning("Moonshine STT returned empty; falling back to whisper")
        except Exception as exc:
            logger.warning("Moonshine STT failed (%s); falling back to whisper", exc)
    if _whisper_cpp_binary():
        model = (os.environ.get("ZOE_WHISPER_MODEL") or "").strip()
        if model and os.path.isfile(model):
            _stt_backend_var.set("whisper.cpp:" + os.path.basename(model))
            return await _run_whisper_cpp(wav_path)
    _stt_backend_var.set("faster-whisper:" + (os.environ.get("ZOE_WHISPER_MODEL") or "base.en").strip())
    return await _run_faster_whisper(wav_path)


@router.post("/transcribe")
async def voice_transcribe(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """
    Transcribe base64 WAV/PCM audio using whisper.cpp on the Jetson (Pi voice daemon).
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
                "url": f"/touch/people.html?person={person_id}&intro=1",
            },
            "panel_id": panel_id,
            "turn_key": turn_key,
        })
    except Exception:
        pass

    return person_id


async def _schedule_voice_chat_save(
    session_id: str, user_text: str, reply: str, user_id: str
) -> None:
    """Fire-and-forget: persist both turns of a voice exchange to chat_messages.

    Called from every exit path in voice_command so the full transcript ends up
    in the DB regardless of which fast-path handled the turn. The nightly digest
    and _load_voice_history both read from chat_messages, so this is the single
    fix that unblocks multi-turn context, transcript search, and nightly extraction.
    """
    if not session_id or user_id in ("guest", "voice-daemon", ""):
        return
    try:
        from chat import _save_chat_message as _svc  # lazy — avoids circular import
        if user_text:
            _spawn_bg(_svc(session_id, "user", user_text))
        if reply:
            _spawn_bg(_svc(session_id, "assistant", reply))
    except Exception:
        pass


async def _run_voice_memory_passes(
    user_text: str, reply: str, user_id: str, session_id: str
) -> None:
    """Run both memory extraction passes for a completed voice exchange.

    Standalone (non-nested) version so it can be called from any early-return
    path — not just the main LLM path at the bottom of voice_command.
    """
    try:
        from memory_extractor import extract_and_ingest as _mi
        from memory_digest import run_turn_digest as _td
        from person_extractor import process_text as _person_extract
        from person_extractor_llm import process_text_llm as _person_extract_llm
        from latent_intent_detector import detect_and_store as _detect_suggestions
        combined = f"{user_text}\n{reply}".strip()
        await asyncio.gather(
            _mi(user_text, reply, user_id=user_id, session_id=session_id,
                source="voice_regex", auto_approve=True),
            _td(user_id, user_text, reply, session_id=session_id,
                source="voice_turn_digest"),
            _person_extract(
                combined,
                user_id=user_id,
                source="voice",
                session_id=session_id,
            ),
            _person_extract_llm(
                combined,
                user_id=user_id,
                source="voice",
                session_id=session_id,
            ),
            return_exceptions=True,
        )
        _spawn_bg(_detect_suggestions(
            user_text,
            user_id=user_id,
            session_id=session_id,
        ))
    except Exception:
        pass


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
    _bound_user = (_VOICE_SESSIONS.get(panel_id) or {}).get("bound_user_id")
    _panel_recent_user = await _resolve_recent_panel_session_user(panel_id, db)
    if not _bound_user and _panel_recent_user:
        _ses = _VOICE_SESSIONS.get(panel_id)
        if _ses is not None:
            _ses["bound_user_id"] = _panel_recent_user
        _bound_user = _panel_recent_user
    _panel_default_user = await _resolve_panel_default_user(panel_id, db)
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

    logger.info(
        "voice/command panel=%s session=%s user=%s len=%d "
        "[identity: identified=%s bound=%s panel_recent=%s panel_default=%s scope_user=%s has_scope=%s]",
        panel_id, session_id, effective_user, len(text),
        identified_user_id, _bound_user, _panel_recent_user, _panel_default_user,
        _scope_identity_user, _has_scope_identity,
    )

    # Persist user turn to chat_messages immediately so all downstream paths
    # (nightly digest, _load_voice_history, multi-turn context) have the transcript.
    if text and effective_user not in ("guest", "voice-daemon", ""):
        try:
            from chat import _save_chat_message as _svc_user_turn
            _spawn_bg(_svc_user_turn(session_id, "user", text))
        except Exception:
            pass

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
        _skybridge_result = await resolve_skybridge_request(
            text,
            _skybridge_user,
            context=_skybridge_context,
            db=db,
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
            if not await _can_use_voice_intent(db, _voice_policy_user, _pub_intent.name):
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
        # channel="voice" keeps run_tier0=False — voice already ran its own richer
        # public-intent Tier-0 (policy/scope gates) above — so this stays the
        # byte-identical Tier-1/1.5 path. Reuse the router decision from the shadow
        # log and keep the dispatch ctx identical via extra_ctx (db/panel_id).
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
                _scope_allowed = await _can_use_voice_intent(db, _voice_policy_user, _scope.intent_name)
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

                try:
                    async def _emit_sentence(sentence: str):
                        nonlocal chunk_index, _t_first_audio
                        s = sentence.strip()
                        if not s:
                            return
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

    command_payload = {
        "text": transcript,
        "panel_id": panel_id,
        "_t_turn_start": t_turn_start,
    }
    if (payload or {}).get("identified_user_id"):
        command_payload["identified_user_id"] = payload["identified_user_id"]

    # Delegate LLM + per-sentence TTS to the existing streaming pipeline.
    # (voice_command's stream generator records the downstream llm_first_token /
    # tts_first_byte / total stage metrics + a path="command" turn count.)
    sub = await voice_command(command_payload, caller=caller, stream=True, db=db)
    try:
        from voice_metrics import voice_turn_count
        voice_turn_count.labels(outcome="ok", path="turn_stream").inc()
    except Exception:
        pass

    async def _wrapped():
        # Lead with the transcript so the panel can show/log what was heard
        # before the first audio chunk arrives.
        yield (_json.dumps({"transcript": transcript}) + "\n").encode()
        if hasattr(sub, "body_iterator"):
            async for chunk in sub.body_iterator:
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
            # Skybridge fast-path returned text only (stream mode) — synthesize it
            # sentence-by-sentence so the panel starts speaking in ~0.6s instead of
            # waiting for the whole reply to synthesize.
            _chunk = 0
            for _sentence in _split_sentences(reply_text):
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

    return StreamingResponse(
        _wrapped(), media_type="application/x-zoe-audio-stream", headers={"Cache-Control": "no-cache"}
    )


def _brain_prewarm_on_wake_enabled() -> bool:
    return os.environ.get("ZOE_BRAIN_PREWARM_ON_WAKE", "1").strip().lower() in ("1", "true", "yes", "on")


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
            zoe_core_client.prewarm(user_id, session_id),
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


@router.post("/wake")
async def voice_wake(payload: dict, caller: dict = Depends(_require_voice_auth)):
    """
    Signal from the Pi daemon that the wake word was detected.
    Used to update panel state (show orb animation, open mic indicator).
    Returns cached or fallback TTS audio for the wake acknowledgement.
    """
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    ack_phrase = os.environ.get("ZOE_WAKE_ACK_PHRASE", "").strip()
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
    Raw audio is transcribed by Whisper, then stored in ambient_memory.
    Raw audio bytes are discarded after transcription — only text is kept.
    """
    from database import get_db

    b64 = str((payload or {}).get("audio_base64", "")).strip()
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or "unknown"))
    room = str((payload or {}).get("room", "")).strip() or None
    duration_s = float((payload or {}).get("duration_seconds", 0.0))

    if not b64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    # Transcribe via whisper.cpp.
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
        transcript = await _run_whisper_cpp(wav_path)
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

    # Store in ambient_memory table.
    try:
        from db_compat import get_compat_db as _get_compat_db
        
        async with _get_compat_db() as db:
            await db.execute(
                """INSERT INTO ambient_memory (panel_id, room, transcript, duration_seconds, source)
                   VALUES (?, ?, ?, ?, 'ambient')""",
                (panel_id, room, transcript, duration_s),
            )
            await db.commit()
        logger.debug("ambient_memory: panel=%s chars=%d", panel_id, len(transcript))
    except Exception as exc:
        logger.warning("ambient_memory insert failed: %s", exc)

    return {"ok": True, "panel_id": panel_id, "transcript": transcript}


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

    b64 = str((payload or {}).get("audio_base64", "")).strip()
    user_id = str((payload or {}).get("user_id", caller.get("user_id", "unknown")))
    display_name = str((payload or {}).get("display_name", user_id)).strip() or user_id
    panel_id = str((payload or {}).get("panel_id", caller.get("panel_id") or ""))

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
                profile_id = old_id
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
                "SELECT id, user_id, display_name, embedding_blob FROM speaker_profiles"
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
    threshold = float(os.environ.get("ZOE_SPEAKER_ID_THRESHOLD", "0.82"))
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


@router.get("/profiles")
async def voice_profiles(caller: dict = Depends(_require_voice_auth)):
    """List enrolled speaker profiles (id, user_id, display_name, sample_count).

    Used by the settings page Voice Identity section to show who is enrolled.
    """
    from db_compat import get_compat_db as _get_compat_db

    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT id, user_id, display_name, sample_count, panel_id FROM speaker_profiles ORDER BY display_name"
            ) as cur:
                rows = await cur.fetchall()
        return {
            "ok": True,
            "profiles": [
                {"id": r[0], "user_id": r[1], "display_name": r[2],
                 "sample_count": r[3] or 1, "panel_id": r[4]}
                for r in rows
            ]
        }
    except Exception as exc:
        logger.error("voice/profiles DB error: %s", exc)
        raise HTTPException(status_code=500, detail="DB error") from exc


@router.delete("/profiles/{profile_id}")
async def voice_profile_delete(profile_id: str, caller: dict = Depends(_require_voice_auth)):
    """Delete an enrolled speaker profile."""
    from db_compat import get_compat_db as _get_compat_db

    try:
        async with _get_compat_db() as db:
            await db.execute("DELETE FROM speaker_profiles WHERE id=?", (profile_id,))
            await db.commit()
        return {"ok": True, "deleted": profile_id}
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

    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "").strip()

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
    env_url = os.environ.get("LIVEKIT_URL", "").strip()
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
