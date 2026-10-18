"""Memories by voice and on the panel card (``memories_voice.py`` + ``routers/memories_card.py``, flag ``ZOE_MEMORIES_VOICE``
default off): "what do you know about me?", the voice-session view, forget / correct by voice and by tap, and the phone's
correct route (``routers/memories.py`` ``/mine/actions`` + ``/mine/{id}/correct``).

SYNTHETIC members and rows only (``demo_bar_`` ids, invented names), fake Chroma (``ci_safe``). Each privacy promise has a
break-the-fix control in this file (the ``test_control_*`` tests patch the wall away and show the leak the wall prevents):

  guest / no identity        -> a guest turn gets the private line and the locked card, never a row
  member A never sees B's    -> every read is A's own rows; a voice forget of B's words finds nothing and B's row survives
  sensitive never on the wall-> health words are never spoken or carded; a restraint-only class (family conflict) neither
  forget is the audited path -> the row is REJECTED through MemoryService.review (an audit row), never deleted here; a forgotten
                                row never reappears in the list, the recall packet or on a re-ingest of the same words
  a correction supersedes    -> the old row is kept as history (``superseded``, ``superseded_by_id``), the new one stands
"""
from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import db_compat
import memories_voice as mv
import memory_service
import memory_space
import panel_review_cards as prc
import provenance_answers as pa
import voice_cards
import voice_session
from db_pool import _Cursor, _ExecResult
from memory_service import MemoryService
from routers import memories_card
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

ME = "demo_bar_00000001"
OTHER = "demo_bar_00000002"
PANEL = "panel-kitchen"
OTHER_SECRET = "Zibblewort"

ME_SISTER = "User's sister Marisol is flying in from Lisbon on Thursday"
ME_LIKE = "I like dark roast coffee with oat milk."
ME_ROUTINE = "I walk the dog every morning before work."
ME_BIRTHDAY = "My birthday is on 14 March."
ME_HEALTH = "I have migraines most weeks and my doctor wants a scan."
ME_FIGHT = "My brother and I had a huge fight and we are not speaking"
ME_SHED = "I keep my bike in the shed behind the garage."


def run(coro):
    return asyncio.run(coro)


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)

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


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_MEMORIES_VOICE", "1")
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memories-voice")
    col = _Col()
    s._collection = lambda: col
    audit: list = []

    async def record_audit(**kw):
        audit.append(kw)

    async def opted_in(_uid):
        return False

    s._append_audit = record_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    from routers import memories

    monkeypatch.setattr(memories, "_svc", lambda: s)
    db = _Sqlite(str(tmp_path / "zoe.db"))
    db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT, name TEXT)")
    db.conn.executemany("INSERT INTO users VALUES (?, ?, ?)", [(ME, "user", "Casey"), (OTHER, "child", "Robin")])
    db.conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)

    async def no_names(*_a, **_k):
        return frozenset()

    monkeypatch.setattr(pa, "_own_names", no_names)
    monkeypatch.setattr(pa, "account_names", no_names)
    pushed: list = []

    async def push(frame):
        pushed.append(frame)
        return 1

    monkeypatch.setattr(voice_cards, "push", push)
    sessions: dict = {}
    monkeypatch.setattr(voice_session, "active_member", lambda panel: sessions.get(panel))
    fresh: dict = {}

    async def fresh_member(panel):
        return fresh.get(panel)

    monkeypatch.setattr(memories_card, "_fresh_panel_member", fresh_member)
    prc.reset()
    mv.reset()
    caller = {"user": None}
    app = FastAPI()
    assert memories_card.register(app) is True
    app.dependency_overrides[memories_card._caller] = lambda: caller["user"]
    yield type("Env", (), {"svc": s, "col": col, "audit": audit, "pushed": pushed, "sessions": sessions, "fresh": fresh,
                           "caller": caller, "client": TestClient(app), "db": db})
    prc.reset()
    mv.reset()


def put(env, text, *, user=ME, when="2026-10-06T02:00:00Z", **kw):
    return run(env.svc.ingest(text, user_id=user, source="chat_regex", confidence=0.9, session_id="s0", captured_at=when, **kw))


