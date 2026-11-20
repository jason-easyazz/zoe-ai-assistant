"""Every contacts entry point that writes ``people`` for a person, run against a recording store: what it reads, what it WRITES
(statement and values), which hooks it fires, the receipts it records, what it returns, and the rows afterwards.

Used twice: ``python3 tests/people_scenarios.py <a zoe-data tree> <out.json>`` writes the record of THAT tree (run against an
export of ``main`` to make ``tests/fixtures/people_as_on_main.json``), and ``test_people_same_as_main.py`` runs the same
scenarios in-process and compares. It imports nothing the one-door or the kernel-door change added, so it runs unchanged on
``main``.

WHERE CONTACTS LIVE. ``people`` (``user_id`` = whose address book; ``visibility`` family = the household shares it, personal =
its owner's alone) and ``person_relationships`` (the edges). Here: one SQLite file with both tables, reached through the pooled
``?`` handle (the screen, the brain's tools, the spoken add, a spoken correction, the day review) and a raw asyncpg-shaped
connection (Skybridge, an accepted offer).

Invented people and words only. The clock is frozen where a statement carries it and ids are counted, so the record is the same
on every run.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

from calendar_scenarios import _plain as _plain_calendar, _restore, _set

ANN, BOB = "ann-0001", "bob-0002"
WORD = "Womble"
_CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)?")       # a wall-clock stamp inside a value
DDL = """
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT DEFAULT 'user');
CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, email TEXT, phone TEXT,
    birthday TEXT, notes TEXT, preferences TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0,
    circle TEXT NOT NULL DEFAULT 'acquaintance', health_score REAL NOT NULL DEFAULT 0.5, notification_count INTEGER NOT NULL DEFAULT 0,
    contact_count INTEGER NOT NULL DEFAULT 0, last_contacted_at TEXT, context TEXT NOT NULL DEFAULT 'personal',
    is_partial INTEGER NOT NULL DEFAULT 0, how_we_met TEXT, first_met_date TEXT, introduced_by_person_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE people_field_definitions (id TEXT PRIMARY KEY, field_key TEXT NOT NULL UNIQUE, label TEXT NOT NULL, field_type TEXT,
    required INTEGER DEFAULT 0, options_json TEXT, scope TEXT, sort_order INTEGER DEFAULT 100, visibility TEXT, is_active INTEGER DEFAULT 1);
CREATE TABLE people_field_values (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, field_key TEXT NOT NULL, value_json TEXT,
    updated_at TEXT, created_at TEXT, UNIQUE(person_id, field_key));
CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL, person_b_id TEXT NOT NULL,
    rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL, notes TEXT, updated_at TEXT,
    valid_to TEXT, created_at TEXT, valid_from TEXT, superseded_by TEXT, authority TEXT, origin TEXT, close_reason TEXT, turn_id TEXT,
    quote_span TEXT, speaker_rank INTEGER);
CREATE TABLE omi_day_candidates (id TEXT PRIMARY KEY, user_id TEXT, status TEXT, written_ref TEXT);
INSERT INTO users (id, name) VALUES ('ann-0001', 'Member A'), ('bob-0002', 'Member B');
INSERT INTO people (id, user_id, name, relationship, visibility, is_partial) VALUES
    ('p-ann-family', 'ann-0001', 'Gribble Snark', 'friend', 'family', 0),
    ('p-ann-private', 'ann-0001', 'Quango Zorb', NULL, 'personal', 0),
    ('p-ann-stub', 'ann-0001', 'Plover', NULL, 'personal', 1),
    ('p-bob-family', 'bob-0002', 'Fed Wibble', 'cousin', 'family', 0),
    ('p-bob-private', 'bob-0002', 'Yarrow Tind', 'boss', 'personal', 0);
