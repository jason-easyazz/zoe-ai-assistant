"""Flue brain client — the cutover seam to the Flue Zoe-brain sidecar.

This is the OPT-IN alternative to ``zoe_core_client`` (the Pi-CLI brain). It is
selected ONLY when ``ZOE_BRAIN_BACKEND == 'flue'`` (see ``brain_dispatch`` /
``routers.chat``); with the env unset or ``'core'`` this module is never reached
and the live brain path is byte-identical to today.

Wire 1 — the retired Flue 1.x beta wire (``labs/flue-zoe-brain`` on :3578),
still selectable with ``ZOE_FLUE_WIRE=1`` — is::

    POST {base}/agents/zoe/<session>?wait=result
    body: {"message": "..."}
    -> {"result": {"text": "..."}}

Its route fails closed unless ``ZOE_BRAIN_OPEN=1`` or a matching
``Authorization: Bearer <ZOE_BRAIN_TOKEN>`` is presented, so this client sends
the bearer token from ``ZOE_BRAIN_TOKEN`` when set.

Wire versions — ``ZOE_FLUE_WIRE`` (default ``2``)
-------------------------------------------------
The block above is the **Flue 1.x (beta.6)** wire, which the retired sidecar on
:3578 spoke; it is opt-in (``ZOE_FLUE_WIRE=1``) and kept byte-identical for
parity. The DEFAULT — unset, empty, or anything unrecognised — is the **Flue
2.x** wire served by the LIVE sidecar in ``labs/flue-zoe-brain-2x`` on :3579
(PR #1616), which is also ``ZOE_FLUE_BRAIN_URL``'s default (B6.5: a missing env
var lands on the live sidecar, never on the retired port). Three things change
between the wires, and only these three::

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

import asyncio
import errno
import json
import logging
import os
import re
from typing import Any, AsyncIterator, Optional
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
# The LIVE Flue 2.x sidecar (`flue-zoe-brain-2x.service`). The 1.x lane on :3578
# was stopped and source-removed (#1678), so a fresh deploy that forgets the env
# var must land HERE — a default of :3578 would fail every brain turn (B6.5).
_DEFAULT_BASE_URL = "http://127.0.0.1:3579"
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


# ── Wire version (ZOE_FLUE_WIRE, default 2) ──────────────────────────────────
#
# 1 = the retired Flue 1.x beta wire (?wait=result + {"message": …}) — opt-in.
# 2 = the Flue 2.x wire served by labs/flue-zoe-brain-2x (no wait param,
#     top-level DeliveredMessage body, stream-read for the non-streaming turn).
#
# DEFAULT 2 IS LOAD-BEARING: this module is on the live voice path and the box
# runs the 2.x sidecar, so an unset/empty/garbage flag must land on the wire the
# live sidecar speaks — the retired wire 400s every turn. Wire 1 stays
# byte-identical WHEN SELECTED (tests/test_flue_client_wire.py::test_wire1_*
# replay the golden request fixtures under ZOE_FLUE_WIRE=1); the default itself
# is pinned by test_wire_flag_unset_is_provably_wire_two — do not "simplify"
# either away.
_WIRE_ENV = "ZOE_FLUE_WIRE"
_WIRE_1 = 1
_WIRE_2 = 2
_NDJSON_CONTENT_TYPE = "application/x-ndjson"
_TOOL_SENTINEL_PREFIX = "__TOOL__:"
_THINKING_SENTINEL_PREFIX = "__THINKING__:"


def _wire_version() -> int:
    """The Flue wire this client speaks. Per-call env read; 2 unless '1'.

    Anything other than '1'/'2' (including a typo like 'v2') logs loudly and
    falls back to 2 — a mis-set flag must degrade to the deployed wire, never to
    an undefined one, and must never do so silently.
    """
    # The flag name is spelled as a LITERAL here on purpose: tools/audit/
    # flag_inventory.py extracts names from the call site, so reading it via the
    # _WIRE_ENV constant would leave ZOE_FLUE_WIRE out of the generated
    # inventory — registered nowhere, invisible to the CI pin.
    raw = (os.environ.get("ZOE_FLUE_WIRE") or "").strip()
    if not raw or raw == "2":
        return _WIRE_2
    if raw == "1":
        return _WIRE_1
    logger.error(
        "%s=%r is not a known Flue wire version (expected '1' or '2'); using wire 2",
        _WIRE_ENV, raw,
    )
    return _WIRE_2


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


# Machine-readable SPECULATIVE-TURN envelope (B1.1). MUST match the sidecar's
# parser (labs/flue-zoe-brain-2x src/speculative-turn.ts SPECULATIVE_ENVELOPE_PREFIX
# / _RE). A speculative voice turn's brain runs before the daemon's verdict; its
# write tools reach zoe-data as a SEPARATE request (intent-dispatch) that the
# turn's ContextVar cannot follow. The sidecar binds this id to the turn and echoes
# it as ``speculative_turn_id`` on every dispatch, so zoe-data holds exactly that
# turn's writes for its verdict — and nobody else's.
#
# WIRE ORDER: outermost, ahead of the replay and identity lines:
#   " zoe-spec:<turn_id>\n zoe-replay:1\n zoe-uid:<id>\n<blocks>\n<user message>"
# Only a speculative voice turn (flag on) carries it; absent = byte-identical.
_SPECULATIVE_ENVELOPE_PREFIX = " zoe-spec:"
_SPECULATIVE_ENVELOPE_RE = re.compile(r"^ zoe-spec:[^\n]*\n")


def _wrap_message_with_speculative_turn(message: str, turn_id: Optional[str]) -> str:
    """Prefix ``message`` with the speculative-turn envelope, or return it unchanged."""
    if not turn_id:
        return message
    return f"{_SPECULATIVE_ENVELOPE_PREFIX}{turn_id}\n{message}"


async def _speculative_turn_for_wire() -> Optional[str]:
    """The bound speculative turn id to forward, or None.

    Only the 2.x sidecar (wire 2) parses the envelope; on wire 1 the line would sit
    ahead of the ^-anchored identity parse and break it, so there the turn instead
    WAITS for its verdict before the brain starts (then nothing needs echoing —
    a committed turn's writes are not held). Nothing bound → None, unchanged wire.
    """
    try:
        import voice_speculation as _vs
    except Exception:  # pragma: no cover - module is in-tree
        return None
    if _vs.bound_gate() is None:
        return None
    turn_id = _vs.bound_turn_id()  # None when the request-supplied id is not wire-safe
    if _wire_version() < _WIRE_2 or turn_id is None:
        await _vs.await_commit("brain:no-turn-echo")
        return None
    return turn_id


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
        # The speculative-turn marker is trusted the same way: never user-forgeable.
        message = _SPECULATIVE_ENVELOPE_RE.sub("", message)
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


def _recall_question_shape(message: str) -> str:
    """Which recall-floor shape this message is: "personal" (a my/I question),
    "event" (an event-shaped question about the user's people/plans —
    ``memory_gate.is_event_question``: "Who is flying in on Thursday, and
    where from?", "where is she flying from" — Samantha bar S1 round 3), or ""
    (not a recall question), or "evidence" (``memory_gate.is_evidence_question``:
    "What exactly did I say about Marisol?", "are you sure?" — no my/I-question
    shape, yet only the packet's dated, quoted bullets can answer it; live miss
    2026-09-30). Pure. Ownership against continuity is decided by
    ``_recall_floor_shape`` — the ONE predicate the floor, the continuity
    exclusivity check and the offer ager share."""
    msg = message or ""
    if _PERSONAL_QUESTION_RE.search(msg):
        return "personal"
    from memory_gate import is_event_question, is_evidence_question  # stdlib-only

    if is_event_question(msg):
        return "event"
    return "evidence" if is_evidence_question(msg) else ""


def _recall_floor_shape(message: str) -> str:
    """The shape the recall floor CLAIMS this turn with, or "" — the ownership
    rule between the recall floor and continuity (pure, no I/O):

    * a personal my/I question is always recall ("do you remember what I
      said? I'm so anxious" keeps the recall block);
    * an event question EMBEDDED in a first-person feeling — the same sentence
      carries both ("I'm anxious about who is flying in on Thursday") — is NOT
      claimed while continuity is on: the user is sharing a feeling, not
      asking, so continuity owns the turn, and its packet still carries the
      event because continuity mode runs the same semantic search on the
      user's words (Greptile #1770);
    * a standalone event question stays recall, even beside a feeling in
      ANOTHER sentence ("I'm exhausted. Who is flying in on Thursday, and where
      from?") — the explicit question is answered from memory (Greptile #1771).
    """
    shape = _recall_question_shape(message)
    if shape == "event" and _continuity_inject_enabled():
        from memory_gate import event_sentences, is_event_question

        if any(_CONTINUITY_RE.search(s) and is_event_question(s)
               for s in event_sentences(message)):
            return ""
    return shape


_RECALL_BLOCK_OPEN = (
    "[MEMORY CONTEXT — Zoe's stored notes about this user; "
    "use them to answer; do not mention this block]"
)
_RECALL_BLOCK_CLOSE = "[END MEMORY CONTEXT]"
_OFFER_BLOCK_OPEN = "[PENDING CONTACT OFFER — do not mention this block]"
_OFFER_BLOCK_CLOSE = "[END PENDING CONTACT OFFER]"
# Every block this seam folds into the user message, as (open-line prefix, close
# line). The sidecar elides them from all but the newest user message under
# ZOE_BRAIN_ELIDE_STALE_BLOCKS; pinned equal to FLUE_CONTEXT_BLOCKS in
# labs/flue-zoe-brain-2x/src/context-blocks.ts. "[Today" is #1781's dated label.
_FLUE_CONTEXT_BLOCKS = (
    ("[MEMORY CONTEXT", "[END MEMORY CONTEXT]"),
    ("[PENDING CONTACT OFFER", "[END PENDING CONTACT OFFER]"),
    ("[Today", "[END Today]"),
    ("[RAISE", "[END RAISE]"),
)
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
    return f"{_OFFER_BLOCK_OPEN}\n" + "\n".join(lines) + f"\n{_OFFER_BLOCK_CLOSE}"


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


def _truncate_packet(packet: str, *, max_chars: int = _RECALL_MAX_CHARS) -> str:
    """Cap the packet at _RECALL_MAX_BULLETS bullet lines / ``max_chars``."""
    lines: list[str] = []
    bullets = 0
    total = 0
    for line in packet.splitlines():
        if line.lstrip().startswith(("-", "•", "*")):
            bullets += 1
            if bullets > _RECALL_MAX_BULLETS:
                break
        total += len(line) + 1
        if lines and total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines).strip()


async def _recall_context_block(message: str, user_id: str) -> str:
    """The delimited memory block for this turn, or '' — NEVER raises.

    '' unless the flag is ON, a real user id is present, and the message
    matches a conservative recall-question shape the floor claims
    (``_recall_floor_shape``: a personal my/I question, an evidence-shaped
    question, or an event-shaped question not embedded in a first-person
    feeling). A fetch failure logs
    and returns '' — the turn always proceeds, at worst without the floor.
    """
    if not _recall_inject_enabled():
        return ""
    if not (user_id or "").strip():
        return ""
    shape = _recall_floor_shape(message)
    if not shape:
        return ""
    try:
        packet = await _fetch_for_prompt_packet(user_id, message)
    except Exception as exc:  # noqa: BLE001 — the recall floor must never break a turn
        logger.warning(
            "seam recall inject: packet fetch failed, continuing without it: %s", exc
        )
        return ""
    packet = _truncate_packet((packet or "").strip())
    bullets = sum(1 for ln in packet.splitlines() if ln.lstrip().startswith(("-", "•", "*")))
    logger.info("SEAM_RECALL user=%s shape=%s bullets=%d chars=%d",
                user_id, shape, bullets, len(packet))
    if not packet:
        return ""
    return f"{_RECALL_BLOCK_OPEN}\n{packet}\n{_RECALL_BLOCK_CLOSE}"


# ── Continuity injection (ZOE_SEAM_CONTINUITY_INJECT, default ON) ───────────
#
# Samantha bar S4 (first baseline 2026-09-28, FAIL 3/3): day 1 "Honestly I'm
# pretty anxious about my job interview at the aquarium…", day 2 "Ugh, I've been
# feeling a bit on edge today." — and the reply never touched the interview. On
# the Flue lane the per-turn memory packet the legacy lane composed (history /
# db_memory_context / portrait) is not consumed by the sidecar, so continuity
# rests on the 4B electing to call recall_memory (it under-fires) and on the
# recall floor above — which only fires on personal-QUESTION shapes. A mood
# STATEMENT is not a question, so nothing carried yesterday's worry.
#
# This is a second trigger class for the same floor: a first-person emotional /
# state statement gets the for-prompt packet composed in `mode="continuity"`
# (what was shared in the last few days leads, emotional rows first — see
# routers.memories._pick_recent_for_continuity) plus one capped portrait line.
# Same wire position as the recall block — inside the latest user message,
# after the identity line — so the sidecar prefix (system prompt + tool block,
# #1725 prompt cache) is untouched.
#
# DEFAULT ON — this is the S4 fix; `ZOE_SEAM_CONTINUITY_INJECT=false|0|off|no`
# is the kill switch (per-call env read, a restart flips it). Fail-open: any
# fetch failure or timeout continues the turn without the block.
_CONTINUITY_INJECT_ENV = "ZOE_SEAM_CONTINUITY_INJECT"

# Emotional / state words a first-person anchor may land on. Deliberately
# excluded: bare "feel/feeling" (→ "I feel like pizza"), "off", "flat", "blue",
# and "sick" (physical, not continuity). "down"/"low"/"not good" carry
# lookaheads for the non-emotional idioms ("I'm down for tacos", "low on milk",
# "not good at chess").
_CONT_STATE = (
    r"(?:anxious|nervous|stressed(?:\s+out)?|stressing|worried|worrying|scared|"
    r"afraid|terrified|on\s+edge|edgy|tense|restless|sad|upset|depressed|"
    r"lonely|miserable|heartbroken|gutted|exhausted|drained|tired|wiped\s+out|"
    r"burnt\s+out|burned\s+out|overwhelmed|frustrated|awful|terrible|horrible|"
    r"rough|crap|crappy|rubbish|panicking|panicky|freaking\s+out|dreading|"
    r"down(?!\s+(?:for|to|with|here|there|at|in|on|by)\b)|"
    r"low(?!\s+on\b)|"
    r"not\s+(?:doing\s+)?(?:great|good|ok|okay|well|so\s+(?:good|great|well)|"
    r"too\s+(?:good|great))(?!\s+(?:at|with|for|enough)\b))"
)
# Softeners that may sit between the anchor and the state word.
_CONT_INTENS = (
    r"(?:(?:so|really|pretty|quite|super|very|just|still|totally|kind\s+of|"
    r"kinda|sort\s+of|a\s+bit|a\s+little|a\s+little\s+bit|bit|a\s+lot|"
    r"incredibly|extremely|honestly|feeling|more|even\s+more)\s+){0,3}"
)
# Leading interjections at the start of a sentence ("Ugh, feeling…").
_CONT_LEAD = r"(?:(?:ugh|honestly|man|god|gosh|well|so|just|oh|argh|meh)[\s,!.]+)*"
_CONT_BAD_DAY = (
    r"(?:rough|hard|awful|terrible|horrible|long|bad|tough|stressful|"
    r"exhausting|draining|brutal|crap|crappy|rubbish|shit|shitty|a\s+lot|"
    r"a\s+nightmare|too\s+much)"
)

# First-person emotional/state STATEMENTS only. Every alternative is anchored to
# the speaker: an explicit "I …" subject, or a sentence-initial "feeling …" /
# "still …" / "today was …" / "rough day" whose implied subject is the user.
# Third-person moods ("my sister is stressed"), questions about the world, and
# "I feel like <thing>" never match.
_CONTINUITY_RE = re.compile(
    r"(?:"
    # I'm / I am / I've been / I have been / I was / I feel / I felt / I keep feeling
    r"\bi(?:['’]?m|\s+am|['’]?ve\s+been|\s+have\s+been|\s+was|\s+feel|\s+felt|"
    r"\s+keep\s+feeling|\s+still\s+feel)\s+" + _CONT_INTENS + _CONT_STATE + r"\b"
    r"|"
    # sentence-initial "feeling …" / "still …" (implied first person)
    r"(?:^|[.!?]\s+)\s*" + _CONT_LEAD + r"(?:feeling|still|so)\s+" + _CONT_INTENS
    + _CONT_STATE + r"\b"
    r"|"
    # "today was rough", "this week has been a lot" — SENTENCE-INITIAL only, so
    # a reported third-person day ("my sister said today was rough", "her work
    # has been a lot") never matches.
    r"(?:^|[.!?]\s+)\s*" + _CONT_LEAD
    + r"(?:today|tonight|this\s+(?:week|morning|afternoon|evening)|work|the\s+day|my\s+day)"
    r"(?:['’]s|\s+was|\s+has\s+been|\s+is\s+being)\s+" + _CONT_INTENS + _CONT_BAD_DAY + r"\b"
    r"|"
    # "I had a rough day", "I've had / we had such a long week" — an EXPLICIT
    # first-person subject ("she had a rough day", "have you had…" never match)…
    r"\b(?:i|we)(?:\s+|['’]ve\s+|\s+have\s+|\s+just\s+)had\s+"
    r"(?:a|such\s+a|one\s+of\s+those)\s+"
    + _CONT_INTENS + _CONT_BAD_DAY + r"\s+(?:day|week|night|morning|shift)s?\b"
    r"|"
    # …or the subjectless shorthand "Had a rough day." at a sentence start.
    r"(?:^|[.!?]\s+)\s*" + _CONT_LEAD + r"had\s+(?:a|such\s+a|one\s+of\s+those)\s+"
    + _CONT_INTENS + _CONT_BAD_DAY + r"\s+(?:day|week|night|morning|shift)s?\b"
    r"|"
    # bare "Rough day." at the start of a sentence
    r"(?:^|[.!?]\s+)\s*" + _CONT_LEAD + _CONT_BAD_DAY + r"\s+(?:day|week|night|morning|shift)\b"
    r"|"
    # rumination: "I can't stop thinking about…", "I keep replaying…"
    r"\bi\s+(?:can['’]?t|cannot|couldn['’]?t)\s+stop\s+"
    r"(?:thinking|worrying|stressing|overthinking|crying)\b"
    r"|\bi\s+keep\s+(?:thinking\s+about|worrying|replaying|overthinking|stressing)\b"
    r")",
    re.IGNORECASE,
)

_CONTINUITY_BLOCK_OPEN = (
    "[MEMORY CONTEXT — what this user shared recently; do not mention this block]"
)
# The block closes with ONE concrete instruction. A 4B model handed a list of
# recent facts and a soft "connect if relevant" rarely picks the worry (Samantha
# bar S4, round 1: 0/3 with an 11-bullet block); handed the single item and a
# single ask, it checks in. The focus item is chosen by the composer
# (routers.memories._continuity_focus: the top recent EMOTIONAL row).
_CONTINUITY_FOCUS_ASK = (
    "The user recently told you: {text}{felt}. If it fits, briefly and warmly ask "
    "how that is going before you answer — in your own words, never quoting them."
)
_CONTINUITY_GENERIC_ASK = (
    "If how the user feels connects to something above, gently check in about it "
    "in your own words; never quote it back."
)
_CONTINUITY_BLOCK_CLOSE = _RECALL_BLOCK_CLOSE
_CONTINUITY_PORTRAIT_MAX_CHARS = 240
# Pre-brain budget for the continuity fetch (packet + portrait, concurrent). A
# miss costs only the block, never the turn — voice latency outranks the floor.
_CONTINUITY_TIMEOUT_S = 3.0
# The portrait is optional: a shorter budget measured from the same start.
_CONTINUITY_PORTRAIT_TIMEOUT_S = 1.0


def _continuity_inject_enabled() -> bool:
    """Per-call env read. DEFAULT ON: unset/empty → on; only an explicit
    false/0/off/no turns it off (the kill switch)."""
    raw = os.environ.get("ZOE_SEAM_CONTINUITY_INJECT", "true")
    return (raw or "true").strip().lower() not in {"0", "false", "off", "no"}


async def _fetch_continuity_packet(user_id: str, message: str) -> dict:
    """The for-prompt result composed in continuity mode, fetched IN-PROCESS:
    ``packet`` plus, when there is one, ``continuity_focus`` ({text, affect}).
    A separate seam from ``_fetch_for_prompt_packet`` so tests can stub each
    trigger class independently."""
    from routers.memories import memory_for_prompt

    result = await memory_for_prompt(
        user_id=user_id,
        message=(message or "")[:512],
        limit=_RECALL_MAX_BULLETS,
        mode="continuity",
        _=None,
    )
    return dict(result or {})


async def _fetch_portrait_line(user_id: str) -> str:
    """The user's portrait text ('' when none), raw — ``_portrait_line`` shapes it."""
    from user_portrait import load_portrait

    return await load_portrait(user_id)


def _portrait_line(text: str) -> str:
    """One capped, single-line portrait summary, or ''. The portrait is an
    LLM-synthesised paragraph derived from user content, so it is flattened to
    one line and stripped of the bracket characters the block delimiters use."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"[\[\]]", "", text)
    if len(text) > _CONTINUITY_PORTRAIT_MAX_CHARS:
        text = text[:_CONTINUITY_PORTRAIT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def is_continuity_turn(message: str, user_id: str) -> bool:
    """True when this turn is a CONTINUITY turn — decided by the trigger alone,
    never by whether a packet came back: the flag is on (default), the id is a
    real (non-guest) user, the recall floor does not own the turn (a personal
    or event-shaped question with ZOE_SEAM_RECALL_INJECT on), and the message is a first-person
    emotional/state statement (``_CONTINUITY_RE``). Pure — no I/O.

    The seam defers pending-contact offers on every such turn (even when the
    packet fetch fails or comes back empty), and
    ``latent_intent_detector.detect_and_store`` skips the offer-aging tick on
    it, so a run of emotional turns can never expire an offer it hid."""
    if not _continuity_inject_enabled():
        return False
    uid = (user_id or "").strip()
    if not uid or uid in ("guest", "voice-guest"):
        return False
    msg = message or ""
    if _recall_inject_enabled() and _recall_floor_shape(msg):
        return False  # the recall floor owns recall-question turns
    return bool(_CONTINUITY_RE.search(msg))


async def _continuity_context_block(message: str, user_id: str) -> str:
    """The continuity memory block for this turn, or '' — NEVER raises.

    '' unless the flag is on (default), a real (non-guest) user id is present,
    the message is a first-person emotional/state statement, and the recall
    floor has not already claimed the turn (a personal question with
    ZOE_SEAM_RECALL_INJECT on gets the recall block, never both).
    """
    uid = (user_id or "").strip()
    msg = message or ""
    if not is_continuity_turn(msg, uid):
        if (_continuity_inject_enabled() and uid and uid not in ("guest", "voice-guest")
                and not (_recall_inject_enabled() and _recall_floor_shape(msg))):
            logger.info("SEAM_CONTINUITY user=%s matched=False bullets=0 chars=0", uid)
        return ""
    # Packet and portrait run concurrently but are awaited INDEPENDENTLY: the
    # packet is the payload (own budget); the portrait is best-effort garnish
    # with a shorter budget from the same start, so a slow portrait store can
    # neither drop a completed packet nor stretch the turn.
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    packet_task = asyncio.ensure_future(_fetch_continuity_packet(uid, msg))
    portrait_task = asyncio.ensure_future(_fetch_portrait_line(uid))
    # Retrieve an unawaited portrait failure so it never logs "exception was
    # never retrieved" when the packet path returns early.
    portrait_task.add_done_callback(lambda t: t.cancelled() or t.exception())
    packet, portrait, focus = "", "", {}
    try:
        try:
            packet_res = await asyncio.wait_for(packet_task, timeout=_CONTINUITY_TIMEOUT_S) or {}
            packet = str(packet_res.get("packet") or "")
            focus = packet_res.get("continuity_focus") or {}
        except Exception as exc:  # noqa: BLE001 — continuity must never break a turn
            logger.warning(
                "seam continuity inject: packet fetch failed/timed out, continuing without it: %r",
                exc,
            )
        if packet:
            remaining = max(0.0, _CONTINUITY_PORTRAIT_TIMEOUT_S - (loop.time() - t0))
            try:
                portrait = _portrait_line(
                    str(await asyncio.wait_for(portrait_task, timeout=remaining) or "")
                )
            except Exception as exc:  # noqa: BLE001 — the portrait is optional
                logger.debug("seam continuity inject: portrait skipped: %r", exc)
    finally:
        # Whatever ended the wait — no packet, a timeout, or the TURN itself being
        # cancelled (CancelledError is not an Exception) — never leave either
        # read running past this function.
        for task in (packet_task, portrait_task):
            if not task.done():
                task.cancel()
    portrait_line = f"About this user: {portrait}" if portrait else ""
    ask = _continuity_ask(focus)
    budget = (_RECALL_MAX_CHARS - (len(portrait_line) + 1 if portrait_line else 0)
              - (len(ask) + 1))
    packet = _truncate_packet((packet or "").strip(), max_chars=budget)
    # Content must never close the block early (a stored memory whose text is
    # the close line): wedge a zero-width space into any occurrence.
    packet = packet.replace(_CONTINUITY_BLOCK_CLOSE, "[​" + _CONTINUITY_BLOCK_CLOSE[1:])
    body = "\n".join(p for p in (portrait_line, packet, ask) if p)
    bullets = sum(1 for ln in packet.splitlines() if ln.lstrip().startswith(("-", "•", "*")))
    logger.info(
        "SEAM_CONTINUITY user=%s matched=True bullets=%d chars=%d focus=%s",
        uid, bullets, len(body), bool(ask and focus),
    )
    block = ""
    if packet:  # a portrait alone is not continuity — nothing recent to carry
        block = f"{_CONTINUITY_BLOCK_OPEN}\n{body}\n{_CONTINUITY_BLOCK_CLOSE}"
    if await _continuity_debug_uid(uid):
        logger.info("SEAM_CONTINUITY_DEBUG user=%s block=%r", uid, block)
    return block


def _continuity_ask(focus: dict) -> str:
    """The closing instruction: one concrete check-in when the composer found a
    recent emotional item, else the generic one. Focus text is stored user
    content, so it is flattened and stripped of block-structure characters."""
    text = re.sub(r"\s+", " ", str((focus or {}).get("text") or "")).strip()
    text = re.sub(r"[\[\]]", "", text)[:200].rstrip(" .")
    if not text:
        return _CONTINUITY_GENERIC_ASK
    affect = str((focus or {}).get("affect") or "").strip().lower()
    felt = f" (they said they felt {affect})" if re.fullmatch(r"[a-z][a-z ]{0,23}", affect) else ""
    return _CONTINUITY_FOCUS_ASK.format(text=text, felt=felt)


_CONTINUITY_DEBUG_ENV = "ZOE_SEAM_CONTINUITY_DEBUG"


async def _continuity_debug_uid(user_id: str) -> bool:
    """True only when ZOE_SEAM_CONTINUITY_DEBUG is on (default OFF) AND the id
    is harness-minted AND provably not a real account — the same two checks the
    ``forget-synthetic`` route makes before it will erase an id:

    * shape: ``user_filters.synthetic_forget_refusal`` (``demo_<tag>_<hex>`` /
      ``test_<tag>_<hex>``, not allowlisted, not a guest sentinel);
    * registration: ``routers.memories._registered_account`` (zoe-auth's
      ``auth_users``) — a real account may legally be NAMED like a demo id.

    Any lookup failure returns False (fail closed): a real user's block or reply
    is never logged, whatever the flag says. The cheap flag + shape checks run
    first, so a debug-off box never touches the database here."""
    if (os.environ.get("ZOE_SEAM_CONTINUITY_DEBUG") or "").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return False
    try:
        from user_filters import synthetic_forget_refusal
    except Exception:  # pragma: no cover - in-tree module
        return False
    if synthetic_forget_refusal(user_id or "") is not None:
        return False
    try:
        from routers.memories import _registered_account

        return not await _registered_account(user_id)
    except Exception as exc:  # noqa: BLE001 — unverifiable id → never log it
        logger.debug("seam continuity debug: registration check failed, not logging: %r", exc)
        return False


def _log_prompt_cache(session_id: str, terminal: dict) -> None:
    """Log llama-server prompt-cache reuse for one brain turn, if reported.

    The 2.x sidecar puts ``prompt_cache: [{"prompt_n", "cache_n"}, ...]`` — one
    entry per model call (tool round), in order — on its ``{"done": true}``
    NDJSON terminal (labs/flue-zoe-brain-2x src/streaming.ts). ``prompt_n`` is
    the tokens RE-PREFILLED for that call and ``cache_n`` the tokens reused from
    llama-server's KV/prompt cache. The FIRST round is what gates time-to-first-
    token: tens of tokens is a cache hit (~200 ms); hundreds is a miss (~1.7 ms
    per token of extra prefill). Pair with the same session's ``VOICE TIMING``
    line. Absent field (older sidecar) → nothing logged. Never raises.
    """
    _log_context_budget(session_id, terminal)
    try:
        rounds = terminal.get("prompt_cache")
        if not isinstance(rounds, list) or not rounds:
            return
        pairs: list[tuple[int, int]] = []
        for entry in rounds:
            if not isinstance(entry, dict):
                continue
            try:
                pairs.append((int(entry.get("prompt_n") or 0), int(entry.get("cache_n") or 0)))
            except (TypeError, ValueError):
                continue
        if not pairs:
            return
        first_prompt, first_cache = pairs[0]
        logger.info(
            "FLUE_PROMPT_CACHE session=%s rounds=%d first_prompt_n=%d first_cache_n=%d "
            "total_prompt_n=%d per_round=%s",
            session_id, len(pairs), first_prompt, first_cache,
            sum(p for p, _ in pairs), ",".join(f"{p}/{c}" for p, c in pairs),
        )
    except Exception:  # noqa: BLE001 - instrumentation must never break a turn
        pass


def _log_context_budget(session_id: str, terminal: dict) -> None:
    """One ``FLUE_CONTEXT_BUDGET`` line from the terminal's ``context_budget``:
    the sidecar's chars/4 estimate of the first model call's prompt sections
    (system / tools / history / tail) plus ``stale`` — injected-block tokens in
    older stored user messages — and ``elided`` (1 = ZOE_BRAIN_ELIDE_STALE_BLOCKS
    removed them). Absent (older sidecar) → nothing. Never raises."""
    try:
        b = terminal.get("context_budget")
        if isinstance(b, dict):
            keys = ("system", "tools", "history", "tail", "stale", "elided")
            logger.info("FLUE_CONTEXT_BUDGET session=%s " + " ".join(f"{k}=%d" for k in keys),
                        session_id, *(int(b.get(k) or 0) for k in keys))
    except Exception:  # noqa: BLE001 - instrumentation must never break a turn
        pass


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
                            _log_prompt_cache(session_id, chunk)
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
    **kwargs: Any,
) -> AsyncIterator[str]:
    """Streaming brain turn through the Flue sidecar — see
    ``_run_flue_brain_streaming_turn`` for the contract.

    This wrapper adds ZOE_SEAM_CONTINUITY_DEBUG's reply log (the first 200
    reply chars, harness-minted ids only — ``_continuity_debug_uid``) and the
    first-turn day brief (``brief_first_turn``, ZOE_BRIEF_ON_FIRST_TURN, default
    OFF): the dated ``[Today …]`` block rides after the user's words like the
    continuity block, and today's shared claim is taken once any real reply text
    was emitted — also when the stream then dies or the consumer walks away. A
    turn that emitted no text (only the fallback) never burns the day's brief.
    The proactivity selector's ``[RAISE …]`` (ZOE_PROACTIVE_SELECTOR, default OFF)
    follows the same prepare/settle contract; the brief wins a turn they share."""
    import brief_first_turn
    from proactive import selector as proactive_selector

    brief = await brief_first_turn.prepare(message, user_id, session_id)
    raised = await proactive_selector.prepare(
        message, user_id, session_id, brief_active=brief is not None)
    turn = _run_flue_brain_streaming_turn(
        message, session_id, user_id, day_brief_block=brief.block if brief else "",
        raise_block=raised.block if raised else "", **kwargs,
    )
    debug = await _continuity_debug_uid((user_id or "").strip())
    reply: list[str] = []
    emitted = False  # real reply text went out (never a sentinel or the fallback)
    try:
        async for delta in turn:
            if not delta.startswith(("__TOOL__:", "__THINKING__:")):
                if debug:
                    reply.append(delta)
                if delta.strip() and delta != _FALLBACK_TEXT:
                    emitted = True
            yield delta
    finally:
        # Close the inner turn deterministically when the consumer stops early
        # (barge-in / cancellation) — exactly as if it had been iterated directly.
        await turn.aclose()
        # Settled HERE, not after the loop: a consumer that disconnects or barges
        # in after the first text exits through this finally (GeneratorExit or a
        # CancelledError), and the brief it heard must still take the claim.
        if brief is not None:
            await brief_first_turn.settle(brief, produced=emitted)
        if raised is not None:
            await proactive_selector.settle(raised, produced=emitted)
        if debug:
            logger.info(
                "SEAM_CONTINUITY_DEBUG user=%s session=%s reply=%r",
                (user_id or "").strip(), session_id, "".join(reply)[:200],
            )


async def _run_flue_brain_streaming_turn(
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
    # ZOE_RECALL_EVIDENCE (default OFF): note this turn's shape so a
    # recall_memory TOOL call made during it quotes the user's words when asked
    # "what did I say / are you sure" (the tool only sends the model's query).
    from recall_evidence import note_turn

    note_turn(uid, message)
    # Deterministic recall floor (default OFF): on a personal-, event- or evidence-shaped
    # question turn, prepend the for-prompt packet so recall no longer depends on the model
    # electing to call its recall_memory tool. Placed BEFORE the identity wrap
    # so the block rides AFTER the identity line on the wire (the sidecar's
    # single-line strip regex is anchored at message start).
    recall_block = await _recall_context_block(message, uid)
    # Continuity (default ON): a first-person mood/state STATEMENT gets the
    # recent-first packet so yesterday's worry reaches today's reply. Never on
    # the same turn as a recall block — the recall floor owns question turns.
    # It rides AFTER the user's words (closest to the reply, where a 4B model
    # acts on it), still inside the latest user message — the sidecar prefix
    # and the #1725 prompt cache are untouched.
    continuity_block = ""
    continuity_turn = False
    if not recall_block:
        continuity_block = await _continuity_context_block(message, uid)
        continuity_turn = is_continuity_turn(message, uid)
    # Offer nudge on ANY turn — skipped when the recall packet already carries
    # the offer directive (the fold tags them "[pending-contact]"), so a
    # recall-shaped turn never asks twice. DEFERRED on a continuity turn: the
    # check-in is that turn's one job, and with the offer present the reply
    # ended in "Would you like me to add Marisol as a contact?" on every sample
    # (Samantha bar S4 round 3). Decided by the TRIGGER, not by the block: an
    # emotional turn whose packet timed out or came back empty still defers.
    # The offer is not surfaced on this turn (the continuity composer omits the
    # fold too) and the per-turn ager skips continuity turns
    # (latent_intent_detector), so the next non-emotional turn offers it.
    offer_block = ""
    raise_block = str(kwargs.get("raise_block") or "")
    if continuity_turn or continuity_block:
        if _offer_inject_enabled():
            logger.info("SEAM_OFFER user=%s deferred=1 reason=continuity", uid)
    elif raise_block:  # one ask per turn, same evidence as continuity (S4 round 3)
        if _offer_inject_enabled():
            logger.info("SEAM_OFFER user=%s deferred=1 reason=raise", uid)
    elif "[pending-contact]" not in recall_block:
        offer_block = await _pending_offer_block(uid)
    _blocks = "\n".join(b for b in (recall_block, offer_block) if b)
    # Sanitise BEFORE assembling: a user-typed " zoe-replay:" line must never reach
    # the start of the outbound message and forge the trusted marker. Only reachable
    # when there is no identity line ahead of it — both blocks return "" for a blank
    # uid — but strip unconditionally rather than depend on that coupling.
    safe_message = _strip_replay_envelope(message)
    brain_message = f"{_blocks}\n{safe_message}" if _blocks else safe_message
    if continuity_block:
        brain_message = f"{brain_message}\n{continuity_block}"
    # First-turn day brief (brief_first_turn, default OFF): same position as the
    # continuity block — after the user's words, inside the latest user message.
    day_block = str(kwargs.get("day_brief_block") or "")
    if day_block:
        brain_message = f"{brain_message}\n{day_block}"
    if raise_block:  # proactive.selector: never on the same turn as the day brief
        brain_message = f"{brain_message}\n{raise_block}"
    outbound_message = _wrap_message_with_identity(brain_message, uid)
    # Replay isolation rides OUTSIDE the identity wrap so its line is first on the
    # wire. Only the replay harness ever passes this; absent → unchanged bytes.
    outbound_message = _wrap_message_with_replay(
        outbound_message, bool(kwargs.get("replay_isolation"))
    )
    # B1.1: a speculative voice turn's id rides OUTERMOST so the sidecar can echo
    # it on this turn's tool writes. Nothing bound (every other turn) → unchanged.
    outbound_message = _wrap_message_with_speculative_turn(
        outbound_message, await _speculative_turn_for_wire()
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
                                    _log_prompt_cache(session_id, chunk)
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
