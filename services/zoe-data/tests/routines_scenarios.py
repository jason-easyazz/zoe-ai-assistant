"""Scenarios over the routines store and its conversation tier, run the same way on ``main`` and on a branch
(``tests/test_routines_same_as_main.py``).

Each scenario starts from an empty, migrated SQLite store at a fixed household morning, drives the store's entry points
(``routines_store``), the tier's (``routines_tier.tier``, each turn opened with ``turn_begins`` as ``fast_tiers`` does) or the
forget cascade's own routines step, and records, AFTER EVERY CALL: what it returned or refused, the four tables, every column of
every row (read from the schema; ``["rows", "unchanged"]`` when no row moved since the last record), what the routines modules
logged, and after a spoken turn the walk state (the step named, the last said change, the lines listed). A value one call writes
is therefore pinned even when a later call overwrites it. DURING every call, in the order they happen, every SQL statement the
store sends (its text, whitespace-folded, and its parameters), every ``commit`` and where each transaction begins and ends (and
the advisory locks taken inside it): the connection behaves as production's ``db_pool.AsyncpgCompat`` does (a statement outside
a transaction commits itself, ``commit`` is a no-op, ``transaction`` is one real transaction), so a statement whose text changes,
or a commit moved, added or dropped, changes the record even when the reply and the rows do not. Nothing is masked: the clocks
are FROZEN (the household clock at
``routines_support.THU``, the walk clock ``_mono``) and the ids the store mints come from a fixed sequence (each id is then
replaced by a label in order of first appearance, so a record does not depend on how many ids a run mints before it).

Invented people and words only. Refuses to run outside the test configuration (``tests/conftest.py``).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import types
import uuid
from datetime import timedelta

ALEX, SAM, RILEY = "m-alex", "m-sam", "m-riley"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?<=:o)[0-9a-f]{12}\b")      # a routine / step id; a one-off line's random tail
_UNDO: list = []
#: the four tables, recorded WHOLE: every column, read from the migrated store's own schema (``PRAGMA table_info``), never listed
#: by hand, so a column a later migration adds is recorded too and a value written to any column is pinned.
TABLES = ("routines", "routine_steps", "routine_days", "routine_day_steps")
#: the loggers whose every record (INFO and up) is part of the record: the store's events and the tier's claims / failures
LOGGERS = ("routines_store", "routines_tier")


def _set(obj, name, value) -> None:
    _UNDO.append((obj, name, getattr(obj, name)))
    setattr(obj, name, value)


def _isolated() -> None:
    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_OS_SHADOW_DIR", "ZOE_LOG_DIR", "MEMPALACE_DATA_DIR"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"routines scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")


class _Stop(Exception):
    """The scenario could not go further; what it recorded so far is its record."""


class _Logged(logging.Handler):
    def __init__(self, log: list):
        super().__init__(logging.INFO)
        self.out = log

    def emit(self, record) -> None:
        self.out.append(["logged", record.name, record.levelname, record.getMessage()])


def _ids():
    """``uuid`` for the store with ``uuid4`` FROZEN to a fixed sequence (deterministic, still version-4 shaped): the ids, and the
    hashes of them the store logs, are the same on every run."""
    n = [0]

    def uuid4():
        n[0] += 1
        return uuid.UUID(bytes=hashlib.sha256(f"routines-scenario-id-{n[0]}".encode()).digest()[:16], version=4)

    return types.SimpleNamespace(uuid4=uuid4, UUID=uuid.UUID)


class _Connection:
    """The store's connection over the scenario's SQLite file, shaped and behaving as production's ``db_pool.AsyncpgCompat`` (what
    ``db_compat.get_compat_db`` yields on Postgres): ``execute`` is awaitable and an async context manager, a statement outside a
    transaction commits itself (asyncpg autocommits), ``commit`` does nothing, ``transaction`` is ONE real transaction (rolled back
    when the block raises) and the per-member advisory lock statement the store sends inside it runs (as a recorded no-op).
    Everything is appended to the scenario's log as it happens: ``["sql", text, params]``, ``["commit"]``, ``["begin"]``,
    ``["end"]`` / ``["rollback"]``."""

    def __init__(self, path: str, log: list):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.create_function("hashtextextended", 2, lambda text, seed: int(hashlib.sha256(str(text).encode()).hexdigest()[:8], 16))
        self.conn.create_function("pg_advisory_xact_lock", 1, lambda key: None)
        self.log = log

    def execute(self, sql, params=()):
        from db_pool import _Cursor, _ExecResult

        self.log.append(["sql", " ".join(str(sql).split()), list(params or ())])

        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params or ()))
            return _Cursor(cur.fetchall(), rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self) -> None:
        self.log.append(["commit"])

    @contextlib.asynccontextmanager
    async def transaction(self):
        self.log.append(["begin"])
        self.conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            self.log.append(["rollback"])
            raise
        self.conn.execute("COMMIT")
        self.log.append(["end"])

    def q(self, sql, params=()):
        """A read for the record itself (the tables, the schema): not logged."""
        return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]


class World:
    def __init__(self, directory: str):
        import db_compat
        import memory_forgotten
        import memory_tombstones
        import routines_store as rs
        import routines_support as support
        import user_erase_gate

        self.rs, self.log = rs, []
        path = os.path.join(directory, "zoe.db")
        support.migrate(path)
        self.db = _Connection(path, self.log)

        @contextlib.asynccontextmanager
        async def fake_db():
            yield self.db

        _set(db_compat, "get_compat_db", fake_db)
        _set(rs, "_now_utc", lambda: support.THU)
        _set(rs, "_mono", lambda: 1000.0)                     # the walk clock, frozen: no walk ages out because the lane is slow
        _set(rs, "uuid", _ids())
        self._env = {k: os.environ.get(k) for k in ("ZOE_TIMEZONE", memory_forgotten.SALT_ENV, "ZOE_ROUTINES", "ZOE_ROUTINE_VOICE")}
        os.environ.update({"ZOE_TIMEZONE": "UTC", memory_forgotten.SALT_ENV: support.LEDGER_SALT, "ZOE_ROUTINES": "on",
                           "ZOE_ROUTINE_VOICE": "on"})
        memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
        import erase_watermark

        erase_watermark.set_backend(erase_watermark.MemoryBackend())      # Postgres in production; in process, as in conftest
        memory_tombstones.clear_all()
        rs.reset_walks()
        self._reset_tier()
        user_erase_gate.reset()
        self.today = support.THU.date()
        self._last_rows = None
        self._handler = _Logged(self.log)
        self._levels = []
        for name in LOGGERS:
            lg = logging.getLogger(name)
            self._levels.append((lg, lg.level))
            lg.setLevel(logging.INFO)
            lg.addHandler(self._handler)

    @staticmethod
    def _reset_tier() -> None:
        import routines_tier

        routines_tier._LISTED.clear()                        # the tier's per-session state: no scenario inherits another's
        routines_tier._SEEN.clear()

    def close(self) -> None:
        import memory_forgotten
        import memory_tombstones
        import user_erase_gate

        for lg, level in self._levels:
            lg.removeHandler(self._handler)
            lg.setLevel(level)
        self._reset_tier()
        while _UNDO:
            obj, name, value = _UNDO.pop()
            setattr(obj, name, value)
        for key, value in self._env.items():
            os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
        self.rs.reset_walks()
        user_erase_gate.reset()
        memory_tombstones.clear_all()
        memory_forgotten.set_backend(None)
        import erase_watermark

        erase_watermark.set_backend(None)
        self.db.conn.close()

    def _rows(self) -> list:
        return [[t, *row] for t in TABLES for row in self.db.q(f"SELECT {', '.join(self.columns(t))} FROM {t} ORDER BY rowid")]

    def snap(self) -> None:
        """The four tables NOW, after the call just recorded (or "unchanged" when no row moved since the last snapshot)."""
        rows = self._rows()
        self.log.append(["rows", "unchanged" if rows == self._last_rows else rows])
        self._last_rows = rows

    async def call(self, label: str, awaitable):
        try:
            out = await awaitable
        except self.rs.StoreError as exc:
            self.log.append([label, "refused", exc.code, exc.status])
            self.snap()
            return None
        self.log.append([label, "returned", out])
        self.snap()
        return out

    async def note(self, label: str, awaitable):
        """A call recorded with what it returned (an eraser's count, a step's text), then the rows."""
        out = await awaitable
        self.log.append([label, out])
        self.snap()
        return out

    def walk(self, session: str) -> list:
        import routines_tier

        named, last = self.rs.walk_entry(session), self.rs.last_entry(session)
        return ["walk", None if named is None else [named.member, named.day_step_id, named.at], last,
                routines_tier._LISTED.get(session)]

    async def say(self, text: str, who: str, session: str = "s1", channel: str = "voice", **ctx):
        """One spoken (or chat) turn, as ``fast_tiers`` runs it: ``turn_begins`` first, then the tier. Recorded: the reply and the
        result's domain / intent / tier, the walk state after the turn, the rows."""
        import routines_tier

        routines_tier.turn_begins(session)
        if channel == "voice":                                # voice: the panel device; the member is the scope
            res = await routines_tier.tier(text, "panel-1", session, channel="voice",
                                           extra_ctx={"panel_id": "panel-1", "scope_user": who, "speaker_verified": True, **ctx})
        else:
            res = await routines_tier.tier(text, who, session, channel=channel, extra_ctx=ctx or None)
        self.log.append(["say", text, None if res is None else getattr(res, "reply", str(res)),
                         None if res is None else [getattr(res, k, None) for k in ("domain", "intent", "tier")]])
        self.log.append(self.walk(session))
        self.snap()
        return res

    def another_tier_answers(self, text: str, session: str = "s1") -> None:
        """A turn a tier ABOVE the routines tier answered: ``fast_tiers`` opens it (``turn_begins``) and the tier never sees it."""
        import routines_tier

        routines_tier.turn_begins(session)
        self.log.append(["another tier answered", text])
        self.log.append(self.walk(session))

    async def morning(self, owner=ALEX, *, by=None, visibility="personal", steps=("brush teeth", "pack the bag")):
        return await self.call("create", self.rs.create_routine(owner, "Morning", "waking", created_by=by, visibility=visibility,
                                                                steps=steps))

    async def day(self, viewer, routine):
        """Today's copy as ``viewer`` sees it, or a recorded "no day copy" (and a scenario that needs one stops there)."""
        d = await self.rs.day_copy(viewer, routine["id"]) if routine else None
        self.log.append(["day_copy", viewer, d])
        self.snap()
        if d is None or not d.get("steps"):
            self.log.append(["no day copy"])
            raise _Stop()
        return d

    def columns(self, table: str) -> list:
        """Every column of ``table`` in schema order, from the store itself."""
        cols = [row[1] for row in self.db.q(f"PRAGMA table_info({table})")]
        if not cols:
            raise RuntimeError(f"routines scenarios: no table {table} in the migrated store")
        return cols

    def record(self) -> list:
        text = json.dumps(self.log + [["rows at the end", self._rows()]], default=str, sort_keys=True)
        seen: dict = {}
        return json.loads(_UUID.sub(lambda m: seen.setdefault(m.group(0), f"<id{len(seen) + 1}>"), text))


