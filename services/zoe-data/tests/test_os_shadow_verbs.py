"""The Zoe OS shadow widened past list add: reminder create, alarm create and the spoken list-item remove (os_shadow.note_act).

Same in-process fake kernel as test_os_shadow.py (wire v1 from its spec; nothing from ``labs/``). Pins: each verb is default-off
behind ``ZOE_OS_SHADOW_VERBS`` even with the switch on; each sends the kernel action and args the lab drivers declare; what the
kernel's verb cannot express is a counted disagreement, never sent; the live write is unchanged with the kernel on, dead or
disagreeing; every new writer of these tables is shadowed or named with a reason; and THE NEGATIVE CONTROL: a kernel that disagrees
on purpose reads as disagree in the owner's report, verb by verb. Names, notes and items below are invented.
"""
from __future__ import annotations

import ast

import importlib.util
import logging
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import os_shadow
import test_os_shadow_hook as hook_scan
from test_os_shadow import MEMBER, SVC, kernel, settle  # noqa: F401  (kernel is a fixture)

pytestmark = pytest.mark.ci_safe

ALL = "list_add,list_remove,reminder_create,alarm_create"
NOTE = "water the quillon fern"
ROOT = SVC.parents[1]
_spec = importlib.util.spec_from_file_location("os_shadow_report", ROOT / "scripts" / "maintenance" / "os_shadow_report.py")
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _in(minutes: int) -> tuple[str, str]:
    import proactive.triggers.reminder_scan as scan

    when = scan.zoe_now() + timedelta(minutes=minutes)
    return when.date().isoformat(), when.strftime("%H:%M")


def remind(minutes=90, recurring=False, due_time="auto", note=NOTE):
    d, t = _in(minutes)
    os_shadow.note_reminder_create(MEMBER, source="service", note=note, due_date=d, due_time=t if due_time == "auto" else due_time,
                                   recurring=recurring)


def acts(kernel_):
    return [a for _, op, a in kernel_.calls if op == "act"]


# ---- default off, verb by verb ------------------------------------------------------------------------------------------------------
def test_with_only_the_switch_on_the_new_verbs_touch_nothing(kernel):
    remind()
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="07:30", repeat="once")
    os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping", item="rice")
    assert os_shadow._q.qsize() == 0 and os_shadow.counters() == {} and kernel.calls == []
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="rice")   # list add: as before
    settle(1)
    assert os_shadow.counters_by_verb() == {"list_add": {"match": 1}}


def test_the_switch_off_wins_over_any_verb_list(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    remind()
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="07:30", repeat="once")
    assert os_shadow._q.qsize() == 0 and kernel.connections == 0


def test_unknown_verb_names_are_ignored(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create, bogus ,REMINDER_CREATE")
    assert os_shadow.verbs() == frozenset({"alarm_create", "reminder_create"})


# ---- each verb against the kernel -----------------------------------------------------------------------------------------------------
def test_each_verb_sends_the_kernels_action_and_args_and_matches(kernel, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.INFO, logger="os_shadow")
    remind(90)
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")
    os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping", item="rice")
    settle(3)
    sent = {a["action"]: a["args"] for a in acts(kernel)}
    assert set(sent) == {"remind.set", "alarm.set", "list.remove"}
    import time as _time
    due = sent["remind.set"]["due_at"]                                                             # production's own due instant
    assert sent["remind.set"]["note"] == NOTE and "in_minutes" not in sent["remind.set"] and isinstance(due, int)
    assert 88 * 60 <= due - _time.time() <= 91 * 60
    assert sent["alarm.set"]["time"] == "06:45" and sent["list.remove"]["item"] == "rice"
    assert all(isinstance(a["asked"], int) for a in sent.values())
    assert os_shadow.counters_by_verb() == {v: {"match": 1} for v in ("reminder_create", "alarm_create", "list_remove")}
    pubs = [a["payload"]["text"] for _, op, a in kernel.calls if op == "publish"]
    # fixed text plus a random tag (os_shadow.cause_text), never the note
    assert sorted(p.rsplit(" ", 1)[0] for p in pubs) == ["shadow alarm create", "shadow list remove", "shadow reminder create"]
    assert all(len(p.rsplit(" ", 1)[1]) == 16 for p in pubs) and len(set(pubs)) == 3
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "verb=alarm_create src=service cls=once" in text and "verb=reminder_create src=service cls=once" in text
    assert NOTE not in text and "quillon" not in text.lower() and "rice" not in text


@pytest.mark.parametrize("kw,why", [({"recurring": True}, "recurring"), ({"due_time": None}, "no_time"),
                                    ({"due_time": "99:99"}, "time")])
def test_a_reminder_the_kernels_verb_cannot_say_is_a_counted_disagreement_never_sent(kernel, monkeypatch, caplog, kw, why):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    caplog.set_level(logging.INFO, logger="os_shadow")
    remind(**kw)
    settle(1)
    assert acts(kernel) == [] and os_shadow.counters_by_verb() == {"reminder_create": {"not_expressible": 1}}
    (line,) = [r.getMessage() for r in caplog.records if "OS_SHADOW_ACT" in r.getMessage()]
    assert f"result=not_expressible reason={why}" in line
    assert report.bucket("not_expressible") == "disagree"


def test_an_alarm_time_that_is_not_hh_mm_is_not_expressible(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="7:5", repeat="once")
    settle(1)
    assert acts(kernel) == [] and os_shadow.counters() == {"not_expressible": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("days,code", [(0, "match"), (3, "not_expressible")])
async def test_a_one_off_alarm_for_a_later_date_is_not_expressible_and_never_sent(kernel, monkeypatch, tmp_path, caplog, days, code):
    """RUNS ``alarms.create_alarm``: the kernel's alarm carries a time of day only, so a one-off dated three days out must not be
    proposed as that time (it could "match" a kernel alarm on another day). The control: the same alarm for the time's NEXT
    occurrence (its date given explicitly) is proposed and matches."""
    import aiosqlite

    import alarms
    from test_alarms import NIGHT, _migrate

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    caplog.set_level(logging.INFO, logger="os_shadow")
    nxt = date.fromisoformat(alarms.next_once_date("06:45", NIGHT))
    path = str(tmp_path / "a.db")
    _migrate(path)
    db = await aiosqlite.connect(path)
    try:
        a = await alarms.create_alarm(db, MEMBER, time_hhmm="06:45", repeat="once", once_date=(nxt + timedelta(days=days)).isoformat(), now=NIGHT)
    finally:
        await db.close()
    assert a["once_date"] == (nxt + timedelta(days=days)).isoformat()
    settle(1)
    assert os_shadow.counters_by_verb() == {"alarm_create": {code: 1}}
    assert len(acts(kernel)) == (0 if days else 1)
    if days:
        assert any("result=not_expressible reason=dated" in r.getMessage() for r in caplog.records)
        assert "dated" in report.ALLOWED["reason"] and report.bucket("not_expressible") == "disagree"


# ---- THE NEGATIVE CONTROL: a kernel that disagrees on purpose reads as disagree in the report -------------------------------------
#: what each verb's read-back must agree on: list add / remove -> item (and list when told), member; reminder -> note AND minutes,
#: member; alarm -> time, member. "wrong_time" changes only the WHEN, "item_differs" only the WHAT (an alarm's WHAT is its time).
WHEN = {"list_add": None, "list_remove": None, "reminder_create": "schedule_differs", "alarm_create": "schedule_differs"}


@pytest.mark.parametrize("mode,result", [("item_differs", "item_differs"), ("refuse:no grant from that person", "kernel_refused_live_did"),
                                         ("unverified", "kernel_unverified_live_did"), ("wrong_time", "schedule_differs"),
                                         ("wrong_member", "member_differs")])
def test_a_kernel_that_disagrees_on_purpose_is_disagree_for_every_verb_in_the_report(kernel, monkeypatch, caplog, mode, result):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.INFO, logger="os_shadow")
    kernel.act_mode = mode
    for _ in range(3):
        os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="rice")
        os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping", item="rice")
        remind(90)
        os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")
    settle(12)
    s = report.summarize(report.parse(r.getMessage() for r in caplog.records))
    assert set(s["verbs"]) == {"list_add", "list_remove", "reminder_create", "alarm_create"}
    for verb, v in s["verbs"].items():
        want = result
        if mode == "item_differs" and verb == "alarm_create":
            want = "schedule_differs"                         # the alarm's only value IS its time
        if mode == "wrong_time" and WHEN[verb] is None:
            want = "match"                                    # a list item has no WHEN: the control does not touch it
        if want == "match":
            assert (v["seen"], v["agree"]) == (3, 3), verb
            continue
        assert (v["seen"], v["agree"], v["disagree"], v["unavailable"], v["agree_rate"]) == (3, 0, 3, 0, 0.0), verb
        assert v["disagree_by_result"] == {want: 3}, verb


