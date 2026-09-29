"""Brief on the first turn of the day (``brief_first_turn``, ``ZOE_BRIEF_ON_FIRST_TURN``).

Fixtures only — no household data, no real ids. The DB edge is a small fake
modelling ``proactive_responses`` (the shared UNIQUE claim), so the claim path
runs through the real ``proactive.arrival`` SQL helpers.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # every edge is stubbed; slim-dep modules only

import contextlib
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import brief_first_turn as bft
import db_compat
import proactive.arrival as arrival
import zoe_core_client as core
import zoe_flue_client as flue

PERTH = ZoneInfo("Australia/Perth")
MEMBER = "member-a"
DAY = "2026-09-29"


def at(h, m=0):
    return datetime(2026, 9, 29, h, m, tzinfo=PERTH).astimezone(timezone.utc)


CTX_FULL = {
    "calendar": [
        {"title": "Standup", "start": "06:00", "end": "06:15", "location": ""},  # over by 08:00
        {"title": "Dentist", "start": "09:30", "end": "10:00", "location": "Main St"},
        {"title": "Dinner with Sam", "start": "18:30", "end": "", "location": ""},
    ],
    "open_loops": [{"text": "book the car service", "hint": "", "weight": 1, "due": None}],
    "emotional_moments": ["User was nervous about the new job", "second one"],
    "portrait_snippet": "Likes tea",
}
CTX_LATER_ONLY = {"calendar": [{"title": "Dinner with Sam", "start": "18:30", "end": "", "location": ""}]}


class _Cur:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeDB:
    """``proactive_responses`` with its UNIQUE (user_id, claim_key, local_date)."""

    def __init__(self):
        self.claims: dict[tuple, str] = {}
        self.ops: list[str] = []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.ops.append(sql.split()[0])
        if sql.startswith("SELECT 1 FROM proactive_responses"):
            return _Cur({"?column?": 1} if tuple(params) in self.claims else None)
        if sql.startswith("INSERT INTO proactive_responses"):
            key = (params[1], params[2], params[4])
            if key in self.claims:
                return _Cur(None)
            self.claims[key] = params[3]  # trigger_type
            return _Cur({"id": params[0]})
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        return None


@pytest.fixture
def env(monkeypatch):
    """Flag on, Perth clock at 08:00, a fake DB and a stubbed gatherer."""
    for key in ("ZOE_BRIEF_WINDOW_START", "ZOE_BRIEF_WINDOW_END", "ZOE_PROACTIVE_BRIEF_ON_ARRIVAL",
                "ZOE_PROACTIVE_SPOKEN", "ZOE_SEAM_RECALL_INJECT", "ZOE_SEAM_OFFER_INJECT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZOE_BRIEF_ON_FIRST_TURN", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setattr(arrival, "_ZOE_TZ", PERTH)
    bft._reset_state()
    db = FakeDB()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(arrival, "_get_compat_db", fake_db)
    state = {"now": at(8), "ctx": CTX_FULL, "gathers": 0, "db": db}

    async def fake_gather(uid, local_date):
        state["gathers"] += 1
        assert local_date == DAY
        return state["ctx"]

    monkeypatch.setattr(bft, "_gather", fake_gather)
    monkeypatch.setattr(bft, "_now_utc", lambda: state["now"])
    yield state
    bft._reset_state()


def claimed(state) -> bool:
    return (MEMBER, arrival.CLAIM_KEY, DAY) in state["db"].claims


# ── greeting vs command (table-driven) ───────────────────────────────────────
@pytest.mark.parametrize("text", [
    "morning", "Good morning!", "hey zoe", "Hey Zoe, what's up?", "hi", "hello there",
    "how's it going", "Morning Zoe, how are you?", "what's on today", "let's talk",
    "Let's chat, Zoe",
])
def test_greeting_turns(text):
    assert bft.turn_shape(text) == "greeting"


@pytest.mark.parametrize("text", [
    "turn on the lights", "play some jazz", "morning, turn on the kitchen lights",
    "hey zoe set a timer for ten minutes", "what's the weather", "let's talk about mum's birthday",
    "", "remind me to call the plumber",
])
def test_command_turns(text):
    assert bft.turn_shape(text) == "command"


# ── gates ─────────────────────────────────────────────────────────────────────
async def test_greeting_gets_the_full_weave_and_no_claim_before_the_reply(env):
    brief = await bft.prepare("morning zoe", MEMBER)
    assert brief is not None and brief.shape == "greeting"
    assert brief.block.startswith(bft.BLOCK_LABEL + "\n") and brief.block.endswith(bft.BLOCK_CLOSE)
    assert "Dentist at 09:30, Main St" in brief.body and "Dinner with Sam at 18:30" in brief.body
    assert "Standup" not in brief.body  # already over — deterministic, not the model's call
    assert "book the car service" in brief.body
    assert brief.body.count("Recently on their mind") == 1  # one emotional follow-up at most
    assert "Likes tea" not in brief.body  # the portrait is not a day item
    assert brief.items == 4
    assert not claimed(env)  # preparing never takes the claim


async def test_claim_taken_only_after_a_produced_reply(env, caplog):
    brief = await bft.prepare("morning", MEMBER)
    assert await bft.settle(brief, produced=False) is False
    assert not claimed(env)
    assert await bft.prepare("morning", MEMBER) is not None  # a failed turn burned nothing
    with caplog.at_level(logging.INFO, logger="brief_first_turn"):
        assert await bft.settle(brief, produced=True) is True
    assert claimed(env)
    assert "BRIEF_FIRST_TURN user=member-a items=4 shape=greeting injected=1 claimed=1" in caplog.text
    bft._reset_state()  # a restart: only the DB claim can stop the second brief
    assert await bft.prepare("morning", MEMBER) is None


async def test_empty_context_injects_nothing_and_takes_no_claim(env):
    env["ctx"] = {"portrait_snippet": "Likes tea", "board_summary": {"pending": 3, "in_progress": 1}}
    assert await bft.prepare("morning", MEMBER) is None
    env["ctx"] = {"calendar": [{"title": "Standup", "start": "06:00", "end": "06:15", "location": ""}]}
    bft._reset_state()
    assert await bft.prepare("morning", MEMBER) is None  # only a finished event
    assert not claimed(env)


@pytest.mark.parametrize("hour", [4, 12, 21])
async def test_outside_the_window_nothing(env, hour):
    env["now"] = at(hour)
    assert await bft.prepare("morning", MEMBER) is None
    assert env["gathers"] == 0 and env["db"].ops == [] and not claimed(env)


async def test_window_is_configurable(env, monkeypatch):
    env["now"] = at(13)
    monkeypatch.setenv("ZOE_BRIEF_WINDOW_END", "14:00")
    assert await bft.prepare("morning", MEMBER) is not None


@pytest.mark.parametrize("uid", ["guest", "voice-guest", "", "demo_s4_ab12cd", "test-user", "ci_bot"])
async def test_guest_and_synthetic_ids_get_nothing(env, uid):
    assert await bft.prepare("morning", uid) is None
    assert env["gathers"] == 0 and env["db"].ops == []


async def test_command_with_a_time_critical_event_gets_one_line(env):
    brief = await bft.prepare("turn on the lights", MEMBER)
    assert brief is not None and brief.shape == "command"
    assert brief.body.startswith(bft.COMMAND_INSTRUCTION)
    lines = [ln for ln in brief.body.splitlines() if ln.startswith("- ")]
    assert lines == ["- Soon: Dentist at 09:30"]


async def test_command_with_an_overdue_loop_gets_one_line(env):
    env["ctx"] = {"open_loops": [{"text": "renew rego", "hint": "", "weight": 1,
                                  "due": "2026-09-28T01:00:00"}]}
    brief = await bft.prepare("play some jazz", MEMBER)
    assert brief is not None
    assert [ln for ln in brief.body.splitlines() if ln.startswith("- ")] == ["- Overdue: renew rego"]


async def test_command_without_anything_time_critical_leaves_it_for_the_next_open_turn(env, caplog):
    env["ctx"] = CTX_LATER_ONLY
    with caplog.at_level(logging.INFO, logger="brief_first_turn"):
        assert await bft.prepare("turn on the lights", MEMBER) is None
    assert "BRIEF_FIRST_TURN user=member-a items=1 shape=command injected=0 claimed=0" in caplog.text
    assert not claimed(env)
    brief = await bft.prepare("morning", MEMBER)
    assert brief is not None and "Dinner with Sam" in brief.body


async def test_continuity_turn_defers_the_brief(env):
    assert await bft.prepare("Ugh, I'm so stressed", MEMBER) is None
    assert env["gathers"] == 0


async def test_flag_off_does_nothing(env, monkeypatch):
    monkeypatch.delenv("ZOE_BRIEF_ON_FIRST_TURN")
    assert await bft.prepare("morning", MEMBER) is None
    assert env["gathers"] == 0 and env["db"].ops == []


async def test_negative_control_the_flag_read_is_what_gates_it(env, monkeypatch):
    """Break the flag read: the flag-off fixture above then injects (so that test
    is not passing vacuously), and a flag-on turn stops injecting."""
    monkeypatch.delenv("ZOE_BRIEF_ON_FIRST_TURN")
    monkeypatch.setattr(bft, "brief_on_first_turn_enabled", lambda: True)
    assert await bft.prepare("morning", MEMBER) is not None
    monkeypatch.setenv("ZOE_BRIEF_ON_FIRST_TURN", "1")
    monkeypatch.setattr(bft, "brief_on_first_turn_enabled", lambda: False)
    bft._reset_state()
    assert await bft.prepare("morning", MEMBER) is None


# ── one claim across all three paths ─────────────────────────────────────────
async def test_first_turn_claim_stops_a_reenabled_0730_spoken_brief(env, monkeypatch):
    monkeypatch.setattr(arrival, "_now_utc", lambda: env["now"])
    await bft.settle(await bft.prepare("morning", MEMBER), produced=True)
    verdict, _ = await arrival.claim_scheduled_brief(user_id=MEMBER, panel_id="p1", pending_id=None)
    assert verdict == "already_spoken"


async def test_0730_claim_stops_the_first_turn_brief(env, monkeypatch):
    monkeypatch.setattr(arrival, "_now_utc", lambda: at(7, 30))
    verdict, _ = await arrival.claim_scheduled_brief(user_id=MEMBER, panel_id="p1", pending_id=None)
    assert verdict == "speak"
    assert await bft.prepare("morning", MEMBER) is None
    assert env["gathers"] == 0


# ── the Flue seam: block after the words, claim after an ok verdict ──────────
class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _flue_client(captured, *, fail=False):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            if fail:
                raise RuntimeError("HTTP 500")
            return _Resp({"result": {"text": "Morning! Dentist at 9:30."}})

    return _Client


async def test_flue_seam_appends_the_block_and_claims_after_the_reply(env, monkeypatch):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    captured: dict = {}
    monkeypatch.setattr(httpx, "AsyncClient", _flue_client(captured))
    seen_claim_at_yield = []
    async for delta in flue.run_flue_brain_streaming("morning zoe", "s1", MEMBER):
        seen_claim_at_yield.append(claimed(env))
    assert seen_claim_at_yield == [False]
    assert claimed(env)
    outbound = json.loads(captured["content"])["message"]
    words = outbound.index("morning zoe")
    assert outbound.index(bft.BLOCK_LABEL) > words and outbound.rstrip().endswith(bft.BLOCK_CLOSE)


async def test_flue_seam_failed_turn_does_not_burn_the_brief(env, monkeypatch):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(httpx, "AsyncClient", _flue_client({}, fail=True))
    out = [d async for d in flue.run_flue_brain_streaming("morning", "s1", MEMBER)]
    assert out == [flue._FALLBACK_TEXT]
    assert not claimed(env)


# ── the core seam: delimited block before the utterance, claim after text ────
def test_core_block_pair_follows_the_close_rule():
    assert core._close_marker(bft.BLOCK_LABEL) == bft.BLOCK_CLOSE
    assert (bft.BLOCK_LABEL, bft.BLOCK_CLOSE) in core._CONTEXT_BLOCKS


def test_core_composition_keeps_the_message_last():
    out = core._compose_message("morning", history=None, db_memory_context=None, portrait=None,
                                day_brief="Instruction\n- Calendar: Dentist at 09:30")
    assert out.index(bft.BLOCK_CLOSE) < out.index(core._UTTERANCE_MARKER)
    assert out.endswith(f"{core._UTTERANCE_MARKER}\nmorning")
    assert core._compose_message("morning", history=None, db_memory_context=None,
                                 portrait=None) == "morning"


class _FakeWorker:
    def __init__(self, fail=False):
        self.fail = fail
        self.composed = ""

    async def stream(self, compose, *, timeout_s):
        self.composed = await compose()
        if self.fail:
            raise RuntimeError("worker died")
        yield "Morning!"


@pytest.mark.parametrize("fail", [False, True])
async def test_core_seam_claims_only_after_text(env, monkeypatch, fail):
    worker = _FakeWorker(fail=fail)

    async def fake_worker_for(*a, **k):
        return worker

    async def no_packet(*a, **k):
        return ""

    monkeypatch.setattr(core, "_worker_for", fake_worker_for)
    monkeypatch.setattr(core, "_memory_packet_block", no_packet)
    at_yield = []
    with contextlib.suppress(RuntimeError):
        async for _ in core.run_zoe_core_streaming("morning", "s1", MEMBER):
            at_yield.append(claimed(env))
    assert bft.BLOCK_LABEL in worker.composed
    assert at_yield == ([] if fail else [False])
    assert claimed(env) is (not fail)
