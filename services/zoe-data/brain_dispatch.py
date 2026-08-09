"""Single source of truth for which brain answers a turn.

zoe-core (Pi on local Gemma) by default; the legacy ``zoe_agent`` brain only when
``ZOE_USE_CORE_BRAIN`` is explicitly off (the validation-window fallback). Every
entry point — text chat AND all the voice paths — routes through here so the
cutover flag controls them consistently. ``routers/chat.py`` imports these under
its historical private names (``_use_flue_brain`` / ``_brain_streaming`` /
``_brain_oneshot``), so its call sites and the routing tests that patch those
names on the chat module keep working unchanged.

Cutover seam: ``ZOE_BRAIN_BACKEND='flue'`` (default ``'core'``) opts the brain
lane into the Flue brain sidecar (``zoe_flue_client``) instead of zoe-core. This
is ADDITIVE and default-OFF — with the env unset/``'core'`` dispatch is
byte-identical to today, so the live voice path is unaffected. The flip is
operator-gated on voice-corpus parity; reversible by env toggle (no migration).

Lane selection vs. failover
---------------------------
``flue > core > legacy`` is **configured lane selection**, evaluated once per
turn from the env — it is NOT, by itself, runtime failover. With
``ZOE_BRAIN_BACKEND=flue`` and a down sidecar, EVERY turn is answered by the
flue client's canned sentinel even though the core lane is healthy (#1613).

``ZOE_BRAIN_FAILOVER`` (default **OFF**) adds bounded runtime failover on top,
and only on top of the strongest possible evidence that a retry is safe:

* **Trigger — transport only.** ``zoe_flue_client.FlueTransportError``, raised
  exclusively for a pre-admission connect failure (refused / connect timeout /
  connect-time reset — the fast-fail ~100 ms class) with no text yielded and no
  2xx admission. That proves the sidecar never executed the turn. Model errors,
  HTTP status errors, slow generations and read timeouts do NOT trigger it —
  the sidecar is running that turn, and re-dispatching would double-run its
  tools/writes (the #1137 duplicate-write class).
* **Never after the first token.** A turn that already streamed output is never
  re-dispatched under any condition — the voice replay invariant: the panel
  would speak the reply twice. A failure after the first delta ends the turn and
  is surfaced in the log; it is never retried.
* **Exactly once.** One retry, on the next configured lane (core, or legacy when
  ``ZOE_USE_CORE_BRAIN`` is off). No loop, no second lane hop.
* **Short-TTL circuit breaker.** After a transport failure the flue lane is
  skipped outright for ``ZOE_BRAIN_FAILOVER_COOLDOWN_S`` (default 45 s), so
  subsequent turns don't pay the failed-connect tax; the first turn after the
  TTL lapses is the probe that re-checks flue (and re-opens the breaker if it is
  still down, or closes it on success). In-process and per-worker by design —
  a restart clears it, which is the correct fail-toward-probing default.
  **Concurrency-safe:** turns overlap, so the breaker is a (deadline,
  generation) pair mutated only by compare-and-set against the generation the
  turn observed, and the half-open probe is claimed by exactly one turn. A
  stale outcome can neither erase a newer open nor re-arm a breaker a newer
  probe just closed. This only ever affects WHETHER flue is attempted — the
  replay, exactly-one-retry and flag-off invariants above are independent of
  it, so the cost of getting it wrong was an extra failed connect, not a
  double-spoken turn.

Every turn emits ONE greppable ``BRAIN_LANE`` line naming the lane attempted and
the lane that served it, so an operator can assert which brain answered
(``grep BRAIN_LANE ~/.zoe-logs/*`` — zoe-data logs to ``~/.zoe-logs/``, not
journald). That line is emitted in BOTH flag states: it is observability, not
dispatch. With the flag off, the lane SELECTION is byte-identical to today.

Two properties make that line trustworthy rather than merely present:

* **Exactly one, on every path a turn can leave by.** The streaming wrapper is
  an async generator, so a consumer that disconnects after the first delta
  throws ``GeneratorExit`` at the yield and skips everything after it. The
  record is therefore emitted through a once-only helper wrapped in
  ``try/finally``: an interrupted turn logs ``outcome=interrupted`` from
  cleanup instead of logging nothing at all.
* **The outcome is the client's verdict, not "the generator finished".**
  ``zoe_flue_client`` renders an HTTP error, a read timeout, an empty 200, a
  rejected wire-2 body and a post-admission stream death alike as its canned
  sentinel and ends the stream normally, so an inferred label called every one
  of them ``ok``. The client reports its terminal verdict through an opt-in
  outcome sink and the line carries it (``ok`` / ``fallback`` / ``error``).
  Labels only — the retry decision remains ``FlueTransportError`` alone.

Imports are lazy inside each function to avoid import-time cycles
(main.py → routers.chat → ... ).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# Indirected so tests can drive the circuit breaker with a fake clock instead of
# sleeping. Monotonic: wall-clock jumps must not extend or cancel a cooldown.
_monotonic = time.monotonic

_DEFAULT_COOLDOWN_S = 45.0

# In-process breaker state.
#
# ``_flue_circuit_open_until`` is the monotonic deadline until which the flue
# lane is skipped (0.0 == closed). ``_flue_circuit_generation`` is a counter
# bumped on every state transition, and it is what makes the breaker correct
# under CONCURRENT turns: a turn may only act on the state it OBSERVED.
#
# Without it, two in-flight turns clobber each other. Turns A and B both enter
# with the breaker closed; B's connect fails and arms a fresh deadline; A's
# reply then lands and an unconditional close ERASES B's newer open, so the
# next turn pays another failed connect. That is real even single-threaded:
# both mutations run after their own ``await``, in either order. So every
# mutation is a compare-and-set against the observed generation — a stale
# outcome (from a turn that started before the current state) is dropped,
# because a newer turn has fresher evidence about whether flue is reachable.
#
# The lock is ``threading.Lock``, not ``asyncio.Lock``: the critical sections
# are a few statements with no ``await`` inside, so this never blocks the event
# loop, it has no loop affinity (an ``asyncio.Lock`` binds to the loop that
# created it, which breaks across the per-test loops and across any worker
# thread), and it stays correct if a turn is ever driven from a thread pool.
# Uncontended acquisition is nanoseconds — nothing measurable next to a turn.
_breaker_lock = threading.Lock()
_flue_circuit_open_until: float = 0.0
_flue_circuit_generation: int = 0


def use_core_brain() -> bool:
    """True when the brain is zoe-core (default); read lazily so a .env value
    bootstrapped after import is honored."""
    return (os.environ.get("ZOE_USE_CORE_BRAIN", "true") or "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def use_flue_brain() -> bool:
    """True ONLY when ``ZOE_BRAIN_BACKEND == 'flue'`` (default ``'core'``).

    The additive, default-OFF cutover seam to the Flue brain sidecar. With the
    env unset or ``'core'`` this returns False and dispatch is byte-identical to
    today (zoe-core, legacy on fallback). Read lazily so a .env value
    bootstrapped after import is honored. The flip is operator-gated on
    voice-corpus parity — do not change the default here.
    """
    return (os.environ.get("ZOE_BRAIN_BACKEND", "core") or "").strip().lower() == "flue"


def failover_enabled() -> bool:
    """True when ``ZOE_BRAIN_FAILOVER`` opts into runtime lane failover.

    DEFAULT OFF (lab-prove-before-prod, `docs/VISION.md` principle 3): brain
    dispatch is a gated voice path, so the operator flips this only after the
    voice replay gate passes on real corpus. Read per call so `.env` + restart
    flips and rolls back with no code change.
    """
    return (os.environ.get("ZOE_BRAIN_FAILOVER", "0") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _cooldown_s() -> float:
    """Circuit-breaker TTL. Default 45 s — long enough that a dead sidecar costs
    one failed connect per cooldown rather than one per turn, short enough that a
    restarted sidecar is picked up within a normal conversational pause."""
    # Literal default inline (not the constant) so the generated flag inventory
    # shows the real value; `_DEFAULT_COOLDOWN_S` pins it, and
    # test_flag_default_is_off_and_cooldown_default_is_documented asserts they agree.
    raw = os.environ.get("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "45")
    if not str(raw or "").strip():
        return _DEFAULT_COOLDOWN_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "ZOE_BRAIN_FAILOVER_COOLDOWN_S=%r is not a number — using %ss",
            raw,
            _DEFAULT_COOLDOWN_S,
        )
        return _DEFAULT_COOLDOWN_S
    return value if value > 0 else 0.0


def _circuit_open() -> bool:
    """True while the flue lane is being skipped — a read-only probe of the
    state (tests + diagnostics). Turns use ``_observe_circuit`` instead, which
    also claims the half-open probe."""
    with _breaker_lock:
        return _flue_circuit_open_until > _monotonic()


def _observe_circuit() -> tuple[bool, int]:
    """Take this turn's view of the breaker: ``(skip_flue, generation)``.

    The generation is the token the turn hands back to ``_open_circuit`` /
    ``_close_circuit``; a mutation is applied only while it is still current.

    This is also where the half-open probe is CLAIMED. When the deadline has
    lapsed, the first turn to see it re-arms the deadline (bumping the
    generation) and is the only turn that dials flue; other turns crossing the
    same lapse see an armed breaker and take the fallback lane rather than
    piling a second failed connect onto a sidecar that is probably still down.
    The probe's own outcome supersedes: success closes the breaker for
    everyone, another transport failure re-arms it.
    """
    global _flue_circuit_open_until, _flue_circuit_generation
    with _breaker_lock:
        now = _monotonic()
        deadline = _flue_circuit_open_until
        if deadline > now:
            return True, _flue_circuit_generation
        if deadline:
            # Lapsed: claim the single half-open probe.
            ttl = _cooldown_s()
            _flue_circuit_generation += 1
            _flue_circuit_open_until = (now + ttl) if ttl > 0 else 0.0
        return False, _flue_circuit_generation


def _open_circuit(observed_generation: int) -> None:
    """Arm the breaker for a fresh TTL — only if nothing changed underneath.

    A stale failure (a turn that started before the current state was written)
    is dropped: whatever produced the current generation looked at flue more
    recently than this turn did.
    """
    global _flue_circuit_open_until, _flue_circuit_generation
    with _breaker_lock:
        if _flue_circuit_generation != observed_generation:
            return
        ttl = _cooldown_s()
        _flue_circuit_generation += 1
        _flue_circuit_open_until = (_monotonic() + ttl) if ttl > 0 else 0.0


def _close_circuit(observed_generation: int) -> None:
    """Clear ONLY the state this turn observed (compare-and-set).

    The load-bearing half of the fix: an unconditional close lets a turn that
    started earlier erase an open armed by a turn that failed later.
    """
    global _flue_circuit_open_until, _flue_circuit_generation
    with _breaker_lock:
        if _flue_circuit_generation != observed_generation:
            return
        if _flue_circuit_open_until:
            _flue_circuit_generation += 1
            _flue_circuit_open_until = 0.0


def reset_failover_state() -> None:
    """Clear the breaker (tests + an operator-facing reset seam).

    Bumps the generation, so any turn still in flight cannot re-apply its
    outcome on top of the reset.
    """
    global _flue_circuit_open_until, _flue_circuit_generation
    with _breaker_lock:
        _flue_circuit_generation += 1
        _flue_circuit_open_until = 0.0


def _log_lane(
    *,
    attempted: str,
    served: str,
    outcome: str,
    session_id: str = "",
    reason: str = "",
) -> None:
    """ONE greppable line per turn: which lane was tried, which one answered.

    Ids only — never the message text. Deliberately unconditional (both flag
    states): "which brain answered that turn" is the question the #1613 runbook
    has to answer, and a log line that only exists when failover is enabled
    cannot answer it for the lane that is live today.
    """
    logger.info(
        "BRAIN_LANE lane_attempted=%s lane_served=%s outcome=%s reason=%s session=%s",
        attempted,
        served,
        outcome,
        reason or "-",
        (session_id or "-")[:64],
    )


def _fallback_lane() -> str:
    """The configured lane BELOW flue — the one a failover retries on."""
    return "core" if use_core_brain() else "legacy"


def _flue_outcome(sink: dict[str, str], *, served_any: bool) -> dict[str, str]:
    """The BRAIN_LANE outcome for a flue turn that RETURNED (no transport error).

    Read from the client's outcome sink rather than inferred, because "the
    generator finished" is indistinguishable from success: ``zoe_flue_client``
    renders an HTTP error, a read timeout, an empty 200, a rejected wire-2 body
    and a post-admission stream death all as its canned sentinel and ends the
    stream normally. Logging those as ``outcome=ok`` reported success for
    precisely the failed brain turns an operator greps this line to diagnose.

    LABELS ONLY. Nothing here is consulted before a dispatch decision — whether
    to fail over is still decided solely by ``FlueTransportError``, so a wrong
    label can misinform an operator but can never re-run a turn.

    A sink with no verdict means a client that does not report (a test double, an
    older module): fall back to the pre-existing label rather than inventing a
    failure.
    """
    outcome = (sink.get("outcome") or "").strip()
    if not outcome:
        return {"outcome": "ok" if served_any else "unknown", "reason": ""}
    return {"outcome": outcome, "reason": (sink.get("reason") or "").strip()[:160]}


# Kwargs this module OWNS and never accepts from a caller.
_INTERNAL_KWARGS = ("raise_transport_errors", "outcome_sink")


def _sanitize_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop dispatch-internal kwargs a caller may have forwarded blindly.

    ``raise_transport_errors`` belongs to the failover wrapper: it passes the
    value explicitly, so a caller-supplied copy riding in ``**kwargs`` is a
    ``TypeError: got multiple values for keyword argument`` — a turn that dies
    before reaching any brain. Worse on the flag-OFF path, where it would be
    forwarded verbatim and let ``FlueTransportError`` escape into a route that
    has nothing to catch it. No caller passes it today; this makes that a
    contract instead of a coincidence.

    ``outcome_sink`` is the same shape of hazard for the same reason: the
    wrapper passes its own dict explicitly, and a caller-supplied one would both
    collide and let an unrelated caller read/forge this turn's operator label.
    """
    if not any(key in kwargs for key in _INTERNAL_KWARGS):
        return kwargs
    logger.warning(
        "brain dispatch: ignoring caller-supplied internal kwarg(s) %s",
        [k for k in _INTERNAL_KWARGS if k in kwargs],
    )
    return {k: v for k, v in kwargs.items() if k not in _INTERNAL_KWARGS}


