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
    assert off == {k: v for k, v in on.items() if k not in ("notify", "notify_touch")}
    guest = {"user_id": "guest", "role": "guest"}
    assert "notify_any" not in run(r.pending_inbox(guest))                            # the kiosk boolean is absent too
    monkeypatch.setenv("ZOE_NOTIFICATIONS", "on")
    assert "notify_any" in run(r.pending_inbox(guest))


def test_home_paints_the_existing_dot_for_notify():
    html = (SVC.parents[1] / "services/zoe-ui/dist/touch/home.html").read_text()
    assert "var mine=!!(d&&!d.quiet&&((d.enabled&&d.count>0)||d.notify>0));" in html
    assert "var any=!!(d&&!d.quiet&&d.notify_any===true);" in html and "paint(mine||any," in html
    assert "notifications-panel.js" not in html                                       # desktop's bell never loads on the estate


def test_a_notifications_dot_press_opens_the_list_never_the_voiceless_pull_and_control():
    """``notify_any`` alone proves no one and /pull carries no voice, so the voiceless pull is refused for a dot lit by notifications
    alone. Since 2026-11-24 that press opens the LIST (POST /api/panel/notifications/open, no mic); only a selector-reported item
    pulls aloud. Behaviour is pinned in dist/test_touch_review_cards.js (the real orb block in a vm); this pins the wiring."""
    html = (SVC.parents[1] / "services/zoe-ui/dist/touch/home.html").read_text()
    paint = html[html.index("function paint(on,review"):]
    paint = paint[:paint.index("}")]
    assert "_dotVoiceOnly=!!(on&&voiceOnly)" in paint and "_dotNotes=!!(on&&notes)" in paint   # set on every paint, cleared with the dot
    assert "var pullable=!!(d&&!d.quiet&&d.enabled&&d.count>0);" in html and ",!pullable,any||" in html
    # ONE press path (orbPress): the orb and Settings > Notifications "Show the card" both run it
    assert "orb.addEventListener('click',function(){orbPress();});" in html
    click = html[html.index("function orbPress(idlePrompt){"):]
    click = click[:click.index("\n  }")]
    lst, pull = click.index("else if(_listOn&&free&&(idlePrompt||(ready&&_dotNotes)))orbList(idlePrompt);"), click.index("else if(ready)orbPull();")
    assert lst < pull                                                                  # the list wins before any pull
    old = click.index("else if(ready&&_dotVoiceOnly)panelVoice(")                      # flag off: the old press, branch for branch
    assert lst < old < pull and "_listOn=!!(d&&d.notify_touch===true);" in html
    assert click.count("orbPull()") == 1                                               # control: a selector item still pulls
    assert "'/api/panel/notifications/open'" in html


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
    assert [a["key"] for a in frame["items"][0]["actions"]] == ["me", "not_me", "clear"]
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
SINCE = 1_790_000_000.0


def test_broken_alerting_is_created_by_the_tick_without_any_refresh_idempotent_and_kept_after_heal(env, monkeypatch):
    """An alert about the Telegram credential failing cannot travel over Telegram. The ops helper records each broken spell;
    zoe-data's periodic tick (sync_alerting_broken, no request, no refresh) creates ONE row per owner per spell, idempotent
    across ticks, kept and marked resolved after the spell heals. Negative controls: a non-owner gets nothing; never broken =
    nothing."""
    import kv_miss_alert

    env.conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (MEMBER,))
    spells = [{"since": SINCE, "reason": "auth", "healed": None}]
    monkeypatch.setattr(kv_miss_alert, "alerting_spells", lambda: spells)
    called = []
    monkeypatch.setattr(ns, "refresh", lambda *a, **k: called.append(a))     # proves no refresh is involved
    assert run(ns.sync_alerting_broken()) == 1
    assert run(ns.sync_alerting_broken()) == 0                                # next tick: same spell, no second row
    rows = env.rows("SELECT id, user_id, type, message, data FROM notifications")
    assert len(rows) == 1 and rows[0][1] == MEMBER and rows[0][2] == ns.ALERTING_TYPE and "Telegram" in rows[0][3]
    assert not json.loads(rows[0][4]).get("resolved") and called == []
    spells[0]["healed"] = SINCE + 600                                         # the spell heals
    run(ns.sync_alerting_broken())
    run(ns.sync_alerting_broken())
    rows = env.rows("SELECT id, data FROM notifications")
    assert len(rows) == 1 and json.loads(rows[0][1]) == {"reason": "auth", "since": int(SINCE), "resolved": True,
                                                           "healed_at": int(SINCE + 600)}
    assert env.rows("SELECT COUNT(*) FROM notifications WHERE user_id = ?", (OTHER,))[0][0] == 0


