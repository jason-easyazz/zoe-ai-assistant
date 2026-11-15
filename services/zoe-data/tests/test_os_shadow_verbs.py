"""The Zoe OS shadow widened past list add: reminder create, alarm create and the spoken list-item remove (os_shadow.note_act).

Same in-process fake kernel as test_os_shadow.py (wire v1 from its spec; nothing from ``labs/``). Pins: each verb is default-off
behind ``ZOE_OS_SHADOW_VERBS`` even with the switch on; each sends the kernel action and args the lab drivers declare; what the
kernel's verb cannot express is a counted disagreement, never sent; the live write is unchanged with the kernel on, dead or
disagreeing; every new writer of these tables is shadowed or named with a reason; and THE NEGATIVE CONTROL: a kernel that disagrees
on purpose reads as disagree in the owner's report, verb by verb. Names, notes and items below are invented.
"""
from __future__ import annotations

import importlib.util
import logging
import re
from datetime import timedelta
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
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="weekdays")
    os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping", item="rice")
    settle(3)
    sent = {a["action"]: a["args"] for a in acts(kernel)}
    assert set(sent) == {"remind.set", "alarm.set", "list.remove"}
    assert sent["remind.set"]["note"] == NOTE and 88 <= sent["remind.set"]["in_minutes"] <= 91     # production's own due instant
    assert sent["alarm.set"]["time"] == "06:45" and sent["list.remove"]["item"] == "rice"
    assert all(isinstance(a["asked"], int) for a in sent.values())
    assert os_shadow.counters_by_verb() == {v: {"match": 1} for v in ("reminder_create", "alarm_create", "list_remove")}
    pubs = [a["payload"]["text"] for _, op, a in kernel.calls if op == "publish"]
    assert sorted(pubs) == ["shadow alarm create", "shadow list remove", "shadow reminder create"]   # fixed text, never the note
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "verb=alarm_create src=service cls=repeat" in text and "verb=reminder_create src=service cls=once" in text
    assert NOTE not in text and "quillon" not in text.lower() and "rice" not in text


@pytest.mark.parametrize("kw,why", [({"recurring": True}, "recurring"), ({"due_time": None}, "no_time"),
                                    ({"minutes": 8 * 24 * 60}, "range"), ({"due_time": "99:99"}, "time")])
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


# ---- THE NEGATIVE CONTROL: a kernel that disagrees on purpose reads as disagree in the report -------------------------------------
@pytest.mark.parametrize("mode,result", [("item_differs", "item_differs"), ("refuse:no grant from that person", "kernel_refused_live_did"),
                                         ("unverified", "kernel_unverified_live_did")])
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
        assert (v["seen"], v["agree"], v["disagree"], v["unavailable"], v["agree_rate"]) == (3, 0, 3, 0, 0.0), verb
        assert v["disagree_by_result"] == {result: 3}, verb


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
    for mode in ("ok", "item_differs", "close", "malformed"):
        kernel.act_mode = mode
        assert await once() == base, mode
    kernel.srv.close()
    Path(kernel.path).unlink()
    assert await once() == base
    settle(5)


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
    for n, mode in enumerate(("ok", "item_differs", "close"), 1):
        kernel.act_mode = mode
        assert await once(n) == base, mode
    settle(3)
    assert sum(os_shadow.counters_by_verb()["alarm_create"].values()) == 3


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
                               r"|delete\s+from\s+" + _T + r"list_items\b)"), frozenset(), "note_list_remove", "list_items",
                    {"list_service.py::rename_item_on_list": "OUT OF SCOPE: the merge retires a duplicate row; a correction, not a remove",
                     "routers/lists.py::delete_item": "OUT OF SCOPE for now: the REST delete reads no item text; shadowing it needs the "
                                                      "text read first (a later, separate change)"}),
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
