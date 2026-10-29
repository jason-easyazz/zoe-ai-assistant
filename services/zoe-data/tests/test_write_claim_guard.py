"""A reply that says it SAVED something is spoken only if something was saved (write_claim_guard + write_receipts).

Live 2026-10-12 (the trust problem): the brain answered a one-word slot reply with "I've added a work meeting to your
calendar for tomorrow at 9 a.m." - no events row, not one tool call in the turn. The guard sits where EVERY brain turn
ends (brain_dispatch), reads claims from per-language lexicon DATA (lexicons_data/write_claims.json) and checks them
against receipts the writers record (calendar_service / list_service / reminder_service / skybridge / the timer store).

Negative controls (run by hand when this was written, each went red): deleting the ``supported`` check in
``guard_text`` / ``judge`` fails the ``*_blocks_*`` tests; deleting the ``write_receipts.record`` line in
``calendar_service.create_event_record`` / ``list_service.add_item_to_list`` fails the ``*_records_a_receipt`` tests (and
the allow-with-write tests, which then see no receipt); emptying the ``es`` block of write_claims.json fails the Spanish
cases; making ``brain_dispatch._claim_guarded`` return ``inner`` fails the dispatch tests.
"""
from __future__ import annotations

import json
import logging
import re

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic; no DB, no sidecar, no network

import brain_dispatch as bd
import write_claim_guard as g
import write_receipts as wr

HONEST_EN = "I didn't manage to add that; want me to try again?"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_WRITE_CLAIM_GUARD", raising=False)
    wr.reset()
    yield
    wr.reset()


# ── 1. what counts as a claim (lexicon DATA, per language) ───────────────────────────────────────────────────────────

CLAIMS_EN = [
    ("I've added a work meeting to your calendar for tomorrow at 9 a.m.", ("calendar",)),
    ("I added oat milk to your shopping list.", ("list",)),
    ("Okay, I've put the paint on your to-do list.", ("list",)),
    ("I've set a reminder for five o'clock.", ("reminder",)),
    ("I've set a timer for ten minutes.", ("timer",)),
    ("Done, I have scheduled the physio for Friday.", ("calendar",)),
    ("Your dentist booking has been added to the calendar.", ("calendar",)),
    ("Added kettle to the shopping list.", ("list",)),
    ("I've made a note of that.", ("note",)),
    ("I've added it.", ()),
    # review (#2004, P1): the ordinary passive of a completed write
    ("The appointment was added to your calendar.", ("calendar",)),
    ("The dentist appointment on Friday at three o'clock was added.", ("calendar",)),
    ("The milk has now been added to your shopping list.", ("list",)),
    ("Milk was successfully added to your shopping list.", ("list",)),
    ("The reminder was set for five o'clock.", ("reminder",)),
    ("It was added.", ()),
    ("Oat milk is added to your shopping list.", ("list",)),
    ("Sure thing, I've added milk to your shopping list.", ("list",)),
    # review (#2004, P1): an ordinary modifier between the subject and the verb must not hide the claim
    ("I've definitely added milk to your shopping list.", ("list",)),
    ("I've really added milk to your shopping list.", ("list",)),
    ("I've just added milk to your shopping list.", ("list",)),
    ("I've successfully added milk to your shopping list.", ("list",)),
    ("I have now set a reminder for five o'clock.", ("reminder",)),
    ("I've gone ahead and added milk to your shopping list.", ("list",)),
    ("I've gone ahead and just added milk to your shopping list.", ("list",)),
    ("I have now definitely gone ahead and added milk to your shopping list.", ("list",)),
    ("I've happily scheduled the physio for Friday.", ("calendar",)),
    ("I've officially booked the dentist for Monday.", ("calendar",)),
    ("We've quietly added eggs to the shopping list.", ("list",)),
    ("I've only added milk to your shopping list.", ("list",)),
    ("Okay, I've totally saved a note about that.", ("note",)),
    ("The milk was definitely added to your shopping list.", ("list",)),
    ("The appointment has been happily booked in your calendar.", ("calendar",)),
    ("Definitely added the milk to your shopping list.", ("list",)),
]

NOT_CLAIMS_EN = [
    "I couldn't add that to your list.",
    "I didn't manage to add that; want me to try again?",
    "Do you want me to add oat milk to your shopping list?",
    "I'll add it to your list.",
    "Would you like me to schedule the physio?",
    "You added oat milk earlier.",
    "Your dentist appointment is scheduled for Friday.",       # describes the calendar, claims no write
    "Sure thing. What should I call the meeting?",
    "I've added Marcus to your contacts.",                      # not a kind this guard owns
    "I've turned the lights on.",
    "I've set the lights to blue.",
    "Did I add it to your list?",
    # review (#2004, P1) - the same passive, said honestly: a read of what is ALREADY there, or a denial
    "It is scheduled for Friday.",
    "The meeting is booked for Monday at nine.",
    "Your physio is scheduled for Friday at ten.",
    "The restaurant was fully booked.",
    "The restaurant was booked solid on Friday.",
    "The appointment wasn't added to your calendar.",
    "Nothing was added to your shopping list.",
    "Was the appointment added to your calendar?",
    "The chicken was set aside to cool.",
    # review (#2004, P1): widening for modifiers must not turn someone ELSE's write, or a name ending in -ly, into my claim
    "Emily added the milk to the shopping list.",
    "I think Mum added milk to the shopping list.",
    "I saw Dad really added milk to the shopping list.",
    "The kids definitely added milk to the shopping list.",
    "I've definitely not added milk to your shopping list.",
    "I've really been meaning to add milk to your shopping list.",
    "Have I definitely added milk to your shopping list?",
    "I will definitely add milk to your shopping list.",
]

CLAIMS_ES = [
    ("He añadido leche a tu lista de compras.", ("list",)),
    ("Ya agendé la cita en tu calendario.", ("calendar",)),
    ("Listo, programé una reunión para mañana.", ("calendar",)),
    ("He puesto un recordatorio para las cinco.", ("reminder",)),
]

NOT_CLAIMS_ES = [
    "No pude añadir eso a tu lista.",
    "¿Quieres que añada leche a tu lista?",
    "Voy a añadirlo a tu lista.",
]


