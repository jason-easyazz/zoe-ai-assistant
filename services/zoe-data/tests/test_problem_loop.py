"""The bounded problem-solving loop (problem_loop.py) - 6 steps / 90 s, escalation card, code-checked success.

Synthetic household only: ids ``member-a`` / ``member-b``, neutral items. The executor is a fake that records write receipts
the way the real writers do (``write_receipts.record``), so "done" here means what it means live: a receipt exists.

Negative controls (by hand when written, each went red): making ``_run_step`` count an ok result as done without a receipt fails
``test_ok_without_receipt_is_not_done`` and ``test_receipt_for_someone_else_does_not_count``; deleting the ``seen`` check fails
``test_repeat_escalates``; deleting the step-budget check fails ``test_step_budget``; letting ``confirmed`` lift a ``never`` in
``household_authority.decide`` fails ``test_never_is_not_lifted_by_a_yes``.
"""
from __future__ import annotations

import asyncio
import itertools

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic; no DB, no sidecar, no network

import household_authority as ha
import problem_loop as pl
import write_receipts as wr
from problem_loop import Step

USER = "member-a"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("ZOE_PROBLEM_LOOP", "on")
    ha.reset_cache()
    wr.reset()
    yield
    wr.reset()


def planner(steps):
    """Plan = a fixed list of steps, then None (the planner believes the goal is met)."""
    it = iter(steps)
    return lambda history: next(it, None)


class Exec:
    """Fake fulfilment: ``list_add``/``reminder_create``/... record a receipt for ``who`` (default the acting member)."""

    def __init__(self, *, receipts=True, who=USER, ok=True, delay=0.0):
        self.calls, self.receipts, self.who, self.ok, self.delay = [], receipts, who, ok, delay

    async def __call__(self, step):
        self.calls.append(step.intent)
        if self.delay:
            await asyncio.sleep(self.delay)
        kind = ha.receipt_kind(step.intent)
        if self.receipts and kind and self.ok:
            wr.record(self.who, kind, f"row-{len(self.calls)}", "fake")
        return {"ok": self.ok, "result": "fine" if self.ok else ""}


def run(steps, *, role="adult", ex=None, **kw):
    ex = ex or Exec()
    out = asyncio.run(pl.run("a household task", user_id=USER, role=role, plan_next=planner(steps), execute=ex, **kw))
    return out, ex


def test_dark_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_PROBLEM_LOOP", raising=False)
    called = []
    out = asyncio.run(pl.run("g", user_id=USER, role="owner", plan_next=lambda h: called.append(1), execute=Exec()))
    assert out.status == "off" and called == []
    monkeypatch.setenv("ZOE_PROBLEM_LOOP", "shadow")            # shadow is the dispatch log, not the loop
    assert asyncio.run(pl.run("g", user_id=USER, role="owner", plan_next=lambda h: called.append(1), execute=Exec())).status == "off"


def test_writes_proven_by_receipts_are_done():
    out, ex = run([Step("list_add", {"item": "flour"}), Step("list_add", {"item": "eggs"}), Step("reminder_create", {"title": "t"})])
    assert out.status == "done" and out.card is None
    assert [r.proof for r in out.steps] == ["receipt:list", "receipt:list", "receipt:reminder"]
    assert ex.calls == ["list_add", "list_add", "reminder_create"]


def test_ok_without_receipt_is_not_done():
    out, _ = run([Step("list_add", {"item": "flour"}, why="add flour")], ex=Exec(receipts=False))
    assert out.status == "escalated" and out.card.reason == "unverified"
    assert out.steps[0].status == "unverified"
    assert "add flour" in out.card.need


def test_receipt_for_someone_else_does_not_count():
    out, _ = run([Step("list_add", {"item": "flour"})], ex=Exec(who="member-b"))
    assert out.status == "escalated" and out.steps[0].status == "unverified"


