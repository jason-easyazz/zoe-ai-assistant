"""The bounded household problem-solving loop (Samantha initiative item 17, lane F E7) - flag-dark ``ZOE_PROBLEM_LOOP``.

A multi-step request ("move my dentist reminder to Friday and add floss to the shopping list") runs as at most
``MAX_STEPS`` tool steps inside ``MAX_SECONDS``. Each step is judged by the household authority table BEFORE it runs, and
counts as done only when CODE proves it (a ``write_receipts`` receipt for a write, an ok result for a read) - never because
the model said so. The loop stops and hands the member an ``EscalationCard`` ("here's what I tried, here's what I need from
you") when:

  - the step or time budget is spent (``step_budget`` / ``time_budget``);
  - the next step is ``ask_first`` for this role and was not confirmed (``ask_first``) or is ``never`` (``never``);
  - the planner repeats the same tool with the same arguments (``repeat``);
  - the planner says it is finished but a write could not be proven (``unverified``).

The loop is a library: the caller supplies ``plan_next(history)`` (returns the next ``Step`` or None when it believes the goal
is met) and ``execute(step)`` (runs one intent, returns ``{"ok": bool, "result": ...}``; in production this is the same
fulfilment ``/api/system/intent-dispatch`` runs). Nothing in the live turn path calls it yet; ``ZOE_PROBLEM_LOOP`` other than
``on`` makes ``run`` return ``status="off"`` before touching either callable.

Log lines carry intent names, classes, verdicts and reason codes only - no user id, goal text or slot values.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional, Union

import household_authority as authority
import write_receipts

logger = logging.getLogger(__name__)

MAX_STEPS = 6
MAX_SECONDS = 90.0

READ_CLASSES = frozenset({"read_public", "read_private"})
READ_INTENTS = frozenset({"list_show"})


@dataclass(frozen=True)
class Step:
    intent: str
    slots: Mapping[str, Any] = field(default_factory=dict)
    why: str = ""             # one human line ("check the calendar"), shown on the card


@dataclass(frozen=True)
class StepRecord:
    step: Step
    action_class: str
    verdict: str              # authority table cell
    status: str               # done | unverified | failed | blocked
    proof: str = ""           # receipt:<kind> | read_ok | "" (nothing proved it)


@dataclass(frozen=True)
class EscalationCard:
    goal: str
    reason: str               # step_budget | time_budget | ask_first | never | repeat | unverified
    tried: tuple[str, ...]
    need: str
    to: str = "member"        # member | guardian | owner

    def text(self) -> str:
        tried = "; ".join(self.tried) if self.tried else "nothing yet"
        return f"Here's what I tried: {tried}. Here's what I need from you: {self.need}"


@dataclass(frozen=True)
class LoopOutcome:
    status: str               # done | escalated | off
    steps: tuple[StepRecord, ...] = ()
    card: Optional[EscalationCard] = None
    elapsed_s: float = 0.0


Planner = Callable[[tuple[StepRecord, ...]], Union[Optional[Step], Awaitable[Optional[Step]]]]
Executor = Callable[[Step], Awaitable[Mapping[str, Any]]]


def enabled() -> bool:
    return authority.mode() == "on"


def _signature(step: Step) -> str:
    try:
        return step.intent + "|" + json.dumps(dict(step.slots or {}), sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return step.intent + "|" + repr(sorted((step.slots or {}).items()))


def _label(step: Step) -> str:
    return step.why.strip() or step.intent.replace("_", " ")


def _tried_lines(records: list[StepRecord]) -> tuple[str, ...]:
    words = {"done": "done", "unverified": "could not confirm", "failed": "did not work", "blocked": "stopped"}
    return tuple(f"{_label(r.step)} ({words.get(r.status, r.status)})" for r in records)


def _is_read(step: Step, action_class: str) -> bool:
    return action_class in READ_CLASSES or step.intent in READ_INTENTS


async def _call_planner(plan_next: Planner, history: tuple[StepRecord, ...]) -> Optional[Step]:
    out = plan_next(history)
    if inspect.isawaitable(out):
        out = await out
    return out


async def run(goal: str, *, user_id: str, role: str, plan_next: Planner, execute: Executor,
              confirmed: frozenset = frozenset(), max_steps: int = MAX_STEPS, max_seconds: float = MAX_SECONDS,
              clock: Callable[[], float] = time.monotonic) -> LoopOutcome:
    """Run the loop for one goal. ``confirmed`` = intents the member already said yes to (turns ``ask_first`` into allow for
    those; ``never`` stays never). Never raises for a planner/executor failure: that is a ``failed`` step or a card."""
    if not enabled():
        return LoopOutcome("off")
    started = clock()
    records: list[StepRecord] = []
    seen: set[str] = set()

    def remaining() -> float:
        return max_seconds - (clock() - started)

    def escalate(reason: str, need: str, to: str = "member") -> LoopOutcome:
        card = EscalationCard(goal=goal, reason=reason, tried=_tried_lines(records), need=need, to=to)
        logger.info("PROBLEM_LOOP escalated reason=%s steps=%d to=%s", reason, len(records), to)
        return LoopOutcome("escalated", tuple(records), card, clock() - started)

    while True:
        if len(records) >= max_steps:
            return escalate("step_budget", "this is taking more steps than I should take on my own - shall I keep going, "
                                           "or would you like to finish it?")
        if remaining() <= 0:
            return escalate("time_budget", "this is taking longer than I should take on my own - shall I keep going?")
        try:
            step = await asyncio.wait_for(_call_planner(plan_next, tuple(records)), timeout=remaining())
        except asyncio.TimeoutError:
            return escalate("time_budget", "this is taking longer than I should take on my own - shall I keep going?")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PROBLEM_LOOP planner failed (%s)", type(exc).__name__)
            return escalate("planner_failed", "I lost track of the plan - could you tell me the next thing to do?")

        if step is None:                                    # the planner believes the goal is met; code decides
            unproven = [r for r in records if r.status == "unverified"]
            if unproven:
                return escalate("unverified", "a quick check that " + ", ".join(_label(r.step) for r in unproven)
                                + " really happened - I couldn't confirm it.")
            failed = [r for r in records if r.status == "failed"]
            if failed and not any(r.status == "done" for r in records):
                return escalate("unverified", "another way to do this - nothing I tried worked.")
            logger.info("PROBLEM_LOOP done steps=%d", len(records))
            return LoopOutcome("done", tuple(records), None, clock() - started)

        sig = _signature(step)
        cls = authority.classify_intent(step.intent, step.slots)
        decision = authority.decide(cls, role, confirmed=step.intent in confirmed)
        if sig in seen:
            records.append(StepRecord(step, decision.action_class, decision.verdict, "blocked"))
            return escalate("repeat", "a hand - I tried the same thing twice and it isn't getting anywhere.")
        seen.add(sig)
        if decision.outcome == "refuse":
            records.append(StepRecord(step, decision.action_class, decision.verdict, "blocked"))
            to = "owner" if decision.action_class == authority.SELF_MODIFY_CLASS else (
                "guardian" if role == "child" else "member")
            return escalate("never", f"someone who can {_label(step)} - that isn't something I can do here.", to)
        if decision.outcome == "ask":
            records.append(StepRecord(step, decision.action_class, decision.verdict, "blocked"))
            return escalate("ask_first", f"a yes before I {_label(step)}.", "guardian" if role == "child" else "member")

        records.append(await _run_step(step, decision, user_id, execute, remaining()))


async def _run_step(step: Step, decision: authority.Decision, user_id: str, execute: Executor,
                    budget_s: float) -> StepRecord:
    """Execute one allowed step in its own receipt turn and prove it by code."""
    ctx = contextvars.copy_context()
    turn = ctx.run(write_receipts.begin)          # receipts this step's task writes are stamped with ITS turn
    try:
        task = ctx.run(asyncio.ensure_future, execute(step))
        result = await asyncio.wait_for(task, timeout=max(budget_s, 0.001))
    except asyncio.TimeoutError:
        return StepRecord(step, decision.action_class, decision.verdict, "failed", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PROBLEM_LOOP step failed intent=%s (%s)", step.intent, type(exc).__name__)
        return StepRecord(step, decision.action_class, decision.verdict, "failed", "")
    ok = bool((result or {}).get("ok"))
    kind = authority.receipt_kind(step.intent)
    if kind:
        if write_receipts.has(user_id, turn, kind):
            return StepRecord(step, decision.action_class, decision.verdict, "done", f"receipt:{kind}")
        return StepRecord(step, decision.action_class, decision.verdict, "unverified" if ok else "failed", "")
    if _is_read(step, decision.action_class):
        return StepRecord(step, decision.action_class, decision.verdict, "done" if ok else "failed", "read_ok" if ok else "")
    # A write with no receipt kind (a light, music, a memory note): an ok result is a claim, not proof.
    return StepRecord(step, decision.action_class, decision.verdict, "unverified" if ok else "failed", "")