@pytest.mark.parametrize("sentence,kinds", CLAIMS_EN)
def test_english_claims_are_recognised(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None, sentence
    assert claim.lang == "en" and claim.kinds == kinds


@pytest.mark.parametrize("sentence", NOT_CLAIMS_EN)
def test_english_non_claims_are_not_claims(sentence):
    assert g.find_claim(sentence) is None, sentence


@pytest.mark.parametrize("sentence,kinds", CLAIMS_ES)
def test_spanish_claims_are_recognised(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None, sentence
    assert claim.lang == "es" and claim.kinds == kinds
    assert "añadir" in claim.honest or "intente" in claim.honest      # the honest line is Spanish too


@pytest.mark.parametrize("sentence", NOT_CLAIMS_ES)
def test_spanish_non_claims_are_not_claims(sentence):
    assert g.find_claim(sentence) is None, sentence


def test_a_new_language_is_data_not_code():
    """The claim vocabulary lives in lexicons_data/write_claims.json: it is valid JSON, carries en + es, every enabled
    language names claim verbs, targets and an honest line - and no English claim word is compiled into the module."""
    data = json.loads(g.DATA_PATH.read_text(encoding="utf-8"))
    for code in ("en", "es"):
        lex = data[code]
        assert lex["enabled"] and lex["claim_verbs"] and lex["targets"] and lex["honest"]
    src = open(g.__file__, encoding="utf-8").read()
    assert "added" not in src.replace("write_claims", "") or '"added"' not in src


# ── 2. the verdict: a claim needs a receipt of its kind since the turn began ─────────────────────────────────────────

CALENDAR_CLAIM = "I've added a work meeting to your calendar for tomorrow at 9 a.m."


def test_guard_text_blocks_a_claim_with_no_write(caplog):
    caplog.set_level(logging.WARNING, logger="write_claim_guard")
    started = wr.begin()
    out = g.guard_text(CALENDAR_CLAIM, "jason", started)
    assert out == HONEST_EN
    assert "WRITE_CLAIM_BLOCKED" in caplog.text and "kinds=calendar" in caplog.text
    assert "work meeting" not in caplog.text            # household text never reaches the log


def test_guard_text_allows_a_claim_backed_by_a_write_this_turn():
    started = wr.begin()
    wr.record("jason", "calendar", "evt-1", "test")
    assert g.guard_text(CALENDAR_CLAIM, "jason", started) == CALENDAR_CLAIM


MODIFIED_CLAIMS = [
    ("I've definitely added milk to your shopping list.", "list"),
    ("I've really added milk to your shopping list.", "list"),
    ("I've just added milk to your shopping list.", "list"),
    ("I've successfully added milk to your shopping list.", "list"),
    ("I have now set a reminder for five o'clock.", "reminder"),
    ("I've gone ahead and added milk to your shopping list.", "list"),
]


@pytest.mark.parametrize("sentence,kind", MODIFIED_CLAIMS)
def test_a_modified_claim_with_no_write_is_blocked(sentence, kind):
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) == HONEST_EN


@pytest.mark.parametrize("sentence,kind", MODIFIED_CLAIMS)
def test_a_modified_claim_with_a_real_receipt_is_spoken_as_is(sentence, kind):
    started = wr.begin()
    wr.record("jason", kind, "row-1", "test")
    assert g.guard_text(sentence, "jason", started) == sentence


@pytest.mark.parametrize("sentence", [
    "Your shopping list has milk, eggs and bread on it.",
    "You've really got milk and eggs on your shopping list.",
    "Milk is definitely on your shopping list.",
    "Your dentist appointment is definitely scheduled for Friday.",
    "Emily added the milk to the shopping list.",
])
def test_an_honest_read_back_is_never_blocked(sentence):
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) == sentence


# ── final-round review (#2004): every claim clause needs its receipt; a tag question hides nothing; "you" is only the writer
# when it is the subject; a bare participle that opens a sentence is a claim only as a completion ellipsis ───────────────

TWO_CLAIMS = "I've added milk to your shopping list, and then I've scheduled the dentist on your calendar."


def test_every_claim_clause_in_a_unit_is_found_and_needs_its_own_receipt():
    claim = g.find_claim(TWO_CLAIMS)
    assert claim is not None and set(claim.kinds) == {"list", "calendar"}, claim
    started = wr.begin()
    wr.record("jason", "list", "li-1", "test")
    assert g.guard_text(TWO_CLAIMS, "jason", started) == HONEST_EN       # the calendar half has no receipt
    wr.record("jason", "calendar", "ev-1", "test")
    assert g.guard_text(TWO_CLAIMS, "jason", started) == TWO_CLAIMS      # both halves backed: spoken as-is


@pytest.mark.parametrize("sentence,kinds", [
    ("I've added milk to your shopping list; I've set a timer for ten minutes.", {"list", "timer"}),
    ("I've added milk to your shopping list, so I've booked the dentist in your calendar.", {"list", "calendar"}),
    ("The milk was added to your shopping list and the dentist was added to your calendar.", {"list", "calendar"}),
    ("I've added milk to your shopping list, and I've added it.", {"list"}),     # a generic claim adds nothing the list receipt lacks
])
def test_a_second_claim_clause_widens_the_kinds_needed(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None and set(claim.kinds) == kinds, (sentence, claim)


@pytest.mark.parametrize("sentence", [
    "I've added milk to your shopping list, okay?",
    "I've added milk to your shopping list, right?",
    "The appointment was added to your calendar, right?",
    "I've set a reminder for five o'clock, yeah?",
    "I've added milk to your shopping list, all right?",
    "He añadido leche a tu lista de compras, ¿vale?",
])
def test_a_trailing_tag_question_does_not_exempt_the_claim(sentence):
    started = wr.begin()
    assert g.find_claim(sentence) is not None, sentence
    assert g.guard_text(sentence, "jason", started) != sentence


def test_a_tag_question_claim_with_a_real_receipt_is_spoken():
    started = wr.begin()
    wr.record("jason", "list", "li-1", "test")
    s = "I've added milk to your shopping list, okay?"
    assert g.guard_text(s, "jason", started) == s


@pytest.mark.parametrize("sentence", [
    "Did I add it to your list?",
    "Have I added milk to your shopping list, okay?",
    "Have you added milk to your shopping list, right?",
    "Do you want me to add oat milk to the shopping list, okay?",
    "Should I schedule the dentist for Friday, right?",
    "Was the appointment added to your calendar, do you know?",
    "Is the milk on your shopping list?",
    "¿Quieres que añada leche a tu lista, vale?",
    "¿He añadido leche a tu lista?",
])
def test_a_genuine_question_is_still_never_a_claim(sentence):
    assert g.find_claim(sentence) is None, sentence


@pytest.mark.parametrize("sentence,kind", [
    ("The appointment you requested has been added to your calendar.", "calendar"),
    ("The milk you mentioned has been added to your shopping list.", "list"),
    ("The reminder you asked for was added to your calendar.", "calendar"),
])
def test_a_passive_with_a_second_person_relative_clause_is_a_claim(sentence, kind):
    claim = g.find_claim(sentence)
    assert claim is not None and kind in claim.kinds, sentence
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) == HONEST_EN
    wr.record("jason", kind, "row-1", "test")
    assert g.guard_text(sentence, "jason", started) == sentence


@pytest.mark.parametrize("sentence", [
    "You added oat milk earlier.",
    "I think you've added milk to the shopping list.",
    "You asked me whether the milk you added is still on your shopping list.",
    "Since you added the dentist to your calendar, you are all set.",
])
def test_you_as_the_writer_still_exempts_an_active_claim(sentence):
    assert g.find_claim(sentence) is None, sentence


