"""A turn's TOOL writes obey the member delete (round 10, item 1): a stalled turn of a deleted member never recreates their content.

THE CLASS RULE: every pooled statement passes ``db_pool.AsyncpgCompat`` (``execute`` / ``fetch`` / ``fetchrow`` / ``fetchval`` /
``executemany``), and there ``erase_watermark.store_write_guard`` refuses a write to a member-owned table
(``member_owned_forget.TURN_WRITE_TABLES``) made inside a turn whose member was erased at or after the turn BEGAN - whichever tool made
it (an intent handler, a fast tier, an MCP tool, a brain tool call through ``/api/system/intent-dispatch``, which is bound to the
member's oldest turn in flight). A STALE turn (older than ``TURN_STALE_S``, not waited for) is cancelled by the delete. A structural
test fails when a module writes a member-owned table on a connection that does not pass the wrapper.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

import db_pool
import erase_watermark as ew
import member_owned_forget as mof

SVC = Path(__file__).resolve().parents[1]
A, B = "member-a", "member-b"


class _Conn:
    """A stand-in asyncpg connection: records what reached it."""

    def __init__(self):
        self.ran: list[str] = []

    async def execute(self, sql, *args):
        self.ran.append(sql)
        return "INSERT 0 1"

    async def fetch(self, sql, *args):
        self.ran.append(sql)
        return []

    async def fetchrow(self, sql, *args):
        self.ran.append(sql)
        return None

    async def fetchval(self, sql, *args):
        self.ran.append(sql)
        return None

    async def executemany(self, sql, args):
        self.ran.append(sql)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    ew.set_backend(ew.MemoryBackend())
    ew._TURNS.clear()
    yield
    ew._TURNS.clear()
    ew.set_backend(None)


def _turn(member, *, ago_s=0.0):
    tok = ew.turn_open("s-" + member, member)
    ew._TURNS[tok]["began"] = ew._now() - dt.timedelta(seconds=ago_s)
    return tok


async def _erase(member):
    await ew.record(member)


def test_a_turn_of_an_erased_member_cannot_write_a_member_table_through_any_entry_point():
    async def go():
        conn = _Conn()
        db = db_pool.AsyncpgCompat(conn)
        tok = _turn(A, ago_s=700)                       # a STALE turn: the delete does not wait for it
        bound = ew.turn_bind(tok)
        try:
            await _erase(A)
            for call in (lambda: db.execute("INSERT INTO reminders (id, user_id, title) VALUES (?, ?, ?)", ("r1", A, "words")),
                         lambda: db.fetchrow("INSERT INTO lists (id, user_id) VALUES ($1, $2) RETURNING id", "l1", A),
                         lambda: db.fetchval("UPDATE list_items SET completed = 1 WHERE id = $1 RETURNING id", "i1"),
                         lambda: db.fetch("INSERT INTO events (id, user_id) VALUES ($1, $2) RETURNING id", "e1", A),
                         lambda: db.executemany("INSERT INTO notes (id, user_id) VALUES ($1, $2)", [("n1", A)])):
                with pytest.raises(ew.TurnWriteRefused):
                    await call()
            assert conn.ran == [], "no refused write reached the database"
            await db.execute("SELECT 1 FROM reminders WHERE user_id = ?", (A,))           # reads are never judged
            await db.execute("INSERT INTO evolution_proposals (id) VALUES (?)", ("p1",))  # not a member-owned table
            assert len(conn.ran) == 2
        finally:
            ew.turn_unbind(bound)
    asyncio.run(go())


def test_control_a_turn_that_began_after_the_erase_and_another_members_turn_write_normally():
    async def go():
        conn = _Conn()
        db = db_pool.AsyncpgCompat(conn)
        await _erase(A)
        await asyncio.sleep(0.01)
        for member in (A, B):                          # A's NEW turn (began after the mark), B's turn: both write
            bound = ew.turn_bind(_turn(member))
            try:
                await db.execute("INSERT INTO reminders (id, user_id) VALUES (?, ?)", ("r-" + member, member))
            finally:
                ew.turn_unbind(bound)
        await db.execute("INSERT INTO reminders (id, user_id) VALUES (?, ?)", ("r-none", A))   # no turn bound: not judged here
        assert len(conn.ran) == 3
    asyncio.run(go())


def test_a_brain_tool_call_is_bound_to_the_members_oldest_turn_in_flight():
    async def go():
        conn = _Conn()
        db = db_pool.AsyncpgCompat(conn)
        _turn(A, ago_s=700)
        _turn(A, ago_s=1)
        await _erase(A)

        async def dispatch():                          # what /api/system/intent-dispatch does around execute_intent
            bound = ew.bind_member_turn(A)
            try:
                await db.execute("INSERT INTO reminders (id, user_id) VALUES (?, ?)", ("r1", A))
            finally:
                ew.turn_unbind(bound)
        with pytest.raises(ew.TurnWriteRefused):
            await asyncio.ensure_future(dispatch())
        assert ew.bind_member_turn("member-nobody") is None, "no turn in flight: nothing bound"
        assert conn.ran == []
    asyncio.run(go())


def test_the_delete_cancels_a_stale_turn_of_the_member_and_waits_for_it_to_end(monkeypatch):
    monkeypatch.setattr(mof, "INFLIGHT_WAIT_S", 2.0)

    async def go():
        ended = asyncio.Event()

        async def stalled_turn():
            tok = ew.turn_open("s-a", A)
            ew._TURNS[tok]["began"] = ew._now() - dt.timedelta(seconds=ew.TURN_STALE_S + 60)
            try:
                await asyncio.sleep(3600)              # a brain call that never came back
            finally:
                ew.turn_close(tok)
                ended.set()
        other = asyncio.ensure_future(_b_turn())
        task = asyncio.ensure_future(stalled_turn())
        await asyncio.sleep(0.01)
        await mof._await_inflight_work(A)
        assert task.cancelled() and ended.is_set(), "the stale turn was cancelled and ran its finally"
        assert not other.done(), "another member's turn is untouched"
        other.cancel()

    async def _b_turn():
        tok = ew.turn_open("s-b", B)
        ew._TURNS[tok]["began"] = ew._now() - dt.timedelta(seconds=ew.TURN_STALE_S + 60)
        try:
            await asyncio.sleep(3600)
        finally:
            ew.turn_close(tok)
    asyncio.run(go())


def test_the_members_own_delete_inside_a_turn_is_never_judged_as_the_turns_write():
    import user_erase_gate

    class _Svc:
        @user_erase_gate.whole_member_erase
        async def delete_user(self, user_id):
            return ew._TURN_CTX.get()

    async def go():
        bound = ew.turn_bind(_turn(A))
        try:
            assert await _Svc().delete_user(A) is None
            assert ew._TURN_CTX.get() is not None, "the caller's binding is back after the delete"
        finally:
            ew.turn_unbind(bound)
    asyncio.run(go())


# ── structural ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

#: modules that write a member-owned table on a RAW pool connection (not the guarded wrapper), each with the check it runs instead
RAW_WRITERS = {
    "person_extractor.py": "process_text / apply_person_fact / _write_relationship refuse on the erase gate's snapshot",
}
_RAW = re.compile(r"\bpool\.acquire\(|asyncpg\.connect\(|aiosqlite\.connect\(")      # a pool connection, not an asyncio lock


def _member_writes(text: str) -> bool:
    return any(ew.write_target(m.group(0)) in mof.TURN_WRITE_TABLES
               for m in re.finditer(r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\"?[a-z_][a-z0-9_]*", text, re.IGNORECASE))


def _raw_member_writers() -> set:
    out = set()
    for p in SVC.rglob("*.py"):
        rel = p.relative_to(SVC)
        if {"tests", "alembic", ".venv", "node_modules"} & set(rel.parts):
            continue
        text = p.read_text(errors="ignore")
        if _RAW.search(text) and _member_writes(text):
            out.add(str(rel))
    return out - {"db_pool.py"}


def test_every_member_owned_write_passes_the_guarded_wrapper_or_is_listed_with_its_own_check():
    assert _member_writes("await conn.execute('INSERT INTO reminders (id) VALUES ($1)')"), "the scan can see a member write"
    assert _RAW.search("async with pool.acquire() as conn:"), "the scan can see a raw connection"
    found = _raw_member_writers()
    assert found - set(RAW_WRITERS) == set(), f"a member-owned write on a raw connection skips the turn guard: {sorted(found - set(RAW_WRITERS))}"
    assert set(RAW_WRITERS) <= found, f"no longer a raw writer: drop it from RAW_WRITERS {sorted(set(RAW_WRITERS) - found)}"


def test_every_write_entry_point_of_the_wrapper_runs_the_guard():
    for name in ("_do_execute", "fetch", "fetchrow", "fetchval", "executemany"):
        assert "_store_write_guard(" in inspect.getsource(getattr(db_pool.AsyncpgCompat, name)), name
    assert {"reminders", "lists", "list_items", "events", "notes", "people", "alarms"} <= mof.TURN_WRITE_TABLES


# ── review round (codex): the tool call's own turn, the caller's connection, the non-SQL store ─────────────────────────────────

def test_a_tool_call_keeps_its_own_turn_after_its_parent_turn_closes():
    """The brain's tool request outlives its parent turn (cancelled once stale, or simply finished): the tool call is a turn of its
    own (member + the parent's start), so its write is still judged - and the delete can wait for it or cancel it."""
    async def go():
        conn = _Conn()
        db = db_pool.AsyncpgCompat(conn)
        parent = _turn(A, ago_s=700)
        bound = ew.bind_member_turn(A)
        try:
            ew.turn_close(parent)                       # the parent is gone
            assert ew.stale_turn_tasks(A), "the tool call is itself a (stale) turn of A the delete can cancel"
            await _erase(A)
            with pytest.raises(ew.TurnWriteRefused):
                await db.execute("INSERT INTO reminders (id, user_id, title) VALUES (?, ?, ?)", ("r1", A, "words"))
        finally:
            ew.turn_unbind(bound)
        assert not ew._TURNS, "unbinding closes the tool call's own turn"
        assert conn.ran == []
    asyncio.run(go())


def test_the_mark_is_read_on_the_callers_own_connection(monkeypatch):
    """No second pooled connection: a busy pool can neither stall the write nor turn the check into an unreadable mark."""
    ew.set_backend(None)                                # the SQL default backend

    def no_second_connection():
        raise AssertionError("a second pooled connection was taken for the mark")
    monkeypatch.setattr(db_pool, "get_db_ctx", no_second_connection)
    mark = (ew._now() + dt.timedelta(seconds=1)).isoformat()

    class _MarkConn(_Conn):
        async def fetch(self, sql, *args):
            self.ran.append(sql)
            return [(mark,)] if "memory_forgotten" in sql else []

    async def go():
        conn = _MarkConn()
        db = db_pool.AsyncpgCompat(conn)
        bound = ew.turn_bind(_turn(A))
        try:
            with pytest.raises(ew.TurnWriteRefused):
                await db.execute("INSERT INTO reminders (id, user_id) VALUES (?, ?)", ("r1", A))
        finally:
            ew.turn_unbind(bound)
        assert [s for s in conn.ran if "INSERT" in s] == [] and any("memory_forgotten" in s for s in conn.ran)
    asyncio.run(go())


def test_a_turns_memory_write_for_an_erased_member_is_dropped_too(tmp_path):
    """MemPalace is not SQL: ``MemoryService.ingest`` applies the same turn rule (an MCP ``memory_add`` / a spoken "remember" in a
    turn whose member was erased since it began writes nothing). Control: the same ingest outside such a turn writes."""
    from memory_service import MemoryService

    def svc_with(writes):
        svc = MemoryService(data_dir=str(tmp_path / "scratch"))
        svc._write_row = lambda mem_id, text, md: writes.append(mem_id)

        async def _noop(**_kw):
            return None
        svc._append_audit = _noop
        svc._get_sync = lambda _id: None
        return svc

    async def go():
        writes: list = []
        bound = ew.turn_bind(_turn(A, ago_s=5))
        try:
            await _erase(A)
            assert await svc_with(writes).ingest("A fixture sentence about tea.", user_id=A, source="chat_regex") is None
        finally:
            ew.turn_unbind(bound)
        assert writes == []
        ref = await svc_with(writes).ingest("A fixture sentence about coffee.", user_id=B, source="chat_regex")
        assert ref is not None and writes == [ref.id], "control: another member's write lands"
    asyncio.run(go())


def test_a_cancelled_palace_write_holds_its_caller_until_the_worker_finishes(tmp_path):
    """The delete cancels a stale turn and waits for it to END: a MemPalace write already in the executor must finish first (the
    caller keeps the member's memory lock until then), so it can never land after the delete."""
    import threading

    from memory_service import MemoryService

    release, landed = threading.Event(), []

    def slow_write():
        release.wait(5)
        landed.append("row")

    async def go():
        svc = MemoryService(data_dir=str(tmp_path / "scratch"))
        task = asyncio.ensure_future(svc._run_write(slow_write))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.1)
        assert not task.done(), "the cancelled caller still waits for its worker"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert landed == ["row"], "the worker finished before the caller ended"
    asyncio.run(go())


