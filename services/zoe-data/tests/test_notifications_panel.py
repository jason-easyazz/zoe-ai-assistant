"""Household notifications on the wall panel: the store, the orb dot, the card and its taps (``notifications_store.py``, flag
``ZOE_NOTIFICATIONS``; the spoken tail is ``test_notification_tail.py``).
Spec + owner acceptance: docs/knowledge/notifications.md. Each privacy / do-not-overwhelm guard has a NEGATIVE CONTROL beside it
(the same call with the guard's condition removed does the thing). Synthetic ids and text only.
"""
from __future__ import annotations

import json
import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import sqlite3
import types
from datetime import datetime, timezone
from pathlib import Path

import db_compat
import notifications_store as ns
import panel_review_cards as prc
import user_erase_gate as erase_gate
from db_pool import _Cursor, _ExecResult

SVC = Path(__file__).resolve().parents[1]
MEMBER, OTHER = "member-a", "member-b"
PANEL = "panel-1"
LINE = "the blue kettle is on the windowsill"          # synthetic "what the pendant heard"


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)      # TestClient runs the app in its own thread
        self.conn.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat())

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
        return self.conn.execute(sql, params).fetchall()


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = _Sqlite(str(tmp_path / "zoe.db"))
    db.conn.execute("CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, type TEXT NOT NULL, title TEXT, message TEXT, "
                    "data TEXT, delivered INTEGER DEFAULT 0, action_taken TEXT, created_at TEXT)")
    db.conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    db.conn.executemany("INSERT INTO users VALUES (?, ?, 'member')", [(MEMBER, "A"), (OTHER, "B")])

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.delenv("ZOE_NOTIFICATIONS", raising=False)            # default ON
    monkeypatch.delenv("ZOE_OMI_SYNC_ENABLED", raising=False)
    pushed: list = []

    async def send(panel_id, event, frame):
        pushed.append((panel_id, event, frame))
        return 1
    import voice_session
    monkeypatch.setattr(voice_session, "send_to_panel", send)
    ns.reset()
    prc.reset()
    db.pushed = pushed
    yield db
    ns.reset()
    prc.reset()


def run(coro):
    return asyncio.run(coro)


def seed(n=3, member=MEMBER, sensitive=()):
    for i in range(n):
        assert run(ns.create(member, "note", f"Title {i}", f"Message {i}", dedupe_id=str(i), sensitive=i in sensitive))


# -- the store -------------------------------------------------------------------------------------------------------------------
def test_create_is_deduped_and_counts_only_logged(env, caplog):
    caplog.set_level("INFO")
    assert run(ns.create(MEMBER, "note", "Secret title", "Secret words", dedupe_id="x"))
    assert run(ns.create(MEMBER, "note", "Secret title", "Secret words", dedupe_id="x")) is None      # same key: one row
    assert run(ns.unread_count(MEMBER)) == 1
    assert "Secret" not in caplog.text


def test_flag_off_creates_nothing_and_control(env, monkeypatch):
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "off")
    assert run(ns.create(MEMBER, "note", "T", "M", dedupe_id="a")) is None
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "on")                   # control
    assert run(ns.create(MEMBER, "note", "T", "M", dedupe_id="a"))


def test_an_erased_member_gets_nothing_and_control(env):
    gen = erase_gate.generation(MEMBER)

    async def erase():
        async with erase_gate.erasing(MEMBER):
            pass
    run(erase())
    assert run(ns.create(MEMBER, "note", "T", "M", dedupe_id="a", scheduled_at=gen)) is None        # scheduled before the erase
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0
    assert run(ns.create(OTHER, "note", "T", "M", dedupe_id="a"))                                    # control: a live member


def test_a_create_during_an_erase_is_refused(env):
    async def go():
        async with erase_gate.erasing(MEMBER):
            task = asyncio.ensure_future(ns.create(MEMBER, "note", "T", "M", dedupe_id="mid"))
            await asyncio.sleep(0.01)                  # the create is waiting on the erase
        return await task
    assert run(go()) is None
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0