INSERT INTO omi_day_candidates (id, user_id, status) VALUES ('cand_bar_0001', 'ann-0001', 'approving');
"""


def _mask(value):
    if isinstance(value, str):
        return _CLOCK.sub("<clock>", value)
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def _plain(value):
    return _mask(_plain_calendar(value))


class _Cur:
    """A cursor that is also what ``execute`` returns: awaitable AND an async context manager (both shapes are used)."""

    def __init__(self, cur):
        self._rows = cur.fetchall() if cur.description else []
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Pooled:
    def __init__(self, con, log) -> None:
        self.con, self.log = con, log

    def execute(self, sql, params=()):
        self.log.append(["sql", " ".join(sql.split()), _plain(list(params or ()))])
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        self.log.append(["commit"])
        self.con.commit()


class _Raw:
    def __init__(self, con, log) -> None:
        self.con, self.log = con, log

    def _run(self, sql, args):
        self.log.append(["pg", " ".join(sql.split()), _plain(list(args))])
        return self.con.execute(re.sub(r"\$(\d+)", r"?\1", sql), tuple(args))

    async def execute(self, sql, *args):
        cur = self._run(sql, args)
        verb = sql.split()[0].upper()
        self.con.commit()
        return f"INSERT 0 {cur.rowcount}" if verb == "INSERT" else f"{verb} {cur.rowcount}"

    async def fetch(self, sql, *args):
        return [dict(r) for r in self._run(sql, args).fetchall()]

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None


class World:
    def __init__(self, log: list) -> None:
        import contacts_conversation
        import correction_apply
        import database
        import db_compat
        import db_pool
        import intent_router
        import mcp_server
        import omi_review_apply
        import pending_suggestions
        import skybridge_service
        import write_receipts
        from push import broadcaster
        from routers import people, voice_tts

        self.people, self.mcp, self.router, self.sky, self.voice, self.sugg, self.fix, self.omi, self.log = (
            people, mcp_server, intent_router, skybridge_service, voice_tts, pending_suggestions, correction_apply, omi_review_apply, log)
        self.con = sqlite3.connect(str(Path(tempfile.mkdtemp(prefix="ppl")) / "z.db"), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.create_function("NOW", 0, lambda: "<now()>")
        self.con.executescript(DDL)
        self.con.commit()
        self.db, self.pg = _Pooled(self.con, log), _Raw(self.con, log)

        def rec(name, pick=lambda *a, **k: [list(a), k], result=None):
            async def fake(*a, **k):
                log.append([name, *_plain(pick(*a, **k))])
                return result
            return fake

        counted = iter(range(1, 10_000))
        _set(uuid, "uuid4", lambda: uuid.UUID(int=next(counted)))
        _set(broadcaster, "broadcast", rec("broadcast", lambda channel, event, data, **k: [channel, event, data, k]))
        _set(mcp_server, "_notify_ui", rec("notify_ui"))
        _set(intent_router, "_notify_ui_channel", rec("notify_ui_channel"))
        _set(write_receipts, "record", lambda *a, **k: log.append(["receipt", *_plain(a)]))
        _set(people, "require_feature_access", rec("feature", lambda _db, user, **k: [user.get("user_id"), k]))
        _set(people, "_store_person_memory", rec("memory", lambda _db, user_id, person, action: [user_id, person.get("id"), action]))
        _set(people, "_recalc_health", rec("health", lambda _db, person_id, user_id: [person_id, user_id]))
        _set(people, "_archive_person_mempalace", rec("archive", lambda person_id, user_id: [person_id, user_id]))
        _set(contacts_conversation, "refresh_person_mirror", rec("mirror", lambda *a, **k: [list(a)]))
        _set(skybridge_service, "_store_skybridge_memory_fact", rec("memory_fact", lambda text, **k: [text, k], result=True))

        async def show_people(intent, user_id, db):       # the read-back card (its own reads are Postgres-only): recorded, not run
            log.append(["show_people", intent.query, user_id])
            return {"handled": True, "cards": []}

        _set(skybridge_service, "_resolve_people", show_people)

        async def actor(db, name, args, ctx):
            return {"user_id": (ctx or {}).get("user_id", ANN), "role": "user"}

        _set(mcp_server, "_resolve_mcp_actor", actor)

        @contextlib.asynccontextmanager
        async def pooled():
            yield self.db

        for module in (database, db_compat, db_pool):
            if hasattr(module, "get_db_ctx"):
                _set(module, "get_db_ctx", pooled)
        _set(db_compat, "get_compat_db", pooled)

    def rows(self) -> dict:
        keep = ("id", "user_id", "name", "relationship", "birthday", "notes", "visibility", "deleted", "circle", "context", "is_partial",
                "updated_at")
        people = [{k: r[k] for k in keep if r[k] is not None} for r in self.con.execute("SELECT * FROM people ORDER BY id")]
        edges = [{k: v for k, v in dict(r).items() if v is not None} for r in self.con.execute("SELECT * FROM person_relationships ORDER BY id")]
        return {"people": people, "person_relationships": edges}


def _member(uid):
    return {"user_id": uid, "role": "user"}


def scenarios() -> dict:
    """name -> a coroutine function of the world; its return value is recorded."""
    from models import PersonCreate, PersonUpdate

    def screen(call, *args, who=ANN, **fields):
        async def run(w):
            fn = getattr(w.people, call)
            body = {"create_person": [PersonCreate(**fields)] if call == "create_person" else None,
                    "update_person": [PersonUpdate(**fields)] if call == "update_person" else None}.get(call) or []
            out = await fn(*args, *body, user=_member(who), db=w.db)
            await asyncio.sleep(0)                                        # a background archive the delete schedules: let it run
            return out
        return run

    def relation(call, *args, who=ANN, **body):
        async def run(w):
            return await getattr(w.people, call)(*args, body, user=_member(who), db=w.db)
        return run

    def tool(tool_name, who=ANN, **args):
        async def run(w):
            return await w.mcp._execute_tool(w.db, tool_name, args, {"user_id": who})
        return run

    def said_add(who=ANN, **slots):
        async def run(w):
            return await w.router._execute_people_create_direct(w.router.Intent("people_create", slots), who)
        return run

    async def said_rename(w):
        return await w.router._rename_person_row(w.db, ANN, "p-ann-stub", "Plover Tansy", "neighbour")

    def spoken_fact(about, who=ANN, **fields):
        async def run(w):
            intent = w.sky.SkybridgeIntent("people", "remember_fact", person_name=about, **fields)
            return await w.sky._resolve_people_remember_fact(intent, who, w.pg)
        return run

    def introduce(called, who=ANN):
        async def run(w):
            return await w.voice._handle_introduce_intent(called, who, "panel-zed", "sess-1", "turn-1", w.db)
        return run

    def offer(who=ANN, **slots):
        async def run(w):
            import os
            os.environ["ZOE_PERSON_SUGGEST_ENABLED"] = "1"
            try:
                return await w.sugg._execute_action(w.pg, "person_create", slots, who)
            finally:
                os.environ.pop("ZOE_PERSON_SUGGEST_ENABLED", None)
        return run

    async def offer_while_off(w):
        return await w.sugg._execute_action(w.pg, "person_create", {"name": WORD}, ANN)

    async def corrected_pet(w):
        return await w.fix._make_pet(w.db, ANN, "p-ann-family", "friend", "dog", "womble is a dog")

    async def corrected_name(w):
        return await w.fix._rename_contact(ANN, "", "Quango Zorb", "Quango Tansy")

    def reviewed(who=ANN, **payload):
        async def run(w):
            item = {"id": "cand_bar_0001", "kind": "person_relation", "day_local": "2099-06-16",
                    "payload": {"person_id": "p-ann-private", "name": "Quango Zorb", "relation": "neighbour", **payload}}
            return await w.omi.write_person_relation(who, item, "review")
        return run

    async def reviewed_undo(w):
        w.con.execute("UPDATE people SET relationship = 'neighbour' WHERE id = 'p-ann-private'")
        w.con.commit()
        return await w.omi.unwrite(ANN, [{"kind": "person_relation", "written_ref": "people:p-ann-private#relationship",
                                          "payload": {"relation": "neighbour"}}])

    return {
        # the screen
        "screen_create": screen("create_person", name=WORD, relationship="friend", notes="likes wombles", birthday="1990-01-02"),
        "screen_create_private": screen("create_person", name=WORD, visibility="personal", circle="inner", context="work"),
        "screen_update_own": screen("update_person", "p-ann-private", name=WORD, notes="likes wombles", visibility="family"),
        "screen_update_nothing": screen("update_person", "p-ann-private"),
        "screen_update_a_household_contact_by_another_member": screen("update_person", "p-ann-family", who=BOB, phone="555-0100"),
        "screen_update_a_private_contact_by_another_member": screen("update_person", "p-ann-private", who=BOB, phone="555-0100"),
        "screen_delete_own": screen("delete_person", "p-ann-private"),
        "screen_delete_a_household_contact_by_another_member": screen("delete_person", "p-ann-family", who=BOB),
        "screen_delete_a_private_contact_by_another_member": screen("delete_person", "p-ann-private", who=BOB),
        "screen_relation_with_a_new_stub": relation("add_relationship", "p-ann-family", other_person_name=WORD, create_partial=True,
                                                    rel_type="friend", rel_group="friends"),
        "screen_relation_without_a_name": relation("add_relationship", "p-ann-family", rel_type="friend"),
        # the brain's tools
        "tool_create": tool("people_create", name=WORD, relationship="friend", notes="likes wombles"),
        "tool_update": tool("people_update", person_id="p-ann-private", name=WORD, notes="likes wombles"),
        "tool_update_no_fields": tool("people_update", person_id="p-ann-private"),
        "tool_update_another_members_household_contact": tool("people_update", BOB, person_id="p-ann-family", name=WORD),
        "tool_delete": tool("people_delete", person_id="p-ann-private"),
        "tool_delete_another_members_household_contact": tool("people_delete", BOB, person_id="p-ann-family"),
        # the spoken paths
        "said_add": said_add(name=WORD, relationship="friend"),
        "said_add_no_name": said_add(relationship="friend"),
        "said_rename": said_rename,
        "spoken_fact_about_a_new_person": spoken_fact(WORD, fact_text="likes wombles"),
        "spoken_fact_about_their_own_contact": spoken_fact("Quango Zorb", fact_text="likes wombles", birthday="1990-01-02"),
        "spoken_fact_about_another_members_household_contact": spoken_fact("Fed Wibble", fact_text="likes wombles"),
        "spoken_fact_without_a_name": spoken_fact("", fact_text="likes wombles"),
        "introduced_a_new_person": introduce(WORD),
        "introduced_a_known_person": introduce("Quango"),
        "introduced_by_a_guest": introduce(WORD, "guest"),
        # an accepted "add X as a contact" offer
        "offer_accepted": offer(name=WORD, relationship="friend"),
        "offer_accepted_for_a_stub_with_a_relation": offer(name="Plover", relationship="neighbour"),
        "offer_accepted_for_a_stub": offer(name="Plover"),
        "offer_accepted_for_a_full_contact": offer(name="Quango Zorb"),
        "offer_accepted_with_a_bad_name": offer(name="him"),
        "offer_accepted_while_the_feature_is_off": offer_while_off,
        # a spoken correction, and the pendant day review
        "corrected_to_a_pet": corrected_pet,
        "corrected_name": corrected_name,
        "reviewed_relation": reviewed(),
        "reviewed_relation_on_a_household_contact": reviewed(person_id="p-ann-family"),
        "reviewed_relation_undo": reviewed_undo,
    }


def run_all() -> dict:
    out = {}
    for name, call in scenarios().items():
        log: list = []
        try:
            world = World(log)
            try:
                log.append(["returned", _plain(asyncio.run(call(world)))])
            except Exception as exc:  # noqa: BLE001 - a refusal is part of the record (an HTTP error by its words; any other by TYPE)
                log.append(["raised", getattr(exc, "status_code", getattr(exc, "status", None)),
                            str(getattr(exc, "detail", None) or type(exc).__name__)])
            log.append(["rows", _plain(world.rows())])
            world.con.close()
        finally:
            _restore()
        out[name] = log
    return out


if __name__ == "__main__":
    sys.path.insert(0, sys.argv[1])
    record = run_all()
    text = "{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(record[k], sort_keys=True)}" for k in sorted(record)) + "\n}\n"
    Path(sys.argv[2]).write_text(text, "utf-8")
