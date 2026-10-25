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
    # adulthood is read from the AUTHORITATIVE account (auth_users + member_modes), never the users mirror
    db.conn.execute("CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, role TEXT)")
    db.conn.executemany("INSERT INTO auth_users VALUES (?, ?)", [(ME, "user"), (OTHER, "child")])
    db.conn.execute("CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT, minor INTEGER)")
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


def test_forget_names_the_number_not_just_the_words(env):
    # Codex P1 on #2144: the tokenizer dropped digits, so "12 Main Street" and "14 Main Street" were the same token set, the
    # ambiguity check never fired and the first-sorted row was forgotten whichever the member named.
    put(env, "My pickup is at 12 Main Street.")
    put(env, "My pickup is at 14 Main Street.", when="2026-10-06T05:00:00Z")
    assert say("forget that my pickup is at 12 Main Street", ME).startswith("Done")
    left = [t for t in approved(env) if "Main Street" in t]
    assert left == ["My pickup is at 14 Main Street."], left
    put(env, "My pickup is at 16 Main Street.", when="2026-10-06T07:00:00Z")
    assert say("forget that my pickup is at Main Street", ME) == mv.WHICH_REPLY     # no number named: ask, never guess
    assert len([t for t in approved(env) if "Main Street" in t]) == 2


def test_equal_token_sets_from_different_rows_ask_which(env):
    # Codex P1 on #2144: two different rows that normalise to the same token set ({sam, love}) left the leftover-words check with
    # nothing to compare, so the first-sorted (newest) row was forgotten for good whichever the member meant. Neither says
    # exactly what was asked -> ask, forget nothing.
    put(env, "Sam, I love.", when="2026-10-06T02:00:00Z")
    put(env, "Sam loves me.", when="2026-10-06T08:00:00Z")
    assert say("forget that I love Sam", ME) in (mv.WHICH_REPLY, mv.DIFFERENT_REPLY)   # either way: asked, nothing erased
    assert sorted(t for t in approved(env) if "Sam" in t) == ["Sam loves me.", "Sam, I love."], "nothing forgotten"


def test_an_exact_text_match_still_forgets_uniquely_among_equal_token_sets(env):
    put(env, "I love Sam.", when="2026-10-06T02:00:00Z")
    put(env, "Sam loves me.", when="2026-10-06T08:00:00Z")      # newer, so it sorts first on token set + recency alone
    assert say("forget that I love Sam", ME).startswith("Done")
    assert [t for t in approved(env) if "Sam" in t] == ["Sam loves me."], "only the row that says exactly that"


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


def test_a_tapped_forget_that_hits_a_store_hiccup_keeps_the_tap_armed(seeded, monkeypatch):
    # Codex P2 on #2144: the tap was consumed before the forget ran, so after STORE_DOWN_REPLY the "forget it" the reply invites
    # had no target. Forgetting is permanent, so the retry must finish the same row.
    rid = row_id(seeded, "dark roast")
    real, down = memory_space.forget_row, {"on": True}

    async def flaky(*a, **kw):
        if down["on"]:
            raise RuntimeError("store hiccup")
        return await real(*a, **kw)

    monkeypatch.setattr(memory_space, "forget_row", flaky)
    mv.tap(PANEL, ME, rid, ME_LIKE)
    assert say("forget it", ME) == pa.STORE_DOWN_REPLY
    assert any("dark roast" in t for t in approved(seeded)), "nothing was forgotten yet"
    down["on"] = False
    assert say("forget it", ME).startswith("Done"), "the tap stayed armed: the repeat finishes the same row"
    assert not any("dark roast" in t for t in approved(seeded))
    assert say("forget it", ME) is None, "the tap is spent once the forget succeeded"


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


