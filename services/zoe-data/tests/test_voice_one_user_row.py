"""One spoken turn = ONE transcript row for the user's words, by construction (stacks on PR #2574).

The voice lane writes a turn's user row twice over: ``voice_command`` saves it the moment the turn ARRIVES, and the reply's save
(``routers.voice_tts._schedule_voice_chat_save``) waits at most ``_ARRIVAL_SETTLE_TIMEOUT_S`` for that insert and writes the row
itself when it was not confirmed. On ac3fa1d3d the recovery minted a fresh id, so an arrival insert that was merely SLOW (it
committed after the recovery gave up on it) left two rows with the same words and different ids: the history sent to the brain,
the turn counts, the nightly exact-words catch-up and the digest all read the turn twice.

Now the turn's id is minted once, in ``_ArrivalRow`` (created where the turn starts), and both writers insert under it through
``_ArrivalRow.write``; ``chat_messages.id`` is the primary key and the insert is ``ON CONFLICT DO NOTHING``, so whichever insert
lands second is a no-op. The fake ``chat_messages`` here (``test_forget_by_turn_id._Chat``) already keeps the FIRST row for an id
(``setdefault``), which is exactly that conflict rule; the real ``routers.chat._save_chat_message`` runs over it.

On the base commit ``_ArrivalRow`` has neither the id nor ``write``; ``_arrival_persist`` below then replays voice_command's
arrival closure exactly as it was there, so every race test fails there BY ASSERTION, not at setup. Synthetic words only.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
from pathlib import Path

import pytest

import memory_service
import turn_forget
from forgotten_support import ledger_env  # noqa: F401 - fixture (the world below needs it)
from test_forget_by_turn_id import UID, run, world  # noqa: F401 - fixture: the real save over a fake chat_messages

pytestmark = pytest.mark.ci_safe

SID = "s-voice-1"
WORDS = "My kayak paddles hang above the garden tap"
REPLY = "Got it."
VOICE_TTS = Path(__file__).resolve().parents[1] / "routers" / "voice_tts.py"


def _new_arrival(vt):
    try:
        return vt._ArrivalRow(SID, WORDS, UID, None)
    except TypeError:                                      # ac3fa1d3d: (session_id, text) only
        return vt._ArrivalRow(SID, WORDS)


async def _arrival_persist(arrival, save, ensure):
    """``voice_command``'s ``_persist_user_turn`` (the arrival save), in the same shape: ensure the session, insert, settle."""
    try:
        await ensure(SID, UID)
        if hasattr(arrival, "write"):
            await arrival.write(save)
        else:                                              # ac3fa1d3d's closure body, verbatim in effect
            arrival.saved = bool(await save(SID, "user", WORDS, user_id=UID))
    finally:
        arrival.settled.set()


def _gated(chat, before):
    """``get_db_ctx`` over the fake ``chat_messages`` whose n-th USER-row insert first awaits ``before(n)`` (which may stall
    or raise: a raise is a failed insert, swallowed by the real save exactly as a database error is)."""
    seen = {"n": 0}

    @contextlib.asynccontextmanager
    async def _ctx():
        async with chat.ctx() as db:
            real = db.execute

            async def execute(sql, params=()):
                if sql.lstrip().upper().startswith("INSERT INTO CHAT_MESSAGES") and params[2] == "user":
                    seen["n"] += 1
                    chat.attempted_user_ids = getattr(chat, "attempted_user_ids", []) + [params[0]]   # the id each insert tried
                    await before(seen["n"])
                return await real(sql, params)
            db.execute = execute
            yield db
    return _ctx


