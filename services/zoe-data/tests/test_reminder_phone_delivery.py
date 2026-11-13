"""Reminders on the phone (reminder_delivery.py, ZOE_REMINDER_PHONE_DELIVERY). Invented members and ids; every outbound call is a fake.

Pins: flag off = the seam is never called and the engine pushes exactly as before; shadow sends and stores nothing; on = owner-only,
opt-in, adults only, quiet hours, neutral text for sensitive reminders, at most once across a restart, one failing channel never
blocks the other or the scheduler, an erase in flight wins, and forget_user removes the member's ledger rows.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
from datetime import datetime

import pytest

pytestmark = pytest.mark.ci_safe

import proactive.engine as engine
import reminder_delivery
import telegram_notify
import user_erase_gate as erase_gate

# The REAL senders, captured before any fixture fakes them (the interface tests below go through these).
_REAL_WEB_PUSH = reminder_delivery._send_web_push
_REAL_TG_SEND, _REAL_TG_AVAILABLE = telegram_notify.send, telegram_notify.available

NOON = datetime(2026, 1, 5, 12, 0)
LATE = datetime(2026, 1, 5, 23, 30)

MEMBERS = {
    "member-a": {"audience": "adult", "subs": 1,
                 "prefs": {"telegram_id": "1001", "reminder_delivery": {"channels": ["telegram", "web_push"]}}},
    "member-b": {"audience": "adult", "subs": 1,          # another adult, linked and opted in: must never receive member-a's reminder
                 "prefs": {"telegram_id": "2002", "reminder_delivery": {"channels": ["telegram", "web_push"]}}},
    "member-c": {"audience": "adult", "subs": 1, "prefs": {"telegram_id": "3003"}},           # linked, never opted in
    "kid-1": {"audience": "minor", "subs": 1,
              "prefs": {"telegram_id": "4004", "reminder_delivery": {"channels": ["telegram", "web_push"]}}},
    "guest": {"audience": "guest", "subs": 0, "prefs": {}},
}


class World:
    def __init__(self):
        self.ledger: dict[str, str] = {}
        self.telegram: list[tuple[str, str]] = []
        self.web_push: list[tuple[str, str]] = []
        self.tg_outcome = "sent"
        self.tg_raises = False
        self.push_raises = False
        self.on_audience = None


def _wire(mp, rd, w: World):
    async def audience(uid):
        if w.on_audience:
            await w.on_audience(uid)
        return MEMBERS.get(uid, {}).get("audience", "unknown")

    async def prefs(uid):
        return MEMBERS[uid]["prefs"]

    async def subs(uid):
        return MEMBERS[uid]["subs"]

    async def claim(key):
        if key in w.ledger:
            return False
        w.ledger[key] = "claimed"
        return True

    async def settle(key, outcome):
        if outcome == "retry":
            w.ledger.pop(key, None)
        elif key in w.ledger:
            w.ledger[key] = outcome

    async def web_push(uid, title, body, url):
        if w.push_raises:
            raise RuntimeError("push service down")
        w.web_push.append((uid, body))
        return 1

    async def tg_send(tid, text, url=None, label=None, *, client=None):
        if w.tg_raises:
            raise RuntimeError("bot down")
        w.telegram.append((tid, text))
        return w.tg_outcome

    mp.setattr(rd, "_audience", audience)
    mp.setattr(rd, "_member_prefs", prefs)
    mp.setattr(rd, "_subscription_count", subs)
    mp.setattr(rd, "_claim", claim)
    mp.setattr(rd, "_settle", settle)
    mp.setattr(rd, "_send_web_push", web_push)
    mp.setattr(telegram_notify, "send", tg_send)
    mp.setattr(telegram_notify, "available", lambda: True)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("ZOE_REMINDER_PHONE_DELIVERY", "on")
    monkeypatch.setenv("ZOE_QUIET_START_HOUR", "22")
    monkeypatch.setenv("ZOE_QUIET_END_HOUR", "7")
    erase_gate.reset()
    w = World()
    _wire(monkeypatch, reminder_delivery, w)
    return w


def _deliver(uid="member-a", *, sensitive=False, now=NOON, dedupe="sched-1", rd=None):
    return asyncio.run((rd or reminder_delivery).deliver(uid, "reminder", "Water the ferns", "", dedupe_id=dedupe,
                                                         sensitive=sensitive, now=now))


# -- the seam -----------------------------------------------------------------------------------------------------------------
def test_owner_only_both_channels(world):
    res = _deliver("member-a")
    assert res.outcomes == {"web_push": "delivered", "telegram": "delivered"} and res.web_push_sent == 1
    assert world.telegram == [("1001", "Water the ferns")]
    assert world.web_push == [("member-a", "Water the ferns")]


def test_not_opted_in_gets_nothing(world):
    res = _deliver("member-c")
    assert set(res.outcomes.values()) == {"skipped:not_opted_in"}
    assert world.telegram == [] and world.web_push == [] and world.ledger == {}


@pytest.mark.parametrize("uid", ["kid-1", "guest", "nobody"])
def test_child_guest_and_unknown_get_nothing(world, uid):
    res = _deliver(uid)
    assert set(res.outcomes.values()) == {"skipped:not_adult"}
    assert world.telegram == [] and world.web_push == []


def test_household_quiet_hours_skip_phone_channels(world):
    assert set(_deliver(now=LATE).outcomes.values()) == {"skipped:quiet_hours"}
    assert world.telegram == [] and world.web_push == [] and world.ledger == {}


def test_member_quiet_hours_win_over_the_household(world, monkeypatch):
    prefs = {**MEMBERS["member-a"]["prefs"], "communication": {"quiet_hours": {"start": "11:00", "end": "13:00"}}}
    monkeypatch.setitem(MEMBERS["member-a"], "prefs", prefs)
    assert set(_deliver(now=NOON).outcomes.values()) == {"skipped:quiet_hours"}
    assert _deliver(now=LATE, dedupe="sched-2").outcomes["telegram"] == "delivered"   # their window, not the household's


def test_sensitive_reminder_is_neutral_unless_full_text(world, monkeypatch):
    _deliver(sensitive=True)
    assert world.telegram == [("1001", reminder_delivery.NEUTRAL_TEXT)]
    assert world.web_push == [("member-a", reminder_delivery.NEUTRAL_TEXT)]
    prefs = {**MEMBERS["member-a"]["prefs"], "reminder_delivery": {"channels": ["telegram"], "full_text": True}}
    monkeypatch.setitem(MEMBERS["member-a"], "prefs", prefs)
    _deliver(sensitive=True, dedupe="sched-2")
    assert world.telegram[-1] == ("1001", "Water the ferns")


def test_at_most_once_across_a_restart(world, monkeypatch):
    _deliver()
    rd2 = importlib.reload(reminder_delivery)          # a process restart: module state gone, the ledger (the DB) kept
    _wire(monkeypatch, rd2, world)
    res = _deliver(rd=rd2)
    assert set(res.outcomes.values()) == {"skipped:duplicate"}
    assert len(world.telegram) == 1 and len(world.web_push) == 1
    _deliver(rd=rd2, dedupe="sched-2")                 # a different fire is not a duplicate
    assert len(world.telegram) == 2


def test_a_definite_failure_releases_the_claim_but_uncertain_does_not(world):
    world.tg_outcome = "retry"
    assert _deliver().outcomes["telegram"] == "failed:retry"
    world.tg_outcome = "sent"
    assert _deliver().outcomes["telegram"] == "delivered"
    world.tg_outcome = "uncertain"
    _deliver(dedupe="sched-2")
    world.tg_outcome = "sent"
    assert _deliver(dedupe="sched-2").outcomes["telegram"] == "skipped:duplicate"


def test_a_failing_channel_does_not_block_the_other_and_never_raises(world):
    world.tg_raises = True
    res = _deliver()
    assert res.outcomes == {"web_push": "delivered", "telegram": "failed:RuntimeError"}
    world.tg_raises, world.push_raises = False, True
    res = _deliver(dedupe="sched-2")
    assert res.outcomes["telegram"] == "delivered" and res.outcomes["web_push"] == "failed:RuntimeError"


def test_a_slow_channel_is_bounded(world, monkeypatch):
    monkeypatch.setattr(reminder_delivery, "CHANNEL_TIMEOUT_S", 0.05)

    async def hang(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(telegram_notify, "send", hang)
    res = _deliver()
    assert res.outcomes == {"web_push": "delivered", "telegram": "failed:timeout"}


def test_a_pre_send_error_releases_the_claim_and_a_retry_delivers_once(world, monkeypatch):
    calls = []

    async def broken_then_fine(uid, title, body, url):
        calls.append(uid)
        if len(calls) == 1:
            raise TypeError("unexpected keyword argument")   # a bad call: nothing left the box
        world.web_push.append((uid, body))
        return 1

    monkeypatch.setattr(reminder_delivery, "_send_web_push", broken_then_fine)
    assert _deliver().outcomes["web_push"] == "failed:TypeError"
    assert not any(k.endswith(":web_push") for k in world.ledger)            # released
    assert _deliver().outcomes["web_push"] == "delivered"                     # the retry of the same fire
    assert _deliver().outcomes["web_push"] == "skipped:duplicate"
    assert world.web_push == [("member-a", "Water the ferns")]


def test_a_mid_send_transport_error_keeps_the_claim(world):
    world.push_raises = True                                                  # RuntimeError from the transport: may have arrived
    assert _deliver().outcomes["web_push"] == "failed:RuntimeError"
    world.push_raises = False
    assert _deliver().outcomes["web_push"] == "skipped:duplicate" and world.web_push == []


def _real_web_push(monkeypatch) -> list:
    """The REAL routers.push.send_push_to_user; only pywebpush.webpush, the subscription read and the VAPID key are stubbed."""
    import sys
    import types

    import routers.push as push

    monkeypatch.setattr(reminder_delivery, "_send_web_push", _REAL_WEB_PUSH)
    sent = []

    class _Cur:
        async def fetchall(self):
            return [{"endpoint": "https://push.invalid/sub-1", "keys_p256dh": "p", "keys_auth": "a"}]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class _DB:
        def execute(self, sql, params=()):
            assert params == ("member-a",)                                    # the owner's own subscriptions only
            return _Cur()

    async def fake_get_db():
        yield _DB()

    mod = types.ModuleType("pywebpush")
    mod.webpush, mod.WebPushException = (lambda **kw: sent.append(kw)), type("WebPushException", (Exception,), {})
    monkeypatch.setitem(sys.modules, "pywebpush", mod)
    monkeypatch.setattr(push, "get_db", fake_get_db)
    monkeypatch.setattr(push, "_get_vapid_keys", lambda: {"private_key": "k", "public_key": "p"})
    return sent


def _real_telegram(monkeypatch) -> list:
    """The REAL telegram_notify.send; only httpx's client is stubbed."""
    import types

    import httpx

    monkeypatch.setattr(telegram_notify, "send", _REAL_TG_SEND)
    monkeypatch.setattr(telegram_notify, "available", _REAL_TG_AVAILABLE)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "test-token")
    monkeypatch.delenv("ZOE_TELEGRAM_NOTIFY_URL", raising=False)
    posts = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, json=None, headers=None):
            posts.append((url, json, headers))
            return types.SimpleNamespace(status_code=200)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return posts