@pytest.fixture
def seeded(env):
    for t in (ME_SISTER, ME_ROUTINE, ME_BIRTHDAY, ME_HEALTH, ME_FIGHT, ME_SHED):
        put(env, t)
    put(env, ME_LIKE, source_excerpt="I like dark roast coffee with oat milk, every single morning.")
    put(env, f"My neighbour {OTHER_SECRET} keeps a parrot called Biscuit.", user=OTHER)
    put(env, f"I have {OTHER_SECRET} disease and I take insulin every day.", user=OTHER)
    return env


def say(text, member, *, panel=PANEL, channel="voice", verified=None, allow_writes=True):
    ctx = {"panel_id": panel, "scope_user": member}
    if verified is not None:
        ctx["speaker_verified"] = verified
    return run(mv.answer(text, "guest", "sess-voice", channel=channel, extra_ctx=ctx, allow_writes=allow_writes))


def approved(env, user=ME):
    return [r.text for r in run(env.svc.list_by_status(user_id=user, status="approved", limit=500))]


def row_id(env, needle, user=ME):
    return next(r.id for r in run(env.svc.list_by_status(user_id=user, status="approved", limit=500)) if needle in r.text)


def card_text(frame) -> str:
    return " ".join(str(v) for it in frame.get("items", []) for v in (it.get("text"), it.get("meta"))) + " " + str(
        frame.get("subtitle") or "")


def last_card(env):
    return next(f for f in reversed(env.pushed) if f.get("kind") == "memories")


def tap(env, token, item, action, choice=None):
    body = {"item": item, "action": action, **({"choice": choice} if choice else {})}
    return env.client.post("/api/panel/memories/act", json=body, headers={"X-Panel-Card": token} if token else {})


# ── flag off ──────────────────────────────────────────────────────────────────────────────────

def test_flag_off_is_inert(seeded, monkeypatch):
    monkeypatch.delenv("ZOE_MEMORIES_VOICE")
    out = run(mv.tier("what do you know about me?", "guest", "s", channel="voice",
                      extra_ctx={"panel_id": PANEL, "scope_user": ME}))
    assert out is None and not seeded.pushed
    app = FastAPI()
    assert memories_card.register(app) is False
    assert TestClient(app).post("/api/panel/memories/act", json={"item": "m1", "action": "forget"}).status_code == 404


# ── who is asking ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("member,verified", [(None, None), (ME, False), ("guest", None), ("guest-3f9a1c", None)])
def test_a_guest_gets_the_private_line_and_the_locked_card(seeded, member, verified):
    reply = say("Hey Zoe, what do you know about me?", member, verified=verified)
    assert reply == mv.PRIVATE_REPLY
    assert [f["kind"] for f in seeded.pushed] == ["locked"]
    blob = str(seeded.pushed)
    for needle in ("Marisol", "coffee", "migraine", OTHER_SECRET, "card_token"):
        assert needle not in blob


def test_a_member_hears_a_short_summary_and_gets_their_own_card(seeded):
    reply = say("what do you know about me?", ME)
    assert reply.startswith("Here's the short version.")
    assert "Marisol" in reply                                       # the newest person, named
    assert "migraine" not in reply.lower() and "doctor" not in reply.lower() and "fight" not in reply.lower()
    assert "private" in reply.lower()
    assert len(reply) < 420, "a SHORT spoken summary"
    frame = last_card(seeded)
    assert frame["member_id"] == ME and frame["private"] is True and frame["card_token"]
    txt = card_text(frame)
    assert "dark roast" in txt and "Marisol" in txt
    assert OTHER_SECRET not in txt and "Biscuit" not in txt
    assert "migraine" not in txt.lower() and "fight" not in txt.lower(), "D11: never on the wall"
    assert "private" in frame["subtitle"]
    groups = [it["group"] for it in frame["items"]]
    assert groups == sorted(groups, key=list(mv.GROUP_ORDER).index), "people, preferences, plans, about you"
    rows = [it for it in frame["items"] if it["id"].startswith("m")]
    assert rows and all(it["meta"] for it in rows), "every row says where it came from"
    assert all(it["meta"].startswith("You told me") for it in rows if not it["soft"])
    assert {a["key"] for a in rows[0]["actions"]} == {"wrong", "forget", "source"}
    # card-local ids only: no row id ever reaches the panel
    ids = {r.id for r in run(seeded.svc.list_by_status(user_id=ME, status="approved", limit=50))}
    assert not any(i in str(frame) for i in ids)