# ── the store ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def _create_own(w):
    await w.morning()


async def _create_for_child(w):
    await w.morning(RILEY, by=ALEX)


async def _create_family(w):
    await w.morning(visibility="family")


async def _create_guest(w):
    await w.morning("guest")


async def _create_family_medicine(w):
    await w.morning(visibility="family", steps=(("take the tablets", "medicine"),))


def _add_step(who, owner=ALEX, by=None, visibility="personal"):
    async def run(w):
        r = await w.morning(owner, by=by, visibility=visibility)
        if r is None:
            return
        await w.call("add_step", w.rs.add_step(who, r["id"], "feed the cat"))
    return run


def _one_off(who, days=1, owner=ALEX, by=None, visibility="personal"):
    async def run(w):
        r = await w.morning(owner, by=by, visibility=visibility)
        if r is None:
            return
        await w.call("one_off", w.rs.add_one_off(who, r["id"], w.today + timedelta(days=days), "take the drill"))
    return run


def _tap(who, action="done", owner=ALEX, by=None, visibility="personal", verified=False, steps=("brush teeth", "pack the bag")):
    async def run(w):
        r = await w.morning(owner, by=by, visibility=visibility, steps=steps)
        d = await w.day(owner, r)
        await w.call("tap", w.rs.act_tapped(who, d["steps"][0]["id"], action, verified=verified))
    return run


