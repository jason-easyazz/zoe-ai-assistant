"""QA round 5 (2026-11-08): recall denied with the fact in the store, spliced contractions, a speaking style that never adapts.

Synthetic names only; nothing here quotes a QA transcript.

1. RECALL FLOOR FOR HOUSEHOLD QUESTIONS (``memory_gate.is_question_turn`` / ``packet_touches_question``, wired in
   ``zoe_flue_client._recall_context_block``). A question with no my/I grammar about someone who is not a ``people`` row matched no floor
   shape, the model did not call recall_memory, and it denied a fact the store held. Pinned: the packet rides the turn when it touches
   the question; a packet that does not touch it injects nothing; a statement is not a question; the kill switch restores the old
   bytes; a continuity (feeling) turn keeps its own block; the fetch never carries a contact offer.
2. CONTRACTION REPAIR (``contraction_repair``, wired at the one end-of-turn place, ``brain_dispatch._claim_guarded``): the MTP draft
   splice ``I don'm`` is restored for every way the reply is cut into deltas; every valid contraction is untouched.
3. REGISTER REQUESTS (``register_requests``): an explicit style request is stored as ``stated`` and the register line of the SAME turn
   carries it; adults only; a story that merely contains the words stores nothing.

Negative controls (run by hand when this was written): drop the ``packet_touches_question`` check -> the "untouched packet" test goes
red; make ``is_question_turn`` return True for everything -> the statement test goes red; drop ``_HOLD_RE`` -> the split-delta test
goes red; drop the ``mr.enabled()`` check in ``register_requests.apply`` -> the flag-off test goes red.
"""
from __future__ import annotations

import asyncio
import random

import pytest

import brain_dispatch
import contraction_repair as cr
import member_register as mr
import memory_gate as mg
import register_requests as rr
import zoe_flue_client as zc
from test_member_register import _clean, rdb, register_of, run, sent  # noqa: F401  (fixtures + helpers of the register suite)

pytestmark = pytest.mark.ci_safe

PACKET = (
    "Known facts about this user:\n"
    "- Mira is six years old [mem:aaaa1111]\n"
    "- Theo is vegetarian and does not eat meat [mem:bbbb2222]\n"
    "- Dana works night shifts at the hospital [mem:cccc3333]\n"
    "- The user does not drink coffee, only tea [mem:dddd4444]\n"
)


# ── 1. the recall floor for household questions ────────────────────────────────────────────────────────────────
@pytest.fixture
def floor(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.delenv("ZOE_RECALL_HOUSEHOLD_QUESTIONS", raising=False)
    calls = []

    async def fake_fetch(uid, msg, focus=None, no_offer=False):
        calls.append({"msg": msg, "no_offer": no_offer})
        return PACKET

    async def no_people(message, uid):
        return []

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)
    monkeypatch.setattr(zc, "_named_person_floor", no_people)
    return calls


def block(message, uid="ada_uid"):
    return asyncio.run(zc._recall_context_block(message, uid))


@pytest.mark.parametrize("question, fact", [
    ("how old is mira", "Mira is six"),
    ("does theo eat meat", "does not eat meat"),
    ("whats danas job", "night shifts"),
    ("do i drink coffee", "only tea"),
    ("Who is Dana?", "night shifts"),
])
def test_a_household_question_the_notes_answer_carries_the_packet(floor, question, fact):
    # none of these matches a floor shape: the old floor returned "" for every one of them
    assert zc._recall_floor_shape(question) == ""
    out = block(question)
    assert out.startswith(zc._RECALL_BLOCK_OPEN) and fact in out
    assert floor[-1]["no_offer"] is True            # a block that may be dropped must not spend a contact offer


@pytest.mark.parametrize("question", [
    "how old is the universe",          # a question, but nothing in the notes touches it (negative control for the gate)
    "what is the weather today",
    "what time is it",
    "who is Ada Lovelace",
])
def test_a_question_the_notes_have_nothing_on_injects_nothing(floor, question):
    assert mg.is_question_turn(question) is True
    assert block(question) == ""
    assert floor, "the packet is fetched (one in-process read) and then judged"


@pytest.mark.parametrize("statement", [
    "Mira is six now",                  # shares a word with the packet, but it is a statement, not a question
    "tell me a joke about Theo",
    "do the dishes",                    # an auxiliary opener with no subject is a command
    "set a timer for five minutes",
])
def test_a_statement_or_command_is_not_a_question_turn(floor, statement):
    assert mg.is_question_turn(statement) is False
    assert block(statement) == ""
    assert floor == []                   # no read at all


def test_the_kill_switch_restores_the_old_bytes(floor, monkeypatch):
    assert block("how old is mira") != ""                                     # positive control
    monkeypatch.setenv("ZOE_RECALL_HOUSEHOLD_QUESTIONS", "off")
    assert block("how old is mira") == ""


def test_a_recall_shaped_question_still_takes_the_old_path(floor):
    assert zc._recall_floor_shape("what's my name") == "personal"
    assert block("what's my name") != ""
    assert floor[-1]["no_offer"] is False            # unchanged: the original call, offers and all


