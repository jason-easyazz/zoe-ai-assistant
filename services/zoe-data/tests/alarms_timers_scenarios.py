"""Every alarm and timer entry point, run against a recording store: what it reads, what it WRITES (statement and values), which
hooks it fires, the receipts it records, what it returns, and what the store holds afterwards.

Used twice: ``python3 tests/alarms_timers_scenarios.py <a zoe-data tree>`` prints the record of THAT tree as JSON (run against an
export of ``main`` to make ``tests/fixtures/alarms_timers_as_on_main.json``), and ``test_alarms_timers_same_as_main.py`` runs the
same scenarios in-process and compares. It imports nothing the kernel-door change added, so it runs unchanged on ``main``.

WHERE THEY LIVE. Alarms: Postgres tables ``alarms`` / ``alarm_events`` / ``alarm_overrides``, written only by ``alarms.py`` (here: a
sqlite file migrated with the real migrations 0063 + 0070). Timers: one in-process registry (``skybridge_service._TimerStore``,
snapshotted to ``~/.zoe/timers.json``), written only by that class's own methods (here: a fresh store whose snapshot writes are
recorded instead of made).

Invented people and words only. The clocks are frozen and the ids are counted, so the record is the same on every run.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ANN, BOB = "ann-0001", "bob-0002"
PERTH = ZoneInfo("Australia/Perth")
NIGHT = datetime(2026, 11, 2, 22, 0, tzinfo=PERTH).timestamp()        # Monday 22:00
SEVEN = datetime(2026, 11, 3, 7, 0, tzinfo=PERTH).timestamp()         # Tuesday 07:00
_UNDO: list = []


def _set(obj, name, value) -> None:
    _UNDO.append((obj, name, getattr(obj, name)))
    setattr(obj, name, value)


def _restore() -> None:
    while _UNDO:
        obj, name, value = _UNDO.pop()
        setattr(obj, name, value)


class _Ids:
    """``uuid.uuid4().hex`` counted instead of random (both stores cut the first 12 characters)."""

    def __init__(self, prefix: str) -> None:
        self.prefix, self.n = prefix, 0

    def uuid4(self):
        self.n += 1
        return types.SimpleNamespace(hex=f"{self.prefix}{self.n:011d}" + "0" * 20)


def _plain(value):
    return json.loads(json.dumps(value, default=str))


# ── alarms ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
class _Rec:
    """An aiosqlite connection that records every statement (reads too: an extra round trip is a change) and commit."""

    def __init__(self, db, log) -> None:
        self._db, self._log = db, log

    def execute(self, sql, params=()):
        self._log.append(["sql", " ".join(sql.split()), _plain(list(params))])
        return self._db.execute(sql, params)

    async def commit(self):
        self._log.append(["commit"])
        await self._db.commit()

    def __getattr__(self, name):
        return getattr(self._db, name)


def _migrate(root: Path, path: str) -> None:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for name in ("0063_alarms.py", "0070_alarm_overrides.py"):
            spec = importlib.util.spec_from_file_location("mig_" + name[:4], root / "alembic" / "versions" / name)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.upgrade()
    engine.dispose()


class _AlarmWorld:
    def __init__(self, root: Path, log: list) -> None:
        import aiosqlite

        import alarm_voice
        import alarms
        import os_shadow
        import write_receipts
        from routers import alarms as rest

        self.alarms, self.voice, self.rest, self.log, self.aiosqlite = alarms, alarm_voice, rest, log, aiosqlite
        self.dir = tempfile.mkdtemp(prefix="alm")
        self.path = str(Path(self.dir) / "z.db")
        _migrate(root, self.path)
        self.clock = {"now": NIGHT}
        import time as _real

        fake = types.SimpleNamespace(time=lambda: self.clock["now"], monotonic=_real.monotonic, sleep=_real.sleep)
        for mod in (alarms, alarm_voice, rest):
            _set(mod, "time", fake)
        _set(alarms, "uuid", _Ids("a"))

        async def emit(event, alarm):
            log.append(["emit", event, alarm.get("id")])

        _set(alarms, "_emit", emit)
        _set(alarms, "enabled", lambda: True)
        _set(os_shadow, "note_alarm_create", lambda owner, **k: log.append(
            ["shadow", owner, {key: (v() if callable(v) else v) for key, v in sorted(k.items())}]))
        _set(write_receipts, "record", lambda *a, **k: log.append(["receipt", *_plain(a)]))

        @contextlib.asynccontextmanager
        async def voice_db():
            async with self.db() as db:
                yield db

        _set(alarm_voice, "_db", voice_db)

    @contextlib.asynccontextmanager
    async def db(self):
        raw = await self.aiosqlite.connect(self.path)
        try:
            yield _Rec(raw, self.log)
        finally:
            await raw.close()

    async def rows(self) -> dict:
        async with self.db() as rec:
            out = {}
            for table, order in (("alarms", "id"), ("alarm_events", "ts, event, alarm_id"), ("alarm_overrides", "alarm_id, on_date")):
                async with rec._db.execute(f"SELECT * FROM {table} ORDER BY {order}") as cur:
                    rows = [list(r) for r in await cur.fetchall()]
                out[table] = [r[1:] if table == "alarm_events" else r for r in rows]       # an event's own id is random: left out
            return out


def _member(uid):
    return {"user_id": uid, "role": "user"}


PANEL = {"user_id": "guest", "role": "guest", "panel_id": "panel-zed"}


def alarm_scenarios() -> dict:
    """name -> a coroutine function of the world; its return value is recorded."""
    async def rest_create(w, user=None, **payload):
        async with w.db() as db:
            return await w.rest.create_alarm({"time_hhmm": "07:00", **payload}, None, user=user or _member(ANN), db=db)

    async def ring(w, at=SEVEN):
        w.clock["now"] = at
        async with w.db() as db:
            return [[e, a["id"]] for e, a in await w.alarms.tick(db, at)]

    def aid(n=1):
        return f"a{n:011d}"

    async def create_member(w):
        return await rest_create(w, label="womble", repeat="weekdays", sound_kind="tone", sound_id="chime", snooze_minutes=5)

    async def create_panel(w):
        return await rest_create(w, user=PANEL, time="06:30")

    async def create_bad_time(w):
        return await rest_create(w, time_hhmm="25:99")

    async def edit(w):
        await rest_create(w)
        async with w.db() as db:
            return await w.rest.update_alarm(aid(), {"label": "womble", "time_hhmm": "06:45"}, None, user=_member(ANN), db=db)

    async def edit_disable(w):
        await rest_create(w)
        async with w.db() as db:
            return await w.rest.update_alarm(aid(), {"enabled": False}, None, user=_member(ANN), db=db)

    async def edit_someone_elses(w):
        await rest_create(w)
        async with w.db() as db:
            return await w.rest.update_alarm(aid(), {"label": "x"}, None, user=_member(BOB), db=db)

    async def edit_household_by_member(w):
        await rest_create(w, user=PANEL)
        async with w.db() as db:
            return await w.rest.update_alarm(aid(), {"label": "womble"}, None, user=_member(BOB), db=db)

    async def delete(w):
        await rest_create(w)
        async with w.db() as db:
            return await w.rest.delete_alarm(aid(), None, user=_member(ANN), db=db)

    async def delete_private_by_panel(w):
        await rest_create(w)
        async with w.db() as db:
            return await w.rest.delete_alarm(aid(), None, user=PANEL, db=db)

    async def snooze(w, user=None):
        await rest_create(w)
        await ring(w)
        async with w.db() as db:
            return await w.rest.snooze_alarm(aid(), None, user=user or _member(ANN), db=db)

    async def snooze_by_panel(w):
        return await snooze(w, PANEL)

    async def snooze_not_ringing(w):
        await rest_create(w)
        async with w.db() as db:
            return await w.rest.snooze_alarm(aid(), None, user=_member(ANN), db=db)

    async def dismiss(w, user=None):
        await rest_create(w)
        await ring(w)
        async with w.db() as db:
            return await w.rest.dismiss_alarm(aid(), None, user=user or _member(ANN), db=db)

    async def dismiss_by_panel(w):
        return await dismiss(w, PANEL)

    async def dismiss_by_another_member(w):
        return await dismiss(w, _member(BOB))

    async def override(w):
        await rest_create(w, repeat="daily")
        async with w.db() as db:
            return await w.rest.set_override(aid(), {"date": "2026-11-05", "time_hhmm": "05:30"}, None, user=_member(ANN), db=db)

    async def override_clear(w):
        await override(w)
        async with w.db() as db:
            return await w.rest.clear_override(aid(), "2026-11-05", None, user=_member(ANN), db=db)

    async def said(w, text, user=ANN):
        cmd = w.voice.parse(text, w.clock["now"])
        return None if cmd is None else (await w.voice.resolve(cmd, user))["spoken_summary"]

    async def spoken_set(w):
        return await said(w, "set an alarm for 7 am")

    async def spoken_set_repeating(w):
        return await said(w, "wake me at 6:30 every weekday")

    async def spoken_snooze(w):
        await said(w, "set an alarm for 7 am")
        await ring(w)
        return await said(w, "snooze")

    async def spoken_off_while_ringing(w):
        await said(w, "set an alarm for 7 am")
        await ring(w)
        return await said(w, "turn off the alarm")

    async def spoken_cancel(w):
        await said(w, "set an alarm for 7 am")
        return await said(w, "cancel my alarm")

    async def spoken_turn_off_then_on(w):
        await said(w, "set an alarm for 7 am")
        return [await said(w, "turn off my 7 am alarm"), await said(w, "turn my 7 am alarm back on")]

    async def spoken_cancel_all(w):
        await said(w, "set an alarm for 7 am")
        await said(w, "set an alarm for 8 am")
        return await said(w, "cancel all my alarms")

    async def spoken_guest_off_panel(w):
        return await said(w, "set an alarm for 7 am", user="guest")

    async def brain_set(w):
        return await w.voice.run_brain_intent("alarm_set", {"request": "6:30 am tomorrow"}, ANN)

    async def brain_refuses_a_cancel(w):
        return await w.voice.run_brain_intent("alarm_set", {"request": "cancel my alarm"}, ANN)

    async def engine_rings_and_stops_by_itself(w):
        await rest_create(w)
        return [await ring(w), await ring(w, SEVEN + w.alarms.RING_MAX_S + 1)]

    async def spoken_shift_a_repeating_alarm(w):
        """One day's change to a repeating alarm (``alarm_voice._run_shift``): a day's exception, the alarm itself untouched."""
        await said(w, "wake me at 6:30 every weekday")
        return await said(w, "get me up an hour earlier on thursday")

    async def spoken_shift_undone(w):
        await spoken_shift_a_repeating_alarm(w)
        return await said(w, "back to normal on thursday")

    async def spoken_shift_a_one_off(w):
        await said(w, "set an alarm for 7 am")
        return await said(w, "get me up an hour earlier tomorrow")

    async def spoken_shift_with_no_alarm(w):
        return await said(w, "I need to be up at 5:30 on thursday")

    async def forget_member(w):
        """The right-to-be-forgotten path (``MemoryService.delete_user`` -> ``alarms.forget_user``): the member's alarms, their
        event rows and their day overrides go; another member's alarm and the household's stay."""
        import db_pool

        await override(w)                                       # the member's own, with an event row and a day's exception
        await rest_create(w, user=_member(BOB), label="quince")
        await rest_create(w, user=PANEL, time="06:30")
        _set(db_pool, "get_db_ctx", w.db)
        return await w.alarms.forget_user(ANN)

    async def forget_nobody(w):
        await rest_create(w)
        return [await w.alarms.forget_user(""), await w.alarms.forget_user(w.alarms.HOUSEHOLD)]

    async def anticipation_wake(w):
        """A "yes" to Zoe's offer of an earlier alarm (``anticipation._do``, kind ``wake``): a one-off alarm for that day."""
        from datetime import timezone

        import anticipation

        offer = {"kind": "wake", "slots": {"wake_time": "05:30", "start_date": "2026-11-05", "label": "womble"}}
        async with w.db() as db:
            reply, receipt = await anticipation._do(db, ANN, offer, None, datetime.fromtimestamp(w.clock["now"], timezone.utc))
        return [reply, receipt]

    async def music_did_not_start(w):
        """The clock-radio's music did not start (``alarm_music._fail``, which calls ``_log``): one ``music_fallback`` event row
        through ``alarms.log_event`` and its commit, on the handle ``_log`` opens itself (``db_pool.get_db_ctx``)."""
        import alarm_music
        import db_pool

        await rest_create(w)
        _set(db_pool, "get_db_ctx", w.db)
        _set(alarm_music, "time", w.alarms.time)
        _set(alarm_music, "STATE", {})
        await alarm_music._fail(None, aid(1), "", "no panel player")
        return alarm_music.STATE[aid(1)]

    return {
        "forget_a_member": forget_member, "forget_nobody_and_the_household": forget_nobody,
        "anticipation_wake_alarm": anticipation_wake, "music_did_not_start_logs_the_fallback": music_did_not_start,
        "spoken_shift_a_repeating_alarm": spoken_shift_a_repeating_alarm, "spoken_shift_undone": spoken_shift_undone,
        "spoken_shift_a_one_off": spoken_shift_a_one_off, "spoken_shift_with_no_alarm_makes_one": spoken_shift_with_no_alarm,
        "rest_create": create_member, "rest_create_at_the_panel": create_panel, "rest_create_bad_time": create_bad_time,
        "rest_edit": edit, "rest_edit_disable": edit_disable, "rest_edit_someone_elses": edit_someone_elses,
        "rest_edit_household_by_a_member": edit_household_by_member,
        "rest_delete": delete, "rest_delete_private_by_the_panel": delete_private_by_panel,
        "rest_snooze": snooze, "rest_snooze_by_the_panel": snooze_by_panel, "rest_snooze_not_ringing": snooze_not_ringing,
        "rest_dismiss": dismiss, "rest_dismiss_by_the_panel": dismiss_by_panel,
        "rest_dismiss_by_another_member": dismiss_by_another_member,
        "rest_override": override, "rest_override_clear": override_clear,
        "spoken_set": spoken_set, "spoken_set_repeating": spoken_set_repeating, "spoken_snooze": spoken_snooze,
        "spoken_off_while_ringing": spoken_off_while_ringing, "spoken_cancel": spoken_cancel,
        "spoken_turn_off_then_on": spoken_turn_off_then_on, "spoken_cancel_all": spoken_cancel_all,
        "spoken_guest_away_from_the_panel": spoken_guest_off_panel,
        "brain_set": brain_set, "brain_refuses_a_cancel": brain_refuses_a_cancel,
        "engine_rings_and_stops_by_itself": engine_rings_and_stops_by_itself,
    }