def _drop_flue_only_kwargs(kwargs: dict) -> None:
    """Strip FLUE-ONLY controls before dispatching to another brain lane.

    ``replay_isolation`` is honoured by the flue sidecar's tool executor, which is
    what performs the writes (see zoe_flue_client._wrap_message_with_replay). The
    other lanes take keyword-only params with no ``**kwargs``, so forwarding it
    would raise TypeError and turn every replay turn into an ERROR verdict.

    Dropped — but LOUDLY. A caller that asked for write isolation and did not get
    it must not find that out from a dirty database.
    """
    if kwargs.pop("replay_isolation", False):
        # FAIL CLOSED (review P1): a caller that asked for write isolation must
        # get a refused turn, never a committed write with a log line. The only
        # caller is the replay harness — an ERROR verdict on every turn is the
        # loud, correct signal that the gate ran against a non-flue lane.
        raise RuntimeError(
            "replay_isolation requested but this turn is not served by the flue "
            "lane — the sidecar write gate cannot engage, so the turn is refused "
            "rather than allowed to COMMIT brain-tool writes. Re-run the replay "
            "gate with the flue lane serving."
        )


def _fallback_streaming(message: str, session_id: str, user_id: str, **kwargs: Any) -> AsyncIterator[str]:
    _drop_flue_only_kwargs(kwargs)
    if use_core_brain():
        from zoe_core_client import run_zoe_core_streaming

        return run_zoe_core_streaming(message, session_id, user_id, **kwargs)
    from zoe_agent import run_zoe_agent_streaming

    return run_zoe_agent_streaming(message, session_id, user_id, **kwargs)