def test_a_member_with_no_account_gets_nothing_and_control(env):
    env.conn.execute("DELETE FROM users WHERE id = ?", (MEMBER,))                       # the account is gone
    assert run(ns.create(MEMBER, "note", "T", "M", dedupe_id="a")) is None
    env.conn.execute("INSERT INTO users VALUES ('member-g', 'G', 'guest')")
    assert run(ns.create("member-g", "note", "T", "M", dedupe_id="a")) is None          # a guest-role account
    assert run(ns.create(OTHER, "note", "T", "M", dedupe_id="a"))                        # control: a live member


def test_a_forgotten_name_is_never_written_and_control(env, monkeypatch):
    import memory_tombstones
    monkeypatch.setattr(memory_tombstones, "matching_tombstone", lambda uid, text: "x" if "Quill" in text else None)
    assert run(ns.create(MEMBER, "note", "Call Quill", "M", dedupe_id="a")) is None
    assert run(ns.create(MEMBER, "note", "Call the plumber", "M", dedupe_id="b"))                     # control


def test_a_guest_owns_no_notifications(env):
    for who in ("guest", "", "voice-guest", "panel-1"):
        assert run(ns.create(who, "note", "T", "M", dedupe_id="a")) is None


# -- the dot -------------------------------------------------------------------------------------------------------------------
def test_the_dot_counts_only_the_callers_own_rows(env):
    from routers import proactive as r
    seed(2)
    me = run(r.pending_inbox({"user_id": MEMBER, "role": "user"}))
    assert me["notify"] == 2 and "Title" not in str(me)                               # a count, never words
    assert "notify_any" not in me                                                      # a member gets their own count only
    assert run(r.pending_inbox({"user_id": OTHER, "role": "user"}))["notify"] == 0    # another member
    for caller in ({"user_id": "guest", "role": "guest"}, {"user_id": MEMBER, "role": "user", "device_token_role": "panel"},
                   {"user_id": MEMBER, "role": "user", "auth_degraded": True}):
        out = run(r.pending_inbox(caller))
        assert "notify" not in out and out["notify_any"] is True                       # not the member: a bare boolean, no count


def test_the_kiosk_boolean_says_only_someone_has_something(env):
    from routers import proactive as r
    guest = {"user_id": "guest", "role": "guest"}
    assert run(r.pending_inbox(guest))["notify_any"] is False                         # nothing waiting
    seed(1)
    out = run(r.pending_inbox(guest))
    assert out["notify_any"] is True
    assert MEMBER not in str(out) and "Title" not in str(out) and "notify" not in out  # never whose, never words, never a count


def test_a_guests_own_rows_never_light_the_kiosk_dot_and_control(env):
    from routers import proactive as r
    guest = {"user_id": "guest", "role": "guest"}
    env.conn.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered) VALUES ('g', 'guest', 'note', 'T', 'M', '{}', 0)")
    assert run(r.pending_inbox(guest))["notify_any"] is False
    env.conn.execute("UPDATE notifications SET user_id = ? WHERE id = 'g'", (MEMBER,))   # control: the same row, a member's
    assert run(r.pending_inbox(guest))["notify_any"] is True


def test_only_sensitive_unread_never_lights_the_kiosk_dot_and_control(env):
    from routers import proactive as r
    guest = {"user_id": "guest", "role": "guest"}
    assert run(ns.create(MEMBER, "note", "Private", "M", dedupe_id="s", sensitive=True))
    assert run(r.pending_inbox(guest))["notify_any"] is False
    assert run(r.pending_inbox({"user_id": MEMBER, "role": "user"}))["notify"] == 1   # the member still sees their own
    assert run(ns.create(MEMBER, "note", "Plain", "M", dedupe_id="p"))                 # control: a non-sensitive one
    assert run(r.pending_inbox(guest))["notify_any"] is True


def test_flag_off_the_inbox_is_unchanged(env, monkeypatch):
    from routers import proactive as r
    seed(1)
    on = run(r.pending_inbox({"user_id": MEMBER, "role": "user"}))
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "off")
    off = run(r.pending_inbox({"user_id": MEMBER, "role": "user"}))
    assert "notify" in on and "notify" not in off
    assert off == {k: v for k, v in on.items() if k != "notify"}
    guest = {"user_id": "guest", "role": "guest"}
    assert "notify_any" not in run(r.pending_inbox(guest))                            # the kiosk boolean is absent too
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "on")
    assert "notify_any" in run(r.pending_inbox(guest))


