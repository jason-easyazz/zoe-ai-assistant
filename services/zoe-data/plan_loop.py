"""The bounded plan-act-verify loop: the executor core. Flag-dark ``ZOE_PLAN_LOOP`` = off (default) | shadow | on.

A request with several parts ("add floss to the shopping list, set a reminder for the dentist on Friday, then tell me what is on
Friday") is run as ONE frozen plan of typed steps. This module is the model-agnostic half: it never plans and never writes a
sentence. Its input is an ordered plan (tool name from the EXISTING registry - the household authority table's intents - plus
arguments plus the part of the speaker's words the step supports); its output is one outcome per step, each with a RECEIPT.

The guarantees, each pinned by a test with a negative control (``tests/test_plan_loop.py``):

  - GATE FIRST, ALL OR NOTHING. Before any tool runs, the whole plan is judged (``judge``): the safety floors on the speaker's
    words (distress, medical, and the child safety floor for who is speaking), the bounds, then every step against the
    household authority table for the speaker's role
    (child / guest / unverified voice / member) and the fridge rule. One step that is not allowed refuses the WHOLE plan with a
    reason from the closed list ``REFUSALS``; nothing has run.
  - BOUNDS. At most ``MAX_STEPS`` steps, ``MAX_SECONDS`` wall clock, ``STEP_TIMEOUT_S`` per step. A caller may tighten a bound,
    never widen it. The plan is frozen before the first tool runs: no replanning, so nothing a tool returns can add a step.
  - NO REPEATS. A plan with the same tool and arguments twice is refused (``repeated_step``); every step is sent at most once.
  - NO SELF-WIDENING. A step's arguments may not name who is acting, a role, a confirmation or a visibility
    (``widens_permissions``): identity and consent come only from the ``Speaker`` the caller built.
  - PROOF, NOT WORDS. A write is ``done`` only when ``write_receipts`` holds a receipt of the tool's kind stamped with THIS
    step's own turn id (a concurrent turn's write never vouches). A tool that says ok and leaves no receipt is ``uncertain``.
  - UNCERTAIN IS NEVER RETRIED AND NEVER CLAIMED. A write that timed out or raised after it may have been sent, with no receipt,
    is ``uncertain``. A read that did not come back is ``failed`` (nothing can have changed).
  - A proven write is re-recorded in the CALLER's turn (source ``plan_loop``), so the existing write-claim guard sees exactly
    the proven writes and nothing else when the loop's reply passes through it (``plan_loop_reply``).

  - WHAT IS JUDGED IS WHAT RUNS. The plan and the speaker are deep-copied into immutable structures the loop owns at intake
    (``freeze_plan`` / ``freeze_speaker``), BEFORE judging: one step's execution, a tool's mutation of its arguments, or the
    planner editing its own objects afterwards cannot change any step. Each step's content hash is recorded at judgement and
    re-checked just before it is sent (``not_as_judged`` otherwise); the tool gets its own shell of the judged content. The loop
    never substitutes one step's result into another's arguments: a step filled from a result is a NEW plan, judged again.

Every logged field is a closed code, a number, or a registered tool name (any other tool name logs as ``unregistered``): no user
id, no words, no argument values, no exception text. Record: docs/knowledge/plan-loop.md. Nothing in a live turn imports this
module yet (pinned by ``test_no_live_module_imports_the_plan_loop``).
"""
from __future__ import annotations

import asyncio
import builtins
import contextvars
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

import household_authority as authority
import write_receipts

logger = logging.getLogger(__name__)

MAX_STEPS = 6
MAX_SECONDS = 90.0
STEP_TIMEOUT_S = 20.0
RESULT_CHARS = 600

#: why a whole plan is refused (closed list; ``Refusal.reason`` is always one of these)
REFUSALS = ("safety_floor", "empty_plan", "too_many_steps", "repeated_step", "bad_arguments", "widens_permissions",
            "fridge_rule", "unknown_tool", "not_allowed", "needs_confirmation", "table_invalid")