async def _fallback_oneshot(message: str, session_id: str, user_id: str, **kwargs: Any) -> str:
    _drop_flue_only_kwargs(kwargs)
    if use_core_brain():
        from zoe_core_client import run_zoe_core

        return await run_zoe_core(message, session_id, user_id, **kwargs)
    from zoe_agent import run_zoe_agent

    return await run_zoe_agent(message, session_id, user_id, **kwargs)


async def _flue_streaming_with_failover(
    message: str, session_id: str, user_id: str, **kwargs: Any
) -> AsyncIterator[str]:
    """Flue streaming turn with ONE bounded retry on the lane below it."""
    from zoe_flue_client import FlueTransportError, run_flue_brain_streaming

    other = _fallback_lane()
    kwargs = _sanitize_kwargs(kwargs)

    # EXACTLY ONE lane line per turn, however this generator unwinds. A streaming
    # consumer can vanish after any delta — a closed tab, a cancelled voice turn,
    # a route that stops iterating — which throws GeneratorExit at the `yield`
    # below and skips every statement after it. The success line used to live
    # there, so an interrupted turn produced NO BRAIN_LANE record at all, and
    # interrupted turns are ordinary runtime turns. BRAIN_LANE is the only
    # lane-attribution instrument the #1613 runbook has; a turn that leaves no
    # trace is an outage you cannot reconstruct.
    logged = False
    served_any = False

    def _emit(**fields: Any) -> None:
        nonlocal logged
        if logged:
            return
        logged = True
        _log_lane(session_id=session_id, **fields)

    try:
        skip_flue, generation = _observe_circuit()
        if skip_flue:
            _emit(
                attempted=other,
                served=other,
                outcome="dispatched",
                reason="flue_circuit_open",
            )
            async for delta in _fallback_streaming(message, session_id, user_id, **kwargs):
                yield delta
            return

        outcome_sink: dict[str, str] = {}
        try:
            async for delta in run_flue_brain_streaming(
                message,
                session_id,
                user_id,
                raise_transport_errors=True,
                outcome_sink=outcome_sink,
                **kwargs,
            ):
                served_any = True
                yield delta
        except FlueTransportError as exc:
            if served_any:
                # Defence in depth: the client contract already forbids raising
                # once text has gone out. If it ever did, the turn STILL must not
                # be replayed — the panel would speak twice. Surface, end, never
                # retry.
                _emit(
                    attempted="flue",
                    served="flue",
                    outcome="mid_stream_error",
                    reason=f"transport_after_first_token:{exc}"[:160],
                )
                return
            _open_circuit(generation)
            _emit(
                attempted="flue",
                served=other,
                outcome="failover",
                reason=f"flue_transport_error:{exc}"[:160],
            )
            async for delta in _fallback_streaming(message, session_id, user_id, **kwargs):
                yield delta
            return

        _close_circuit(generation)
        _emit(attempted="flue", served="flue", **_flue_outcome(outcome_sink, served_any=served_any))
    finally:
        # Cleanup pays the record the turn owes if no branch above emitted one —
        # i.e. the consumer walked away mid-stream. Only ever a log call: yielding
        # during GeneratorExit is illegal, and nothing here may alter dispatch.
        _emit(
            attempted="flue",
            served="flue" if served_any else "-",
            outcome="interrupted",
            reason="consumer_disconnected",
        )


