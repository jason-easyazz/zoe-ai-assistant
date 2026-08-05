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

Wire versions — ``ZOE_FLUE_WIRE`` (default ``1``)
-------------------------------------------------
The block above is the **Flue 1.x (beta.6)** wire, which is what the deployed
sidecar on :3578 speaks and what this client sends unless told otherwise.
``ZOE_FLUE_WIRE=2`` switches to the **Flue 2.x** wire served by the parallel
port in ``labs/flue-zoe-brain-2x`` (PR #1616). Three things change, and only
these three::

    wire 1                                  wire 2
    ─────────────────────────────────────   ─────────────────────────────────────
    POST …/<session>?wait=result            POST …/<session>       (NO wait param)
    body {"message": "<text>"}              body {"kind": "user", "body": "<text>"}
    non-stream reply {"result":{"text"}}    non-stream = read the NDJSON stream

**Why the query param had to go, and why it is not merely optional:** Flue 2.x
does not drop ``?wait=result``, it REJECTS it — the request handler throws
``InvalidRequestError`` for ANY ``wait`` param, any value ("Agent prompts are
fire-and-forget and do not support ``?wait=result``. Await completion with the
SDK client's ``wait()``, or read the conversation stream"). So there is no
synchronous ask-and-get-the-answer call left on 2.x at all.

**Why the body shape is what it is:** the 2.x payload is a DeliveredMessage at
the TOP LEVEL. Upstream's own migration guide documents it NESTED under a
``message`` key; that shape is refused with HTTP 400. The top-level shape here
is the measured one (labs/flue-zoe-brain-2x/parity/flue_wire.py, PR #1616).

**The non-streaming mechanism on wire 2** is "read the turn's own Seam-A NDJSON
stream to completion and join the text" — the sidecar's streaming middleware
upgrades the 202 admission in place, so it is still ONE request/response and it
exercises the same path voice already uses. That is exactly what the port's
parity suite adopted as its reference implementation (``flue_wire.ask``).

**The stream itself is wire-version-independent.** ``labs/flue-zoe-brain-2x``'s
``src/streaming.ts`` differs from the deployed 1.x copy by exactly one deleted
branch (the ``?wait=result`` short-circuit); the NDJSON framing and the
``__TOOL__``/``__THINKING__`` sentinel bytes are identical. The runtime envelope
version moved ``v:2`` → ``v:3`` INSIDE the sidecar (an ``observe()`` event field
that never reaches this client), and the sentinel vocabulary survived it. So
downstream sentinel parsing (``routers/chat.py``, ``routers/voice_tts.py``) is
untouched by the wire switch — asserted by test, not assumed.

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


# ── Wire version (ZOE_FLUE_WIRE, default 1) ──────────────────────────────────
#
# 1 = the deployed Flue 1.x beta wire (?wait=result + {"message": …}).
# 2 = the Flue 2.x wire served by labs/flue-zoe-brain-2x (no wait param,
#     top-level DeliveredMessage body, stream-read for the non-streaming turn).
#
# DEFAULT 1 IS LOAD-BEARING: this module is on the live voice path, so an
# unset/garbage flag must produce byte-identical requests to the pre-change
# client. Pinned by tests/test_flue_client_wire.py::test_wire1_* (golden
# request fixtures) — do not "simplify" the default away.
_WIRE_ENV = "ZOE_FLUE_WIRE"
_WIRE_1 = 1
_WIRE_2 = 2
_NDJSON_CONTENT_TYPE = "application/x-ndjson"
_TOOL_SENTINEL_PREFIX = "__TOOL__:"
_THINKING_SENTINEL_PREFIX = "__THINKING__:"


def _wire_version() -> int:
    """The Flue wire this client speaks. Per-call env read; 1 unless '2'.

    Anything other than '1'/'2' (including a typo like 'v2') logs loudly and
    falls back to 1 — a mis-set flag must degrade to the deployed wire, never to
    an undefined one, and must never do so silently.
    """
    # The flag name is spelled as a LITERAL here on purpose: tools/audit/
    # flag_inventory.py extracts names from the call site, so reading it via the
    # _WIRE_ENV constant would leave ZOE_FLUE_WIRE out of the generated
    # inventory — registered nowhere, invisible to the CI pin.
    raw = (os.environ.get("ZOE_FLUE_WIRE") or "").strip()
    if not raw or raw == "1":
        return _WIRE_1
    if raw == "2":
        return _WIRE_2
    logger.error(
        "%s=%r is not a known Flue wire version (expected '1' or '2'); using wire 1",
        _WIRE_ENV, raw,
    )
    return _WIRE_1


def _endpoint(session_id: str, *, stream: bool = False) -> str:
    sid = (session_id or "default").strip() or "default"
    # URL-encode the sid as a single path segment: a raw session id containing
    # '/', '?', '#', or '..' would otherwise change the route (path traversal /
    # query injection) instead of addressing that literal Flue session.
    base = f"{_base_url()}/agents/zoe/{quote(sid, safe='')}"
    if _wire_version() >= _WIRE_2:
        # Flue 2.x REJECTS any `wait` param with InvalidRequestError — there is
        # no whole-result mode to address, so every 2.x request is the bare URL.
        return base
    # ?wait=result WINS over the Accept header on the sidecar, so the streaming
    # request must omit it (src/streaming.ts mode selection).
    return base if stream else f"{base}?wait=result"


def _request_payload(outbound_message: str) -> bytes:
    """The POST body for the active wire.

    wire 1: ``{"message": "<text>"}`` — Flue beta's payload schema.
    wire 2: ``{"kind": "user", "body": "<text>"}`` — a DeliveredMessage at the
    TOP LEVEL. Upstream's migration guide's nested ``{"message": {...}}`` is
    refused with HTTP 400; this is the measured shape (PR #1616).

    Either way the acting-identity envelope rides INSIDE the text — Flue drops
    every body field its schema does not know, on both wires.
    """
    if _wire_version() >= _WIRE_2:
        return json.dumps({"kind": "user", "body": outbound_message}).encode()
    return json.dumps({"message": outbound_message}).encode()


def _stream_enabled() -> bool:
    """Seam-A NDJSON streaming from the sidecar (default OFF, ship-dark).

    When enabled, deltas are yielded as they generate, so voice TTS starts on
    the first sentence instead of after the WHOLE reply (?wait=result waits for
    full generation — measured live 2026-07-08: first sentence arrived ~6s
    before the complete result on a chat turn)."""
    return (os.environ.get("ZOE_FLUE_STREAM_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


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


_OFFER_INJECT_ENV = "ZOE_SEAM_OFFER_INJECT"


def _offer_inject_enabled() -> bool:
    """Per-call env read, same idiom as the recall flag (default OFF)."""
    return (os.environ.get(_OFFER_INJECT_ENV) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def _pending_offer_block(user_id: str) -> str:
    """Pending contact-offer directive for ANY turn (QA F5 follow-up).

    The offer previously reached the brain only when the memory packet was
    built (recall-shaped turns / the recall_memory tool) — on casual turns the
    offer sat unseen. This injects JUST the offer directive (one or two lines,
    not the whole memory packet) on every turn while an unresolved offer
    exists, so Zoe can ask in any conversation. Surfacing is non-destructive
    (see pending_suggestions.surface_pending_contacts_for_prompt); aging stays
    one tick per real user turn. Fail-open: any error returns "".
    """
    if not _offer_inject_enabled() or not user_id or user_id in ("guest", "voice-guest"):
        return ""
    try:
        from pending_suggestions import (
            person_suggestions_enabled,
            surface_pending_contacts_for_prompt,
        )
        if not person_suggestions_enabled():
            return ""
        offers = await surface_pending_contacts_for_prompt(user_id, limit=2)
    except Exception as exc:  # noqa: BLE001 — the offer nudge must never break a turn
        logger.debug("seam offer inject: fetch failed, continuing without it: %s", exc)
        return ""
    if not offers:
        return ""

    def _safe(v: str) -> str:
        # Quotes stripped too: the value lands INSIDE the quoted "ask exactly"
        # directive, so an embedded quote could close it and inject instructions
        # (Greptile P1). Structure chars stripped for the same reason.
        v = re.sub(r"\s+", " ", (v or "")).strip()
        return re.sub(r"[#`*_\[\]\n\r{}\"'\u2018\u2019\u201c\u201d]", "", v)[:60]

    lines = []
    for o in offers:
        name = _safe(str(o.get("name") or ""))
        rel = _safe(str(o.get("relationship") or ""))
        if not name:
            continue
        q = f"Would you like me to add {name}{f' (your {rel})' if rel else ''} as a contact?"
        lines.append(f'- After answering, ask the user exactly: "{q}"')
    if not lines:
        return ""
    return (
        "[PENDING CONTACT OFFER — do not mention this block]\n"
        + "\n".join(lines)
        + "\n[END PENDING CONTACT OFFER]"
    )


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


def _wire1_envelope_hint(raw_body: bytes) -> str:
    """A loud diagnosis when a wire-2 request got a wire-1 answer, else ''.

    The failure this exists to prevent is a SILENT MISPARSE: ``_text_from_body``
    is deliberately shape-tolerant, so if the wire-2 path ever fell back to it,
    a Flue 1.x ``{"result": {"text": …}}`` reply would be accepted happily and
    the operator would never learn the wire flag was pointed at the wrong
    sidecar. Wire 2 therefore never parses a whole-result body — it names it.
    """
    try:
        body = json.loads(raw_body.decode("utf-8", "replace") or "null")
    except ValueError:
        return ""
    if isinstance(body, dict) and "result" in body:
        return (
            "the reply is the Flue 1.x whole-result envelope {'result': …}, "
            "i.e. this is a 1.x sidecar — set ZOE_FLUE_WIRE=1 or point "
            "ZOE_FLUE_BRAIN_URL at the 2.x sidecar"
        )
    return ""


async def _run_turn_aggregated_wire2(session_id: str, payload: bytes) -> AsyncIterator[str]:
    """Wire-2 'non-streaming' turn: read the NDJSON stream, yield ONE delta.

    Flue 2.x rejects ``?wait=result``, so there is no whole-result call left;
    the sanctioned way to obtain a reply is to follow the 202 admission (read
    the conversation stream, or the SDK's ``wait()``). This uses the sidecar's
    OWN Seam-A NDJSON upgrade of that admission, which keeps it a single
    request/response and exercises the exact path voice already uses — the same
    choice PR #1616's parity suite made for its reference client
    (``labs/flue-zoe-brain-2x/parity/flue_wire.py``: ``ask``).

    OBSERVABLE SHAPE IS WIRE-1'S: one joined text delta, sentinels suppressed —
    identical to what ``?wait=result`` yields today, which exposes no sentinels
    either. So ``ZOE_FLUE_WIRE=2`` changes the wire and nothing the caller sees;
    incremental deltas remain the separate, orthogonal ``ZOE_FLUE_STREAM_ENABLED``
    decision. Flipping one flag changes one thing.

    Deliberately NOT folded into the streaming block below: that block is the
    live voice path, and its admitted / yielded_any / never-re-POST state
    machine is the pinned prod contract. Duplicating ~30 lines of line parsing
    is cheaper than reworking it.
    """
    import httpx

    headers = dict(_headers())
    headers["Accept"] = _NDJSON_CONTENT_TYPE
    parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            async with client.stream(
                "POST", _endpoint(session_id, stream=True), content=payload, headers=headers
            ) as resp:
                resp.raise_for_status()
                if _NDJSON_CONTENT_TYPE not in (resp.headers.get("content-type") or ""):
                    # The turn WAS admitted (2xx) and is running; re-POSTing it
                    # would double-execute (the #1137 duplicate-write class), and
                    # there is no wait=result to fall back to on 2.x anyway.
                    hint = _wire1_envelope_hint(await resp.aread())
                    logger.error(
                        "flue wire-2 turn: sidecar answered %r, not %s%s "
                        "(turn admitted; NOT re-POSTing)",
                        resp.headers.get("content-type"), _NDJSON_CONTENT_TYPE,
                        f" — {hint}" if hint else "",
                    )
                    yield _FALLBACK_TEXT
                    return
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        logger.warning("flue wire-2 stream: undecodable line %r", line[:120])
                        continue
                    if isinstance(chunk, str):
                        # Activity sentinels are not reply text — dropped here
                        # exactly as the wire-1 whole-result path never sees them.
                        if chunk.startswith((_TOOL_SENTINEL_PREFIX, _THINKING_SENTINEL_PREFIX)):
                            continue
                        parts.append(chunk)
                        continue
                    if isinstance(chunk, dict):
                        if chunk.get("done"):
                            break
                        if "error" in chunk:
                            logger.warning(
                                "flue wire-2 turn reported error: %s", str(chunk["error"])[:200]
                            )
                            break
    except Exception as exc:  # noqa: BLE001 - a brain hiccup must never crash a turn
        # No re-POST on either branch: a 2.x turn is fire-and-forget, so the
        # sidecar may already be executing it.
        logger.warning("flue wire-2 turn failed: %s", exc)
        if not parts:
            yield _FALLBACK_TEXT
            return

    text = "".join(parts)
    if text:
        yield text
        return
    logger.warning("flue wire-2 turn produced no text; treating as a failed turn")
    yield _FALLBACK_TEXT


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
    # Offer nudge on ANY turn — skipped when the recall packet already carries
    # the offer directive (the fold tags them "[pending-contact]"), so a
    # recall-shaped turn never asks twice.
    offer_block = ""
    if "[pending-contact]" not in recall_block:
        offer_block = await _pending_offer_block(uid)
    _blocks = "\n".join(b for b in (recall_block, offer_block) if b)
    brain_message = f"{_blocks}\n{message}" if _blocks else message
    outbound_message = _wrap_message_with_identity(brain_message, uid)
    payload = _request_payload(outbound_message)

    if _wire_version() >= _WIRE_2 and not _stream_enabled():
        # Wire 2 has no whole-result call: the non-streaming turn is a stream
        # read collapsed to a single delta. See _run_turn_aggregated_wire2.
        async for delta in _run_turn_aggregated_wire2(session_id, payload):
            yield delta
        return

    if _stream_enabled():
        # Seam-A NDJSON stream (src/streaming.ts): each line is a JSON string
        # (one text delta or __TOOL__/__THINKING__ sentinel chunk), terminated
        # by {"done": true} or {"error": ...}. Yield deltas as they arrive so
        # sentence-TTS starts DURING generation. If the stream dies after text
        # was yielded, just end the turn — the sidecar already executed it
        # (writes included), so falling back to ?wait=result would RE-RUN the
        # turn (the #1137 duplicate-write class).
        yielded_any = False
        admitted = False  # a 2xx means the sidecar is EXECUTING the turn
        try:
            import httpx

            headers = dict(_headers())
            headers["Accept"] = "application/x-ndjson"
            async with httpx.AsyncClient(timeout=_timeout_s()) as client:
                async with client.stream(
                    "POST", _endpoint(session_id, stream=True), content=payload, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    admitted = True
                    if "application/x-ndjson" in (resp.headers.get("content-type") or ""):
                        finished = False
                        async for line in resp.aiter_lines():
                            line = (line or "").strip()
                            if not line:
                                continue
                            try:
                                chunk = json.loads(line)
                            except ValueError:
                                logger.warning("flue stream: undecodable line %r", line[:120])
                                continue
                            if isinstance(chunk, str):
                                if chunk:
                                    yielded_any = True
                                    yield chunk
                                continue
                            if isinstance(chunk, dict):
                                if chunk.get("done"):
                                    finished = True
                                    break
                                if "error" in chunk:
                                    logger.warning("flue stream reported error: %s", str(chunk["error"])[:200])
                                    finished = True  # sidecar owned + reported the failure
                                    if not yielded_any:
                                        yield _FALLBACK_TEXT
                                    break
                        if finished or yielded_any:
                            return
                        logger.warning("flue stream ended without a terminal line and no text")
                        yield _FALLBACK_TEXT
                        return
                    # Sidecar ignored the Accept header (older build / stream
                    # kill-switched via ZOE_BRAIN_STREAM=0): the plain POST was
                    # a 202 admission and the turn IS NOW RUNNING async — a
                    # wait=result re-POST would execute it a second time. This
                    # is an operator misconfig (client flag on, sidecar off):
                    # flip ZOE_FLUE_STREAM_ENABLED off or ZOE_BRAIN_STREAM on.
                    # A wire-1 whole-result envelope means the flag points at a
                    # 1.x sidecar — name that too, same as the wire-2 turn path.
                    hint = _wire1_envelope_hint(await resp.aread())
                    logger.error(
                        "flue stream misconfig: client streaming ON but sidecar replied %r "
                        "(turn admitted async; reply unavailable — NOT re-POSTing)%s",
                        resp.headers.get("content-type"),
                        f" — {hint}" if hint else "",
                    )
                    yield _FALLBACK_TEXT
                    return
        except Exception as exc:  # noqa: BLE001 - a brain hiccup must never crash a turn
            if yielded_any:
                # Mid-stream failure after real text: the turn executed; ending
                # here loses the tail but never re-runs it.
                logger.warning("flue stream died mid-turn (after text): %s", exc)
                return
            if admitted:
                # 2xx received ⇒ the sidecar is already running this turn
                # (writes included). Re-POSTing via wait=result would execute
                # it a second time — the #1137 duplicate-write class. Eat the
                # reply rather than double-run the action.
                logger.warning("flue stream died after admission, before text (%s) — NOT re-POSTing", exc)
                yield _FALLBACK_TEXT
                return
            if _wire_version() >= _WIRE_2:
                # No wait=result on 2.x to fall back TO — the block below would
                # send a `?wait=result` the runtime answers with a 400. Nothing
                # was admitted, so the turn simply did not happen.
                logger.warning("flue wire-2 stream failed pre-admission (%s) — no wait=result fallback exists", exc)
                yield _FALLBACK_TEXT
                return
            logger.warning("flue stream request failed pre-admission (%s) — falling back to wait=result", exc)

    # WIRE-1 ONLY BELOW. Both wire-2 routes into this block return above; this
    # guard makes that structural fact checkable rather than merely argued, so a
    # later edit cannot quietly send a `?wait=result` to a 2.x runtime.
    if _wire_version() >= _WIRE_2:  # pragma: no cover - unreachable by construction
        logger.error("flue wire-2 reached the wait=result path — refusing to send it")
        yield _FALLBACK_TEXT
        return

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            resp = await client.post(_endpoint(session_id), content=payload, headers=_headers())
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
    """Non-streaming brain turn — collects the Flue stream into one string.

    __TOOL__/__THINKING__ are activity sentinels for streaming UI consumers, not
    reply text. The streaming path strips them before display/TTS; a
    non-streaming caller must too, or the returned string is raw sentinel JSON
    prepended to the actual answer (confirmed live: /api/chat?stream=false
    returned `__TOOL__:{…recall_memory…}…Your locker code is beef42.`). Mirrors
    the same skip in zoe_core_client.run_zoe_core.
    """
    chunks: list[str] = []
    async for delta in run_flue_brain_streaming(message, session_id, user_id, **kwargs):
        if delta.startswith("__TOOL__:") or delta.startswith("__THINKING__:"):
            continue
        chunks.append(delta)
    return "".join(chunks).strip()
