"""Every notes and journal entry point that writes, run against a recording store: what it reads, what it WRITES (statement and
values), which hooks it fires, the receipts it records, what it returns, and the rows afterwards.

Used twice: ``python3 tests/notes_journal_scenarios.py <a zoe-data tree> <out.json>`` writes the record of THAT tree (run
against an export of ``main`` to make ``tests/fixtures/notes_journal_as_on_main.json``), and
``test_notes_journal_same_as_main.py`` runs the same scenarios in-process and compares. It imports nothing the one-door or the
kernel-door change added (the recording handles are ``calendar_scenarios``'), so it runs unchanged on ``main``.

WHERE THEY LIVE. ``notes`` (``user_id`` = the author; ``visibility`` personal or family) and ``journal_entries`` (``user_id`` =
the writer; always personal). Here: one SQLite file with both tables in the 0001 shape, reached through the pooled ``?`` handle
(the screen, the brain's tools, the spoken path) and a raw asyncpg-shaped connection (an accepted suggestion). An accepted
suggestion is run twice: its action alone (``_execute_action``, on a connection that commits each statement) and through
``execute_suggestion``, which owns the transaction: on ``_Tx``, whose statements wait for the transaction's end and whose
begin, commit and rollback are recorded. ``pending_suggestions`` holds the offers, in the minimal shape that path reads.

Invented people and words only. Ids are counted, not random, so the record is the same on every run.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

from calendar_scenarios import STORE_NOW, _Pooled, _Raw, _plain, _restore, _set

ANN, BOB = "ann-0001", "bob-0002"
WORD = "womble"
# the column defaults are the frozen store clock (``STORE_NOW``): every stored stamp is a real value in the record
DDL = f"""
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE notes (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT, content TEXT NOT NULL, category TEXT DEFAULT 'general',
    tags TEXT, visibility TEXT NOT NULL DEFAULT 'personal', deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '{STORE_NOW}', updated_at TEXT NOT NULL DEFAULT '{STORE_NOW}');
CREATE TABLE journal_entries (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT, content TEXT NOT NULL, mood TEXT,
    mood_score INTEGER, tags TEXT, weather TEXT, location TEXT, photos TEXT, privacy_level TEXT DEFAULT 'personal',
    visibility TEXT NOT NULL DEFAULT 'personal', deleted INTEGER DEFAULT 0, person_id TEXT,
    created_at TEXT NOT NULL DEFAULT '{STORE_NOW}', updated_at TEXT NOT NULL DEFAULT '{STORE_NOW}');
INSERT INTO users (id, name) VALUES ('ann-0001', 'Member A'), ('bob-0002', 'Member B');
INSERT INTO notes (id, user_id, title, content, visibility) VALUES
    ('note-ann', 'ann-0001', 'gribble', 'snark', 'personal'), ('note-ann-family', 'ann-0001', 'quango', 'zorb', 'family'),
    ('note-bob', 'bob-0002', 'plover', 'fed', 'personal');
INSERT INTO journal_entries (id, user_id, title, content) VALUES
    ('entry-ann', 'ann-0001', 'gribble', 'snark'), ('entry-bob', 'bob-0002', 'plover', 'fed');
CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, action_type TEXT NOT NULL, pre_filled_slots TEXT,
    resolved INTEGER NOT NULL DEFAULT 0);
INSERT INTO pending_suggestions (id, user_id, action_type, pre_filled_slots) VALUES
    ('sugg-ann', 'ann-0001', 'note_create', '{{"title": "womble", "content": "bring the womble"}}');
"""
TABLES = ("notes", "journal_entries", "pending_suggestions")


class _Tx(_Raw):
    """The accepted suggestion's own connection (``execute_suggestion`` acquires it and opens ``transaction()`` on it): outside
    a transaction each statement commits; inside one nothing commits until the block ends, then ``commit`` (or, when the block
    raised, ``rollback``) is recorded and applied. ``ended`` says how the last transaction ended."""

    def __init__(self, con, log) -> None:
        super().__init__(con, log)
        self.depth, self.ended = 0, None

    async def execute(self, sql, *args):
        if not self.depth:
            return await super().execute(sql, *args)
        cur = self._run(sql, args)
        verb = sql.split()[0].upper()
        return f"INSERT 0 {cur.rowcount}" if verb == "INSERT" else f"{verb} {cur.rowcount}"

    def transaction(self):
        @contextlib.asynccontextmanager
        async def block():
            self.depth += 1
            self.log.append(["pg_begin"])
            try:
                yield
            except BaseException:
                self.depth -= 1
                self.con.rollback()
                self.ended = "rolled_back"
                self.log.append(["pg_rollback"])
                raise
            self.depth -= 1
            self.con.commit()
            self.ended = "committed"
            self.log.append(["pg_commit"])
        return block()


class _TxPool:
    """``get_pool()`` for ``execute_suggestion``: ``acquire()`` hands out the world's one ``_Tx``."""

    def __init__(self, world) -> None:
        self.world = world

    def acquire(self):
        @contextlib.asynccontextmanager
        async def held():
            yield self.world.tx
        return held()