#: what one step came to, and the closed list of codes that say why
STATUSES = ("done", "failed", "uncertain", "skipped")
CODES = {"done": ("receipt", "read_ok"),
         "failed": ("tool_said_no", "tool_refused", "read_timeout", "read_error"),
         "uncertain": ("timeout", "error_after_send", "no_receipt", "unprovable"),
         "skipped": ("time_budget", "dependency", "not_as_judged")}
MODES = ("off", "shadow", "on")
#: what a refusal, a log line or anything else this module emits says for a tool the authority table does not register
UNREGISTERED = "unregistered"
_MAX_DEPTH = 8

READ_CLASSES = frozenset({"read_public", "read_private"})
READ_TOOLS = frozenset({"list_show"})
#: fridge rule (owner, 2026-10-15): anyone at the panel edits ITEMS; nobody at the panel deletes, empties or renames a list
FRIDGE_RULE_TOOLS = frozenset({"list_delete", "list_clear", "list_rename"})
#: argument names that would let a step say who it acts for or how far its write is seen
FORBIDDEN_ARG_KEYS = frozenset({"user_id", "user", "as_user", "acting_user", "on_behalf_of", "owner", "role", "confirmed",
                                "confirm", "verified", "visibility", "shared", "share", "household", "panel", "at_panel",
                                "token", "internal", "speculative_turn_id"})
#: the ids a panel write is recorded under besides the speaker's own (``list_service``: the shared household viewer)
_RECEIPT_ALIASES = ("guest",)


@dataclass(frozen=True)
class Step:
    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    part: str = ""                    # the claim it supports: the speaker's own words for this part
    needs: tuple[int, ...] = ()       # indexes of earlier steps that must be ``done`` first


@dataclass(frozen=True)
class Speaker:
    user_id: str
    role: str                         # a ``household_authority`` role
    verified: bool = True             # voice is a password: an unverified member is judged as an unrecognised voice
    at_panel: bool = False
    confirmed: frozenset = frozenset()   # tools the speaker already said yes to (turns ask_first into allow, never ``never``)


@dataclass(frozen=True)
class Receipt:
    kind: str                         # a ``write_receipts`` kind, or "read"
    row_ids: tuple[str, ...] = ()
    count: int = 0


@dataclass(frozen=True)
class StepOutcome:
    step: Step
    action_class: str
    status: str                       # STATUSES
    code: str                         # CODES[status]
    receipt: Optional[Receipt] = None
    result: str = ""                  # what a read returned (bounded); empty for a write
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class Refusal:
    reason: str                       # REFUSALS
    step_index: int = -1              # -1 = the plan as a whole
    tool: str = ""
    action_class: str = ""
    role: str = ""


@dataclass(frozen=True)
class PlanResult:
    status: str                       # off | refused | shadow | ran
    outcomes: tuple[StepOutcome, ...] = ()
    refusal: Optional[Refusal] = None
    elapsed_s: float = 0.0

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)


class StepRejected(Exception):
    """An executor raises this when the tool DEFINITELY did not run (a validation verdict). Anything else it raises may have
    happened after the write was sent."""


Executor = Callable[[Step], Awaitable[Mapping[str, Any]]]
Floors = Callable[[str], str]
ChildFloor = Callable[[str, "Speaker", str], str]


def mode() -> str:
    try:
        from typed_env import flag

        m = str(flag("ZOE_PLAN_LOOP"))
        return m if m in MODES else "off"
    except Exception:  # noqa: BLE001 - registry unavailable: off (fail dark)
        return "off"


def _err(exc: BaseException) -> str:
    """An exception for a log line: a builtin class name, else "other" (a caller's class name is not a closed set)."""
    return type(exc).__name__ if getattr(builtins, type(exc).__name__, None) is type(exc) else "other"


class _Unfreezable:
    """Stands in for a value that cannot be copied into an immutable structure; ``judge`` refuses it (``bad_arguments``)."""

    def __repr__(self) -> str:
        return "<unfreezable>"


_UNFREEZABLE = _Unfreezable()


