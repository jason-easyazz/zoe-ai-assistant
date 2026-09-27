"""B1.1 speculation gate for /api/voice/turn_stream (flag-dark, default OFF).

The panel daemon fires a turn at its FIRST end-of-turn verdict (a short deep-quiet
tail) instead of the full endpoint tail, so STT + brain start ~300-800 ms earlier.
The cost of guessing early is that the user may not have finished: the daemon keeps
recording and later delivers a verdict. This module holds everything AUDIBLE the
turn stream produces until that verdict arrives, so:

  * nothing audible is ever sent for a cancelled speculative turn, and
  * a committed turn is released exactly once, in order, on the same connection.

Design: server-side gate, daemon-owned verdict (see
docs/architecture/b1-speculative-turn-start.md). Sources: HF speech-to-speech
``--speculative_reopen_ms``, Pipecat ``speculation_gate.py``, LiveKit
``_transcripts_equivalent``.

Phase 2 — side effects wait for the verdict. The gate only holds what is AUDIBLE;
a speculative turn also runs the intent layer on a transcript PREFIX, and a write
there ("add milk" before the user finished "…and eggs") cannot be undone by a
cancel. So while a speculative turn is unresolved, every side effect waits for the
verdict and is dropped on cancel: the gate is bound to the turn's task tree through
a ``ContextVar`` (``bind``), and the write funnels call ``await_commit`` (inline
writes) or ``defer_until_commit`` (background writes — history, memory passes,
queued in spawn order). Reads and chat run speculatively. With no gate bound —
every non-speculative turn, every other channel, flag off — both are no-ops.

Pure asyncio + stdlib so it is slim-CI testable; the router only wires it.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from collections import OrderedDict
from typing import AsyncIterator, Optional

from typed_env import env_bool, env_int

logger = logging.getLogger(__name__)

# Literal keys in the env reads below (not these constants) so the flag-inventory
# scanner records the server-side readers; the names are exported for tests.
FLAG = "ZOE_SPECULATIVE_TURN"
MAX_HOLD_FLAG = "ZOE_SPECULATIVE_MAX_HOLD_MS"

ACTIONS = ("commit", "cancel", "resolve")

# Cancel reasons that are reported (metric label + ``reason`` on the cancelled
# frame) as their own outcome: neither is a user interruption, so neither may
# count toward the cancellation-rate gate.
_DISTINCT_CANCEL_REASONS = ("hold_timeout", "empty_transcript")


class DuplicateTurn(ValueError):
    """``open_gate`` for a ``turn_id`` whose gate is still unresolved."""


class SpeculativeTurnCancelled(asyncio.CancelledError):
    """A side effect was held for a speculative turn whose verdict was not a
    commit, so it is DROPPED. A ``CancelledError`` on purpose: every write site
    sits under ``except Exception`` handlers that would otherwise swallow it and
    carry on (fall through to another tier, synthesize a "done" reply, save
    history) — the turn is dead, and it must end like a cancelled task."""


def speculative_turn_enabled() -> bool:
    """Per-call env read (like the other voice flags) so a flip needs no restart."""
    return env_bool("ZOE_SPECULATIVE_TURN", default=False)


def max_hold_seconds() -> float:
    """Safety valve: no verdict within this window → cancel (fail-closed to silence)."""
    return max(0.05, env_int("ZOE_SPECULATIVE_MAX_HOLD_MS", default=5000) / 1000.0)


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def normalize_transcript(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub("", (text or "").lower())).strip()


def transcripts_equivalent(speculative: Optional[str], final: Optional[str]) -> bool:
    """LiveKit-style: the resumed speech changed nothing if the normalised
    transcripts are equal. Two empties are NOT equivalent — there is nothing to
    release for an empty speculative turn."""
    a, b = normalize_transcript(speculative or ""), normalize_transcript(final or "")
    return bool(a) and a == b


def is_audible_frame(frame: bytes) -> bool:
    """A wire line the panel would PLAY: a ``chunk``/``full_audio`` header or the
    base64 body line that follows a header (any non-JSON line)."""
    try:
        obj = json.loads(frame)
    except Exception:
        return True
    return isinstance(obj, dict) and ("chunk" in obj or "full_audio" in obj)


class SpeculationGate:
    """One speculative turn's verdict slot. ``resolve`` is thread-safe."""

    def __init__(self, turn_id: str, *, max_hold_s: float) -> None:
        self.turn_id = turn_id
        self.created = time.monotonic()
        self.deadline = self.created + max_hold_s
        self.speculative_transcript: Optional[str] = None
        self.final_transcript: Optional[str] = None
        self.action: Optional[str] = None
        self.reason: Optional[str] = None
        self.event = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        # Tail of this turn's deferred background side effects (FIFO chain).
        self._side_effect_tail: Optional[asyncio.Future] = None

    @property
    def resolved(self) -> bool:
        return self.action is not None

    def resolve(self, action: str, *, final_transcript: Optional[str] = None,
                reason: Optional[str] = None) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown speculation action {action!r}")
        if self.resolved:
            return  # first verdict wins; a late duplicate is ignored
        self.action = action
        self.final_transcript = final_transcript
        self.reason = reason
        try:
            self._loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:  # loop closed — nothing left to wake
            pass

    def verdict(self) -> str:
        """``commit`` / ``equivalent`` / ``cancel`` / ``hold_timeout`` / ``empty_transcript``."""
        if self.action == "commit":
            return "commit"
        if self.action == "resolve":
            return ("equivalent"
                    if transcripts_equivalent(self.speculative_transcript, self.final_transcript)
                    else "cancel")
        return self.reason if self.reason in _DISTINCT_CANCEL_REASONS else "cancel"


