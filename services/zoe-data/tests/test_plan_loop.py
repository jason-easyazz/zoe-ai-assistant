"""The bounded plan-act-verify loop, executor core (plan_loop.py), flag-dark ZOE_PLAN_LOOP.

Each guarantee has a negative control IN this file: the same scenario is re-run with the guard under test replaced by a
permissive stand-in (``monkeypatch``), and the bad outcome the guard exists to stop must then appear. So a test here cannot pass
because the scenario was harmless:
  - no receipt check (``proof`` always vouches)        -> the lying tool's write is reported ``done``  (a false claim)
  - no gate (``judge`` allows everything)              -> the child's forbidden step RUNS
  - no repeat rule / no floor                          -> covered by the gate control: the refused plan executes
"""
from __future__ import annotations

import asyncio
import logging

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic with scripted tool fakes; no DB, no network, no model

import household_authority as ha
import plan_loop as pl
import write_receipts

ADULT = pl.Speaker("member-a", "adult", at_panel=True)
CHILD = pl.Speaker("member-c", "child", at_panel=True)
GUEST = pl.Speaker("guest", "guest", at_panel=True)
NO_FLOOR = lambda text: ""  # noqa: E731

ADD = pl.Step("list_add", {"item": "floss", "list_type": "shopping"}, "add floss to the shopping list")
REMIND = pl.Step("reminder_create", {"title": "dentist", "date": "friday"}, "set a reminder for the dentist on friday")
SHOW = pl.Step("calendar_show", {"qualifier": "friday"}, "tell me what is on friday")
FORGET = pl.Step("memory_retire", {"pick": 1}, "forget that")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("ZOE_PLAN_LOOP", "on")
    ha.reset_cache()
    write_receipts.reset()
    yield
    write_receipts.reset()


class Tools:
    """Scripted tool fakes. ``faults`` maps a tool to: fail | lie | hang | write_then_hang | error | reject."""

    def __init__(self, faults=None, user="member-a"):
        self.faults, self.user, self.calls, self.rows = dict(faults or {}), user, [], []

    async def __call__(self, step: pl.Step):
        self.calls.append(step.tool)
        fault = self.faults.get(step.tool, "")
        if fault == "fail":
            return {"ok": False, "result": ""}
        if fault == "lie":
            return {"ok": True, "result": "Added."}
        if fault == "hang":
            await asyncio.sleep(5)
        if fault == "error":
            raise RuntimeError("connection reset")
        if fault == "reject":
            raise pl.StepRejected("not a date")
        kind = ha.receipt_kind(step.tool)
        if kind:
            self.rows.append(step.tool)
            write_receipts.record(self.user, kind, f"row-{len(self.rows)}", "fake")
        if fault == "write_then_hang":
            await asyncio.sleep(5)
        return {"ok": True, "result": "nothing on that day" if step.tool.endswith("_show") else "ok"}


def run(plan, speaker=ADULT, tools=None, utterance="add floss and set a reminder", **kw):
    tools = tools if tools is not None else Tools(user=speaker.user_id)
    kw.setdefault("floors", NO_FLOOR)
    kw.setdefault("step_timeout_s", 0.05)
    return asyncio.run(pl.run(plan, speaker, utterance=utterance, execute=tools, **kw)), tools


def codes(result):
    return [(o.step.tool, o.status, o.code) for o in result.outcomes]


# ── the happy path and the receipt rule ──────────────────────────────────────────────────────────────────────────────────────

def test_three_parts_each_proven():
    result, tools = run([ADD, REMIND, SHOW])
    assert result.status == "ran" and tools.calls == ["list_add", "reminder_create", "calendar_show"]
    assert codes(result) == [("list_add", "done", "receipt"), ("reminder_create", "done", "receipt"),
                             ("calendar_show", "done", "read_ok")]
    assert result.outcomes[0].receipt == pl.Receipt("list", ("row-1",), 1)
    assert result.outcomes[2].result == "nothing on that day"


def test_a_tool_that_says_ok_but_wrote_nothing_is_uncertain_never_done():
    result, _ = run([ADD, REMIND], tools=Tools({"list_add": "lie"}))
    assert codes(result) == [("list_add", "uncertain", "no_receipt"), ("reminder_create", "done", "receipt")]


def test_negative_control_without_the_receipt_check_the_lie_becomes_a_false_claim(monkeypatch):
    monkeypatch.setattr(pl, "proof", lambda user_id, turn, kind: pl.Receipt(kind, ("forged",), 1) if kind else None)
    result, tools = run([ADD], tools=Tools({"list_add": "lie"}))
    assert codes(result) == [("list_add", "done", "receipt")] and tools.rows == []      # claimed done, nothing written