async def _tap_then_undo(w):
    r = await w.morning()
    d = await w.day(ALEX, r)
    for action in ("done", "undo", "snooze", "done"):
        await w.call("tap", w.rs.act_tapped(ALEX, d["steps"][0]["id"], action, verified=False))


async def _said(w):
    r = await w.morning()
    d = await w.day(ALEX, r)
    await w.note("named", w.rs.note_named("s1", ALEX, d["steps"][0]["id"]))
    await w.call("said", w.rs.act_said("s1", ALEX, "done", verified=True))
    await w.call("said-again", w.rs.act_said("s1", ALEX, "done", verified=True))
    await w.call("said-other-member", w.rs.act_said("s1", SAM, "done", verified=True))


async def _said_nothing_named(w):
    await w.morning()
    await w.call("said", w.rs.act_said("s1", ALEX, "done", verified=True))


async def _said_then_undone(w):
    """ "No, I haven't" right after a said tick: that one line goes back (``undo_said``); another member's undo, and a second
    one, change nothing."""
    r = await w.morning()
    d = await w.day(ALEX, r)
    await w.note("named", w.rs.note_named("s1", ALEX, d["steps"][0]["id"]))
    await w.call("said", w.rs.act_said("s1", ALEX, "done", verified=True))
    await w.call("undo-other-member", w.rs.undo_said("s1", SAM, verified=True))
    await w.call("undo", w.rs.undo_said("s1", ALEX, verified=True))
    await w.call("undo-again", w.rs.undo_said("s1", ALEX, verified=True))