def test_the_same_run_with_an_agreeing_kernel_is_all_agree(kernel, monkeypatch, caplog):
    """The control's other arm: identical traffic, a kernel that agrees: so the disagreement above is the kernel's, not the reader's."""
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.INFO, logger="os_shadow")
    for _ in range(3):
        os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping", item="rice")
        remind(90)
        os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")
    settle(9)
    s = report.summarize(report.parse(r.getMessage() for r in caplog.records))
    assert {v: (x["agree"], x["disagree"], x["agree_rate"]) for v, x in s["verbs"].items()} == {
        v: (3, 0, 1.0) for v in ("list_remove", "reminder_create", "alarm_create")}
    assert all(x["hop_answered"] == 3 and x["hop_ms_p90"] >= x["hop_ms_p50"] > 0 for x in s["verbs"].values())


def test_a_dead_kernel_is_unavailable_never_agree_or_disagree(kernel, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.INFO, logger="os_shadow")
    kernel.srv.close()
    Path(kernel.path).unlink()
    remind(90)
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")
    settle(2)
    s = report.summarize(report.parse(r.getMessage() for r in caplog.records))
    for v in s["verbs"].values():
        assert (v["agree"], v["disagree"], v["unavailable"], v["agree_rate"], v["hop_answered"]) == (0, 0, 1, None, 0)


# ---- the live writes are unchanged ----------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_live_reminder_create_is_identical_with_the_kernel_on_dead_or_disagreeing(kernel, monkeypatch):
    from models import ReminderCreate
    from reminder_service import create_reminder_record
    from test_reminder_scheduled_on_create import _Db

    async def _allow(*_a, **_k):
        return None

    monkeypatch.setattr("reminder_service.require_feature_access", _allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", _allow)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", lambda **_k: _allow())
    d, t = _in(60)

    async def once():
        db = _Db([])
        out = await create_reminder_record(ReminderCreate(title=NOTE, due_date=d, due_time=t), user={"user_id": MEMBER, "role": "admin"},
                                           db=db)
        return {k: v for k, v in db.stored.items() if k != "id"}, {k: v for k, v in out.items() if k != "id"}

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    base = await once()
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    for mode, code in (("ok", "match"), ("item_differs", "item_differs"), ("close", "kernel_unavailable"), ("malformed", "kernel_malformed")):
        os_shadow._reset_for_tests()                 # each mode on its own worker and circuit, its own outcome checked
        assert os_shadow.revive()
        kernel.act_mode = mode
        assert await once() == base, mode
        settle(1)
        assert os_shadow.counters_by_verb() == {"reminder_create": {code: 1}}, mode
    os_shadow._reset_for_tests()
    assert os_shadow.revive()
    kernel.srv.close()
    Path(kernel.path).unlink()
    assert await once() == base
    settle(1)
    assert os_shadow.counters_by_verb() == {"reminder_create": {"kernel_unavailable": 1}}