@pytest.mark.parametrize("sentence", [
    "Scheduled maintenance starts at 2 PM.",
    "Added sugar is unhealthy.",
    "Booked flights are cheaper on Tuesdays.",
    "Added sugar is bad for you, so check the label.",
    "Scheduled meetings are shown on the calendar.",
    "Saved searches appear in your account.",
])
def test_a_sentence_opening_participle_used_as_an_adjective_is_not_a_claim(sentence):
    assert g.find_claim(sentence) is None, sentence
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) == sentence


@pytest.mark.parametrize("sentence", [
    "Added the milk.",
    "Done - added it.",
    "Added milk to the shopping list.",
    "Added it.",
    "Scheduled the dentist for Friday.",
    "Added those to your calendar.",
])
def test_a_completion_ellipsis_is_still_a_claim(sentence):
    claim = g.find_claim(sentence)
    assert claim is not None, sentence
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) == HONEST_EN


def test_a_write_before_the_turn_began_does_not_back_a_claim():
    wr.record("jason", "calendar", "evt-0", "test")
    started = wr.begin()
    assert g.guard_text(CALENDAR_CLAIM, "jason", started) == HONEST_EN


def test_a_write_for_another_user_does_not_back_a_claim():
    started = wr.begin()
    wr.record("sam", "calendar", "evt-1", "test")
    assert g.guard_text(CALENDAR_CLAIM, "jason", started) == HONEST_EN


def test_the_receipt_must_match_the_kind_claimed():
    started = wr.begin()
    wr.record("jason", "list", "item-1", "test")        # a LIST item landed ...
    assert g.guard_text(CALENDAR_CLAIM, "jason", started) == HONEST_EN       # ... that is not a calendar event
    assert g.guard_text("I added oat milk to your shopping list.", "jason", started) == "I added oat milk to your shopping list."


def test_calendar_and_reminder_receipts_back_each_other():
    started = wr.begin()
    wr.record("jason", "reminder", "rem-1", "test")
    assert g.guard_text(CALENDAR_CLAIM, "jason", started) == CALENDAR_CLAIM


def test_a_claim_with_no_target_is_backed_by_a_receipt_and_only_a_receipt():
    """Review (#2004, P1): a tool NAME is not evidence - the start/args sentinel arrives before the tool finishes, and a tool that
    then FAILED (or an unrelated non-read tool) must not back "I've added it". Only a receipt does."""
    started = wr.begin()
    assert g.guard_text("I've added it.", "jason", started) == HONEST_EN
    assert "tools_run" not in g.guard_text.__code__.co_varnames and "tools_run" not in g.supported.__code__.co_varnames
    wr.record("jason", "note", "n-1", "test")
    assert g.guard_text("I've added it.", "jason", started) == "I've added it."
    assert g.guard_text("I've added it.", "someone-else", started) == HONEST_EN


def test_only_the_claiming_sentence_is_replaced_and_only_once():
    started = wr.begin()
    reply = "Sure thing. I've added a work meeting to your calendar. I've scheduled the physio for Friday. Anything else?"
    assert g.guard_text(reply, "jason", started) == f"Sure thing. {HONEST_EN} Anything else?"


def test_a_recap_question_may_stand_on_a_recent_write():
    wr.record("jason", "list", "item-1", "test")
    started = wr.begin() + 0.001                       # the write is BEFORE this turn ...
    recap = "Yes, I added oat milk to your shopping list."
    assert g.guard_text(recap, "jason", started, user_text="Did you add the oat milk?") == recap
    assert g.guard_text(recap, "jason", started, user_text="Tell me a joke") == HONEST_EN


def test_mode_off_and_shadow(monkeypatch, caplog):
    started = wr.begin()
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "off")
    assert g.guard_text(CALENDAR_CLAIM, "jason", started) == CALENDAR_CLAIM
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "shadow")
    caplog.set_level(logging.WARNING, logger="write_claim_guard")
    assert g.guard_text(CALENDAR_CLAIM, "jason", started) == CALENDAR_CLAIM          # logged, not changed
    assert "WRITE_CLAIM_BLOCKED action=shadow" in caplog.text


def test_spanish_claim_is_blocked_with_a_spanish_honest_line_and_allowed_with_a_receipt():
    started = wr.begin()
    out = g.guard_text("He añadido leche a tu lista de compras.", "jason", started)
    assert out == "No logré añadir eso; ¿quieres que lo intente otra vez?"
    wr.record("jason", "list", "item-1", "test")
    assert g.guard_text("He añadido leche a tu lista de compras.", "jason", started) == "He añadido leche a tu lista de compras."


# ── 3. the writers record receipts ───────────────────────────────────────────────────────────────────────────────────

class _FakeDB:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))

        class _Cur:
            async def fetchone(self_inner):
                return None

            async def fetchall(self_inner):
                return []

        return _Cur()


@pytest.mark.asyncio
async def test_create_event_record_records_a_receipt():
    from calendar_service import create_event_record

    started = wr.begin()
    record = await create_event_record(_FakeDB(), user_id="jason", title="Pottery", start_date="2099-01-02")
    receipts = wr.since("jason", started, ("calendar",))
    assert [r.row_id for r in receipts] == [record["id"]]


@pytest.mark.asyncio
async def test_add_item_to_list_records_a_receipt_for_new_and_replayed_adds(monkeypatch):
    import list_service

    started = wr.begin()

    async def fake_find(db, *, user_id, list_type, name):
        return "list-1"

    async def no_dup(db, *, list_id, text, window_s):
        return None

    monkeypatch.setattr(list_service, "find_list_id", fake_find)
    monkeypatch.setattr(list_service, "_find_recent_duplicate", no_dup)
    out = await list_service.add_item_to_list(_FakeDB(), user_id="jason", list_type="shopping", list_name="Shopping",
                                              text="oat milk", new_list_visibility="family", dedup_window_s=10)
    assert [r.row_id for r in wr.since("jason", started, ("list",))] == [out["item_id"]]

    wr.reset()
    started = wr.begin()

    async def a_dup(db, *, list_id, text, window_s):
        return "item-old"

    monkeypatch.setattr(list_service, "_find_recent_duplicate", a_dup)
    out = await list_service.add_item_to_list(_FakeDB(), user_id="jason", list_type="shopping", list_name="Shopping",
                                              text="oat milk", new_list_visibility="family", dedup_window_s=10)
    assert out["deduped"] is True
    assert wr.has("jason", started, "list")             # the item IS on the list, so saying so is true


def test_the_timer_store_records_a_receipt():
    import skybridge_service as sky

    started = wr.begin()
    sky._timers.create("jason", "pasta", 600)
    assert wr.has("jason", started, "timer")


def test_receipts_are_bounded():
    for i in range(wr._MAX_PER_USER + 20):
        wr.record("jason", "list", str(i), "test")
    assert len(wr.since("jason", 0.0)) == wr._MAX_PER_USER


