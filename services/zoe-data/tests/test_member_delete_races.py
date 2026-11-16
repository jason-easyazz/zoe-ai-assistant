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


# ── voice turns: the same rule, keyed by the turn's START, re-keyed when the speaker is resolved ───────────────────────────────

@pytest.fixture
def voice(monkeypatch, tmp_path, chat):
    """The voice turn's tail through its real helpers: the clip label (``_label_member_clip``) and the transcript save
    (``_schedule_voice_chat_save`` -> its background ``_persist`` -> ``_save_chat_message``) over the recording store."""
    import routers.chat as chat_mod
    import routers.voice_tts as vt
    import voice_clips

    _chat_mod, store = chat
    store.sessions = []

    async def ensure(session_id, user_id):
        store.sessions.append((session_id, user_id))
    monkeypatch.setattr(chat_mod, "_ensure_user_and_chat_session", ensure)
    monkeypatch.setenv("ZOE_VOICE_CLIP_VERDICT", "1")
    corpus = tmp_path / "voice-samples"
    corpus.mkdir()
    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(corpus))

    def save_clip(name):
        voice_clips.begin()
        p = corpus / f"{name}.wav"
        p.write_bytes(b"RIFF")
        voice_clips.note_saved(str(p))
        return p
    return vt, store, save_clip


async def _voice_tail(vt, member, session):
    await vt._schedule_voice_chat_save(session, "what's on tomorrow", "you have the dentist", member)
    for t in list(vt._BG_TASKS):
        await t


@pytest.mark.parametrize("when", ["before_resolution", "after_resolution"])
def test_a_delete_while_a_voice_turn_is_in_flight_leaves_nothing_of_the_speaker(voice, when):
    import erase_watermark

    vt, store, save_clip = voice

    async def go():
        bound = erase_watermark.voice_turn_begin()            # the turn starts; the speaker is not known yet
        try:
            clip = save_clip("turn-a")
            if when == "before_resolution":
                await erase_watermark.record(A)               # the delete lands before speaker identification finishes
                await vt._label_member_clip(A)
            else:
                await vt._label_member_clip(A)                # identified (and labelled) ...
                await erase_watermark.record(A)               # ... then the delete lands before the tail writes: its mark,
                import voice_clips                             # and its clip erase, which finds the clip labelled before it
                voice_clips.erase_member(A)
            await _voice_tail(vt, A, "vs-1")
            return clip
        finally:
            erase_watermark.voice_turn_end(bound)
    clip = asyncio.run(go())
    assert not clip.exists() and not clip.with_suffix(".verdict.json").exists(), "the clip is not kept, labelled or not"
    assert store.rows["chat_messages"] == set() and store.sessions == [], "no transcript row and no session row"


def test_a_delete_during_the_voice_turns_tail_writes_leaves_nothing_and_another_speakers_turn_is_untouched(voice):
    import erase_watermark

    vt, store, save_clip = voice

    async def land():
        store.on_insert = None
        await erase_watermark.record(A)                       # the delete lands INSIDE the first transcript insert

    async def turn(member, session, clip_name, hook=None):
        bound = erase_watermark.voice_turn_begin()
        try:
            save_clip(clip_name)
            await vt._label_member_clip(member)
            store.on_insert = hook
            await _voice_tail(vt, member, session)
        finally:
            erase_watermark.voice_turn_end(bound)

    async def go():
        await turn(A, "vs-a", "turn-a", hook=land)
        await turn(B, "vs-b", "turn-b")
    asyncio.run(go())
    assert len(store.rows["chat_messages"]) == 2, "only B's two rows: A's insert was taken out again, A's reply never written"


def test_the_delete_waits_for_a_voice_turn_once_its_speaker_is_known_and_for_its_background_tail(voice, monkeypatch):
    import erase_watermark
    import member_owned_forget as mof

    vt, store, save_clip = voice
    monkeypatch.setattr(mof, "INFLIGHT_WAIT_S", 0.2)

    async def go():
        bound = erase_watermark.voice_turn_begin()
        assert erase_watermark.inflight_turns(A) == 0, "not A's yet: the speaker is unknown"
        save_clip("turn-a")
        await vt._label_member_clip(A)                        # re-keyed to A
        gate = asyncio.Event()

        async def slow_tail():
            await gate.wait()
        vt._spawn_bg(slow_tail())                             # background work the turn started
        erase_watermark.voice_turn_end(bound)                 # the request returned ...
        with pytest.raises(RuntimeError, match="in flight"):
            await mof._await_inflight_work(A)                 # ... but its tail still holds the turn
        gate.set()
        for t in list(vt._BG_TASKS):
            await t
        await mof._await_inflight_work(A)                     # tail done: the delete proceeds
    asyncio.run(go())


@pytest.mark.parametrize("lane", ["chat", "voice"])
def test_a_tool_write_inside_a_turn_lands_before_the_delete_erases_so_nothing_survives(lane, monkeypatch):
    """A tool the brain calls mid-turn (a reminder, a list item) writes while the turn is in flight; the delete WAITS for the turn,
    so the tool's write lands before the erase, which then takes it."""
    import erase_watermark
    import member_owned_forget as mof

    store = {"reminders": set()}
    order = []

    async def go():
        if lane == "chat":
            token = erase_watermark.turn_open("s-tool", A)
        else:
            bound = erase_watermark.voice_turn_begin()
            erase_watermark.turn_identify(A)
        started = asyncio.Event()

        async def delete():
            started.set()
            await mof._await_inflight_work(A)
            order.append("erase")
            store["reminders"] = {r for r in store["reminders"] if r[0] != A}
        d = asyncio.ensure_future(delete())
        await started.wait()
        await asyncio.sleep(0)
        store["reminders"].add((A, "rem-from-tool"))          # the tool's write, mid-turn
        order.append("tool")
        if lane == "chat":
            erase_watermark.turn_close(token)
        else:
            erase_watermark.voice_turn_end(bound)
        await d
    asyncio.run(go())
    assert order == ["tool", "erase"] and store["reminders"] == set()