def test_a_spell_that_healed_before_any_tick_still_leaves_a_resolved_row(env, monkeypatch):
    import kv_miss_alert

    env.conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (MEMBER,))
    monkeypatch.setattr(kv_miss_alert, "alerting_spells", lambda: [{"since": SINCE, "reason": "config", "healed": SINCE + 5}])
    assert run(ns.sync_alerting_broken()) == 1
    (data,) = env.rows("SELECT data FROM notifications")[0]
    assert json.loads(data)["resolved"] is True
    # a healed spell must stop READING as broken (review of PR 2458): the kept row says alerts work again
    title, message = env.rows("SELECT title, message FROM notifications")[0]
    assert title == ns.ALERTING_HEALED_TITLE and message == ns.ALERTING_HEALED_MESSAGE
    assert "not getting through" not in title and "refusing" not in message
    assert run(ns.sync_alerting_broken()) == 0                                # a later tick neither re-creates nor rewrites it
    assert env.rows("SELECT title FROM notifications")[0][0] == ns.ALERTING_HEALED_TITLE
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 1


def test_an_open_spell_still_reads_as_broken(env, monkeypatch):
    import kv_miss_alert

    env.conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (MEMBER,))
    monkeypatch.setattr(kv_miss_alert, "alerting_spells", lambda: [{"since": SINCE, "reason": "auth", "healed": None}])
    assert run(ns.sync_alerting_broken()) == 1
    title, message = env.rows("SELECT title, message FROM notifications")[0]
    assert title == ns.ALERTING_TITLE and message == ns.ALERTING_MESSAGE["auth"]


def test_never_broken_creates_nothing(env, monkeypatch):
    import kv_miss_alert

    env.conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (MEMBER,))
    monkeypatch.setattr(kv_miss_alert, "alerting_spells", lambda: [])
    assert run(ns.sync_alerting_broken()) == 0
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0


def test_the_runtime_health_tick_runs_the_sync():
    """The 5-minute tick that needs no request is what creates the row."""
    import ast

    tree = ast.parse((SVC / "main.py").read_text())
    fns = {n.name: ast.get_source_segment((SVC / "main.py").read_text(), n) for n in tree.body
           if isinstance(n, ast.AsyncFunctionDef)}
    assert "await _sync_alerting_notice()" in fns["_runtime_health_refresh_loop"]
    assert "sync_alerting_broken()" in fns["_sync_alerting_notice"]


# -- the orb press with no voice: the list, clear that stays cleared, a tap that opens something (owner, 2026-11-24) ----------------
GUEST = {"user_id": "guest", "role": "guest"}


def _raw(env, nid, member, type_, title="T", message="M", data="{}", delivered=0):
    env.conn.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (nid, member, type_, title, message, data, delivered, datetime.now(timezone.utc).isoformat()))
    env.conn.commit()


