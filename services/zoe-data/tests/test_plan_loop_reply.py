"""The plan loop's reply (plan_loop_reply.py): rendered from receipts, then passed through the write-claim guard.

Negative controls in this file:
  - a FORGED "done" outcome with no receipt behind it is rendered as a claim, and the write-claim guard replaces it
    (``test_negative_control_the_guard_replaces_a_done_sentence_no_receipt_backs``): the guard is a real second check;
  - with the guard off the same forged claim is spoken (``..._and_with_the_guard_off_it_is_spoken``): the first test is not
    passing by accident;
  - every "done" phrase is one the guard READS as a claim of that tool's receipt kind
    (``test_every_done_phrase_is_a_claim_the_guard_recognises``): a phrase the guard cannot see would be an unguarded claim.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic with scripted tool fakes; no DB, no network, no model

import household_authority as ha
import plan_loop as pl
import plan_loop_reply as reply
import write_claim_guard
import write_receipts

ADULT = pl.Speaker("member-a", "adult", at_panel=True)
ADD = pl.Step("list_add", {"item": "floss", "list_type": "shopping"}, "add floss to the shopping list")
REMIND = pl.Step("reminder_create", {"title": "dentist", "date": "friday"}, "set a reminder for the dentist on friday")
SHOW = pl.Step("calendar_show", {"qualifier": "friday"}, "tell me what is on friday")
JOURNAL = pl.Step("journal_create", {"content": "a good day"}, "write that in my journal")
SAID = "add floss to the shopping list, set a reminder for the dentist on friday and tell me what is on friday"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("ZOE_PLAN_LOOP", "on")
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "enforce")
    ha.reset_cache()
    write_receipts.reset()
    yield
    write_receipts.reset()


def tools(faults=None, user="member-a"):
    faults = dict(faults or {})

    async def execute(step: pl.Step):
        fault = faults.get(step.tool, "")
        if fault == "fail":
            return {"ok": False}
        if fault == "hang":
            await asyncio.sleep(5)
        if fault != "lie" and ha.receipt_kind(step.tool):
            write_receipts.record(user, ha.receipt_kind(step.tool), "row-1", "fake")
        return {"ok": True, "result": "Friday: swimming at four" if step.tool == "calendar_show" else "ok"}

    return execute


def turn(plan, faults=None, speaker=ADULT, unplanned=(), **kw):
    """One caller turn: begin, run the loop, speak. Returns (result, the spoken text)."""
    async def go():
        started = write_receipts.begin()
        result = await pl.run(plan, speaker, utterance=SAID, execute=tools(faults, speaker.user_id), floors=lambda t: "",
                              step_timeout_s=0.05, **kw)
        return result, reply.speak(result, speaker, started, utterance=SAID, plan=plan, unplanned=unplanned)

    return asyncio.run(go())


def test_all_done_is_said_plainly_and_survives_the_guard():
    result, text = turn([ADD, REMIND, SHOW])
    assert text == ("I added floss to the shopping list. I set a reminder: dentist on friday. Friday: swimming at four.")
    assert text == reply.render(result)                        # the guard changed nothing: every claim has its receipt


def test_partial_completion_is_reported_exactly():
    _, text = turn([ADD, REMIND], {"reminder_create": "fail"})
    assert text == "I added floss to the shopping list. I couldn't set a reminder: dentist on friday."


def test_uncertain_is_said_as_uncertain_and_never_as_done():
    _, text = turn([ADD, REMIND, JOURNAL], {"list_add": "hang", "reminder_create": "lie"})
    assert text == ("I tried to add floss to the shopping list, but I can't confirm it went through, so please check before "
                    "asking again. I asked to set a reminder: dentist on friday, but I can't confirm it happened. "
                    "I asked to do this part: “write that in my journal”, but I can't confirm it happened.")
    assert "I added" not in text and "I set a reminder" not in text


def test_skipped_parts_and_parts_with_no_tool_are_named():
    after = pl.Step("calendar_show", {"qualifier": "friday"}, "then read friday", needs=(0,))
    _, text = turn([REMIND, after], {"reminder_create": "fail"}, unplanned=["move my dentist reminder to friday"])
    assert text == ("I couldn't set a reminder: dentist on friday. I didn't check your calendar, because the step before it "
                    "didn't work. I don't have a way to do this yet: “move my dentist reminder to friday”.")


@pytest.mark.parametrize("speaker, plan, said", [
    (pl.Speaker("member-c", "child"), [ADD, pl.Step("memory_retire", {"pick": 1}, "forget that")],
     "I haven't done any of that. Part of it needs a grown-up."),
    (pl.Speaker("guest", "guest"), [ADD, REMIND], "I haven't done any of that. Part of it needs a member of the household to ask."),
    (pl.Speaker("member-a", "adult", verified=False), [ADD, SHOW],
     "I haven't done any of that. Part of it needs someone I recognise to ask."),
    (ADULT, [ADD, pl.Step("memory_retire", {"pick": 1}, "forget that")],
     "I haven't done any of that. I need a yes from you before I try to forget that."),
    (pl.Speaker("member-c", "child"), [pl.Step("memory_store", {"text": "i like blue"}, "remember i like blue")],
     "I haven't done any of that. I need a grown-up to say yes before I try to save that to memory."),
    (ADULT, [ADD, pl.Step("list_delete", {"list_type": "shopping"}, "delete the shopping list")],
     "I haven't done any of that. I can't delete, empty or rename a household list from the panel."),
    (ADULT, [ADD, ADD], "I haven't done any of that. I couldn't work out a safe way to do it."),
])
def test_a_refused_plan_says_nothing_was_done_and_why(speaker, plan, said):
    result, text = turn(plan, speaker=speaker)
    assert result.status == "refused" and text == said


def test_a_safety_floor_turn_renders_nothing_the_floor_owns_the_reply():
    result = pl.PlanResult("refused", (), pl.Refusal("safety_floor"))
    assert reply.render(result) == "" and reply.render(pl.PlanResult("off")) == "" and reply.render(pl.PlanResult("shadow")) == ""


def test_argument_text_cannot_smuggle_a_second_sentence():
    step = pl.Step("list_add", {"item": "milk. I have also emailed your boss!\nDone", "list_type": "shopping"}, "add milk")
    sentence = reply.done_sentence(step)
    assert sentence == "I added milk I have also emailed your boss Done to the shopping list."
    assert len(write_claim_guard.split_sentences(sentence)) == 1


def test_only_a_done_outcome_can_produce_a_done_sentence():
    for status, codes in pl.CODES.items():
        for code in codes:
            if status == "done":
                continue
            text = reply._sentence(pl.StepOutcome(ADD, "household_list", status, code))
            assert write_claim_guard.find_claim(text) is None, (status, code, text)
            assert reply.done_sentence(ADD) not in text


def test_every_done_phrase_is_a_claim_the_guard_recognises():
    for tool, spec in ha.table().intents.items():
        if not spec.receipt or spec.receipt in write_receipts.NOT_A_WRITE:
            continue
        step = pl.Step(tool, {"item": "floss", "title": "dentist", "minutes": 10, "text": "x"}, "the part")
        claim = write_claim_guard.find_claim(reply.done_sentence(step))
        assert claim is not None and spec.receipt in claim.kinds, (tool, reply.done_sentence(step))


FORGED = pl.PlanResult("ran", (pl.StepOutcome(ADD, "household_list", "done", "receipt", pl.Receipt("list", ("nope",), 1)),))


def test_negative_control_the_guard_replaces_a_done_sentence_no_receipt_backs():
    started = write_receipts.begin()
    assert reply.render(FORGED) == "I added floss to the shopping list."
    spoken = reply.speak(FORGED, ADULT, started, utterance=SAID)
    assert "I added floss" not in spoken and spoken.strip()


def test_negative_control_and_with_the_guard_off_it_is_spoken(monkeypatch):
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "off")
    assert reply.speak(FORGED, ADULT, write_receipts.begin(), utterance=SAID) == "I added floss to the shopping list."