def test_a_correction_is_not_acknowledged_while_a_twin_still_holds_the_old_value(env, monkeypatch):
    # Codex P1 on #2144: a twin whose retire the store refused left the old value approved while the API said "corrected"; and the
    # superseded primary then answered "gone", so the partial write could never be finished. Now: raise, then the retry finishes.
    put(env, "I keep my bike in the shed.")
    put(env, "My bike lives in the shed behind the garage.")
    ra, rb = row_id(env, "keep my bike"), row_id(env, "bike lives")

    async def twins(user_id, ref, *, svc=None, **_kw):
        b = await env.svc.get(rb)
        return [b] if ref.id == ra and b is not None and (b.metadata or {}).get("status", "approved") == "approved" else []

    monkeypatch.setattr(pa, "twins_of", twins)
    real_review, refuse = env.svc.review, {"on": True}

    async def review(mem_id, *a, **kw):
        if refuse["on"] and mem_id == rb and kw.get("decision") == "reject":
            return None                                  # the store refused the twin's retire
        return await real_review(mem_id, *a, **kw)

    monkeypatch.setattr(env.svc, "review", review)
    with pytest.raises(RuntimeError):
        run(memory_space.correct_row(ME, ra, "I keep my bike in the garage."))
    assert any("bike lives" in t for t in approved(env)), "the twin still recalls the old value"
    refuse["on"] = False
    assert run(memory_space.correct_row(ME, ra, "I keep my bike in the garage.")) == "corrected"   # the retry finishes it
    assert not any("bike lives" in t for t in approved(env))
    assert any("in the garage" in t for t in approved(env))
    # a superseded row asked for DIFFERENT words is not a retry of this correction: one answer, gone
    assert run(memory_space.correct_row(ME, ra, "I keep my bike at work.")) == "gone"


def test_a_tapped_correction_is_not_acknowledged_while_a_twin_holds_the_old_value(env, monkeypatch):
    # Codex P1 on #2144: the tapped path used provenance_answers.fix, whose twin loop swallowed a refused retire and said "Fixed"
    # with the tap consumed - repeating the correction could never retire the stale approved twin. Now: not acknowledged, the tap
    # stays armed, and saying it again finishes the twin.
    put(env, "Swimming is on Friday at 9")
    put(env, "Swimming lessons are on Friday at 9 am")
    ra, rb = row_id(env, "Swimming is"), row_id(env, "Swimming lessons")

    async def twins(user_id, ref, *, svc=None, **_kw):
        b = await env.svc.get(rb)
        return [b] if ref.id == ra and b is not None and (b.metadata or {}).get("status", "approved") == "approved" else []

    monkeypatch.setattr(pa, "twins_of", twins)
    real_review, refuse = env.svc.review, {"on": True}

    async def review(mem_id, *a, **kw):
        if refuse["on"] and mem_id == rb and kw.get("decision") == "reject":
            return None                                  # the store refused the twin's retire
        return await real_review(mem_id, *a, **kw)

    monkeypatch.setattr(env.svc, "review", review)
    mv.tap(PANEL, ME, ra, "Swimming is on Friday at 9")
    assert say("it's on Saturday", ME) == pa.STORE_DOWN_REPLY, "a stale approved twin must not be acknowledged as fixed"
    assert any("lessons are on Friday" in t for t in approved(env)), "the twin still recalls the old value"
    refuse["on"] = False
    reply = say("it's on Saturday", ME)                   # the tap stayed armed: the same words finish the job
    assert reply.startswith("Fixed"), reply
    assert not any("Friday" in t for t in approved(env)), approved(env)
    assert "Swimming is on Saturday at 9" in approved(env)


