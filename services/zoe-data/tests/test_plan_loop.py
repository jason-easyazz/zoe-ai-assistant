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


# ── the child safety floor (child_safety_floor.py, on main since #2480) ──────────────────────────────────────────────────────

HAZARD = "add milk to the shopping list and how do i use the gas stove"
HAZARD_PLAN = [pl.Step("list_add", {"item": "milk"}, "add milk to the shopping list"),
               pl.Step("note_search", {"query": "gas stove"}, "how do i use the gas stove")]


def test_a_childs_hazard_ask_refuses_the_whole_plan_and_an_adults_does_not():
    result, tools = run(HAZARD_PLAN, speaker=CHILD, utterance=HAZARD)
    assert result.refusal.reason == "safety_floor" and tools.calls == []
    for speaker in (GUEST, pl.Speaker("member-a", "adult", verified=False)):        # not positively an adult: same answer
        assert run(HAZARD_PLAN, speaker=speaker, utterance=HAZARD)[0].refusal.reason == "safety_floor"
    assert run(HAZARD_PLAN, utterance=HAZARD)[0].status == "ran"
    assert pl.child_floor("add milk to the shopping list and remind me about the dentist", CHILD) == ""


def test_negative_control_without_the_child_floor_the_childs_hazard_plan_runs():
    result, tools = run(HAZARD_PLAN, speaker=CHILD, utterance=HAZARD, child=lambda text, speaker, session_id: "")
    assert result.status == "ran" and tools.calls == ["list_add", "note_search"]


def test_a_speaker_who_said_they_are_a_child_this_session_is_refused_whatever_the_account_says():
    import child_safety_floor

    child_safety_floor._SESSIONS.clear()
    assert child_safety_floor.check("i am 10 years old", "s1", "member-a") is None   # the real turn records the statement
    assert pl.child_floor(HAZARD, ADULT, "s1") == "child" and pl.child_floor(HAZARD, ADULT, "s2") == ""
    result, tools = run(HAZARD_PLAN, utterance=HAZARD, session_id="s1")
    assert result.refusal.reason == "safety_floor" and tools.calls == []
    # the loop's own check is read-only: a statement seen only by it is NOT recorded
    assert pl.child_floor("i am 10 years old and " + HAZARD, ADULT, "s3") == "child"
    assert ("member-a", "s3") not in child_safety_floor._SESSIONS
    child_safety_floor._SESSIONS.clear()


def test_a_child_floor_that_cannot_be_asked_lets_only_an_adult_through(monkeypatch):
    import child_safety_floor

    monkeypatch.setattr(child_safety_floor, "check", lambda *a, **k: 1 / 0)
    assert pl.child_floor(HAZARD, CHILD) == "child_floor_error" and pl.child_floor(HAZARD, GUEST) == "child_floor_error"
    assert pl.child_floor(HAZARD, ADULT) == ""


# ── what is judged is exactly what runs (review 2026-11-16, child safety) ────────────────────────────────────────────────────

class Recorder(Tools):
    """Tools that also record the CLASS of exactly what each call received, and can run a side effect after each call."""

    def __init__(self, after=None, user="member-c", **kw):
        super().__init__(user=user, **kw)
        self.after, self.executed = after or (lambda step: None), []

    async def __call__(self, step: pl.Step):
        self.executed.append((step.tool, ha.classify_intent(step.tool, step.args), pl.signature(step)))
        out = await super().__call__(step)
        try:
            self.after(step)
        except Exception:  # noqa: BLE001 - a tool's own failed mutation is not this test's subject
            pass
        return out


def judged_vs_executed(result, tools):
    return [o.action_class for o in result.outcomes if o.status != "skipped"], [c for _, c, _ in tools.executed]


def test_reviewer_case_an_earlier_steps_write_into_a_shared_list_cannot_make_the_childs_later_step_shared():
    shared = []                                                                 # the planner reused one list for both steps
    plan = [pl.Step("calendar_create", {"title": "swim", "date": "friday", "attendees": shared}, "swim on friday"),
            pl.Step("calendar_create", {"title": "piano", "date": "monday", "attendees": shared}, "piano on monday")]
    tools = Recorder(after=lambda step: shared.append("member-b"))
    result, _ = run(plan, speaker=CHILD, tools=tools)
    judged, executed = judged_vs_executed(result, tools)
    assert result.status == "ran" and judged == ["personal_write", "personal_write"]
    assert executed == judged and "calendar_shared" not in executed               # run exactly as judged, never as shared


def test_a_tool_that_mutates_its_own_arguments_changes_nothing_that_was_judged():
    def mutate(step):
        step.args["attendees"].append("member-b")                              # immutable now: raises inside the tool
    def rebind(step):
        object.__setattr__(step, "args", {"title": "x", "attendees": ["member-b"]})   # rebinding the tool's own copy
    for after in (mutate, rebind):
        plan = [pl.Step("calendar_create", {"title": "swim", "attendees": []}, "swim"),
                pl.Step("reminder_create", {"title": "piano", "date": "monday"}, "piano")]
        tools = Recorder(after=after)
        result, _ = run(plan, speaker=CHILD, tools=tools)
        assert judged_vs_executed(result, tools) == (["personal_write", "personal_write"],) * 2
        assert dict(result.outcomes[0].step.args) == {"title": "swim", "attendees": ()}   # the record is what was judged