def _freeze(v: Any, depth: int = 0) -> Any:
    """A deep, immutable copy the loop owns: exact str / int / float / bool / None, tuple for list or tuple, frozenset for a set,
    a read-only mapping for a mapping. A scalar subclass is copied to its base type (no custom truth or equality survives)."""
    if depth > _MAX_DEPTH:
        return _UNFREEZABLE
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return int.__int__(v)
    if isinstance(v, float):
        return float.__float__(v)
    if isinstance(v, str):
        return str.__str__(v)
    try:
        if isinstance(v, Mapping):
            items = list(v.items())
            if not all(isinstance(k, str) for k, _ in items):
                return _UNFREEZABLE
            return MappingProxyType({str.__str__(k): _freeze(x, depth + 1) for k, x in items})
        if isinstance(v, (list, tuple)):
            return tuple(_freeze(x, depth + 1) for x in v)
        if isinstance(v, (set, frozenset)):
            return frozenset(_freeze(x, depth + 1) for x in v)
    except Exception:  # noqa: BLE001 - an unhashable or misbehaving container: refused, never half-copied
        return _UNFREEZABLE
    return _UNFREEZABLE


def freeze_args(args: Any) -> Mapping[str, Any]:
    out = _freeze(args)
    return out if isinstance(out, Mapping) else _UNFREEZABLE  # type: ignore[return-value]


def freeze_step(step: Any) -> "Step":
    """The loop's own immutable copy of one planned step (read ONCE from the planner's object)."""
    tool, part = getattr(step, "tool", ""), getattr(step, "part", "")
    try:
        needs = tuple(int.__int__(n) if isinstance(n, int) else -1 for n in (getattr(step, "needs", ()) or ()))
    except Exception:  # noqa: BLE001
        needs = (-1,)
    return Step(str.__str__(tool).strip() if isinstance(tool, str) else "", freeze_args(getattr(step, "args", None)),
                str.__str__(part) if isinstance(part, str) else "", needs)


def freeze_plan(plan: Sequence[Any]) -> tuple["Step", ...]:
    return tuple(freeze_step(s) for s in plan)


def freeze_speaker(speaker: Any) -> "Speaker":
    """The loop's own copy of who is speaking, read once: everything judged and everything run uses this copy."""
    uid, role = getattr(speaker, "user_id", ""), getattr(speaker, "role", "")
    try:
        confirmed = frozenset(str.__str__(t) for t in (getattr(speaker, "confirmed", ()) or ()) if isinstance(t, str))
    except Exception:  # noqa: BLE001
        confirmed = frozenset()
    return Speaker(str.__str__(uid) if isinstance(uid, str) else "", str.__str__(role) if isinstance(role, str) else "",
                   bool(getattr(speaker, "verified", False)), bool(getattr(speaker, "at_panel", True)), confirmed)


def _canon(v: Any) -> Any:
    if isinstance(v, Mapping):
        return ("m", tuple(sorted((repr(k), _canon(x)) for k, x in v.items())))
    if isinstance(v, (list, tuple)):
        return (type(v).__name__, tuple(_canon(x) for x in v))
    if isinstance(v, (set, frozenset)):
        return (type(v).__name__, tuple(sorted(repr(_canon(x)) for x in v)))
    return ("v", type(v).__name__, repr(v))


def digest(step: "Step") -> str:
    """Content hash of one step as judged; re-checked just before the step is sent."""
    body = repr((step.tool, _canon(step.args), step.part, _canon(step.needs)))
    return hashlib.sha256(body.encode("utf-8", "backslashreplace")).hexdigest()


#: the distress + medical checks together; longer than the child floor's budget because the first call on a cold process compiles
#: their lexicons. Over it the answer is not trusted (``floor_timeout``): a sync check cannot be interrupted, only disbelieved.
FLOOR_BUDGET_S = 5.0