def test_web_push_through_the_real_send_push_to_user(world, monkeypatch):
    import json

    sent = _real_web_push(monkeypatch)
    res = _deliver(sensitive=True)
    assert res.outcomes["web_push"] == "delivered" and res.web_push_sent == 1
    assert len(sent) == 1 and sent[0]["subscription_info"]["endpoint"] == "https://push.invalid/sub-1"
    assert json.loads(sent[0]["data"]) == {"title": "Zoe", "body": reminder_delivery.NEUTRAL_TEXT, "url": "/"}


def test_telegram_through_the_real_telegram_notify(world, monkeypatch):
    posts = _real_telegram(monkeypatch)
    assert _deliver().outcomes["telegram"] == "delivered"
    assert posts == [(telegram_notify.DEFAULT_NOTIFY_URL, {"telegram_id": "1001", "text": "Water the ferns"},
                      {"Authorization": "Bearer test-token"})]


@pytest.mark.parametrize("visibility, full_text, shows_text", [
    ("personal", False, False),     # the schema default for a member's own reminder: a lock screen must not show it
    ("family", False, True),        # the one household-visible value (routers/reminders.py read filter)
    (None, False, False),
    ("", False, False),
    ("something-new", False, False),
    ("personal", True, True),       # the member chose full text
])
def test_phone_text_is_neutral_unless_the_reminder_is_household_visible(world, monkeypatch, visibility, full_text, shows_text):
    import json

    row = {"title": "Water the ferns", "category": "general", "visibility": visibility}

    class _DB:
        async def execute(self, sql, params=()):
            assert params == ("rem-1",)
            return type("C", (), {"fetchone": staticmethod(lambda: _one(row))})()

    async def _one(r):
        return r

    @contextlib.asynccontextmanager
    async def fake_db():
        yield _DB()

    monkeypatch.setattr(reminder_delivery, "_db", fake_db)
    prefs = {**MEMBERS["member-a"]["prefs"], "reminder_delivery": {"channels": ["telegram", "web_push"], "full_text": full_text}}
    monkeypatch.setitem(MEMBERS["member-a"], "prefs", prefs)
    sent, posts = _real_web_push(monkeypatch), _real_telegram(monkeypatch)
    sensitive = asyncio.run(reminder_delivery.reminder_sensitive("rem-1"))
    assert _deliver(sensitive=sensitive).outcomes == {"web_push": "delivered", "telegram": "delivered"}
    expected = "Water the ferns" if shows_text else reminder_delivery.NEUTRAL_TEXT
    assert json.loads(sent[0]["data"])["body"] == expected
    assert posts[0][1]["text"] == expected