def test_member_b_never_sees_member_a(seeded):
    say("what do you know about me", OTHER)
    txt = card_text(last_card(seeded))
    assert "Biscuit" in txt
    assert "Marisol" not in txt and "dark roast" not in txt


def test_inferred_rows_are_marked_and_never_the_spoken_example(env):
    put(env, ME_LIKE)
    ref = run(env.svc.ingest("User seems to enjoy long hikes in the hills", user_id=ME, source="chat_regex", confidence=0.6,
                             session_id="s1", source_excerpt="we did a long hike in the hills on sunday", authority="inferred",
                             captured_at="2026-10-07T02:00:00Z"))
    assert ref is not None
    known = run(pa.gather(ME))
    hike = next(i for i in known.items if "hike" in i.text)
    assert hike.inferred
    reply = say("what do you know about me", ME)
    assert "hike" not in reply.lower(), "an inferred row is never offered as something the member said"
    assert "worked out" in reply
    row = next(it for it in last_card(env)["items"] if "hike" in it["text"].lower())
    assert row["soft"] is True and not row["meta"].startswith("You told me")


def test_this_week_reads_only_the_window(env):
    now = time.time()
    put(env, "I like dark roast coffee with oat milk.", when=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 2 * 86400)))
    put(env, ME_SHED, when=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 40 * 86400)))
    reply = say("what have you learned about me this week?", ME)
    assert "1 thing" in reply and "dark roast" in reply
    txt = card_text(last_card(env))
    assert "dark roast" in txt and "shed" not in txt


def test_a_person_of_mine_is_read_back_and_carded(seeded):
    reply = say("what do you remember about Marisol", ME)
    assert reply and "Marisol" in reply and "Lisbon" in reply
    frame = last_card(seeded)
    assert [it["id"] for it in frame["items"]] == ["p1"]


def test_a_name_that_is_not_one_of_my_people_is_left_to_recall(seeded):
    assert say("what do you remember about Paris", ME) is None
    assert say("what do you remember about Zibblewort", ME) is None      # OTHER's neighbour: not in MY people
    assert not seeded.pushed


def test_a_private_topic_is_never_said_on_the_wall(seeded):
    reply = say("what do you know about my health", ME)
    assert reply == mv.TOPIC_WALL_REPLY and "migraine" not in reply.lower()
    assert not seeded.pushed


def test_a_voice_lane_without_a_panel_identity_is_untouched(seeded):
    out = run(mv.answer("what do you know about me?", ME, "s", channel="voice", extra_ctx={"panel_id": PANEL}))
    assert out is None
    out = run(mv.answer("what do you know about me?", ME, "s", channel="chat", extra_ctx={}))
    assert out is None, "chat keeps provenance_answers' longer list"


@pytest.mark.parametrize("text", [
    "remind me to call Marisol tomorrow at 9", "add oat milk to the shopping list", "what's on my calendar today",
    "when is Marisol's birthday?", "what's on my plate?", "forget that", "forget what I said", "forget that I said that",
    "don't forget that I have a dentist appointment", "forget about Marisol", "why did you say that?", "turn on the lights",
    "what do you know about the weather", "I like dark roast coffee",
])
def test_other_intents_are_never_stolen(seeded, text):
    assert say(text, ME) is None, text
    assert run(mv.answer(text, ME, "s", channel="chat", extra_ctx={})) is None, text


# ── forget by voice ──────────────────────────────────────────────────────────────────────────