_GATES: dict[str, SpeculationGate] = {}

# Verdicts under which the speculative turn's work is kept (and so its side effects run).
RELEASED_VERDICTS = ("commit", "equivalent")

# Safety cap on how long a deferred background effect waits for the previous one
# of the same turn (history row, then memory passes). Ordering is by completion;
# this only bounds a wedged predecessor.
_SIDE_EFFECT_ORDER_CAP_S = 60.0

# ── Phase 2: side effects wait for the verdict ─────────────────────────────
#
# Bound for the speculative turn's task tree only (``bind`` in the router around
# the voice_command task; ``gate_frames`` binds its upstream pulls). ContextVars
# are copied into every task created beneath, so background tasks spawned by the
# turn see it too — and nothing else ever does.
_BOUND: contextvars.ContextVar[Optional[SpeculationGate]] = contextvars.ContextVar(
    "zoe_speculation_gate", default=None)

# Intents that are idempotent READS with no side effect — safe to run on a
# transcript prefix. FAIL-CLOSED: anything not listed (every write, every
# device/music/timer action, every unknown or future intent) waits for the
# verdict. Superset of fast_tiers._TIER0_READ_INTENTS (pinned by a test).
SPECULATION_SAFE_INTENTS = frozenset({
    "time_query", "date_query", "weather", "calculate", "greeting",
    "acknowledgement", "status_check", "time_planning_clarification",
    "list_show", "calendar_show", "reminder_list", "timer_status",
    "note_search", "people_search", "recipe_search",
    "journal_streak", "journal_prompt", "transaction_summary",
})

# Routed domains (semantic_router ``routed``) whose brain turn may start before
# the verdict: chat and the pure-read domains. Every other domain (lists,
# calendar, reminders, timers, people, memory, music, smart_home, notes, …) is a
# write-capable domain, so the WHOLE turn waits for the verdict.
SPECULATION_SAFE_ROUTED_DOMAINS = frozenset({"chat", "weather", "time"})

# Skybridge (domain, action) pairs that only render/read. Fail-closed as above.
_SAFE_SKYBRIDGE_DOMAINS = frozenset({"clock", "weather"})
_SAFE_SKYBRIDGE_ACTIONS = frozenset({"show", "status", "overview", "forecast", "identity"})


def bind(gate: Optional[SpeculationGate]) -> contextvars.Token:
    """Bind ``gate`` for the current context (and every task created from it)."""
    return _BOUND.set(gate)


def unbind(token: contextvars.Token) -> None:
    _BOUND.reset(token)


def bound_gate() -> Optional[SpeculationGate]:
    return _BOUND.get()


def intent_is_speculation_safe(intent_name: Optional[str]) -> bool:
    return (intent_name or "") in SPECULATION_SAFE_INTENTS


def skybridge_intent_is_speculation_safe(domain: Optional[str], action: Optional[str]) -> bool:
    return (domain or "") in _SAFE_SKYBRIDGE_DOMAINS or (action or "") in _SAFE_SKYBRIDGE_ACTIONS


