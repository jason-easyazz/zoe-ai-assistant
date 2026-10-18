"""Panel review cards (``panel_review_cards.py`` + ``routers/panel_review_cards.py``, flag ``ZOE_PANEL_REVIEW_CARDS`` default off):
open loops read back and closed by voice or touch, the day review card with new voices to name, and owner decision D5 (a pendant
follow-up is an open loop).

SYNTHETIC members and loops only. Negative controls (run by hand-mutating the code; each named test goes red):
  * guest / no identity      -> make ``_member`` return ``user_id`` unchecked (guest turn test)
  * another member's loops   -> drop ``WHERE user_id = ?`` from ``member_loops`` (cross-member test)
  * a tap after the session  -> make ``authorize`` return the card without either check (session-closed test)
  * a row not on the card    -> look the item up in the database instead of ``card.items`` (unknown item test)
  * a stolen reminder/list   -> return a reply instead of None when no loop matches (fall-through test)
  * the wall floor           -> make ``_private`` return False (restraint test)
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db_compat
import panel_review_cards as prc
import voice_cards
import voice_session
from db_pool import _Cursor, _ExecResult
from routers import panel_review_cards as prc_router

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
A, B, PANEL = "member-ash", "member-bo", "panel-kitchen"


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def rows(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


def _migrate(engine, fname):
    spec = importlib.util.spec_from_file_location(f"mig_{fname[:4]}", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine, "0045_omi_recordings.py")
    _migrate(engine, "0054_omi_day_review.py")
    db = _Sqlite(path)
    for ddl in ("CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT, name TEXT)",
                "CREATE TABLE open_loops (id INTEGER PRIMARY KEY, user_id TEXT, loop_text TEXT, context TEXT, follow_up_hint TEXT, "
                "emotional_weight INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, follow_up_after TEXT, "
                "resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT)",
                "CREATE TABLE proactive_candidates (user_id TEXT, source_ref TEXT, expires_at TEXT)"):
        db.conn.execute(ddl)
    db.conn.executemany("INSERT INTO users VALUES (?, 'user', ?)", [(A, "Ash"), (B, "Bo")])
    db.conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_PANEL_REVIEW_CARDS", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    monkeypatch.delenv("ZOE_OMI_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_OMI_DAY_REVIEW", raising=False)
    prc.reset()
    pushed: list = []

    async def push(frame):
        pushed.append(frame)
        return 1
    monkeypatch.setattr(voice_cards, "push", push)
    sessions: dict = {}
    monkeypatch.setattr(voice_session, "active_member", lambda panel: sessions.get(panel))
    reminders: list = []

    async def make_reminder(member, loop, due_date, due_time):
        reminders.append((member, loop["text"], due_date, due_time))
        await prc.close_loop(member, loop["id"], "reminder")
        return True
    monkeypatch.setattr(prc, "loop_to_reminder", make_reminder)
    app = FastAPI()
    assert prc_router.register(app) is True
    caller = {"user": {"user_id": "guest", "role": "guest"}}
    app.dependency_overrides[prc_router._caller] = lambda: caller["user"]
    yield type("Env", (), {"db": db, "pushed": pushed, "sessions": sessions, "reminders": reminders, "caller": caller,
                           "client": TestClient(app), "app": app})
    prc.reset()


def loop(env, text, user=A, context=None):
    env.db.conn.execute("INSERT INTO open_loops (user_id, loop_text, context) VALUES (?, ?, ?)", (user, text, context))
    env.db.conn.commit()


def say(text, scope_user, *, panel=PANEL, channel="voice", allow_writes=True):
    ctx = {"panel_id": panel, "scope_user": scope_user}
    return asyncio.run(prc.answer(text, "guest", channel=channel, extra_ctx=ctx, allow_writes=allow_writes))


def tap(env, token, item, action, choice=None):
    body = {"item": item, "action": action, **({"choice": choice} if choice else {})}
    return env.client.post("/api/panel/cards/act", json=body, headers={"X-Panel-Card": token} if token else {})


def open_loops(env, user=A):
    return [r["loop_text"] for r in env.db.rows("SELECT loop_text FROM open_loops WHERE user_id = ? AND resolved IS NOT TRUE", (user,))]


# --- the words ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,kind,desc", [
    ("what's on my plate", "read", ""), ("Hey Zoe, what's on my plate today?", "read", ""), ("what did I say I'd do", "read", ""),
    ("what have I said I would do", "read", ""), ("what did I promise to do", "read", ""), ("show me my open loops", "read", ""),
    ("I've done the plumber one", "done", "plumber"), ("tick off the plumber one", "done", "plumber"),
    ("the plumber one is done", "done", "plumber"), ("drop the gutter one", "drop", "gutter"),
    ("remind me about the plumber one tomorrow at 9", "remind", "plumber"), ("review my day", "day", ""),
    ("name the new voices", "day", ""), ("what did my pendant hear today", "day", ""),
])
def test_shapes(text, kind, desc):
    got = prc.shape(text)
    assert got and got[0] == kind and got[1] == desc


@pytest.mark.parametrize("text", [
    "what's on my calendar", "what are my reminders", "what did I say about the dentist", "add milk to the shopping list",
    "remind me to call mum tomorrow at 9", "what do I need to do today", "remove milk from the list", "forget the plumber",
    "what's on the menu", "who is logged in", "what's on my shopping list",
])
def test_reminders_lists_and_recall_are_not_claimed(text):
    assert prc.shape(text) is None


def test_a_done_shape_matching_no_loop_falls_through(env):
    loop(env, "Call the plumber about the leaking tap")
    assert say("I've done the milk one", A) is None                 # nothing of Ash's says milk: the turn goes on as before
    assert say("remind me about the dentist one tomorrow at 9", A) is None
    assert open_loops(env) == ["Call the plumber about the leaking tap"]


def test_parse_when():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)      # a Monday, 2 PM
    assert prc.parse_when("tomorrow at 9", now) == ("2026-10-20", "09:00")
    assert prc.parse_when("at 5pm", now) == ("2026-10-19", "17:00")
    assert prc.parse_when("at 9", now) == ("2026-10-20", "09:00")   # 9 AM has gone today: tomorrow
    assert prc.parse_when("someday", now) is None


# --- privacy ----------------------------------------------------------------------------------------------------------------------
def test_a_guest_turn_hears_nothing_and_the_panel_gets_the_locked_card(env):
    loop(env, "Call the plumber about the leaking tap")
    assert say("what's on my plate", None) == prc.PRIVATE_REPLY
    assert say("what's on my plate", "guest") == prc.PRIVATE_REPLY
    assert say("review my day", None) == prc.PRIVATE_REPLY
    assert say("I've done the plumber one", None) is None
    assert all(f["kind"] == "locked" and not f.get("items") and "card_token" not in f for f in env.pushed)
    assert "plumber" not in json.dumps(env.pushed)
    assert open_loops(env) == ["Call the plumber about the leaking tap"]
    assert asyncio.run(prc.answer("what's on my plate", "guest", channel="chat", extra_ctx={}, allow_writes=True)) == prc.PRIVATE_REPLY


def test_a_member_reads_only_their_own_loops(env):
    loop(env, "Call the plumber about the leaking tap")
    loop(env, "Book the gutter clean before winter")
    loop(env, "Return the library books to the branch", user=B)
    reply = say("what's on my plate", A)
    assert "plumber" in reply and "gutter" in reply and "library" not in reply
    (frame,) = env.pushed
    assert frame["kind"] == "open_loops" and frame["member_id"] == A and frame["panel_id"] == PANEL
    assert "library" not in json.dumps(frame) and frame["card_token"]
    assert {i["id"] for i in frame["items"]} == {"1", "2"}           # opaque ids, never the database ids


def test_health_and_money_loops_stay_off_the_wall(env):
    loop(env, "User worries about the migraines most afternoons")
    loop(env, "User owes the bank money on the overdue loan")
    loop(env, "Call the plumber about the leaking tap")
    reply = say("what did I say I'd do", A)
    for word in ("migraine", "loan"):
        assert word not in reply.lower() and word not in json.dumps(env.pushed).lower()
    assert "1 open loop" in reply
    typed = asyncio.run(prc.answer("what's on my plate", A, channel="chat", extra_ctx={}, allow_writes=True))
    assert "migraine" in typed and "3 open loops" in typed     # a typed answer is not the wall: nothing approved is unreachable


# --- voice closes ----------------------------------------------------------------------------------------------------------------
def test_voice_done_drop_and_remind(env):
    loop(env, "Call the plumber about the leaking tap")
    loop(env, "Book the gutter clean before winter")
    loop(env, "Return the library books", user=B)
    assert say("I've done the plumber one", A).startswith("Ticked off")
    assert open_loops(env) == ["Book the gutter clean before winter"]
    assert say("I've done the library one", A) is None                 # Bo's loop: Ash's words never reach it
    assert open_loops(env, B) == ["Return the library books"]
    assert "remind you" in say("remind me about the gutter one tomorrow at 9", A)
    assert env.reminders and env.reminders[0][0] == A and env.reminders[0][3] == "09:00"
    assert open_loops(env) == []


def test_a_replay_turn_writes_nothing(env):
    loop(env, "Call the plumber about the leaking tap")
    assert say("I've done the plumber one", A, allow_writes=False).startswith("Ticked off")
    assert open_loops(env) == ["Call the plumber about the leaking tap"]


def test_two_loops_that_match_are_asked_about(env):
    loop(env, "Call the plumber about the tap")
    loop(env, "Pay the plumber for the hot water")
    assert say("I've done the plumber one", A).endswith("Which one?")
    assert len(open_loops(env)) == 2


# --- taps ----------------------------------------------------------------------------------------------------------------------
def _card(env, scope=A):
    say("what's on my plate", scope)
    return env.pushed[-1]


def test_a_tap_inside_the_voice_session_closes_the_loop(env):
    loop(env, "Call the plumber about the leaking tap")
    env.sessions[PANEL] = A
    tok = _card(env)["card_token"]
    r = tap(env, tok, "1", "done")
    assert r.status_code == 200 and open_loops(env) == []
    assert tap(env, tok, "1", "done").status_code == 404               # one decision per row


def test_a_tap_after_the_session_closed_is_refused(env):
    loop(env, "Call the plumber about the leaking tap")
    env.sessions[PANEL] = A
    tok = _card(env)["card_token"]
    env.sessions.pop(PANEL)                                             # timeout / another voice / "lock"
    assert tap(env, tok, "1", "done").status_code == 403
    env.caller["user"] = {"user_id": B, "role": "user"}                 # another member's session is not Ash's
    assert tap(env, tok, "1", "done").status_code == 403
    env.sessions[PANEL] = B                                             # nor is Bo's voice session on that panel
    assert tap(env, tok, "1", "done").status_code == 403
    assert open_loops(env) == ["Call the plumber about the leaking tap"]
    env.sessions.pop(PANEL)
    env.caller["user"] = {"user_id": A, "role": "user"}                 # Ash's own PIN sign-in on the panel
    assert tap(env, tok, "1", "drop").status_code == 200 and open_loops(env) == []


def test_no_token_an_unknown_item_and_a_superseded_card(env):
    loop(env, "Call the plumber about the leaking tap")
    loop(env, "Return the library books", user=B)
    env.sessions[PANEL] = A
    tok = _card(env)["card_token"]
    assert tap(env, "", "1", "done").status_code == 404
    assert tap(env, tok, "2", "done").status_code == 404               # not on the card (Bo's loop is id 2 in the table)
    assert tap(env, tok, "1", "explode").status_code == 400
    tok2 = _card(env)["card_token"]
    assert tap(env, tok, "1", "done").status_code == 404               # one live card per panel
    assert open_loops(env, B) == ["Return the library books"]
    r = tap(env, tok2, "1", "remind", "nope")
    assert r.status_code == 400
    key = env.pushed[-1]["items"][0]["actions"][1]["choices"][0]["key"]
    assert tap(env, tok2, "1", "remind", key).status_code == 200 and env.reminders[0][0] == A


# --- the day review card -----------------------------------------------------------------------------------------------------------
def test_the_day_card_offers_at_most_three_voices_and_tags_only_card_people(env, monkeypatch):
    from routers import omi_voices as ov

    monkeypatch.setenv("ZOE_OMI_SYNC_ENABLED", "1")
    calls = []

    async def owner(user):
        if user["user_id"] != A:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="owner only")
        return user

    async def unknown(user):
        return {"voices": [{"id": f"cl{i}", "name": f"Voice {i}", "recordings": 2,
                            "samples": [{"segment_id": f"s{i}", "has_audio": i == 1, "text": "SECRET LINE"}]} for i in range(1, 6)]}

    async def people(user):
        return {"people": [{"id": "p-sam", "name": "Sam"}]}

    async def tag_voice(cid, body, user):
        calls.append(("tag", cid, body.person_id, user["user_id"]))
        return {"ok": True}

    async def clip(seg, user):
        from fastapi.responses import Response
        calls.append(("clip", seg, user["user_id"]))
        return Response(content=b"RIFF", media_type="audio/wav")
    for name, fn in (("_require_owner", owner), ("unknown_voices", unknown), ("my_people", people), ("tag_voice", tag_voice),
                     ("segment_clip", clip)):
        monkeypatch.setattr(ov, name, fn)
    assert say("review my day", B) == "Nothing from your day is waiting."   # not the pendant's owner: no voices, no card rows
    assert not env.pushed[-1]["items"]
    reply = say("review my day", A)
    assert "3 new voices" in reply
    frame = env.pushed[-1]
    voices = [i for i in frame["items"] if i["group"] == "voices"]
    assert len(voices) == 3 and "SECRET LINE" not in json.dumps(frame)       # no transcript on the wall
    env.sessions[PANEL] = A
    tok = frame["card_token"]
    assert env.client.get("/api/panel/cards/clip/v1", headers={"X-Panel-Card": tok}).status_code == 200
    assert env.client.get("/api/panel/cards/clip/v2", headers={"X-Panel-Card": tok}).status_code == 404   # no audio held
    assert tap(env, tok, "v1", "tag", "p-sam").status_code == 400           # a raw person id is not a card choice
    assert tap(env, tok, "v1", "tag", "p1").status_code == 200
    assert ("tag", "cl1", "p-sam", A) in calls
    qr = env.client.get("/api/panel/cards/phone-qr", headers={"X-Panel-Card": tok})
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg")
    env.sessions.pop(PANEL)
    assert env.client.get("/api/panel/cards/clip/v2", headers={"X-Panel-Card": tok}).status_code == 403
    assert env.client.get("/api/panel/cards/phone-qr", headers={"X-Panel-Card": tok}).status_code == 403


# --- D5: a pendant follow-up is an open loop ------------------------------------------------------------------------------------
def test_an_approved_follow_up_becomes_a_quiet_open_loop(env, monkeypatch):
    import memory_forgotten
    import omi_review_apply as apply
    import user_erase_gate

    async def no_spans(user_id, text):
        return []
    monkeypatch.setattr(memory_forgotten, "spans", no_spans)
    user_erase_gate.reset()
    env.db.conn.execute("INSERT INTO omi_day_candidates (id, review_id, user_id, day_local, kind, payload, dedup_key, created_at) "
                        "VALUES ('cand-1', 'rv', ?, '2026-10-18', 'follow_up', ?, 'k1', '2026-10-18T10:00:00Z')",
                        (A, json.dumps({"title": "Call the plumber"})))
    env.db.conn.commit()
    out = asyncio.run(apply.approve(A, "cand-1", via="panel_touch"))
    assert out["status"] == "approved" and out["written_ref"].startswith("open_loops:")
    (row,) = env.db.rows("SELECT loop_text, context FROM open_loops WHERE user_id = ?", (A,))
    assert row == {"loop_text": "Call the plumber", "context": "pendant:cand-1"}
    assert "plumber" in say("what's on my plate", A)                  # and it is on the plate