@pytest.mark.asyncio
async def test_the_live_alarm_create_is_identical_with_the_kernel_on_dead_or_disagreeing(kernel, monkeypatch, tmp_path):
    import aiosqlite

    import alarms
    from test_alarms import NIGHT, _migrate

    async def once(n):
        path = str(tmp_path / f"a{n}.db")
        _migrate(path)
        db = await aiosqlite.connect(path)
        try:
            a = await alarms.create_alarm(db, MEMBER, time_hhmm="06:45", repeat="once", now=NIGHT)
            return {k: v for k, v in dict(a).items() if k != "id"}
        finally:
            await db.close()

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    base = await once(0)
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    for n, (mode, code) in enumerate((("ok", "match"), ("item_differs", "schedule_differs"), ("close", "kernel_unavailable")), 1):
        os_shadow._reset_for_tests()
        assert os_shadow.revive()
        kernel.act_mode = mode
        assert await once(n) == base, mode
        settle(1)
        assert os_shadow.counters_by_verb() == {"alarm_create": {code: 1}}, mode


# ---- structure pins: every WRITER FUNCTION of a shadowed verb is hooked or named (the list-add scan, test_os_shadow_hook.py) ------
_T = r'["`]?(?:\w+["`]?\.["`]?)?'
#: verb -> (the SQL a writer of it runs, any spelling; writer helpers; the hook it must call; the table, for the prefilter;
#:          {"file::function": why it is NOT shadowed})
WRITERS = {
    "reminder_create": (re.compile(r"(?is)\binsert\s+(?:or\s+\w+\s+)?into\s+" + _T + r"reminders\b"), frozenset(), "note_reminder_create",
                        "reminders", {}),
    "alarm_create": (re.compile(r"(?is)\binsert\s+(?:or\s+\w+\s+)?into\s+" + _T + r"alarms\b"), frozenset(), "note_alarm_create",
                     "alarms", {}),
    "list_remove": (re.compile(r"(?is)\b(?:update\s+" + _T + r"list_items[\"`]?\s+set\s+(?:[^;]*?,\s*)?deleted\s*=\s*(?:1|true)\b"
                               r"|delete\s+from\s+" + _T + r"list_items\b)"), frozenset({"remove_item_record"}), "note_list_remove", "list_items",
                    {"list_service.py::rename_item_on_list": "OUT OF SCOPE: the merge retires a duplicate row; a correction, not a remove",
                     "list_service.py::remove_item_record": "The shared row writer; callers retain commit ownership",
                     "skybridge_service.py::_resolve_list_remove_item": "Calls the service hook after commit",
                     "routers/lists.py::delete_item": "Calls the service hook after commit"}),
}


#: verb -> {"file::writer": the one caller that holds the hook, after the transaction the writer runs in}
HOOKED_BY_CALLER = {"reminder_create": {"pending_suggestions.py::_execute_action": "execute_suggestion"}}


@pytest.mark.parametrize("verb", sorted(WRITERS))
def test_every_writer_function_of_a_shadowed_verb_is_hooked_or_named_with_a_reason(verb):
    sql, helpers, hook, table, excused = WRITERS[verb]
    sites, bare = hook_scan.scan(sql, helpers, frozenset({hook}), table)
    by_caller = HOOKED_BY_CALLER.get(verb, {})
    named = set(excused) | set(by_caller)
    assert bare == named, f"{verb}: a writer function neither reaches os_shadow.{hook} nor is named: {sorted(bare ^ named)}"
    hook_scan.assert_hooked_by_caller(by_caller, frozenset({hook}))
    if verb == "list_remove":
        service = (hook_scan.SVC / "list_service.py").read_text("utf-8")
        sky = (hook_scan.SVC / "skybridge_service.py").read_text("utf-8")
        assert hook_scan.hooked(dict(hook_scan._functions(ast.parse(service)))["note_item_left"], frozenset({hook}))
        caller = dict(hook_scan._functions(ast.parse(sky)))["_resolve_list_remove_item"]
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "note_item_left"
                   for n in ast.walk(caller))
    else:
        assert sites - bare, f"{verb}: no hooked writer found at all (the pattern is wrong)"


@pytest.mark.parametrize("verb,sql", [
    ("reminder_create", "insert into reminders (id) values (1)"), ("reminder_create", 'INSERT INTO "public"."reminders" (id)'),
    ("alarm_create", "Insert\n Into alarms (id)"), ("list_remove", "UPDATE list_items SET deleted = 1 WHERE id = $1"),
    ("list_remove", "update list_items set updated_at = NOW(), deleted = TRUE where id = ?"), ("list_remove", "DELETE FROM list_items WHERE id = 1")])
def test_the_verb_detectors_read_every_spelling(verb, sql):
    pattern = WRITERS[verb][0]
    assert hook_scan.writer_sites(f"async def f(db):\n    await db.execute({sql!r})", pattern) == ["f"]
    assert hook_scan.writer_sites(f"async def f(db):\n    await db.execute({sql[:6]!r} + {sql[6:]!r})", pattern) == ["f"]
    assert hook_scan.writer_sites("def f():\n    return 'SELECT * FROM reminders, alarms, list_items'", pattern) == []


def test_a_hooked_writer_does_not_hide_an_unhooked_one_for_a_new_verb():
    src = ("async def a(db):\n    await db.execute('INSERT INTO alarms (id) VALUES (1)')\n    os_shadow.note_alarm_create('u')\n"
           "async def b(db):\n    await db.execute('insert into alarms (id) values (2)')\n")
    assert hook_scan.unhooked(src, WRITERS["alarm_create"][0], frozenset(), frozenset({"note_alarm_create"})) == ["b"]


# ---- every verb: one line per outcome, one deadline per write ------------------------------------------------------------------------
def _verb_call(verb, user=MEMBER, empty=False):
    """One live write of ``verb``; ``empty`` = the write carries no value (no item, no note)."""
    if verb == "list_add":
        os_shadow.note_add(user, source="spoken", list_type="shopping", list_name="Shopping", item="" if empty else "rice")
    elif verb == "list_remove":
        os_shadow.note_list_remove(user, source="skybridge", list_type="shopping", list_name="Shopping", item="" if empty else "rice")
    elif verb == "reminder_create":
        remind(note="" if empty else NOTE) if user == MEMBER else os_shadow.note_reminder_create(
            user, source="service", note=NOTE, due_date=_in(90)[0], due_time=_in(90)[1], recurring=False)
    else:
        os_shadow.note_alarm_create(user, source="service", time_hhmm="" if empty else "06:45", repeat="once")


