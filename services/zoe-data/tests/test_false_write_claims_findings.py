"""The 2026-10-12 "Zoe says she saved things she never saved" findings, each pinned as a regression (invented items only).

 1. a one-word slot answer -> the brain narrated "I've added a work meeting to your calendar" with no write
    (the guard: tests/test_write_claim_guard.py) - and the request that began that exchange now books itself.
 2. a COMPLAINT ("I was asking you to set meetings and add things to the shopping list") was stored whole as an item and
    answered "I've added … Check the panel and tap Done" - over a form nobody could confirm.
 3. "add noodles? Noodles. To the shopping list." stored "noodles? Noodles".
 4. "Can you add fix the gate to my to-do list" -> a list SHOW, nothing added.
 5. "Can we add a doctor's appointment on Friday at twelve o'clock?" -> two failed NLU calls, then the brain asked for a title
    the words had already given.
 6. a mis-heard "… in my calendar are at 12 o'clock" at router confidence 0.39 was ANSWERED with a calendar read.

Negative controls (run by hand when written, each went red): restoring ``_list_item_ok`` -> ``True`` fails finding 2;
removing ``clean_item_text`` from ``_list_add_from_text`` fails 3; reverting ``_list_add_from_text``'s list-name alternation to
the old one (no ``to-do``) fails 4; removing the ``calendar_create`` branch of ``detect_and_extract_intent`` fails 5; deleting
the floor in ``skybridge_router_gate`` fails 6.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

import intent_router
import list_service
import skybridge_service as sky
from skybridge_service import classify_skybridge_intent

TODAY = date(2026, 10, 12)       # a Monday
FRIDAY = date(2026, 10, 16)

COMPLAINT = ("I don't know where you got that from. I was asking you to set meetings and and add things "
             "to the shopping list.")


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    monkeypatch.setattr(sky, "today_for_zoe_tz", lambda: TODAY)


def _two_stage(domain, conf=0.9, gated=False, head_top="", reason=None):
    d = {"domain": domain, "head_conf": conf, "gated": gated, "head_top": head_top or domain}
    if reason:
        d["reason"] = reason
    return {"two_stage": d}


# ── finding 2: a sentence is not an item ─────────────────────────────────────────────────────────────────────────────

def test_a_complaint_is_never_a_list_add_in_either_parser():
    i = intent_router.detect_intent(COMPLAINT, log_miss=False)
    assert i is None or i.name != "list_add", i
    s = classify_skybridge_intent(COMPLAINT)
    assert s is None or s.action != "add_item", s


@pytest.mark.parametrize("sentence", [
    "I'd like you to know I was asking for the shopping list to be shorter, so add that to my list",
    "What about the thing I said earlier to my shopping list",
    "That is not what I meant by add things to the shopping list",
])
def test_other_sentences_that_end_in_a_list_are_not_items(sentence):
    i = intent_router.detect_intent(sentence, log_miss=False)
    assert i is None or i.name != "list_add" or list_service.is_plausible_item(i.slots["item"]), i


@pytest.mark.parametrize("item,ok", [
    ("oat milk", True), ("fix the gate", True), ("do laundry", True), ("call the plumber back", True),
    ("i don't know where you got that from", False), ("what about the thing", False), ("things", False),
    ("noodles. and then some", False), ("", False),
    ("one two three four five six seven eight nine ten eleven twelve thirteen", False),
])
def test_is_plausible_item(item, ok):
    assert list_service.is_plausible_item(item) is ok


# ── review (#2004, P1): a possessive item ("my passport") is an item, and a REFUSED explicit command is not re-read as a
# garbled one (the garbled matcher's optional "a" ate the first letter of "add" -> the stored item "dd my passport") ────────

@pytest.mark.parametrize("item", ["my passport", "your book", "my mum's birthday card", "our spare keys", "my reading glasses"])
def test_a_possessive_noun_phrase_is_a_plausible_item(item):
    assert list_service.is_plausible_item(item) is True


# review (#2004, P2): an auxiliary-looking word can be the NOUN of the item ("can opener", "can of paint", "spare can", "last will")
@pytest.mark.parametrize("item", ["my can opener", "our can of paint", "my spare can", "your last will", "my can of soup"])
def test_an_auxiliary_homograph_used_as_a_noun_is_still_an_item(item):
    assert list_service.is_plausible_item(item) is True


@pytest.mark.parametrize("item", ["my phone can ring", "my can is empty", "my phone will not work", "our car can not start", "my dog has a cold"])
def test_an_auxiliary_in_predicate_position_is_still_a_sentence(item):
    assert list_service.is_plausible_item(item) is False


@pytest.mark.parametrize("said,item", [
    ("add my can opener to my shopping list", "my can opener"),
    ("add our can of paint to the work list", "our can of paint"),
])
def test_a_homograph_noun_item_takes_the_deterministic_add_path(said, item):
    i = intent_router.detect_intent(said, log_miss=False)
    assert i is not None and i.name == "list_add" and i.slots["item"] == item, i


@pytest.mark.parametrize("item", ["my phone is dead", "your dog doesn't like it", "my kids don't know", "your answer was wrong"])
def test_a_possessive_sentence_is_still_not_an_item(item):
    assert list_service.is_plausible_item(item) is False


@pytest.mark.parametrize("said,item", [
    ("add my passport to my shopping list", "my passport"),
    ("add your book to the shopping list", "your book"),
    ("put my keys on the shopping list", "my keys"),
    ("Can you add my mum's birthday card to the shopping list", "my mum's birthday card"),
])
def test_a_possessive_item_is_stored_clean_on_both_parsers(said, item):
    i = intent_router.detect_intent(said, log_miss=False)
    assert i is not None and i.name == "list_add" and i.slots["item"] == item, i
    # the Skybridge cleaner may drop a leading "my" from the stored text ("my passport" -> "passport"); the point
    # here is that it is an ADD, not a refusal
    s = classify_skybridge_intent(said)
    assert s is not None and s.action == "add_item" and s.item_text in {item, item.split(" ", 1)[1]}, s


@pytest.mark.parametrize("said", [
    "add I don't know where you got that to the shopping list",
    "add what to the shopping list",
    "Can you add what to the shopping list",
    "please add things to my shopping list",
])
def test_a_refused_explicit_add_never_falls_into_the_garbled_matcher(said):
    """The item is refused ('a sentence / a vague noun'): the detector declines, it does not store 'dd what' or 'can you add what'."""
    i = intent_router.detect_intent(said, log_miss=False)
    assert i is None or i.name != "list_add", i


def test_a_genuinely_garbled_add_still_recovers():
    i = intent_router.detect_intent("I'd go to council to my work list", log_miss=False)
    assert i is not None and i.name == "list_add" and i.slots["item"] == "council", i


def test_the_old_panel_reply_that_claimed_an_unconfirmed_add_is_gone():
    """voice_tts's list_add panel path said "I've added X … Check the panel and tap Done" while NOTHING was saved until Done
    was tapped (and nobody could tap it). The reply no longer claims the write, and the path is behind the INTENT_GATE."""
    src = (Path(__file__).resolve().parents[1] / "routers" / "voice_tts.py").read_text(encoding="utf-8")
    assert "I've added {_item_text} to your list" not in src
    assert "Check the panel and tap Done" not in src
    assert 'keyword_intent_allowed("list_add"' in src and "_panel_is_live(panel_id)" in src


# ── finding 3: a stutter and punctuation are not part of the item ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("noodles? Noodles", "noodles"),
    ("um oat milk and and eggs", "oat milk and eggs"),
    ("the the kettle.", "the kettle"),
    ("Noodles.", "Noodles"),
    ("  bread   ", "bread"),
    ("milk! Milk!", "milk"),
])
def test_clean_item_text(raw, clean):
    assert list_service.clean_item_text(raw) == clean


def test_the_noodles_utterance_stores_clean_text_on_both_parsers():
    said = "Can you add noodles? Noodles. To the shopping list."
    s = classify_skybridge_intent(said)
    assert s.action == "add_item" and s.item_text == "noodles" and s.list_type == "shopping"
    i = intent_router.detect_intent(said, log_miss=False)
    assert i is not None and i.name == "list_add" and i.slots["item"] == "noodles"


# ── finding 4: "add … to my to-do list" is an add, never a show ──────────────────────────────────────────────────────

@pytest.mark.parametrize("said,item,list_type", [
    ("Can you add fix the gate to my to-do list.", "fix the gate", "tasks"),
    ("add fix the gate to my to do list", "fix the gate", "tasks"),
    ("Please add water the ferns to the todo list", "water the ferns", "tasks"),
    ("put fix the gate on my to-do list", "fix the gate", "tasks"),
    ("Hey Zoe, put oat milk on my shopping list please", "oat milk", "shopping"),
    ("add kayaking to my bucket list", "kayaking", "bucket"),
])
def test_add_to_a_named_list_is_an_add(said, item, list_type):
    s = classify_skybridge_intent(said)
    assert s is not None and (s.domain, s.action) == ("lists", "add_item"), s
    assert s.item_text == item and s.list_type == list_type


def test_a_write_led_turn_no_parser_claims_is_not_answered_with_a_show():
    s = classify_skybridge_intent("add a task called fix the gate to my list of jobs")
    assert s is None or s.action != "show"
    # ... while the real reads still read
    assert classify_skybridge_intent("show my to-do list").action == "show"
    assert classify_skybridge_intent("what's on my shopping list").action == "show"


def test_the_gate_vetoes_a_read_that_contradicts_an_add_in_the_words():
    show = sky.SkybridgeIntent(domain="lists", action="show")
    verdict = sky.skybridge_router_gate("Can you add fix the gate to my jobs list", show, _two_stage("lists"))
    assert verdict[:2] == ("veto", "verb_contradiction")
    # a genuine read is untouched
    assert sky.skybridge_router_gate("show my jobs list", show, _two_stage("lists"))[:2] == ("allow", "router_agrees")


# ── finding 5: day + time + a default title, read back ───────────────────────────────────────────────────────────────

APPT = "Can we add a doctor's appointment on Friday at twelve o'clock?"


def test_appointment_with_a_day_and_a_spoken_time_is_booked_for_that_day_with_a_default_title():
    s = classify_skybridge_intent(APPT)
    assert (s.domain, s.action) == ("calendar", "create_event")
    assert s.title == "Doctor's appointment"
    assert s.target_time == "12:00" and s.start_date == FRIDAY and s.all_day is False


@pytest.mark.parametrize("said,title,hhmm", [
    ("schedule a dentist appointment for tomorrow at 3 pm", "Dentist appointment", "15:00"),
    ("add a team meeting on Wednesday at nine o'clock", "Team meeting", "09:00"),
    ("book a physio session on Thursday at half past two", None, None),   # not understood -> claims nothing
    ("add lunch with Sam to my calendar at noon", "Lunch with Sam", "12:00"),
])
def test_spoken_times_and_titles(said, title, hhmm):
    s = classify_skybridge_intent(said)
    if title is None:
        assert s is None or s.action != "create_event"
        return
    assert (s.action, s.title, s.target_time) == ("create_event", title, hhmm)


def test_a_bare_hour_without_am_pm_is_still_not_guessed():
    assert classify_skybridge_intent("Can you add dentist at 9", {"intent": {"domain": "calendar"}, "cards": []}) is None


def test_what_about_a_meeting_tomorrow_at_nine_books_itself():
    """The turn that began the slot-answer exchange ("What about a meeting tomorrow at 9 a.m. for work"): the words carry
    title, day and time, so nothing is left to ask."""
    s = classify_skybridge_intent("What about uh at a meeting tomorrow at 9 a.m. For work.")
    assert (s.domain, s.action) == ("calendar", "create_event")
    assert s.title == "Meeting for work" and s.target_time == "09:00"
    assert s.start_date == date(2026, 10, 13)


def test_weekday_dates_for_calendar_reads_and_writes():
    assert sky._calendar_date_from_text(" what's on friday ", TODAY)[1] == FRIDAY
    assert sky._calendar_date_from_text(" what's on monday ", TODAY)[1] == date(2026, 10, 19)    # a bare weekday is the NEXT one
    # an explicit date still wins over the weekday label
    assert sky._calendar_date_from_text(" saturday 17 october ", TODAY)[1] == date(2026, 10, 17)


@pytest.mark.asyncio
async def test_the_booking_reads_back_the_day_and_the_time(monkeypatch):
    class _Db:
        def __init__(self):
            self.rows = []

        async def execute(self, sql, *args):
            self.rows.append(args)

        async def commit(self):
            return None

    async def fake_calendar(intent, user_id, db):
        return {"cards": [], "intent": {}}

    monkeypatch.setattr(sky, "_resolve_calendar", fake_calendar)
    intent = classify_skybridge_intent(APPT)
    result = await sky._resolve_calendar_create(intent, "jason", _Db())
    assert result["spoken_summary"] == "Added Doctor's appointment on Friday at 12 PM."


@pytest.mark.parametrize("hhmm,spoken", [("12:00", "12 PM"), ("09:30", "9:30 AM"), ("00:15", "12:15 AM"), ("15:00", "3 PM")])
def test_spoken_clock(hhmm, spoken):
    assert sky._spoken_clock(hhmm) == spoken


@pytest.mark.asyncio
async def test_the_legacy_calendar_intent_fills_its_slots_without_the_nlu_filler(monkeypatch):
    """Two failed nlu_extractor calls (6.8 s) on exactly this sentence: the words carry everything, so no LLM is asked."""
    async def boom(*_a, **_k):
        raise AssertionError("the NLU filler must not be called for a sentence with a title, a day and a time")

    import nlu_extractor

    monkeypatch.setattr(nlu_extractor, "extract_slots_for_intent", boom)
    intent = await intent_router.detect_and_extract_intent(APPT, "jason")
    assert intent is not None and intent.name == "calendar_create"
    assert intent.slots == {"title": "Doctor's appointment", "time": "12:00", "date": "2026-10-16"}


# ── finding 6: a mis-heard write at router confidence 0.39 is not answered ───────────────────────────────────────────

MISHEARD = "Keynes pollutants in my calendar are at 12 o'clock"


def test_a_misheard_write_at_low_router_confidence_is_vetoed():
    s = classify_skybridge_intent(MISHEARD)
    assert s is not None and (s.domain, s.action) == ("calendar", "show")      # the keyword fallback's reading
    below_gate = _two_stage("chat", conf=0.39, gated=True, head_top="calendar", reason="below_gate")
    assert sky.skybridge_router_gate(MISHEARD, s, below_gate)[:2] == ("veto", "router_unsure_low_conf")
    # a bit more evidence and the old recovery still works
    assert sky.skybridge_router_gate(MISHEARD, s, _two_stage("chat", conf=0.47, gated=True, head_top="calendar",
                                                             reason="below_gate"))[:2] == ("allow", "router_unsure")


def test_unsure_write_floor_applies_to_writes_only_and_can_be_tuned(monkeypatch):
    add = sky.SkybridgeIntent(domain="lists", action="add_item", item_text="oat milk")
    low = _two_stage("chat", conf=0.40, gated=True, head_top="lists", reason="below_gate")
    assert sky.skybridge_router_gate("add oat milk to my list", add, low)[:2] == ("veto", "router_unsure_low_conf")
    # a plain READ keeps the garbled-STT recovery at any confidence (the existing contract)
    overview = sky.SkybridgeIntent(domain="lists", action="overview")
    assert sky.skybridge_router_gate("shammy my lists", overview, _two_stage("chat", conf=0.2, gated=True,
                                                                           head_top="lists"))[:2] == ("allow", "router_unsure")
    monkeypatch.setenv("ZOE_SKYBRIDGE_UNSURE_FLOOR_WRITE", "0")
    assert sky.skybridge_router_gate("add oat milk to my list", add, low)[:2] == ("allow", "router_unsure")


@pytest.mark.asyncio
async def test_the_vetoed_misheard_turn_reaches_the_brain_not_a_calendar_card(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="skybridge_service")
    router = _two_stage("chat", conf=0.3936, gated=True, head_top="calendar", reason="below_gate")
    result = await sky.resolve_skybridge_request(MISHEARD, "jason", router_decision=router)
    assert result["handled"] is False and result["vetoed"] is True
    assert "decision=veto reason=router_unsure_low_conf" in caplog.text


# ── (e) never "check the panel" for a panel that cannot show it ──────────────────────────────────────────────────────

class _Ws:
    pass


def test_panel_live_needs_a_subscriber_on_the_panels_own_channel():
    """The write-claim guard's "may I say check the panel?" question reads ONE liveness source: push.PushBroadcaster.panel_live
    (a subscriber on the panel's OWN channel, or a recent UI-action ack). Its full matrix is pinned in test_push_panel_status.py."""
    from push import PushBroadcaster

    bc = PushBroadcaster()
    assert bc.panel_live("panel-a") is False and bc.panel_live(None) is False
    bc._connections["all"] = {_Ws()}                         # an 'all' subscriber is not THIS panel
    assert bc.panel_live("panel-a") is False
    bc._connections["panel_panel-a"] = {_Ws()}
    assert bc.panel_live("panel-a") is True
    assert bc.panel_live("panel-b") is False


def test_the_voice_helper_fails_closed(monkeypatch):
    import routers.voice_tts as vt

    monkeypatch.setattr("push.broadcaster.panel_live", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    assert vt._panel_is_live("panel-a") is False