def safety_floor(utterance: str) -> str:
    """Which floor owns this turn ("" = none). The distress and medical floors answer such a turn with fixed text before any
    brain call; a plan is never run on one. A floor that cannot be asked refuses (fail closed): the STRICT entry points raise when
    a check could not run, where ``detect`` would answer "nothing found" for its single-turn callers."""
    began = _floor_clock()
    try:
        import distress_handoff
        import medical_safety

        d, m = distress_handoff.detect_strict(utterance), medical_safety.detect_strict(utterance)
        if type(d) is not distress_handoff.Verdict or d.tier not in distress_handoff._RANK \
                or type(m) is not medical_safety.Verdict or not isinstance(m.cls, str):
            return "floor_unknown"                 # a verdict that is not well formed is not "nothing found"
        if _floor_clock() - began > FLOOR_BUDGET_S:
            return "floor_timeout"
        if d.tier != "none":
            return "distress"
        if m.cls:
            return "medical"
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("PLAN_LOOP floor check failed (%s): refusing", _err(exc))
        return "floor_error"


#: a child floor check slower than this cannot be trusted to have run: it is treated as "cannot tell"
CHILD_FLOOR_BUDGET_S = 1.0
_floor_clock = time.monotonic
#: what ``child_floor`` returns ("" = the floor explicitly said this turn is safe for this speaker); anything else refuses the plan
CHILD_FLOOR_CODES = ("", "child", "child_floor_blind", "child_floor_error", "child_floor_off", "child_floor_timeout",
                     "child_floor_unknown")


def _floor_who(speaker: "Speaker") -> str:
    """The speaker in the floor's own terms (``child_safety_floor._who``'s answers), from the loop's frozen ``Speaker`` with no
    lookup: a child account is a child (verified or not: the floor refuses that id too); a verified owner or adult is an adult; an
    UNVERIFIED owner or adult voice is a guest (owner 2026-11-16: "you cant tell its a child and not an adult, so it's let
    through"); a guest or unrecognised voice is a guest only on an id the floor itself treats as a guest (``manner_block.is_guest``,
    as ``_who``), otherwise ``unverified`` like an account ``_who`` cannot place; a role in neither set is ``unverified``. The
    floor refuses ``unverified`` and ``child``."""
    if speaker.role not in authority.ROLES:
        return "unverified"
    if speaker.role == "child":
        return "child"
    if speaker.role in ("owner", "adult"):
        return "adult" if speaker.verified else "guest"
    try:
        import manner_block

        return "guest" if not speaker.user_id.strip() or manner_block.is_guest(speaker.user_id) else "unverified"
    except Exception as exc:  # noqa: BLE001 - cannot tell it is a guest id: the floor's "cannot place" answer
        logger.warning("PLAN_LOOP guest id check failed (%s)", _err(exc))
        return "unverified"


def child_floor(utterance: str, speaker: "Speaker", session_id: str = "") -> str:
    """"" only when the child safety floor EXPLICITLY clears this turn for this speaker; otherwise a code from
    ``CHILD_FLOOR_CODES``. Asked READ-ONLY (``dry``: it records no age statement here; ``brain_dispatch`` does that on the real
    turn). Explicitly safe = ``check`` returned None (no hazard ask), or a well-formed, NOT blind ``Pending`` that is not
    declared, for a speaker the FLOOR'S OWN role rule lets through (``child_safety_floor.source_for``, the rule ``resolve`` uses:
    an adult or a guest passes, a child or an account it cannot place does not; owner 2026-11-16: an unrecognised or unverified
    voice is a guest: "you cant tell its a child and not an adult, so it's let through"). FAIL CLOSED, for everyone: a blind
    result (the floor's own detection failed: it cannot see a child declared earlier in the session), a raise, a check slower
    than ``CHILD_FLOOR_BUDGET_S``, or any unexpected return refuses the WHOLE plan. Refusing only hands the turn back to
    the ordinary single-turn path, where the floor itself answers. A floor switched OFF (``ZOE_CHILD_SAFETY_FLOOR=off``) did not
    run, and a floor that did not run is not a verdict: while it is off the loop plans for NOBODY (``child_floor_off``) and
    every turn goes to the single-turn path."""
    began = _floor_clock()
    try:
        import child_safety_floor

        off = child_safety_floor.mode() == "off"
        pending = child_safety_floor.check(utterance, session_id, speaker.user_id, dry=True)
        off = off or child_safety_floor.mode() == "off"
        slow = _floor_clock() - began > CHILD_FLOOR_BUDGET_S
        if off:
            code = "child_floor_off"
        elif slow:
            code = "child_floor_timeout"
        elif pending is None:
            code = ""
        elif type(pending) is not child_safety_floor.Pending or not isinstance(pending.blind, bool) \
                or not isinstance(pending.declared, bool) or not isinstance(pending.cls, str) \
                or not isinstance(pending.reply, str) or not isinstance(pending.key, tuple) \
                or len(pending.key) != 2 or not all(isinstance(k, str) for k in pending.key):
            code = "child_floor_unknown"
        elif pending.blind:
            code = "child_floor_blind"
        else:
            code = "child" if child_safety_floor.source_for(pending, _floor_who(speaker)) else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("PLAN_LOOP child floor check failed (%s)", _err(exc))
        code = "child_floor_error"
    if code not in ("", "child"):
        logger.warning("PLAN_LOOP child floor cannot tell code=%s: refusing the plan", code)
    return code