class World:
    def __init__(self, log: list) -> None:
        import anticipation
        import database
        import intent_router
        import mcp_server
        import pending_suggestions
        import person_extractor
        import person_extractor_llm
        import write_receipts
        from push import broadcaster
        from routers import journal, notes

        self.notes, self.journal, self.mcp, self.router, self.sugg, self.log = notes, journal, mcp_server, intent_router, pending_suggestions, log
        self.con = sqlite3.connect(str(Path(tempfile.mkdtemp(prefix="nj")) / "z.db"), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.create_function("NOW", 0, lambda: "<now()>")
        self.con.executescript(DDL)
        self.con.commit()
        self.db, self.pg, self.tx = _Pooled(self.con, log), _Raw(self.con, log), _Tx(self.con, log)

        def rec(name, pick=lambda *a, **k: [list(a), k]):
            async def fake(*a, **k):
                log.append([name, *_plain(pick(*a, **k))])
            return fake

        counted = iter(range(1, 10_000))
        _set(uuid, "uuid4", lambda: uuid.UUID(int=next(counted)))
        _set(pending_suggestions, "get_pool", lambda: _TxPool(self))
        _set(anticipation, "mode", lambda: "off")                                # an offer of the anticipation lane is not in here
        _set(broadcaster, "broadcast", rec("broadcast", lambda channel, event, data, **k: [channel, event, data, k]))
        _set(mcp_server, "_notify_ui", rec("notify_ui"))
        _set(intent_router, "_notify_ui_channel", rec("notify_ui_channel"))
        _set(write_receipts, "record", lambda *a, **k: log.append(["receipt", *_plain(a)]))
        for module in (notes, journal):
            _set(module, "require_feature_access", rec("feature", lambda _db, user, **k: [user.get("user_id"), k]))
        _set(notes, "_store_note_memory", rec("memory", lambda _db, user_id, note, action: [user_id, note, action]))
        _set(journal, "_store_journal_memory", rec("memory", lambda _db, user_id, entry, action: [user_id, entry, action]))
        _set(person_extractor, "process_text", rec("people_extract", lambda text, **k: [text, k]))
        _set(person_extractor_llm, "process_text_llm", rec("people_extract_llm", lambda text, **k: [text, k]))

        async def actor(db, name, args, ctx):
            return {"user_id": (ctx or {}).get("user_id", ANN), "role": "user"}

        _set(mcp_server, "_resolve_mcp_actor", actor)

        @contextlib.asynccontextmanager
        async def pooled():
            yield self.db

        _set(database, "get_db_ctx", pooled)

    def rows(self) -> dict:
        return {table: [{k: v for k, v in dict(r).items() if v is not None}
                        for r in self.con.execute(f"SELECT * FROM {table} ORDER BY id")] for table in TABLES}


def _member(uid):
    return {"user_id": uid, "role": "user"}


def scenarios() -> dict:
    """name -> a coroutine function of the world; its return value is recorded."""
    from models import JournalEntryCreate, JournalEntryUpdate, NoteCreate, NoteUpdate

    def note(call, *args, who=ANN, **fields):
        async def run(w):
            fn = getattr(w.notes, call)
            body = [NoteCreate(**fields)] if call == "create_note" else [NoteUpdate(**fields)] if call != "delete_note" else []
            out = await fn(*args, *body, user=_member(who), db=w.db)
            await asyncio.sleep(0)                                        # the people extraction the create schedules: let it run
            return out
        return run

    def entry(call, *args, who=ANN, **fields):
        async def run(w):
            fn = getattr(w.journal, call)
            body = [JournalEntryCreate(**fields)] if call == "create_entry" else [JournalEntryUpdate(**fields)] if call != "delete_entry" else []
            return await fn(*args, *body, user=_member(who), db=w.db)
        return run

    def tool(name, who=ANN, **args):
        async def run(w):
            return await w.mcp._execute_tool(w.db, name, args, {"user_id": who})
        return run

    def said(kind, who=ANN, **slots):
        async def run(w):
            fn = getattr(w.router, f"_execute_{kind}_create_direct")
            return await fn(w.router.Intent(f"{kind}_create", slots), who)
        return run

    def suggestion(who=ANN, **slots):
        async def run(w):
            return await w.sugg._execute_action(w.pg, "note_create", slots, who)
        return run

    def accepted(suggestion_id, who=ANN):
        async def run(w):
            return await w.sugg.execute_suggestion(suggestion_id, who)
        return run

    return {
        # notes: the screen
        "note_rest_create": note("create_note", title=WORD, content="bring the womble", tags=["a", "b"]),
        "note_rest_create_for_the_house": note("create_note", content=WORD, visibility="family", category="home"),
        "note_rest_edit": note("update_note", "note-ann", title=WORD, tags=["c"]),
        "note_rest_edit_nothing": note("update_note", "note-ann"),
        "note_rest_patch": note("patch_note", "note-ann", content=WORD),
        "note_rest_edit_a_household_note_by_another_member": note("update_note", "note-ann-family", who=BOB, title=WORD),
        "note_rest_edit_a_private_note_by_another_member": note("update_note", "note-ann", who=BOB, title=WORD),
        "note_rest_edit_a_missing_note": note("update_note", "note-nope", title=WORD),
        "note_rest_delete": note("delete_note", "note-ann"),
        "note_rest_delete_a_household_note_by_another_member": note("delete_note", "note-ann-family", who=BOB),
        "note_rest_delete_a_missing_note": note("delete_note", "note-nope"),
        # notes: the brain's tools, the spoken path, an accepted suggestion
        "note_tool_create": tool("note_create", title=WORD, content="bring the womble", category="home"),
        "note_tool_update": tool("note_update", note_id="note-ann", title=WORD, content="x"),
        "note_tool_update_no_fields": tool("note_update", note_id="note-ann"),
        "note_tool_update_another_members_household_note": tool("note_update", BOB, note_id="note-ann-family", title=WORD),
        "note_tool_delete": tool("note_delete", note_id="note-ann"),
        "note_tool_delete_another_members_note": tool("note_delete", BOB, note_id="note-ann"),
        "note_spoken_create": said("note", content="bring the womble", title=WORD),
        "note_spoken_create_no_content": said("note", title=WORD),
        "note_suggestion_accepted": suggestion(title=WORD, content="bring the womble"),
        "note_suggestion_accepted_with_a_description_only": suggestion(description=WORD),
        "note_suggestion_accepted_through_the_accept": accepted("sugg-ann"),
        "note_suggestion_accepted_by_another_member": accepted("sugg-ann", who=BOB),
        # the journal: the screen, the brain's tool, the spoken path
        "journal_rest_create": entry("create_entry", title=WORD, content="a day of wombles", mood="calm", mood_score=7, tags=["a"],
                                     photos=["p1"]),
        "journal_rest_edit": entry("update_entry", "entry-ann", content=WORD, tags=["c"]),
        "journal_rest_edit_nothing": entry("update_entry", "entry-ann"),
        "journal_rest_edit_another_members_entry": entry("update_entry", "entry-ann", who=BOB, content=WORD),
        "journal_rest_delete": entry("delete_entry", "entry-ann"),
        "journal_rest_delete_another_members_entry": entry("delete_entry", "entry-ann", who=BOB),
        "journal_tool_create": tool("journal_create_entry", content="a day of wombles", title=WORD, mood="calm", mood_score=7, tags="a, b"),
        "journal_spoken_create": said("journal", content="a day of wombles", title=WORD, mood="calm", tags="a, b"),
        "journal_spoken_create_no_content": said("journal", title=WORD),
    }


def _isolated() -> None:
    """Refuse to run outside the test configuration: a note or an entry made reaches stores that default under the live
    ``~/.zoe`` (logs, the shadow's files). ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_LOG_DIR", "MEMPALACE_DATA_DIR", "ZOE_MEMORY_REJECT_LEDGER"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"notes and journal scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")


def run_one(name: str, call=None) -> list:
    """The record of one scenario, in a world of its own."""
    _isolated()
    call = call or scenarios()[name]
    log: list = []
    try:
        world = World(log)
        try:
            log.append(["returned", _plain(asyncio.run(call(world)))])
        except Exception as exc:  # noqa: BLE001 - a refusal is part of the record (an HTTP error by its words; any other by TYPE)
            log.append(["raised", getattr(exc, "status_code", None), str(getattr(exc, "detail", None) or type(exc).__name__)])
        log.append(["rows", _plain(world.rows())])
        world.con.close()
    finally:
        _restore()
    return log


def run_all() -> dict:
    _isolated()
    return {name: run_one(name, call) for name, call in scenarios().items()}


if __name__ == "__main__":
    sys.path[:0] = [sys.argv[1], sys.argv[1].rstrip("/") + "/tests"]
    import conftest  # noqa: F401 - that tree's test configuration: every live store path pinned to a temporary directory
    record = run_all()
    text = "{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(record[k], sort_keys=True)}" for k in sorted(record)) + "\n}\n"
    Path(sys.argv[2]).write_text(text, "utf-8")