async def _erase_entity(w):
    """ "Forget <a thing>" (``erase_entity``, the forget cascade's call): in the member's OWN routines the step and the day line
    whose words name it go; another member's routine with the same words stays."""
    r = await w.morning()
    await w.day(ALEX, r)
    await w.morning(RILEY, by=ALEX)
    await w.note("erased", w.rs.erase_entity(ALEX, re.compile(r"\bteeth\b", re.I), strict=True))
    await w.note("erased-nothing", w.rs.erase_entity(ALEX, re.compile(r"\bquince\b", re.I), strict=True))


async def _forget_user(w):
    await w.morning()
    await w.morning(RILEY, by=ALEX)
    await w.note("forgot", w.rs.forget_user(ALEX))


# ── the conversation tier ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def _tier_walk(w):
    await w.morning()
    for text in ("what's next?", "done", "skip", "yep"):
        await w.say(text, ALEX)


async def _tier_yep_alone(w):
    await w.morning()
    await w.say("yep", ALEX)


async def _tier_one_off(w):
    await w.morning()
    await w.say("remind me to take the drill to work tomorrow", ALEX)
    await w.say("remind me at 7 to take the drill", ALEX)


async def _tier_other_member(w):
    await w.morning()
    await w.say("what's next?", SAM)
    await w.say("done", SAM)


async def _tier_start_done_then_spoken_undo(w):
    """ "Start my morning routine" (``_start``: the day copy, the first line named), "done", then the SPOKEN undo ("no, I
    haven't", ``_bare`` -> ``undo_said``: the line goes back and is named again), a second undo (nothing to undo), a routine the
    member has none of, and the start again."""
    await w.morning()
    for text in ("start my morning routine", "done", "no, I haven't", "untick that", "start my bedtime routine",
                 "start my morning routine"):
        await w.say(text, ALEX)


async def _tier_start_when_all_done(w):
    r = await w.morning(steps=("brush teeth",))
    d = await w.day(ALEX, r)
    await w.call("tap", w.rs.act_tapped(ALEX, d["steps"][0]["id"], "done", verified=False))
    await w.say("start my morning routine", ALEX)               # nothing left: ``_start`` answers with the summary