def test_inside_a_transaction_the_mark_read_runs_in_a_savepoint_and_a_failed_read_refuses_the_write(monkeypatch):
    ew.set_backend(None)

    class _TxConn(_Conn):
        def __init__(self):
            super().__init__()
            self.savepoints = 0

        def is_in_transaction(self):
            return True

        def transaction(self):
            conn = self

            class _Sp:
                async def __aenter__(self):
                    conn.savepoints += 1

                async def __aexit__(self, *exc):
                    return False
            return _Sp()

        async def fetch(self, sql, *args):
            if "memory_forgotten" in sql:
                raise TimeoutError("statement timeout")
            return await super().fetch(sql, *args)

    async def go():
        conn = _TxConn()
        db = db_pool.AsyncpgCompat(conn)
        bound = ew.turn_bind(_turn(A))
        try:
            with pytest.raises(ew.TurnWriteRefused):
                await db.execute("INSERT INTO reminders (id, user_id) VALUES (?, ?)", ("r1", A))
        finally:
            ew.turn_unbind(bound)
        # the read ran in a SAVEPOINT (a failed read never aborts the caller's transaction), and the unreadable mark FAILS CLOSED:
        # the write is refused before it runs
        assert conn.savepoints == 1 and not any("INSERT" in s for s in conn.ran), "read in a savepoint; unreadable mark: refused"
    asyncio.run(go())


def test_a_read_still_cancels_at_once_and_a_cancelled_write_stays_cancelled_whatever_the_worker_does(tmp_path):
    """Only WRITES drain: a recall read (``_run_sync``) under a timeout returns at once. And once a write's caller is cancelled, the
    caller gets the cancellation even when the worker then fails (a best-effort caller must not catch that error and carry on)."""
    import threading

    from memory_service import MemoryService

    release = threading.Event()

    def slow_read():
        release.wait(5)
        return []

    def failing_write():
        release.wait(5)
        raise RuntimeError("palace hiccup")

    async def go():
        svc = MemoryService(data_dir=str(tmp_path / "scratch"))
        t0 = asyncio.get_running_loop().time()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(svc._run_sync(slow_read), timeout=0.1)
        assert asyncio.get_running_loop().time() - t0 < 2, "a read timeout is not held by its worker"
        task = asyncio.ensure_future(svc._run_write(failing_write))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