# ── 4. the stream guard ──────────────────────────────────────────────────────────────────────────────────────────────

async def _collect(agen):
    return [d async for d in agen]


def _stream(*deltas):
    async def gen():
        for d in deltas:
            yield d

    return gen()


@pytest.mark.asyncio
async def test_stream_replaces_an_unsupported_claim_across_delta_boundaries():
    started = wr.begin()
    pieces = ["Sure", " thing. ", "I'", "ve add", "ed a work meet", "ing to your calendar for tomorrow at 9 a.m.", " Anything else?"]
    out = await _collect(g.guard_stream(_stream(*pieces), "jason", started))
    assert "".join(out) == f"Sure thing. {HONEST_EN} Anything else?"


@pytest.mark.asyncio
async def test_stream_lets_a_supported_claim_through_in_its_original_pieces():
    started = wr.begin()
    wr.record("jason", "calendar", "evt-1", "test")
    pieces = ["I'", "ve add", "ed a work meeting", " to your calendar. ", "Done."]
    assert await _collect(g.guard_stream(_stream(*pieces), "jason", started)) == pieces


@pytest.mark.asyncio
async def test_stream_leaves_an_ordinary_reply_byte_identical_and_unheld():
    started = wr.begin()
    pieces = ["The ", "weather ", "in Perth ", "is ", "24 degrees ", "and sunny. ", "Enjoy the beach."]
    assert await _collect(g.guard_stream(_stream(*pieces), "jason", started)) == pieces


@pytest.mark.asyncio
async def test_stream_releases_a_unit_the_moment_its_first_lead_words_hold_no_claim_verb():
    """First-audio protection: a unit that has run LEAD_TOKENS words with no claim verb can never become a claim (a claim verb
    must sit within those words), so everything from there on is released at once - without waiting for the unit to end."""
    started = wr.begin()
    seen: list = []
    opening = "The weather in Perth this afternoon is twenty four degrees and mostly "

    async def inner():
        yield opening
        seen.append("after first delta")
        yield "sunny with a light breeze from the south west. "

    out = []
    async for piece in g.guard_stream(inner(), "jason", started):
        out.append(piece)
        if piece == opening:
            assert seen == []                 # released BEFORE the next delta was even requested
    assert "".join(out) == opening + "sunny with a light breeze from the south west. "


@pytest.mark.asyncio
async def test_stream_holds_a_short_opening_until_it_is_known_not_to_be_a_claim():
    """The other side of the same bound: with fewer than LEAD_TOKENS words seen, a passive claim ("The appointment was added")
    could still follow, so nothing is released yet - and once the unit ends the whole reply is one clean sentence."""
    started = wr.begin()
    seen: list = []

    async def inner():
        yield "The weather "
        seen.append("second delta requested")
        yield "is fine. "

    out = []
    async for piece in g.guard_stream(inner(), "jason", started):
        out.append(piece)
        assert seen, "released before the unit's first lead words were in"
    assert out == ["The weather ", "is fine. "]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["add_to_list", "people", "add_calendar_event", "get_weather"])
async def test_stream_a_tool_that_started_but_wrote_nothing_does_not_back_a_generic_claim(tool):
    """Review (#2004, P1): phase=start / phase=args name the tool BEFORE it finishes. A tool that failed (no receipt) must not
    let "I've added it." through; the sentinels themselves still pass untouched."""
    started = wr.begin()
    sentinel = '__TOOL__:{"phase": "start", "id": "1", "name": "%s"}' % tool
    out = await _collect(g.guard_stream(_stream(sentinel, "I've added it."), "jason", started))
    assert out[0] == sentinel and "".join(out[1:]) == HONEST_EN


@pytest.mark.asyncio
async def test_stream_a_tool_that_started_and_really_wrote_backs_a_generic_claim():
    started = wr.begin()
    sentinel = '__TOOL__:{"phase": "start", "id": "1", "name": "add_to_list"}'

    async def inner():
        yield sentinel
        wr.record("jason", "list", "item-1", "tool")
        yield "I've added it."

    assert await _collect(g.guard_stream(inner(), "jason", started)) == [sentinel, "I've added it."]


@pytest.mark.asyncio
async def test_stream_writes_that_land_mid_stream_back_the_claim():
    """The tool runs BEFORE the final text: a receipt recorded after the stream began still backs the claim."""
    started = wr.begin()

    async def inner():
        yield "__TOOL__:{\"phase\": \"start\", \"id\": \"1\", \"name\": \"add_calendar_event\"}"
        wr.record("jason", "calendar", "evt-9", "tool")
        yield CALENDAR_CLAIM

    out = await _collect(g.guard_stream(inner(), "jason", started))
    assert out[-1] == CALENDAR_CLAIM


@pytest.mark.asyncio
async def test_stream_spanish():
    started = wr.begin()
    out = await _collect(g.guard_stream(_stream("He añadido ", "leche a tu lista de compras."), "jason", started))
    assert "".join(out) == "No logré añadir eso; ¿quieres que lo intente otra vez?"


# ── 4b. review (#2004): blockers are judged in the claim's own clause ───────────────────────────────────────────────────

CLAUSE_CLAIMS = [
    "I've added milk to your shopping list, so you won't forget it.",
    "I've added milk to your shopping list as you wanted.",
    "I've added milk to your list, but I'm not sure about eggs.",
    "I've added milk to your list, I'm not sure about eggs though.",
    "I've added milk to your shopping list; I couldn't find the eggs.",
    "I've added milk to your shopping list — not that you asked twice.",
    "Sure, I couldn't add eggs but I've added milk to your shopping list.",
    "He añadido leche a tu lista de compras, pero no estoy segura de los huevos.",
    "He añadido leche a tu lista de compras para que no lo olvides.",
]


@pytest.mark.parametrize("sentence", CLAUSE_CLAIMS)
def test_a_blocker_in_another_clause_does_not_exempt_a_claim(sentence):
    claim = g.find_claim(sentence)
    assert claim is not None, sentence
    assert claim.kinds == ("list",), (sentence, claim)
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) in (HONEST_EN, "No logré añadir eso; ¿quieres que lo intente otra vez?")


@pytest.mark.parametrize("sentence", [
    "I couldn't add that to your list, but I can try again.",
    "I'd add milk to your list, so just say the word.",
    "I haven't added milk to your shopping list, because I wasn't sure.",
    "I'll add it to your list as soon as you say.",
    "No pude añadir eso a tu lista, pero puedo intentarlo otra vez.",
])
def test_a_blocker_inside_the_claim_clause_still_exempts_it(sentence):
    assert g.find_claim(sentence) is None, sentence


def test_a_comma_inside_a_list_of_items_does_not_split_the_claim_clause():
    claim = g.find_claim("I've added milk, eggs and bread to your shopping list.")
    assert claim is not None and claim.kinds == ("list",)
    # the target noun in a LATER clause is not borrowed: this claim names no target of its own
    claim = g.find_claim("I've added milk, but the shopping list is long.")
    assert claim is not None and claim.kinds == ()