def test_home_paints_the_existing_dot_for_notify():
    html = (SVC.parents[1] / "services/zoe-ui/dist/touch/home.html").read_text()
    assert "var mine=!!(d&&!d.quiet&&((d.enabled&&d.count>0)||d.notify>0));" in html
    assert "paint(mine||!!(d&&!d.quiet&&d.notify_any===true)," in html
    assert "notifications-panel.js" not in html                                       # desktop's bell never loads on the estate


def test_a_guest_dot_press_opens_the_mic_never_the_voiceless_pull_and_control():
    """``notify_any`` alone proves no one: /pull carries no voice, so the server refuses the guest and the dot could never be read.
    The press must open the mic (a verified voice opens the member's session); only a member's own dot runs orbPull."""
    html = (SVC.parents[1] / "services/zoe-ui/dist/touch/home.html").read_text()
    paint = html[html.index("function paint(on,review"):]
    paint = paint[:paint.index("}")]
    assert "_dotVoiceOnly=!!(on&&voiceOnly)" in paint                                  # set on every paint, cleared with the dot
    assert "var pullable=!!(d&&!d.quiet&&d.enabled&&d.count>0);" in html and ",!pullable);" in html
    # voice-only = nothing the proactive selector reports: the guest's notify_any AND a member's notifications-only dot, because
    # the voiceless /pull is refused for both
    # ONE press path (orbPress): the orb and Settings > Notifications "Show the card" both run it
    assert "orb.addEventListener('click',function(){orbPress();});" in html
    click = html[html.index("function orbPress(idlePrompt){"):]
    click = click[:click.index("\n  }")]
    guest, member = click.index("if(ready&&_dotVoiceOnly)panelVoice("), click.index("else if(ready)orbPull();")
    assert guest < member                                                              # the guest branch wins before any pull
    assert click.count("orbPull()") == 1                                               # control: a member's own dot still pulls


# -- the card + taps -----------------------------------------------------------------------------------------------------------
def _fake_repass(monkeypatch, *, confirmed=0, props=("p1",), confirm_status="confirmed"):
    state = {"props": list(props), "confirmed": [], "rejected": []}

    async def wearer_status(db, uid):
        props = state["props"] if uid == MEMBER else []          # proposals are per member, like the real table
        return {"confirmed": confirmed, "needed": 10, "proposals": [{"id": p, "windows": 6, "recordings": 2} for p in props]}

    async def proposal_lines(db, uid, pid, limit=6):
        return [{"recording_id": "r", "at_ms": 0, "text": LINE}]

    async def confirm_wearer(db, uid, pid):                       # the real shape: a dict, truthy whatever happened
        if confirm_status != "confirmed":
            return {"status": confirm_status, "confirmed": 0}
        state["props"].remove(pid)
        state["confirmed"].append((uid, pid))
        return {"status": "confirmed", "confirmed": 6}

    async def reject_wearer(db, uid, pid):
        state["props"].remove(pid)
        state["rejected"].append((uid, pid))
        return 1
    mod = types.SimpleNamespace(wearer_status=wearer_status, proposal_lines=proposal_lines, confirm_wearer=confirm_wearer,
                                reject_wearer=reject_wearer)
    import sys
    monkeypatch.setitem(sys.modules, "omi_repass", mod)
    monkeypatch.setenv("ZOE_OMI_SYNC_ENABLED", "1")
    return state


def test_the_wearer_creator_makes_one_notification_per_batch(env, monkeypatch):
    state = _fake_repass(monkeypatch)
    nid = run(ns.notify_wearer_proposals(MEMBER))
    assert nid and run(ns.notify_wearer_proposals(MEMBER)) is None                     # same batch: once
    (row,) = env.rows("SELECT title, message, data FROM notifications")
    assert row[0] == ns.WEARER_TITLE and LINE not in "".join(map(str, row))            # the row never holds what was said
    state["props"].append("p2")
    assert run(ns.notify_wearer_proposals(MEMBER)) is None                             # the batch grew: the SAME ask, updated in place
    state["props"].remove("p1")
    assert run(ns.notify_wearer_proposals(MEMBER)) is None                             # the batch shrank: still no second row
    (row,) = env.rows("SELECT data FROM notifications")
    assert json.loads(row[0])["proposals"] == ["p2"] and run(ns.unread_count(MEMBER)) == 1


