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


# ── the ambient lane and the post-turn background writers ─────────────────────────────────────────────────────────────────────

class _AmbientDB:
    def __init__(self, hook=None):
        self.rows, self.hook = [], hook

    async def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO ambient_memory"):
            if self.hook:
                h, self.hook = self.hook, None
                await h()
            self.rows.append(params[-1])
        elif s.startswith("DELETE FROM ambient_memory WHERE user_id = ?"):
            self.rows = [u for u in self.rows if u != params[0]]

    async def commit(self):
        pass


@pytest.mark.parametrize("gap", ["during_transcription", "during_the_insert"])
def test_a_delete_while_an_ambient_segment_is_in_flight_stores_nothing_of_the_speaker(monkeypatch, gap):
    import db_compat
    import erase_watermark
    import routers.voice_tts as vt

    db = _AmbientDB()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    async def claim(payload, caller):
        return payload.get("who")

    async def transcribe(_path):
        if gap == "during_transcription" and current["who"] == A:
            assert erase_watermark.inflight_turns(A) == 1, "the delete would wait for this request"
            await erase_watermark.record(A)
        return "the kettle is broken again"
    current = {}
    monkeypatch.setattr(db_compat, "get_compat_db", ctx)
    monkeypatch.setattr(vt, "_accepted_voice_claim", claim)
    monkeypatch.setattr(vt, "_turn_own_identity", lambda caller, claimed: claimed)
    monkeypatch.setattr(vt, "_transcribe_audio_impl", transcribe)
    monkeypatch.setattr(vt, "_lane_panel_id", lambda payload, caller, lane: "p1")

    async def post(who):
        current["who"] = who
        if gap == "during_the_insert" and who == A:
            async def land():
                await erase_watermark.record(A)
            db.hook = land
        return await vt.voice_ambient({"audio_base64": "UklGRg==", "who": who}, caller={"source": "device"})

    async def go():
        return await post(A), await post(B)
    out_a, out_b = asyncio.run(go())
    assert out_a["stored"] is False and A not in db.rows, "nothing of the deleted speaker"
    assert out_b["stored"] is True and db.rows == [B], "another member's segment is stored as before"


def test_an_offer_queued_by_a_turn_pass_is_not_stored_for_a_member_erased_since(monkeypatch):
    """latent_intent_detector and memory_disputes both store through pending_suggestions.store_suggestions: an offer from a pass the
    member's erase outran is never stored; one that lands across the erase is taken out again."""
    import pending_suggestions as ps
    import user_erase_gate as gate

    rows = []

    class _DB:
        def __init__(self, hook=None):
            self.hook = hook

        async def execute(self, sql, *args):
            s = " ".join(sql.split())
            if s.startswith("INSERT INTO pending_suggestions"):
                if self.hook:
                    h, self.hook = self.hook, None
                    h()
                rows.append(args[1])
            elif s.startswith("DELETE FROM pending_suggestions WHERE user_id"):
                rows[:] = [u for u in rows if u != args[0]]

        async def fetch(self, *a, **k):
            return []
    holder = {"db": _DB()}

    @contextlib.asynccontextmanager
    async def ctx():
        yield holder["db"]

    async def no(*_a, **_k):
        return False

    async def nothing(*_a, **_k):
        return lambda *_texts: False                    # the forget filter: no offer names a forgotten entity
    monkeypatch.setattr(ps, "get_db_ctx", ctx)
    monkeypatch.setattr(ps, "contact_offers_declined", no)
    monkeypatch.setattr(ps, "_filter_or_withhold", nothing)
    offer = [{"action_type": "reminder_create", "description": "remind you about the dentist", "offer_phrase": "want a reminder?"}]

    async def go():
        snap = gate.generation(A)
        with gate.erase_span(A):                       # the member delete ran after the pass was queued
            pass
        with gate.scheduled(A, snap):
            first = await ps.store_suggestions(A, "s-1", offer)
        snap_b = gate.generation(B)
        def b_erased():                                # B's delete lands INSIDE the insert (a whole erase span: enter and exit)
            with gate.erase_span(B):
                pass
        holder["db"] = _DB(hook=b_erased)
        with gate.scheduled(B, snap_b):
            second = await ps.store_suggestions(B, "s-2", offer)
        return first, second
    first, second = asyncio.run(go())
    assert first == 0 and second == 0 and rows == [], rows


def test_the_person_extractors_writes_are_refused_for_a_member_erased_since_the_pass_was_queued(monkeypatch):
    import person_extractor as pe
    import user_erase_gate as gate

    reached = []

    async def opted_out(*_a, **_k):
        reached.append("past the gate")
        return True
    monkeypatch.setattr(pe, "_opted_out", opted_out)

    async def go():
        snap = gate.generation(A)
        with gate.erase_span(A):
            pass
        with gate.scheduled(A, snap):
            n = await pe.process_text("my sister Mara lives in Perth", user_id=A, source="conversation")
            ok = await pe.apply_person_fact("Mara", "lives_in", "Perth", user_id=A, source="conversation")
            edge = await pe._write_relationship(A, "Mara", "Tove", "sister", "family", db=None)
        return n, ok, edge
    assert asyncio.run(go()) == (0, False, None) and reached == [], "no write path was even entered"