def _act_lines(caplog, verb):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"OS_SHADOW_ACT verb={verb} ")]


@pytest.mark.parametrize("verb", ["list_add", "list_remove", "reminder_create", "alarm_create"])
def test_every_outcome_of_every_verb_writes_exactly_one_line(kernel, monkeypatch, caplog, verb):
    import queue

    import user_erase_gate

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.INFO, logger="os_shadow")
    want = []

    def one(code, fn):
        fn()
        want.append(code)
        settle(len(want))

    one("match", lambda: _verb_call(verb))
    kernel.act_mode = "refuse"
    one("kernel_refused_live_did", lambda: _verb_call(verb))
    kernel.act_mode = "ok"
    one("member_not_in_roster", lambda: _verb_call(verb, user="someone-else"))
    # an alarm with no time still reaches the worker: the kernel's verb cannot say it (a counted disagreement)
    one("not_expressible" if verb == "alarm_create" else "no_input", lambda: _verb_call(verb, empty=True))
    one("no_input", lambda: _verb_call(verb, user=""))
    with user_erase_gate.erase_span(MEMBER):
        one("dropped_erased", lambda: _verb_call(verb))
    user_erase_gate.reset()
    with monkeypatch.context() as m:
        m.setattr(os_shadow, "_worker_alive", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        one("shadow_error", lambda: _verb_call(verb))
    with monkeypatch.context() as m:
        m.setattr(os_shadow, "kernel_args", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        one("not_expressible", lambda: _verb_call(verb))                      # a field the worker could not read: reason=parse
    with monkeypatch.context() as m:
        m.setattr(os_shadow, "roster", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        one("shadow_error", lambda: _verb_call(verb))
    kernel.delay = 1.0
    one("timeout", lambda: _verb_call(verb))
    kernel.delay = 0.0
    with monkeypatch.context() as m:
        full = queue.Queue(maxsize=1)
        full.put_nowait(("stop",))
        m.setattr(os_shadow, "_q", full)
        one("queue_full", lambda: _verb_call(verb))
    os_shadow._q.put(("stop",))
    os_shadow._worker.join(5)
    one("worker_down", lambda: _verb_call(verb))
    lines = _act_lines(caplog, verb)
    assert sorted(ln.split(" result=", 1)[1].split()[0] for ln in lines) == sorted(want)
    assert len(lines) == len(want) and set(os_shadow.counters_by_verb()) == {verb}
    assert sum(os_shadow.counters_by_verb()[verb].values()) == len(want)
    recs = report.parse(lines)                                                 # the report reads every one of them, by its own code
    assert len(recs) == len(lines) and all(r["verb"] == verb and r["result"] in report.ALLOWED["result"] for r in recs)


@pytest.mark.parametrize("verb", ["list_remove", "reminder_create", "alarm_create"])
def test_every_verb_runs_on_one_deadline(kernel, monkeypatch, caplog, verb):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.INFO, logger="os_shadow")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 1.0)
    monkeypatch.setattr(os_shadow, "JOB_DEADLINE_S", 0.2)
    kernel.stall[("act", 2)] = 0.6                                            # under the per-op timeout, over the write's budget
    _verb_call(verb)
    settle(1, timeout=3)
    (line,) = _act_lines(caplog, verb)
    assert " result=timeout " in line and float(line.split(" hop_ms=", 1)[1].split()[0]) < 350



# ---- the WHEN is compared, not just the WHAT --------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,result", [("wrong_time", "schedule_differs"), ("item_differs", "item_differs"),
                                         ("no_schedule", "schedule_unproven"), ("ok", "match")])
def test_a_reminder_the_kernel_schedules_at_the_wrong_time_is_never_a_match(kernel, monkeypatch, mode, result):
    """THE pass-mark fault: the read-back was compared on the note alone. The time changes here, the note does not (and the reverse);
    a kernel whose read-back does not say WHEN (the lab driver today) cannot read as a match either."""
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    kernel.act_mode = mode
    remind(90)
    settle(1)
    assert os_shadow.counters_by_verb() == {"reminder_create": {result: 1}}


@pytest.mark.parametrize("mode,result", [("wrong_time", "schedule_differs"), ("ok", "match"), ("wrong_member", "member_differs")])
def test_an_alarm_at_the_wrong_time_or_for_someone_else_is_never_a_match(kernel, monkeypatch, mode, result):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    kernel.act_mode = mode
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")
    settle(1)
    assert os_shadow.counters_by_verb() == {"alarm_create": {result: 1}}


def test_a_repeating_alarm_is_not_expressible_never_compared_as_one_time(kernel, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    caplog.set_level(logging.INFO, logger="os_shadow")
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="weekdays")
    settle(1)
    assert acts(kernel) == [] and os_shadow.counters_by_verb() == {"alarm_create": {"not_expressible": 1}}
    assert any("cls=repeat" in r.getMessage() and "reason=recurring" in r.getMessage() for r in caplog.records)


# ---- with the feature off, nothing is built and nothing is read beyond the flags ---------------------------------------------------
NOTE_CALLS = {
    "note_add": lambda: os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="rice"),
    "note_list_remove": lambda: os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping",
                                                           item="rice"),
    "note_reminder_create": lambda: remind(90),
    "note_alarm_create": lambda: os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once"),
    "note_act": lambda: os_shadow.note_act("list_add", MEMBER, source="spoken", raw={"item": "rice", "list": "Shopping"}),
}


def _spy_on_build(monkeypatch, early=False):
    """Every note function hands ``_door`` a ``build`` callable: wrap ``_door`` so the one it receives is a spy. ``built`` then lists
    every payload build, whichever helpers the builder happens to call (patching ``_list_raw`` / ``_text`` missed the alarm and
    ``note_act`` builders, which call neither). ``early`` is the mutation the test must catch: build() run ahead of the flag check."""
    built = []
    real_door = os_shadow._door

    def door(verb, user_id, source, landed, build):
        def spy():
            built.append(verb)
            return build()
        if early:
            spy()
        return real_door(verb, user_id, source, landed, spy)

    monkeypatch.setattr(os_shadow, "_door", door)
    return built


