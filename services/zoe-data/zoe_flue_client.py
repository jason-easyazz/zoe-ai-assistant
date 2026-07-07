"""Flue brain client — the cutover seam to the Flue Zoe-brain sidecar.

This is the OPT-IN alternative to ``zoe_core_client`` (the Pi-CLI brain). It is
selected ONLY when ``ZOE_BRAIN_BACKEND == 'flue'`` (see ``brain_dispatch`` /
``routers.chat``); with the env unset or ``'core'`` this module is never reached
and the live brain path is byte-identical to today.

The Flue sidecar (``labs/flue-zoe-brain``) serves::

    POST {base}/agents/zoe/<session>?wait=result
    body: {"message": "..."}
    -> {"result": {"text": "..."}}

Its route fails closed unless ``ZOE_BRAIN_OPEN=1`` or a matching
``Authorization: Bearer <ZOE_BRAIN_TOKEN>`` is presented, so this client sends
the bearer token from ``ZOE_BRAIN_TOKEN`` when set.

Stream shape parity
-------------------
``run_zoe_core_streaming`` is an async generator that yields plain text deltas
plus optional ``__TOOL__`` / ``__THINKING__`` sentinel strings. The Flue sidecar
(``?wait=result``) returns the FULL reply text in one shot and does not expose
tool/thinking events yet, so we yield that text as a single delta. The shape is
identical (an async iterator of ``str``); the caller's sentinel handlers simply
see no sentinels. If/when the sidecar exposes streaming or tool events, map them
here to the same sentinels (see ``zoe_core_client._read_turn``).

Failures are caught and surfaced as a short error string delta rather than
raised — a brain backend hiccup must never crash a turn.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, AsyncIterator
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Read lazily (NOT at import) so a .env value bootstrapped after import is honored
# — bootstrap_runtime_env() populates os.environ in lifespan startup, which runs
# after this module is imported.
_DEFAULT_BASE_URL = "http://127.0.0.1:3578"
_DEFAULT_TIMEOUT_S = 180.0

# Graceful, user-facing fallback emitted whenever a flue turn cannot produce a
# usable reply — transport/parse error OR an HTTP 200 with empty text. Shared so
# both failure surfaces render identically instead of one blanking the turn.
_FALLBACK_TEXT = "Sorry, I had trouble reaching my brain just now. Could you try again?"


def _base_url() -> str:
    return (os.environ.get("ZOE_FLUE_BRAIN_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _bearer_token() -> str:
    return (os.environ.get("ZOE_BRAIN_TOKEN") or "").strip()


def _timeout_s() -> float:
    try:
        return float(os.environ.get("ZOE_FLUE_BRAIN_TIMEOUT_S", _DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _endpoint(session_id: str) -> str:
    sid = (session_id or "default").strip() or "default"
    # URL-encode the sid as a single path segment: a raw session id containing
    # '/', '?', '#', or '..' would otherwise change the route (path traversal /
    # query injection) instead of addressing that literal Flue session.
    return f"{_base_url()}/agents/zoe/{quote(sid, safe='')}?wait=result"


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = _bearer_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# Machine-readable acting-identity envelope. MUST match the sidecar's parser
# (labs/flue-zoe-brain src/request-identity.ts IDENTITY_ENVELOPE_PREFIX / _RE):
# a leading " zoe-uid:<id>\n" line the sidecar reads then strips before the model
# sees the message. Kept here so the trusted user_id rides the one field Flue
# persists into the agent fiber (the message) rather than a body field it drops.
_IDENTITY_ENVELOPE_PREFIX = " zoe-uid:"


def _wrap_message_with_identity(message: str, user_id: str) -> str:
    """Prefix ``message`` with the acting-identity envelope, or return it unchanged.

    An empty/blank ``user_id`` yields the message untouched so the sidecar falls
    back to its env identity. The id is placed on its own leading line terminated
    by a newline, matching the sidecar's single-line regex.
    """
    # Strip embedded CR/LF too: .strip() only trims the ends, but a newline inside
    # the id would terminate the single-line envelope early and leak the remainder
    # into the prompt the model sees. Keeps the envelope contract tight on both sides.
    uid = (user_id or "").strip().replace("\n", "").replace("\r", "")
    if not uid:
        return message
    return f"{_IDENTITY_ENVELOPE_PREFIX}{uid}\n{message}"


# ── Deterministic recall floor (ZOE_SEAM_RECALL_INJECT, default OFF) ─────────
#
# BUG B (live hard-gate 2026-07-07): "my locker code is 31999" sat at the TOP
# of the /api/memories/for-prompt packet, yet the flue brain answered "I don't
# have that stored" — the model simply didn't call its recall_memory tool that
# turn (the known ~97% invocation ceiling; prompt doctrine already pushed).
# Tool-gated recall can never be 100% on a 4B model, so on a conservative
# personal-question shape the SEAM prepends the for-prompt packet to the
# outbound message deterministically. The recall_memory tool stays for deeper
# queries — this is a floor, not a replacement.
#
# ENVELOPE CONTRACT: the block is placed AFTER the identity line. The sidecar's
# stripIdentityEnvelope (labs/flue-zoe-brain/src/request-identity.ts) matches
# `^ zoe-uid:<id>\n` anchored at the START of the message, so the wire order is
# " zoe-uid:<id>\n<block>\n<user message>" — the sidecar strips only the
# identity line and the model reads block + message.
#
# Flag-gated, DEFAULT OFF: the operator enables ZOE_SEAM_RECALL_INJECT via env
# only after the replay gate passes. Flag off = byte-identical outbound message.
_RECALL_INJECT_ENV = "ZOE_SEAM_RECALL_INJECT"

# Conservative personal-question shapes only — each alternative pins a
# possessive/self reference ("my", "I", "me"), so ordinary chat ("what is the
# weather", "who is Ada Lovelace") never matches.
_PERSONAL_QUESTION_RE = re.compile(
    r"\b(?:"
    r"what'?s\s+my|what\s+is\s+my|"
    # "do you remember" must itself anchor to self-reference — bare
    # "do you remember the alamo" is general chat, not personal recall.
    r"do\s+you\s+remember\s+(?:my|(?:that|what|when|where|if)\s+i)\b|"
    r"what\s+did\s+i|"
    r"when\s+did\s+i|when'?s\s+my|when\s+is\s+my|where\s+do\s+i|"
    r"who'?s\s+my|who\s+is\s+my|what\s+do\s+you\s+know\s+about\s+me"
    r")",
    re.IGNORECASE,
)

_RECALL_BLOCK_OPEN = (
    "[MEMORY CONTEXT — Zoe's stored notes about this user; "
    "use them to answer; do not mention this block]"
)
_RECALL_BLOCK_CLOSE = "[END MEMORY CONTEXT]"
_RECALL_MAX_BULLETS = 12
_RECALL_MAX_CHARS = 1600


def _recall_inject_enabled() -> bool:
    """Per-call env read (matches the module's other env lookups) so the
    operator can flip the flag with a restart, no code change."""
    return (os.environ.get(_RECALL_INJECT_ENV) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def _fetch_for_prompt_packet(user_id: str, message: str) -> str:
    """The /api/memories/for-prompt packet text, fetched IN-PROCESS.

    Calls the composer function directly (routers.memories.memory_for_prompt)
    instead of an HTTP self-call — same event loop, no socket round-trip.
    ``_=None`` skips the FastAPI internal-token dependency, which guards the
    HTTP surface, not in-process callers; the endpoint itself fails closed for
    guest/unknown users (empty packet). Lazy import keeps this module
    slim-importable for tests.
    """
    from routers.memories import memory_for_prompt

    result = await memory_for_prompt(
        user_id=user_id,
        message=(message or "")[:512],
        limit=_RECALL_MAX_BULLETS,
        _=None,
    )
    return str((result or {}).get("packet") or "")


def _truncate_packet(packet: str) -> str:
    """Cap the packet at _RECALL_MAX_BULLETS bullet lines / _RECALL_MAX_CHARS."""
    lines: list[str] = []
    bullets = 0
    total = 0
    for line in packet.splitlines():
        if line.lstrip().startswith(("-", "•", "*")):
            bullets += 1
            if bullets > _RECALL_MAX_BULLETS:
                break
        total += len(line) + 1
        if lines and total > _RECALL_MAX_CHARS:
            break
        lines.append(line)
    return "\n".join(lines).strip()


async def _recall_context_block(message: str, user_id: str) -> str:
    """The delimited memory block for this turn, or '' — NEVER raises.

    '' unless the flag is ON, a real user id is present, and the message
    matches the conservative personal-question shape. A fetch failure logs and
    returns '' — the turn always proceeds, at worst without the floor.
    """
    if not _recall_inject_enabled():
        return ""
    if not (user_id or "").strip():
        return ""
    if not _PERSONAL_QUESTION_RE.search(message or ""):
        return ""
    try:
        packet = await _fetch_for_prompt_packet(user_id, message)
    except Exception as exc:  # noqa: BLE001 — the recall floor must never break a turn
        logger.warning(
            "seam recall inject: packet fetch failed, continuing without it: %s", exc
        )
        return ""
    packet = _truncate_packet((packet or "").strip())
    if not packet:
        return ""
    return f"{_RECALL_BLOCK_OPEN}\n{packet}\n{_RECALL_BLOCK_CLOSE}"


def _text_from_body(body: Any) -> str:
    """Pull the reply text out of the sidecar's {result:{text}} envelope.

    Defensive about shape: accepts {result:{text}}, {result:"..."}, or a bare
    {text}/string so a minor sidecar change doesn't blank the turn.
    """
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""
    result = body.get("result", body)
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text
        # Fall back to a stringy nested field if present.
        for key in ("output", "content", "message"):
            val = result.get(key)
            if isinstance(val, str):
                return val
        return ""
    if isinstance(result, str):
        return result
    return ""


async def run_flue_brain_streaming(
    message: str,
    session_id: str,
    user_id: str = "",
    **kwargs: Any,
) -> AsyncIterator[str]:
    """Streaming brain turn through the Flue sidecar.

    Drop-in for ``run_zoe_core_streaming``: yields text deltas (and, in future,
    ``__TOOL__`` / ``__THINKING__`` sentinels if the sidecar exposes them). The
    ``?wait=result`` endpoint returns the whole reply at once, so we yield it as
    one delta. Errors/timeouts are caught and yielded as a short error string —
    never raised — so a backend hiccup can't crash a turn.

    The acting ``user_id`` is forwarded to the sidecar so it can bind per-request
    identity (whose memory/tools to touch) instead of falling back to a single
    process-wide ``ZOE_BRAIN_USER_ID``. Extra kwargs (history, db_memory_context,
    portrait, voice_mode, callbacks, etc.) are accepted for
    run_zoe_core_streaming signature compatibility; the sidecar owns its own
    persona/memory/tools, so they're intentionally ignored here.
    """
    url = _endpoint(session_id)
    # Forward the caller's identity when known so the sidecar isn't pinned to one
    # env-configured user. The id is carried as an ENVELOPE PREFIX on the message,
    # not a separate body field: the sidecar's Flue payload schema accepts only
    # {message, images} and silently drops any other field, so a top-level
    # ``user_id`` never reaches the agent fiber. The sidecar reads this prefix and
    # strips it before the model sees the text (see labs/flue-zoe-brain
    # src/request-identity.ts wrapMessageWithIdentity / forwardedIdentityFromMessages).
    # Keep the format byte-for-byte in sync with that module. Omit empty/guest ids
    # so the sidecar's own fail-closed identity handling applies.
    uid = (user_id or "").strip()
    # Deterministic recall floor (default OFF): on a personal-question turn,
    # prepend the for-prompt packet so recall no longer depends on the model
    # electing to call its recall_memory tool. Placed BEFORE the identity wrap
    # so the block rides AFTER the identity line on the wire (the sidecar's
    # single-line strip regex is anchored at message start).
    recall_block = await _recall_context_block(message, uid)
    brain_message = f"{recall_block}\n{message}" if recall_block else message
    outbound_message = _wrap_message_with_identity(brain_message, uid)
    body_obj: dict[str, str] = {"message": outbound_message}
    payload = json.dumps(body_obj).encode()
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            resp = await client.post(url, content=payload, headers=_headers())
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:  # noqa: BLE001 - a brain hiccup must never crash a turn
        logger.warning("flue brain turn failed: %s", exc)
        yield _FALLBACK_TEXT
        return

    text = _text_from_body(body)
    if text:
        yield text
        return

    # HTTP 200 but no usable text (e.g. {"result": {}} or {"result": {"text": ""}}).
    # The streaming chat path has already opened a text message; ending it with
    # zero chunks would render a blank assistant turn. Treat an empty successful
    # result as a failed brain turn and emit the same graceful fallback we use for
    # transport/parse errors, so the user always gets a coherent reply.
    logger.warning("flue brain returned an empty result; treating as a failed turn")
    yield _FALLBACK_TEXT


async def run_flue_brain(
    message: str,
    session_id: str,
    user_id: str = "",
    **kwargs: Any,
) -> str:
    """Non-streaming brain turn — collects the Flue stream into one string."""
    chunks: list[str] = []
    async for delta in run_flue_brain_streaming(message, session_id, user_id, **kwargs):
        chunks.append(delta)
    return "".join(chunks).strip()