def test_forget_that_i_goes_through_the_audited_row_forget(seeded):
    rid = row_id(seeded, "dark roast")
    reply = say("forget that I like dark roast coffee", ME)
    assert reply == "Done - I've forgotten that you like dark roast coffee with oat milk.", reply
    assert not any("dark roast" in t for t in approved(seeded))
    assert any(a.get("mem_id") == rid and a.get("action") == "reject" for a in seeded.audit), "audited reject"
    assert any("Marisol" in t for t in approved(seeded)), "only that row"


def test_a_forgotten_row_never_reappears(seeded):
    say("forget that I like dark roast coffee", ME)
    view = run(memory_space.read_view(ME))
    assert "dark roast" not in str(view)
    packet = run(seeded.svc.load_for_prompt(ME, limit=50))
    assert "dark roast" not in str(packet)
    seeded.pushed.clear()
    say("what do you know about me", ME)
    assert "dark roast" not in card_text(last_card(seeded))
    assert put(seeded, ME_LIKE) is None, "the same words re-mined are not stored again"
    assert not any("dark roast" in t for t in approved(seeded))


def test_a_private_row_is_forgotten_without_being_read_out(seeded):
    reply = say("forget that I have migraines", ME)
    assert reply == "Done - I've forgotten that."
    assert not any("migraine" in t for t in approved(seeded))


def test_a_guest_cannot_forget_and_nothing_changes(seeded):
    assert say("forget that I like dark roast coffee", None) == mv.FORGET_PRIVATE_REPLY
    assert say("forget that I like dark roast coffee", ME, verified=False) == mv.FORGET_PRIVATE_REPLY
    assert any("dark roast" in t for t in approved(seeded))


def test_forgetting_another_members_words_finds_nothing(seeded):
    assert say("forget that I keep a parrot called Biscuit", ME) == mv.NOT_FOUND_REPLY
    assert any("Biscuit" in t for t in approved(seeded, OTHER))


def test_a_dry_turn_never_forgets(seeded):
    assert say("forget that I like dark roast coffee", ME, allow_writes=False) is None
    assert any("dark roast" in t for t in approved(seeded))


def test_two_different_rows_ask_which(env):
    put(env, "I like dark roast coffee in the morning.")
    put(env, "I like dark roast coffee after dinner.")
    assert say("forget that I like dark roast coffee", ME) == mv.WHICH_REPLY
    assert len([t for t in approved(env) if "dark roast" in t]) == 2


# ── the card: taps ────────────────────────────────────────────────────────────────────────────

def _member_card(env):
    say("what do you know about me", ME)
    frame = last_card(env)
    item = next(it for it in frame["items"] if "dark roast" in it["text"])
    return frame, item


def test_a_tap_needs_the_members_voice_session_or_their_own_session(seeded):
    frame, item = _member_card(seeded)
    assert tap(seeded, frame["card_token"], item["id"], "forget", "yes").status_code == 403      # session closed, no caller
    seeded.caller["user"] = {"user_id": OTHER, "role": "user"}
    assert tap(seeded, frame["card_token"], item["id"], "forget", "yes").status_code == 403      # someone else's session
    assert tap(seeded, "not-a-token", item["id"], "forget", "yes").status_code == 404
    assert any("dark roast" in t for t in approved(seeded))
    seeded.sessions[PANEL] = ME
    assert tap(seeded, frame["card_token"], "m999", "forget", "yes").status_code == 404           # not on this card
    assert tap(seeded, frame["card_token"], item["id"], "forget").status_code == 400              # confirm first
    r = tap(seeded, frame["card_token"], item["id"], "forget", "yes")
    assert r.status_code == 200 and r.json()["note"] == "Forgotten."
    assert not any("dark roast" in t for t in approved(seeded))


def test_wrong_then_say_it_supersedes_and_keeps_history(seeded):
    seeded.sessions[PANEL] = ME
    say("what do you know about me", ME)
    frame = last_card(seeded)
    item = next(it for it in frame["items"] if "birthday" in it["text"].lower())
    old = row_id(seeded, "14 March")
    r = tap(seeded, frame["card_token"], item["id"], "wrong", "voice")
    assert r.status_code == 200 and r.json()["keep"] is True
    reply = say("my birthday is on the 15th of March", ME)
    assert reply.startswith("Fixed"), reply
    texts = approved(seeded)
    assert any("15th of March" in t for t in texts) and not any("14 March" in t for t in texts)
    _doc, meta = seeded.col.rows[old]
    assert meta["status"] == "superseded" and meta.get("superseded_by_id"), "the old row is history, not deleted"