@pytest.mark.parametrize("name", sorted(NOTE_CALLS))
@pytest.mark.parametrize("off", ["switch", "verb"])
def test_with_the_feature_off_no_note_function_builds_its_payload(kernel, monkeypatch, name, off):
    built = _spy_on_build(monkeypatch)
    if off == "switch":
        monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    else:
        other = "list_add" if name == "note_alarm_create" else "alarm_create"
        monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", other)              # the switch on, this verb not named
    reads = []
    real_flag = __import__("typed_env").flag
    monkeypatch.setattr(__import__("typed_env"), "flag", lambda n, *a, **k: (reads.append(n), real_flag(n, *a, **k))[1])
    NOTE_CALLS[name]()
    assert built == [], f"{name} built its payload with the feature off"
    assert os_shadow.counters() == {} and os_shadow._q.qsize() == 0
    assert len(reads) == len(set(reads)) and set(reads) <= {os_shadow.FLAG, os_shadow.VERBS_FLAG}, reads   # each flag read once at most


@pytest.mark.parametrize("name", sorted(NOTE_CALLS))
def test_the_build_spy_catches_a_payload_built_ahead_of_the_flag_check(kernel, monkeypatch, name):
    """NEGATIVE CONTROL for the test above: with build() moved ahead of the flag check (the mutation a reviewer made by hand), the spy
    sees the build for EVERY note function, the alarm and ``note_act`` builders included, so the test above would fail."""
    built = _spy_on_build(monkeypatch, early=True)
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    NOTE_CALLS[name]()
    assert built, f"the spy did not see {name} build its payload"


@pytest.mark.parametrize("name", ["note_add", "note_list_remove", "note_reminder_create"])
def test_with_the_feature_on_a_builder_that_raises_is_one_counted_shadow_error_never_a_raise(kernel, monkeypatch, name):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))      # noqa: E731
    monkeypatch.setattr(os_shadow, "_list_raw", boom)
    monkeypatch.setattr(os_shadow, "_text", boom)
    NOTE_CALLS[name]()
    assert os_shadow.counters() == {"shadow_error": 1}


def _door_flags_before_build(source):
    import ast

    tree = ast.parse(source)
    door = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_door")
    body = door.body[1:] if isinstance(door.body[0], ast.Expr) and isinstance(door.body[0].value, ast.Constant) else door.body
    first = body[0]
    return (isinstance(first, ast.If)
            and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "wants" for n in ast.walk(first.test))
            and not any(isinstance(n, ast.Name) and n.id == "build" for n in ast.walk(first)))


def test_every_note_function_checks_the_flags_before_it_touches_an_argument():
    """Structure: in every ``note_*`` function the arguments that carry the write (everything but who, the source code and the
    verb) are used ONLY inside the lambda handed to ``_note``, and ``_door`` reads ``wants(verb)`` before it calls ``build()``."""
    import ast

    tree = ast.parse((SVC / "os_shadow.py").read_text("utf-8"))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("note_")]
    assert {f.name for f in fns} == set(NOTE_CALLS)
    for fn in fns:
        args = {a.arg for a in fn.args.args + fn.args.kwonlyargs} - {"user_id", "source", "verb", "landed"}
        inside = {id(n) for lam in ast.walk(fn) if isinstance(lam, ast.Lambda) for n in ast.walk(lam)}
        used = [n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id in args and id(n) not in inside]
        assert not used, (fn.name, used)
        (call,) = [c for c in fn.body if isinstance(c, ast.Expr) and isinstance(c.value, ast.Call)]
        assert len(fn.body) <= 2, fn.name                               # a docstring and the one call: nothing runs before the flags
        assert getattr(call.value.func, "id", "") == "_note", fn.name
    source = (SVC / "os_shadow.py").read_text("utf-8")
    assert _door_flags_before_build(source)
    door = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_door")
    call = next(n for n in ast.walk(door) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "wants")
    lines = source.splitlines(keepends=True)
    start = sum(map(len, lines[:call.lineno - 1])) + call.col_offset
    end = sum(map(len, lines[:call.end_lineno - 1])) + call.end_col_offset
    for replacement in ("False", repr("wants(verb)")):
        mutated = source[:start] + replacement + source[end:]
        mutated += "\n# wants(verb): the removed flag call\n"
        assert not _door_flags_before_build(mutated)
    mutated = source[:start] + "wants(verb) and build()" + source[end:]
    assert not _door_flags_before_build(mutated)



# ---- a reminder is compared on its ABSOLUTE due instant, so a queue delay cannot shift it -------------------------------------------
@pytest.mark.parametrize("delay_min", [0, 7])
def test_a_job_delayed_in_the_queue_sends_the_same_instant_and_still_matches(kernel, monkeypatch, delay_min):
    """The door stamps the commit; the worker sends minutes counted from the SEND. A fake wall clock moves on ``delay_min`` minutes
    between the two: what the kernel is asked for (and what is compared) is the same absolute instant, and the kernel's own clock
    turns the minutes back into it."""
    import time as _time

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    clock = [_time.time()]
    monkeypatch.setattr(os_shadow, "_wall", lambda: clock[0])
    os_shadow._reset_for_tests()                                     # no worker yet: the job waits in the queue
    monkeypatch.setattr(os_shadow, "_worker_alive", lambda: True)
    d, t = _in(90)
    os_shadow.note_reminder_create(MEMBER, source="service", note=NOTE, due_date=d, due_time=t, recurring=False)
    clock[0] += 60 * delay_min                                       # the queue delay
    assert os_shadow.revive()
    settle(1)
    (sent,) = acts(kernel)
    assert "in_minutes" not in sent["args"]                          # the absolute instant, never both
    assert 88 * 60 <= sent["args"]["due_at"] - (clock[0] - 60 * delay_min) <= 91 * 60     # counted from the COMMIT, whatever the delay
    assert os_shadow.counters_by_verb() == {"reminder_create": {"match": 1}}


