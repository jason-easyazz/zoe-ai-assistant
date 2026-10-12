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


def test_a_claim_with_no_target_is_backed_by_any_receipt_or_a_non_read_tool():
    started = wr.begin()
    assert g.guard_text("I've added it.", "jason", started) == HONEST_EN
    assert g.guard_text("I've added it.", "jason", started, tools_run=frozenset({"people"})) == "I've added it."
    assert g.guard_text("I've added it.", "jason", started, tools_run=frozenset({"get_weather"})) == HONEST_EN
    wr.record("jason", "note", "n-1", "test")
    assert g.guard_text("I've added it.", "jason", started) == "I've added it."


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
async def test_stream_does_not_hold_text_that_cannot_become_a_claim():
    """First-audio protection: a sentence that cannot be a claim is released the moment it is known not to be."""
    started = wr.begin()
    seen: list = []

    async def inner():
        yield "The weather "
        seen.append("after first delta")
        yield "is fine. "

    out = []
    async for piece in g.guard_stream(inner(), "jason", started):
        out.append(piece)
        if piece == "The weather ":
            assert seen == []                 # released BEFORE the next delta was even requested
    assert out == ["The weather ", "is fine. "]


@pytest.mark.asyncio
async def test_stream_passes_tool_sentinels_and_counts_a_non_read_tool_for_a_generic_claim():
    started = wr.begin()
    sentinel = '__TOOL__:{"phase": "start", "id": "1", "name": "people"}'
    out = await _collect(g.guard_stream(_stream(sentinel, "I've added it."), "jason", started))
    assert out == [sentinel, "I've added it."]
    out = await _collect(g.guard_stream(_stream("I've added it."), "jason", started))
    assert "".join(out) == HONEST_EN


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