def test_a_feeling_asked_as_a_question_keeps_its_continuity_block(floor, monkeypatch):
    monkeypatch.setattr(zc, "is_continuity_turn", lambda message, uid: True)
    assert block("why am i so tired lately") == ""
    assert floor == []


def test_no_floor_flag_no_block(floor, monkeypatch):
    monkeypatch.delenv("ZOE_SEAM_RECALL_INJECT")
    assert block("how old is mira") == ""


def test_the_household_packet_is_capped_tighter_than_a_recall_packet(floor, monkeypatch):
    big = "Known facts:\n" + "\n".join(f"- Mira fact number {i} [mem:{i:08d}]" for i in range(30))

    async def fake_fetch(uid, msg, focus=None, no_offer=False):
        return big

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)
    out = block("how old is mira")
    bullets = [ln for ln in out.splitlines() if ln.lstrip().startswith("-")]
    assert 0 < len(bullets) <= zc._HOUSEHOLD_MAX_BULLETS


def test_touch_tokens_handle_lazy_possessives_and_function_words():
    assert mg.packet_touches_question("whats danas job", "- Dana works nights")
    assert not mg.packet_touches_question("what is the", "- The user is the one who is here")   # only function words
    assert not mg.packet_touches_question("how old is the universe", "- Mira is six")
    # cross-review: a contraction's stem ("don", "won") is not a content word, so two sentences that only share "don't" do not touch
    assert not mg.packet_touches_question("Why don't black holes emit light?", "- The user's parents don't eat meat")
    assert not mg.packet_touches_question("why won't it rain", "- Theo won't eat onions")
    assert mg.packet_touches_question("whats Dana's job", "- Dana's job is nursing")      # a possessive keeps its name


# ── 2. the contraction splice ────────────────────────────────────────────────────────────────────────────────
SPLICED = [
    ("I don'm not really set up for jokes", "I'm not really set up for jokes"),
    ("you don're a tea drinker", "you're a tea drinker"),
    ("I don've got nothing on that", "I've got nothing on that"),
    ("Since it is health, I don'd check first", "Since it is health, I'd check first"),
    ("I don’m sure", "I’m sure"),
    ("they don'll be late", "they'll be late"),
]
VALID = [
    "I don't know", "you don't have to", "I can't say", "she won't mind", "I'm here", "you're right", "I've got it", "I'd say so",
    "they'll come", "we don't, but I'm sure we're fine", "Don't worry, I'd help", "I do not know", "I don", "don'm alone",
    "it isn't", "that doesn't matter", "who's there", "Don is here, I'm told",
]


@pytest.mark.parametrize("bad, good", SPLICED)
def test_a_spliced_contraction_is_restored(bad, good):
    assert cr.repair(bad) == good


@pytest.mark.parametrize("text", VALID)
def test_valid_text_is_never_touched(text):
    assert cr.repair(text) == text


def test_every_way_of_cutting_a_reply_into_deltas_gives_the_same_text():
    reply = "So I don'm sure. I don't know; you don're right, and I don've it. I don'd go. I don"
    rng = random.Random(7)
    for _ in range(300):
        cuts = sorted(rng.sample(range(1, len(reply)), rng.randint(1, 12)))
        parts = [reply[i:j] for i, j in zip([0] + cuts, cuts + [len(reply)])]
        rs = cr.RepairStream()
        got = "".join(rs.feed(p) for p in parts) + rs.finish()
        assert got == cr.repair(reply), parts
    # the token boundary of the real glitch: the stray word, then the real token
    rs = cr.RepairStream()
    assert rs.feed("I") + rs.feed(" don") + rs.feed("'m") + rs.feed(" sure") + rs.finish() == "I'm sure"


def _collect(stream):
    async def go():
        return [d async for d in stream]
    return asyncio.run(go())


def _aiter(items):
    async def gen():
        for i in items:
            yield i
    return gen()


def test_the_stream_filter_repairs_and_passes_sentinels_through():
    out = _collect(cr.filter_stream(_aiter(["__THINKING__:x", "I", " don", "'m", " here", "__TOOL__:y", ". You", " don", "'t"])))
    assert "".join(o for o in out if not o.startswith("__")) == "I'm here. You don't"
    assert "__THINKING__:x" in out and "__TOOL__:y" in out


def test_flag_off_leaves_the_splice_alone(monkeypatch):
    monkeypatch.setenv("ZOE_CONTRACTION_REPAIR", "off")
    assert "".join(_collect(cr.filter_stream(_aiter(["I", " don", "'m"])))) == "I don'm"     # positive control: the filter is what repairs
    assert cr.repair_text("I don'm") == "I don'm"
    monkeypatch.delenv("ZOE_CONTRACTION_REPAIR")
    assert cr.repair_text("I don'm") == "I'm"