def test_another_turns_receipt_does_not_vouch_for_this_step():
    write_receipts.record("member-a", "list", "someone-else", "other_turn")             # unstamped: a concurrent tool call
    result, _ = run([ADD], tools=Tools({"list_add": "lie"}))
    assert codes(result) == [("list_add", "uncertain", "no_receipt")]


def test_a_proven_write_is_visible_to_the_callers_turn_and_an_unproven_one_is_not():
    async def go():
        started = write_receipts.begin()
        await pl.run([ADD, REMIND], ADULT, utterance="x", execute=Tools({"reminder_create": "lie"}), floors=NO_FLOOR)
        return started

    started = asyncio.run(go())
    assert write_receipts.has("member-a", started, "list") and not write_receipts.has("member-a", started, "reminder")


def test_a_write_with_no_receipt_kind_cannot_be_proven():
    result, _ = run([pl.Step("journal_create", {"content": "a good day"}, "write that in my journal")])
    assert codes(result) == [("journal_create", "uncertain", "unprovable")]


# ── failure, timeout, uncertainty ────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fault, status, code", [
    ("fail", "failed", "tool_said_no"), ("reject", "failed", "tool_refused"), ("hang", "uncertain", "timeout"),
    ("error", "uncertain", "error_after_send"), ("write_then_hang", "done", "receipt")])
def test_write_outcomes(fault, status, code):
    result, tools = run([ADD, REMIND], tools=Tools({"list_add": fault}))
    assert codes(result)[0] == ("list_add", status, code)
    assert codes(result)[1] == ("reminder_create", "done", "receipt")                   # one bad part never stops the others
    assert tools.calls.count("list_add") == 1                                            # sent once, never retried


@pytest.mark.parametrize("fault, code", [("hang", "read_timeout"), ("error", "read_error"), ("fail", "tool_said_no")])
def test_a_read_that_did_not_come_back_is_failed_not_uncertain(fault, code):
    result, _ = run([SHOW], tools=Tools({"calendar_show": fault}))
    assert codes(result) == [("calendar_show", "failed", code)]


def test_a_step_that_needs_a_failed_step_is_skipped():
    after = pl.Step("calendar_show", {"qualifier": "friday"}, "then read friday", needs=(0,))
    result, tools = run([REMIND, after], tools=Tools({"reminder_create": "fail"}))
    assert codes(result)[1] == ("calendar_show", "skipped", "dependency") and tools.calls == ["reminder_create"]


def test_wall_clock_budget_skips_what_is_left():
    ticks = iter(range(0, 1000, 40))                                                     # every clock read is 40 s later
    result, tools = run([ADD, REMIND, SHOW], clock=lambda: float(next(ticks)))
    assert [o.status for o in result.outcomes] == ["done", "skipped", "skipped"] and tools.calls == ["list_add"]
    assert result.outcomes[1].code == "time_budget"


def test_a_caller_may_tighten_a_bound_never_widen_it():
    plan = [pl.Step("list_add", {"item": f"thing {i}"}, f"add thing {i}") for i in range(pl.MAX_STEPS + 1)]
    result, tools = run(plan, max_steps=99, max_seconds=9999)
    assert result.refusal.reason == "too_many_steps" and tools.calls == []
    assert run(plan[:3], max_steps=2)[0].refusal.reason == "too_many_steps"


# ── the gate: whole plan or nothing ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_childs_forbidden_part_refuses_the_whole_plan_and_nothing_runs():
    result, tools = run([ADD, FORGET], speaker=CHILD)
    assert result.status == "refused" and tools.calls == []
    assert result.refusal == pl.Refusal("not_allowed", 1, "memory_retire", "memory_forget", "child")


def test_negative_control_without_the_gate_the_childs_part_runs(monkeypatch):
    monkeypatch.setattr(pl, "judge", lambda plan, speaker, **kw: (None, tuple("x" for _ in plan)))
    result, tools = run([ADD, FORGET], speaker=CHILD)
    assert "memory_retire" in tools.calls and result.status == "ran"


