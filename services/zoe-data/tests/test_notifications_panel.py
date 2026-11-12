"""Household notifications on the wall panel: the store, the orb dot, the card and its taps (``notifications_store.py``, flag
``ZOE_NOTIFICATIONS``; the spoken tail is ``test_notification_tail.py``).
Spec + owner acceptance: docs/knowledge/notifications.md. Each privacy / do-not-overwhelm guard has a NEGATIVE CONTROL beside it
(the same call with the guard's condition removed does the thing). Synthetic ids and text only.
"""
from __future__ import annotations

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
    assert run(ns.create(MEMBER, "note", "T", "M", dedupe_id="a"))                                   # control: scheduled now


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
    assert "((d.enabled&&d.count>0)||d.notify>0||d.notify_any===true)" in html
    assert "notifications-panel.js" not in html                                       # desktop's bell never loads on the estate


# -- the card + taps -----------------------------------------------------------------------------------------------------------
def _fake_repass(monkeypatch, *, confirmed=0, props=("p1",)):
    state = {"props": list(props), "confirmed": [], "rejected": []}

    async def wearer_status(db, uid):
        props = state["props"] if uid == MEMBER else []          # proposals are per member, like the real table
        return {"confirmed": confirmed, "needed": 10, "proposals": [{"id": p, "windows": 6, "recordings": 2} for p in props]}

    async def proposal_lines(db, uid, pid, limit=6):
        return [{"recording_id": "r", "at_ms": 0, "text": LINE}]

    async def confirm_wearer(db, uid, pid):
        state["props"].remove(pid)
        state["confirmed"].append((uid, pid))
        return 1

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
    assert run(ns.notify_wearer_proposals(MEMBER))                                     # a new batch: a new notification


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


def test_a_create_scheduled_before_the_erase_stays_erased(env):
    gen = erase_gate.generation(MEMBER)
    run(ns.forget_user(MEMBER))
    assert run(ns.create(MEMBER, "note", "T", "M", dedupe_id="late", scheduled_at=gen)) is None
    assert env.rows("SELECT COUNT(*) FROM notifications")[0][0] == 0