def _client(monkeypatch, caller, *, at_panel):
    """The card routes with ``caller`` as the request's principal; ``at_panel`` = is the request the registered panel itself."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import panel_principal
    from routers import notifications as rn

    async def is_panel_guest(request, user, db=None, *, mode=None):
        return bool(at_panel) and panel_principal.is_guest_id((user or {}).get("user_id"))

    async def panel_of(request, user, db=None):
        return PANEL if at_panel else ""
    monkeypatch.setattr(panel_principal, "is_panel_guest", is_panel_guest)
    monkeypatch.setattr(panel_principal, "panel_of", panel_of)
    app = FastAPI()
    assert rn.register(app) is True
    app.dependency_overrides[rn._caller] = lambda: caller
    return TestClient(app)


def test_receipts_never_light_the_dot_or_fill_the_card_and_control(env):
    from routers import proactive as r
    for i, t in enumerate(ns.RECEIPT_TYPES * 20):                                      # 60 "reminder created / snoozed / done" echoes
        _raw(env, f"r{i}", MEMBER, t, "Reminder Created", "Reminder added: water the fern")
    assert run(ns.unread_count(MEMBER)) == 0 and run(ns.unread(MEMBER)) == []
    assert run(r.pending_inbox({"user_id": MEMBER, "role": "user"}))["notify"] == 0
    assert run(r.pending_inbox(GUEST))["notify_any"] is False
    assert run(ns.card(PANEL, MEMBER)) is None
    _raw(env, "due", MEMBER, "reminder", "Reminder", "water the fern")                 # control: a reminder that FIRED is a notification
    assert run(ns.unread_count(MEMBER)) == 1 and run(r.pending_inbox(GUEST))["notify_any"] is True


def test_the_press_shows_the_panel_the_wall_safe_rows_without_a_voice_and_controls(env, monkeypatch):
    _raw(env, "h1", MEMBER, "omi_health", "Pendant", "The pendant battery is low.")
    _raw(env, "r1", OTHER, "reminder", "Reminder", "collect the purple parcel")       # a member's own words: never on the wall
    _raw(env, "s1", MEMBER, "omi_health", "Pendant", "private pendant line", json.dumps({"sensitive": True}))
    c = _client(monkeypatch, GUEST, at_panel=True)
    frame = c.post("/api/panel/notifications/open", json={"panel_id": "x"}).json()
    texts = json.dumps(frame)
    assert [it["id"] for it in frame["items"]] == ["n1", "locked"] and frame["touch"] is True and frame["private"] is False
    assert "battery is low" in texts and frame["items"][0]["open"] == "settings"
    assert "purple parcel" not in texts and "private pendant line" not in texts        # locked, and sensitive not even counted
    assert MEMBER not in texts and OTHER not in texts and frame["member_id"] == ""     # never whose
    assert frame["items"][1]["open"] == "voice" and frame["items"][1]["actions"] == []
    assert env.pushed == []                                                            # answered to the press, pushed to no screen
    # controls: the same press from a guest session that is NOT the panel, and with the touch flag off
    assert _client(monkeypatch, GUEST, at_panel=False).post("/api/panel/notifications/open", json={}).status_code == 403


def test_touch_flag_off_is_the_old_press_for_every_caller_and_control(env, monkeypatch):
    """Off = the route answers as if unmounted for the panel AND for a member's own session, and the inbox poll (what the page
    already loads) carries no ``notify_touch``, so the page never asks /open at all."""
    from routers import proactive as r
    seed(1)
    _raw(env, "h1", MEMBER, "omi_health", "Pendant", "The pendant battery is low.")
    member = {"user_id": MEMBER, "role": "user"}
    monkeypatch.setenv("ZOE_NOTIFICATIONS_PANEL_TOUCH", "off")
    assert _client(monkeypatch, GUEST, at_panel=True).post("/api/panel/notifications/open", json={}).status_code == 404
    assert _client(monkeypatch, member, at_panel=False).post("/api/panel/notifications/open", json={}).status_code == 404
    assert "notify_touch" not in run(r.pending_inbox(GUEST)) and "notify_touch" not in run(r.pending_inbox(member))
    assert run(r.pending_inbox(GUEST))["notify_any"] is True and run(r.pending_inbox(member))["notify"] == 2   # the dot itself is as before
    assert env.pushed == [] and prc._CARDS == {}                                       # nothing minted, nothing pushed
    monkeypatch.setenv("ZOE_NOTIFICATIONS_PANEL_TOUCH", "on")                          # control: the same calls, flag on
    assert _client(monkeypatch, GUEST, at_panel=True).post("/api/panel/notifications/open", json={}).json()["touch"] is True
    assert _client(monkeypatch, member, at_panel=False).post("/api/panel/notifications/open", json={}).json()["private"] is True
    assert run(r.pending_inbox(GUEST))["notify_touch"] is True and run(r.pending_inbox(member))["notify_touch"] is True


def test_a_clear_from_the_panel_stays_cleared_and_controls(env, monkeypatch):
    from routers import proactive as r
    _raw(env, "h1", MEMBER, "omi_health", "Pendant", "The pendant battery is low.")
    c = _client(monkeypatch, GUEST, at_panel=True)
    frame = c.post("/api/panel/notifications/open", json={}).json()
    h = {"X-Panel-Card": frame["card_token"]}
    # control: the same token and tap from somewhere that is not the panel is refused and clears nothing
    away = _client(monkeypatch, GUEST, at_panel=False)
    assert away.post("/api/panel/notifications/act", json={"item": "n1", "action": "done"}, headers=h).status_code == 403
    assert env.rows("SELECT delivered FROM notifications")[0][0] == 0
    with pytest.raises(prc.CardError):                                                 # and no other card route honours a panel token
        prc.authorize(frame["card_token"], {"user_id": MEMBER, "role": "user"})
    c = _client(monkeypatch, GUEST, at_panel=True)
    assert c.post("/api/panel/notifications/act", json={"item": "n1", "action": "done"}, headers=h).json() == {"ok": True}
    assert env.rows("SELECT delivered, action_taken FROM notifications")[0] == (1, "done_panel")
    # ... and it STAYS cleared: a reload (a fresh press), the dot's poll, the member's own count, the same creator running again
    assert c.post("/api/panel/notifications/open", json={}).json().get("empty") is True
    assert run(r.pending_inbox(GUEST))["notify_any"] is False
    assert run(r.pending_inbox({"user_id": MEMBER, "role": "user"}))["notify"] == 0
    env.conn.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered) VALUES ('h1', ?, 'omi_health', 'P', 'M', '{}', 0) "
                     "ON CONFLICT (id) DO NOTHING", (MEMBER,))
    assert run(ns.unread_count(MEMBER)) == 0


def test_a_cleared_voice_ask_is_not_asked_again_when_the_batch_only_shrinks_and_control(env, monkeypatch):
    state = _fake_repass(monkeypatch, props=("p1", "p2"))
    nid = run(ns.notify_wearer_proposals(MEMBER))
    assert nid and run(ns.mark_read(MEMBER, nid, "later"))                             # the member cleared the ask ("Not now")
    state["props"].remove("p1")                                                        # one proposal expired: a different batch hash
    assert run(ns.notify_wearer_proposals(MEMBER)) is None
    assert run(ns.unread_count(MEMBER)) == 0                                           # cleared stays cleared
    state["props"].append("p9")                                                        # control: a voice never asked about IS asked
    assert run(ns.notify_wearer_proposals(MEMBER)) and run(ns.unread_count(MEMBER)) == 1


def test_not_now_clears_the_voice_ask_and_leaves_the_proposals_undecided(env, monkeypatch):
    state = _fake_repass(monkeypatch)
    run(ns.notify_wearer_proposals(MEMBER))
    frame = run(ns.card(PANEL, MEMBER))
    assert [a["key"] for a in frame["items"][0]["actions"]] == ["me", "not_me", "clear"]
    item = prc._CARDS[frame["card_token"]].items["w1"]
    assert run(ns.act(MEMBER, item, "clear")) == {"ok": True}
    assert run(ns.unread_count(MEMBER)) == 0 and state["props"] == ["p1"] and not state["confirmed"] and not state["rejected"]
    assert run(ns.notify_wearer_proposals(MEMBER)) is None                             # the creator's next pass does not bring it back


def test_clear_all_takes_the_rows_that_did_not_fit_and_only_the_cards_own_and_controls(env, monkeypatch):
    _fake_repass(monkeypatch)
    run(ns.notify_wearer_proposals(MEMBER))
    seed(12)
    seed(2, member=OTHER)
    _raw(env, "rc", MEMBER, "reminder_created", "Reminder Created", "Reminder added: x")
    c = _client(monkeypatch, {"user_id": MEMBER, "role": "user"}, at_panel=False)
    frame = c.post("/api/panel/notifications/open", json={"panel_id": PANEL}).json()   # the member's own session: their own card
    assert frame["private"] is True and frame["member_id"] == MEMBER and frame["more"] == 5 and frame["clear_all"] == "Clear all"
    out = c.post("/api/panel/notifications/act", json={"item": "*", "action": "clear_all"}, headers={"X-Panel-Card": frame["card_token"]})
    assert out.json() == {"ok": True, "cleared": 13}                                   # all 12, shown or not, and the old receipt
    assert run(ns.unread_count(MEMBER)) == 1                                           # the voice ask wants an answer: not swept up
    assert env.rows("SELECT type FROM notifications WHERE user_id = ? AND delivered = 0", (MEMBER,)) == [(ns.WEARER_TYPE,)]
    assert run(ns.unread_count(OTHER)) == 2                                            # control: another member's rows are untouched
    # the panel's own clear-all: the household's wall-safe rows, never a member's own words
    _raw(env, "h1", OTHER, "omi_health", "Pendant", "The pendant battery is low.")
    _raw(env, "r1", OTHER, "reminder", "Reminder", "collect the purple parcel")
    k = _client(monkeypatch, GUEST, at_panel=True)
    kf = k.post("/api/panel/notifications/open", json={}).json()
    assert k.post("/api/panel/notifications/act", json={"item": "*", "action": "clear_all"}, headers={"X-Panel-Card": kf["card_token"]}).json()["cleared"] == 1
    left = {r[0] for r in env.rows("SELECT id FROM notifications WHERE user_id = ? AND delivered = 0", (OTHER,))}
    assert "h1" not in left and "r1" in left and len(left) == 3


def test_many_sync_notices_are_one_row_and_one_clear_and_control(env):
    for i in range(5):
        _raw(env, f"s{i}", MEMBER, "omi_sync", "Pendant synced", f"I've synced {i + 1} recordings from your pendant.")
    _raw(env, "a", MEMBER, "note", "A", "one")
    _raw(env, "b", MEMBER, "note", "A", "two")                                         # control: other kinds are never folded
    frame = run(ns.card(PANEL, MEMBER))
    sync = [it for it in frame["items"] if it["text"] == "Pendant synced"]
    assert len(sync) == 1 and "(and 4 earlier)" in sync[0]["meta"] and len(frame["items"]) == 3
    item = prc._CARDS[frame["card_token"]].items[sync[0]["id"]]
    assert run(ns.act(MEMBER, item, "done")) == {"ok": True}
    assert env.rows("SELECT COUNT(*) FROM notifications WHERE type = 'omi_sync' AND delivered = 0")[0][0] == 0
    assert run(ns.unread_count(MEMBER)) == 2


def test_a_tap_on_a_row_names_what_it_opens_and_controls(env):
    long = "Telegram is refusing the sign-in of the alert bot. " * 4
    _raw(env, "1", MEMBER, "reminder", "Reminder", "water the fern")
    _raw(env, "2", MEMBER, "transcript_check", "Transcript check", "A few words need you.")
    _raw(env, "3", MEMBER, "omi_health", "Pendant", "The pendant battery is low.")
    _raw(env, "4", MEMBER, "ops_alerting_broken", "Phone alerts are not getting through", long)
    _raw(env, "5", MEMBER, "note", "Bins", "tonight", json.dumps({"open": "calendar"}))  # a creator may name an estate screen
    _raw(env, "6", MEMBER, "note", "Odd", "x", json.dumps({"open": "/people.html"}))     # control: anything else is not a target
    by = {it["text"]: it for it in run(ns.card(PANEL, MEMBER))["items"]}
    assert by["Reminder"]["open"] == "reminder" and by["Transcript check"]["open"] == "checks" and by["Pendant"]["open"] == "settings"
    assert by["Bins"]["open"] == "calendar" and "open" not in by["Odd"] and by["Odd"]["detail"] == "x"
    alert = by["Phone alerts are not getting through"]
    assert "open" not in alert and len(alert["detail"]) > len(alert["meta"])            # no screen for it: the row opens out instead
    # the panel's own card cannot open a member's private screen: there the tap asks for a voice
    wall = {it["text"]: it for it in run(ns.panel_card(PANEL))["items"]}
    assert wall["Transcript check"]["open"] == "voice" and wall["Pendant"]["open"] == "settings"
    assert "Reminder" not in wall and "Bins" not in wall and ns.LOCKED_TEXT in wall


def test_every_row_does_something_on_a_tap_and_control(env, monkeypatch):
    _fake_repass(monkeypatch)
    run(ns.notify_wearer_proposals(MEMBER))
    _raw(env, "1", MEMBER, "note", "Short", "hi")                                      # short, no screen: used to have no tap at all
    _raw(env, "2", MEMBER, "info", "No words", "")
    _raw(env, "3", MEMBER, "omi_health", "Pendant", "The pendant battery is low.")
    _raw(env, "4", MEMBER, "ops_alerting_broken", "Phone alerts are not getting through", "short")
    for frame in (run(ns.card(PANEL, MEMBER)), run(ns.panel_card(PANEL))):
        assert len(frame["items"]) >= 4
        for it in frame["items"]:
            assert bool(it.get("open")) != bool(it.get("detail")), it["text"]           # exactly one of: a screen / the mic, or its words
    by = {it["text"]: it for it in run(ns.card(PANEL, MEMBER))["items"]}
    assert by["Short"]["detail"] == "hi" and by["No words"]["detail"] == "No words"    # control: a row with a screen keeps the screen
    assert by["Pendant"]["open"] == "settings" and "detail" not in by["Pendant"]


def test_one_clear_takes_the_whole_sync_backlog_not_a_read_window_and_control(env, monkeypatch):
    for i in range(55):
        _raw(env, f"n{i:03d}", MEMBER, "note", f"Note {i}", "m")
    for i in range(130):                                                               # newest, and far past any 50-row read
        _raw(env, f"s{i:03d}", MEMBER, "omi_sync", "Pendant synced", "I've synced 2 recordings from your pendant.")
    _raw(env, "so", OTHER, "omi_sync", "Pendant synced", "I've synced 1 recording from your pendant.")
    c = _client(monkeypatch, {"user_id": MEMBER, "role": "user"}, at_panel=False)
    frame = c.post("/api/panel/notifications/open", json={"panel_id": PANEL}).json()
    (sync,) = [it for it in frame["items"] if it["text"] == "Pendant synced"]          # folded by kind in SQL: counted over the
    assert "(and 129 earlier)" in sync["meta"] and len(frame["items"]) == ns.MAX_CARD  # whole table, and the notes still get rows
    h = {"X-Panel-Card": frame["card_token"]}
    assert c.post("/api/panel/notifications/act", json={"item": sync["id"], "action": "done"}, headers=h).json() == {"ok": True}
    left = dict(env.rows("SELECT type, COUNT(*) FROM notifications WHERE user_id = ? AND delivered = 0 GROUP BY type", (MEMBER,)))
    assert left == {"note": 55}                                                        # every sync row, and control: only that kind
    assert run(ns.unread_count(OTHER)) == 1                                            # control: and only that member's
    again = c.post("/api/panel/notifications/open", json={"panel_id": PANEL}).json()
    assert not [it for it in again["items"] if it["text"] == "Pendant synced"]         # it does not come back


def test_clear_all_is_one_update_past_every_cap_and_controls(env, monkeypatch):
    _fake_repass(monkeypatch)
    run(ns.notify_wearer_proposals(MEMBER))
    for i in range(520):                                                               # past the panel read's 500
        _raw(env, f"h{i:03d}", MEMBER, "omi_health", "Pendant", "The pendant battery is low.")
    for i in range(520):                                                               # newer rows the panel may not clear fill any window
        _raw(env, f"r{i:03d}", OTHER, "reminder", "Reminder", "collect the purple parcel")
    _raw(env, "ops", MEMBER, "ops_alerting_broken", "Phone alerts are not getting through", "Telegram is refusing the sign-in.")
    _raw(env, "tc", MEMBER, "transcript_check", "Transcript check", "A few words need you.")
    _raw(env, "hs", MEMBER, "omi_health", "Pendant", "private", json.dumps({"sensitive": True}))
    _raw(env, "hg", "guest", "omi_health", "Pendant", "a guest row")
    k = _client(monkeypatch, GUEST, at_panel=True)
    kf = k.post("/api/panel/notifications/open", json={}).json()
    out = k.post("/api/panel/notifications/act", json={"item": "*", "action": "clear_all"}, headers={"X-Panel-Card": kf["card_token"]}).json()
    assert out == {"ok": True, "cleared": 520}                                         # every device notice, not the first 500 rows read
    left = {r[0] for r in env.rows("SELECT id FROM notifications WHERE delivered = 0 AND type <> 'reminder'")}
    # controls: a member's voice ask and transcript check, the owner alert, a sensitive row and a guest's row are all untouched
    assert {"ops", "tc", "hs", "hg"} <= left and len(left) == 5
    assert env.rows("SELECT COUNT(*) FROM notifications WHERE delivered = 0 AND type = 'reminder'")[0][0] == 520
    m = _client(monkeypatch, {"user_id": OTHER, "role": "user"}, at_panel=False)       # a member's own Clear all: all 520 of theirs
    mf = m.post("/api/panel/notifications/open", json={"panel_id": PANEL}).json()
    assert m.post("/api/panel/notifications/act", json={"item": "*", "action": "clear_all"}, headers={"X-Panel-Card": mf["card_token"]}).json()["cleared"] == 520
    assert run(ns.unread_count(OTHER)) == 0 and run(ns.unread_count(MEMBER)) == 4      # control: MEMBER's four are still theirs


def test_the_panel_cannot_clear_or_read_an_ask_addressed_to_one_member_and_control(env, monkeypatch):
    _fake_repass(monkeypatch)
    wid = run(ns.notify_wearer_proposals(MEMBER))
    _raw(env, "tc", MEMBER, "transcript_check", "Transcript check", "A few words from last night need you.")
    _raw(env, "ops", MEMBER, "ops_alerting_broken", "Phone alerts are not getting through", "Telegram is refusing the sign-in.")
    _raw(env, "h1", MEMBER, "omi_health", "Pendant", "The pendant battery is low.")
    k = _client(monkeypatch, GUEST, at_panel=True)
    frame = k.post("/api/panel/notifications/open", json={}).json()
    by = {it["text"]: it for it in frame["items"]}
    text = json.dumps(frame)
    for title in (ns.WEARER_TITLE, "Transcript check"):                                # the title alone: no stored message, no Clear
        assert by[title]["actions"] == [] and by[title]["open"] == "voice" and by[title]["meta"] == ns.PANEL_TITLE_ONLY_META
    assert ns.WEARER_MESSAGE not in text and "last night need you" not in text and LINE not in text
    alert = by["Phone alerts are not getting through"]                                 # the owner alert: words, but not the panel's to clear
    assert alert["actions"] == [] and "refusing the sign-in" in alert["detail"]
    held = prc._CARDS[frame["card_token"]].items
    assert [v["ref"] for v in held.values()] == ["h1"]                                 # only the device notice can be acted on at all
    h = {"X-Panel-Card": frame["card_token"]}
    for it in (by[ns.WEARER_TITLE], by["Transcript check"], alert):
        assert k.post("/api/panel/notifications/act", json={"item": it["id"], "action": "done"}, headers=h).status_code == 404
    assert k.post("/api/panel/notifications/act", json={"item": "*", "action": "clear_all"}, headers=h).json()["cleared"] == 1
    assert {r[0] for r in env.rows("SELECT id FROM notifications WHERE delivered = 0")} == {wid, "tc", "ops"}
    # control: the device notice was the panel's to clear, and the member's own card still offers Not now on their ask
    assert by["Pendant"]["actions"] == [{"key": "done", "label": "Clear"}]
    mine = run(ns.card(PANEL, MEMBER))
    ask = next(it for it in mine["items"] if it["text"] == ns.WEARER_TITLE)
    assert [a["key"] for a in ask["actions"]] == ["me", "not_me", "clear"]
