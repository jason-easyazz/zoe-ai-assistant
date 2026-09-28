"""B2.1 brief-on-arrival (``proactive/arrival.py``, flag ``ZOE_PROACTIVE_BRIEF_ON_ARRIVAL``).

A 07:30 morning brief that was created but not heard is spoken ONCE, the first
time its member is present as ``owner`` on a panel between 07:00 and 11:00 local.

Every gate has a test here. Two carry explicit negative controls, because a
silent fixture would pass a "nothing happened" test vacuously:

* once per day — ``test_negative_control_without_the_unique_claim_it_speaks_twice``
  drops the UNIQUE claim and the same two-arrival sequence speaks twice; the
  real test resets the in-process throttle between arrivals, so only the DB
  claim can be what stops the second one;
* tier — ``test_only_owner_tier_speaks`` runs the identical fixture for
  ``owner`` (speaks) and ``bound_guest`` / ``absent`` (silent).

The fake DB models the SQL in Python; the SQL itself was run against the live
Postgres (read-only SELECTs, a rolled-back TEMP table for the claim/upsert).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import asyncio
import contextlib
import copy
import json
import inspect
import io
import logging
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path

import proactive.arrival as arrival
import proactive.engine as engine
import proactive.presence as presence_mod
import ui_orchestrator
import voice_announce

PERTH = zoneinfo.ZoneInfo("Australia/Perth")
USER = "jason"
PANEL = "panel-kitchen"
BRIEF = "Good morning Jason! It's Monday, September 28. You have dentist at 10:00 today."
TEASER = "Good morning Jason — your brief is ready when you are."


def local(h, m=0, s=0, day=28):
    return datetime(2026, 9, day, h, m, s, tzinfo=PERTH).astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


# --------------------------------------------------------------------------- #
# Fake DB: the tables arrival.py reads/writes, with the SQL modelled in Python.
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *_):
        return False


class FakeDB:
    def __init__(self):
        self.pending = []        # proactive_pending
        self.announcements = []  # voice_announcements
        self.turns = []          # (user_id, created_at) user turns in chat_messages
        self.responses = []      # proactive_responses
        self.device_panels = {PANEL, "panel-bedroom"}  # panels holding a live device token
        self.enforce_unique = True
        self.fail_claim_link = False
        self.ops = []

    @contextlib.asynccontextmanager
    async def transaction(self):
        saved = (copy.deepcopy(self.announcements), copy.deepcopy(self.responses))
        try:
            yield self
        except BaseException:
            self.announcements, self.responses = saved
            raise

    def execute(self, sql, params=()):
        return _Exec(lambda: self._do(" ".join(sql.split()), tuple(params)))

    async def commit(self):
        pass

    async def _do(self, sql, p):
        self.ops.append(sql)
        if sql.startswith("SELECT id, message, claimed, trigger_context FROM proactive_pending"):
            user, trig, start = p
            rows = [r for r in self.pending
                    if r["user_id"] == user and r["trigger_type"] == trig
                    and ts(r["created_at"]) >= ts(start)]
            return _Cursor(sorted(rows, key=lambda r: ts(r["created_at"]), reverse=True))
        if sql.startswith("SELECT message, delivered_at, expired, expires_at FROM voice_announcements"):
            user, trig, start = p
            return _Cursor([r for r in self.announcements
                            if r["user_id"] == user and r["trigger_type"] == trig
                            and ts(r["created_at"]) >= ts(start)])
        if sql.startswith("SELECT 1 FROM device_tokens"):
            (panel,) = p
            return _Cursor([{"?column?": 1}] if panel in self.device_panels else [])
        if "FROM chat_messages" in sql:
            start, end, user = p
            hits = [t for u, t in self.turns if u == user and ts(start) <= ts(t) <= ts(end)]
            return _Cursor([{"first_turn": min(hits) if hits else None}])
        if sql.startswith("INSERT INTO proactive_responses"):
            (rid, user, key, trig, day, panel, pend, missed, window, created) = p
            if self.enforce_unique and any(
                r["user_id"] == user and r["claim_key"] == key and r["local_date"] == day
                for r in self.responses
            ):
                return _Cursor([])
            self.responses.append(dict(
                id=rid, user_id=user, claim_key=key, trigger_type=trig, local_date=day,
                panel_id=panel,
                pending_id=pend, missed=missed, response_window_s=window, created_at=created,
                announcement_id=None, spoken_at=None, outcome=None, responded=None,
                responded_at=None, evaluated_at=None,
            ))
            return _Cursor([{"id": rid}])
        if sql.startswith("UPDATE proactive_responses SET announcement_id"):
            if self.fail_claim_link:
                raise RuntimeError("claim link failed")
            ann, spoken, rid = p
            for r in self.responses:
                if r["id"] == rid:
                    r.update(announcement_id=ann, spoken_at=spoken)
            return _Cursor([])
        if "FROM proactive_responses r" in sql:
            (key,) = p
            out = []
            for r in self.responses:
                if r["claim_key"] != key or r["evaluated_at"] is not None:
                    continue
                va = next((a for a in self.announcements if a["id"] == r["announcement_id"]), {})
                out.append({**r, "delivered_at": va.get("delivered_at"),
                            "expired": va.get("expired"), "expires_at": va.get("expires_at")})
            return _Cursor(out)
        if sql.startswith("UPDATE proactive_responses SET outcome"):
            outcome, responded, responded_at, evaluated_at, rid = p
            for r in self.responses:
                if r["id"] == rid and r["evaluated_at"] is None:
                    r.update(outcome=outcome, responded=responded,
                             responded_at=responded_at, evaluated_at=evaluated_at)
            return _Cursor([])
        raise AssertionError(f"unexpected SQL in brief-on-arrival: {sql[:120]}")


@pytest.fixture
def h(monkeypatch):
    db = FakeDB()
    state = {"now": local(8, 15), "tier": "owner", "panel": PANEL,
             "panel_enqueues": [], "daemon_enqueues": []}

    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield db

    monkeypatch.setenv("ZOE_PROACTIVE_BRIEF_ON_ARRIVAL", "1")
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "1")
    monkeypatch.delenv("ZOE_PROACTIVE_ARRIVAL_RESPONSE_S", raising=False)
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    monkeypatch.setattr(arrival, "_get_compat_db", fake_compat_db)
    monkeypatch.setattr(engine, "_get_compat_db", fake_compat_db)
    monkeypatch.setattr(arrival, "_now_utc", lambda: state["now"])
    monkeypatch.setattr(arrival, "_ZOE_TZ", PERTH)
    monkeypatch.setattr(engine, "_ZOE_TZ", PERTH)
    monkeypatch.setattr(engine, "_QUIET_START", 22)
    monkeypatch.setattr(engine, "_QUIET_END", 7)

    async def fake_tier(user_id, within_s=None):
        if state["tier"] == "absent":
            return "absent", None
        return state["tier"], state["panel"]

    monkeypatch.setattr(presence_mod, "panel_presence_tier", fake_tier)

    async def fake_panel_enqueue(_db, **kw):
        state["panel_enqueues"].append(kw)
        return {"action_id": "a1"}

    monkeypatch.setattr(ui_orchestrator, "enqueue_ui_action", fake_panel_enqueue)

    async def fake_daemon_enqueue(_db, **kw):
        ann_id = f"ann-{len(state['daemon_enqueues']) + 1}"
        state["daemon_enqueues"].append(kw)
        db.announcements.append(dict(
            id=ann_id, user_id=kw["user_id"], trigger_type=kw["trigger_type"],
            message=kw["message"], created_at=iso(state["now"]),
            expires_at=iso(state["now"] + timedelta(seconds=120)),
            delivered_at=None, expired=0,
        ))
        return ann_id

    monkeypatch.setattr(voice_announce, "enqueue_announcement", fake_daemon_enqueue)

    # Today's 07:30 brief: created, unclaimed, never queued for the speaker.
    db.pending.append(pending_row("pend-1", local(7, 30)))
    arrival._reset_state()
    yield db, state
    arrival._reset_state()


def pending_row(pid, created, message=BRIEF, claimed=0):
    return dict(id=pid, user_id=USER, trigger_type="morning_checkin", message=message,
                claimed=claimed, created_at=iso(created),
                trigger_context=json.dumps({"spoken_guest_safe": TEASER}))


def spoken(state):
    return [e for e in state["daemon_enqueues"] if e["trigger_type"] == arrival.TRIGGER_TYPE]


# --------------------------------------------------------------------------- #
# Fires once, on the first owner presence in the window
# --------------------------------------------------------------------------- #
async def test_fires_on_first_owner_presence_in_window(h, caplog):
    db, state = h
    caplog.set_level(logging.INFO, logger="proactive.arrival")
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    [ann] = spoken(state)
    assert ann["message"] == BRIEF and ann["panel_id"] == PANEL and ann["user_id"] == USER
    assert [e["panel_id"] for e in state["panel_enqueues"]] == [PANEL]
    [claim] = db.responses
    assert claim["local_date"] == "2026-09-28" and claim["missed"] == "absent"
    assert claim["announcement_id"] == "ann-1" and claim["pending_id"] == "pend-1"
    line = next(r.getMessage() for r in caplog.records if "PROACTIVE_SPOKEN" in r.getMessage())
    for field in ("trigger=morning_checkin_arrival", f"user={USER}", f"panel={PANEL}",
                  "outcome=enqueued", "daemon_queue=queued", "tier=owner", "missed=absent"):
        assert field in line, line


@pytest.mark.parametrize("hour,minute,expected", [
    (6, 59, "outside_window"),
    (7, 0, "spoken"),
    (10, 59, "spoken"),
    (11, 0, "outside_window"),
    (14, 0, "outside_window"),
])
async def test_window_is_0700_to_1100_local(h, hour, minute, expected):
    db, state = h
    state["now"] = local(hour, minute)
    if hour < 7 or (hour == 7 and minute < 30):
        db.pending[0]["created_at"] = iso(local(0, 1))  # a brief exists "today" either way
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == expected
    assert len(spoken(state)) == (1 if expected == "spoken" else 0)


async def test_not_twice_same_day_even_after_restart_or_on_another_panel(h):
    db, state = h
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    arrival._reset_state()  # a restart / second worker: only the DB claim remains
    state["now"] = local(9, 40)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "done:already_fired"
    arrival._reset_state()
    state["panel"] = "panel-bedroom"
    assert await arrival.maybe_speak_brief_on_arrival(USER, "panel-bedroom") == "done:already_fired"
    assert len(spoken(state)) == 1
    assert len(db.responses) == 1


async def test_negative_control_without_the_unique_claim_it_speaks_twice(h):
    """Same sequence as the test above, claim constraint removed -> two briefs.
    Proves the once-per-day test is guarded by the claim, not by the fixture."""
    db, state = h
    db.enforce_unique = False
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    arrival._reset_state()
    state["now"] = local(9, 40)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    assert len(spoken(state)) == 2


async def test_next_day_is_a_fresh_day(h):
    db, state = h
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    arrival._reset_state()
    state["now"] = local(8, 15, day=29)
    db.pending.append(pending_row("pend-2", local(7, 30, day=29)))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    assert [r["local_date"] for r in db.responses] == ["2026-09-28", "2026-09-29"]


# --------------------------------------------------------------------------- #
# Tier and panel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tier,expected", [
    ("owner", "spoken"),               # the control: identical fixture speaks
    ("bound_guest", "tier_bound_guest"),
    ("absent", "tier_absent"),
])
async def test_only_owner_tier_speaks(h, tier, expected):
    db, state = h
    state["tier"] = tier
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == expected
    assert len(spoken(state)) == (1 if tier == "owner" else 0)
    assert len(db.responses) == (1 if tier == "owner" else 0)


async def test_only_on_the_panel_where_presence_was_seen(h):
    db, state = h
    state["panel"] = "panel-bedroom"  # freshest owner row is elsewhere
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "other_panel"
    assert spoken(state) == [] and db.responses == []


@pytest.mark.parametrize("uid", ["guest", "voice-guest", "anonymous", "",
                                 "test-route-probe", "demo_b08_x", "probe-1"])
async def test_never_for_synthetic_or_guest_ids(h, uid):
    db, state = h
    assert await arrival.maybe_speak_brief_on_arrival(uid, PANEL) == "synthetic"
    assert db.ops == [] and spoken(state) == []


# --------------------------------------------------------------------------- #
# Already heard / opened / in flight / quiet hours / talking
# --------------------------------------------------------------------------- #
async def test_skips_when_the_full_brief_was_already_delivered(h):
    db, state = h
    db.announcements.append(dict(id="va-0730", user_id=USER, trigger_type="morning_checkin",
                                 message=BRIEF, created_at=iso(local(7, 30)),
                                 expires_at=iso(local(7, 32)), delivered_at=iso(local(7, 30, 5)),
                                 expired=0))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "done:already_heard"
    assert spoken(state) == [] and db.responses == []


async def test_a_delivered_guest_teaser_does_not_count_as_heard(h):
    db, state = h
    db.announcements.append(dict(id="va-0730", user_id=USER, trigger_type="morning_checkin",
                                 message=TEASER, created_at=iso(local(7, 30)),
                                 expires_at=iso(local(7, 32)), delivered_at=iso(local(7, 30, 5)),
                                 expired=0))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    assert db.responses[0]["missed"] == "guest_teaser"


async def test_an_expired_undelivered_brief_is_spoken(h):
    db, state = h
    db.announcements.append(dict(id="va-0730", user_id=USER, trigger_type="morning_checkin",
                                 message=BRIEF, created_at=iso(local(7, 30)),
                                 expires_at=iso(local(7, 32)), delivered_at=None, expired=1))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    assert db.responses[0]["missed"] == "expired"


async def test_waits_while_the_0730_brief_is_still_queued(h):
    db, state = h
    state["now"] = local(7, 30, 30)
    db.announcements.append(dict(id="va-0730", user_id=USER, trigger_type="morning_checkin",
                                 message=BRIEF, created_at=iso(local(7, 30)),
                                 expires_at=iso(local(7, 32)), delivered_at=None, expired=0))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "in_flight"
    assert spoken(state) == [] and db.responses == []


async def test_skips_when_opened_in_chat(h):
    db, state = h
    db.pending[0]["claimed"] = 1
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "done:opened_in_chat"
    assert spoken(state) == []


async def test_no_brief_created_yet_is_not_final(h):
    db, state = h
    db.pending.clear()
    state["now"] = local(7, 10)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "no_brief"
    assert USER not in arrival._done_for_day  # re-checked on a later sync


async def test_never_in_quiet_hours(h, monkeypatch):
    db, state = h
    monkeypatch.setattr(engine, "_QUIET_END", 9)  # household sleeps in until 09:00
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "quiet_hours"
    assert spoken(state) == [] and db.ops == []
    state["now"] = local(9, 5)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"


async def test_does_not_talk_over_a_recent_turn(h):
    db, state = h
    db.turns.append((USER, iso(state["now"] - timedelta(seconds=30))))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "recent_turn"
    assert spoken(state) == [] and db.responses == []
    state["now"] += timedelta(minutes=3)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"


# --------------------------------------------------------------------------- #
# Response signal (B2.2)
# --------------------------------------------------------------------------- #
async def _speak_and_play(db, state, played_after_s=5):
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    played = state["now"] + timedelta(seconds=played_after_s)
    ann = next(a for a in db.announcements if a["trigger_type"] == arrival.TRIGGER_TYPE)
    ann["delivered_at"] = iso(played)
    return played


async def test_response_recorded_when_the_member_replies_in_the_window(h):
    db, state = h
    played = await _speak_and_play(db, state)
    db.turns.append((USER, iso(played + timedelta(seconds=40))))
    state["now"] = played + timedelta(seconds=60)
    assert await arrival.evaluate_pending_responses() == 0  # window still open
    state["now"] = played + timedelta(seconds=121)
    assert await arrival.evaluate_pending_responses() == 1
    [r] = db.responses
    assert (r["outcome"], r["responded"]) == ("accepted", 1)
    assert r["responded_at"] == iso(played + timedelta(seconds=40))
    assert await arrival.evaluate_pending_responses() == 0  # evaluated once


async def test_response_ignored_when_the_reply_is_late_or_absent(h):
    db, state = h
    played = await _speak_and_play(db, state)
    db.turns.append((USER, iso(played + timedelta(seconds=300))))  # after the window
    db.turns.append(("someone-else", iso(played + timedelta(seconds=10))))
    state["now"] = played + timedelta(seconds=400)
    assert await arrival.evaluate_pending_responses() == 1
    assert (db.responses[0]["outcome"], db.responses[0]["responded"]) == ("ignored", 0)


async def test_response_undelivered_when_the_daemon_never_played_it(h):
    db, state = h
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    state["now"] += timedelta(seconds=60)
    assert await arrival.evaluate_pending_responses() == 0  # still queued
    state["now"] += timedelta(seconds=120)  # past the 120 s announcement TTL
    assert await arrival.evaluate_pending_responses() == 1
    assert (db.responses[0]["outcome"], db.responses[0]["responded"]) == ("undelivered", None)


async def test_response_window_is_tunable(h, monkeypatch):
    db, state = h
    monkeypatch.setenv("ZOE_PROACTIVE_ARRIVAL_RESPONSE_S", "600")
    played = await _speak_and_play(db, state)
    db.turns.append((USER, iso(played + timedelta(seconds=300))))
    state["now"] = played + timedelta(seconds=601)
    assert await arrival.evaluate_pending_responses() == 1
    assert db.responses[0]["outcome"] == "accepted"


# --------------------------------------------------------------------------- #
# Greptile #1749 fixes: panel scoping, scheduled-path coordination, claim link,
# all of today's rows
# --------------------------------------------------------------------------- #
async def test_the_arrival_row_is_claimable_only_by_its_own_panel(monkeypatch):
    """A member's full brief queued for the kitchen is never played by another
    panel's daemon — in the default claim-any mode too — while an ordinary row
    keeps the alias-tolerant claim-any behaviour."""
    import aiosqlite

    monkeypatch.delenv("ZOE_ANNOUNCE_STRICT_PANEL", raising=False)
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute(
            """CREATE TABLE voice_announcements (
                   id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT,
                   message TEXT NOT NULL, trigger_type TEXT DEFAULT '',
                   created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                   delivered_at TEXT, delivered_to TEXT,
                   expired INTEGER NOT NULL DEFAULT 0)"""
        )
        await voice_announce.enqueue_announcement(
            conn, user_id=USER, message=BRIEF, panel_id=PANEL,
            trigger_type=arrival.TRIGGER_TYPE,
        )
        assert await voice_announce.claim_announcements(conn, panel_id="panel-bedroom") == []
        [got] = await voice_announce.claim_announcements(conn, panel_id=PANEL)
        assert got["trigger_type"] == arrival.TRIGGER_TYPE and got["text"] == BRIEF
        # Control: an unscoped row is still claimable across panel aliases.
        await voice_announce.enqueue_announcement(
            conn, user_id=USER, message="toast", panel_id="panel_a1b2", trigger_type="x",
        )
        assert len(await voice_announce.claim_announcements(conn, panel_id="panel-bedroom")) == 1
    finally:
        await conn.close()


async def test_refuses_to_queue_for_a_panel_no_daemon_can_claim(h, caplog):
    db, state = h
    caplog.set_level(logging.INFO, logger="proactive.arrival")
    db.device_panels = {"zoe-touch-pi"}  # PANEL is a browser alias with no device token
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "unscoped_panel"
    assert state["daemon_enqueues"] == [] and state["panel_enqueues"] == []
    assert db.responses == [] and USER not in arrival._done_for_day
    assert any("outcome=unscoped_panel" in r.getMessage() for r in caplog.records)


async def _scheduled_speak():
    await engine._maybe_speak_notification(
        user_id=USER, message=BRIEF, trigger_type="morning_checkin",
        guest_safe_message=TEASER, pending_id="pend-1",
    )


def full_briefs(state):
    return [e for e in state["daemon_enqueues"] if e["message"] == BRIEF]


async def test_arrival_then_scheduled_speaks_the_brief_exactly_once(h):
    """The race: the 07:30 brief row exists, arrival runs before the scheduled
    path reaches its speaker lane (07:29:59 -> 07:30)."""
    db, state = h
    state["now"] = local(7, 30, 0)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    await _scheduled_speak()
    assert len(full_briefs(state)) == 1
    [claim] = db.responses
    assert claim["trigger_type"] == arrival.TRIGGER_TYPE


async def test_scheduled_then_arrival_speaks_the_brief_exactly_once(h):
    db, state = h
    state["now"] = local(7, 30, 0)
    await _scheduled_speak()
    [claim] = db.responses
    assert claim["trigger_type"] == "morning_checkin" and claim["announcement_id"] == "ann-1"
    db.announcements[0]["expired"] = 1  # even if it never played, the claim holds
    state["now"] = local(8, 0)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "done:already_fired"
    assert len(full_briefs(state)) == 1


async def test_negative_control_uncoordinated_paths_speak_twice(h):
    """Without the shared claim (UNIQUE off) the same arrival-then-scheduled
    sequence queues the brief twice — the coordination test is not vacuous."""
    db, state = h
    db.enforce_unique = False
    state["now"] = local(7, 30, 0)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    await _scheduled_speak()
    assert len(full_briefs(state)) == 2


async def test_scheduled_path_is_unchanged_with_arrival_off(h, monkeypatch):
    db, state = h
    monkeypatch.setenv("ZOE_PROACTIVE_BRIEF_ON_ARRIVAL", "")
    await _scheduled_speak()
    await _scheduled_speak()
    assert len(full_briefs(state)) == 2  # pre-existing behaviour: no claim taken
    assert db.responses == []
    assert not any("proactive_responses" in op for op in db.ops)


async def test_claim_link_failure_is_never_recorded_as_undelivered(h):
    """Announcement insert + claim link are one transaction: a failed link
    queues nothing, and the sweep records ``unknown`` — never ``undelivered``."""
    db, state = h
    db.fail_claim_link = True
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "spoken"
    assert not any(a["trigger_type"] == arrival.TRIGGER_TYPE for a in db.announcements)
    [claim] = db.responses
    assert claim["announcement_id"] is None
    state["now"] += timedelta(seconds=121)
    assert await arrival.evaluate_pending_responses() == 1
    assert claim["outcome"] == "unknown" and claim["responded"] is None


async def test_a_delivered_full_brief_counts_even_after_a_later_brief(h):
    """All of today's rows are considered: a later manual brief with different
    text cannot hide the earlier delivered one."""
    db, state = h
    db.announcements.append(dict(id="va-0730", user_id=USER, trigger_type="morning_checkin",
                                 message=BRIEF, created_at=iso(local(7, 30)),
                                 expires_at=iso(local(7, 32)), delivered_at=iso(local(7, 30, 5)),
                                 expired=0))
    db.pending.append(pending_row("pend-manual", local(7, 50),
                                  message="Good morning Jason! Here's your manual brief."))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "done:already_heard"
    assert spoken(state) == []


async def test_an_unrecognised_delivered_text_counts_as_heard(h):
    db, state = h
    db.announcements.append(dict(id="va-x", user_id=USER, trigger_type="morning_checkin",
                                 message="some other morning text", created_at=iso(local(7, 31)),
                                 expires_at=iso(local(7, 33)), delivered_at=iso(local(7, 31, 5)),
                                 expired=0))
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "done:already_heard"


# --------------------------------------------------------------------------- #
# Flag off -> nothing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("arrival_flag,spoken_flag", [("", "1"), ("1", ""), ("0", "0")])
async def test_flag_off_is_a_true_noop(h, monkeypatch, arrival_flag, spoken_flag):
    db, state = h
    monkeypatch.setenv("ZOE_PROACTIVE_BRIEF_ON_ARRIVAL", arrival_flag)
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", spoken_flag)
    assert await arrival.maybe_speak_brief_on_arrival(USER, PANEL) == "disabled"
    arrival.schedule_on_owner_presence(USER, PANEL)
    assert not arrival._tasks and not arrival._inflight
    db.responses.append(dict(id="r0", user_id=USER, claim_key=arrival.CLAIM_KEY,
                             trigger_type=arrival.TRIGGER_TYPE,
                             local_date="2026-09-28", evaluated_at=None, announcement_id=None,
                             response_window_s=120, created_at=iso(local(7, 0))))
    assert await arrival.evaluate_pending_responses() == 0
    assert db.ops == [] and spoken(state) == [] and state["panel_enqueues"] == []


# --------------------------------------------------------------------------- #
# The hook
# --------------------------------------------------------------------------- #
async def test_schedule_runs_in_background_and_throttles(h):
    db, state = h
    arrival.schedule_on_owner_presence(USER, PANEL)
    arrival.schedule_on_owner_presence(USER, PANEL)  # the kiosk's next 5 s sync
    assert len(arrival._tasks) == 1
    await asyncio.gather(*list(arrival._tasks))
    assert len(spoken(state)) == 1
    assert arrival._done_for_day[USER] == "2026-09-28"
    arrival._last_check.clear()
    arrival.schedule_on_owner_presence(USER, PANEL)  # done for today: no task
    assert not arrival._tasks


def test_schedule_gates_cheaply_before_any_task(h):
    _, state = h
    state["now"] = local(12, 0)
    arrival.schedule_on_owner_presence(USER, PANEL)  # outside window
    arrival.schedule_on_owner_presence("guest", PANEL)
    assert not arrival._tasks and not arrival._last_check


def test_router_hook_fires_only_for_a_members_own_foreground_session(monkeypatch):
    import routers.ui_actions as ui_actions

    calls = []
    monkeypatch.setattr(arrival, "schedule_on_owner_presence",
                        lambda uid, pid: calls.append((uid, pid)))
    ui_actions._note_owner_presence({"user_id": USER, "role": "admin"}, PANEL, 1)
    ui_actions._note_owner_presence({"user_id": "guest", "role": "guest"}, PANEL, 1)
    ui_actions._note_owner_presence({"user_id": USER, "panel_id": PANEL}, PANEL, 1)  # device token
    ui_actions._note_owner_presence({"user_id": USER, "role": "admin"}, PANEL, 0)  # background tab
    assert calls == [(USER, PANEL)]

    def boom(*_a):
        raise RuntimeError("proactive module broken")

    monkeypatch.setattr(arrival, "schedule_on_owner_presence", boom)
    ui_actions._note_owner_presence({"user_id": USER}, PANEL, 1)  # never raises

    for endpoint in (ui_actions.bind_panel, ui_actions.sync_ui_state):
        assert "_note_owner_presence(user, panel_id, is_foreground)" in inspect.getsource(endpoint)


# --------------------------------------------------------------------------- #
# Migration 0030
# --------------------------------------------------------------------------- #
def _render(monkeypatch, fn, rev: str) -> str:
    from alembic.config import Config

    svc = Path(__file__).resolve().parents[1]
    url = "postgresql+psycopg2://u:p@localhost/db"
    monkeypatch.setenv("POSTGRES_URL", url)
    buf = io.StringIO()
    cfg = Config(output_buffer=buf, stdout=buf)
    cfg.set_main_option("script_location", str(svc / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    fn(cfg, rev, sql=True)
    return buf.getvalue()


def test_migration_0030_creates_the_claim_table_if_missing(monkeypatch):
    from alembic import command

    up = " ".join(_render(monkeypatch, command.upgrade, "0029:0030").split())
    assert "CREATE TABLE IF NOT EXISTS proactive_responses" in up
    assert "claim_key TEXT NOT NULL" in up
    assert "UNIQUE (user_id, claim_key, local_date)" in up
    down = _render(monkeypatch, command.downgrade, "0030:0029")
    assert "DROP TABLE IF EXISTS proactive_responses" in down
