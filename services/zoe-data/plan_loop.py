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

Logs carry tool names, classes, codes and counts only: no user id, no words, no argument values.
Record: docs/knowledge/plan-loop.md. Nothing in a live turn imports this module yet.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from dataclasses import dataclass, field
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
         "skipped": ("time_budget", "dependency")}

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

        return str(flag("ZOE_PLAN_LOOP"))
    except Exception:  # noqa: BLE001 - registry unavailable: off (fail dark)
        return "off"


def safety_floor(utterance: str) -> str:
    """Which floor owns this turn ("" = none). The distress and medical floors answer such a turn with fixed text before any
    brain call; a plan is never run on one. A floor that cannot be asked refuses (fail closed)."""
    try:
        import distress_handoff
        import medical_safety

        if distress_handoff.detect(utterance).tier != "none":
            return "distress"
        if medical_safety.detect(utterance).cls:
            return "medical"
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("PLAN_LOOP floor check failed (%s): refusing", type(exc).__name__)
        return "floor_error"


def child_floor(utterance: str, speaker: "Speaker", session_id: str = "") -> str:
    """"child" when the child safety floor would answer this turn ("" = it would not). Asked READ-ONLY (``dry``: it records no
    age statement here; ``brain_dispatch`` does that on the real turn). A hazard request passes only for a speaker who is
    positively an adult and has not said in this session that they are a child; a floor that cannot be asked refuses the same
    way (fail safe). Refusing here only hands the turn back, where the floor itself answers."""
    adult = effective_role(speaker) in ("owner", "adult")
    try:
        import child_safety_floor

        pending = child_safety_floor.check(utterance, session_id, speaker.user_id, dry=True)
        if pending is None:
            return ""
        return "child" if pending.declared or not adult else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("PLAN_LOOP child floor check failed (%s)", type(exc).__name__)
        return "" if adult else "child_floor_error"


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
    """Pure gate over the WHOLE plan: (refusal or None, the action class of every step). Runs no tool."""
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
        tool = (step.tool or "").strip()
        problem = _args_problem(step.args)
        if not problem and any((not isinstance(n, int)) or n < 0 or n >= i for n in step.needs):
            problem = "bad_arguments"
        if problem:
            return Refusal(problem, i, tool, role=role), ()
        if tool in FRIDGE_RULE_TOOLS and speaker.at_panel:
            return Refusal("fridge_rule", i, tool, "irreversible_other", role), ()
        if tool not in known:
            return Refusal("unknown_tool", i, tool, authority.UNKNOWN_CLASS, role), ()
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
    done, _ = await asyncio.wait({task}, timeout=max(timeout_s, 0.001))
    if not done:
        task.cancel()
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        raise asyncio.TimeoutError
    return task.result()


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


async def _run_step(step: Step, action_class: str, speaker: Speaker, execute: Executor, budget_s: float,
                    clock: Callable[[], float]) -> StepOutcome:
    """Send one allowed step ONCE, in its own receipt turn, and decide its status from the evidence."""
    began = clock()
    ctx = contextvars.copy_context()
    turn = ctx.run(write_receipts.begin)
    raised, ok, text = "", False, ""
    try:
        result = await _bounded(ctx.run(asyncio.ensure_future, execute(step)), budget_s)
        ok = bool((result or {}).get("ok"))
        text = str((result or {}).get("result") or "")[:RESULT_CHARS]
    except asyncio.TimeoutError:
        raised = "timeout"
    except StepRejected:
        raised = "rejected"
    except Exception as exc:  # noqa: BLE001
        raised = "rejected" if 400 <= int(getattr(exc, "status_code", 0) or 0) < 500 else "error"
        logger.info("PLAN_LOOP step raised tool=%s (%s)", step.tool, type(exc).__name__)

    def out(status: str, code: str, receipt: Optional[Receipt] = None, result_text: str = "") -> StepOutcome:
        return StepOutcome(step, action_class, status, code, receipt, result_text, clock() - began)

    if is_read(step.tool, action_class):
        if raised:
            return out("failed", "read_timeout" if raised == "timeout" else "read_error")
        if ok and text.strip():
            return out("done", "read_ok", Receipt("read", (), 1), text)
        return out("failed", "tool_said_no")
    kind = authority.receipt_kind(step.tool)
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
    plan = tuple(plan)
    started = clock()
    max_seconds = max(0.0, min(float(max_seconds), MAX_SECONDS))
    step_timeout_s = max(0.001, min(float(step_timeout_s), STEP_TIMEOUT_S))
    refusal, classes = judge(plan, speaker, utterance=utterance, floors=floors, child=child, session_id=session_id,
                             max_steps=max_steps)
    if refusal is not None or m != "on":
        result = PlanResult("refused" if refusal else "shadow", (), refusal, clock() - started)
        _log(result, m, len(plan))
        return result
    outcomes: list[StepOutcome] = []
    for i, step in enumerate(plan):
        remaining = max_seconds - (clock() - started)
        if remaining <= 0:
            outcomes.append(StepOutcome(step, classes[i], "skipped", "time_budget"))
        elif any(outcomes[n].status != "done" for n in step.needs):
            outcomes.append(StepOutcome(step, classes[i], "skipped", "dependency"))
        else:
            outcomes.append(await _run_step(step, classes[i], speaker, execute, min(step_timeout_s, remaining), clock))
    result = PlanResult("ran", tuple(outcomes), None, clock() - started)
    _log(result, m, len(plan))
    return result