def test_an_old_wearer_ask_buried_under_newer_notifications_is_still_the_one_ask(env, monkeypatch):
    state = _fake_repass(monkeypatch)
    assert run(ns.notify_wearer_proposals(MEMBER))
    seed(60)                                                                           # 60 newer unread rows push it out of any newest-50 read
    state["props"].append("p2")
    assert run(ns.notify_wearer_proposals(MEMBER)) is None
    rows = env.rows("SELECT data FROM notifications WHERE type = ?", (ns.WEARER_TYPE,))
    assert len(rows) == 1 and json.loads(rows[0][0])["proposals"] == ["p1", "p2"]


def test_no_wearer_notification_once_the_print_is_confirmed(env, monkeypatch):
    _fake_repass(monkeypatch, confirmed=10)
    assert run(ns.notify_wearer_proposals(MEMBER)) is None


def test_proposal_lines_reach_only_the_members_own_panel_card(env, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    _fake_repass(monkeypatch)
    run(ns.notify_wearer_proposals(MEMBER))
    extra = {"scope_user": MEMBER, "panel_id": PANEL}
    assert run(ns.pull_addendum(MEMBER, "voice", extra, alone=True)) == "You have a notification on the screen."
    ((panel, event, frame),) = env.pushed
    assert panel == PANEL and event == "voice_card" and frame["member_id"] == MEMBER and frame["private"] is True
    assert LINE in frame["items"][0]["meta"]
    assert [a["key"] for a in frame["items"][0]["actions"]] == ["me", "not_me"]
    assert LINE not in caplog.text
    # an unverified voice / guest turn: no card, no count (control is the call above)
    env.pushed.clear()
    for who, ctx in (("guest", {"panel_id": PANEL}), (MEMBER, {"scope_user": None, "panel_id": PANEL}),
                     (MEMBER, {"scope_user": "guest", "panel_id": PANEL}), (MEMBER, {"scope_user": OTHER, "panel_id": PANEL})):
        assert run(ns.pull_addendum(who, "voice", ctx, alone=True)) == ""
    assert env.pushed == []


def test_a_tap_confirms_the_voice_and_clears_the_notification(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import notifications as rn
    state = _fake_repass(monkeypatch)
    run(ns.notify_wearer_proposals(MEMBER))
    frame = run(ns.card(PANEL, MEMBER))
    app = FastAPI()
    assert rn.register(app) is True
    caller = {"user_id": MEMBER, "role": "user"}
    app.dependency_overrides[rn._caller] = lambda: caller
    c = TestClient(app)
    h = {"X-Panel-Card": frame["card_token"]}
    caller.update(user_id=OTHER)
    assert c.post("/api/panel/notifications/act", json={"item": "w1", "action": "me"}, headers=h).status_code == 403   # not theirs
    caller.update(user_id=MEMBER)
    assert c.post("/api/panel/notifications/act", json={"item": "zz", "action": "me"}, headers=h).status_code == 404
    assert c.post("/api/panel/notifications/act", json={"item": "w1", "action": "me"}, headers=h).json()["ok"] is True
    assert state["confirmed"] == [(MEMBER, "p1")]
    assert run(ns.unread_count(MEMBER)) == 0                                            # nothing left to ask: read


@pytest.mark.parametrize("status,code", [("not_found", 409), ("not_eligible", 403)])
def test_a_confirm_that_did_not_happen_never_says_it_did(env, monkeypatch, status, code):
    state = _fake_repass(monkeypatch, confirm_status=status)
    run(ns.notify_wearer_proposals(MEMBER))
    run(ns.card(PANEL, MEMBER))
    item = {"type": "wearer", "ref": "p1", "nid": "x", "batch": ["p1"]}
    with pytest.raises(ns.ActError) as ei:
        run(ns.act(MEMBER, item, "me"))
    assert ei.value.status == code and state["confirmed"] == [] and run(ns.unread_count(MEMBER)) == 1


def test_flag_off_the_card_routes_are_absent(env, monkeypatch):
    from fastapi import FastAPI
    from routers import notifications as rn
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "off")
    assert rn.register(FastAPI()) is False


def test_flag_off_the_orb_press_says_and_pushes_nothing(env, monkeypatch):
    seed(1)
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "off")
    assert run(ns.pull_addendum(MEMBER, "voice", {"scope_user": MEMBER, "panel_id": PANEL}, alone=True)) == ""
    assert env.pushed == []