async def _flue_oneshot_with_failover(
    message: str, session_id: str, user_id: str, **kwargs: Any
) -> str:
    """Flue one-shot turn with ONE bounded retry on the lane below it."""
    from zoe_flue_client import FlueTransportError, run_flue_brain

    other = _fallback_lane()
    kwargs = _sanitize_kwargs(kwargs)

    skip_flue, generation = _observe_circuit()
    if skip_flue:
        _log_lane(
            attempted=other,
            served=other,
            outcome="dispatched",
            session_id=session_id,
            reason="flue_circuit_open",
        )
        return await _fallback_oneshot(message, session_id, user_id, **kwargs)

    outcome_sink: dict[str, str] = {}
    try:
        text = await run_flue_brain(
            message,
            session_id,
            user_id,
            raise_transport_errors=True,
            outcome_sink=outcome_sink,
            **kwargs,
        )
    except FlueTransportError as exc:
        _open_circuit(generation)
        _log_lane(
            attempted="flue",
            served=other,
            outcome="failover",
            session_id=session_id,
            reason=f"flue_transport_error:{exc}"[:160],
        )
        return await _fallback_oneshot(message, session_id, user_id, **kwargs)

    _close_circuit(generation)
    _log_lane(
        attempted="flue",
        served="flue",
        session_id=session_id,
        **_flue_outcome(outcome_sink, served_any=bool(text)),
    )
    return text


