"""intercom: the native LAN PA / intercom (ZOE_INTERCOM, flag-dark) and the owner's call-permission rules (2026-10-18).

The house has ONE panel today, so every multi-panel behaviour is proven here against SIMULATED panels: a living-room panel (the
origin), a kitchen panel and a hall panel that hold live device tokens, a garage panel with NO device token (no speaker daemon) and
an office panel whose token is revoked. Nothing here touches the real panel or plays audio.

Falsifiable pins (each was broken once to watch it fail):
  * drop the intercom triggers from voice_announce.PANEL_SCOPED_TRIGGERS -> the living-room daemon claims the kitchen's row ->
    test_a_named_panel_hears_it_and_no_other_panel_can_claim_it goes red;
  * drop the guest check in decide() -> test_guests_can_never_use_the_intercom goes red;
  * drop the adults-only drop-in rule -> test_only_adults_drop_in_everyone_else_rings goes red;
  * drop the quiet-hours check -> test_quiet_hours_hold goes red.
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import aiosqlite
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db_compat  # noqa: E402
import intercom as ic  # noqa: E402
import voice_announce  # noqa: E402

pytestmark = pytest.mark.ci_safe

LIVING, KITCHEN, HALL, GARAGE, OFFICE = "zoe-panel-living", "zoe-panel-kitchen", "zoe-panel-hall", "zoe-panel-garage", "zoe-panel-office"
ADULT, KID = "ann-0001", "kid-0002"
LABELS = ["kitchen", "living room", "hall", "hallway"]

_SCHEMA = [
    """CREATE TABLE panels (panel_id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT, is_active INTEGER DEFAULT 1,
       room_id TEXT)""",
    "CREATE TABLE rooms (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE)",
    "CREATE TABLE device_tokens (panel_id TEXT, revoked INTEGER DEFAULT 0)",
    """CREATE TABLE voice_announcements (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT, message TEXT NOT NULL,
       trigger_type TEXT DEFAULT '', created_at TEXT NOT NULL, expires_at TEXT NOT NULL, delivered_at TEXT, delivered_to TEXT,
       expired INTEGER NOT NULL DEFAULT 0, played_at TEXT)""",
]
_ROOMS = [("r1", "Living Room", "living-room"), ("r2", "Kitchen", "kitchen"), ("r3", "Garage", "garage"), ("r4", "Office", "office")]
_PANELS = [
    (LIVING, "Zoe Living", None, 1, "r1"),
    (KITCHEN, "Kitchen Panel", None, 1, "r2"),
    (HALL, "Zoe Hall", "Hallway", 1, None),  # no room: answers to its location
    (GARAGE, "Zoe Garage", None, 1, "r3"),   # no device token: no daemon to speak
    (OFFICE, "Zoe Office", None, 1, "r4"),   # revoked token
]
_TOKENS = [(LIVING, 0), (KITCHEN, 0), (HALL, 0), (OFFICE, 1)]


@pytest.fixture
def house(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")

    with sqlite3.connect(path) as conn:
        for ddl in _SCHEMA:
            conn.execute(ddl)
        conn.executemany("INSERT INTO rooms VALUES (?, ?, ?)", _ROOMS)
        conn.executemany("INSERT INTO panels VALUES (?, ?, ?, ?, ?)", _PANELS)
        conn.executemany("INSERT INTO device_tokens VALUES (?, ?)", _TOKENS)

    @contextlib.asynccontextmanager
    async def fake_db():
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    async def name(member):
        return {ADULT: "Ann", KID: "Kai"}.get(member, "")

    async def adult(member):
        return member == ADULT

    state = {"quiet": False, "path": path}
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(ic, "_sender_name", name)
    monkeypatch.setattr(ic, "_is_adult", adult)
    monkeypatch.setattr(ic, "_quiet_now", lambda now=None: state["quiet"])
    monkeypatch.setenv("ZOE_INTERCOM", "on")
    monkeypatch.delenv("ZOE_ANNOUNCE_STRICT_PANEL", raising=False)
    state["db"] = fake_db
    return state


async def _rows(house):
    async with house["db"]() as db:
        async with db.execute("SELECT panel_id, user_id, message, trigger_type FROM voice_announcements ORDER BY panel_id") as cur:
            return [tuple(r) for r in await cur.fetchall()]


def _ctx(member, panel=LIVING):
    return {"scope_user": member, "panel_id": panel}


async def _say(text, member=ADULT, panel=LIVING, **kw):
    return await ic.tier(text, member or "guest", "s1", channel="voice", extra_ctx=_ctx(member, panel), **kw)


# ── parsing ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,kind,target,message", [
    ("tell the kitchen dinner's ready", "announce", "kitchen", "dinner's ready"),
    ("Hey Zoe, tell the kitchen that dinner's ready.", "announce", "kitchen", "dinner's ready."),
    ("tell the kitchen panel to come and eat", "announce", "kitchen", "come and eat"),
    ("let the living room know the movie's starting", "announce", "living room", "the movie's starting"),
    ("tell everyone dinner's ready", "announce", "", "dinner's ready"),
    ("tell the whole house we're leaving in five", "announce", "", "we're leaving in five"),
    ("announce: dinner's ready", "announce", "", "dinner's ready"),
    ("announce dinner's ready", "announce", "", "dinner's ready"),
    ("make an announcement, shoes on everyone", "announce", "", "shoes on everyone"),
    ("announce to the kitchen that the oven's on", "announce", "kitchen", "the oven's on"),
    ("call the kitchen", "ring", "kitchen", ""),
    ("ring the hallway please", "ring", "hallway", ""),
    ("can you call the living room panel?", "ring", "living room", ""),
    ("drop in on the kitchen", "drop_in", "kitchen", ""),
])
def test_intercom_phrasings_are_recognised(text, kind, target, message):
    req = ic.parse(text, LABELS)
    assert req is not None, text
    assert (req.kind, req.target, req.message) == (kind, target, message)


@pytest.mark.parametrize("text", [
    "tell me a joke",
    "tell me about the kitchen",
    "tell the kids dinner's ready",       # not a panel: Matrix / messaging territory, the turn falls through
    "call mum",
    "call everyone",
    "call the kitchen about the oven",
    "ring the garage",                     # not a speaker panel
    "let the kitchen light stay on",
    "turn on the kitchen light",
    "what's the announcement about",
    "the announcement was loud",
    "tell the kitchen",                    # nothing to say
    "drop in on everyone",
    "",
])
def test_look_alikes_are_left_alone(text):
    assert ic.parse(text, LABELS) is None


def test_the_longest_label_wins():
    req = ic.parse("tell the living room dinner's ready", ["living", "living room"])
    assert (req.target, req.message) == ("living room", "dinner's ready")


# ── the policy (pure) ───────────────────────────────────────────────────────────────────────────────────────────────────

IDX = {"kitchen": [KITCHEN], "living room": [LIVING], "hallway": [HALL]}
ALL = [LIVING, KITCHEN, HALL]


def _decide(req, caller=ADULT, adult=True, origin=LIVING, quiet=False, all_panels=ALL):
    return ic.decide(req, caller=caller, caller_is_adult=adult, origin_panel=origin, panels_by_label=IDX,
                     all_panels=all_panels, quiet=quiet)


def test_guests_can_never_use_the_intercom():
    for req in (ic.Request("announce", "kitchen", "hi"), ic.Request("announce", "", "hi"), ic.Request("ring", "kitchen"),
                ic.Request("drop_in", "kitchen")):
        d = _decide(req, caller=None, adult=False)
        assert (d.allowed, d.reason, d.targets) == (False, "guest", ())


def test_quiet_hours_hold():
    for req in (ic.Request("announce", "", "hi"), ic.Request("ring", "kitchen"), ic.Request("drop_in", "kitchen")):
        d = _decide(req, quiet=True)
        assert (d.allowed, d.reason) == (False, "quiet_hours")


def test_only_adults_drop_in_everyone_else_rings():
    assert _decide(ic.Request("drop_in", "kitchen")) == ic.Decision(True, "drop_in", "ok", (KITCHEN,))
    kid = _decide(ic.Request("drop_in", "kitchen"), caller=KID, adult=False)
    assert kid == ic.Decision(True, "ring", "drop_in_adults_only", (KITCHEN,))
    # everyone rings first, kids included
    assert _decide(ic.Request("ring", "kitchen"), caller=KID, adult=False) == ic.Decision(True, "ring", "ok", (KITCHEN,))


def test_never_to_the_panel_you_are_at():
    d = _decide(ic.Request("ring", "living room"))
    assert (d.allowed, d.reason) == (False, "only_here")
    d = _decide(ic.Request("announce", "", "hi"))
    assert d.targets == (KITCHEN, HALL)
    d = _decide(ic.Request("announce", "", "hi"), all_panels=[LIVING])  # today's house: one panel
    assert (d.allowed, d.reason) == (False, "no_target")


# ── end to end against the simulated house ──────────────────────────────────────────────────────────────────────────────

async def test_a_named_panel_hears_it_and_no_other_panel_can_claim_it(house):
    res = await _say("tell the kitchen dinner's ready")
    assert res is not None and res.tier == "intercom" and res.reply == "Okay, I've told the kitchen."
    assert await _rows(house) == [(KITCHEN, ADULT, "Announcement from Ann: dinner's ready", "intercom_announce")]
    async with house["db"]() as db:
        # the origin's own daemon and the hall's daemon poll first - neither may take the kitchen's row (default claim-any mode)
        assert await voice_announce.claim_announcements(db, panel_id=LIVING) == []
        assert await voice_announce.claim_announcements(db, panel_id=HALL) == []
        got = await voice_announce.claim_announcements(db, panel_id=KITCHEN)
    assert [g["text"] for g in got] == ["Announcement from Ann: dinner's ready"]


async def test_announce_reaches_every_other_speaker_panel_once(house):
    res = await _say("announce: shoes on, we're leaving")
    assert res.reply == "Okay, I've announced it on 2 panels."
    rows = await _rows(house)
    assert [r[0] for r in rows] == [HALL, KITCHEN]  # not the origin, not the token-less garage, not the revoked office
    async with house["db"]() as db:
        assert len(await voice_announce.claim_announcements(db, panel_id=KITCHEN)) == 1
        assert len(await voice_announce.claim_announcements(db, panel_id=HALL)) == 1
        assert await voice_announce.claim_announcements(db, panel_id=KITCHEN) == []


async def test_a_call_rings_and_says_the_audio_leg_is_not_built(house):
    res = await _say("call the kitchen")
    assert res.reply.startswith("I've rung the kitchen") and "aren't set up yet" in res.reply
    assert await _rows(house) == [(KITCHEN, ADULT, "Ann would like to talk to you, from the living room panel.", "intercom_ring")]


async def test_a_kids_drop_in_becomes_a_ring(house):
    res = await _say("drop in on the kitchen", member=KID)
    assert res.reply.startswith("Only adults can drop in")
    assert [r[3] for r in await _rows(house)] == ["intercom_ring"]


async def test_a_guest_turn_queues_nothing(house):
    res = await _say("tell the kitchen dinner's ready", member=None)
    assert res.reply.startswith("Only signed-in members")
    assert await _rows(house) == []


async def test_quiet_hours_queue_nothing(house):
    house["quiet"] = True
    res = await _say("announce dinner's ready")
    assert res.reply.startswith("It's quiet hours")
    assert await _rows(house) == []


async def test_dry_runs_answer_but_queue_nothing(house):
    res = await _say("tell the kitchen dinner's ready", allow_writes=False)
    assert res.reply == "Okay, I've told the kitchen."
    assert await _rows(house) == []


async def test_shadow_decides_but_answers_and_queues_nothing(house, monkeypatch):
    monkeypatch.setenv("ZOE_INTERCOM", "shadow")
    assert await _say("tell the kitchen dinner's ready") is None
    assert await _rows(house) == []


@pytest.mark.parametrize("value", ["", "off", "OFFF", "1", "true"])
async def test_off_returns_before_any_db_read(house, monkeypatch, value):
    monkeypatch.setenv("ZOE_INTERCOM", value)
    opened = []

    def boom():
        opened.append(1)  # the tier swallows the raise, so the call itself is the evidence
        raise RuntimeError("the DB was read with the intercom off")

    monkeypatch.setattr(db_compat, "get_compat_db", boom)
    assert await _say("tell the kitchen dinner's ready") is None
    assert opened == []


async def test_other_channels_and_other_turns_fall_through(house):
    assert await ic.tier("tell the kitchen dinner's ready", ADULT, "s1", channel="chat", extra_ctx=_ctx(ADULT)) is None
    for text in ("call mum", "tell me a joke", "what time is it"):
        assert await _say(text) is None
    assert await _rows(house) == []


async def test_a_db_failure_never_breaks_the_turn(house, monkeypatch):
    @contextlib.asynccontextmanager
    async def broken():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(db_compat, "get_compat_db", broken)
    assert await _say("tell the kitchen dinner's ready") is None


async def test_the_fast_tier_wrapper_runs_it_before_the_router(house, monkeypatch):
    import fast_tiers as ft
    import panel_review_cards as prc
    import self_model as sm
    import session_status as ss
    import voice_enrollment as ve

    async def none(*a, **k):
        return None

    for name in ("_distress_tier", "_feedback_tier", "_provenance_tier"):
        monkeypatch.setattr(ft, name, none)
    for mod in (sm, ve, ss, prc):
        monkeypatch.setattr(mod, "tier", none)
    reached = []

    async def core(text, user_id, session_id, **kw):
        reached.append(text)
        return None

    monkeypatch.setattr(ft, "_resolve_core", core)
    import memory_provenance as mp

    monkeypatch.setattr(mp, "note_direct_reply", lambda *a, **k: None)
    res = await ft.resolve("tell the kitchen dinner's ready", ADULT, "s1", channel="voice", extra_ctx=_ctx(ADULT),
                           phase="conversation")
    assert res is not None and res.tier == "intercom"
    assert reached == []
    assert await ft.resolve("call mum", ADULT, "s1", channel="voice", extra_ctx=_ctx(ADULT), phase="conversation") is None
    assert reached == ["call mum"]


def test_the_owner_rule_is_recorded_in_the_vision():
    vision = (ROOT.parents[1] / "docs" / "VISION.md").read_text()
    assert "intercom" in vision.lower() and "unprompted" in vision.lower()


def test_quiet_window_uses_the_household_hours(monkeypatch):
    monkeypatch.setenv("ZOE_QUIET_START_HOUR", "22")
    monkeypatch.setenv("ZOE_QUIET_END_HOUR", "7")
    from time_utils import zoe_timezone

    tz = zoe_timezone()
    assert ic._quiet_now(datetime(2026, 10, 19, 23, 30, tzinfo=tz)) is True
    assert ic._quiet_now(datetime(2026, 10, 19, 6, 59, tzinfo=tz)) is True
    assert ic._quiet_now(datetime(2026, 10, 19, 18, 0, tzinfo=tz)) is False