# ── 4c. review (#2004): streaming sees the same sentences however the deltas are cut ──────────────────────────────────

STREAM_CORPUS = [
    CALENDAR_CLAIM,
    "The weather is fine. I've added milk to your shopping list.",
    "The weather is fine.\nI've added milk to your shopping list.",
    "Fine. I've added milk to your shopping list, so you won't forget it. Anything else?",
    "Sure thing. I've added a work meeting to your calendar for tomorrow at 9 a.m. Anything else?",
    "Okay. I couldn't add that. Want me to try again?",
    "It's 24 degrees in Perth. Enjoy the beach!",
    "Done! I've scheduled the physio for Friday. I've put oat milk on your shopping list.",
    "He añadido leche a tu lista de compras. ¿Algo más?",
    "No pasa nada. Ya agendé la cita en tu calendario, para que no se te olvide.",
    # review (#2004, P1): a passive, and a claim that does not open its sentence, cut at every position
    "The appointment was added to your calendar. Anything else?",
    "Your dentist booking has been added to the calendar.",
    "Sure thing, I've added milk to your shopping list.",
    "The milk and the eggs and the bread were all added to your shopping list. Anything else?",
    "Okay, so I understand that you would like a meeting tomorrow at nine, so I've added it to your calendar.",
    "The weather in Perth this afternoon is twenty four degrees and mostly sunny, and I've added the beach to nothing. Enjoy!",
    "Your physio is scheduled for Friday at ten. The restaurant was fully booked. Nothing was added to your list.",
    "One two three four five six seven eight nine ten eleven twelve I've added milk to your shopping list.",
    "I went through everything on your list this morning and it all looks fine, the milk was added to your shopping list.",
    # review (#2004, P1): modifiers before the verb are judged the same however the text is cut
    "Okay. I've definitely added milk to your shopping list. Anything else?",
    "I have now definitely gone ahead and added milk to your shopping list, so that is sorted.",
    "Emily added the milk to the shopping list. I think Mum really added eggs too.",
    # final round (#2004): several claim clauses, a tag question, a relative clause, a bare participle
    TWO_CLAIMS,
    "I've added milk to your shopping list, okay? Anything else?",
    "The appointment you requested has been added to your calendar. Anything else?",
    "Scheduled maintenance starts at 2 PM. Added sugar is unhealthy. Added the milk.",
    # follow-ups to #2004: a trailing temporal clause, a dash / colon tag, each cut at every position
    "I've added milk to your shopping list, after you asked me to. Anything else?",
    "I've added milk to your shopping list once you asked me to.",
    "I'll add milk to your shopping list after you confirm. When I've added it, I'll say so.",
    "Okay. The appointment was added to your calendar when you confirmed it. Anything else?",
    "I've added milk to your shopping list \u2014 okay? Anything else?",
    "I've added milk to your shopping list - right?",
    "I've added milk to your shopping list: okay?",
    "I've added milk to your list after I've scheduled the physio. I'll tell you when I've set a reminder.",
    "I've added milk to your list, once I've scheduled the physio. Anything else?",
    "I've put the dentist after lunch in your calendar. Anything else?",
    "I'll put the dentist after lunch in your calendar. Milk is added to your shopping list after lunch.",
    "Okay, have I added milk to your shopping list \u2014 right? I've added eggs to your shopping list - okay?",
    "I'll tell you after I've scheduled the physio, then I've added milk to your list when I've set a reminder.",
    "Should I add eggs \u2014 okay? I've added milk to your shopping list \u2013 all right?",
]


def _stream_text(reply, cuts, user="jason", started=None):
    import asyncio

    pieces, prev = [], 0
    for c in list(cuts) + [len(reply)]:
        pieces.append(reply[prev:c])
        prev = c
    pieces = [p for p in pieces if p]

    async def run():
        return "".join([d async for d in g.guard_stream(_stream(*pieces), user, started)])

    return asyncio.new_event_loop().run_until_complete(run())


@pytest.mark.parametrize("reply", STREAM_CORPUS)
def test_stream_equals_the_whole_reply_guard_at_every_single_cut(reply):
    started = wr.begin()
    want = g.guard_text(reply, "jason", started)
    for cut in range(1, len(reply)):
        assert _stream_text(reply, [cut], started=started) == want, (reply, cut, reply[:cut], reply[cut:])


@pytest.mark.parametrize("reply", STREAM_CORPUS)
def test_stream_equals_the_whole_reply_guard_char_by_char_and_word_by_word(reply):
    started = wr.begin()
    want = g.guard_text(reply, "jason", started)
    assert _stream_text(reply, range(1, len(reply)), started=started) == want
    word_cuts = [m.end() for m in re.finditer(r"\S+\s*", reply)][:-1]
    assert _stream_text(reply, word_cuts, started=started) == want


def test_a_claim_after_a_long_run_up_is_still_caught_in_its_own_clause():
    """A claim verb must sit within LEAD_TOKENS words of its UNIT - and a long sentence is cut into units at the first comma after
    that many words, so a chatty run-up does not hide a claim (the run-up is kept, the claiming clause replaced)."""
    started = wr.begin()
    reply = "Okay, so I understand that you would like a meeting tomorrow at nine, so I've added it to your calendar."
    out = g.guard_text(reply, "jason", started)
    assert out == f"Okay, so I understand that you would like a meeting tomorrow at nine, {HONEST_EN}"
    wr.record("jason", "calendar", "evt", "t")
    assert g.guard_text(reply, "jason", started) == reply
    # the unit bound: a verb 13+ words into a comma-less run-on is beyond what streaming can hold for (documented limit)
    assert g.find_claim("One two three four five six seven eight nine ten eleven twelve I've added milk to your list.") is None


_FUZZ_PHRASES = [
    "Sure thing,", "Okay.", "I've added milk to your shopping list.", "The appointment was added to your calendar.",
    "Your dentist booking has been added to the calendar.", "The weather is fine today in Perth and the sun is out,",
    "It is scheduled for Friday.", "Nothing was added to your list.", "I couldn't add that.", "Done!", "Anything else?",
    "I've scheduled the physio for Friday at 9 a.m. tomorrow morning.", "so I've put oat milk on your list", "and then",
    "The restaurant was fully booked.", "one two three four five six seven eight nine ten eleven twelve thirteen,",
    "He añadido leche a tu lista de compras.", "Ya agendé la cita en tu calendario,", "You added it earlier.",
    "Oat milk is added to your shopping list.", "after you asked me to.", "after I've scheduled the physio.", "once I've set a reminder,", "after lunch in your calendar.", "Okay, have I added it \u2014 right?", "I'll add it once you confirm.", "Milk is added when you tap Done,",
    "I've added milk to your shopping list - okay?", "The appointment was added to your calendar \u2014 right?", "Which items were added to your shopping list yesterday evening for the party?",
]