def test_a_failed_twin_lookup_stops_the_write_before_it_happens(env, monkeypatch):
    # Codex P1 on #2144: twins_of swallowed a transient list_by_status failure and returned [], so a correction (or forget) went
    # ahead, retired nothing and reported success while an approved twin still recalled the old value. Now the write is not made.
    put(env, "Swimming is on Friday at 9")
    put(env, "Swimming lessons are on Friday at 9 am")
    ra = row_id(env, "Swimming is")
    real_list, down = env.svc.list_by_status, {"on": True}

    async def list_by_status(*a, **kw):
        if down["on"]:
            raise RuntimeError("store hiccup")
        return await real_list(*a, **kw)

    monkeypatch.setattr(env.svc, "list_by_status", list_by_status)
    with pytest.raises(RuntimeError):
        run(memory_space.correct_row(ME, ra, "Swimming is on Saturday at 9"))
    with pytest.raises(RuntimeError):
        run(memory_space.forget_row(ME, ra))
    assert env.col.rows[ra][1]["status"] == "approved", "nothing was written while the twins could not be looked up"
    mv.tap(PANEL, ME, ra, "Swimming is on Friday at 9")
    assert say("it's on Saturday", ME) == pa.STORE_DOWN_REPLY          # the tapped path is strict too, and stays armed
    assert env.col.rows[ra][1]["status"] == "approved"
    down["on"] = False
    assert say("it's on Saturday", ME).startswith("Fixed")             # the same words, store back: the whole job
    assert not any("Friday" in t for t in approved(env)), approved(env)


def test_a_child_whose_users_mirror_says_member_is_still_locked(env):
    # _ensure_user_and_chat_session writes role='member' to the data-plane users mirror for EVERY chatting account, a child's
    # included - the lock must read the authoritative account, not the mirror (Codex P1 on #2144).
    env.db.conn.execute("UPDATE users SET role = 'member' WHERE id = ?", (OTHER,))
    env.db.conn.commit()
    put(env, "Grandma picks you up on Tuesdays", user=OTHER)
    rid = row_id(env, "Grandma", OTHER)
    _curate(env, rid)
    assert run(memory_space.forget_row(OTHER, rid)) == "protected"
    assert run(memory_space.correct_row(OTHER, rid, "Grandma picks me up every day")) == "protected"


def test_a_minor_member_mode_locks_even_an_adult_role(env):
    env.db.conn.execute("UPDATE auth_users SET role = 'member' WHERE user_id = ?", (OTHER,))
    env.db.conn.execute("INSERT INTO member_modes VALUES (?, 'kid', 1)", (OTHER,))
    env.db.conn.commit()
    put(env, "Grandma picks you up on Tuesdays", user=OTHER)
    rid = row_id(env, "Grandma", OTHER)
    _curate(env, rid)
    assert run(memory_space.forget_row(OTHER, rid)) == "protected"


def test_an_unknown_account_locks_a_curated_row(env):
    env.db.conn.execute("DELETE FROM auth_users WHERE user_id = ?", (ME,))
    env.db.conn.commit()
    put(env, "Swimming is on Saturday at 9")
    rid = row_id(env, "Swimming")
    _curate(env, rid)
    assert run(memory_space.forget_row(ME, rid)) == "protected"


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
    for guest in ({"user_id": "guest", "role": "guest"}, {"user_id": ME, "role": "guest"},
                  {"user_id": ME, "role": "user", "device_token_role": "panel"},          # the panel device token
                  {"user_id": ME, "role": "user", "auth_degraded": True}):              # an auth outage
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


# ── Codex round 3 on #2144 ──────────────────────────────────────────────────────────────────────

def test_covers_matches_true_inflections_only_never_a_prefix():
    tok = mv._tokens
    assert mv._covers(tok("I like plans"), tok("I like planned trips and plans"))
    assert not mv._covers(tok("I like plans"), tok("I like planets")), "plan is a prefix of planet, not a variant of it"
    assert not mv._covers(tok("I like plant"), tok("I like plans")) and not mv._covers(tok("I like plan"), tok("I like plant"))
    for a, b in (("plan", "plans"), ("love", "loves"), ("love", "loved"), ("love", "loving"), ("plan", "planned"), ("walk", "walking")):
        assert mv._covers(tok(a), tok(b)) and mv._covers(tok(b), tok(a)), (a, b)
    for a, b in (("plan", "planet"), ("mat", "matter"), ("walk", "walnut"), ("cart", "carton")):
        assert not mv._covers(tok(a), tok(b)), (a, b)