# ── timers ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
T0 = 1_800_000_000.0


class _TimerWorld:
    def __init__(self, log: list) -> None:
        import skybridge_service as sky
        import write_receipts

        self.sky, self.log, self.clock = sky, log, {"now": T0}
        import time as _real

        _set(sky, "time", types.SimpleNamespace(time=lambda: self.clock["now"], monotonic=_real.monotonic, sleep=_real.sleep))
        _set(sky, "uuid", _Ids("t"))
        store = sky._TimerStore()
        store._loaded = True
        store._save = lambda: log.append(["save", self.state()])                    # the snapshot it WOULD write, not written
        store._arm = lambda timer: log.append(["arm", timer["timer_id"]])
        store._disarm = lambda timer_id: log.append(["disarm", timer_id])
        _set(sky, "_timers", store)
        _set(sky, "_spawn", lambda coro: (coro.close(), log.append(["revoke_queued"]))[1])
        _set(write_receipts, "record", lambda *a, **k: log.append(["receipt", *_plain(a)]))
        self.store = store

    def state(self) -> dict:
        return _plain({owner: sorted(timers, key=lambda t: t["timer_id"]) for owner, timers in sorted(self.store._by_owner.items())})


def timer_scenarios() -> dict:
    def say(w, text, owner=ANN):
        intent = w.sky.classify_skybridge_intent(text)
        out = w.sky._resolve_timer(intent, owner)
        return {k: v for k, v in out.items() if k in ("spoken_summary", "timer_cancelled_id", "timer_cancelled_ids", "timer_adjusted_id")}

    def tid(n=1):
        return f"t{n:011d}"

    def create(w):
        return say(w, "set a pasta timer for 10 minutes")

    def create_direct(w):
        out = w.sky.create_timer_direct(ANN, minutes=3, label="Eggs")
        return out["spoken_summary"]

    def create_for_a_guest(w):
        return w.sky.create_timer_direct("", minutes=1)["spoken_summary"]

    def create_from_a_panel_voice_turn(w):
        w.sky.bind_timer_panel("panel-zed")
        try:
            return say(w, "set a timer for 5 minutes")
        finally:
            w.sky.bind_timer_panel("")

    def add_time(w):
        create(w)
        return say(w, "add 5 minutes to the pasta timer")

    def take_time_off(w):
        create(w)
        return say(w, "take 2 minutes off the pasta timer")

    def cancel(w):
        create(w)
        return say(w, "cancel the pasta timer")

    def cancel_all(w):
        create(w)
        say(w, "set an egg timer for 3 minutes")
        return say(w, "cancel all timers")

    def cancel_ambiguous(w):
        create(w)
        say(w, "set an egg timer for 3 minutes")
        return say(w, "cancel the timer")

    def cancel_none(w):
        return say(w, "cancel the timer")

    def status(w):
        create(w)
        return say(w, "how long is left on the timer")

    def panel_tap_cancels_a_running_timer(w):
        create(w)
        return bool(w.sky.cancel_any_timer(tid()))

    def panel_dismisses_a_ringing_timer(w):
        create(w)
        w.clock["now"] = T0 + 601                                           # it has ended: ringing, inside the grace
        return [[t["timer_id"], t["status"]] for t in w.sky.all_active_timers()], bool(w.sky.cancel_any_timer(tid()))

    def member_cancels_their_own_by_id(w):
        create(w)
        return bool(w.sky.cancel_timer_by_id(ANN, tid()))

    def member_cannot_cancel_anothers_by_id(w):
        create(w)
        return bool(w.sky.cancel_timer_by_id(BOB, tid()))

    def a_finished_timer_leaves_after_the_grace(w):
        create(w)
        w.clock["now"] = T0 + 600 + w.sky.TIMER_RING_GRACE_S + 1
        return w.sky.all_active_timers()

    def done(how, *, cancel_first=False, twice=False):
        """The timer ends and the one task that announces it runs (``_ring_when_done``: the only path that sets ``announced`` /
        ``voice_failed``): the queue takes the line, refuses it, or it is quiet hours."""
        def run(w):
            import db_compat
            import voice_announce

            w.sky.bind_timer_panel("panel-zed")
            try:
                say(w, "set a pasta timer for 10 minutes")
            finally:
                w.sky.bind_timer_panel("")
            w.clock["now"] = T0 + 601                                       # it has ended: inside the ring grace

            async def enqueue(db, **kw):
                w.log.append(["announce", _plain({k: kw[k] for k in sorted(kw)})])
                if how == "failed":
                    raise RuntimeError("queue down")

            @contextlib.asynccontextmanager
            async def no_db():
                yield None

            _set(voice_announce, "enqueue_announcement", enqueue)
            _set(db_compat, "get_compat_db", no_db)
            _set(w.sky, "_timer_quiet_now", lambda: how == "quiet")
            if cancel_first:
                w.sky.cancel_any_timer(tid())
            for _ in range(2 if twice else 1):
                asyncio.run(w.sky._ring_when_done(w.store, tid()))
            return [[t["timer_id"], bool(t.get("announced")), bool(t.get("voice_failed"))]
                    for timers in w.store._by_owner.values() for t in timers]
        return run

    return {
        "timer_done_is_announced": done("queued"), "timer_done_when_the_queue_fails": done("failed"),
        "timer_done_in_quiet_hours": done("quiet"), "timer_done_is_announced_once": done("queued", twice=True),
        "timer_done_after_a_cancel_says_nothing": done("queued", cancel_first=True),
        "timer_create": create, "timer_create_direct": create_direct, "timer_create_for_a_guest": create_for_a_guest,
        "timer_create_from_a_panel_voice_turn": create_from_a_panel_voice_turn,
        "timer_add_time": add_time, "timer_take_time_off": take_time_off,
        "timer_cancel": cancel, "timer_cancel_all": cancel_all, "timer_cancel_ambiguous": cancel_ambiguous,
        "timer_cancel_none": cancel_none, "timer_status": status,
        "timer_panel_tap_cancels_a_running_timer": panel_tap_cancels_a_running_timer,
        "timer_panel_dismisses_a_ringing_timer": panel_dismisses_a_ringing_timer,
        "timer_member_cancels_their_own_by_id": member_cancels_their_own_by_id,
        "timer_member_cannot_cancel_anothers_by_id": member_cannot_cancel_anothers_by_id,
        "timer_a_finished_timer_leaves_after_the_grace": a_finished_timer_leaves_after_the_grace,
    }