def test_a_kernel_that_reports_the_due_time_off_by_the_queue_delay_is_schedule_differs(kernel, monkeypatch):
    """A kernel that reports the due instant 7 minutes off (what a relative send through a delayed queue produced): never a match."""
    import time as _time

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    clock = [_time.time()]
    monkeypatch.setattr(os_shadow, "_wall", lambda: clock[0])
    os_shadow._reset_for_tests()
    monkeypatch.setattr(os_shadow, "_worker_alive", lambda: True)
    d, t = _in(90)
    os_shadow.note_reminder_create(MEMBER, source="service", note=NOTE, due_date=d, due_time=t, recurring=False)
    clock[0] += 420
    kernel.act_mode = "late_by_delay"
    kernel.late_s = 420
    assert os_shadow.revive()
    settle(1)
    assert os_shadow.counters_by_verb() == {"reminder_create": {"schedule_differs": 1}}


def test_the_due_instant_comparison_tolerates_only_the_minute_rounding():
    sent = {"note": "n", "in_minutes": 5, "due_at": 1_000_000.0}
    rec = lambda due: {"verified": True, "outcome": "verified", "on_behalf_of": "m", "proof": {"note": "n", "due_at": due}}  # noqa: E731
    assert os_shadow.compare("added", rec(1_000_030), sent, "reminder_create", member="m")[0] == "match"
    assert os_shadow.compare("added", rec(1_000_061), sent, "reminder_create", member="m")[0] == "schedule_differs"
    assert os_shadow.compare("added", rec("soon"), sent, "reminder_create", member="m")[0] == "kernel_malformed"    # not a time
    no_due = {"verified": True, "outcome": "verified", "on_behalf_of": "m", "proof": {"note": "n", "in_minutes": 5}}
    assert os_shadow.compare("added", no_due, sent, "reminder_create", member="m")[0] == "schedule_unproven"   # relative is no proof


# ---- marker text through any caller argument, every verb: never in a line or a counter key ----------------------------------------
MARK = "Zq7Marker"
_M = f"{MARK} x=1 result=match"
MARKED = {
    "list_add": [dict(source=_M), dict(item=_M), dict(list_name=_M), dict(list_type=_M)],
    "list_remove": [dict(source=_M), dict(item=_M), dict(list_name=_M), dict(list_type=_M)],
    "reminder_create": [dict(source=_M), dict(note=_M), dict(due_time=_M), dict(due_date=_M)],
    "alarm_create": [dict(source=_M), dict(time_hhmm=_M), dict(repeat=_M)],
}


def _call_marked(verb, kw):
    if verb in ("list_add", "list_remove"):
        base = dict(source="spoken", list_type="shopping", list_name="Shopping", item="rice") | kw
        (os_shadow.note_add if verb == "list_add" else os_shadow.note_list_remove)(MEMBER, **base)
    elif verb == "reminder_create":
        d, t = _in(90)
        os_shadow.note_reminder_create(MEMBER, **(dict(source="service", note=NOTE, due_date=d, due_time=t, recurring=False) | kw))
    else:
        os_shadow.note_alarm_create(MEMBER, **(dict(source="service", time_hhmm="06:45", repeat="once") | kw))


@pytest.mark.parametrize("verb,kw", [(v, kw) for v, kws in MARKED.items() for kw in kws])
def test_marker_text_from_any_argument_of_any_verb_never_reaches_a_line_or_a_counter(kernel, monkeypatch, caplog, verb, kw):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.DEBUG)
    _call_marked(verb, kw)
    settle(1)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert MARK not in text and not any(MARK in str(k) for k in os_shadow.counters())
    assert not any(MARK in str(k) or MARK in str(v) for k, v in os_shadow.counters_by_verb().items())
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("OS_SHADOW_ACT ")]
    assert line.count("result=") == 1 and f"verb={verb} " in line


@pytest.mark.parametrize("mode", ["refuse:" + MARK, "error_code"])
def test_marker_text_from_the_kernel_never_reaches_a_line(kernel, monkeypatch, caplog, mode):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.DEBUG)
    kernel.act_mode, kernel.error_code = mode, MARK
    remind(90)
    settle(1)
    assert MARK not in " ".join(r.getMessage() for r in caplog.records)


def test_a_verb_name_that_is_not_a_verb_is_not_shadowed_and_never_logged(kernel, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    caplog.set_level(logging.DEBUG)
    os_shadow.note_act(MARK, MEMBER, source="spoken", raw={"item": "rice", "list": "Shopping"})
    assert MARK not in " ".join(r.getMessage() for r in caplog.records) and os_shadow.counters() == {}


@pytest.mark.parametrize("verb", ["list_add", "list_remove", "reminder_create", "alarm_create"])
def test_a_receipt_that_names_no_member_is_member_unproven_for_every_verb(kernel, monkeypatch, verb):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", ALL)
    kernel.act_mode = "no_member"
    _call_marked(verb, {})
    settle(1)
    assert os_shadow.counters_by_verb() == {verb: {"member_unproven": 1}}



# ---- an older kernel (no due_at) and bad numbers in a kernel's read-back ------------------------------------------------------------
@pytest.mark.parametrize("delay_min", [0, 7])
def test_an_older_kernel_gets_one_retry_in_minutes_from_the_send_and_reads_schedule_unproven(kernel, monkeypatch, caplog, delay_min):
    import time as _time

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    caplog.set_level(logging.INFO, logger="os_shadow")
    clock = [_time.time()]
    monkeypatch.setattr(os_shadow, "_wall", lambda: clock[0])
    kernel.old_kernel = True
    os_shadow._reset_for_tests()
    monkeypatch.setattr(os_shadow, "_worker_alive", lambda: True)
    d, t = _in(90)
    os_shadow.note_reminder_create(MEMBER, source="service", note=NOTE, due_date=d, due_time=t, recurring=False)
    clock[0] += 60 * delay_min
    assert os_shadow.revive()
    settle(1)
    tries = acts(kernel)
    assert [sorted(x["args"]) for x in tries] == [["asked", "due_at", "note"], ["asked", "in_minutes", "note"]]   # once, then the fallback
    assert 88 - delay_min <= tries[1]["args"]["in_minutes"] <= 91 - delay_min                                   # from the SEND
    assert os_shadow.counters_by_verb() == {"reminder_create": {"schedule_unproven": 1}}
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("OS_SHADOW_ACT ")]
    assert " result=schedule_unproven reason=old_kernel " in line


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "soon", True, 10 ** 30])
def test_a_due_instant_that_is_not_a_time_is_kernel_malformed(bad):
    sent = {"note": "n", "due_at": 1_000_000}
    rec = {"verified": True, "outcome": "verified", "on_behalf_of": "m", "proof": {"note": "n", "due_at": bad}}
    assert os_shadow.compare("added", rec, sent, "reminder_create", member="m")[0] == "kernel_malformed"


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_a_reminder_read_back_with_a_non_standard_number_is_malformed_on_the_wire(kernel, monkeypatch, raw):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    kernel.act_mode = ('raw_result:{"verified": true, "outcome": "verified", "on_behalf_of": "%s", "proof": {"note": "%s", "due_at": %s}}'
                       % (MEMBER, NOTE, raw))
    remind(90)
    settle(1)
    assert os_shadow.counters_by_verb() == {"reminder_create": {"kernel_malformed": 1}}