def test_forget_that_i_like_plans_does_not_erase_planets(env):
    put(env, "I like planets and telescopes")
    assert say("forget that I like plans", ME) == mv.NOT_FOUND_REPLY
    assert any("planets" in t for t in approved(env)), "forgetting is permanent: a near-miss word must not take the row"
    put(env, "I like plans on Sundays")
    assert say("forget that I like plans", ME).startswith("Done")
    assert any("planets" in t for t in approved(env)) and not any("plans on Sundays" in t for t in approved(env))


def test_a_tapped_correction_whose_preflight_read_fails_keeps_the_tap_armed(env, monkeypatch):
    put(env, "Swimming is on Friday at 9")
    rid = row_id(env, "Swimming")
    real, down = env.svc.get, {"on": True}

    async def flaky(row):
        if down["on"]:
            raise RuntimeError("store hiccup")
        return await real(row)

    monkeypatch.setattr(env.svc, "get", flaky)
    mv.tap(PANEL, ME, rid, "Swimming is on Friday at 9")
    assert say("it's on Saturday", ME) == pa.STORE_DOWN_REPLY, "said, not silently dropped to another tier"
    assert "Swimming is on Friday at 9" in approved(env)
    down["on"] = False
    assert say("it's on Saturday", ME).startswith("Fixed"), "the tap stayed armed: the retry finishes the same row"


@pytest.mark.parametrize("question", ["what do you know about me?", "what did you learn about me this week", "what do you remember about Marisol"])
def test_the_reply_only_says_it_is_on_the_screen_if_a_card_was_delivered(seeded, monkeypatch, question):
    put(seeded, "I like quiet mornings", when=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))   # inside "this week", whenever this runs
    assert "on the screen" in say(question, ME)                       # delivered (the fixture's push returns 1)
    sent: list = []

    async def nowhere(frame):
        sent.append(frame)
        return 0                                                      # no live socket on that panel

    monkeypatch.setattr(voice_cards, "push", nowhere)
    reply = say(question, ME)
    assert sent and reply, "a card was attempted"
    assert "screen" not in reply.lower() and "tap one" not in reply.lower(), reply


# -- Codex round 5 on #2144 -----------------------------------------------------------------------------------------------------

def test_single_letter_terms_distinguish_facts(env):
    assert mv._tokens("I take vitamin C") != mv._tokens("I take vitamin D")
    put(env, "I take vitamin D daily")
    assert say("forget that I take vitamin C", ME) == mv.NOT_FOUND_REPLY, "forgetting is permanent: C is not D"
    assert any("vitamin D" in t for t in approved(env))


def test_find_person_requires_the_full_name_when_a_surname_is_spoken():
    import asyncio
    from types import SimpleNamespace as NS
    wall = NS(people=[NS(name="Sam Patel")])

    def find(n):
        return asyncio.run(mv._find_person(ME, wall, n))

    assert find("Sam Wilson") is None
    assert find("Sam Patel") is wall.people[0]
    assert find("Sam") is wall.people[0], "a bare first name keeps the unique-first-name fallback"


def test_phone_actions_follow_the_role_capabilities(seeded, monkeypatch):
    import routers.memories as rm
    from fastapi import HTTPException

    denied = set()

    async def fake(db, user, *, feature, action, surface="api"):
        if action in denied:
            raise HTTPException(status_code=403, detail="no")

    monkeypatch.setattr(rm, "require_feature_access", fake)
    c = _phone({"user_id": ME, "role": "user"})
    denied.add("review")
    assert c.get("/api/memories/mine/actions", headers=H).json() == {"forget": False, "correct": False}
    denied.add("read")
    assert c.get("/api/memories/mine/actions", headers=H).status_code == 403


# ── last review round on #2144 ────────────────────────────────────────────────────────────────
def test_a_lone_opposite_polarity_row_is_never_erased(env):
    put(env, "I don't like jazz.")
    assert say("forget that I like jazz", ME) == mv.DIFFERENT_REPLY
    assert approved(env) == ["I don't like jazz."]
    put(env, "I like blues music.")
    assert say("forget that I don't like blues music", ME) in (mv.DIFFERENT_REPLY, mv.NOT_FOUND_REPLY)
    assert "I like blues music." in approved(env)


