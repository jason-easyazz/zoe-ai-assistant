"""QA round 6a (2026-11-08) memory fixes. Synthetic names only; no transcript text.

1. A named child filed as a pet because the kids list ended beside an animal (``kin_pet_guard``).
2. A false "I didn't manage to save that" on a turn whose post-turn capture is queued (``write_claim_guard.note_pending_capture``).
3. The recall block says a time only where a note carries one (``zoe_flue_client._WHEN_RULE``).

Negative controls (each turns the named test red when the fix line is removed):
  * make ``repair`` return None                          -> test_a_kids_list_beside_an_animal_keeps_the_types
  * drop the ``is_unidentified`` check                   -> test_pending_capture_is_not_recorded_for_guests_forgets_recaps_or_short_words
  * drop the ``is_forget_turn`` check                    -> same
  * delete the ``note_pending_capture`` call in brain_dispatch._claim_guarded -> test_claim_guarded_records_the_pending_capture
  * remove ``_WHEN_RULE`` from the returned block        -> test_the_recall_block_carries_the_when_rule
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

pytestmark = pytest.mark.ci_safe

import kin_pet_guard as kp
import write_claim_guard as g
import write_receipts as wr

KIDS_AND_CAT = "I have two kids, Ava (8) and Ben (4), and a fat cat called Mo"


# ── 1. child vs pet ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_names_are_read_from_the_users_own_words():
    assert kp.child_names(KIDS_AND_CAT) == {"ava", "ben"}
    assert kp.pet_names(KIDS_AND_CAT) == {"mo"}
    # an article or filler after the list is not a name
    assert kp.child_names("my kids Ava and Ben and the cat") == {"ava", "ben"}
    assert kp.child_names("nothing about family here") == set()


@pytest.mark.parametrize("fact,ftype,want_fact", [
    ("User's pet Ava is obsessed with horses", "pet", "User's child Ava is obsessed with horses"),
    ("User's pet is named Ben", "pet", "User's child is named Ben"),
    ("Ava is obsessed with horses", "pet", "Ava is obsessed with horses"),
])
def test_a_kids_list_beside_an_animal_keeps_the_types(fact, ftype, want_fact):
    out = kp.repair(fact, ftype, KIDS_AND_CAT)
    assert out == (want_fact, "relationship")


def test_the_animal_stays_a_pet_and_a_child_filed_on_the_animal_is_repaired():
    assert kp.repair("User's pet Mo is fat", "pet", KIDS_AND_CAT) is None
    assert kp.repair("User's child Mo is fat", "relationship", KIDS_AND_CAT) == ("User's pet Mo is fat", "pet")


def test_ambiguous_or_unrelated_facts_are_left_alone():
    assert kp.repair("User's pet Ava is obsessed with horses", "pet", "my cat is called Ava") is None       # no kids cue
    assert kp.repair("User's pet Rex barks", "pet", KIDS_AND_CAT) is None                                    # a name neither list holds
    assert kp.repair("User's pet Ava and Mo play", "pet", KIDS_AND_CAT) is None                             # names both kinds
    # a name that is BOTH a child and a pet in one message is not guessed
    assert kp.repair("User's pet Sam", "pet", "kids Sam and Lee, and a dog called Sam") is None
    assert kp.repair("User's child Sam is happy", "relationship", "kids Sam and Lee, and a dog called Sam") is None


def test_the_digest_applies_the_guard_to_every_fact():
    import memory_digest

    src = inspect.getsource(memory_digest.run_turn_digest)
    assert "kin_pet_guard.repair(fact, fact_type, user_message)" in src


# ── 2. a queued capture is an honest memory claim ──────────────────────────────────────────────────────────────────────

CLAIM = "Got it, I'll remember that."
FIRST_INTRO = "hi there, I'm Sam and I live with my partner and two kids"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_WRITE_CLAIM_GUARD", raising=False)
    wr.reset()
    yield
    wr.reset()


def _guarded(user, user_text, reply=CLAIM, *, pending=True):
    started = wr.begin()
    if pending:
        g.note_pending_capture(user, user_text)
    return g.guard_text(reply, user, started, user_text=user_text)


def test_a_first_introduction_is_not_told_it_failed_to_save():
    assert _guarded("member_a", FIRST_INTRO) == CLAIM
    assert _guarded("member_a", "will you remember me when I come back?", "Of course, I'll remember you.") == "Of course, I'll remember you."


def test_without_the_pending_capture_the_claim_is_still_replaced():
    out = _guarded("member_a", FIRST_INTRO, pending=False)
    assert "didn't manage to save" in out


def test_pending_capture_is_not_recorded_for_guests_forgets_recaps_or_short_words():
    started = wr.begin()
    assert g.note_pending_capture("guest", FIRST_INTRO) is False
    assert g.note_pending_capture("guest-ab12", FIRST_INTRO) is False
    assert g.note_pending_capture("member_a", "forget what I said about the quote") is False
    assert g.note_pending_capture("member_a", "did you save what I said earlier today") is False
    assert g.note_pending_capture("member_a", "remember me") is False
    assert wr.since("member_a", started) == []
    assert "didn't manage to save" in _guarded("guest", FIRST_INTRO)


def test_the_receipt_vouches_for_memory_only():
    # a list claim on the same turn still needs the list writer's receipt
    out = _guarded("member_a", "add oat milk to my shopping list please", "I've added oat milk to your shopping list.")
    assert "didn't manage to add" in out


def test_claim_guarded_records_the_pending_capture(monkeypatch):
    import brain_dispatch as bd

    async def inner():
        yield CLAIM

    async def run():
        out = []
        async for piece in bd._claim_guarded(inner(), FIRST_INTRO, "member_a", {}):
            out.append(piece)
        return "".join(out)

    assert "didn't manage" not in asyncio.run(run())


# ── 3. no invented "when" ──────────────────────────────────────────────────────────────────────────────────────────────

def test_the_recall_block_carries_the_when_rule(monkeypatch):
    import zoe_flue_client as zc

    async def fake_packet(user_id, message, **kw):
        return "- User is a chef"

    monkeypatch.setattr(zc, "_recall_inject_enabled", lambda: True)
    monkeypatch.setattr(zc, "_recall_floor_shape", lambda m: "personal")
    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_packet)
    block = asyncio.run(zc._recall_context_block("what did I say I'd be doing today", "member_a"))
    assert zc._WHEN_RULE in block
    assert block.rstrip().endswith(zc._RECALL_BLOCK_CLOSE)
    assert "unless the note shows it" in zc._WHEN_RULE


def test_the_one_shot_lane_records_the_pending_capture_too():
    import brain_dispatch as bd

    started = wr.begin()
    assert bd._claim_guarded_text(CLAIM, FIRST_INTRO, "member_a", started, {}) == CLAIM
    assert "didn't manage" in bd._claim_guarded_text(CLAIM, FIRST_INTRO, "guest", wr.begin(), {})


# -- review round (Codex on #2350) ---------------------------------------------------------------------------

def test_a_pending_capture_never_backs_an_untargeted_claim():
    started = wr.begin()
    g.note_pending_capture("member_a", "add oat milk to my shopping list please")
    out = g.guard_text("I've added it.", "member_a", started, user_text="add oat milk to my shopping list please")
    assert out != "I've added it."           # no list writer ran: the untargeted claim is replaced
    # negative control: a memory claim on the same pending capture still passes
    assert g.guard_text(CLAIM, "member_a", started, user_text=FIRST_INTRO) == CLAIM


def test_an_opted_out_member_is_not_promised_a_capture(monkeypatch):
    import user_prefs

    monkeypatch.setattr(user_prefs, "cached_memory_opt_out", lambda uid: True)
    assert g.note_pending_capture("member_a", FIRST_INTRO) is False
    monkeypatch.setattr(user_prefs, "cached_memory_opt_out", lambda uid: False)
    assert g.note_pending_capture("member_a", FIRST_INTRO) is True


def test_kin_pet_repair_only_touches_the_noun_that_describes_the_named_subject():
    import kin_pet_guard as k

    # a child who HAS a pet is a correct statement
    assert k.repair("Ava loves her pet hamster", "pet", "My daughter Ava loves her pet hamster.") is None
    # the bleed it exists for is still repaired
    msg = "kids Ava (8) and Ben (4), and a fat cat called Mo"
    assert k.repair("User's pet Ava is obsessed with horses", "pet", msg) == ("User's child Ava is obsessed with horses", "relationship")


def test_a_recap_question_is_not_answered_by_a_queued_capture():
    # Codex on #2350: "did you save that?" asks about COMMITTED writes; a capture queued for an earlier turn is no evidence
    wr.reset()
    g.note_pending_capture("member_a", FIRST_INTRO)
    started = wr.begin()
    out = g.guard_text("Yes, I saved that note.", "member_a", started, user_text="did you save what I told you earlier?")
    assert out != "Yes, I saved that note."
    wr.record("member_a", "memory", "m1", "test")             # negative control: a committed write does back it
    assert g.guard_text("Yes, I saved that note.", "member_a", started, user_text="did you save what I told you earlier?") == "Yes, I saved that note."


def test_kin_pet_repair_leaves_a_pet_owner_alone():
    import kin_pet_guard as k

    assert k.repair("Ava is a pet owner", "pet", "My daughter Ava is a pet owner.") is None


def test_a_gender_word_describing_a_pet_is_not_a_child_cue():
    import kin_pet_guard as k

    assert k.repair("User's pet Mo is female", "pet", "My cat is a girl called Mo.") is None
    assert "mo" not in k.child_names("My cat is a girl called Mo.")
    # negative control: a real child list is still read
    assert k.child_names("kids Ava (8) and Ben (4), and a fat cat called Mo") == {"ava", "ben"}


def test_a_later_name_with_its_own_predicate_is_not_another_child():
    import kin_pet_guard as k

    msg = "My kids are Ava and Ben, and Mo is our cat."
    assert k.child_names(msg) == {"ava", "ben"}
    assert k.repair("User's pet Mo likes fish", "pet", msg) is None
    # negative control: the first child may carry a predicate ("daughter Ava is 8") and the list still reads
    assert k.child_names("my daughter Ava is 8 and my son Ben is 4") >= {"ava", "ben"}


def test_a_pet_gender_fact_is_not_rewritten_as_a_pet_role():
    # Codex on #2350: "Mo is a girl" is a correct pet fact; boy / girl are not kinship roles
    import kin_pet_guard as k

    assert k.repair("Mo is a girl.", "pet", "My cat named Mo is a girl.") is None
    # negative control: a real misfiled kinship word is still repaired
    assert k.repair("Mo is a daughter", "pet", "kids Ava (8) and a fat cat called Mo") == ("Mo is a pet", "pet")