def test_a_planner_object_mutated_after_intake_changes_nothing():
    raw = [pl.Step("list_add", {"item": "milk", "tags": []}, "add milk"),
           pl.Step("calendar_create", {"title": "piano", "attendees": []}, "piano")]

    def planner_edits(step):
        raw[1].args["attendees"].append("member-b")                            # the planner's own objects, after intake
        raw[1].args["title"] = "changed"
        object.__setattr__(raw[1], "tool", "memory_retire")
        raw.append(pl.Step("memory_retire", {"pick": 1}, "forget"))
    tools = Recorder(after=planner_edits)
    result, _ = run(raw, speaker=CHILD, tools=tools)
    assert [t for t, _, _ in tools.executed] == ["list_add", "calendar_create"]
    assert judged_vs_executed(result, tools) == (["household_list", "personal_write"],) * 2
    assert dict(result.outcomes[1].step.args) == {"title": "piano", "attendees": ()}


def test_a_later_step_filled_from_an_earlier_result_runs_as_judged_and_the_filled_plan_is_judged_afresh():
    later_args = {"title": "piano", "date": "monday"}

    def fill_from_result(step):
        if step.tool == "people_search":
            later_args["attendees"] = ["member-b"]                             # "invite whoever step 1 found"
    plan = [pl.Step("people_search", {"query": "piano teacher"}, "who teaches piano"),
            pl.Step("calendar_create", later_args, "book piano", needs=(0,))]
    tools = Recorder(after=fill_from_result)
    result, _ = run(plan, speaker=CHILD, tools=tools)
    assert [c for _, c, _ in tools.executed][1] == "personal_write" == result.outcomes[1].action_class
    # the loop never substitutes: the filled step reaches a tool only as a NEW plan, and that one is judged again
    refiled, tools2 = run([pl.Step("calendar_create", later_args, "book piano")], speaker=CHILD)
    assert refiled.refusal == pl.Refusal("not_allowed", 0, "calendar_create", "calendar_shared", "child") and not tools2.calls


def test_nested_dict_list_tuple_set_arguments_are_deep_frozen_and_owned():
    nested = {"a": [1, {"b": [2, 3]}], "c": (4, [5]), "d": {6, 7}, "e": frozenset({8})}
    step = pl.freeze_step(pl.Step("list_add", {"item": "milk", "meta": nested, "tags": ["x", "y"], "set": {"z"}}, "add"))
    nested["a"][1]["b"].append(99)
    nested["c"][1].append(99)
    nested["d"].add(99)
    frozen = step.args["meta"]
    assert frozen["a"][1]["b"] == (2, 3) and frozen["c"] == (4, (5,)) and frozen["d"] == frozenset({6, 7})
    assert step.args["tags"] == ("x", "y") and step.args["set"] == frozenset({"z"})
    with pytest.raises(TypeError):
        step.args["item"] = "other"
    with pytest.raises(TypeError):
        frozen["a"][1]["b"] = ()
    class Tricky(str):
        pass
    t = pl.freeze_step(pl.Step(Tricky(" list_add "), {Tricky("item"): Tricky("milk")}, "add", needs=(True, "x")))
    assert type(t.tool) is str and t.tool == "list_add" and all(type(k) is str and type(v) is str for k, v in t.args.items())
    assert t.needs == (1, -1)
    for args in ({"item": {"nested": "x"}}, {"item": ["a", ["b"]]}, {"item": {"a", "b"}}, {"item": object()}):
        result, tools = run([pl.Step("list_add", args, "x")])
        assert result.refusal.reason == "bad_arguments" and tools.calls == []      # still refused: freezing widens nothing


def test_a_step_changed_after_judgement_is_not_sent(monkeypatch):
    held = {}
    real = pl.freeze_plan
    monkeypatch.setattr(pl, "freeze_plan", lambda plan: held.setdefault("plan", real(plan)))

    def tamper(step):                                                         # reaching the loop's OWN record of step 2
        object.__setattr__(held["plan"][1], "args", pl.freeze_args({"title": "piano", "attendees": ["member-b"]}))
    plan = [pl.Step("reminder_create", {"title": "swim"}, "swim"), pl.Step("calendar_create", {"title": "piano"}, "piano")]
    tools = Recorder(after=tamper)
    result, _ = run(plan, speaker=CHILD, tools=tools)
    assert codes(result)[1] == ("calendar_create", "skipped", "not_as_judged") and len(tools.executed) == 1
    # negative control: with the content check gone, the tampered (shared) step reaches the tool
    held.clear()
    monkeypatch.setattr(pl, "digest", lambda step: "same")
    tools = Recorder(after=tamper)
    run(plan, speaker=CHILD, tools=tools)
    assert [c for _, c, _ in tools.executed] == ["personal_write", "calendar_shared"]


