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

Pure asyncio + stdlib so it is slim-CI testable; the router only wires it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
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


def cancelled_frame(gate: SpeculationGate) -> bytes:
    return (json.dumps({
        "done": True, "cancelled": True, "reply": "",
        "turn_id": gate.turn_id, "reason": gate.verdict(),
        "transcript": gate.speculative_transcript or "",
    }) + "\n").encode()


def _record_outcome(outcome: str) -> None:
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
    try:
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
                pending = asyncio.ensure_future(upstream.__anext__())
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
            pending = None
            if held or is_audible_frame(frame):
                held.append(frame)
            else:
                yield frame

        verdict = gate.verdict()
        _record_outcome(verdict)
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