def effective_role(speaker: Speaker) -> str:
    """The role the plan is judged as: unknown roles and unverified members are an unrecognised voice."""
    if speaker.role not in authority.ROLES:
        return "unrecognised_voice"
    if not speaker.verified and speaker.role not in authority.NO_ACCOUNT_ROLES:
        return "unrecognised_voice"
    return speaker.role


def signature(step: Step) -> str:
    try:
        return step.tool + "|" + json.dumps(dict(step.args or {}), sort_keys=True, default=str).casefold()
    except Exception:  # noqa: BLE001
        return step.tool + "|" + repr(sorted((step.args or {}).items()))


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _args_problem(args: Any) -> str:
    if not isinstance(args, Mapping):
        return "bad_arguments"
    for k, v in args.items():
        if not isinstance(k, str):
            return "bad_arguments"
        if k.strip().casefold() in FORBIDDEN_ARG_KEYS:
            return "widens_permissions"
        if not (_scalar(v) or (isinstance(v, (list, tuple)) and all(_scalar(x) for x in v))):
            return "bad_arguments"
    return ""


def judge(plan: Sequence[Step], speaker: Speaker, *, utterance: str = "", floors: Floors = safety_floor,
          child: ChildFloor = child_floor, session_id: str = "",
          max_steps: int = MAX_STEPS) -> tuple[Optional[Refusal], tuple[str, ...]]:
    """Pure gate over the WHOLE plan: (refusal or None, the action class of every step). Runs no tool. It judges its own
    frozen copy (``freeze_plan``; a plan ``run`` already froze copies to identical content). A refusal names a tool only when
    the table registers it (``UNREGISTERED`` otherwise), so the planner's text never travels in a refusal or a log line."""
    plan, speaker = freeze_plan(plan), freeze_speaker(speaker)
    role = effective_role(speaker)
    if floors(utterance or "") or child(utterance or "", speaker, session_id):
        return Refusal("safety_floor", role=role), ()
    if authority.table() is None:
        return Refusal("table_invalid", role=role), ()
    if not plan:
        return Refusal("empty_plan", role=role), ()
    if len(plan) > max(0, min(int(max_steps), MAX_STEPS)):
        return Refusal("too_many_steps", role=role), ()
    known = authority.table().intents
    seen: set[str] = set()
    classes: list[str] = []
    for i, step in enumerate(plan):
        tool = step.tool                                   # stripped, exact str: freeze_step
        name = tool if tool in known or tool in FRIDGE_RULE_TOOLS else UNREGISTERED
        problem = _args_problem(step.args)
        if not problem and any((not isinstance(n, int)) or n < 0 or n >= i for n in step.needs):
            problem = "bad_arguments"
        if problem:
            return Refusal(problem, i, name, role=role), ()
        if tool in FRIDGE_RULE_TOOLS and speaker.at_panel:
            return Refusal("fridge_rule", i, name, "irreversible_other", role), ()
        if tool not in known:
            return Refusal("unknown_tool", i, UNREGISTERED, authority.UNKNOWN_CLASS, role), ()
        sig = signature(step)
        if sig in seen:
            return Refusal("repeated_step", i, tool, role=role), ()
        seen.add(sig)
        decision = authority.decide(authority.classify_intent(tool, step.args), role, confirmed=tool in speaker.confirmed)
        if decision.outcome == "refuse":
            return Refusal("not_allowed", i, tool, decision.action_class, role), ()
        if decision.outcome == "ask":
            return Refusal("needs_confirmation", i, tool, decision.action_class, role), ()
        classes.append(decision.action_class)
    return None, tuple(classes)