async def _tier_leaving_one_line(w):
    """A one-off said for today, then "I'm off to work" (``_leaving``: every routine's day copy, the take-with lines; ONE line is
    named through ``_speak_many`` -> ``note_named``), then "got it" ticks exactly that line."""
    await w.morning()
    for text in ("remind me to take the drill to work today", "I'm off to work", "got it"):
        await w.say(text, ALEX)


async def _tier_leaving_several_lines(w):
    """Two take-with lines: "I'm off" LISTS both and names none; "got it" is asked about ("Which one"), never ticked; a second
    "got it" changes nothing."""
    await w.morning()
    for text in ("remind me to take the drill to work today", "remind me to pack the umbrella to work today", "I'm off",
                 "got it", "got it"):
        await w.say(text, ALEX)


async def _tier_medicine_spoken(w):
    """A medicine line by voice: unverified "done" is refused and the line RE-NAMED (``_bare`` -> ``note_named``); verified, it is
    ticked; an unverified spoken undo is refused; then a verified one finds the window gone."""
    await w.morning(steps=(("take the tablets", "medicine"), "pack the bag"))
    await w.say("what's next?", ALEX, speaker_verified=False)
    await w.say("done", ALEX, speaker_verified=False)
    await w.say("done", ALEX)
    await w.say("no, I haven't", ALEX, speaker_verified=False)
    await w.say("no, I haven't", ALEX)


async def _tier_whats_left_snooze_stop(w):
    """ "What's left?" with several lines lists them and names none (``_speak_many``); "got it" is asked about; then "what's next?",
    "later" (a snooze), "stop" (the walk ends), and a "done" that ticks nothing."""
    await w.morning(steps=("brush teeth", "pack the bag", "feed the cat"))
    for text in ("what's left?", "got it", "what's next?", "later", "stop", "done"):
        await w.say(text, ALEX)


async def _tier_walk_ends_when_another_tier_answers(w):
    await w.morning()
    await w.say("what's next?", ALEX)
    w.another_tier_answers("what's the weather?")
    await w.say("done", ALEX)                                   # the walk closed at turn_begins: nothing is ticked


async def _tier_chat_walk(w):
    await w.morning()
    for text in ("what's next?", "done", "remind me to take the drill to work tomorrow"):
        await w.say(text, ALEX, channel="chat")


async def _tier_shadow_writes_nothing(w):
    """``ZOE_ROUTINE_VOICE=shadow``: the tier only logs what it would claim; no reply, no day copy, no row."""
    await w.morning()
    os.environ["ZOE_ROUTINE_VOICE"] = "shadow"                  # restored by ``World.close``
    for text in ("what's next?", "done", "remind me to take the drill to work tomorrow", "start my morning routine"):
        await w.say(text, ALEX)


async def _erase_entity_by_the_forget_cascade(w):
    """ "Forget <a thing>" through the cascade's OWN routines step (``memory_forget_cascade._clear``, its ``routines`` label only,
    the shadow step already done as ``cascade_forget`` does), so how the cascade calls ``erase_entity`` is pinned too."""
    import memory_forget_cascade as cascade

    r = await w.morning()
    await w.day(ALEX, r)
    await w.morning(RILEY, by=ALEX)
    res = cascade.CascadeResult()
    await w.note("cascade", cascade._clear(ALEX, cascade._name_re("teeth"), None, res, only=frozenset({"routines"}), shadow_done=True))
    w.log.append(["cascade failed", list(res.failed)])


async def _readers_by_viewer(w):
    """What each viewer is shown (the readers every writer and the card read through): a personal, a child's and a family routine
    read as the subject, another adult, the child, a guest and the bare panel."""
    mine = await w.morning()
    kids = await w.morning(RILEY, by=ALEX)
    house = await w.morning(visibility="family")
    for viewer in (ALEX, SAM, RILEY, "guest", None):
        await w.call(f"list as {viewer}", w.rs.list_routines(viewer))
        for label, r in (("mine", mine), ("kids", kids), ("house", house)):
            await w.call(f"get {label} as {viewer}", w.rs.get_routine(viewer, r["id"]))
            await w.call(f"day {label} as {viewer}", w.rs.day_copy(viewer, r["id"]))