def test_property_the_class_judged_is_the_class_executed_for_random_plans_with_shared_objects():
    import random

    rng = random.Random(20261116)
    for _ in range(150):
        pool = [[] for _ in range(2)]
        tools_ = ["calendar_create", "reminder_create", "list_add", "calendar_show", "people_search"]
        plan = []
        for i in range(rng.randint(1, pl.MAX_STEPS)):
            tool = rng.choice(tools_)
            args = {"title": f"t{i}"} if tool != "list_add" else {"item": f"i{i}"}
            if tool == "calendar_create" and rng.random() < 0.8:
                args["attendees"] = rng.choice(pool)
            if rng.random() < 0.3:
                args["tags"] = rng.choice(pool)
            plan.append(pl.Step(tool, args, f"part {i}"))

        def chaos(step, pool=pool, plan=plan):
            rng.choice(pool).append(f"m{rng.randint(0, 9)}")
            for s in rng.sample(plan, k=1):
                try:
                    s.args["attendees"] = rng.choice(pool) + ["x"]
                except TypeError:
                    pass
            try:
                step.args["attendees"].append("y")
            except (AttributeError, KeyError, TypeError):
                pass
        speaker = rng.choice([CHILD, pl.Speaker("member-a", "adult", confirmed=frozenset({"calendar_create"}))])
        tools = Recorder(after=chaos, user=speaker.user_id)
        result, _ = run(plan, speaker=speaker, tools=tools)
        judged, executed = judged_vs_executed(result, tools)
        assert executed == judged
        if speaker is CHILD:
            assert "calendar_shared" not in executed


# ── privacy: nothing the speaker or the planner wrote reaches a log or a stored record ───────────────────────────────────────

MARK = "zqxplanted7731"


def test_a_planted_marker_reaches_no_log_and_no_stored_record(monkeypatch, caplog):
    BadExc = type(MARK.title(), (Exception,), {})
    planted = [pl.Step("list_add", {"item": MARK, MARK: MARK, "tags": [MARK]}, MARK),
               pl.Step("calendar_show", {"qualifier": MARK}, MARK)]

    async def raising(step):
        raise BadExc(MARK)
    with caplog.at_level(logging.DEBUG):
        for m in ("shadow", "on"):
            monkeypatch.setenv("ZOE_PLAN_LOOP", m)
            for plan in ([pl.Step(MARK, {"x": MARK}, MARK)], [pl.Step(MARK, {"x": {MARK: MARK}}, MARK)],
                         [pl.Step(MARK, {"user_id": MARK}, MARK)], planted):
                result, _ = run(plan, utterance=MARK)
                r = result.refusal
                if r is not None:
                    assert MARK not in repr(r)                                 # the returned refusal
                    assert r.tool in ("", getattr(pl, "UNREGISTERED", "unregistered")) or r.tool in ha.table().intents
            run(planted, utterance=MARK, tools=raising)
        import distress_handoff
        monkeypatch.setattr(distress_handoff, "detect", lambda text: (_ for _ in ()).throw(BadExc(MARK)))
        assert pl.safety_floor(MARK) == "floor_error"
    assert caplog.records and not any(MARK in r.getMessage().casefold() for r in caplog.records)
    assert MARK not in repr(write_receipts._STORE).casefold()


def test_negative_control_the_old_raw_name_would_have_leaked(monkeypatch, caplog):
    monkeypatch.setattr(pl, "UNREGISTERED", MARK)                             # a "sentinel" that is the user's text again
    monkeypatch.setenv("ZOE_PLAN_LOOP", "shadow")
    with caplog.at_level(logging.INFO, logger="plan_loop"):
        run([pl.Step("send_money", {"x": 1}, "x")])
    assert MARK in caplog.text


# ── lab-only: no live module imports the loop ────────────────────────────────────────────────────────────────────────────────

def _plan_loop_importers(root):
    import ast
    import pathlib

    hits = []
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] in ("tests", ".venv", "venv") or rel.name.startswith("plan_loop"):
            continue                                                          # the loop's own files and its tests
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "plan_loop" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                names = [str(node.args[0].value)]
            if any(n == "plan_loop" or n.startswith("plan_loop.") or n.startswith("plan_loop_") for n in names):
                hits.append(str(rel))
    return sorted(set(hits))


def test_no_live_module_imports_the_plan_loop(tmp_path):
    import pathlib

    assert _plan_loop_importers(pathlib.Path(pl.__file__).parent) == []
    # planted-import control: each form is caught
    for i, line in enumerate(("import plan_loop", "from plan_loop import run", "import importlib\nimportlib.import_module('plan_loop')",
                              "from x import plan_loop_planner", "__import__('plan_loop')")):
        (tmp_path / f"live_{i}.py").write_text(line + "\n")
    (tmp_path / "plan_loop_planner.py").write_text("import plan_loop\n")          # the loop's own file: allowed
    assert _plan_loop_importers(tmp_path) == [f"live_{i}.py" for i in range(5)]