def is_read(tool: str, action_class: str) -> bool:
    return action_class in READ_CLASSES or tool in READ_TOOLS


async def _bounded(aw, timeout_s: float):
    """Await for at most ``timeout_s``. On expiry the task is cancelled and NOT awaited, so a tool that swallows cancellation
    cannot hold the loop past its budget."""
    task = asyncio.ensure_future(aw)
    try:
        done, _ = await asyncio.wait({task}, timeout=max(timeout_s, 0.001))
    except BaseException:                      # the CALLER was cancelled mid-wait: the tool must not go on to write
        task.cancel()
        task.add_done_callback(_consume)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_consume)
        raise asyncio.TimeoutError
    return task.result()


def _consume(task: "asyncio.Future") -> None:
    """Retrieve a cancelled tool task's outcome so it is never reported as an unretrieved exception."""
    if not task.cancelled():
        task.exception()


def _status_code(exc: BaseException) -> int:
    """An executor exception's HTTP-style status, 0 when it has none or it is not a number (never raises)."""
    try:
        return int(getattr(exc, "status_code", 0) or 0)
    except Exception:  # noqa: BLE001 - "UNKNOWN", None-like objects, a raising property: an ordinary error for this step
        return 0


def proof(user_id: str, turn: "write_receipts.TurnStart", kind: str) -> Optional[Receipt]:
    """The receipt of exactly ``kind`` written inside THIS step's turn, or None. An unstamped receipt (a write from some other
    request of the same member) and a queued capture are not proof of this step."""
    if not kind:
        return None
    rows: list[str] = []
    for uid in dict.fromkeys((user_id,) + _RECEIPT_ALIASES):
        for r in write_receipts.since(uid, turn, turn=turn.turn):
            if r.kind == kind and r.turn == turn.turn and r.source != "pending_capture":
                rows.append(r.row_id)
    return Receipt(kind, tuple(rows), len(rows)) if rows else None


