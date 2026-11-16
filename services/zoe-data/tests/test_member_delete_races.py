"""Member delete vs writers that started BEFORE it and finish AFTER it (the late-write rule, erase_watermark).

A turn in flight is registered (``erase_watermark.turn_open``): the delete waits for it (bounded, then fails closed), and each of the
turn's writes - the transcript row, the run state, the AG-UI event stream - re-checks the member's erase mark against the turn's start
right before AND right after its write. A panel's announcement claim re-checks after its commit, and the delete waits for claims in
flight. Deterministic: the delete is made to land at a hook inside the gap, never by sleeping. Synthetic ids only.
"""
from __future__ import annotations

import asyncio
import contextlib

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe

A, B = "ann", "bob"


class _Store:
    """The three chat tables as id sets; ``on_insert`` runs inside an INSERT (where a delete can land mid-write)."""

    def __init__(self):
        self.rows = {"chat_messages": set(), "openclaw_run_state": set(), "chat_ag_ui_runs": set()}
        self.on_insert = None

    async def execute(self, sql, params=()):
        s = " ".join(sql.split())
        for t in self.rows:
            if s.startswith(f"INSERT INTO {t}"):
                if self.on_insert:
                    await self.on_insert()
                self.rows[t].add(params[0])
            elif s.startswith(f"DELETE FROM {t} WHERE id = ?"):
                self.rows[t].discard(params[0])
        return None

    async def commit(self):
        pass


@pytest.fixture
def chat(monkeypatch):
    import db_pool
    import erase_watermark
    import routers.chat as chat_mod

    store = _Store()

    @contextlib.asynccontextmanager
    async def ctx():
        yield store

    async def no_touch(*_a, **_k):
        return None
    monkeypatch.setattr(chat_mod, "get_db_ctx", ctx)
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setattr(chat_mod, "_touch_chat_session", no_touch)
    yield chat_mod, store
    erase_watermark._TURNS.clear()


async def _writes(chat_mod, sid, uid):
    await chat_mod._save_chat_message(sid, "user", "my private words", user_id=uid)
    await chat_mod._record_run_state("run-1", sid, uid, mode="chat", status="completed", request_text="my private words",
                                     response_text="a reply")
    await chat_mod._persist_ag_ui_run(sid, "run-1", [{"type": "TEXT", "delta": "a reply"}])


def test_a_turn_that_began_before_the_delete_writes_nothing_after_it(chat):
    import erase_watermark

    chat_mod, store = chat

    async def go():
        erase_watermark.turn_open("s-1", A)                  # the turn is in flight (blocked at the brain, say)
        await erase_watermark.record(A)                      # ... the member delete completes ...
        await _writes(chat_mod, "s-1", A)                    # ... and the turn resumes and tries to persist
        erase_watermark.turn_close("s-1")
    asyncio.run(go())
    assert store.rows == {"chat_messages": set(), "openclaw_run_state": set(), "chat_ag_ui_runs": set()}


def test_a_delete_that_lands_during_a_turns_write_leaves_nothing_behind(chat):
    """The pre-check passed; the delete lands INSIDE the insert: the post-check removes the row."""
    import erase_watermark

    chat_mod, store = chat

    landed = []

    def land_for(uid):
        async def land():
            store.on_insert = None
            landed.append(uid)
            await erase_watermark.record(uid)
        return land

    async def go():
        # one member per write, so each write's PRE-check passes and the delete lands inside that write
        for sid, uid, write in (("s-1", "m-1", "msg"), ("s-2", "m-2", "run"), ("s-3", "m-3", "events")):
            erase_watermark.turn_open(sid, uid)
            store.on_insert = land_for(uid)
            if write == "msg":
                await chat_mod._save_chat_message(sid, "user", "my private words", user_id=uid)
            elif write == "run":
                await chat_mod._record_run_state("run-" + uid, sid, uid, mode="chat", status="completed", request_text="w")
            else:
                await chat_mod._persist_ag_ui_run(sid, "run-" + uid, [{"type": "TEXT"}])
            erase_watermark.turn_close(sid)
    asyncio.run(go())
    assert landed == ["m-1", "m-2", "m-3"], "each delete landed inside its write (the pre-check had passed)"
    assert store.rows == {"chat_messages": set(), "openclaw_run_state": set(), "chat_ag_ui_runs": set()}


def test_another_members_turn_and_a_turn_after_the_delete_still_write(chat):
    import erase_watermark

    chat_mod, store = chat

    async def go():
        await erase_watermark.record(A)
        erase_watermark.turn_open("s-b", B)
        await _writes(chat_mod, "s-b", B)
        erase_watermark.turn_close("s-b")
    asyncio.run(go())
    assert all(len(v) == 1 for v in store.rows.values()), store.rows


def test_the_delete_waits_for_the_members_turn_in_flight_and_fails_closed_after_the_bound(monkeypatch):
    import erase_watermark
    import member_owned_forget as mof

    monkeypatch.setattr(mof, "INFLIGHT_WAIT_S", 0.2)
    erase_watermark.turn_open("s-1", A)
    try:
        with pytest.raises(RuntimeError, match="in flight"):
            asyncio.run(mof._await_inflight_work(A))
        asyncio.run(mof._await_inflight_work(B))             # another member's delete is not held
    finally:
        erase_watermark.turn_close("s-1")
    asyncio.run(mof._await_inflight_work(A))                 # nothing in flight: returns


class _CommitHook:
    """An aiosqlite connection whose ``commit`` runs a hook first (the gap between the claim and the return)."""

    def __init__(self, inner, hook):
        self.inner, self.hook = inner, hook

    def execute(self, *a, **k):
        return self.inner.execute(*a, **k)

    async def commit(self):
        await self.inner.commit()
        if self.hook:
            h, self.hook = self.hook, None
            await h()


def test_a_delete_between_the_announcement_claim_and_its_return_hands_the_panel_nothing(tmp_path):
    import erase_watermark
    import voice_announce

    seen = {}

    async def go():
        raw = await aiosqlite.connect(str(tmp_path / "va.db"))
        raw.row_factory = aiosqlite.Row
        await raw.execute("CREATE TABLE voice_announcements (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT, message TEXT NOT NULL, "
                          "trigger_type TEXT, created_at TEXT, expires_at TEXT, delivered_at TEXT, delivered_to TEXT, played_at TEXT, "
                          "expired INTEGER DEFAULT 0)")
        await voice_announce.enqueue_announcement(raw, user_id=A, message="a line for the member", panel_id="p1", trigger_type="reminder")
        await voice_announce.enqueue_announcement(raw, user_id=B, message="a line for B", panel_id="p1", trigger_type="reminder")

        async def delete_lands():
            seen["in_flight"] = voice_announce.inflight_claims(A)   # the delete would wait for this claim
            await erase_watermark.record(A)
        got = await voice_announce.claim_announcements(_CommitHook(raw, delete_lands), panel_id="p1")
        seen["after"] = voice_announce.inflight_claims(A)
        await raw.close()
        return got
    got = asyncio.run(go())
    assert [g["text"] for g in got] == ["a line for B"], "the deleted member's line is not handed to the panel"
    assert seen == {"in_flight": 1, "after": 0}