async def _turn(chat, monkeypatch, before, *, ensure_fails=False):
    """One voice turn's two writers racing: the arrival save (stalled / failed by ``before``) and the reply's save."""
    import db_pool
    from routers import chat as chat_mod
    from routers import voice_tts as vt

    noted = []
    real_note = turn_forget.note_user_turn

    def spy_note(user_id, text, *a, **k):                  # the turn's count (chat.py's count_turn guard)
        noted.append(text)
        return real_note(user_id, text, *a, **k)

    calls = {"ensure": 0}

    async def ensure(_sid, _uid):
        calls["ensure"] += 1
        if ensure_fails and calls["ensure"] == 1:          # the arrival's session guard fails: its insert is never tried
            raise RuntimeError("transient session-row failure")

    monkeypatch.setattr(turn_forget, "note_user_turn", spy_note)
    monkeypatch.setattr(db_pool, "get_db_ctx", _gated(chat, before))
    monkeypatch.setattr(chat_mod, "_ensure_user_and_chat_session", ensure)
    monkeypatch.setattr(vt, "_ARRIVAL_SETTLE_TIMEOUT_S", 0.05)
    spawned = []
    monkeypatch.setattr(vt, "_spawn_bg", lambda coro: spawned.append(asyncio.ensure_future(coro)))

    arrival = _new_arrival(vt)
    vt._USER_TURN_SAVED.set(arrival)
    arrival_task = asyncio.ensure_future(_arrival_persist(arrival, chat_mod._save_chat_message, ensure))
    await asyncio.sleep(0)
    await vt._schedule_voice_chat_save(SID, WORDS, REPLY, UID)
    return arrival, arrival_task, spawned, noted


def _rows(chat):
    return [(mid, c) for mid, (c, _meta) in chat.rows.items()]


def _users(chat):
    return [mid for mid, c in _rows(chat) if c == WORDS]


def _assert_one_row(chat, noted):
    users = _users(chat)
    assert len(users) == 1, _rows(chat)
    assert [c for _m, c in _rows(chat)] == [WORDS, REPLY]                 # the user row still precedes the reply
    turn = turn_forget.previous_turn(UID)
    assert turn is not None and turn.mids == (users[0],)                  # the turn's id set is that one committed id
    assert noted == [WORDS]                                               # counted once


def test_a_slow_arrival_that_commits_after_the_recovery_adds_no_second_row(world, monkeypatch):
    """Arrival stalls past the settle timeout; the recovery commits the row; THEN the arrival commits. One row (two on ac3fa1d3d)."""
    _svc, chat = world

    async def scenario():
        release = asyncio.Event()

        async def before(n):
            if n == 1:
                await release.wait()

        arrival, arrival_task, spawned, noted = await _turn(chat, monkeypatch, before)
        await asyncio.gather(*spawned)                                    # the recovery gave up waiting and wrote the row
        assert len(_users(chat)) == 1
        release.set()
        await asyncio.gather(arrival_task, return_exceptions=True)        # ... and the slow arrival insert lands after it
        return arrival, noted

    arrival, noted = run(scenario())
    _assert_one_row(chat, noted)
    assert _users(chat) == [arrival.mid] and arrival.saved


def test_an_arrival_that_commits_just_as_the_recovery_gives_up_adds_no_second_row(world, monkeypatch):
    """The recovery's wait times out, and the arrival commits between that and the recovery's own insert. One row."""
    _svc, chat = world

    async def scenario():
        gate, landed = asyncio.Event(), asyncio.Event()

        async def before(n):
            if n == 1:
                await gate.wait()
            elif n == 2:                                                  # the recovery's insert: the arrival lands first
                gate.set()
                await landed.wait()

        arrival, arrival_task, spawned, noted = await _turn(chat, monkeypatch, before)
        arrival_task.add_done_callback(lambda _t: landed.set())
        await asyncio.gather(arrival_task, *spawned, return_exceptions=True)
        return arrival, noted

    arrival, noted = run(scenario())
    _assert_one_row(chat, noted)
    assert _users(chat) == [arrival.mid]


def test_a_failed_arrival_insert_is_recovered_under_the_turns_id(world, monkeypatch):
    """The arrival insert fails; the recovery commits. One row, and the recorded id is the committed one (#2574's rule)."""
    _svc, chat = world

    async def scenario():
        async def before(n):
            if n == 1:
                raise RuntimeError("transient insert failure")

        arrival, arrival_task, spawned, noted = await _turn(chat, monkeypatch, before)
        await asyncio.gather(arrival_task, *spawned, return_exceptions=True)
        return arrival, noted

    arrival, noted = run(scenario())
    _assert_one_row(chat, noted)
    assert arrival.mid and _users(chat) == [arrival.mid]                  # the ONE stored row carries the id minted at turn start
    assert chat.attempted_user_ids == [arrival.mid, arrival.mid]          # the failed insert and the recovery tried the SAME id