def test_receipt_from_another_turn_does_not_count():
    class OtherTurn(Exec):
        async def __call__(self, step):
            self.calls.append(step.intent)
            import contextvars

            ctx = contextvars.copy_context()
            ctx.run(wr.begin)                                  # a concurrent turn of the same member writes
            ctx.run(wr.record, USER, "list", "row-x", "other-turn")
            return {"ok": True}

    out, _ = run([Step("list_add", {"item": "flour"})], ex=OtherTurn())
    assert out.steps[0].status == "unverified"


def test_write_with_no_receipt_kind_is_a_claim_not_proof():
    out, _ = run([Step("music_play", {"query": "something calm"})])
    assert out.steps[0].status == "unverified" and out.card.reason == "unverified"


def test_a_light_switch_is_proved_by_its_device_receipt():
    """Since 2026-11-07 a switch the hub confirmed records a ``device`` receipt (smart_home_service.record_device_receipt)."""
    out, _ = run([Step("smart_home", {"action": "on", "room": "kitchen"})])
    assert out.steps[0].status == "done" and out.steps[0].proof == "receipt:device"
    out, _ = run([Step("smart_home", {"action": "on", "room": "kitchen"})], ex=Exec(receipts=False))
    assert out.steps[0].status == "unverified"


def test_read_ok_is_done_and_read_failure_is_failed():
    out, _ = run([Step("weather", {})])
    assert out.status == "done" and out.steps[0].proof == "read_ok"
    out, _ = run([Step("weather", {})], ex=Exec(ok=False))
    assert out.status == "escalated" and out.steps[0].status == "failed"


def test_step_budget():
    counter = itertools.count()
    ex = Exec()
    out = asyncio.run(pl.run("g", user_id=USER, role="adult", execute=ex,
                             plan_next=lambda h: Step("weather", {"n": next(counter)})))
    assert out.status == "escalated" and out.card.reason == "step_budget"
    assert len(ex.calls) == pl.MAX_STEPS == 6


def test_time_budget_by_clock():
    t = itertools.count(0, 20)                             # every clock read is 20 s later
    counter = itertools.count()
    ex = Exec()
    out = asyncio.run(pl.run("g", user_id=USER, role="adult", execute=ex, clock=lambda: float(next(t)),
                             plan_next=lambda h: Step("weather", {"n": next(counter)})))
    assert out.card.reason == "time_budget"
    assert len(ex.calls) < pl.MAX_STEPS
    assert pl.MAX_SECONDS == 90.0


def test_slow_step_is_cut_at_the_budget():
    out, ex = run([Step("weather", {})], ex=Exec(delay=1.0), max_seconds=0.05)
    assert out.status == "escalated"
    assert out.steps[0].status == "failed"
    assert out.elapsed_s < 0.9


def test_caller_cannot_widen_the_step_budget():
    counter = itertools.count()
    ex = Exec()
    out = asyncio.run(pl.run("g", user_id=USER, role="adult", execute=ex, max_steps=50,
                             plan_next=lambda h: Step("weather", {"n": next(counter)})))
    assert out.card.reason == "step_budget" and len(ex.calls) == 6


def test_caller_cannot_widen_the_time_budget():
    t = itertools.count(0, 50)                             # 50 s per clock read: a 1000 s budget would allow many steps
    counter = itertools.count()
    ex = Exec()
    out = asyncio.run(pl.run("g", user_id=USER, role="adult", execute=ex, max_seconds=1000.0, clock=lambda: float(next(t)),
                             plan_next=lambda h: Step("weather", {"n": next(counter)})))
    assert out.card.reason == "time_budget" and len(ex.calls) <= 1


def test_a_failed_write_after_a_good_read_is_not_done():
    class FailWrites(Exec):
        async def __call__(self, step):
            self.calls.append(step.intent)
            return {"ok": step.intent == "calendar_show"}

    out, _ = run([Step("calendar_show", {}), Step("reminder_create", {"title": "t"}, why="set the reminder")], ex=FailWrites())
    assert out.status == "escalated" and out.card.reason == "failed"
    assert "set the reminder" in out.card.need