# -- forget ----------------------------------------------------------------------------------------------------------------------
def test_a_member_erase_deletes_their_rows_and_tail_caps_and_control(env):
    seed(2)
    seed(1, member=OTHER)
    env.conn.execute("INSERT INTO app_settings (key, value) VALUES (?, '{}'), (?, '{}')", (ns.tail_key(MEMBER), ns.tail_key(OTHER)))
    assert run(ns.forget_user(OTHER)) == 1                                             # control: another member's erase leaves ours
    assert run(ns.unread_count(MEMBER)) == 2
    assert run(ns.forget_user(MEMBER)) == 2
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0
    assert env.rows("SELECT COUNT(*) FROM app_settings")[0][0] == 0


def test_an_erase_during_the_wearer_read_leaves_no_row_and_control(env, monkeypatch):
    """The wearer creator's erase generation is taken BEFORE its awaited read: a forget_user that completes during the read stales it."""
    _fake_repass(monkeypatch)
    real = ns._wearer_status

    async def control_read(uid):
        return await real(uid)
    monkeypatch.setattr(ns, "_wearer_status", control_read)
    assert run(ns.notify_wearer_proposals(OTHER)) is None                              # OTHER has no proposals: nothing
    assert run(ns.notify_wearer_proposals(MEMBER))                                     # control: no erase, a row lands
    run(ns.forget_user(MEMBER))
    ns.reset()

    async def erasing_read(uid):
        st = await real(uid)
        await ns.forget_user(uid)                                                      # the erase completes mid-read
        return st
    monkeypatch.setattr(ns, "_wearer_status", erasing_read)
    assert run(ns.notify_wearer_proposals(MEMBER)) is None
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0


def test_a_create_scheduled_before_the_erase_stays_erased(env):
    gen = erase_gate.generation(MEMBER)
    run(ns.forget_user(MEMBER))
    assert run(ns.create(MEMBER, "note", "T", "M", dedupe_id="late", scheduled_at=gen)) is None
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0



def test_a_failed_tail_state_erase_fails_the_erase_and_control(env, monkeypatch):
    seed(1)
    real = env.execute

    def broken(sql, params=()):
        if sql.startswith("DELETE FROM app_settings"):
            raise RuntimeError("disk I/O error")
        return real(sql, params)
    monkeypatch.setattr(env, "execute", broken)
    with pytest.raises(RuntimeError):
        run(ns.forget_user(MEMBER))
    monkeypatch.setattr(env, "execute", real)                                          # control: the same erase, a healthy store
    assert run(ns.forget_user(MEMBER)) == 0


def test_the_card_holds_eight_rows_after_wearer_expansion(env, monkeypatch):
    _fake_repass(monkeypatch, props=("p1", "p2", "p3"))
    run(ns.notify_wearer_proposals(MEMBER))
    seed(7)
    frame = run(ns.card(PANEL, MEMBER))
    assert len(frame["items"]) == ns.MAX_CARD and frame["more"] == 2 and frame["more_label"] == "And 2 more."
    assert set(prc._CARDS[frame["card_token"]].items) == {it["id"] for it in frame["items"]}   # a hidden row cannot be acted on