def test_stream_equals_the_whole_reply_guard_for_random_replies_cut_at_random_places():
    """Property check over generated replies: however the deltas are cut (1-6 random cuts, plus char-by-char), the stream is
    byte-identical to the whole-reply guard - with and without a receipt."""
    import random

    rng = random.Random(20261013)
    for case in range(120):
        reply = " ".join(rng.choice(_FUZZ_PHRASES) for _ in range(rng.randint(1, 5)))
        wr.reset()
        started = wr.begin()
        if case % 3 == 0:
            wr.record("jason", "list", "i", "t")
            wr.record("jason", "calendar", "c", "t")
        want = g.guard_text(reply, "jason", started)
        cut_sets = [sorted(rng.sample(range(1, len(reply)), min(len(reply) - 1, rng.randint(1, 6)))) for _ in range(3)]
        cut_sets.append(range(1, len(reply)))
        for cuts in cut_sets:
            assert _stream_text(reply, cuts, started=started) == want, (reply, list(cuts))


def test_a_punctuation_only_delta_keeps_the_sentence_boundary():
    """Review (#2004): 'The weather is fine.' then ' I've added milk…' used to pass the claim through unchanged."""
    started = wr.begin()
    out = "".join(_collect_sync(g.guard_stream(_stream("The weather is fine", ".", " I've added milk to your shopping list."),
                                              "jason", started)))
    assert out == f"The weather is fine. {HONEST_EN}"
    out = "".join(_collect_sync(g.guard_stream(_stream("The weather is fine.", " I've added milk to your shopping list."),
                                              "jason", started)))
    assert out == f"The weather is fine. {HONEST_EN}"


def _collect_sync(agen):
    import asyncio

    async def run():
        return [d async for d in agen]

    return asyncio.new_event_loop().run_until_complete(run())


# ── 5. wired at the one place every brain turn ends ──────────────────────────────────────────────────────────────────

@pytest.fixture
def core_lane(monkeypatch):
    import zoe_core_client

    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    monkeypatch.delenv("ZOE_BRAIN_BACKEND", raising=False)
    box = {"text": CALENDAR_CLAIM, "write": False}

    async def fake_stream(msg, sid, uid="", **kw):
        if box["write"]:
            wr.record(uid, "calendar", "evt-1", "tool")
        yield box["text"]

    async def fake_oneshot(msg, sid, uid="", **kw):
        if box["write"]:
            wr.record(uid, "calendar", "evt-1", "tool")
        return box["text"]

    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", fake_stream)
    monkeypatch.setattr(zoe_core_client, "run_zoe_core", fake_oneshot)
    return box


@pytest.mark.asyncio
async def test_brain_streaming_blocks_a_narrated_write(core_lane, caplog):
    caplog.set_level(logging.WARNING, logger="write_claim_guard")
    out = "".join(await _collect(bd.brain_streaming("Work.", "s", "jason")))
    assert out == HONEST_EN
    assert "WRITE_CLAIM_BLOCKED" in caplog.text


@pytest.mark.asyncio
async def test_brain_streaming_allows_a_real_write(core_lane):
    core_lane["write"] = True
    assert "".join(await _collect(bd.brain_streaming("Work.", "s", "jason"))) == CALENDAR_CLAIM


@pytest.mark.asyncio
async def test_brain_oneshot_blocks_a_narrated_write_and_allows_a_real_one(core_lane):
    assert await bd.brain_oneshot("Work.", "s", "jason") == HONEST_EN
    core_lane["write"] = True
    assert await bd.brain_oneshot("Work.", "s", "jason") == CALENDAR_CLAIM


@pytest.mark.asyncio
async def test_replay_isolation_is_never_guarded(core_lane):
    """The replay harness reports a write as done without committing it: its replies have no receipts by design."""
    inner = _stream(CALENDAR_CLAIM)
    assert bd._claim_guarded(inner, "x", "jason", {"replay_isolation": True}) is inner
    assert bd._claim_guarded_text(CALENDAR_CLAIM, "x", "jason", 0.0, {"replay_isolation": True}) == CALENDAR_CLAIM


@pytest.mark.asyncio
async def test_guard_kill_switch_restores_the_old_stream(core_lane, monkeypatch):
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "off")
    assert "".join(await _collect(bd.brain_streaming("Work.", "s", "jason"))) == CALENDAR_CLAIM


# ── 6. receipts belong to their turn (review, #2004, P2) ─────────────────────────────────────────────────────────────────

def test_a_concurrent_turns_write_does_not_back_this_turns_claim():
    """A panel turn and a chat turn for the SAME user both begin; the chat turn's calendar write must not vouch for the panel
    turn's narrated one. Each turn runs in its own task context, as two requests do."""
    import contextvars

    box = {}

    def panel_turn_begins():
        box["panel"] = wr.begin()

    def chat_turn_begins_and_writes():
        box["chat"] = wr.begin()
        wr.record("jason", "calendar", "evt-chat", "calendar_service")      # made INSIDE the chat turn's context

    contextvars.copy_context().run(panel_turn_begins)
    contextvars.copy_context().run(chat_turn_begins_and_writes)
    assert box["panel"].turn != box["chat"].turn
    assert g.guard_text(CALENDAR_CLAIM, "jason", box["chat"]) == CALENDAR_CLAIM        # its own write backs it
    assert g.guard_text(CALENDAR_CLAIM, "jason", box["panel"]) == HONEST_EN            # ... and is invisible to the other turn
    assert wr.has("jason", box["panel"], "calendar") is False and wr.has("jason", box["chat"], "calendar") is True


def test_a_write_with_no_turn_context_still_counts_for_every_turn_of_the_user():
    """The brain's tool calls reach zoe-data as a separate HTTP request - no turn context. Counting such a receipt for the
    user's turns is the safe direction: it can never block a TRUE claim."""
    import contextvars

    box = {}
    contextvars.copy_context().run(lambda: box.update(a=wr.begin()))
    contextvars.copy_context().run(lambda: box.update(b=wr.begin()))
    contextvars.Context().run(lambda: wr.record("jason", "calendar", "evt-dispatch", "intent-dispatch"))   # an empty context
    assert g.guard_text(CALENDAR_CLAIM, "jason", box["a"]) == CALENDAR_CLAIM
    assert g.guard_text(CALENDAR_CLAIM, "jason", box["b"]) == CALENDAR_CLAIM


def test_a_recap_question_may_stand_on_an_earlier_turns_write():
    import contextvars

    box = {}
    contextvars.copy_context().run(lambda: (box.update(old=wr.begin()), wr.record("jason", "list", "i-1", "t")))
    started = contextvars.copy_context().run(wr.begin)
    recap = "Yes, I added oat milk to your shopping list."
    assert g.guard_text(recap, "jason", started, user_text="Did you add the oat milk?") == recap
    assert g.guard_text(recap, "jason", started, user_text="Tell me a joke") == HONEST_EN