def _outcome(log, call):
    try:
        log.append(["returned", _plain(call())])
    except Exception as exc:  # noqa: BLE001 - a refusal is part of the record
        # an HTTP / alarm error with its own words; any other exception by its TYPE only (the interpreter's wording differs
        # between Python versions, and it is the fault, not its wording, that main and this branch must share)
        detail = str(getattr(exc, "detail", None) or getattr(exc, "reason", None) or type(exc).__name__)
        log.append(["raised", getattr(exc, "status_code", getattr(exc, "status", None)), detail])


def _isolated() -> None:
    """Refuse to run outside the test configuration: the timer store and the logs default under the live ``~/.zoe``.
    ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_TIMER_STORE", "ZOE_LOG_DIR", "MEMPALACE_DATA_DIR"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"alarm and timer scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")


def run_one(name: str, root: Path | None = None) -> list:
    """One scenario's record, from reset state."""
    _isolated()
    root = Path(root or Path(__file__).resolve().parents[1])
    log: list = []
    alarm_calls = alarm_scenarios()
    try:
        if name in alarm_calls:
            world = _AlarmWorld(root, log)
            _outcome(log, lambda: asyncio.run(alarm_calls[name](world)))
            log.append(["rows", _plain(asyncio.run(world.rows()))])
        else:
            world = _TimerWorld(log)
            _outcome(log, lambda: timer_scenarios()[name](world))
            log.append(["store", world.state()])
    finally:
        _restore()
    return _plain(log)


def run_all(root: Path | None = None) -> dict:
    return {name: run_one(name, root) for name in [*alarm_scenarios(), *timer_scenarios()]}


if __name__ == "__main__":
    sys.path[:0] = [sys.argv[1], sys.argv[1].rstrip("/") + "/tests"]
    import conftest  # noqa: F401 - that tree's test configuration: every live store path pinned to a temporary directory
    print(json.dumps(run_all(Path(sys.argv[1])), indent=1, sort_keys=True))