def test_a_one_word_fix_after_a_tap_swaps_only_that_value(env):
    put(env, "Swimming is on Friday at 9")
    old = row_id(env, "Swimming")
    mv.tap(PANEL, ME, old, "Swimming is on Friday at 9")
    reply = say("it's on Saturday", ME)
    assert reply.startswith("Fixed"), reply
    assert "Swimming is on Saturday at 9" in approved(env)
    assert env.col.rows[old][1]["status"] == "superseded"


def test_a_tap_is_only_for_the_next_turn_of_the_same_member(seeded):
    seeded.sessions[PANEL] = ME
    frame, item = _member_card(seeded)
    tap(seeded, frame["card_token"], item["id"], "wrong", "voice")
    assert say("forget it", None) is None                    # a guest's turn in between consumes and clears it
    assert say("forget it", ME) is None                      # so the member's "forget it" no longer means that row
    assert any("dark roast" in t for t in approved(seeded))
    tap(seeded, frame["card_token"], item["id"], "wrong", "voice")
    assert say("forget it", ME).startswith("Done")
    assert not any("dark roast" in t for t in approved(seeded))


def test_a_tap_expires(seeded):
    mv.tap(PANEL, ME, row_id(seeded, "dark roast"), ME_LIKE, now=time.monotonic() - mv.TAP_TTL_S - 1)
    assert say("forget it", ME) is None
    assert any("dark roast" in t for t in approved(seeded))


def test_a_command_after_a_tap_is_not_taken_as_the_correction(seeded):
    mv.tap(PANEL, ME, row_id(seeded, "dark roast"), ME_LIKE)
    assert say("turn on the lights", ME) is None             # a command is not the correction, and it clears the tap
    assert say("forget it", ME) is None                      # so a later "forget it" no longer means that row
    assert any("dark roast" in t for t in approved(seeded))
    mv.tap(PANEL, ME, row_id(seeded, "dark roast"), ME_LIKE)
    assert say("never mind", ME) == mv.LEAVE_IT_REPLY
    assert say("forget it", ME) is None
    assert any("dark roast" in t for t in approved(seeded))


def test_where_from_shows_exact_words_only_behind_a_fresh_pin(seeded):
    seeded.sessions[PANEL] = ME
    frame, item = _member_card(seeded)
    r = tap(seeded, frame["card_token"], item["id"], "source")
    assert r.status_code == 200 and r.json()["keep"] is True
    assert "PIN" in r.json()["detail"] and "oat milk" not in r.json()["detail"]
    seeded.caller["user"] = {"user_id": ME, "role": "user"}
    seeded.fresh[PANEL] = ME
    detail = tap(seeded, frame["card_token"], item["id"], "source").json()["detail"]
    assert "oat milk" in detail, detail
    seeded.caller["user"] = {"user_id": ME, "role": "user", "device_token_role": "panel"}
    assert "oat milk" not in tap(seeded, frame["card_token"], item["id"], "source").json()["detail"]


def test_forgetting_a_person_needs_a_fresh_pin_and_uses_the_existing_intent(seeded, monkeypatch):
    calls = []

    async def forget_person(member, name):
        calls.append((member, name))
        return "Done - I've forgotten Marisol."

    monkeypatch.setattr(mv, "forget_person", forget_person)
    seeded.sessions[PANEL] = ME
    say("what do you know about me", ME)
    frame = last_card(seeded)
    p = next(it for it in frame["items"] if it["id"].startswith("p") and "Marisol" in it["text"])
    r = tap(seeded, frame["card_token"], p["id"], "forget_person", "yes")
    assert r.status_code == 403 and r.json()["detail"] == "pin_needed" and not calls
    seeded.caller["user"] = {"user_id": ME, "role": "user"}
    seeded.fresh[PANEL] = ME
    r = tap(seeded, frame["card_token"], p["id"], "forget_person", "yes")
    assert r.status_code == 200 and calls == [(ME, "Marisol")]