@pytest.mark.asyncio
async def test_the_stream_guard_uses_the_turns_own_receipts():
    import contextvars

    box = {}

    def other_turn():
        box["other"] = wr.begin()
        wr.record("jason", "calendar", "evt-x", "t")

    mine = contextvars.copy_context().run(wr.begin)
    contextvars.copy_context().run(other_turn)
    out = await _collect(g.guard_stream(_stream(CALENDAR_CLAIM), "jason", mine))
    assert "".join(out) == HONEST_EN
    out = await _collect(g.guard_stream(_stream(CALENDAR_CLAIM), "jason", box["other"]))
    assert "".join(out) == CALENDAR_CLAIM


def test_receipts_stay_per_user_across_turns():
    box = {}
    import contextvars

    contextvars.copy_context().run(lambda: (box.update(t=wr.begin()), wr.record("someone-else", "calendar", "e", "t")))
    assert g.guard_text(CALENDAR_CLAIM, "jason", box["t"]) == HONEST_EN


@pytest.mark.asyncio
async def test_text_held_back_is_not_lost_when_the_lane_fails_mid_stream():
    """The guard holds a unit's first words; if the lane dies after them (failover / transport error), what was generated is
    still released - judged - before the error propagates, exactly as an unguarded stream would have delivered it."""
    started = wr.begin()

    async def inner():
        yield "Your next meeting is "
        raise RuntimeError("lane died")

    got = []
    with pytest.raises(RuntimeError):
        async for piece in g.guard_stream(inner(), "jason", started):
            got.append(piece)
    assert "".join(got) == "Your next meeting is "

    async def claiming():
        yield "I've added milk to your shopping list"
        raise RuntimeError("lane died")

    got = []
    with pytest.raises(RuntimeError):
        async for piece in g.guard_stream(claiming(), "jason", started):
            got.append(piece)
    assert "".join(got) == HONEST_EN                      # an unfinished claim is still judged, not leaked


# ── 5. follow-ups to #2004 (declined in review, done here) ─────────────────────────────────────────────────────────────
# Each group is pinned in BOTH directions: the claim is still caught (and spoken when a receipt backs it), and the honest
# sentence of the same shape is never blocked.
# Negative controls (run by hand, each went red): emptying ``clause_temporals`` in write_claims.json fails the temporal-tail claim
# tests; deleting the ``temporal_tail and aux in lang.passive_state`` skip fails the honest "is added once you tap Done" case;
# reducing ``_TAG_SEP_RE`` to ``[,;]`` fails the dash / colon tag tests.

HONEST_ES = "No logré añadir eso; ¿quieres que lo intente otra vez?"

TEMPORAL_TAIL_CLAIMS = [
    ("I've added milk to your shopping list, after you asked me to.", ("list",)),
    ("I've added milk to your shopping list after you asked me to.", ("list",)),
    ("I've added milk to your shopping list when you asked.", ("list",)),
    ("I've added milk to your shopping list once you confirmed it.", ("list",)),
    ("I've added milk to your shopping list before the shop opens.", ("list",)),
    ("I've scheduled the physio for Friday, after you asked me to.", ("calendar",)),
    ("The appointment was added to your calendar after you confirmed.", ("calendar",)),
    ("I've added it, when you asked me to.", ()),
    ("He añadido leche a tu lista de compras después de que lo pidieras.", ("list",)),
    ("He añadido leche a tu lista de compras, cuando lo pediste.", ("list",)),
]


@pytest.mark.parametrize("sentence,kinds", TEMPORAL_TAIL_CLAIMS)
def test_a_trailing_temporal_clause_does_not_disable_a_completed_claim(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None and claim.kinds == kinds, (sentence, claim)
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) in (HONEST_EN, HONEST_ES)


@pytest.mark.parametrize("sentence,kind", [
    ("I've added milk to your shopping list, after you asked me to.", "list"),
    ("I've added milk to your shopping list when you asked.", "list"),
    ("I've scheduled the physio for Friday once you confirmed it.", "calendar"),
])
def test_a_temporal_tail_claim_with_a_real_receipt_is_spoken_as_is(sentence, kind):
    started = wr.begin()
    wr.record("jason", kind, "row-1", "test")
    assert g.guard_text(sentence, "jason", started) == sentence


def test_a_temporal_tail_does_not_hide_a_second_claim_or_a_promise():
    # the claim clause is real; the later clause is a promise and stays exempt, so the sentence is one claim, not two
    claim = g.find_claim("I've added milk to your shopping list after you asked me to, and I'll add eggs once you confirm.")
    assert claim is not None and claim.kinds == ("list",)
    # a later claim clause behind a temporal tail still needs its own receipt
    claim = g.find_claim("I've added milk to your list after you asked, and I've scheduled the physio.")
    assert claim is not None and sorted(claim.kinds) == ["calendar", "list"]


NOT_CLAIMS_TEMPORAL = [
    "I'll add milk to your shopping list after you confirm.",
    "I'll have added milk to your shopping list once you confirm.",
    "When I've added milk to your shopping list, I'll let you know.",
    "Once I've added milk to your shopping list I'll tell you.",
    "Before I've added milk to your shopping list I need a quantity.",
    "After I've added milk to your shopping list, I'll set a reminder.",
    "I'll tell you when I've added milk to your shopping list.",
    "I'll let you know once I've added it.",
    "I haven't added milk to your shopping list after you asked.",
    "I've not added milk to your shopping list when you asked me to.",
    "Nothing was added to your shopping list after you asked.",
    "I couldn't add milk to your shopping list once you asked.",
]

# A present-tense passive tail describes a pending form, it does not report a write.
NOT_CLAIMS_TEMPORAL += [
    "Milk is added to your shopping list once you tap Done.",
    "The milk is added to your shopping list when you confirm.",
    # a temporal word with nothing after it ("once before") is still not a clause
    "I've added milk to your shopping list once before.",
    "No pude añadir leche a tu lista después de que lo pidieras.",
    "Añadiré leche a tu lista cuando me lo confirmes.",
]


@pytest.mark.parametrize("sentence", NOT_CLAIMS_TEMPORAL)
def test_an_honest_sentence_with_a_temporal_word_is_still_never_a_claim(sentence):
    assert g.find_claim(sentence) is None, sentence


DASH_TAG_CLAIMS = [
    "I've added milk to your shopping list — okay?",
    "I've added milk to your shopping list - right?",
    "I've added milk to your shopping list: okay?",
    "I've added milk to your shopping list – all right?",
    "I've added milk to your shopping list -- yeah?",
    "The appointment was added to your calendar — right?",
    "I've set a reminder for five o'clock - okay?",
    "He añadido leche a tu lista de compras — ¿vale?",
]


@pytest.mark.parametrize("sentence", DASH_TAG_CLAIMS)
def test_a_dash_or_colon_tag_question_does_not_exempt_the_claim(sentence):
    assert g.find_claim(sentence) is not None, sentence
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) != sentence
    for kind in ("list", "calendar", "reminder"):
        wr.record("jason", kind, kind + "-1", "t")
    assert g.guard_text(sentence, "jason", started) == sentence