def test_a_chat_turns_post_turn_passes_hold_the_turn_so_the_delete_waits_for_them():
    import erase_watermark
    import routers.chat as chat_mod

    token = erase_watermark.turn_open("s-hold", A)
    coro = chat_mod._persist_memory_candidates(A, "s-hold", "hello", "hi")   # queued from inside the turn
    erase_watermark.turn_close(token)                                       # the turn's stream ended
    try:
        assert erase_watermark.inflight_turns(A) == 1, "its passes still hold it: the member delete waits"
    finally:
        coro.close()
        erase_watermark._TURNS.clear()


def test_a_clip_whose_wav_cannot_be_dropped_keeps_its_owner_label_and_the_retry_removes_both(monkeypatch, tmp_path):
    """drop_current: labelled as the member's first, then the WAV first and the label last. A WAV unlink that fails RAISES and leaves
    the label naming the member, so their erase (a re-run of the delete) still finds and removes both."""
    import os as _os

    import voice_clips

    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(tmp_path))
    voice_clips.begin()
    wav = tmp_path / "turn-a.wav"
    wav.write_bytes(b"RIFF")
    voice_clips.note_saved(str(wav))
    real = _os.unlink
    fails = {"n": 1}

    def flaky(path, *a, **k):
        if str(path).endswith(".wav") and fails["n"]:
            fails["n"] -= 1
            raise PermissionError("busy")
        return real(path, *a, **k)
    monkeypatch.setattr(voice_clips.os, "unlink", flaky)
    with pytest.raises(PermissionError):
        voice_clips.drop_current(A)                            # the caller is told
    side = tmp_path / "turn-a.verdict.json"
    assert wav.exists() and side.exists() and '"ann"' in side.read_text(), "the audio keeps its owner label"
    assert voice_clips.erase_member(A) == 1 and not wav.exists() and not side.exists(), "the retry removes both"


@pytest.mark.parametrize("clip, verdict", [(None, "1"), ("/tmp/saved-clip.wav", "0")])
def test_with_no_clip_the_voice_turn_pays_no_watermark_read_or_worker_dispatch(monkeypatch, clip, verdict):
    """The clip guard costs the voice path nothing when the turn saved no clip (the usual case, and always with the clip flags
    off): the speaker is identified for the delete, and neither the watermark nor a worker thread is touched."""
    import erase_watermark
    import routers.voice_tts as vt
    import voice_clips

    identified = []
    monkeypatch.setattr(erase_watermark, "turn_identify", identified.append)
    monkeypatch.setattr(voice_clips, "current_clip", lambda: clip)
    monkeypatch.setenv("ZOE_VOICE_CLIP_VERDICT", verdict)   # a saved clip with the verdict flag off is unlabelled: nothing to do

    async def no_read(*_a, **_k):
        raise AssertionError("the watermark was read with no clip to label")

    def no_thread(*_a, **_k):
        raise AssertionError("a worker was dispatched with no clip to label")

    monkeypatch.setattr(erase_watermark, "turn_write_refused", no_read)
    monkeypatch.setattr(vt.asyncio, "to_thread", no_thread)
    asyncio.run(vt._label_member_clip(A))
    assert identified == [A], "the delete still learns whose turn this is"


def test_a_turn_that_begins_while_the_members_delete_runs_writes_nothing():
    """A turn that began AFTER the delete's first mark passes the began <= mark rule until the final mark: while the delete runs
    (``user_erase_gate.erase_span``) every write of the member's turns is refused outright."""
    import erase_watermark
    import user_erase_gate

    async def go():
        with user_erase_gate.erase_span(A):
            token = erase_watermark.turn_open(None, A)              # begins mid-delete
            ctx = erase_watermark.turn_bind(token)
            try:
                during = await erase_watermark.turn_write_refused(None, "transcript")
            finally:
                erase_watermark.turn_unbind(ctx)
                erase_watermark.turn_close(token)
        token = erase_watermark.turn_open(None, B)                  # another member is unaffected
        ctx = erase_watermark.turn_bind(token)
        try:
            other = await erase_watermark.turn_write_refused(None, "transcript")
        finally:
            erase_watermark.turn_unbind(ctx)
            erase_watermark.turn_close(token)
        return during, other
    assert asyncio.run(go()) == (True, False)


def test_an_unreadable_erase_mark_refuses_a_turns_write(monkeypatch):
    """FAIL CLOSED: a turn that may have outlived its member's delete does not write when the mark cannot be read."""
    import erase_watermark

    async def unreadable(*_a, **_k):
        raise erase_watermark.Unavailable("store down")

    monkeypatch.setattr(erase_watermark, "watermark", unreadable)

    async def go():
        token = erase_watermark.turn_open(None, A)
        ctx = erase_watermark.turn_bind(token)
        try:
            return await erase_watermark.turn_write_refused(None, "transcript")
        finally:
            erase_watermark.turn_unbind(ctx)
            erase_watermark.turn_close(token)
    assert asyncio.run(go()) is True