def scenarios() -> dict:
    med = (("take the tablets", "medicine"), "pack the bag")
    return {
        "create_own": _create_own, "create_for_a_child": _create_for_child, "create_family": _create_family,
        "create_guest_refused": _create_guest, "create_family_medicine_refused": _create_family_medicine,
        "add_step_subject": _add_step(ALEX), "add_step_other_refused": _add_step(SAM),
        "add_step_child_subject_of_adults_routine": _add_step(RILEY, RILEY, ALEX), "add_step_adult_creator": _add_step(ALEX, RILEY, ALEX),
        "add_step_family_any_member": _add_step(SAM, visibility="family"), "add_step_family_guest_refused": _add_step("guest", visibility="family"),
        "one_off_subject": _one_off(ALEX), "one_off_other_refused": _one_off(SAM), "one_off_past_day_refused": _one_off(ALEX, -1),
        "one_off_child_subject_of_adults_routine": _one_off(RILEY, 1, RILEY, ALEX), "one_off_family_any_member": _one_off(SAM, visibility="family"),
        "tap_done_subject": _tap(ALEX), "tap_skip_subject": _tap(ALEX, "skip"), "tap_snooze_subject": _tap(ALEX, "snooze"),
        "tap_other_refused": _tap(SAM), "tap_child_own_routine_set_by_adult": _tap(RILEY, owner=RILEY, by=ALEX),
        "tap_adult_creator_on_childs_refused": _tap(ALEX, owner=RILEY, by=ALEX),
        "tap_family_by_the_panel": _tap(None, visibility="family"), "tap_family_by_a_member": _tap(SAM, visibility="family"),
        "tap_personal_by_the_panel_refused": _tap(None),
        "tap_medicine_unverified_refused": _tap(ALEX, steps=med), "tap_medicine_verified": _tap(ALEX, steps=med, verified=True),
        "tap_medicine_other_refused": _tap(SAM, steps=med, verified=True),
        "tap_done_undo_snooze_done": _tap_then_undo, "said_done_then_nothing_named": _said, "said_with_nothing_named": _said_nothing_named,
        "forget_user": _forget_user, "said_done_then_undone": _said_then_undone, "erase_entity_own_routines_only": _erase_entity,
        "tier_walk": _tier_walk, "tier_yep_alone": _tier_yep_alone, "tier_one_off_and_timed_reminder": _tier_one_off,
        "tier_other_member": _tier_other_member,
        "tier_start_done_then_spoken_undo": _tier_start_done_then_spoken_undo, "tier_start_when_all_done": _tier_start_when_all_done,
        "tier_leaving_one_line": _tier_leaving_one_line, "tier_leaving_several_lines": _tier_leaving_several_lines,
        "tier_medicine_spoken": _tier_medicine_spoken, "tier_whats_left_snooze_stop": _tier_whats_left_snooze_stop,
        "tier_walk_ends_when_another_tier_answers": _tier_walk_ends_when_another_tier_answers, "tier_chat_walk": _tier_chat_walk,
        "tier_shadow_writes_nothing": _tier_shadow_writes_nothing,
        "erase_entity_by_the_forget_cascade": _erase_entity_by_the_forget_cascade, "readers_by_viewer": _readers_by_viewer,
    }


def run(name: str) -> list:
    _isolated()

    async def go():
        with tempfile.TemporaryDirectory(prefix="routines-scn-") as directory:
            w = World(directory)
            try:
                with contextlib.suppress(_Stop):
                    await scenarios()[name](w)
                return w.record()
            finally:
                w.close()

    return asyncio.run(go())


def run_all() -> dict:
    return {name: run(name) for name in sorted(scenarios())}


if __name__ == "__main__":      # python3 <this file> <out.json>, with the CWD the zoe-data to record (an export of main)
    import sys

    sys.path[:0] = [".", "tests"]
    import conftest  # noqa: F401 - the CWD's test configuration: every live store path pinned to a temporary directory

    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import routines_scenarios

    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        out = routines_scenarios.run_all()
        fh.write("{\n" + ",\n".join(f"{json.dumps(k)}: {json.dumps(out[k], separators=(',', ':'), sort_keys=True)}" for k in sorted(out)) + "\n}\n")