@pytest.mark.parametrize("sentence", [
    "Did I add it to your list — okay?",
    "Have I added milk to your shopping list - right?",
    "Do you want me to add oat milk to the shopping list: okay?",
    "I've added milk — should I add eggs too?",
    "I've added milk to your shopping list - do you want eggs?",
    "Is the milk on your shopping list — yes?",
    "Shall I put the paint on your to-do list - okay?",
    "¿Quieres que añada leche a tu lista — vale?",
])
def test_a_genuine_question_with_a_dash_is_still_never_a_claim(sentence):
    assert g.find_claim(sentence) is None, sentence


def test_a_hyphen_inside_a_word_is_not_a_tag_separator():
    # "to-do" is one word; a hyphen with no spaces around it never cuts a question
    assert g.find_claim("Is the paint on your to-do list?") is None
    assert g.find_claim("Is that the to-do list right?") is None


# ── 5b. review of the follow-ups: a COMPLETED claim inside the split-off temporal clause is a claim of its own ─────────────
# "I've added milk to your list after I've scheduled the physio." was spoken verbatim with only a list receipt: the tail clause kept
# its leading "after", a blocker, and was skipped. The leading temporal word of a tail that follows a CLAIM is now stripped, so
# the tail's own claim needs its own receipt. A tail after a promise / denial stays blocked ("I'll tell you after I've added it").

TAIL_CLAIMS = [
    ("I've added milk to your list after I've scheduled the physio.", ("calendar", "list")),
    ("I've added milk to your list, once I've scheduled the physio.", ("calendar", "list")),
    ("I've added milk to your list when I've scheduled the physio.", ("calendar", "list")),
    ("I've added milk to your list before I've set a reminder.", ("list", "reminder")),
    ("The milk was added to your list after I scheduled the physio.", ("calendar", "list")),
    ("He añadido leche a tu lista después de que he programado la cita.", ("calendar", "list")),
]


@pytest.mark.parametrize("sentence,kinds", TAIL_CLAIMS)
def test_a_completed_claim_inside_a_temporal_tail_needs_its_own_receipt(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None and tuple(sorted(claim.kinds)) == kinds, (sentence, claim)
    started = wr.begin()
    wr.record("jason", "list", "l1", "t")                 # the FIRST claim's receipt alone must not vouch for the second
    assert g.guard_text(sentence, "jason", started) in (HONEST_EN, HONEST_ES), sentence
    for kind in kinds:
        wr.record("jason", kind, kind + "-2", "t")
    assert g.guard_text(sentence, "jason", started) == sentence


@pytest.mark.parametrize("sentence,kinds", [
    ("I've added milk to your list once you've scheduled the physio.", ("list",)),     # YOU did the second: not Zoe's write
    ("I've added milk to your list after you scheduled the physio.", ("list",)),
    ("I've added milk to your list after I asked.", ("list",)),                        # a tail with no claim verb of its own
])
def test_a_temporal_tail_that_claims_nothing_adds_no_kind(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None and tuple(sorted(claim.kinds)) == kinds, (sentence, claim)


@pytest.mark.parametrize("sentence", [
    "I'll tell you after I've scheduled the physio.",
    "I'll tell you when I've set a reminder.",
    "I haven't added milk to your list after I've scheduled the physio.",
    "When I've scheduled the physio, I'll tell you.",
    "I'll add milk to your list once I've scheduled the physio.",
    "Milk is added to your list after I've scheduled the physio.",
    "I couldn't add milk to your list before I've scheduled the physio.",
])
def test_a_temporal_tail_after_a_promise_or_denial_is_still_not_a_claim(sentence):
    assert g.find_claim(sentence) is None, sentence


# ── 5c. Codex review of #2021 ────────────────────────────────────────────────────────────────────────────────────────────────
# (1) A temporal word that introduces a PREPOSITIONAL time phrase ("after lunch", "before dinner") is not a clause: it is neither
# split off nor a blocker, so the claim keeps its target nouns. (2) A discourse opener ("Okay, ...") does not hide the
# interrogative of a genuine question that ends in a dash tag.

PREPOSITIONAL_TIME_CLAIMS = [
    ("I've put the dentist after lunch in your calendar.", ("calendar",)),
    ("I've added milk after work to your shopping list.", ("list",)),
    ("I've put the dentist in your calendar before dinner.", ("calendar",)),
    ("I've scheduled the physio for Friday after lunch.", ("calendar",)),
    ("I've put the paint on your to-do list when ready.", ("list",)),
    ("He puesto la cita después del almuerzo en tu calendario.", ("calendar",)),
]


@pytest.mark.parametrize("sentence,kinds", PREPOSITIONAL_TIME_CLAIMS)
def test_a_prepositional_time_phrase_keeps_the_claims_target(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None and tuple(sorted(claim.kinds)) == kinds, (sentence, claim)
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) in (HONEST_EN, HONEST_ES), sentence
    wr.record("jason", kinds[0], "row-1", "t")
    assert g.guard_text(sentence, "jason", started) == sentence


@pytest.mark.parametrize("sentence", [
    "I'll put the dentist after lunch in your calendar.",
    "I haven't put the dentist after lunch in your calendar.",
    "Do you want me to put the dentist after lunch in your calendar?",
    "Milk is added to your shopping list after lunch.",
    "The milk is added to your shopping list when done.",
    "After lunch I'll put the dentist in your calendar.",
    "Before dinner, I haven't added milk to your shopping list.",
    "I couldn't put the dentist in your calendar before dinner.",
])
def test_an_honest_sentence_with_a_prepositional_time_phrase_is_still_never_a_claim(sentence):
    assert g.find_claim(sentence) is None, sentence


DISCOURSE_QUESTIONS = [
    "Okay, have I added milk to your shopping list — right?",
    "So, did I add the dentist to your calendar - okay?",
    "Well, should I put the paint on your to-do list: right?",
    "Hmm, have I scheduled the physio for Friday – all right?",
    "Okay, so have I added milk to your shopping list — right?",
    "Bueno, ¿he añadido leche a tu lista — vale?",
]


@pytest.mark.parametrize("sentence", DISCOURSE_QUESTIONS)
def test_a_genuine_question_behind_a_discourse_opener_is_never_a_claim(sentence):
    assert g.find_claim(sentence) is None, sentence
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) == sentence


@pytest.mark.parametrize("sentence", [
    "Okay, I've added milk to your shopping list — right?",
    "So I've added milk to your shopping list - okay?",
    "Well, the appointment was added to your calendar: right?",
    "Okay, so I've scheduled the physio for Friday — okay?",
])
def test_a_claim_behind_a_discourse_opener_and_a_dash_tag_is_still_a_claim(sentence):
    assert g.find_claim(sentence) is not None, sentence
    started = wr.begin()
    assert g.guard_text(sentence, "jason", started) != sentence