def test_an_arrival_that_never_reached_its_insert_is_counted_by_the_recovery(world, monkeypatch):
    """The arrival's session guard fails before its save runs, so nothing counted the turn: the recovery writes the row AND counts
    it (ac3fa1d3d passed ``count_turn=False`` unconditionally, so the turn was never noted and "forget that" could not bind to it)."""
    _svc, chat = world

    async def scenario():
        async def before(_n):
            return None

        arrival, arrival_task, spawned, noted = await _turn(chat, monkeypatch, before, ensure_fails=True)
        await asyncio.gather(arrival_task, *spawned, return_exceptions=True)
        return noted

    noted = run(scenario())
    _assert_one_row(chat, noted)


def test_when_both_inserts_fail_there_is_no_row_no_id_and_the_turns_writes_are_unbound(world, monkeypatch, caplog):
    """Both writers fail: no user row, the turn is noted once with NO saved-turn id, and a fact captured in that turn is counted
    unbound (``MEMORY_WRITE_UNBOUND code=no_source_turn``) and stored legacy-shaped under shadow / refused under enforce."""
    import db_pool

    svc, chat = world
    caplog.set_level(logging.INFO)
    store = db_pool.get_db_ctx

    async def scenario():
        async def before(_n):
            raise RuntimeError("database down")

        arrival, arrival_task, spawned, noted = await _turn(chat, monkeypatch, before)
        await asyncio.gather(arrival_task, *spawned, return_exceptions=True)
        return arrival, noted

    arrival, noted = run(scenario())
    assert _users(chat) == [] and not arrival.saved
    # this PR's part of the case: both failed inserts were attempted under the one id minted at turn start (the rest of the
    # test is #2574's unbound path, kept here because a turn with no committed id must still behave that way afterwards)
    assert arrival.mid and chat.attempted_user_ids == [arrival.mid, arrival.mid]
    turn = turn_forget.previous_turn(UID)
    assert turn is not None and turn.mids == () and noted == [WORDS]
    monkeypatch.setattr(db_pool, "get_db_ctx", store)                     # the capture sees the store every forget test sees
    before = memory_service.UNBOUND["no_source_turn"]

    async def capture(fact):
        with turn_forget.capture_scope(UID, WORDS):
            return await svc.ingest(fact, user_id=UID, source="turn_digest", status="approved", source_excerpt=WORDS)
    shadow = run(capture("User's kayak paddles hang above the garden tap"))
    assert shadow is not None and memory_service.UNBOUND["no_source_turn"] == before + 1
    assert not {"source_turn", "source_kind"} & set(run(svc.get(shadow.id)).metadata)
    monkeypatch.setenv(memory_service.TURN_ID_FLAG, "enforce")
    assert run(capture("User keeps the kayak paddles by the tap")) is None
    assert memory_service.UNBOUND["no_source_turn"] == before + 2


SNAPPED = "Turn off the garden tap light"