def test_a_step_that_ignores_cancellation_cannot_outlive_the_budget():
    import time

    async def stubborn(step):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            await asyncio.sleep(0.6)                       # suppresses + stalls on cancellation
        return {"ok": True}

    async def main():
        t0 = time.monotonic()
        out = await pl.run("g", user_id=USER, role="adult", plan_next=planner([Step("weather", {})]), execute=stubborn,
                           max_seconds=0.1)
        return out, time.monotonic() - t0

    out, took = asyncio.run(main())
    assert out.status == "escalated" and out.steps[0].status == "failed"
    assert took < 0.5


def test_repeat_escalates():
    ex = Exec()
    out = asyncio.run(pl.run("g", user_id=USER, role="adult", execute=ex,
                             plan_next=lambda h: Step("weather", {"city": "x"})))
    assert out.card.reason == "repeat" and ex.calls == ["weather"]


def test_ask_first_stops_before_running_and_a_yes_lets_it_run():
    out, ex = run([Step("memory_retire", {"which": 1}, why="forget that note")], role="owner")
    assert out.card.reason == "ask_first" and ex.calls == []
    assert "a yes before I forget that note" in out.card.need and out.card.to == "member"
    out, ex = run([Step("memory_retire", {"which": 1})], role="owner", confirmed=frozenset({"memory_retire"}))
    assert ex.calls == ["memory_retire"]


def test_never_is_not_lifted_by_a_yes():
    out, ex = run([Step("memory_retire", {"which": 1})], role="child", confirmed=frozenset({"memory_retire"}))
    assert out.card.reason == "never" and ex.calls == [] and out.card.to == "guardian"
    out, ex = run([Step("smart_home", {"action": "on"})], role="guest", confirmed=frozenset({"smart_home"}))
    assert out.card.reason == "never" and ex.calls == []


def test_unknown_tool_asks():
    out, ex = run([Step("send_message", {"to": "x"}, why="send the message")], role="owner")
    assert out.card.reason == "ask_first" and ex.calls == []


def test_card_says_what_was_tried_and_what_is_needed():
    out, _ = run([Step("calendar_show", {}, why="check the calendar"),
                  Step("memory_retire", {}, why="forget the old note")], role="adult")
    text = out.card.text()
    assert text.startswith("Here's what I tried: check the calendar (done); forget the old note (stopped).")
    assert "Here's what I need from you: a yes before I forget the old note." in text


def test_planner_failure_is_a_card_not_a_crash():
    def bad(_h):
        raise RuntimeError("x")

    out = asyncio.run(pl.run("g", user_id=USER, role="adult", plan_next=bad, execute=Exec()))
    assert out.status == "escalated" and out.card.reason == "planner_failed"


# Lane F E7 shape: five synthetic everyday tasks; success = 0 unauthorised actions and >= 70% completion.
E7_TASKS = [
    ("reschedule a reminder", "adult", [Step("reminder_list", {}), Step("reminder_create", {"title": "t", "due": "fri"})]),
    ("list from a recipe", "adult", [Step("list_add", {"item": i}) for i in ("flour", "eggs", "butter")]),
    ("find a free slot", "owner", [Step("calendar_show", {"day": "sat"})]),
    ("draft a message to a contact", "owner", [Step("people_search", {"q": "x"}), Step("send_message", {"to": "x"})]),
    ("kitchen timer from a guest", "guest", [Step("timer_create", {"minutes": 10})]),
]


def test_e7_synthetic_household_tasks():
    done = unauthorised = 0
    for _goal, role, steps in E7_TASKS:
        out, ex = run(steps, role=role)
        for intent in ex.calls:
            if ha.decide(ha.classify_intent(intent), role).outcome != "allow":
                unauthorised += 1
        done += out.status == "done"
        if out.status == "escalated":
            assert out.card.reason == "ask_first"           # the message is the one that needs a yes
    assert unauthorised == 0
    assert done / len(E7_TASKS) >= 0.7