def test_a_household_health_reminder_is_still_neutral(world, monkeypatch):
    row = {"title": "Dentist at nine", "category": "health", "visibility": "family"}

    class _DB:
        async def execute(self, sql, params=()):
            async def one():
                return row
            return type("C", (), {"fetchone": staticmethod(one)})()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield _DB()

    monkeypatch.setattr(reminder_delivery, "_db", fake_db)
    assert asyncio.run(reminder_delivery.reminder_sensitive("rem-1")) is True


def test_shadow_sends_and_stores_nothing(world, monkeypatch):
    monkeypatch.setenv("ZOE_REMINDER_PHONE_DELIVERY", "shadow")
    res = _deliver()
    assert res.outcomes == {"web_push": "would_send", "telegram": "would_send"}
    assert world.telegram == [] and world.web_push == [] and world.ledger == {}


def test_an_erase_in_flight_wins(world):
    async def erase_now(uid):
        async with erase_gate.erasing(uid):
            pass

    world.on_audience = erase_now
    res = _deliver()
    assert set(res.outcomes.values()) == {"skipped:erased"}
    assert world.telegram == [] and world.web_push == [] and world.ledger == {}


def test_forget_user_removes_only_that_members_rows(monkeypatch):
    seen = []

    class _DB:
        async def execute(self, sql, params=()):
            seen.append((sql, params))
            return type("C", (), {"rowcount": 2})()

        async def commit(self):
            pass

    @contextlib.asynccontextmanager
    async def fake_db():
        yield _DB()

    monkeypatch.setattr(reminder_delivery, "_db", fake_db)
    assert asyncio.run(reminder_delivery.forget_user("member_a")) == 2
    sql, params = seen[0]
    assert sql.startswith("DELETE FROM app_settings WHERE key LIKE") and params == ("reminder\\_phone:member\\_a:%",)
    assert reminder_delivery.ledger_key("member_a", "x", "telegram").startswith("reminder_phone:member_a:")