@pytest.mark.parametrize("arrival_lands", [True, False], ids=["arrival_committed", "arrival_failed"])
def test_an_stt_snap_that_rewrites_the_words_keeps_the_turns_one_row_and_one_count(world, monkeypatch, arrival_lands):
    """Review P2 (PR #2658, thread ...uZ_S): an STT snap (acted on, or a confirmed "Did you mean ...?") rewrites the turn's words
    after the arrival row was minted; the reply's save then carries the REWRITTEN words. It must still name the arrival row: one
    user row (the words as said, under the minted id), counted once - not a second row for the snapped command, counted again (a
    later "forget that" would bind to the synthetic command, not the spoken turn). Fails on 785f91c35 (the text-matching guard
    dropped the arrival and minted a new, counted row)."""
    _svc, chat = world
    from routers import voice_tts as vt

    async def scenario():
        async def before(n):
            if n == 1 and not arrival_lands:
                raise RuntimeError("transient insert failure")

        real_save = vt._schedule_voice_chat_save

        async def save_after_snap(sid, _text, reply, uid, *a, **k):
            rewritten = getattr(vt, "_turn_words_rewritten", None)   # the snap site's call (absent on 785f91c35)
            if rewritten is not None:
                rewritten(SNAPPED)
            return await real_save(sid, SNAPPED, reply, uid, *a, **k)
        monkeypatch.setattr(vt, "_schedule_voice_chat_save", save_after_snap)
        arrival, arrival_task, spawned, noted = await _turn(chat, monkeypatch, before)
        await asyncio.gather(arrival_task, *spawned, return_exceptions=True)
        return arrival, noted

    arrival, noted = run(scenario())
    assert [c for _m, c in _rows(chat)] == [WORDS, REPLY], _rows(chat)    # one user row, the words as said; no snapped-command row
    assert _users(chat) == [arrival.mid]
    assert noted == [WORDS]                                               # counted once
    turn = turn_forget.previous_turn(UID)
    assert turn is not None and turn.mids == (arrival.mid,)               # "forget that" binds to the spoken turn


def test_both_stt_snap_rewrites_in_voice_command_keep_the_turn_identity():
    """Each place ``voice_command`` rewrites ``text`` to a snapped command also tells the arrival row (``_turn_words_rewritten``)."""
    src = VOICE_TTS.read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "voice_command")
    body = ast.unparse(fn)
    for rewrite in ("text = str(pending['stt_snap_text'])", "text = _snap.text"):
        at = body.index(rewrite)
        assert "_turn_words_rewritten(text)" in body[at:at + 200], f"{rewrite!r} does not keep the turn identity"


# ── structural: a voice turn's user row is written ONLY under the turn's minted id ─────────────────────────────────────────────

def _save_aliases(tree) -> set:
    names = {"_save_chat_message"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("chat"):
            names |= {a.asname or a.name for a in node.names if a.name == "_save_chat_message"}
    return names


def _callee(call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _role(call):
    for kw in call.keywords:
        if kw.arg == "role":
            return kw.value
    return call.args[1] if len(call.args) > 1 else None


def test_no_voice_code_saves_a_user_row_except_under_the_turns_minted_id():
    """Every save of a role-"user" row in ``routers/voice_tts.py`` goes through ``_ArrivalRow.write`` (which passes the turn's
    ``message_id``); a direct call of the chat save with a user role - or with a role that is not a literal ``"assistant"`` -
    anywhere else fails this test, as does a raw transcript INSERT. On ac3fa1d3d both writers called the save directly."""
    src = VOICE_TTS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    aliases = _save_aliases(tree)
    assert "INSERT INTO chat_messages" not in src
    stray = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) in aliases:
            role = _role(node)
            if not (isinstance(role, ast.Constant) and role.value == "assistant"):
                stray.append(node.lineno)
    assert stray == [], f"voice_tts saves a non-assistant row outside _ArrivalRow.write at lines {stray}"

    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ArrivalRow")
    write = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "write")
    [save_call] = [n for n in ast.walk(write) if isinstance(n, ast.Call) and _callee(n) == "save"]
    assert isinstance(_role(save_call), ast.Constant) and _role(save_call).value == "user"
    kw = {k.arg: ast.unparse(k.value) for k in save_call.keywords if k.arg}
    assert kw.get("message_id") == "self.mid" and "count_turn" in kw
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    assert "self.mid = uuid.uuid4().hex" in ast.unparse(init)

    def body_of(name):
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        return ast.unparse(fn)
    # both writers of the turn's user row go through it: the arrival save and the reply's recovery save
    assert "await _arrival.write(_svc_user_turn)" in body_of("voice_command")
    assert "_ArrivalRow(session_id, text, effective_user, _speaker_verified)" in body_of("voice_command")
    assert "await row.write(_svc, user_id)" in body_of("_schedule_voice_chat_save")
