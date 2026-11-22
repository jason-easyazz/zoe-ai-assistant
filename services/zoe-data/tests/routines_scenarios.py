"""Scenarios over the routines store and its conversation tier, run the same way on ``main`` and on a branch
(``tests/test_routines_same_as_main.py``).

Each scenario starts from an empty, migrated SQLite store at a fixed household morning, drives the store's entry points
(``routines_store``) or the tier's (``routines_tier.tier``), and records what each call returned or refused and the four
tables as they stand at the end, every column of every row (read from the schema). Ids are random: each is replaced by a label in order of first appearance.

Invented people and words only. Refuses to run outside the test configuration (``tests/conftest.py``).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import tempfile
from datetime import timedelta

ALEX, SAM, RILEY = "m-alex", "m-sam", "m-riley"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?<=:o)[0-9a-f]{12}\b")      # a routine / step id; a one-off line's random tail
_UNDO: list = []
#: the four tables, recorded WHOLE: every column, read from the migrated store's own schema (``PRAGMA table_info``), never listed
#: by hand, so a column a later migration adds is recorded too and a value written to any column is pinned.
TABLES = ("routines", "routine_steps", "routine_days", "routine_day_steps")


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
        self.db = support.Sqlite(path)

        @contextlib.asynccontextmanager
        async def fake_db():
            yield self.db

        _set(db_compat, "get_compat_db", fake_db)
        _set(rs, "_now_utc", lambda: support.THU)
        self._env = {k: os.environ.get(k) for k in ("ZOE_TIMEZONE", memory_forgotten.SALT_ENV, "ZOE_ROUTINES", "ZOE_ROUTINE_VOICE")}
        os.environ.update({"ZOE_TIMEZONE": "UTC", memory_forgotten.SALT_ENV: support.LEDGER_SALT, "ZOE_ROUTINES": "on",
                           "ZOE_ROUTINE_VOICE": "on"})
        memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
        import erase_watermark

        erase_watermark.set_backend(erase_watermark.MemoryBackend())      # Postgres in production; in process, as in conftest
        memory_tombstones.clear_all()
        rs.reset_walks()
        user_erase_gate.reset()
        self.today = support.THU.date()

    def close(self) -> None:
        import memory_forgotten
        import memory_tombstones
        import user_erase_gate

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

    async def call(self, label: str, awaitable):
        try:
            out = await awaitable
        except self.rs.StoreError as exc:
            self.log.append([label, "refused", exc.code, exc.status])
            return None
        self.log.append([label, "returned", out])
        return out

    async def say(self, text: str, who: str, session: str = "s1", **ctx):
        import routines_tier

        res = await routines_tier.tier(text, "panel-1", session, channel="voice",      # voice: the panel device; the member is the scope
                                       extra_ctx={"panel_id": "panel-1", "scope_user": who, "speaker_verified": True, **ctx})
        self.log.append(["say", text, None if res is None else getattr(res, "reply", str(res))])
        return res

    async def morning(self, owner=ALEX, *, by=None, visibility="personal", steps=("brush teeth", "pack the bag")):
        return await self.call("create", self.rs.create_routine(owner, "Morning", "waking", created_by=by, visibility=visibility,
                                                                steps=steps))

    async def day(self, viewer, routine):
        """Today's copy as ``viewer`` sees it, or a recorded "no day copy" (and a scenario that needs one stops there)."""
        d = await self.rs.day_copy(viewer, routine["id"]) if routine else None
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
        rows = [[t, *row] for t in TABLES for row in self.db.q(f"SELECT {', '.join(self.columns(t))} FROM {t} ORDER BY rowid")]
        text = json.dumps(self.log + rows, default=str, sort_keys=True)
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
    w.log.append(["named", await w.rs.note_named("s1", ALEX, d["steps"][0]["id"])])
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
    w.log.append(["named", await w.rs.note_named("s1", ALEX, d["steps"][0]["id"])])
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
    w.log.append(["erased", await w.rs.erase_entity(ALEX, re.compile(r"\bteeth\b", re.I), strict=True)])
    w.log.append(["erased-nothing", await w.rs.erase_entity(ALEX, re.compile(r"\bquince\b", re.I), strict=True)])


async def _forget_user(w):
    await w.morning()
    await w.morning(RILEY, by=ALEX)
    w.log.append(["forgot", await w.rs.forget_user(ALEX)])


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