def test_a_reminder_eight_days_out_is_sent_as_its_instant_and_compared(kernel, monkeypatch):
    """Before due_at the kernel's 1..10080 minutes made this not_expressible:range; the absolute instant says it."""
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    remind(minutes=8 * 24 * 60)
    settle(1)
    assert os_shadow.counters_by_verb() == {"reminder_create": {"match": 1}} and "due_at" in acts(kernel)[0]["args"]


# ---- the shadow proposes against the RESOLVED list, not the words ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_card_context_remove_on_a_custom_list_is_shadowed_against_that_list(kernel, monkeypatch):
    import skybridge_service as sky

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

    class _DB:
        async def fetch(self, sql, *args):
            if "FROM lists" in sql and "WHERE id = $1" in sql:
                return [{"id": "L9", "name": "Camping gear", "list_type": "personal"}]      # the card's list
            if "FROM list_items" in sql:
                return [{"id": "I1", "list_id": "L9", "text": "tent pegs", "completed": 0}]
            return []

        async def execute(self, sql, *args):
            return "UPDATE 1"

    async def nothing(*a, **k):
        return {}

    monkeypatch.setattr(sky, "_ensure_default_lists", nothing)
    monkeypatch.setattr(sky, "_maybe_commit", nothing)
    monkeypatch.setattr(sky, "_resolve_lists", nothing)
    monkeypatch.setattr(sky, "_audit_list_item", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(os_shadow, "wants", lambda verb: True)
    monkeypatch.setattr(os_shadow, "note_list_remove", lambda *a, **k: seen.append(k))
    context = {"cards": [{"content": {"source": "list_show", "list_id": "L9", "list_type": "personal"}}]}
    intent = sky.SkybridgeIntent(domain="lists", action="remove_item", item_text="tent pegs")     # no list named: the card decides
    await sky._resolve_list_remove_item(intent, MEMBER, _DB(), context)
    assert [k["list_name"] for k in seen] == ["Camping gear"] and seen[0]["list_type"] == "personal"
    assert await sky._find_list_for_remove(intent, MEMBER, _DB(), context) == ("L9", "personal")   # every other caller: unchanged


# ---- the reminder and alarm hooks run only after a commit that succeeded (fakes that CAN fail) ------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_the_reminder_hook_runs_after_its_commit_and_never_after_a_failed_one(kernel, monkeypatch, fail):
    from models import ReminderCreate
    from reminder_service import create_reminder_record
    from test_reminder_scheduled_on_create import _Db

    async def _allow(*_a, **_k):
        return None

    class _CommitDb(_Db):
        committed = 0

        async def commit(self):
            if fail:
                raise RuntimeError("commit failed")
            self.committed += 1

    monkeypatch.setattr("reminder_service.require_feature_access", _allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", _allow)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", lambda **_k: _allow())
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "reminder_create")
    db = _CommitDb([])
    seen = []
    monkeypatch.setattr(os_shadow, "note_reminder_create", lambda *a, **k: seen.append(db.committed))
    d, t = _in(60)
    try:
        await create_reminder_record(ReminderCreate(title=NOTE, due_date=d, due_time=t), user={"user_id": MEMBER, "role": "admin"}, db=db)
    except RuntimeError:
        assert fail
    assert seen == ([] if fail else [1])               # after the one commit; nothing when the commit failed


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_the_alarm_hook_runs_after_its_commit_and_never_after_a_failed_one(kernel, monkeypatch, tmp_path, fail):
    import aiosqlite

    import alarms
    from test_alarms import NIGHT, _migrate

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    path = str(tmp_path / "a.db")
    _migrate(path)
    db = await aiosqlite.connect(path)
    state = {"committed": 0}
    real_commit = db.commit

    async def commit():
        if fail:
            raise RuntimeError("commit failed")
        await real_commit()
        state["committed"] += 1

    monkeypatch.setattr(db, "commit", commit)
    seen = []
    monkeypatch.setattr(os_shadow, "note_alarm_create", lambda *a, **k: seen.append(state["committed"]))
    try:
        await alarms.create_alarm(db, MEMBER, time_hhmm="06:45", repeat="once", now=NIGHT)
    except RuntimeError:
        assert fail
    finally:
        await db.close()
    assert seen == ([] if fail else [1])


def test_with_the_shadow_off_an_alarm_create_computes_no_date(monkeypatch):
    """Review (PR #2631), no regression against main: ``alarms.create_alarm`` hands the hook a CALLABLE for ``dated``; with the
    switch off the hook touches nothing, so ``next_once_date`` (timezone / date work) is never run after the commit."""
    import ast
    import alarms

    calls = []
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    os_shadow.note_alarm_create("member-aoff", source="service", time_hhmm="07:10", repeat="once", dated=lambda: calls.append(1) or True)
    assert calls == []
    # structural: the one production caller passes a lambda, never an eagerly computed value
    tree = ast.parse(open(alarms.__file__, encoding="utf-8").read())
    hooks = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "note_alarm_create"]
    # every call MUST pass ``dated`` (a missing keyword would make an all() over its keywords vacuously true) and it must be a lambda
    dated = [[kw.value for kw in n.keywords if kw.arg == "dated"] for n in hooks]
    assert hooks and all(len(d) == 1 for d in dated), "every note_alarm_create call passes dated="
    assert all(isinstance(d[0], ast.Lambda) for d in dated), "dated= is a lambda (lazy), never an eagerly computed value"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["spoken", "mcp", "rest"])