def test_preference_validation_is_strict():
    assert reminder_delivery.validate({"channels": ["telegram", "sms", "telegram"], "full_text": "yes"}) == \
        {"channels": ["telegram"], "full_text": False}
    assert reminder_delivery.validate(None) == {"channels": [], "full_text": False}


# -- the engine wiring --------------------------------------------------------------------------------------------------------
@pytest.fixture
def fire(monkeypatch):
    calls = {"push": 0, "deliver": []}

    class _DB:
        async def execute(self, *a, **k):
            return None

        async def commit(self):
            pass

    @contextlib.asynccontextmanager
    async def fake_db():
        yield _DB()

    async def create_pending(**_):
        return "pend-1"

    async def no_speak(**_):
        return None

    async def send_push(user_id, message, extra=None):
        calls["push"] += 1
        return 1

    async def deliver(*a, **k):
        calls["deliver"].append(k)
        if calls.get("raise"):
            raise RuntimeError("seam broke")
        return reminder_delivery.Result(web_push_sent=1)

    async def not_sensitive(item_id):
        return False

    monkeypatch.setattr(engine, "_get_compat_db", fake_db)
    monkeypatch.setattr(engine, "create_pending", create_pending)
    monkeypatch.setattr(engine, "_maybe_speak_notification", no_speak)
    monkeypatch.setattr(engine, "_send_push", send_push)
    monkeypatch.setattr(engine, "_is_in_quiet_hours", lambda *a: False)
    monkeypatch.setattr(reminder_delivery, "deliver", deliver)
    monkeypatch.setattr(reminder_delivery, "reminder_sensitive", not_sensitive)

    def run(mode, trigger="reminder"):
        monkeypatch.setenv("ZOE_REMINDER_PHONE_DELIVERY", mode)
        asyncio.run(engine.fire_notification(user_id="member-a", message="Water the ferns", trigger_type=trigger,
                                             pending_id="sched-1", item_id="rem-1"))
        return calls

    return run


def test_flag_off_never_calls_the_seam(fire):
    calls = fire("off")
    assert calls["push"] == 1 and calls["deliver"] == []


def test_shadow_keeps_the_engine_push_and_dry_runs_the_seam(fire):
    calls = fire("shadow")
    assert calls["push"] == 1 and [c["dry_run"] for c in calls["deliver"]] == [True]
    assert calls["deliver"][0]["dedupe_id"] == "sched-1"


def test_on_moves_reminder_push_into_the_seam(fire):
    calls = fire("on")
    assert calls["push"] == 0 and [c["dry_run"] for c in calls["deliver"]] == [False]


def test_on_leaves_other_triggers_alone(fire):
    calls = fire("on", trigger="scheduled")
    assert calls["push"] == 1 and calls["deliver"] == []


def test_a_broken_seam_never_reaches_the_scheduler(fire):
    first = fire("on")
    first["raise"] = True
    fire("on")                                       # must not raise
    assert len(first["deliver"]) == 2