def brain_streaming(message: str, session_id: str, user_id: str = "", **kwargs: Any) -> AsyncIterator[str]:
    """Streaming brain turn — Flue (opt-in) > zoe-core (default) > legacy.

    Configured lane selection; runtime failover only behind ``ZOE_BRAIN_FAILOVER``.
    """
    kwargs = _sanitize_kwargs(kwargs)
    if use_flue_brain():
        if failover_enabled():
            return _flue_streaming_with_failover(message, session_id, user_id, **kwargs)
        from zoe_flue_client import run_flue_brain_streaming

        _log_lane(attempted="flue", served="flue", outcome="dispatched", session_id=session_id)
        return run_flue_brain_streaming(message, session_id, user_id, **kwargs)
    lane = _fallback_lane()
    _log_lane(attempted=lane, served=lane, outcome="dispatched", session_id=session_id)
    return _fallback_streaming(message, session_id, user_id, **kwargs)


async def brain_oneshot(message: str, session_id: str, user_id: str = "", **kwargs: Any) -> str:
    """Non-streaming brain turn — Flue (opt-in) > zoe-core (default) > legacy.

    Configured lane selection; runtime failover only behind ``ZOE_BRAIN_FAILOVER``.
    """
    kwargs = _sanitize_kwargs(kwargs)
    if use_flue_brain():
        if failover_enabled():
            return await _flue_oneshot_with_failover(message, session_id, user_id, **kwargs)
        from zoe_flue_client import run_flue_brain

        _log_lane(attempted="flue", served="flue", outcome="dispatched", session_id=session_id)
        return await run_flue_brain(message, session_id, user_id, **kwargs)
    lane = _fallback_lane()
    _log_lane(attempted=lane, served=lane, outcome="dispatched", session_id=session_id)
    return await _fallback_oneshot(message, session_id, user_id, **kwargs)