def test_forget_person_is_the_memory_forget_entity_intent(monkeypatch):
    import intent_router

    seen = []

    async def execute(intent, user_id="guest"):
        seen.append((intent.name, dict(intent.slots), user_id))
        return "ok"

    monkeypatch.setattr(intent_router, "execute_intent", execute)
    assert run(mv.forget_person(ME, "Marisol")) == "ok"
    assert seen == [("memory_forget_entity", {"name": "Marisol"}, ME)]


def test_the_phone_qr_points_at_the_memories_page(seeded, monkeypatch):
    import auth_handoff
    import setup_qr

    monkeypatch.setattr(auth_handoff, "phone_base_url", lambda request: "https://zoe.example")
    monkeypatch.setattr(setup_qr, "render_svg", lambda url: f"<svg>{url}</svg>")
    seeded.sessions[PANEL] = ME
    frame, _item = _member_card(seeded)
    r = seeded.client.get("/api/panel/memories/phone-qr", headers={"X-Panel-Card": frame["card_token"]})
    assert r.status_code == 200 and "https://zoe.example/memories.html#about-me" in r.text


# ── curated rows (owner D7 / D9) ─────────────────────────────────────────────────────────────

def _curate(env, rid):
    doc, meta = env.col.rows[rid]
    env.col.rows[rid] = (doc, {**meta, "origin": memory_space.CURATED_ORIGIN})


def test_a_child_cannot_overwrite_or_forget_a_curated_row(env):
    put(env, "Grandma picks you up on Tuesdays", user=OTHER)
    rid = row_id(env, "Grandma", OTHER)
    _curate(env, rid)
    assert run(memory_space.forget_row(OTHER, rid)) == "protected"
    assert run(memory_space.correct_row(OTHER, rid, "Grandma picks me up every day")) == "protected"
    assert any("Grandma" in t for t in approved(env, OTHER))


def test_an_adult_may_change_their_own_curated_row(env):
    put(env, "Swimming is on Saturday at 9")
    rid = row_id(env, "Swimming")
    _curate(env, rid)
    assert run(memory_space.forget_row(ME, rid)) == "forgotten"


# ── the phone's correct route ────────────────────────────────────────────────────────────────

def _phone(user):
    from auth import get_current_user
    from database import get_db
    from routers.memories import router

    app = FastAPI()
    app.include_router(router)

    async def _user(request: Request):
        return dict(user)

    async def _no_db():
        yield None

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_db] = _no_db
    return TestClient(app)


H = {"X-Session-ID": "sess-member"}


def test_phone_actions_are_flag_dark(seeded, monkeypatch):
    c = _phone({"user_id": ME, "role": "user"})
    assert c.get("/api/memories/mine/actions", headers=H).json() == {"forget": True, "correct": True}
    monkeypatch.delenv("ZOE_MEMORIES_VOICE")
    assert c.get("/api/memories/mine/actions", headers=H).status_code == 404
    rid = row_id(seeded, "14 March")
    assert c.post(f"/api/memories/mine/{rid}/correct", json={"text": "My birthday is on 15 March."}, headers=H).status_code == 404


def test_phone_correct_supersedes_with_history(seeded):
    c = _phone({"user_id": ME, "role": "user"})
    rid = row_id(seeded, "14 March")
    r = c.post(f"/api/memories/mine/{rid}/correct", json={"text": "My birthday is on 15 March."}, headers=H)
    assert r.status_code == 200, r.text
    assert "My birthday is on 15 March." in approved(seeded)
    assert seeded.col.rows[rid][1]["status"] == "superseded"
    assert any(a.get("action") == "edit" for a in seeded.audit), "audited edit"