async def test_list_remove_observes_committed_row(monkeypatch, path):
    import contextlib
    import sqlite3
    import intent_router
    import mcp_server
    import routers.lists as lists

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE lists (id TEXT, user_id TEXT, visibility TEXT, list_type TEXT, name TEXT, deleted INT, created_at TEXT);"
                       "CREATE TABLE list_items (id TEXT, list_id TEXT, text TEXT, completed INT, deleted INT, updated_at TEXT);"
                       "INSERT INTO lists VALUES ('luz', 'miv', 'personal', 'shopping', 'Vellum', 0, '1');"
                       "INSERT INTO list_items VALUES ('qir', 'luz', 'womble', 0, 0, '1');")
    conn.commit()

    class DB:
        async def execute(self, sql, params=()):
            return Cursor(conn.execute(sql.replace('NOW()', "'now'"), params))
        async def commit(self):
            conn.commit()

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor
        async def fetchone(self):
            return self.cursor.fetchone()
        async def fetchall(self):
            return self.cursor.fetchall()

    db = DB()
    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    async def noop(*args, **kwargs):
        return None
    async def actor(*args, **kwargs):
        return {"user_id": "miv"}
    async def panel_actor(*args, **kwargs):
        return False, "miv"
    monkeypatch.setattr(os_shadow, "wants", lambda verb: True)
    observed = []
    def note(user, **kwargs):
        row = conn.execute("SELECT completed, deleted FROM list_items WHERE id='qir'").fetchone()
        assert not conn.in_transaction
        assert row["deleted" if path == "rest" else "completed"] == 1
        observed.append((user, kwargs))
    monkeypatch.setattr(os_shadow, "note_list_remove", note)
    monkeypatch.setattr("database.get_db_ctx", ctx)
    monkeypatch.setattr(intent_router, "_notify_lists_ui", noop)
    monkeypatch.setattr(mcp_server, "_notify_ui", noop)
    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)
    monkeypatch.setattr(lists, "_item_actor", panel_actor)
    monkeypatch.setattr(lists, "_require_item_feature", noop)
    monkeypatch.setattr(lists, "_require_list_item_mutation_access", noop)
    monkeypatch.setattr(lists.broadcaster, "broadcast", noop)
    if path == "spoken":
        await intent_router._execute_list_remove_direct(intent_router.Intent("list_remove", {"item": "womble", "list_type": "shopping"}), "miv")
    elif path == "mcp":
        await mcp_server._execute_tool(db, "list_remove_item", {"item_text": "womble", "list_type": "shopping"})
    else:
        await lists.delete_item("shopping", "luz", "qir", user={"user_id": "miv"}, db=db)
    assert observed == [("miv", {"source": path, "item": "womble", "list_type": "shopping", "list_name": "Vellum"})]
    conn.close()


@pytest.mark.asyncio
async def test_list_remove_shadow_off_does_not_query(monkeypatch):
    import list_service
    class DB:
        async def execute(self, *args):
            pytest.fail("shadow off must not query")
    monkeypatch.setattr(os_shadow, "wants", lambda verb: False)
    await list_service.note_item_left(DB(), "miv", source="rest", list_id="luz", item_id="qir")


class _LeftDB:
    """A stand-in connection for ``note_item_left``: one row keyed by item id, or a failing read."""

    def __init__(self, row=None, fail=False):
        self.row, self.fail, self.sql = row, fail, []

    async def execute(self, sql, params=()):
        self.sql.append(sql)
        if self.fail:
            raise RuntimeError("store hiccup")
        row = self.row

        class _Cur:
            async def fetchone(self):
                return row
        return _Cur()


@pytest.mark.parametrize("kind", ["deleted", "ticked"])
def test_a_removed_item_resolves_its_names_after_the_commit_whichever_way_it_left(monkeypatch, kind):
    """Review (PR #2679): the lookup runs AFTER the commit, on an item that is by then deleted (REST, skybridge) or ticked off
    (voice, MCP): its SELECT must not filter on either, and the hook gets the item text and the list's name."""
    import asyncio
    import list_service

    seen = []
    monkeypatch.setattr(os_shadow, "wants", lambda verb: verb == "list_remove")
    monkeypatch.setattr(os_shadow, "note_list_remove", lambda user_id, **kw: seen.append((user_id, kw)))
    db = _LeftDB({"text": "plim wafers", "list_type": "shopping", "name": "Quarn list"})
    asyncio.run(list_service.note_item_left(db, "member-left", source="spoken" if kind == "ticked" else "rest", list_id="l-1", item_id="i-1"))
    assert seen == [("member-left", {"source": "spoken" if kind == "ticked" else "rest", "list_type": "shopping",
                                     "list_name": "Quarn list", "item": "plim wafers"})]
    sql = " ".join(db.sql).lower()
    assert "deleted" not in sql and "completed" not in sql, sql        # neither state hides the row from the lookup


def test_the_remove_hook_never_raises_and_never_queries_when_off(monkeypatch):
    import asyncio
    import list_service

    monkeypatch.setattr(os_shadow, "wants", lambda verb: True)
    monkeypatch.setattr(os_shadow, "note_list_remove", lambda *a, **k: (_ for _ in ()).throw(AssertionError("not reached")))
    asyncio.run(list_service.note_item_left(_LeftDB(fail=True), "member-left", source="rest", list_id="l-1", item_id="i-1"))   # read fails: swallowed
    off = _LeftDB({"text": "x", "list_type": "shopping", "name": "y"})
    monkeypatch.setattr(os_shadow, "wants", lambda verb: False)
    asyncio.run(list_service.note_item_left(off, "member-left", source="rest", list_id="l-1", item_id="i-1"))
    assert off.sql == []                                               # switch off: no query at all
