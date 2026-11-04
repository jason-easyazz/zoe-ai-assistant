"""An IN-PROGRESS write claim ("Adding X to your list now.") is spoken only if the write really lands in the turn.

Live 2026-11-04 (hands-on QA, typed chat): "can you add balloons, blue streamers and goldfish crackers to the shopping list" was
routed lists (conf 0.92), the chat lane deferred the write to the brain (``EXPERT_DEFER_WRITE ... write_ok=False``), the brain
called no tool (``FLUE_TOOLS tools=[]``) and replied "Adding balloons, blue streamers, and goldfish crackers to your shopping list
now." Nothing was written and no WRITE_CLAIM_BLOCKED fired: the claim lexicon only knew past tense ("added"), and the Oxford comma
before "and goldfish" split the target noun off the verb's clause.

The fix: progressive / immediate-promise claim forms in ``lexicons_data/write_claims.json``; such a claim is judged at the END of
the turn (the brain may say it just before its tool call), and a write the fast path deferred to the brain tells the guard which
kind an untargeted "Adding those now." claims.

Negative control (run by hand when this was written): restoring write_claim_guard.py + write_claims.json from origin/main fails
the ``*incident*``, ``*in_progress*`` and ``*deferred*`` tests below - the incident sentence passes through unchanged.
"""
from __future__ import annotations

import asyncio
import re

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic; no DB, no sidecar, no network

import brain_dispatch as bd
import write_claim_guard as g
import write_receipts as wr

HONEST_EN = "I didn't manage to add that; want me to try again?"
INCIDENT = "Adding balloons, blue streamers, and goldfish crackers to your shopping list now."


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_WRITE_CLAIM_GUARD", raising=False)
    wr.reset()
    wr.note_deferred_write(None)
    yield
    wr.reset()
    wr.note_deferred_write(None)


def _stream(*deltas):
    async def gen():
        for d in deltas:
            yield d

    return gen()


def _run(agen):
    async def run():
        return [d async for d in agen]

    return asyncio.new_event_loop().run_until_complete(run())


# ── 1. what counts ───────────────────────────────────────────────────────────────────────────────────────────────────────

IN_PROGRESS_CLAIMS = [
    (INCIDENT, ("list",)),
    ("Adding milk to your shopping list now.", ("list",)),
    ("Sure, adding eggs to the shopping list for you.", ("list",)),
    ("I'm adding oat milk to your shopping list.", ("list",)),
    ("I'm just setting a reminder for five o'clock.", ("reminder",)),
    ("Setting a timer for ten minutes now.", ("timer",)),
    ("I am setting a reminder for five o'clock.", ("reminder",)),         # cross-review: the full form of "I'm"
    ("We are adding bread to the shopping list.", ("list",)),
    ("I will add that to your shopping list right now.", ("list",)),
    ("Putting that on your calendar now.", ("calendar",)),
    ("Saving that to your notes for you.", ("note",)),
    ("Scheduling the physio for Friday at ten for you.", ("calendar",)),
    ("We're adding bread to the shopping list.", ("list",)),
    ("I'll add that to your shopping list right now.", ("list",)),
    ("I'll put the dentist on your calendar straight away.", ("calendar",)),
    ("I'll set a timer now.", ("timer",)),
]

NOT_IN_PROGRESS_CLAIMS = [
    "Shall I add balloons to your shopping list?",
    "I can add balloons to your shopping list.",
    "Do you want me to add balloons to your shopping list now?",
    "Want me to start adding things to your shopping list?",
    "I'll add it to your list.",                                    # a promise, no "now"
    "I'll add it to your list later.",
    "I'll add that to your list now if you like.",                  # an offer
    "I'm going to add milk to your shopping list.",                 # future
    "I'm not adding anything to your list.",
    "I was adding milk to your list when it failed.",
    "You're adding a lot to your list!",
    "Adding salt makes it taste better.",
    "Adding the eggs, then whisk for two minutes.",
    "Add milk to your shopping list by tapping the plus.",           # an instruction, not my write
    "Adding milk to your shopping list is easy.",
    "Setting the table is the kids' job.",
    # cross-review: a bare progressive with a real target noun is an instruction or a description, not Zoe's write
    "Adding milk to your shopping list by tapping the plus.",
    "Adding milk to your shopping list lets you track it.",
    "Setting a timer for ten minutes helps you focus.",
    "They are adding milk to your list.",
    "Are we adding milk to your list?",
    "I will add it to your list.",
    "I will add milk to your list now if you want.",
    "I will definitely not add it now.",
]