def test_phone_correct_refuses_guests_and_other_members_rows(seeded):
    rid = row_id(seeded, "Biscuit", OTHER)
    c = _phone({"user_id": ME, "role": "user"})
    r = c.post(f"/api/memories/mine/{rid}/correct", json={"text": "My neighbour keeps a cat."}, headers=H)
    assert r.status_code == 404
    assert any("Biscuit" in t for t in approved(seeded, OTHER))
    for guest in ({"user_id": "guest", "role": "guest"}, {"user_id": ME, "role": "guest"}):
        mine = row_id(seeded, "14 March")
        assert _phone(guest).post(f"/api/memories/mine/{mine}/correct", json={"text": "x y z w"},
                                  headers=H).status_code in (401, 403)
    assert _phone({"user_id": ME, "role": "user"}).post(f"/api/memories/mine/{row_id(seeded, '14 March')}/correct",
                                                        json={"text": "x"}).status_code == 401      # no session header


# ── provenance labels ────────────────────────────────────────────────────────────────────────

def test_provenance_labels():
    it = pa.Item("r1", "User likes tea", 1760000000.0, "preferences", origin="omi_review", cls="user_confirmed")
    assert memory_space.provenance_label(it).startswith("From your day review, approved by you on ")
    it = pa.Item("r1", "User likes tea", 1760000000.0, "preferences", cls="user_confirmed")
    assert memory_space.provenance_label(it).startswith("You confirmed this on ")
    it = pa.Item("r1", "User likes tea", 1760000000.0, "preferences", cls="user_stated")
    assert memory_space.provenance_label(it).startswith("You told me on ")
    it = pa.Item("r1", "User likes tea", None, "preferences", inferred=True, cls="model_from_transcript")
    assert memory_space.provenance_label(it) == "I worked this out"


# ── static walls ─────────────────────────────────────────────────────────────────────────────

def test_no_parallel_delete_path():
    """Every forget / correction here goes through memory_space / provenance_answers / the forget intent: this tier never
    reviews, erases or deletes a memory row itself."""
    from pathlib import Path

    for name in ("memories_voice.py", "routers/memories_card.py"):
        src = (Path(memory_space.__file__).parent / name).read_text(encoding="utf-8")
        for needle in (".review(", "erase_rows", "DELETE FROM", "hard_delete", "delete_user(", "forget_last("):
            assert needle not in src, f"{name} must not call {needle} itself"


def test_forget_a_person_keeps_the_members_calendar_and_lists():
    """Owner D6 (2026-10-18/19): "forget Sam" keeps the owner's own calendar events and list items. The cascade never deletes from
    either store (an event only loses Sam as an attendee)."""
    from pathlib import Path

    src = (Path(memory_space.__file__).parent / "memory_forget_cascade.py").read_text(encoding="utf-8")
    assert "DELETE FROM events" not in src and "list_items" not in src and "DELETE FROM reminders" not in src


# ── break-the-fix controls ───────────────────────────────────────────────────────────────────

def test_control_without_the_identity_check_a_guest_would_hear_rows(seeded, monkeypatch):
    monkeypatch.setattr(prc, "_member", lambda user_id, channel, ctx: ME)
    reply = say("what do you know about me?", None)
    assert reply != mv.PRIVATE_REPLY and "Marisol" in reply


def test_control_without_the_restraint_wall_family_conflict_would_reach_the_card(seeded, monkeypatch):
    monkeypatch.setattr(prc, "_private", lambda text: False)
    say("what do you know about me", ME)
    assert "fight" in card_text(last_card(seeded)).lower()


def test_control_without_the_owner_filter_another_members_row_would_be_forgotten(seeded, monkeypatch):
    real = pa.gather

    async def everyone(user_id, *, svc=None):
        a = await real(ME, svc=svc)
        b = await real(OTHER, svc=svc)
        a.items += b.items
        return a

    monkeypatch.setattr(pa, "gather", everyone)
    monkeypatch.setattr(pa, "_owns", lambda meta, uid: True)
    reply = say("forget that I keep a parrot called Biscuit", ME)
    assert reply != mv.NOT_FOUND_REPLY, "own-rows-only reading is what kept another member's row out of reach"
    # the store's own owner check is a second wall: the reject is refused, so the row survives even then
    assert any("Biscuit" in t for t in approved(seeded, OTHER))