def test_the_end_of_turn_guard_repairs_both_the_streamed_and_the_one_shot_reply():
    streamed = _collect(brain_dispatch._claim_guarded(_aiter(["I", " don", "'m", " not sure."]), "hello", "ada_uid", {}))
    assert "".join(streamed) == "I'm not sure."
    one_shot = brain_dispatch._claim_guarded_text("I don'm not sure.", "hello", "ada_uid", 0.0, {})
    assert one_shot == "I'm not sure."


# ── 3. explicit style requests ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("message, want", [
    ("dont end with a question pls", ("questions", "none")),
    ("stop ending every reply with a question", ("questions", "none")),
    ("Please stop ending your replies on a question.", ("questions", "none")),
    ("I said stop ending with questions", ("questions", "none")),
    ("stop asking me questions", ("questions", "none")),
    ("be more casual", ("formality", "casual")),
    ("can you talk like a mate", ("formality", "casual")),
    ("be more formal please", ("formality", "formal")),
    ("keep it short", ("length", "terse")),
    ("ok zoe, from now on shorter replies", ("length", "terse")),
    ("give me more detail", ("length", "fuller")),
    ("no more jokes", ("humour", "off")),
    ("you can ask questions again", ("questions", "normal")),
])
def test_an_explicit_request_is_read(message, want):
    assert want in rr.parse(message)


@pytest.mark.parametrize("message", [
    "my boss told me to keep it short",            # a story: the request does not open the turn
    "what is a casual tone",
    "im trying to relax",
    "relax",
    "he never ends with a question",
    "\"be more casual\" is what my boss told me. What does that mean?",      # reported speech (cross-review): quoted, so not a request
    "'keep it short' she said",
    "how do i stop ending sentences with a question mark in my essay and keep it short for the examiner on friday morning at nine "
    "o'clock sharp in the main hall of the school",     # over the length cap
    "",
])
def test_a_sentence_that_only_contains_the_words_is_not_a_request(message):
    assert rr.parse(message) == []


def test_flag_off_stores_nothing(rdb, monkeypatch):                       # noqa: F811
    assert run(rr.apply("ada_uid", "stop ending with questions")) == []   # ZOE_MEMBER_REGISTER unset
    assert run(mr.list_entries("ada_uid")) == []


def test_an_adult_request_is_stored_as_stated_and_the_same_turn_obeys_it(rdb, sent, monkeypatch):   # noqa: F811
    monkeypatch.setenv("ZOE_MEMBER_REGISTER", "on")
    assert register_of(sent("hello there")) is None                           # no entries, no line (the QA finding, reproduced)
    wire = sent("stop ending every reply with a question")
    assert "never end a reply with a question" in (register_of(wire) or "")  # rendered on THIS turn
    entries = {e.key: e for e in run(mr.list_entries("ada_uid"))}
    assert entries["questions"].value == "none" and entries["questions"].provenance == "stated"
    assert "never end a reply with a question" in (register_of(sent("what is for dinner")) or "")   # and on every turn after


def test_a_second_request_replaces_the_first_and_forget_still_works(rdb, sent, monkeypatch):          # noqa: F811
    monkeypatch.setenv("ZOE_MEMBER_REGISTER", "on")
    sent("be more formal")
    sent("be more casual")
    line = register_of(sent("hi")) or ""
    assert "casual" in line and "formal" not in line
    assert run(mr.forget("ada_uid", "formality")) is True
    assert "casual" not in (register_of(sent("hi")) or "")


def test_a_child_a_guest_and_an_unknown_account_store_nothing(rdb, monkeypatch):                         # noqa: F811
    monkeypatch.setenv("ZOE_MEMBER_REGISTER", "on")
    for uid in ("mia_uid", "kid_uid", "guest", "voice-guest", "nobody_uid", "odd_uid"):
        assert run(rr.apply(uid, "be more casual")) == [], uid
    assert run(mr.list_entries("mia_uid")) == [] == run(mr.list_entries("nobody_uid"))
    assert run(rr.apply("ada_uid", "be more casual")) == [("formality", "casual", "written")]   # positive control: an adult does


def test_the_longest_possible_line_still_fits_the_budget_with_the_new_key():
    es = [mr.Entry("length", "length", "fuller", "stated"), mr.Entry("formality", "formality", "formal", "stated"),
          mr.Entry("humour", "humour", "light", "stated"), mr.Entry("pace", "pace", "slow", "stated"),
          mr.Entry("questions", "questions", "none", "stated")]
    es += [mr.Entry(f"topic:{t}", "topic", t, "stated") for t in ("a" * 24, "b" * 24, "c" * 24, "d" * 24, "e" * 24)]
    text = mr.render(es)
    assert "never end a reply with a question" in text
    assert mr.estimate_tokens(text) <= mr.MAX_REGISTER_TOKENS, mr.estimate_tokens(text)


def test_the_replay_harness_never_writes_a_register(rdb, sent, monkeypatch):                             # noqa: F811
    monkeypatch.setenv("ZOE_MEMBER_REGISTER", "on")
    sent("be more casual", replay_isolation=True)
    assert run(mr.list_entries("ada_uid")) == []