@pytest.mark.parametrize("sentence,kinds", IN_PROGRESS_CLAIMS)
def test_in_progress_claims_are_recognised(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None, sentence
    assert claim.kinds == kinds and claim.in_progress


@pytest.mark.parametrize("sentence", NOT_IN_PROGRESS_CLAIMS)
def test_questions_offers_and_instructions_are_not_in_progress_claims(sentence):
    assert g.find_claim(sentence) is None, sentence
    assert g.find_claim(sentence, implied_kind="list") is None, sentence


def test_a_completed_claim_is_not_in_progress():
    assert g.find_claim("I've added milk to your shopping list.").in_progress is False


def test_an_oxford_comma_before_the_last_item_keeps_the_target_in_the_verbs_clause():
    """The incident's own shape, past tense: the ", and" before the last item used to split "to your shopping list" off."""
    claim = g.find_claim("I've put balloons, blue streamers, and goldfish crackers on your shopping list.")
    assert claim is not None and claim.kinds == ("list",)
    # a joiner that opens a NEW clause still splits it, so a blocker there stays in its own clause
    assert g.find_claim("I've added milk to your list, and I'm not sure about eggs.").kinds == ("list",)


# ── 2. the incident, end to end through the guard ────────────────────────────────────────────────────────────────────────

def test_incident_reply_with_no_write_is_replaced():
    started = wr.begin()
    assert g.guard_text(INCIDENT, "jason", started) == HONEST_EN
    assert "".join(_run(g.guard_stream(_stream("Adding balloons, blue ", "streamers, and goldfish crackers to ",
                                                     "your shopping list now."), "jason", started))) == HONEST_EN


def test_incident_reply_backed_by_a_real_write_is_spoken_as_is():
    started = wr.begin()
    wr.record("jason", "list", "item-1", "tool")
    assert g.guard_text(INCIDENT, "jason", started) == INCIDENT


def test_in_progress_claim_said_before_the_tool_call_is_released_once_the_write_lands():
    """The brain narrates, THEN calls the tool: judging at the sentence would block a true claim. It is held until the receipt."""
    started = wr.begin()
    start = '__TOOL__:{"phase": "start", "id": "1", "name": "add_to_list"}'
    result = '__TOOL__:{"phase": "result", "id": "1", "name": "add_to_list"}'
    released_before_write: list = []

    async def inner():
        yield "Adding milk to your shopping list now. "
        yield start
        released_before_write.extend(out)
        wr.record("jason", "list", "item-1", "tool")
        yield result
        yield "Anything else?"

    out: list = []

    async def run():
        async for d in g.guard_stream(inner(), "jason", started):
            out.append(d)

    asyncio.new_event_loop().run_until_complete(run())
    assert released_before_write == [start]               # the claim was HELD while the write had not landed
    text = "".join(d for d in out if not d.startswith("__TOOL__:"))
    assert text == "Adding milk to your shopping list now. Anything else?"
    assert out.index(result) < out.index("Adding milk to your shopping list now. ")


def test_in_progress_claim_with_a_tool_that_wrote_nothing_is_replaced_at_the_end_of_the_turn():
    started = wr.begin()
    start = '__TOOL__:{"phase": "start", "id": "1", "name": "add_to_list"}'
    out = _run(g.guard_stream(_stream("Adding milk to your shopping list now. ", start, "Anything else?"), "jason", started))
    assert "".join(d for d in out if not d.startswith("__TOOL__:")) == f"{HONEST_EN} Anything else?"


def test_a_unit_with_a_completed_and_an_in_progress_claim_waits_for_the_end_of_the_turn():
    """Cross-review: "I've added milk…, and I'm setting a reminder now." must not be judged before the reminder can land."""
    started = wr.begin()
    reply = "I've added milk to your shopping list, and I'm setting a reminder now. "
    claim = g.find_claim(reply)
    assert claim.kinds == ("list", "reminder") and claim.in_progress
    wr.record("jason", "list", "i", "t")

    async def inner():
        yield reply
        wr.record("jason", "reminder", "r", "tool")       # lands AFTER the sentence was spoken
        yield "Anything else?"

    assert "".join(_run(g.guard_stream(inner(), "jason", started))) == reply + "Anything else?"
    started2 = wr.begin()
    wr.record("jason", "list", "i2", "t")                  # the reminder never lands this time
    assert "".join(_run(g.guard_stream(_stream(reply, "Anything else?"), "jason", started2))) == f"{HONEST_EN} Anything else?"


IN_PROGRESS_CORPUS = [
    INCIDENT,
    "Sure thing. Adding milk to your shopping list now. Anything else?",
    "I'm setting a reminder for five o'clock. The weather is fine today.",
    "I've added milk to your shopping list, and I'm setting a reminder now. Anything else?",
    "I will add that to your shopping list right now.",
    "Okay, I'll add that to your shopping list right now - anything else?",
    "Adding milk to your shopping list now. I've added eggs to your shopping list.",
    "Shall I add balloons to your shopping list? I can do it now.",
]


@pytest.mark.parametrize("reply", IN_PROGRESS_CORPUS)
@pytest.mark.parametrize("write", [False, True])
def test_stream_equals_the_whole_reply_guard_for_in_progress_claims(reply, write):
    started = wr.begin()
    if write:
        wr.record("jason", "list", "i", "t")
        wr.record("jason", "reminder", "r", "t")
    want = g.guard_text(reply, "jason", started)
    cut_sets = [[c] for c in range(1, len(reply))] + [list(range(1, len(reply)))]
    cut_sets.append([m.end() for m in re.finditer(r"\S+\s*", reply)][:-1])
    for cuts in cut_sets:
        pieces, prev = [], 0
        for c in list(cuts) + [len(reply)]:
            pieces.append(reply[prev:c])
            prev = c
        got = "".join(_run(g.guard_stream(_stream(*[p for p in pieces if p]), "jason", started)))
        assert got == want, (reply, cuts)


_FUZZ = [
    "Sure thing.", "Adding milk to your shopping list now.", "I'm setting a reminder for five o'clock,", "Anything else?",
    "I've added eggs to your shopping list.", "Shall I add bread to your list?", "I'll add that to your list right now.",
    "The weather is fine today in Perth and the sun is out,", "Putting that on your calendar now -", "Adding those now.",
    "Adding balloons, blue streamers, and goldfish crackers to your shopping list now.", "Okay.", "I can add it.",
]


@pytest.mark.parametrize("implied", [None, "list"])
def test_stream_equals_the_whole_reply_guard_for_random_in_progress_replies(implied):
    import random

    rng = random.Random(20261104)
    for case in range(80):
        reply = " ".join(rng.choice(_FUZZ) for _ in range(rng.randint(1, 4)))
        wr.reset()
        started = wr.begin()
        if case % 3 == 0:
            wr.record("jason", "list", "i", "t")
        want = g.guard_text(reply, "jason", started, implied_kind=implied)
        cut_sets = [sorted(rng.sample(range(1, len(reply)), min(len(reply) - 1, rng.randint(1, 6)))) for _ in range(3)]
        cut_sets.append(list(range(1, len(reply))))
        for cuts in cut_sets:
            pieces, prev = [], 0
            for c in list(cuts) + [len(reply)]:
                pieces.append(reply[prev:c])
                prev = c
            got = "".join(_run(g.guard_stream(_stream(*[p for p in pieces if p]), "jason", started, implied_kind=implied)))
            assert got == want, (reply, cuts)


# ── 3. a write the chat lane deferred to the brain ───────────────────────────────────────────────────────────────────────

def test_deferred_write_kind_is_taken_once():
    wr.note_deferred_write("list")
    assert wr.take_deferred_write() == "list"
    assert wr.take_deferred_write() is None
    wr.note_deferred_write("weather")                    # not a write kind
    assert wr.take_deferred_write() is None


def test_an_untargeted_in_progress_claim_claims_the_deferred_kind():
    started = wr.begin()
    reply = "Sure! Adding those for you now."
    assert g.find_claim(reply) is None                   # no target noun: on its own, nothing to check
    assert g.guard_text(reply, "jason", started, implied_kind="list") == f"Sure! {HONEST_EN}"
    wr.record("jason", "list", "item-1", "tool")
    assert g.guard_text(reply, "jason", started, implied_kind="list") == reply


@pytest.mark.asyncio
async def test_expert_dispatch_marks_a_deferred_write(monkeypatch):
    import expert_dispatch as xd

    monkeypatch.setattr(xd, "is_enabled", lambda: True)
    monkeypatch.setattr(xd, "_plan", lambda domain, text: ("list_add", {}, "write"))
    res = await xd.dispatch("lists", "add balloons to the shopping list", {"score": 0.92, "user_id": "jason"}, write_ok=False)
    assert res is None
    assert wr.take_deferred_write() == "list"


@pytest.fixture
def core_lane(monkeypatch):
    import zoe_core_client

    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")
    box = {"text": "Sure, adding those for you now.", "write": False}

    async def fake_stream(msg, sid, uid="", **kw):
        yield box["text"]
        if box["write"]:
            wr.record(uid, "list", "item-1", "tool")
        yield " Anything else?"

    async def fake_oneshot(msg, sid, uid="", **kw):
        if box["write"]:
            wr.record(uid, "list", "item-1", "tool")
        return box["text"] + " Anything else?"

    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", fake_stream)
    monkeypatch.setattr(zoe_core_client, "run_zoe_core", fake_oneshot)
    return box


def test_a_replay_turn_still_consumes_the_deferred_marker():
    """Cross-review: the replay exemption returns before guarding, but must not leave the marker for a later turn."""
    wr.note_deferred_write("list")
    inner = _stream("x")
    assert bd._claim_guarded(inner, "x", "jason", {"replay_isolation": True}) is inner
    assert wr.take_deferred_write() is None


@pytest.mark.asyncio
async def test_deferred_write_and_no_tool_call_gets_the_honest_reply(core_lane):
    wr.note_deferred_write("list")
    out = "".join([d async for d in bd.brain_streaming("add balloons to the shopping list", "s", "jason")])
    assert out == f"{HONEST_EN} Anything else?"
    wr.note_deferred_write("list")
    assert await bd.brain_oneshot("add balloons to the shopping list", "s", "jason") == f"{HONEST_EN} Anything else?"


@pytest.mark.asyncio
async def test_deferred_write_with_the_tool_called_keeps_the_normal_confirmation(core_lane):
    core_lane["write"] = True
    wr.note_deferred_write("list")
    out = "".join([d async for d in bd.brain_streaming("add balloons to the shopping list", "s", "jason")])
    assert out == "Sure, adding those for you now. Anything else?"
    wr.note_deferred_write("list")
    assert await bd.brain_oneshot("add balloons to the shopping list", "s", "jason") == "Sure, adding those for you now. Anything else?"


@pytest.mark.asyncio
async def test_without_a_deferred_write_an_untargeted_in_progress_line_is_left_alone(core_lane):
    out = "".join([d async for d in bd.brain_streaming("hello", "s", "jason")])
    assert out == "Sure, adding those for you now. Anything else?"
