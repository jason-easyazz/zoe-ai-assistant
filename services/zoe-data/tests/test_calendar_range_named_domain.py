"""Three live voice failures (2026-11-03), each fixed as a CLASS.

1. TIME RANGES. "add work to my calendar from 9am to 6pm" booked an ALL-DAY event and said "Added work to your calendar
   today.": the timed parser needs "at|for <time>", and the no-time fallback took the title and silently dropped the range.
   Ranges now parse into start + end and the reply says them; the no-time fallback never claims a turn whose words carry a
   time (or a date) it did not read - those go on to the brain, which can ask.
2. NAMED DOMAIN. "do I have anything on my calendar today" (heard as "... calendar to say?") - the router chose lists, the
   Skybridge calendar read was vetoed (router_disagrees), and the lists expert read the shopping list aloud. The noun the
   words name outranks a router that chose a different, non-chat domain (Skybridge gate, the keyword INTENT_GATE and the
   expert dispatch). A chat verdict, a question-shape re-point and words that name no domain keep the veto.
3. UNKEPT PROMISE. "that's not my calendar" -> the brain replied with only a lookup announcement ("let me check that for you
   right now") and called no tool. An announcement-only reply in a turn with no tool sentinel is replaced with an honest line.

Negative controls (run by hand when written; each went red): removing the range branch in ``classify_skybridge_intent``
fails the range tests; removing the ``_unread_when`` check fails ``test_no_time_fallback_never_drops_a_time``; removing the
``named_domain`` branch in ``skybridge_router_gate`` fails the gate tests; removing the replacement in
``narration_filter.filter_stream`` fails ``test_announcement_only_reply_without_a_tool_is_replaced``.
Synthetic utterances only (ci_safe).
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

pytestmark = pytest.mark.ci_safe

import command_shapes
import expert_dispatch
import fast_tiers
import narration_filter as nf
import skybridge_service as sky

TODAY = date(2026, 10, 12)  # a Monday


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(sky, "today_for_zoe_tz", lambda: TODAY)
    monkeypatch.delenv("ZOE_SKYBRIDGE_ROUTER_VETO", raising=False)
    monkeypatch.delenv("ZOE_INTENT_ROUTER_GATE", raising=False)


# ── 1. time ranges ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("utterance,start,end", [
    ("Add work to my calendar from 9am to 6pm", "09:00", "18:00"),          # the live turn
    ("add work to my calendar from 9am to 6pm please", "09:00", "18:00"),
    ("add work to my calendar 9 to 5", "09:00", "17:00"),                    # neither said: appointment hours
    ("add work to my calendar 9am-6pm", "09:00", "18:00"),
    ("add work to my calendar between 9 and 5", "09:00", "17:00"),
    ("add work from 9 till 6 to my calendar", "09:00", "18:00"),
    ("add work to my calendar from nine to five", "09:00", "17:00"),
    ("add work to my calendar from 9am to 6", "09:00", "18:00"),             # the end inherits the reading that fits
    ("add work to my calendar from 9 to 6pm", "09:00", "18:00"),             # the start takes the reading before the end
    ("add work to my calendar from 11 to 2", "11:00", "14:00"),
    ("add work to my calendar from 9:30 to 5:30", "09:30", "17:30"),
    ("add work to my calendar from 14:00 to 16:00", "14:00", "16:00"),
    ("add dinner to my calendar from 7 to 9 tonight", "19:00", "21:00"),
    ("schedule a meeting from 2 to 3pm", "14:00", "15:00"),
])
def test_time_ranges_parse_into_start_and_end(utterance, start, end):
    intent = sky.classify_skybridge_intent(utterance, None)
    assert intent is not None and (intent.domain, intent.action) == ("calendar", "create_event")
    assert (intent.target_time, intent.end_time, intent.all_day) == (start, end, False)
    assert intent.title.lower() in ("work", "dinner", "meeting")


def test_range_title_drops_the_date_it_was_given():
    intent = sky.classify_skybridge_intent("add work on 14 November from 9 to 5 to my calendar", None)
    assert (intent.title, intent.start_date, intent.target_time, intent.end_time) == \
        ("work", date(2026, 11, 14), "09:00", "17:00")


def test_range_keeps_the_day():
    intent = sky.classify_skybridge_intent("add gym to my calendar from 6 to 7 tomorrow", None)
    assert (intent.title, intent.target_time, intent.end_time) == ("gym", "18:00", "19:00")
    assert intent.start_date == date(2026, 10, 13)


@pytest.mark.parametrize("utterance", [
    "add work to my calendar from 6pm to 9am",
    "add work to my calendar from 10am to 9am",
])
def test_a_range_that_ends_before_it_starts_asks_and_writes_nothing(utterance):
    intent = sky.classify_skybridge_intent(utterance, None)
    assert (intent.domain, intent.action, intent.query) == ("calendar", "create_event", sky.RANGE_END_BEFORE_START)

    class _Db:
        async def execute(self, *_a):
            raise AssertionError("a backwards range must not be written")

    result = asyncio.run(sky._resolve_calendar_create(intent, "member-1", _Db()))
    assert result["actions"] == [] and "ends before it starts" in result["spoken_summary"]
    assert "What times" in result["spoken_summary"]


class _RecordingDb:
    def __init__(self):
        self.rows = []

    async def execute(self, _sql, *args):
        self.rows.append(args)

    async def commit(self):
        return None


async def test_range_is_written_with_its_end_and_read_back(monkeypatch):
    async def fake_calendar(intent, user_id, db):
        return {"cards": [], "intent": {}}

    monkeypatch.setattr(sky, "_resolve_calendar", fake_calendar)
    db = _RecordingDb()
    intent = sky.classify_skybridge_intent("add work to my calendar from 9am to 6pm", None)
    result = await sky._resolve_calendar_create(intent, "member-1", db)
    row = db.rows[0]
    assert row[2:7] == ("work", "2026-10-12", "09:00", "2026-10-12", "18:00")
    assert row[10] == 0                                                  # not all-day
    assert result["spoken_summary"] == "Added work to your calendar today from 9 AM to 6 PM."


async def test_single_time_and_all_day_read_backs_are_unchanged(monkeypatch):
    async def fake_calendar(intent, user_id, db):
        return {"cards": [], "intent": {}}

    monkeypatch.setattr(sky, "_resolve_calendar", fake_calendar)
    timed = sky.classify_skybridge_intent("add a meeting at 3pm to my calendar", None)
    assert (await sky._resolve_calendar_create(timed, "member-1", _RecordingDb()))["spoken_summary"] == \
        "Added Meeting today at 3 PM."
    whole = sky.classify_skybridge_intent("add work to my calendar", None)
    assert whole.all_day
    assert (await sky._resolve_calendar_create(whole, "member-1", _RecordingDb()))["spoken_summary"] == \
        "Added work to your calendar today."


@pytest.mark.parametrize("utterance", [
    "add dentist to my calendar at half past nine",
    "add work to my calendar at quarter to nine",
    "add dentist to my calendar on November 14",                         # a date the date parser does not read
    "add dentist to my calendar on the 14th",
    "add work to my calendar tomorrow morning",
    "add an event called work from 9 to 5ish",
    "add work to my calendar at noon-ish",
])
def test_no_time_fallback_never_drops_a_time(utterance):
    """The CLASS: a time / date the parsers did not read must never become an all-day "Added X to your calendar today."."""
    intent = sky.classify_skybridge_intent(utterance, None)
    assert intent is None or not (intent.action == "create_event" and intent.all_day), (utterance, intent)


@pytest.mark.parametrize("utterance,day", [
    ("add work to my calendar", TODAY),
    ("add dentist to my calendar on friday", date(2026, 10, 16)),
    ("add the dentist to my calendar on 14 November", date(2026, 11, 14)),
    ("add 5k run to my calendar", TODAY),
    ("add work to my calendar tomorrow", date(2026, 10, 13)),
])
def test_no_time_fallback_still_books_all_day(utterance, day):
    intent = sky.classify_skybridge_intent(utterance, None)
    assert (intent.action, intent.all_day, intent.start_date) == ("create_event", True, day)


def test_timer_and_reminder_phrasing_never_takes_the_range_path():
    assert sky._calendar_range_from_text("schedule a timer from 9 to 5", None) is None
    assert sky._calendar_range_from_text("add a reminder to my calendar from 9 to 5", None) is None


# ── 2. the named noun outranks a router that chose another domain ─────────────


def _two_stage(domain, *, conf=0.702, gated=False, reason=None, head_top=None):
    return {"two_stage": {"domain": domain, "tool": None if domain == "chat" else "x", "head_conf": conf, "gated": gated,
                          "reason": reason, "head_top": head_top or domain, "shortlist": [domain]}}


@pytest.mark.parametrize("utterance,router", [
    ("do I have anything on my calendar today", "lists"),
    ("Do I have anything on my calendar to say?", "lists"),             # the garbled STT form of the live turn
    ("what's on my schedule", "lists"),
    ("my calendar for today please", "timers"),
])
def test_gate_named_calendar_beats_a_different_router_domain(utterance, router):
    intent = sky.classify_skybridge_intent(utterance, None)
    assert (intent.domain, intent.action) == ("calendar", "show")
    decision, reason, *_ = sky.skybridge_router_gate(utterance, intent, _two_stage(router))
    assert (decision, reason) == ("allow", "named_domain")


@pytest.mark.parametrize("router,want", [
    (_two_stage("chat", conf=0.8, gated=True, head_top="chat", reason="chat_top"), ("veto", "router_chat")),
    (_two_stage("chat", conf=0.54, gated=True, head_top="people", reason="low_conf"), ("veto", "router_chat")),
    (_two_stage("memory", reason="evidence_question"), ("veto", "router_disagrees")),
])
def test_gate_named_noun_does_not_override_chat_or_a_question_repoint(router, want):
    utterance = "do I have anything on my calendar today"
    intent = sky.classify_skybridge_intent(utterance, None)
    decision, reason, *_ = sky.skybridge_router_gate(utterance, intent, router)
    assert (decision, reason) == want


@pytest.mark.parametrize("utterance,router", [
    # a statement ABOUT the calendar is a memory, whatever noun it mentions: the noun never outranks memory / people
    ("remember that my calendar is busy on fridays", "memory"),
    ("my calendar is a mess", "people"),
])
def test_gate_named_noun_never_outranks_memory_or_people(utterance, router):
    intent = sky.classify_skybridge_intent(utterance, None)
    assert intent is not None and intent.domain == "calendar"
    assert sky.skybridge_router_gate(utterance, intent, _two_stage(router))[:2] == ("veto", "router_disagrees")


def test_gate_ambiguous_words_keep_the_veto():
    # no domain noun at all: the router's confident other domain still vetoes
    intent = sky.SkybridgeIntent("calendar", "show")
    assert sky.skybridge_router_gate("anything happening today", intent, _two_stage("lists"))[:2] == \
        ("veto", "router_disagrees")
    # the words ALSO name the router's domain: genuinely ambiguous, the router decides
    assert sky.skybridge_router_gate("is the calendar on my shopping list", intent, _two_stage("lists"))[:2] == \
        ("veto", "router_disagrees")


@pytest.mark.parametrize("text,named", [
    ("what's on my calendar", {"calendar"}),
    ("what's on the shopping list", {"lists"}),
    ("list my reminders", {"reminders"}),        # "list" here is a verb
    ("remind me to call the plumber", set()),   # a verb names no domain
    ("how long left on the timer", {"timers"}),
    ("cancel my alarm", {"alarms"}),
    ("what's the weather", set()),
])
def test_named_domains(text, named):
    assert command_shapes.named_domains(text) == frozenset(named)


def test_intent_gate_shares_the_rule():
    verdict = {"domain": "lists", "head_top": "lists", "head_conf": 0.7, "gated": False}
    assert fast_tiers.intent_gate_decision("calendar_show", verdict, "what's on my calendar today") == \
        ("allow", "named_domain")
    assert fast_tiers.intent_gate_decision("calendar_show", verdict, "what's on today") == ("veto", "router_disagrees")
    assert fast_tiers.intent_gate_decision("calendar_show", verdict) == ("veto", "router_disagrees")


async def test_expert_never_answers_a_turn_that_names_another_domain(monkeypatch):
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(expert_dispatch, "threshold_for", lambda _d: 0.0)

    planned = []
    monkeypatch.setattr(expert_dispatch, "_plan", lambda domain, text: planned.append((domain, text)))
    out = await expert_dispatch.dispatch("lists", "Do I have anything on my calendar to say?", {"score": 0.7})
    assert out is None and planned == [], "the lists expert planned a calendar question"
    await expert_dispatch.dispatch("lists", "what's on my shopping list", {"score": 0.7})
    assert planned == [("lists", "what's on my shopping list")]          # its own domain still reaches the planner


@pytest.mark.parametrize("domain,text,mismatch", [
    ("lists", "do I have anything on my calendar today", True),
    ("lists", "what's on my shopping list", False),
    ("calendar", "any reminders today", False),                 # calendar/reminders are siblings
    ("lists", "what's on my calendar and shopping list", False),
    ("lists", "what have I got today", False),                  # names nothing: the router decides
    ("weather", "is my calendar wet", False),                   # only the record-domain experts are guarded
])
def test_names_another_domain(domain, text, mismatch):
    assert expert_dispatch._names_another_domain(domain, text) is mismatch


# ── 3. an announcement that ends a turn with no tool call ─────────────────────


async def _collect(chunks, **kw):
    async def src():
        for c in chunks:
            yield c

    return [d async for d in nf.filter_stream(src(), **kw)]


async def test_announcement_only_reply_without_a_tool_is_replaced():
    out = await _collect(["Let me check that ", "for you right now."])
    assert "".join(out) == nf.UNKEPT_PROMISE_REPLY


async def test_sorry_lead_announcement_is_also_caught():
    out = await _collect(["Sorry, let me check that for you right now."])
    assert "".join(out) == nf.UNKEPT_PROMISE_REPLY


async def test_announcement_with_a_tool_call_is_left_alone():
    tool = '__TOOL__:{"phase": "start", "id": "1", "name": "calendar_list_events"}'
    out = await _collect(["Let me check that for you right now.", tool])
    assert "".join(d for d in out if not d.startswith("__TOOL__")) == "Let me check that for you right now."


async def test_lane_without_visible_tools_is_left_alone():
    out = await _collect(["Let me check that for you right now."], tools_visible=False)
    assert "".join(out) == "Let me check that for you right now."


@pytest.mark.parametrize("chunks,want", [
    (["Let me check. You have two events today."], "You have two events today."),      # the answer follows: stripped
    (["I'll check the weather and get back to you."], "I'll check the weather and get back to you."),  # a deferral
    (["You have two events today."], "You have two events today."),
])
async def test_other_replies_are_unchanged(chunks, want):
    assert "".join(await _collect(chunks)) == want