async def _run_step(step: Step, action_class: str, kind: str, speaker: Speaker, execute: Executor, budget_s: float,
                    clock: Callable[[], float]) -> StepOutcome:
    """Send one allowed step ONCE, in its own receipt turn, and decide its status from the evidence. ``step``, ``action_class``
    and ``kind`` are the judged values; the tool gets its own shell of the same immutable content, so nothing it does to the
    object it was handed reaches the loop's record."""
    began = clock()
    ctx = contextvars.copy_context()
    turn = ctx.run(write_receipts.begin)
    raised, ok, text = "", False, ""
    try:
        sent = Step(step.tool, step.args, step.part, step.needs)
        result = await _bounded(ctx.run(asyncio.ensure_future, execute(sent)), budget_s)
        ok = bool((result or {}).get("ok"))
        text = str((result or {}).get("result") or "")[:RESULT_CHARS]
    except asyncio.TimeoutError:
        raised = "timeout"
    except StepRejected:
        raised = "rejected"
    except Exception as exc:  # noqa: BLE001
        raised = "rejected" if 400 <= _status_code(exc) < 500 else "error"
        logger.info("PLAN_LOOP step raised tool=%s (%s)", step.tool, _err(exc))   # a judged step: a registered tool

    def out(status: str, code: str, receipt: Optional[Receipt] = None, result_text: str = "") -> StepOutcome:
        return StepOutcome(step, action_class, status, code, receipt, result_text, clock() - began)

    if is_read(step.tool, action_class):
        if raised:
            return out("failed", "read_timeout" if raised == "timeout" else "read_error")
        if ok and text.strip():
            return out("done", "read_ok", Receipt("read", (), 1), text)
        return out("failed", "tool_said_no")
    receipt = proof(speaker.user_id, turn, kind)
    if receipt is not None:                      # the row is the truth, whatever the call itself did afterwards
        for row_id in receipt.row_ids:           # the caller's turn (and so the write-claim guard) sees the proven write
            write_receipts.record(speaker.user_id, kind, row_id, "plan_loop")
        return out("done", "receipt", receipt)
    if raised == "rejected":
        return out("failed", "tool_refused")
    if raised:
        return out("uncertain", "timeout" if raised == "timeout" else "error_after_send")
    if not ok:
        return out("failed", "tool_said_no")
    return out("uncertain", "no_receipt" if kind else "unprovable")


def _log(result: PlanResult, m: str, steps: int) -> None:
    r = result.refusal
    logger.info("PLAN_LOOP mode=%s status=%s steps=%d done=%d failed=%d uncertain=%d skipped=%d refusal=%s tool=%s class=%s "
                "role=%s ms=%.0f", m, result.status, steps, result.count("done"), result.count("failed"),
                result.count("uncertain"), result.count("skipped"), r.reason if r else "-", (r.tool if r else "") or "-",
                (r.action_class if r else "") or "-", (r.role if r else "") or "-", result.elapsed_s * 1000.0)


async def run(plan: Sequence[Step], speaker: Speaker, *, utterance: str, execute: Executor, floors: Floors = safety_floor,
              child: ChildFloor = child_floor, session_id: str = "",
              max_steps: int = MAX_STEPS, max_seconds: float = MAX_SECONDS, step_timeout_s: float = STEP_TIMEOUT_S,
              clock: Callable[[], float] = time.monotonic) -> PlanResult:
    """Judge the whole plan, then (``on`` only) run each step once and prove it. Never raises for a tool failure."""
    m = mode()
    if m == "off":
        return PlanResult("off")
    plan, speaker = freeze_plan(plan), freeze_speaker(speaker)    # intake: the loop's own copy, BEFORE judging
    judged = tuple(digest(s) for s in plan)
    started = clock()
    max_seconds = max(0.0, min(float(max_seconds), MAX_SECONDS))
    step_timeout_s = max(0.001, min(float(step_timeout_s), STEP_TIMEOUT_S))
    refusal, classes = judge(plan, speaker, utterance=utterance, floors=floors, child=child, session_id=session_id,
                             max_steps=max_steps)
    if refusal is not None or m != "on":
        result = PlanResult("refused" if refusal else "shadow", (), refusal, clock() - started)
        _log(result, m, len(plan))
        return result
    kinds = tuple(authority.receipt_kind(s.tool) for s in plan)  # pinned with the verdict, before the first tool runs
    outcomes: list[StepOutcome] = []
    for i, step in enumerate(plan):
        remaining = max_seconds - (clock() - started)
        if digest(step) != judged[i]:
            outcomes.append(StepOutcome(step, classes[i], "skipped", "not_as_judged"))
        elif remaining <= 0:
            outcomes.append(StepOutcome(step, classes[i], "skipped", "time_budget"))
        elif any(outcomes[n].status != "done" for n in step.needs):
            outcomes.append(StepOutcome(step, classes[i], "skipped", "dependency"))
        else:
            outcomes.append(await _run_step(step, classes[i], kinds[i], speaker, execute, min(step_timeout_s, remaining),
                                            clock))
    result = PlanResult("ran", tuple(outcomes), None, clock() - started)
    _log(result, m, len(plan))
    return result
