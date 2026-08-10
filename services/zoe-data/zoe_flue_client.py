"""Flue brain client — the cutover seam to the Flue Zoe-brain sidecar.

This is the OPT-IN alternative to ``zoe_core_client`` (the Pi-CLI brain). It is
selected ONLY when ``ZOE_BRAIN_BACKEND == 'flue'`` (see ``brain_dispatch`` /
``routers.chat``); with the env unset or ``'core'`` this module is never reached
and the live brain path is byte-identical to today.

Wire 1 — this client's in-code default, formerly served by the retired 1.x
sidecar (``labs/flue-zoe-brain``) — is::

    POST {base}/agents/zoe/<session>?wait=result
    body: {"message": "..."}
    -> {"result": {"text": "..."}}

Its route fails closed unless ``ZOE_BRAIN_OPEN=1`` or a matching
``Authorization: Bearer <ZOE_BRAIN_TOKEN>`` is presented, so this client sends
the bearer token from ``ZOE_BRAIN_TOKEN`` when set.

Wire versions — ``ZOE_FLUE_WIRE`` (default ``1``)
-------------------------------------------------
The block above is the **Flue 1.x (beta.6)** wire, which the retired sidecar on
:3578 spoke and which this client still sends by default (a rollback leftover).
``ZOE_FLUE_WIRE=2`` switches to the **Flue 2.x** wire served by the LIVE sidecar
in ``labs/flue-zoe-brain-2x`` on :3579 (PR #1616) — the wire the box runs. Three
things change, and only these three::

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
raised — a brain backend hiccup must never crash a turn. The ONE opt-in
exception is ``raise_transport_errors=True`` (used only by
``brain_dispatch``'s failover wrapper): a pre-admission transport failure then
raises ``FlueTransportError`` so the turn can be re-dispatched on the core lane
instead of being answered with the canned sentinel. Everything else — HTTP
status errors, read timeouts, decode errors, an empty 200, and an HTTP 400 from
a wire-mismatched sidecar (a REFUSAL, not an unreachable host) — still renders
``_FALLBACK_TEXT``, because those mean the sidecar answered or RAN the turn.
That contract holds identically on wire 1 and wire 2: every wire-2 route has its
own pre-admission check, so ``ZOE_FLUE_WIRE=2`` does not silently disable
failover.

A second opt-in, ``outcome_sink``, lets the caller learn WHICH of those a turn
was (``ok`` / ``fallback`` / ``error``) for its operator log. It is labels only
and never changes what is yielded or retried.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import re
from typing import Any, AsyncIterator
from urllib.parse import quote

logger = logging.getLogger(__name__)


class FlueTransportError(RuntimeError):
    """The flue sidecar was never REACHED for this turn.

    Raised ONLY when the caller opts in with ``raise_transport_errors=True``
    (``brain_dispatch``'s failover wrapper) AND the failure is transport-class
    AND the turn produced no text and was never admitted. It therefore proves
    the strong property a re-dispatch needs: **the sidecar did not execute this
    turn**, so retrying it on another lane cannot double-run a tool/write and
    cannot make the panel speak twice.

    Default (kwarg absent/False) is unchanged: transport errors are swallowed
    and rendered as ``_FALLBACK_TEXT``.
    """


def _is_transport_failure(exc: BaseException) -> bool:
    """True only for the fast-fail 'never reached the sidecar' class.

    IN: connection refused, connect timeout, connect-time reset/unreachable —
    the ~100ms class where nothing was accepted by the sidecar.

    OUT, deliberately: an HTTP status error (the sidecar answered, so it is UP
    and it RAN the turn), read/write/pool timeouts (a slow generation — the
    turn is executing; a retry would double-run it), decode errors, and an
    empty 200. Those are model/server-level failures, not transport ones, and
    they keep today's canned-sentinel behaviour.
    """
    try:
        import httpx
    except Exception:  # pragma: no cover - httpx is a hard dep of this module
        httpx = None  # type: ignore[assignment]

    if httpx is not None:
        # Order matters: HTTPStatusError/ReadTimeout are checked first because a
        # widening of httpx's class tree must never silently make them retryable.
        if isinstance(exc, httpx.HTTPStatusError):
            return False
        if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
            return False
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return True
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError)):
        return True
    if isinstance(exc, OSError) and exc.errno in {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    }:
        return True
    return False

# Read lazily (NOT at import) so a .env value bootstrapped after import is honored
# — bootstrap_runtime_env() populates os.environ in lifespan startup, which runs
# after this module is imported.
_DEFAULT_BASE_URL = "http://127.0.0.1:3578"
_DEFAULT_TIMEOUT_S = 180.0

# Graceful, user-facing fallback emitted whenever a flue turn cannot produce a
# usable reply — transport/parse error OR an HTTP 200 with empty text. Shared so
# both failure surfaces render identically instead of one blanking the turn.
_FALLBACK_TEXT = "Sorry, I had trouble reaching my brain just now. Could you try again?"


# ── Turn-outcome reporting (opt-in, labels only) ─────────────────────────────
#
# ``brain_dispatch``'s failover wrapper emits ONE greppable ``BRAIN_LANE`` line
# per turn. Without this channel it could only observe "the generator finished",
# so every failure this module RENDERS AS ``_FALLBACK_TEXT`` — an HTTP status
# error, a read timeout, an empty 200, a post-admission stream death — was
# logged ``outcome=ok``: success reported for exactly the failed brain turns an
# operator greps that line to diagnose.
#
# The channel is an OPTIONAL mutable dict rather than a return value, an
# exception, or a sentinel-string comparison, because:
#   * the yielded stream shape is the pinned prod contract and must not change;
#   * a label must NEVER influence dispatch — the retry/replay invariants are
#     decided by ``FlueTransportError`` alone, and nothing here is read before a
#     dispatch decision;
#   * matching the reply against ``_FALLBACK_TEXT`` would be a guess (it cannot
#     tell a fallback from a brain that happened to say the same sentence, and
#     it cannot see a truncation that served real text at all).
#
# Absent (the default, and every flag-off call) nothing is recorded and the
# module behaves byte-identically.
FLUE_OUTCOME_OK = "ok"              # the sidecar answered, terminated cleanly
FLUE_OUTCOME_FALLBACK = "fallback"  # _FALLBACK_TEXT served; the brain did not answer
FLUE_OUTCOME_ERROR = "error"        # real text served, but the turn failed/truncated


def _record_outcome(
    sink: dict[str, str] | None, outcome: str, reason: str = ""
) -> None:
    """Record this turn's outcome for the caller, if one asked for it.

    Each exit path of a turn calls this exactly once, at the point the outcome
    is finally decided, so the sink holds the terminal verdict rather than an
    intermediate guess. Never raises: an observability label must not be able to
    fail a turn.
    """
    if sink is None:
        return
    try:
        sink["outcome"] = outcome
        sink["reason"] = reason[:160]
    except Exception:  # noqa: BLE001 - a label must never break a turn
        logger.debug("flue outcome sink rejected a write; continuing")


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
# (labs/flue-zoe-brain-2x src/request-identity.ts IDENTITY_ENVELOPE_PREFIX / _RE):
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


# Machine-readable REPLAY-ISOLATION envelope. MUST match the sidecar's parser
# (labs/flue-zoe-brain-2x src/replay-mode.ts REPLAY_ENVELOPE_PREFIX / _RE).
#
# WHY: the replay gate replays Jason's corpus through the LIVE pipeline. The
# harness's allow_writes=False governs only fast_tiers; on brain fall-through the
# sidecar's tools run with ZOE_BRAIN_ALLOW_WRITES=true and execute REAL writes —
# reminders, notes, journal, people, MemPalace memories, Home Assistant device
# state, Music Assistant playback. The probe's cleanup swept only events and
# list_items, so everything else leaked into live data silently, and every new
# mutating tool leaked by default. This marker tells the sidecar to report those
# writes as done without committing them.
#
# WIRE ORDER: applied OUTSIDE the identity wrap, so the replay line is FIRST:
#   " zoe-replay:1\n zoe-uid:<id>\n<blocks>\n<user message>"
# Both sidecar parsers are ^-anchored; it strips the replay line, then the
# identity line. Absent marker = byte-identical to today's outbound message.
_REPLAY_ENVELOPE_PREFIX = " zoe-replay:"

# A user-typed leading " zoe-replay:…" line must never be mistaken for the
# trusted marker. Anchored + multiline-free, matching the sidecar regex exactly.
_REPLAY_ENVELOPE_RE = re.compile(r"^ zoe-replay:[^\n]*\n")


def _strip_replay_envelope(message: str) -> str:
    """Remove any leading replay-envelope line(s) from UNTRUSTED message text.

    The marker is a trusted server-side signal, so the seam must be the only thing
    that can set it. Without this, a user whose turn carries no identity envelope
    (``user_id`` blank/guest → ``_wrap_message_with_identity`` returns the message
    untouched) could type " zoe-replay:1" as their first line and land it at
    position 0, where the sidecar's ^-anchored parser would honour it and silently
    void their own writes. Loops so a stack of forged lines can't shield one.

    Not a security boundary against a compromised seam — it closes the one path
    where user-authored text reaches the start of the outbound message.
    """
    prev = None
    while prev != message:
        prev = message
        message = _REPLAY_ENVELOPE_RE.sub("", message)
    return message


def _wrap_message_with_replay(message: str, replay: bool) -> str:
    """Prefix ``message`` with the replay-isolation envelope when ``replay`` is set.

    ``replay`` false → the message is returned unchanged, so the live lane's wire
    bytes are exactly what they are today.
    """
    if not replay:
        return message
    return f"{_REPLAY_ENVELOPE_PREFIX}1\n{message}"


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
# stripIdentityEnvelope (labs/flue-zoe-brain-2x/src/request-identity.ts) matches
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


async def _run_turn_aggregated_wire2(
    session_id: str,
    payload: bytes,
    *,
    raise_transport_errors: bool = False,
    outcome_sink: dict[str, str] | None = None,
) -> AsyncIterator[str]:
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

    ``raise_transport_errors`` carries the SAME contract here as on the streaming
    path: a pre-admission transport failure (no 2xx, no text) raises
    ``FlueTransportError`` so the caller may re-dispatch. An HTTP 400 does NOT —
    the sidecar answered, so the turn was REFUSED, not unreachable.
    """
    import httpx

    headers = dict(_headers())
    headers["Accept"] = _NDJSON_CONTENT_TYPE
    parts: list[str] = []
    done_seen = False
    error_terminal = ""
    # A 2xx means the sidecar is EXECUTING the turn — the same admission gate the
    # streaming path uses, tracked here so a transport raise can never fire once
    # the sidecar has taken ownership of the turn.
    admitted = False
    stream_died = False
    try:
        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            async with client.stream(
                "POST", _endpoint(session_id, stream=True), content=payload, headers=headers
            ) as resp:
                if resp.status_code == 400:
                    # The MIRROR misconfig of the wire-1-reply case below: a 1.x
                    # sidecar rejects the wire-2 {kind, body} shape with 400
                    # ("message" required), and raise_for_status() would bury
                    # the diagnosis in a bare HTTPStatusError. 4xx = the turn
                    # was NEVER admitted, so naming the wire flag is safe here.
                    logger.error(
                        "flue wire-2 turn: sidecar rejected the wire-2 body with "
                        "HTTP 400 — if ZOE_FLUE_BRAIN_URL points at a Flue 1.x "
                        "sidecar, set ZOE_FLUE_WIRE=1 or repoint at the 2.x one "
                        "(body: %r)", (await resp.aread())[:200],
                    )
                    # NOT a transport failure: the sidecar answered. The turn was
                    # refused, not unreachable, so a lane failover would be
                    # re-dispatching on a guess rather than on proof.
                    _record_outcome(
                        outcome_sink, FLUE_OUTCOME_FALLBACK, "wire2_body_rejected_400"
                    )
                    yield _FALLBACK_TEXT
                    return
                resp.raise_for_status()
                admitted = True
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
                    _record_outcome(
                        outcome_sink, FLUE_OUTCOME_FALLBACK, "wire2_not_ndjson"
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
                            done_seen = True
                            break
                        if "error" in chunk:
                            error_terminal = str(chunk["error"])[:200]
                            break
    except Exception as exc:  # noqa: BLE001 - a brain hiccup must never crash a turn
        # No re-POST on either branch: a 2.x turn is fire-and-forget, so the
        # sidecar may already be executing it.
        logger.warning("flue wire-2 turn failed: %s", exc)
        if not parts:
            if not admitted and raise_transport_errors and _is_transport_failure(exc):
                # Never admitted, nothing accumulated: the sidecar did not
                # execute this turn, so the caller may safely re-dispatch it.
                # Guarded on ``admitted`` as well as the exception class, so a
                # connect-shaped error surfacing mid-stream can never be
                # mistaken for "the turn never happened".
                raise FlueTransportError(str(exc)) from exc
            _record_outcome(outcome_sink, FLUE_OUTCOME_FALLBACK, "wire2_turn_failed")
            yield _FALLBACK_TEXT
            return
        # Partial text survives — the turn ran and is reported below as a failed
        # (truncated) turn rather than a clean success.
        stream_died = True

    text = "".join(parts)
    # A success is ONLY a {"done": true} terminal. An {"error": ...} terminal or
    # a truncated EOF still returns whatever text arrived — the turn already
    # executed server-side (writes included) and nothing was spoken yet, so the
    # partial reply is strictly better than a fallback that pretends the brain
    # was unreachable — but it must be LOUD, never a silent success.
    if error_terminal:
        logger.error(
            "flue wire-2 turn ended with an error terminal: %s%s",
            error_terminal,
            " (partial reply text returned)" if text else "",
        )
    elif not done_seen and text:
        logger.error(
            "flue wire-2 stream ended without {'done': true} — reply may be "
            "TRUNCATED (%d chars returned)", len(text),
        )
    if text:
        # A clean success is a {"done": true} terminal and nothing else. An error
        # terminal, a mid-stream death, or a truncated EOF all still SERVE the
        # partial text (the turn executed server-side) — but they are failures,
        # and reporting them as ok is what hid them from the operator.
        if error_terminal:
            _record_outcome(
                outcome_sink, FLUE_OUTCOME_ERROR, f"wire2_error_terminal:{error_terminal}"
            )
        elif stream_died:
            _record_outcome(outcome_sink, FLUE_OUTCOME_ERROR, "wire2_stream_died_after_text")
        elif not done_seen:
            _record_outcome(outcome_sink, FLUE_OUTCOME_ERROR, "wire2_truncated_no_done")
        else:
            _record_outcome(outcome_sink, FLUE_OUTCOME_OK)
        yield text
        return
    logger.warning("flue wire-2 turn produced no text; treating as a failed turn")
    _record_outcome(
        outcome_sink,
        FLUE_OUTCOME_FALLBACK,
        f"wire2_error_terminal:{error_terminal}" if error_terminal else "wire2_no_text",
    )
    yield _FALLBACK_TEXT


async def run_flue_brain_streaming(
    message: str,
    session_id: str,
    user_id: str = "",
    *,
    raise_transport_errors: bool = False,
    outcome_sink: dict[str, str] | None = None,
    **kwargs: Any,
) -> AsyncIterator[str]:
    """Streaming brain turn through the Flue sidecar.

    ``raise_transport_errors`` (opt-in, default False) makes a pre-admission
    transport failure raise ``FlueTransportError`` instead of yielding
    ``_FALLBACK_TEXT``, so ``brain_dispatch`` can re-dispatch the turn on the
    core lane. It NEVER fires once text has been yielded or the turn was
    admitted (2xx) — see the mid-stream comments below. It holds identically on
    both wires: every wire-2 route reaches its own pre-admission check.

    ``outcome_sink`` (opt-in, default None) is a dict this turn writes its
    terminal verdict into (``outcome`` / ``reason``) for the caller's operator
    log. Labels only — nothing here changes what is yielded or retried.

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
    # strips it before the model sees the text (see labs/flue-zoe-brain-2x
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
    # Sanitise BEFORE assembling: a user-typed " zoe-replay:" line must never reach
    # the start of the outbound message and forge the trusted marker. Only reachable
    # when there is no identity line ahead of it — both blocks return "" for a blank
    # uid — but strip unconditionally rather than depend on that coupling.
    safe_message = _strip_replay_envelope(message)
    brain_message = f"{_blocks}\n{safe_message}" if _blocks else safe_message
    outbound_message = _wrap_message_with_identity(brain_message, uid)
    # Replay isolation rides OUTSIDE the identity wrap so its line is first on the
    # wire. Only the replay harness ever passes this; absent → unchanged bytes.
    outbound_message = _wrap_message_with_replay(
        outbound_message, bool(kwargs.get("replay_isolation"))
    )
    payload = _request_payload(outbound_message)

    if _wire_version() >= _WIRE_2 and not _stream_enabled():
        # Wire 2 has no whole-result call: the non-streaming turn is a stream
        # read collapsed to a single delta. See _run_turn_aggregated_wire2.
        # Both opt-ins are forwarded: a wire-2 non-streaming turn must be able to
        # fail over on a dead sidecar exactly like the wire-1 wait=result turn
        # it replaces, and must report the same truthful outcome.
        async for delta in _run_turn_aggregated_wire2(
            session_id,
            payload,
            raise_transport_errors=raise_transport_errors,
            outcome_sink=outcome_sink,
        ):
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
                    if resp.status_code == 400 and _wire_version() >= _WIRE_2:
                        # Same mirror-misconfig diagnosis as the aggregated
                        # wire-2 path: a 1.x sidecar 400s the {kind, body}
                        # shape, and raise_for_status() would bury the wire
                        # diagnosis. 4xx = never admitted, safe to name it.
                        logger.error(
                            "flue wire-2 stream: sidecar rejected the wire-2 "
                            "body with HTTP 400 — if ZOE_FLUE_BRAIN_URL points "
                            "at a Flue 1.x sidecar, set ZOE_FLUE_WIRE=1 or "
                            "repoint at the 2.x one (body: %r)",
                            (await resp.aread())[:200],
                        )
                        # Refused, not unreachable — never a transport failure.
                        _record_outcome(
                            outcome_sink, FLUE_OUTCOME_FALLBACK, "wire2_body_rejected_400"
                        )
                        yield _FALLBACK_TEXT
                        return
                    resp.raise_for_status()
                    admitted = True
                    if "application/x-ndjson" in (resp.headers.get("content-type") or ""):
                        finished = False
                        done_ok = False
                        stream_error = ""
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
                                    done_ok = True
                                    break
                                if "error" in chunk:
                                    logger.warning("flue stream reported error: %s", str(chunk["error"])[:200])
                                    finished = True  # sidecar owned + reported the failure
                                    stream_error = str(chunk["error"])[:160]
                                    if not yielded_any:
                                        yield _FALLBACK_TEXT
                                    break
                        if finished or yielded_any:
                            # Truthful terminal label. Only a {"done": true} that
                            # actually carried text is a success: an error
                            # terminal is a failed brain turn, and running out of
                            # lines without a terminal is a truncated one. Both
                            # used to reach the caller indistinguishable from ok.
                            if stream_error:
                                _record_outcome(
                                    outcome_sink,
                                    FLUE_OUTCOME_ERROR if yielded_any else FLUE_OUTCOME_FALLBACK,
                                    f"stream_error_terminal:{stream_error}",
                                )
                            elif not done_ok:
                                _record_outcome(
                                    outcome_sink, FLUE_OUTCOME_ERROR, "stream_truncated_no_terminal"
                                )
                            elif yielded_any:
                                _record_outcome(outcome_sink, FLUE_OUTCOME_OK)
                            else:
                                _record_outcome(
                                    outcome_sink, FLUE_OUTCOME_ERROR, "stream_done_without_text"
                                )
                            return
                        logger.warning("flue stream ended without a terminal line and no text")
                        _record_outcome(
                            outcome_sink, FLUE_OUTCOME_FALLBACK, "stream_no_terminal_no_text"
                        )
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
                    _record_outcome(
                        outcome_sink, FLUE_OUTCOME_FALLBACK, "stream_misconfig_not_ndjson"
                    )
                    yield _FALLBACK_TEXT
                    return
        except Exception as exc:  # noqa: BLE001 - a brain hiccup must never crash a turn
            if yielded_any:
                # Mid-stream failure after real text: the turn executed; ending
                # here loses the tail but never re-runs it.
                logger.warning("flue stream died mid-turn (after text): %s", exc)
                _record_outcome(
                    outcome_sink, FLUE_OUTCOME_ERROR, f"stream_died_after_text:{exc}"
                )
                return
            if admitted:
                # 2xx received ⇒ the sidecar is already running this turn
                # (writes included). Re-POSTing via wait=result would execute
                # it a second time — the #1137 duplicate-write class. Eat the
                # reply rather than double-run the action.
                logger.warning("flue stream died after admission, before text (%s) — NOT re-POSTing", exc)
                _record_outcome(
                    outcome_sink, FLUE_OUTCOME_FALLBACK, "stream_died_after_admission"
                )
                yield _FALLBACK_TEXT
                return
            # PRE-ADMISSION, NO TEXT — and the transport check is ordered BEFORE
            # the wire-2 branch on purpose. Both wires arrive here having
            # admitted nothing and yielded nothing, which is exactly the proof a
            # re-dispatch needs, so the opt-in raise is correct on wire 2 too.
            # Ordering it after the wire-2 return would silently disable failover
            # for every turn with ZOE_FLUE_WIRE=2 — the wire the 2.x sidecar
            # speaks, i.e. precisely the deployment this failover exists to cover.
            if raise_transport_errors and _is_transport_failure(exc):
                # The sidecar did not run this turn, so the caller may safely
                # re-dispatch it. Raise HERE rather than falling through to
                # wait=result: that re-POST would pay a second failed connect
                # against the same dead socket, and on wire 2 there is no
                # wait=result to fall through TO at all.
                raise FlueTransportError(str(exc)) from exc
            if _wire_version() >= _WIRE_2:
                # No wait=result on 2.x to fall back TO — the block below would
                # send a `?wait=result` the runtime answers with a 400. Nothing
                # was admitted, so the turn simply did not happen.
                logger.warning("flue wire-2 stream failed pre-admission (%s) — no wait=result fallback exists", exc)
                _record_outcome(outcome_sink, FLUE_OUTCOME_FALLBACK, "wire2_pre_admission_failure")
                yield _FALLBACK_TEXT
                return
            logger.warning("flue stream request failed pre-admission (%s) — falling back to wait=result", exc)

    # WIRE-1 ONLY BELOW. Both wire-2 routes into this block return above; this
    # guard makes that structural fact checkable rather than merely argued, so a
    # later edit cannot quietly send a `?wait=result` to a 2.x runtime.
    if _wire_version() >= _WIRE_2:  # pragma: no cover - unreachable by construction
        logger.error("flue wire-2 reached the wait=result path — refusing to send it")
        _record_outcome(outcome_sink, FLUE_OUTCOME_FALLBACK, "wire2_reached_wait_result")
        yield _FALLBACK_TEXT
        return

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            resp = await client.post(_endpoint(session_id), content=payload, headers=_headers())
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:  # noqa: BLE001 - a brain hiccup must never crash a turn
        if raise_transport_errors and _is_transport_failure(exc):
            # Connect refused/timed out: the request never reached the sidecar,
            # so nothing executed and the caller may re-dispatch this turn.
            raise FlueTransportError(str(exc)) from exc
        # Reached only for the NON-transport classes (HTTP status error, read
        # timeout, decode error) — the sidecar answered or is still running the
        # turn, so this is a failed brain turn, not an unreachable brain.
        logger.warning("flue brain turn failed: %s", exc)
        _record_outcome(outcome_sink, FLUE_OUTCOME_FALLBACK, f"turn_failed:{exc}")
        yield _FALLBACK_TEXT
        return

    text = _text_from_body(body)
    if text:
        _record_outcome(outcome_sink, FLUE_OUTCOME_OK)
        yield text
        return

    # HTTP 200 but no usable text (e.g. {"result": {}} or {"result": {"text": ""}}).
    # The streaming chat path has already opened a text message; ending it with
    # zero chunks would render a blank assistant turn. Treat an empty successful
    # result as a failed brain turn and emit the same graceful fallback we use for
    # transport/parse errors, so the user always gets a coherent reply.
    logger.warning("flue brain returned an empty result; treating as a failed turn")
    _record_outcome(outcome_sink, FLUE_OUTCOME_FALLBACK, "empty_200")
    yield _FALLBACK_TEXT


async def run_flue_brain(
    message: str,
    session_id: str,
    user_id: str = "",
    *,
    raise_transport_errors: bool = False,
    outcome_sink: dict[str, str] | None = None,
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
    async for delta in run_flue_brain_streaming(
        message,
        session_id,
        user_id,
        raise_transport_errors=raise_transport_errors,
        outcome_sink=outcome_sink,
        **kwargs,
    ):
        if delta.startswith("__TOOL__:") or delta.startswith("__THINKING__:"):
            continue
        chunks.append(delta)
    return "".join(chunks).strip()
