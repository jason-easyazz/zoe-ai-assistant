"""The day review as a conversation (memories-space P6): ``day_review_raise.py`` + its seams in ``brief_first_turn``,
``fast_tiers``, ``routers/proactive`` (the orb) and ``routers/voice_tts`` (the per-turn identity note).

SYNTHETIC data only: invented ids, invented candidates in a migrated SQLite (0045 + 0054). The REAL omi_day_review reads
(``pending_counts`` / ``raise_facts``) run against it; the member's raise state (``user_prefs``, Postgres-only SQL) is an
in-memory dict behind the module's two state seams.

Negative controls (run by hand-mutating the code and watching the named test go red; re-run after touching the rule):
  * flag gate          -> make ``enabled()`` ignore ``ZOE_OMI_DAY_REVIEW`` (shadow test) / return True (flag-off test)
  * once a day         -> drop the ``state.get("day") == _local_day(now)`` check in ``prepare``
  * snooze             -> make ``_snoozed`` return False (two-ignores test); drop ``facts.get("decided_since")`` (engagement test)
  * guest / other      -> make ``turn_gate`` skip the ``scope_user != uid`` check; let an unnoted ``voice-`` session through
  * crowded room       -> make ``others_present`` return False (raise + pull tests)
  * nothing sensitive  -> make ``summary`` / ``raise_line`` render a candidate's payload title
  * no writes          -> call ``omi_review_apply.write_list_item`` from ``answer`` (the write guard fires)
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import sqlalchemy as sa

import brief_first_turn as bft
import day_review_raise as drr
import db_compat
import omi_review_apply
from test_omi_day_review import _Sqlite, _migrate

SVC = Path(__file__).resolve().parents[1]
PERTH = ZoneInfo("Australia/Perth")
OWNER, OTHER = "owner-a", "member-b"
PANEL = "panel-kitchen"
VSID = "voice-panel-panel-kitchen-0001"
SENSITIVE = "chemotherapy appointment"          # a health-class candidate title: must never be spoken


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=PERTH).astimezone(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine("sqlite:///" + path)
    _migrate(engine, "0045_omi_recordings.py")
    _migrate(engine, "0054_omi_day_review.py")
    db = _Sqlite(path)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    for key in ("ZOE_BRIEF_WINDOW_START", "ZOE_BRIEF_WINDOW_END", "ZOE_PANEL_REVIEW_CARDS", "ZOE_PUBLIC_URL",
                "ZOE_DAY_REVIEW_RAISE_SNOOZE_DAYS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZOE_DAY_REVIEW_RAISE", "1")
    monkeypatch.setenv("ZOE_OMI_DAY_REVIEW", "on")
    monkeypatch.setenv("ZOE_BRIEF_ON_FIRST_TURN", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    monkeypatch.setenv("ZOE_PULL_NOT_PUSH", "off")

    prefs: dict = dict()

    async def load_state(uid):
        p = prefs.get(uid, dict())
        st = p.get(drr.STATE_KEY)
        return (dict(st) if isinstance(st, dict) else dict()), str(p.get(drr.ENGAGED_KEY) or "")

    async def save(uid, key, value):
        prefs.setdefault(uid, dict())[key] = json.loads(json.dumps(value))

    monkeypatch.setattr(drr, "_load_state", load_state)
    monkeypatch.setattr(drr, "_save", save)
    clk = [100_000.0]
    monkeypatch.setattr(drr, "_clock", lambda: clk[0])
    drr.reset()
    bft._reset_state()

    state = dict(now=at(20, 8), ctx=dict(), claims=[], db=db, prefs=prefs, clk=clk, sent=[], cards=[])

    async def claim_state(uid, now):
        return "free"

    async def take_claim(uid, now):
        state["claims"].append(uid)
        return True

    async def gather(uid, local_date):
        return state["ctx"]

    monkeypatch.setattr(bft, "_claim_state", claim_state)
    monkeypatch.setattr(bft, "_take_claim", take_claim)
    monkeypatch.setattr(bft, "_gather", gather)
    monkeypatch.setattr(bft, "_now_utc", lambda: state["now"])
    monkeypatch.setattr(drr, "_now", lambda: state["now"])

    # The phone and the panel: recorded, never sent.
    async def fake_send(tid, text, url=None, label=None, *, client=None):
        state["sent"].append((tid, text, url))
        return "sent"

    async def tid_of(member):
        return "424242" if member == OWNER else None

    import telegram_notify
    import voice_cards

    monkeypatch.setattr(telegram_notify, "send", fake_send)
    monkeypatch.setattr(telegram_notify, "available", lambda: True)
    monkeypatch.setattr(drr, "_telegram_id", tid_of)

    async def fake_push(frame):
        state["cards"].append(frame)
        return 1

    monkeypatch.setattr(voice_cards, "push", fake_push)

    # NO WRITES: every writer of the approve path explodes if raising or pulling ever reaches it.
    for name in dir(omi_review_apply):
        if name.startswith("write_") or name in ("approve", "approve_candidate", "apply"):
            monkeypatch.setattr(omi_review_apply, name, _boom(name))
    yield state
    drr.reset()
    bft._reset_state()


def _boom(name):
    async def boom(*a, **k):
        raise AssertionError("the raise / pull called a write path: " + name)
    return boom


def add_cand(env, kind, title, *, user=OWNER, status="pending", created=None, decided=None, day="2026-10-19"):
    n = len(env["db"].rows("SELECT id FROM omi_day_candidates"))
    env["db"].conn.execute(
        "INSERT INTO omi_day_candidates (id, review_id, user_id, day_local, kind, payload, dedup_key, status, decided_at, created_at) "
        "VALUES (?, 'rev1', ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c%d" % n, user, day, kind, json.dumps(dict(title=title, item=title)), "k%d" % n, status, decided,
         created or _iso(at(20, 4))))
    env["db"].conn.commit()


def seed(env):
    add_cand(env, "calendar_event", "Project check-in")
    add_cand(env, "calendar_event", "Dentist with Sam")
    add_cand(env, "list_item", "oat milk")
    add_cand(env, "reminder", SENSITIVE)
    add_cand(env, "list_item", "secret item", user=OTHER)


def heard(env, *, scope=OWNER, verdict=True, claimed=OWNER, panel=PANEL, sid=VSID):
    drr.note_voice_turn(sid, panel, scope_user=scope, verdict=verdict, claimed=claimed)


def warm(env):
    """The process has listened for longer than the crowd window (unknown is crowded before that)."""
    drr.note_voice_turn("voice-panel-boot", "panel-other", scope_user=None, verdict=None, claimed=None)
    env["clk"][0] += drr.CROWD_WINDOW_S + 1


def snapshot(env):
    return env["db"].rows("SELECT id, status, decided_at, payload FROM omi_day_candidates ORDER BY id")


async def brief(env, text="good morning", user=OWNER, sid="chat-session-1"):
    return await bft.prepare(text, user, sid)


def vctx(scope=OWNER):
    return dict(panel_id=PANEL, scope_user=scope)


# --- the words -----------------------------------------------------------------------------------------------------------------
def test_the_lines_are_counts_only():
    counts = dict(calendar_event=2, list_item=1, reminder=1)
    assert drr.raise_line(counts) == ("I went through your day - there are four things for you to look at whenever you like "
                                      "(two events, one thing for your lists and one reminder).")
    assert drr.summary(counts) == "I went through your day and found two events, one thing for your lists and one reminder."
    assert drr.phone_text(counts) == "I went through your day: four things are waiting for you to look at."
    assert drr.summary(dict(person=1, owner_fact=3, mystery=2)) == (
        "I went through your day and found one person to remember, three things about you and two other things.")
    # #2126's kinds: a fact or a relationship about a tagged person is said as one group, never by name
    assert drr.summary(dict(person_fact=1, person_relation=2)) == (
        "I went through your day and found three things about people you know.")


@pytest.mark.parametrize("text", [
    "what did you find in my day?", "Zoe, what did you find in my day", "what did you find today", "anything from today?",
    "is there anything from my day", "anything from my pendant", "what did you get from my day", "what's in my day review",
    "so what did you pick up from today, Zoe?", "how did my day review go",
])
def test_pull_phrases(text):
    assert drr.classify(text) == "pull"


@pytest.mark.parametrize("text", [
    # other tiers' and intents' phrasings are never stolen
    "what's up", "anything for me", "what have I got", "what's on my plate?", "review my day", "what did my pendant hear",
    "name the new voices", "what do you know about me", "what's on today", "anything on today", "what's on my calendar today",
    "remind me to call the plumber tomorrow", "add milk to the shopping list", "what's the weather today",
    "what did you find on the internet about Bali", "anything from the shops", "set a timer for ten minutes", "good morning",
    "what's new",
])
def test_no_other_phrase_is_ours(text):
    assert drr.classify(text) is None


def test_ours_are_nobody_elses_and_nobody_elses_are_ours():
    import panel_review_cards as prc
    import proactive.pull as pl

    ours = ["what did you find in my day", "anything from today", "anything from my day", "what did you find today",
            "send my day to my phone"]
    for t in ours:
        assert prc.shape(t) is None and pl.classify(t) is None, t
    for t in sorted(pl.PULL_PHRASES):
        assert drr.classify(t) is None, t


def test_decline_phrases():
    for t in ("not now", "Not now, Zoe", "leave it", "maybe later", "no thanks", "don't mention it again",
              "stop telling me about my day"):
        assert drr.is_decline(t), t
    for t in ("no", "not now turn on the lights", "add later to the list", "yes"):
        assert not drr.is_decline(t), t


# --- flag gates ------------------------------------------------------------------------------------------------------------------
async def test_flag_off_means_nothing_anywhere(env, monkeypatch):
    seed(env)
    warm(env)
    monkeypatch.setenv("ZOE_DAY_REVIEW_RAISE", "0")
    b = await brief(env)
    assert b is None or "pendant day review" not in b.body
    assert await drr.tier("what did you find in my day", OWNER, channel="chat") is None
    assert await drr.orb_review(OWNER) is None
    assert await drr.pull_addendum(OWNER, "chat", None) == ""
    drr.note_voice_turn(VSID, PANEL, scope_user=OWNER, verdict=False, claimed=None)
    assert VSID not in drr._TURNS and PANEL not in drr._HEARD
    assert env["prefs"] == dict()


async def test_a_shadow_day_review_never_talks(env, monkeypatch):
    seed(env)
    monkeypatch.setenv("ZOE_OMI_DAY_REVIEW", "shadow")
    assert await drr.prepare(OWNER, "chat-1") is None
    assert await drr.tier("anything from today", OWNER, channel="chat") is None


# --- the raise -------------------------------------------------------------------------------------------------------------------
async def test_a_greeting_carries_one_counts_only_line_and_settle_records_it(env):
    seed(env)
    b = await brief(env)
    assert b is not None and b.day_review is not None
    assert "I went through your day - there are four things" in b.body
    assert "two events, one thing for your lists and one reminder" in b.body
    for word in ("Dentist", "Sam", "oat milk", "chemotherapy", "Project", "secret"):
        assert word not in b.body, word
    assert env["prefs"] == dict()                         # preparing writes nothing
    await bft.settle(b, produced=True)
    st = env["prefs"][OWNER][drr.STATE_KEY]
    assert st == dict(day="2026-10-20", at=_iso(at(20, 8)), unanswered=1)


async def test_a_command_turn_never_carries_it(env):
    seed(env)
    env["ctx"] = dict(calendar=[dict(title="School run", start="08:30", end="09:00", location="")])
    b = await brief(env, "turn on the kitchen lights")
    assert b is not None and "School run" in b.body           # the brief's own one soon-line still rides ...
    assert "pendant day review" not in b.body and b.day_review is None
    await bft.settle(b, produced=True)
    assert env["prefs"] == dict()                              # ... and no raise is recorded for it


async def test_a_turn_with_no_reply_burns_nothing(env):
    seed(env)
    b = await brief(env)
    await bft.settle(b, produced=False)
    assert env["prefs"] == dict()
    assert (await brief(env)).day_review is not None


async def test_never_more_than_once_a_day_even_across_a_restart(env):
    seed(env)
    await bft.settle(await brief(env), produced=True)
    bft._reset_state()
    drr.reset()                                         # a zoe-data restart: only the stored state remembers
    env["now"] = at(20, 10)
    b = await brief(env, sid="chat-session-2")
    assert b is None or b.day_review is None
    env["now"] = at(21, 8)                              # the next household day may raise again
    bft._reset_state()
    assert (await brief(env)).day_review is not None


async def test_two_ignored_raises_snooze_it_until_something_newer_and_three_days(env):
    seed(env)
    for day in (20, 21):
        env["now"] = at(day, 8)
        bft._reset_state()
        b = await brief(env)
        assert b.day_review is not None, day
        await bft.settle(b, produced=True)
    assert env["prefs"][OWNER][drr.STATE_KEY]["unanswered"] == 2
    env["now"] = at(22, 8)
    bft._reset_state()
    assert (await brief(env)) is None                    # snoozed: the same old items are not raised a third time
    add_cand(env, "reminder", "Water the ferns", created=_iso(at(22, 4)))   # a newer review ...
    bft._reset_state()
    assert (await brief(env)) is None                    # ... but not three days yet
    env["now"] = at(24, 8)
    bft._reset_state()
    b = await brief(env)
    assert b is not None and b.day_review is not None    # newer AND three days: raised again, a fresh count
    await bft.settle(b, produced=True)
    assert env["prefs"][OWNER][drr.STATE_KEY]["unanswered"] == 1


async def test_taking_it_up_resets_the_count(env):
    seed(env)
    await bft.settle(await brief(env), produced=True)
    env["now"] = at(20, 9)
    assert await drr.tier("what did you find in my day?", OWNER, channel="chat") is not None   # a pull takes it up
    env["now"] = at(21, 8)
    bft._reset_state()
    b = await brief(env)
    assert b.day_review is not None and b.day_review.streak == 0
    await bft.settle(b, produced=True)
    env["now"] = at(21, 9)                              # ... and so does deciding any item (desktop / phone / card)
    env["db"].conn.execute("UPDATE omi_day_candidates SET status = 'dismissed', decided_at = ? WHERE id = 'c0'",
                           (_iso(env["now"]),))
    env["db"].conn.commit()
    env["now"] = at(22, 8)
    bft._reset_state()
    b = await brief(env)
    assert b.day_review is not None and b.day_review.streak == 0


async def test_after_he_asked_today_the_brief_does_not_repeat_it(env):
    seed(env)
    env["now"] = at(20, 6)
    assert await drr.tier("anything from today?", OWNER, channel="chat") is not None      # he pulled it at 06:00
    env["now"] = at(20, 8)
    b = await brief(env)
    assert b is None or b.day_review is None                                             # the 08:00 brief does not say it again
    env["now"] = at(21, 8)
    bft._reset_state()
    assert (await brief(env)).day_review is not None                                     # the next day it may


async def test_a_decline_right_after_the_raise_snoozes_it_and_steals_nothing(env):
    seed(env)
    await bft.settle(await brief(env), produced=True)
    assert await drr.tier("not now", OWNER, channel="chat") is None       # recorded; the pull tier / the brain answer it
    assert env["prefs"][OWNER][drr.STATE_KEY]["unanswered"] == drr.MAX_UNANSWERED
    env["now"] = at(21, 8)
    bft._reset_state()
    assert await brief(env) is None


async def test_a_decline_survives_a_restart_between_the_raise_and_the_not_now(env):
    seed(env)
    await bft.settle(await brief(env), produced=True)
    drr.reset()                                                           # zoe-data restarted after the raise
    env["now"] = at(20, 8, 5)
    assert await drr.tier("not now", OWNER, channel="chat") is None
    assert env["prefs"][OWNER][drr.STATE_KEY]["unanswered"] == drr.MAX_UNANSWERED


async def test_a_late_not_now_is_not_about_the_raise(env):
    seed(env)
    await bft.settle(await brief(env), produced=True)
    env["now"] = at(20, 8, 30)                                            # 30 minutes later: about something else
    await drr.tier("not now", OWNER, channel="chat")
    assert env["prefs"][OWNER][drr.STATE_KEY]["unanswered"] == 1


@pytest.mark.parametrize("flag,value", [("ZOE_DAY_REVIEW_RAISE", "0"), ("ZOE_OMI_DAY_REVIEW", "shadow")])
async def test_switched_off_between_prepare_and_settle_writes_nothing(env, monkeypatch, flag, value):
    seed(env)
    b = await brief(env)
    assert b.day_review is not None
    monkeypatch.setenv(flag, value)
    await bft.settle(b, produced=True)
    assert env["prefs"] == dict()


async def test_a_not_now_with_no_recent_raise_records_nothing(env):
    seed(env)
    assert await drr.tier("not now", OWNER, channel="chat") is None
    assert env["prefs"] == dict()


# --- who hears it ----------------------------------------------------------------------------------------------------------------
async def test_a_verified_voice_alone_hears_it(env):
    seed(env)
    warm(env)
    heard(env)
    b = await brief(env, sid=VSID)
    assert b is not None and b.day_review is not None


@pytest.mark.parametrize("scope,verdict,claimed", [
    (None, True, OWNER),      # the owner's voice matched but the session refused it (model gate): ONLY the scope rule stops it
    (None, None, None), (OTHER, None, None), (None, False, None), (OTHER, True, OTHER),
])
async def test_a_guest_or_another_member_never_hears_it(env, scope, verdict, claimed):
    seed(env)
    warm(env)
    heard(env, scope=scope, verdict=verdict, claimed=claimed)
    b = await brief(env, sid=VSID)                    # the brain turn is attributed to the owner (a stale binding) ...
    assert b is None or b.day_review is None          # ... but the turn did not identify them: nothing, not even the count
    assert "pendant day review" not in (b.body if b else "")


async def test_another_member_gets_only_their_own(env):
    seed(env)
    reply = (await drr.tier("anything from today", OTHER, channel="chat")).reply
    assert reply.startswith("I went through your day and found one thing for your lists.")
    assert "event" not in reply and "reminder" not in reply


async def test_an_unnoted_voice_turn_is_refused_never_assumed(env):
    seed(env)
    warm(env)
    b = await brief(env, sid="voice-panel-panel-hall-9")
    assert b is None or b.day_review is None


@pytest.mark.parametrize("uid", ["guest", "voice-guest", "panel-kitchen", "demo_s5_ab12cd", ""])
async def test_no_raise_for_a_non_member(env, uid):
    seed(env)
    assert await drr.prepare(uid, "chat-1") is None
    assert await drr.orb_review(uid) in (False, None)


async def test_a_guest_pull_gets_the_private_line_and_the_locked_card(env):
    seed(env)
    warm(env)
    res = await drr.tier("what did you find in my day", "guest", channel="voice", extra_ctx=vctx(None))
    assert res.reply == drr.PRIVATE_REPLY
    assert [c["kind"] for c in env["cards"]] == ["locked"]
    assert not re.search(r"[0-9]", res.reply) and " two " not in res.reply and " four " not in res.reply


# --- crowded room ----------------------------------------------------------------------------------------------------------------
async def test_a_second_voice_stops_the_raise(env):
    seed(env)
    warm(env)
    drr.note_voice_turn("voice-panel-x", PANEL, scope_user=None, verdict=False, claimed=None)    # someone unverified spoke
    heard(env)
    b = await brief(env, sid=VSID)
    assert b is None or b.day_review is None
    env["clk"][0] += drr.CROWD_WINDOW_S + 1                                                     # ten quiet minutes later
    heard(env)
    assert (await brief(env, sid=VSID)).day_review is not None


async def test_a_fresh_pin_turn_with_no_voice_verdict_is_an_unknown_voice(env):
    # scoped to the owner by a fresh PIN, but the speaker gate said nothing: whoever spoke is unknown, so the room is crowded
    seed(env)
    warm(env)
    heard(env, scope=OWNER, verdict=None, claimed=None)
    b = await brief(env, sid=VSID)
    assert b is None or b.day_review is None
    res = await drr.tier("what did you find in my day?", OWNER, channel="voice", extra_ctx=vctx())
    assert res.reply == drr.CROWDED_SENT


async def test_unknown_presence_after_a_restart_is_crowded(env):
    seed(env)
    heard(env)                                         # the first voice this process heard: presence is not known yet
    b = await brief(env, sid=VSID)
    assert b is None or b.day_review is None


async def test_a_crowded_pull_says_nothing_private_aloud_and_sends_it_to_the_phone(env):
    seed(env)
    warm(env)
    drr.note_voice_turn("voice-panel-x", PANEL, scope_user=OTHER, verdict=True, claimed=OTHER)  # another member is in the room
    heard(env)
    res = await drr.tier("what did you find in my day?", OWNER, channel="voice", extra_ctx=vctx())
    assert res.reply == drr.CROWDED_SENT
    for word in ("event", "reminder", "list", "two", "four"):
        assert word not in res.reply, word
    assert [c["kind"] for c in env["cards"]] == ["locked"]                          # the list leaves the wall
    assert len(env["sent"]) == 1
    tid, text, url = env["sent"][0]
    assert tid == "424242" and text == "I went through your day: four things are waiting for you to look at."
    for word in ("Dentist", "Sam", "oat", "chemotherapy", "event", "reminder"):
        assert word not in text
    # the same ask again within ten minutes does not spam the phone
    res2 = await drr.tier("what did you find in my day?", OWNER, channel="voice", extra_ctx=vctx())
    assert res2.reply == drr.CROWDED_NO_PHONE and len(env["sent"]) == 1


async def test_a_crowded_pull_with_no_linked_phone_says_ask_again_alone(env):
    seed(env)
    warm(env)
    drr.note_voice_turn("voice-panel-x", PANEL, scope_user=None, verdict=False, claimed=None)
    res = await drr.tier("anything from today", OTHER, channel="voice", extra_ctx=vctx(OTHER))
    assert res.reply == drr.CROWDED_NO_PHONE and env["sent"] == []


async def test_alone_the_pull_speaks_counts_and_pushes_the_private_card(env, monkeypatch):
    seed(env)
    warm(env)
    heard(env)
    import panel_review_cards as prc

    pushed = []

    async def parts(member):
        return ([dict(id="c0")], [], [])

    def card(panel_id, member, cands, voices, people):
        pushed.append((panel_id, member))
        return dict(panel_id=panel_id, kind="day_review")

    monkeypatch.setenv("ZOE_PANEL_REVIEW_CARDS", "1")
    monkeypatch.setattr(prc, "day_parts", parts)
    monkeypatch.setattr(prc, "day_card", card)
    res = await drr.tier("what did you find in my day?", OWNER, channel="voice", extra_ctx=vctx())
    assert res.reply == ("I went through your day and found two events, one thing for your lists and one reminder. "
                         "They're on the screen.")
    assert pushed == [(PANEL, OWNER)] and env["sent"] == []
    assert SENSITIVE not in res.reply


async def test_send_it_to_my_phone(env):
    seed(env)
    res = await drr.tier("send my day review to my phone", OWNER, channel="chat")
    assert res.reply == drr.PHONE_SENT and len(env["sent"]) == 1


# --- what's up + the orb ---------------------------------------------------------------------------------------------------------
async def test_whats_up_adds_one_sentence_only_for_the_identified_member_alone(env):
    seed(env)
    warm(env)
    heard(env)
    assert await drr.pull_addendum(OWNER, "voice", vctx()) == (
        "Also, I went through your day and found two events, one thing for your lists and one reminder.")
    assert await drr.pull_addendum(OWNER, "voice", vctx(None)) == ""
    drr.note_voice_turn("voice-panel-x", PANEL, scope_user=None, verdict=False, claimed=None)
    assert await drr.pull_addendum(OWNER, "voice", vctx()) == ""


async def test_the_fast_tier_pull_appends_it(env, monkeypatch):
    import fast_tiers
    import proactive.pull as pl

    seed(env)
    monkeypatch.setenv("ZOE_PULL_NOT_PUSH", "1")

    async def fake_pull(uid, sid, **kw):
        return pl.PullResult(pl.EMPTY_REPLY)

    monkeypatch.setattr(pl, "pull", fake_pull)
    res = await fast_tiers._pull_tier("what's up", OWNER, "chat-1", "chat", None)
    assert res.reply == "Also, I went through your day and found two events, one thing for your lists and one reminder."
    monkeypatch.setenv("ZOE_DAY_REVIEW_RAISE", "0")
    res = await fast_tiers._pull_tier("what's up", OWNER, "chat-1", "chat", None)
    assert res.reply == pl.EMPTY_REPLY


async def test_the_orb_says_yes_or_no_never_how_many(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import auth
    from routers import proactive as proactive_router

    seed(env)
    app = FastAPI()
    app.include_router(proactive_router.router)
    who = dict(user_id=OWNER)
    app.dependency_overrides[auth.resolve_acting_user] = lambda: dict(who)
    client = TestClient(app)
    body = client.get("/api/proactive/inbox").json()
    assert body["review"] is True and body["count"] == 0      # the pull queue's count is not the review's
    who["user_id"] = "guest"
    assert client.get("/api/proactive/inbox").json()["review"] is False
    monkeypatch.setenv("ZOE_DAY_REVIEW_RAISE", "0")
    who["user_id"] = OWNER
    assert "review" not in client.get("/api/proactive/inbox").json()


# --- read-only -----------------------------------------------------------------------------------------------------------------
async def test_raising_and_pulling_write_nothing(env):
    seed(env)
    warm(env)
    heard(env)
    before = snapshot(env)
    await bft.settle(await brief(env, sid=VSID), produced=True)
    await drr.tier("what did you find in my day?", OWNER, channel="voice", extra_ctx=vctx())
    await drr.tier("anything from today", OWNER, channel="chat")
    await drr.tier("send my day to my phone", OWNER, channel="chat")
    await drr.pull_addendum(OWNER, "chat", None)
    await drr.orb_review(OWNER)
    assert snapshot(env) == before
    assert set(env["prefs"][OWNER]) <= set([drr.STATE_KEY, drr.ENGAGED_KEY])


async def test_a_dry_replay_records_nothing_and_sends_nothing(env):
    seed(env)
    warm(env)
    drr.note_voice_turn("voice-panel-x", PANEL, scope_user=None, verdict=False, claimed=None)
    res = await drr.tier("what did you find in my day?", OWNER, channel="voice", extra_ctx=vctx(), allow_writes=False)
    assert res.reply == drr.CROWDED_NO_PHONE
    assert env["sent"] == [] and env["cards"] == [] and env["prefs"] == dict()


def test_the_module_imports_no_write_path():
    src = (SVC / "day_review_raise.py").read_text()
    for needle in ("omi_review_apply", "list_service", "reminder_service", "calendar_service", "memory_service",
                   "MemoryService", ".ingest(", "INSERT", "UPDATE", "DELETE"):
        assert needle not in src.replace("omi_review_apply``)", ""), needle


def test_the_voice_lane_notes_every_turn_before_the_tiers():
    src = (SVC / "routers" / "voice_tts.py").read_text()
    note = src.index("_day_review_raise.note_voice_turn(")
    assert src.index("_scope_identity_user = _vs_decision.scope_user") < note < src.index('phase="conversation"')