def test_a_minted_card_is_dead_once_the_flag_is_off_and_control(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import notifications as rn
    seed(1)
    frame = run(ns.card(PANEL, MEMBER))
    app = FastAPI()
    assert rn.register(app) is True                                                    # mounted while on
    app.dependency_overrides[rn._caller] = lambda: {"user_id": MEMBER, "role": "user"}
    c = TestClient(app)
    h = {"X-Panel-Card": frame["card_token"]}
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "off")                                     # turned off after the card was minted
    assert c.post("/api/panel/notifications/act", json={"item": "n1", "action": "done"}, headers=h).status_code == 404
    assert env.rows("SELECT delivered FROM notifications")[0][0] == 0                  # nothing was marked read
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "on")                                      # control: the same tap, flag on
    assert c.post("/api/panel/notifications/act", json={"item": "n1", "action": "done"}, headers=h).json()["ok"] is True


def test_a_dry_pull_says_nothing_about_notifications_and_control(env):
    seed(2)
    ctx = {"scope_user": MEMBER, "panel_id": PANEL}
    assert run(ns.pull_addendum(MEMBER, "voice", ctx, alone=True, dry=True)) == ""
    assert env.pushed == [] and ns.ANNOUNCED == {}
    assert run(ns.pull_addendum(MEMBER, "voice", ctx, alone=True)) == "You have two notifications on the screen."   # control


# -- the orb press with the proactive selector off ---------------------------------------------------------------------------------
def test_whats_up_collects_notifications_with_the_selector_off_and_controls(env, monkeypatch):
    import fast_tiers
    import proactive.pull as pl
    monkeypatch.delenv("ZOE_PROACTIVE_SELECTOR", raising=False)                       # its default: pull() answers None
    monkeypatch.delenv("ZOE_PULL_NOT_PUSH", raising=False)
    seed(2)
    ctx = {"scope_user": MEMBER, "panel_id": PANEL, "speaker_verified": True}
    for who, c in ((MEMBER, {**ctx, "speaker_verified": None}), (MEMBER, {**ctx, "speaker_verified": False}),
                   ("guest", {"panel_id": PANEL, "speaker_verified": True})):         # unverified voice / guest: nothing
        assert run(fast_tiers._pull_tier("what's up", who, "s1", "voice", c)) is None
    assert run(fast_tiers._pull_tier("what's up", MEMBER, "s1", "voice", ctx, dry=True)) is None   # a dry replay: nothing
    assert env.pushed == [] and ns.ANNOUNCED == {}
    res = run(fast_tiers._pull_tier("what's up", MEMBER, "s1", "voice", ctx))
    assert res is not None and res.reply == "You have two notifications on the screen." and res.tier == "pull"
    assert env.pushed and env.pushed[-1][0] == PANEL
    # control: with the selector ON its None stays final (a brief about to speak, a guest, a lost race)

    async def declined(*a, **k):
        return None
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setattr(pl, "pull", declined)
    assert run(fast_tiers._pull_tier("what's up", MEMBER, "s1", "voice", ctx)) is None


# -- the second channel for "owner alerting is broken" ---------------------------------------------------------------------------
def test_broken_alerting_reaches_the_owner_on_the_panel_once_and_control(env, monkeypatch):
    """An alert about the Telegram credential failing cannot travel over Telegram: while the shared helper records alerting as
    broken, an OWNER gets one panel notification per broken spell. Negative controls: a non-owner member gets none; nothing is
    created once alerting is healthy."""
    import kv_miss_alert

    env.conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (MEMBER,))
    broken = {"since": 1_790_000_000.0, "last": 1_790_000_100.0, "reason": "auth", "count": 3}
    monkeypatch.setattr(kv_miss_alert, "alerting_broken", lambda: broken)
    assert run(ns.notify_alerting_broken(MEMBER))
    assert run(ns.notify_alerting_broken(MEMBER)) is None                     # same spell: one row
    assert run(ns.notify_alerting_broken(OTHER)) is None                      # not an owner
    rows = env.rows("SELECT user_id, type, title, message FROM notifications")
    assert len(rows) == 1 and rows[0][0] == MEMBER and rows[0][1] == ns.ALERTING_TYPE
    assert "Telegram" in rows[0][3]
    monkeypatch.setattr(kv_miss_alert, "alerting_broken", lambda: None)     # healthy: nothing
    env.conn.execute("DELETE FROM notifications")
    assert run(ns.notify_alerting_broken(MEMBER)) is None
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0