@pytest.mark.parametrize("speaker, plan, reason", [
    (GUEST, [ADD, REMIND], "not_allowed"),                                               # a guest never makes a personal write
    (pl.Speaker("member-a", "adult", verified=False), [ADD, SHOW], "not_allowed"),       # voice is a password
    (pl.Speaker("member-a", "wizard"), [SHOW], "not_allowed"),                           # an unknown role is nobody
    (ADULT, [ADD, FORGET], "needs_confirmation"),                                        # forget is ask-first for an adult
    (CHILD, [pl.Step("memory_store", {"text": "i like blue"}, "remember i like blue")], "needs_confirmation"),
    (ADULT, [ADD, pl.Step("list_delete", {"list_type": "shopping"}, "delete the shopping list")], "fridge_rule"),
    (ADULT, [ADD, pl.Step("send_money", {"amount": 5}, "send five dollars")], "unknown_tool"),
    (ADULT, [ADD, ADD], "repeated_step"),
    (ADULT, [ADD, pl.Step("list_add", {"item": "FLOSS", "list_type": "shopping"}, "again")], "repeated_step"),
    (CHILD, [pl.Step("memory_store", {"text": "x", "confirmed": True}, "x")], "widens_permissions"),
    (GUEST, [pl.Step("list_add", {"item": "milk", "user_id": "member-a"}, "x")], "widens_permissions"),
    (ADULT, [pl.Step("list_add", {"item": "milk", "Visibility": "family"}, "x")], "widens_permissions"),
    (ADULT, [pl.Step("list_add", {"item": {"nested": "x"}}, "x")], "bad_arguments"),
    (ADULT, [pl.Step("list_add", {"item": "milk"}, "x", needs=(0,))], "bad_arguments"),  # a step cannot need itself
    (ADULT, [], "empty_plan"),
])
def test_refusals(speaker, plan, reason):
    result, tools = run(plan, speaker=speaker)
    assert result.status == "refused" and result.refusal.reason == reason and tools.calls == []
    assert reason in pl.REFUSALS


def test_a_confirmation_comes_from_the_speaker_and_never_overrides_never():
    yes = pl.Speaker("member-a", "adult", confirmed=frozenset({"memory_retire"}))
    assert run([FORGET], speaker=yes)[0].status == "ran"
    child_yes = pl.Speaker("member-c", "child", confirmed=frozenset({"memory_retire"}))
    assert run([FORGET], speaker=child_yes)[0].refusal.reason == "not_allowed"


def test_guest_at_the_panel_may_edit_the_household_list():
    result, _ = run([ADD, pl.Step("list_add", {"item": "milk"}, "and milk")], speaker=GUEST, tools=Tools(user="guest"))
    assert [o.status for o in result.outcomes] == ["done", "done"]


def test_safety_floors_own_the_turn():
    result, tools = run([ADD, REMIND], floors=lambda text: "medical")
    assert result.refusal.reason == "safety_floor" and tools.calls == []
    # the real floors, on words they are built to catch
    assert pl.safety_floor("add milk to the list and i think i swallowed a button battery") == "medical"
    assert pl.safety_floor("add floss to the shopping list and remind me about the dentist") == ""


def test_a_floor_that_cannot_be_asked_refuses(monkeypatch):
    import distress_handoff

    monkeypatch.setattr(distress_handoff, "detect", lambda text: 1 / 0)
    assert pl.safety_floor("add milk") == "floor_error"


def test_an_invalid_table_refuses_everything(monkeypatch):
    monkeypatch.setattr(ha, "table", lambda: None)
    assert run([SHOW])[0].refusal.reason == "table_invalid"


# ── the flag ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_off_touches_nothing(monkeypatch):
    monkeypatch.delenv("ZOE_PLAN_LOOP")
    floors_called = []
    result, tools = run([ADD], floors=lambda t: floors_called.append(t) or "")
    assert result == pl.PlanResult("off") and tools.calls == [] and floors_called == []


def test_shadow_judges_logs_codes_only_and_runs_nothing(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_PLAN_LOOP", "shadow")
    with caplog.at_level(logging.INFO, logger="plan_loop"):
        ok, tools = run([ADD, REMIND])
        refused, _ = run([ADD, FORGET], speaker=CHILD, tools=tools)
    assert ok.status == "shadow" and refused.refusal.reason == "not_allowed" and tools.calls == []
    text = caplog.text
    assert "status=shadow steps=2" in text and "refusal=not_allowed tool=memory_retire class=memory_forget role=child" in text
    for private in ("floss", "dentist", "member-a", "member-c"):
        assert private not in text


def test_every_code_is_from_the_closed_lists():
    seen = set()
    for fault in ("", "fail", "lie", "hang", "error", "reject", "write_then_hang"):
        result, _ = run([ADD, SHOW], tools=Tools({"list_add": fault, "calendar_show": fault}))
        seen |= {(o.status, o.code) for o in result.outcomes}
    assert seen and all(status in pl.STATUSES and code in pl.CODES[status] for status, code in seen)
