"""Reminder phone delivery, the lab rehearsal: the REAL ``proactive.engine.fire_notification`` -> the REAL ``reminder_delivery``.

``test_reminder_phone_delivery.py`` already pins the seam with its own I/O faked and the engine wiring with ``deliver`` faked; this
file runs the two together, with a scratch SQLite store standing in for the database (the seam's real SQL runs against it: prefs,
push_subscriptions, reminders, the ``app_settings`` claim rows) and fake senders (no network), for four invented members:

  adult-in    an adult, Telegram linked, opted in to both channels
  adult-out   an adult, Telegram linked, never opted in
  kid-1       a child, linked and "opted in" (the adult rule must stop it)
  adult-quiet an adult, opted in, inside their own quiet hours

per mode: off = the seam never runs and the engine's effects are the baseline; shadow = the same engine effects plus one
``REMINDER_PHONE mode=shadow`` line per channel with the documented class, nothing sent, no claim row; on = sent once per channel to
the opted-in adult only, neutral text for a private / health reminder unless full_text, and a second fire of the same id sends
nothing. Log lines carry classes and a hash only. Record: docs/knowledge/reminder-phone-delivery.md.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from datetime import datetime

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe

import enrollment_state
import proactive.engine as engine
import proactive.triggers.reminder_scan as reminder_scan
import push as push_module
import reminder_delivery
import telegram_notify
import user_erase_gate as erase_gate

NOON = datetime(2026, 1, 5, 12, 0)
PLAIN = ("rem-plain", "Water the ferns", "chores", "family")                       # household-visible, not health: full text
PRIVATE = ("rem-private", "Take the blood pressure tablet", "health", "personal")   # neutral unless full_text

OPTED = {"channels": ["telegram", "web_push"], "full_text": False}
MEMBERS = {
    "adult-in": ("adult", {"telegram_id": "5001", "reminder_delivery": OPTED}),
    "adult-out": ("adult", {"telegram_id": "5002"}),
    "kid-1": ("minor", {"telegram_id": "5003", "reminder_delivery": OPTED}),
    "adult-quiet": ("adult", {"telegram_id": "5004", "reminder_delivery": OPTED,
                              "communication": {"quiet_hours": {"start": "11:00", "end": "13:00"}}}),
}
SHADOW_CLASS = {"adult-in": "would_send", "adult-out": "skipped:not_opted_in", "kid-1": "skipped:not_adult",
                "adult-quiet": "skipped:quiet_hours"}
FORBIDDEN_IN_LOGS = [PLAIN[1], PRIVATE[1], reminder_delivery.NEUTRAL_TEXT, *MEMBERS, "5001", "5002", "5003", "5004"]


class Lab:
    def __init__(self, path):
        self.path = path
        self.telegram: list[tuple[str, str]] = []
        self.web_push: list[tuple[str, str]] = []
        self.engine_push: list[str] = []
        self.deliver_calls = 0

    @contextlib.asynccontextmanager
    async def db(self):
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    def rows(self, sql, args=()):
        async def go():
            async with self.db() as db:
                cur = await db.execute(sql, args)
                out = [tuple(r) for r in await cur.fetchall()]
                await db.commit()
                return out
        return asyncio.run(go())

    def reset_engine_effects(self):
        self.engine_push = []
        self.rows("DELETE FROM notifications")
        self.rows("UPDATE proactive_scheduled SET fired = 0")

    def effects(self):
        """What the engine itself did (the panel side): its direct pushes, in-app notifications, fired schedule rows."""
        return (sorted(self.engine_push), self.rows("SELECT user_id, message FROM notifications ORDER BY user_id, message"),
                self.rows("SELECT id, fired FROM proactive_scheduled ORDER BY id"))


@pytest.fixture
def lab(tmp_path, monkeypatch):
    lab = Lab(str(tmp_path / "lab.db"))

    async def setup():
        async with lab.db() as db:
            await db.executescript("""
                CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, prefs TEXT, updated_at TEXT);
                CREATE TABLE push_subscriptions (user_id TEXT, endpoint TEXT);
                CREATE TABLE reminders (id TEXT PRIMARY KEY, title TEXT, category TEXT, visibility TEXT);
                CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
                CREATE TABLE notifications (id TEXT, user_id TEXT, type TEXT, title TEXT, message TEXT, delivered INT, created_at TEXT);
                CREATE TABLE proactive_scheduled (id TEXT PRIMARY KEY, fired INT DEFAULT 0);
            """)
            for uid, (_aud, prefs) in MEMBERS.items():
                await db.execute("INSERT INTO user_preferences VALUES (?, ?, '')", (uid, json.dumps(prefs)))
                await db.execute("INSERT INTO push_subscriptions VALUES (?, 'https://push.invalid/x')", (uid,))
                for sched in ("sched-1", "sched-2"):
                    await db.execute("INSERT INTO proactive_scheduled (id) VALUES (?)", (f"{sched}-{uid}",))
            for r in (PLAIN, PRIVATE):
                await db.execute("INSERT INTO reminders VALUES (?, ?, ?, ?)", r)
            await db.commit()
    asyncio.run(setup())

    async def audience_of(uid, db=None):
        return MEMBERS.get(uid, ("unknown",))[0]

    async def web_push(uid, title, body, url):
        lab.web_push.append((uid, body))
        return 1

    async def tg_send(tid, text, url=None, label=None, *, client=None):
        lab.telegram.append((tid, text))
        return "sent"

    async def engine_send_push(user_id, message, extra=None):          # the engine's direct (pre-seam) web push
        lab.engine_push.append(user_id)
        return 0                                                       # nobody's browser is open: the in-app fallback runs

    real_deliver = reminder_delivery.deliver

    async def spy_deliver(*a, **k):
        lab.deliver_calls += 1
        return await real_deliver(*a, **k)

    pend = iter(range(1, 10_000))

    async def create_pending(**_):
        return f"pend-{next(pend)}"

    async def no_speak(**_):
        return None

    class _Broadcaster:
        async def broadcast(self, *a, **k):
            return None

    monkeypatch.setattr(reminder_delivery, "_db", lab.db)
    monkeypatch.setattr(reminder_delivery, "_send_web_push", web_push)
    monkeypatch.setattr(reminder_delivery, "deliver", spy_deliver)
    monkeypatch.setattr(enrollment_state, "audience_of", audience_of)
    monkeypatch.setattr(telegram_notify, "send", tg_send)
    monkeypatch.setattr(telegram_notify, "available", lambda: True)
    monkeypatch.setattr(reminder_scan, "zoe_now", lambda now_utc=None: NOON)
    monkeypatch.setattr(engine, "_get_compat_db", lab.db)
    monkeypatch.setattr(engine, "_send_push", engine_send_push)
    monkeypatch.setattr(engine, "create_pending", create_pending)
    monkeypatch.setattr(engine, "_maybe_speak_notification", no_speak)
    monkeypatch.setattr(engine, "_is_in_quiet_hours", lambda *a: False)
    monkeypatch.setattr(push_module, "broadcaster", _Broadcaster())
    monkeypatch.setenv("ZOE_QUIET_START_HOUR", "22")
    monkeypatch.setenv("ZOE_QUIET_END_HOUR", "7")
    erase_gate.reset()
    return lab


def _fire(mode, monkeypatch, uid, reminder=PLAIN, sched="sched-1"):
    monkeypatch.setenv("ZOE_REMINDER_PHONE_DELIVERY", mode)
    asyncio.run(engine.fire_notification(user_id=uid, message=reminder[1], trigger_type="reminder",
                                         pending_id=f"{sched}-{uid}", item_id=reminder[0]))


def _phone_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("REMINDER_PHONE")]


def _assert_lines_carry_no_words(lines):
    for line in lines:
        assert re.fullmatch(r"REMINDER_PHONE mode=(shadow|on) kind=reminder channel=(web_push|telegram) "
                            r"outcome=[a-z_:A-Z]+ ref=[0-9a-f]{8}", line), line
        for word in FORBIDDEN_IN_LOGS:
            assert word not in line, f"log line carries {word!r}: {line}"


def _run_all(mode, monkeypatch):
    for uid in MEMBERS:
        _fire(mode, monkeypatch, uid)


def test_off_never_calls_the_seam_and_is_the_baseline(lab, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="reminder_delivery")
    _run_all("off", monkeypatch)
    assert lab.deliver_calls == 0 and _phone_lines(caplog) == []
    assert lab.telegram == [] and lab.web_push == [] and lab.rows("SELECT * FROM app_settings") == []
    pushes, notes, fired = lab.effects()
    assert pushes == sorted(MEMBERS)                                   # the engine's own push, once each, as before
    assert notes == sorted((u, PLAIN[1]) for u in MEMBERS)            # nobody reached: the in-app fallback, as before
    assert [f for i, f in fired if i.startswith("sched-1-")] == [1] * len(MEMBERS)


def test_shadow_logs_one_classed_line_per_channel_and_sends_and_stores_nothing(lab, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="reminder_delivery")
    _run_all("shadow", monkeypatch)
    shadow_effects = lab.effects()
    lines = _phone_lines(caplog)
    assert lab.deliver_calls == len(MEMBERS)
    assert len(lines) == 2 * len(MEMBERS)
    by_member = [lines[i:i + 2] for i in range(0, len(lines), 2)]      # fired one member at a time, channels in CHANNELS order
    for uid, pair in zip(MEMBERS, by_member):
        assert [re.search(r"channel=(\S+) outcome=(\S+)", ln).groups() for ln in pair] == \
            [("web_push", SHADOW_CLASS[uid]), ("telegram", SHADOW_CLASS[uid])], uid
        assert all(" mode=shadow " in ln for ln in pair)
    _assert_lines_carry_no_words(lines)
    assert lab.telegram == [] and lab.web_push == []                   # the fake senders saw nothing
    assert lab.rows("SELECT * FROM app_settings") == []                # no claim row

    # and the engine's own effects (panel side) are exactly the off baseline, run on the same store
    lab.reset_engine_effects()
    caplog.clear()
    _run_all("off", monkeypatch)
    assert lab.effects() == shadow_effects and _phone_lines(caplog) == []


def test_on_sends_once_per_channel_to_the_opted_in_adult_only(lab, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="reminder_delivery")
    _run_all("on", monkeypatch)
    assert lab.telegram == [("5001", PLAIN[1])]
    assert lab.web_push == [("adult-in", PLAIN[1])]
    assert lab.engine_push == []                                       # reminder web push moved into the seam
    claims = lab.rows("SELECT key, value FROM app_settings ORDER BY key")
    assert [v for _k, v in claims] == ["sent", "sent"] and all(k.startswith("reminder_phone:adult-in:") for k, _ in claims)
    # the panel side is unchanged: the in-app fallback for everyone the phone did not reach
    assert lab.rows("SELECT user_id FROM notifications ORDER BY user_id") == [(u,) for u in sorted(MEMBERS) if u != "adult-in"]
    lines = _phone_lines(caplog)
    _assert_lines_carry_no_words(lines)
    outcomes = sorted(set(re.search(r"outcome=(\S+)", ln).group(1) for ln in lines))
    assert outcomes == ["delivered", "skipped:not_adult", "skipped:not_opted_in", "skipped:quiet_hours"]

    # a second fire of the same id sends nothing
    caplog.clear()
    _fire("on", monkeypatch, "adult-in")
    assert len(lab.telegram) == 1 and len(lab.web_push) == 1
    assert [re.search(r"outcome=(\S+)", ln).group(1) for ln in _phone_lines(caplog)] == ["skipped:duplicate"] * 2


def test_on_a_private_health_reminder_is_neutral_unless_full_text(lab, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="reminder_delivery")
    _fire("on", monkeypatch, "adult-in", reminder=PRIVATE)
    assert lab.telegram == [("5001", reminder_delivery.NEUTRAL_TEXT)]
    assert lab.web_push == [("adult-in", reminder_delivery.NEUTRAL_TEXT)]

    async def opt_full_text():
        async with lab.db() as db:
            prefs = {**MEMBERS["adult-in"][1], "reminder_delivery": {"channels": ["telegram", "web_push"], "full_text": True}}
            await db.execute("UPDATE user_preferences SET prefs = ? WHERE user_id = 'adult-in'", (json.dumps(prefs),))
            await db.commit()
    asyncio.run(opt_full_text())
    _fire("on", monkeypatch, "adult-in", reminder=PRIVATE, sched="sched-2")
    assert lab.telegram[-1] == ("5001", PRIVATE[1]) and lab.web_push[-1] == ("adult-in", PRIVATE[1])
    _assert_lines_carry_no_words(_phone_lines(caplog))