def test_a_lone_row_with_who_did_what_swapped_is_never_erased(env):
    put(env, "Sam loves me.")
    assert say("forget that I love Sam", ME) == mv.DIFFERENT_REPLY
    assert approved(env) == ["Sam loves me."]


def test_the_agreeing_row_goes_when_an_opposite_one_sits_beside_it(env):
    put(env, "I don't like jazz.", when="2026-10-06T02:00:00Z")
    put(env, "I like jazz.", when="2026-10-06T03:00:00Z")
    assert say("forget that I like jazz", ME).startswith("Done")
    assert approved(env) == ["I don't like jazz."]


def test_a_retry_after_a_half_done_untapped_forget_finishes_the_same_row(seeded, monkeypatch):
    real, state = pa._erase_words, {"down": True, "ran": 0}

    async def flaky(*a, **kw):
        state["ran"] += 1
        if state["down"]:
            raise RuntimeError("cleanup failed")    # the row is already rejected when the cleanup step fails
        return await real(*a, **kw)

    monkeypatch.setattr(pa, "_erase_words", flaky)
    assert say("forget that I like dark roast coffee", ME) == pa.STORE_DOWN_REPLY
    assert not any("dark roast" in t for t in approved(seeded)), "the row is already rejected: a fresh search cannot see it"
    state["down"] = False
    assert say("forget that I like dark roast coffee", ME) != mv.NOT_FOUND_REPLY, "the repeat finds the same row"
    assert state["ran"] == 2, "and re-runs the cleanup step that failed"
    assert say("forget that I like dark roast coffee", ME) == mv.NOT_FOUND_REPLY, "the retry target is spent"


def test_panel_card_actions_need_the_review_capability(seeded, monkeypatch):
    from fastapi import HTTPException

    seeded.sessions[PANEL] = ME
    say("what do you know about me", ME)
    frame = last_card(seeded)
    token = frame["card_token"]
    item = next(it for it in frame["items"] if "dark roast" in it["text"])["id"]
    denied = {"on": True}

    async def fake(db, user, *, feature, action, surface="api"):
        assert (feature, action) == ("memories", "review") and user["user_id"] == ME
        if denied["on"]:
            raise HTTPException(status_code=403, detail="no")

    monkeypatch.setattr(memories_card, "require_feature_access", fake)
    assert tap(seeded, token, item, "forget", "yes").status_code == 403
    assert tap(seeded, token, item, "wrong", "voice").status_code == 403
    assert any("dark roast" in t for t in approved(seeded)), "nothing changed"
    assert say("forget it", ME) is None, "no correction was armed"
    assert tap(seeded, token, item, "source").status_code == 200, "reading where it came from is not a review action"
    denied["on"] = False
    assert tap(seeded, token, item, "forget", "yes").status_code == 200
    assert not any("dark roast" in t for t in approved(seeded))


def test_an_inferred_person_fact_is_not_said_as_told(env):
    put(env, "User's sister Marisol lives in Lisbon.")
    put(env, "User's sister Marisol is a nurse.", when="2026-10-06T03:00:00Z")
    rows = run(env.svc.list_by_status(user_id=ME, status="approved", limit=50))
    lisbon = next(r for r in rows if "Lisbon" in r.text)
    real = pa.gather

    async def gather(member, **kw):
        known = await real(member, **kw)
        for it in known.items:
            if it.id == lisbon.id:
                it.inferred = True
        return known

    import pytest as _p
    mp_ = _p.MonkeyPatch()
    mp_.setattr(pa, "gather", gather)
    try:
        wall = run(mv.gather_wall(ME))
    finally:
        mp_.undo()
    e = next(x for x in wall.people if x.name == "Marisol")
    assert mv._inferred_count([e]) == 1
    said = mv.spoken_person(e)
    assert "Lisbon" not in said and "nurse" in said and "worked out 1 thing" in said, said
    assert "worked out 1 thing" in mv.spoken_me(wall)