def turn_is_speculation_safe(intent_name: Optional[str], routed_domain: Optional[str],
                             skybridge: Optional[tuple] = None) -> tuple[bool, str]:
    """Whole-turn classification for a speculative turn, from the intent layer's
    own cheap classifiers (regex ``detect_intent``, the semantic router's
    ``routed`` domain, Skybridge's ``classify_skybridge_intent``).

    Any signal that says WRITE holds the turn: a non-read intent, a non-read
    Skybridge action, or a routed domain outside {chat, weather, time}. A MISSING
    routed domain (router off — its default — or failed) is NEUTRAL, not a write:
    the verdict then rests on the regex read-allowlist and the Skybridge pair, and
    a brain turn that later reaches for a write tool is still held at the
    intent-dispatch seam (turn-id echo) or the non-echoing lane hold."""
    if intent_name and not intent_is_speculation_safe(intent_name):
        return False, f"intent:{intent_name}"
    if skybridge is not None and not skybridge_intent_is_speculation_safe(*skybridge):
        return False, f"skybridge:{skybridge[0]}:{skybridge[1]}"
    if routed_domain and routed_domain not in SPECULATION_SAFE_ROUTED_DOMAINS:
        return False, f"domain:{routed_domain}"
    return True, "read_or_chat"


async def await_commit(what: str, gate: Optional[SpeculationGate] = None) -> None:
    """Hold an inline side effect until the bound speculative turn is decided.

    Returns at once when no speculative turn is bound, or its verdict already
    released it; otherwise waits (bounded by the gate's max-hold deadline) and
    returns on commit/equivalent. Any other verdict — cancel, hold timeout,
    client gone — raises ``SpeculativeTurnCancelled``: the side effect never runs.
    """
    gate = gate if gate is not None else _BOUND.get()
    if gate is None:
        return
    if not gate.resolved:
        logger.info("voice/turn_stream speculation holding side effect %s until verdict turn_id=%s",
                    what, gate.turn_id)
        remaining = gate.deadline - time.monotonic()
        if remaining > 0:
            try:
                await asyncio.wait_for(gate.event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
        if not gate.resolved:
            gate.resolve("cancel", reason="hold_timeout")
    verdict = gate.verdict()
    if verdict in RELEASED_VERDICTS:
        return
    logger.info("voice/turn_stream speculation dropped side effect %s (%s) turn_id=%s",
                what, verdict, gate.turn_id)
    raise SpeculativeTurnCancelled(f"{what}: speculative turn {gate.turn_id} {verdict}")


# Verdicts of recently CLOSED gates, so a brain-tool dispatch that arrives just
# after its turn's stream ended still gets the right answer (released → run,
# dropped → refuse). Bounded; oldest evicted first.
_RECENT_VERDICTS: "OrderedDict[str, str]" = OrderedDict()
_RECENT_VERDICTS_MAX = 256

# The daemon mints turn ids as hex; anything else is never forwarded to the brain.
_TURN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def bound_turn_id() -> Optional[str]:
    """The bound speculative turn's id when it is safe to put on the wire, else None.
    The brain seam forwards it (``zoe_flue_client``) so the sidecar echoes it back on
    every intent-dispatch the turn's tools make."""
    gate = _BOUND.get()
    if gate is None or not _TURN_ID_RE.match(gate.turn_id or ""):
        return None
    return gate.turn_id


async def hold_speculative_dispatch(turn_id: Optional[str], intent_name: str) -> Optional[str]:
    """Brain-lane hold for ``POST /api/system/intent-dispatch``.

    A speculative chat turn's brain runs before the verdict, and its write tools
    reach zoe-data as a SEPARATE request, which the ContextVar cannot follow. The
    Flue sidecar echoes the turn id it was sent (``speculative_turn_id``), so the
    hold is keyed on the ORIGINATING turn: only that turn's non-read dispatches
    wait for its verdict. No id (every other session, channel or lane) → runs at
    once.

    Returns None to proceed, or the refusal reason (the caller answers
    ``ok: false``; a cancelled stream is never heard):
      ``speculative_turn_cancelled`` — the turn's verdict dropped it;
      ``speculative_turn_unknown``   — flag on, but this process has neither the
        gate nor a recorded outcome for the id (e.g. zoe-data restarted mid-turn,
        taking the verdict with it). FAIL-CLOSED: permitting it would let a
        cancelled turn's write execute. The user hears nothing from a turn whose
        stream died with the restart, and the daemon re-runs the full recording.
    """
    if not turn_id or intent_is_speculation_safe(intent_name):
        return None
    gate = _GATES.get(turn_id)
    if gate is not None:
        try:
            await await_commit(f"brain-tool:{intent_name}", gate)
        except SpeculativeTurnCancelled:
            return "speculative_turn_cancelled"
        return None
    verdict = _RECENT_VERDICTS.get(turn_id)
    if verdict is None:
        if not speculative_turn_enabled():
            return None  # flag off: nothing here is speculative any more
        logger.warning("intent-dispatch refused %s: speculative turn_id=%s unknown to this process "
                       "(no gate, no recorded verdict) — fail-closed", intent_name, turn_id)
        return "speculative_turn_unknown"
    return None if verdict in RELEASED_VERDICTS else "speculative_turn_cancelled"


def defer_until_commit(coro, what: str = "background"):
    """Wrap a background side-effect coroutine for the bound speculative turn.

    No gate bound → ``coro`` unchanged (schedule it as usual). Otherwise returns a
    coroutine that waits for the verdict, runs ``coro`` exactly once on
    commit/equivalent — after every earlier deferred effect of the same turn
    (spawn order: the user-turn row lands before the reply row) — and closes it
    unrun on cancel.
    """
    gate = _BOUND.get()
    if gate is None:
        return coro
    prev: Optional[asyncio.Future] = gate._side_effect_tail
    done: asyncio.Future = gate._loop.create_future()
    gate._side_effect_tail = done

    async def _deferred():
        try:
            try:
                await await_commit(what, gate)
            except SpeculativeTurnCancelled:
                _close_unstarted(coro)
                return None
            if prev is not None and not prev.done():
                # Spawn order: start only once the PREVIOUS effect has finished.
                # The cap is a safety valve against a wedged effect, not a
                # schedule — hitting it is a bug worth a WARNING.
                _done, _pending = await asyncio.wait({prev}, timeout=_SIDE_EFFECT_ORDER_CAP_S)
                if _pending:
                    logger.warning("voice/turn_stream speculation: %s waited %.0fs for the previous "
                                   "side effect of turn_id=%s; running it out of order",
                                   what, _SIDE_EFFECT_ORDER_CAP_S, gate.turn_id)
            return await coro
        finally:
            if not done.done():
                done.set_result(None)
            _close_unstarted(coro)  # cancelled while waiting: never run, no "never awaited" warning

    return _deferred()


def _close_unstarted(coro) -> None:
    if getattr(coro, "cr_frame", None) is not None and not getattr(coro, "cr_running", False):
        close = getattr(coro, "close", None)
        if close is not None:
            close()


def open_gate(turn_id: str) -> SpeculationGate:
    """Register the gate for ``turn_id``. ``turn_id`` is request-supplied, so a
    retried/duplicated ``/turn_stream`` must NOT silently replace an active
    gate (the verdict would resolve only the newer one and the first stream
    would hold to timeout while a second brain call ran): it is refused with
    ``DuplicateTurn`` (409 at the router). A resolved gate no longer blocks."""
    existing = _GATES.get(turn_id)
    if existing is not None and not existing.resolved:
        raise DuplicateTurn(turn_id)
    gate = SpeculationGate(turn_id, max_hold_s=max_hold_seconds())
    _GATES[turn_id] = gate
    return gate


def get_gate(turn_id: str) -> Optional[SpeculationGate]:
    return _GATES.get(turn_id or "")


def close_gate(gate: SpeculationGate) -> None:
    if _GATES.get(gate.turn_id) is gate:
        _GATES.pop(gate.turn_id, None)
    if not gate.resolved:
        gate.resolve("cancel", reason="closed")
    _RECENT_VERDICTS[gate.turn_id] = gate.verdict()
    _RECENT_VERDICTS.move_to_end(gate.turn_id)
    while len(_RECENT_VERDICTS) > _RECENT_VERDICTS_MAX:
        _RECENT_VERDICTS.popitem(last=False)


def ack_frame(gate: SpeculationGate) -> bytes:
    """First frame of every gated stream. It is the daemon's PROOF that the
    server is gating: audio on a speculative stream that never carried this
    frame means the server's flag is off (rollback / one-sided rollout) and the
    daemon must play nothing. Never emitted with the flag off (wire unchanged)."""
    return (json.dumps({"speculation": "gated", "turn_id": gate.turn_id}) + "\n").encode()


def cancelled_frame(gate: SpeculationGate, reason: Optional[str] = None) -> bytes:
    return (json.dumps({
        "done": True, "cancelled": True, "reply": "",
        "turn_id": gate.turn_id, "reason": reason or gate.verdict(),
        "transcript": gate.speculative_transcript or "",
    }) + "\n").encode()


def record_outcome(outcome: str) -> None:
    try:
        from voice_metrics import voice_speculation_count
        voice_speculation_count.labels(outcome=outcome).inc()
    except Exception:
        pass


async def gate_frames(upstream: AsyncIterator[bytes], gate: SpeculationGate) -> AsyncIterator[bytes]:
    """Forward ``upstream`` through the gate.

    Until the verdict: non-audible frames pass through while nothing is held;
    from the first audible frame on, EVERY frame is held in order. Verdict
    commit/equivalent → flush, then pass through. Anything else → drop the
    held frames, close upstream (its ``finally`` cancels the brain task) and
    end the stream with a ``cancelled`` done frame. Never resolves → max-hold
    cancel.
    """
    held: list[bytes] = []
    pending: Optional[asyncio.Task] = None
    upstream_done = False
    started = False  # has upstream.__anext__ ever been called?
    # Every pre-verdict pull runs with the gate BOUND, so the work the stream does
    # lazily inside its generator (the brain lane runs there) sees the same
    # side-effect barrier as the voice_command task. Each pull is its own task
    # created inside this context, so the binding cannot leak to the caller.
    pull_ctx = contextvars.copy_context()
    pull_ctx.run(_BOUND.set, gate)
    try:
        yield ack_frame(gate)
        while not gate.resolved:
            remaining = gate.deadline - time.monotonic()
            if remaining <= 0:
                gate.resolve("cancel", reason="hold_timeout")
                break
            waiter = asyncio.ensure_future(gate.event.wait())
            if upstream_done:
                await asyncio.wait({waiter}, timeout=remaining)
                if not waiter.done():
                    waiter.cancel()
                continue
            if pending is None:
                pending = pull_ctx.run(asyncio.ensure_future, upstream.__anext__())
                started = True
            done, _ = await asyncio.wait({pending, waiter}, timeout=remaining,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not waiter.done():
                waiter.cancel()
            if pending not in done:
                continue
            try:
                frame = pending.result()
            except StopAsyncIteration:
                pending = None
                upstream_done = True
                if not held:
                    return  # nothing audible was ever produced — nothing to gate
                continue
            except asyncio.CancelledError:
                # A side effect held by ``await_commit`` ends the turn with
                # SpeculativeTurnCancelled once the verdict drops it. That is
                # the verdict's consequence, not a stream failure: let the
                # verdict path below answer ``cancelled``. Before any verdict a
                # cancelled upstream is still an error and propagates.
                pending = None
                if not gate.resolved:
                    raise
                upstream_done = True
                continue
            pending = None
            if held or is_audible_frame(frame):
                held.append(frame)
            else:
                yield frame

        verdict = gate.verdict()
        record_outcome(verdict)
        logger.info("voice/turn_stream speculation %s turn_id=%s held=%d",
                    verdict, gate.turn_id, len(held))
        if verdict not in ("commit", "equivalent"):
            held.clear()
            yield cancelled_frame(gate)
            return
        for frame in held:
            yield frame
        held.clear()
        if pending is not None:
            task, pending = pending, None
            try:
                yield await task
            except StopAsyncIteration:
                return
        if not upstream_done:
            async for frame in upstream:
                yield frame
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close_gate(gate)
        aclose = getattr(upstream, "aclose", None)
        if aclose is not None:
            if not started:
                # A verdict that arrived before the first pull (cancel during
                # STT) means upstream was never started — and ``aclose()`` on a
                # never-started async generator does NOT run its ``finally``,
                # which is where the router cancels the brain task. Prime it to
                # its first yield (the transcript line: no brain work) so the
                # close below reaches that cleanup.
                try:
                    await upstream.__anext__()
                except StopAsyncIteration:
                    pass
                except Exception as exc:  # cleanup must not raise
                    logger.debug("speculation: priming upstream for close failed: %s", exc)
            try:
                await aclose()
            except Exception:
                pass
