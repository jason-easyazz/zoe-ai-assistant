"""first_boot: the ordered setup checklist and "what is missing, in order" (pure; no database, no environment)."""
from __future__ import annotations

import dataclasses

import pytest

import first_boot as fb

pytestmark = pytest.mark.ci_safe


def facts(**over) -> fb.Facts:
    """Everything done, then ``over`` applied."""
    base = {i.id: True for i in fb.ITEMS}
    base.update(over)
    return fb.Facts(**base)


def states(f: fb.Facts) -> dict:
    return {s.id: s.state for s in fb.evaluate(f)}


# ── the checklist itself ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_items_are_ordered_and_only_need_earlier_items():
    seen = set()
    for item in fb.ITEMS:
        assert item.tier in fb.TIERS
        assert item.source, item.id
        assert set(item.needs) <= seen, f"{item.id} needs a LATER item: {item.needs}"
        seen.add(item.id)


def test_every_item_has_exactly_one_facts_field():
    fields = {f.name for f in dataclasses.fields(fb.Facts)} - {"deferred"}
    assert fields == {i.id for i in fb.ITEMS}


def test_the_blocking_set_is_what_a_working_household_needs():
    blocking = [i.id for i in fb.ITEMS if i.tier == fb.BLOCKING]
    assert blocking == ["owner_account", "forget_secret", "panel_paired", "panel_named", "time_and_place", "owner_pin",
                        "owner_voice"]


# ── evaluate / missing / next_step ────────────────────────────────────────────────────────────────────────────────────────

def test_a_blank_unit_offers_the_owner_account_first_and_everything_else_waits_or_is_missing():
    blank = fb.Facts(**{i.id: False for i in fb.ITEMS})
    st = states(blank)
    assert st["owner_account"] == fb.MISSING
    assert st["forget_secret"] == fb.MISSING
    assert st["panel_paired"] == fb.WAITING
    assert st["owner_voice"] == fb.WAITING
    assert fb.next_step(blank).id == "owner_account"
    assert [s.id for s in fb.missing(blank)][:3] == ["owner_account", "forget_secret", "panel_paired"]
    assert not fb.ready(blank)


def test_waiting_names_the_first_unmet_prerequisite():
    f = facts(owner_pin=False, owner_voice=False)
    voice = next(s for s in fb.evaluate(f) if s.id == "owner_voice")
    assert (voice.state, voice.blocked_by) == (fb.WAITING, "owner_pin")


def test_unknown_is_never_offered_and_never_counts_as_done():
    f = facts(owner_voice=None)
    assert states(f)["owner_voice"] == fb.UNKNOWN
    assert fb.next_step(f) is None
    assert not fb.ready(f)
    g = facts(owner_pin=None, owner_voice=False)
    assert states(g)["owner_voice"] == fb.WAITING        # a dependent of an unknown item waits rather than being offered
    assert fb.next_step(g) is None


def test_deferred_deferrable_settles_but_deferred_blocking_does_not():
    f = facts(backup_target=False, deferred=frozenset({"backup_target"}))
    assert states(f)["backup_target"] == fb.DEFERRED and fb.ready(f)
    g = facts(owner_voice=False, first_win=False, deferred=frozenset({"owner_voice"}))
    assert states(g)["owner_voice"] == fb.DEFERRED and not fb.ready(g)
    assert fb.next_step(g).id == "first_win"               # one "no" to the voice does not stall the flow


def test_a_deferral_of_a_done_item_is_ignored():
    assert states(facts(deferred=frozenset({"calendar"})))["calendar"] == fb.DONE


def test_blocking_items_come_before_deferrable_in_next_step():
    f = facts(phone_linked=False, owner_pin=False, owner_voice=False)
    assert fb.next_step(f).id == "owner_pin"


def test_child_marked_na_when_there_is_no_child():
    f = facts(child_marked=fb.NA)
    assert states(f)["child_marked"] == fb.NA
    assert fb.ready(f) and "child_marked" not in [s.id for s in fb.missing(f, include_optional=True)]


def test_ready_ignores_optional_items():
    assert fb.ready(facts(calendar=False, music=False, more_members=False))


def test_missing_is_deterministic():
    f = facts(owner_pin=False, phone_linked=False, music=False)
    assert fb.missing(f) == fb.missing(f)
    assert [s.id for s in fb.missing(f)] == ["owner_pin", "phone_linked"]   # a voice already enrolled stays done
    assert [s.id for s in fb.missing(f, include_optional=True)][-1] == "music"


# ── nudge: any day, never unprompted ──────────────────────────────────────────────────────────────────────────────────────

def test_nudge_rejects_an_unknown_surface():
    assert fb.nudge(facts(owner_pin=False), "timer") is None


def test_nudge_never_names_a_deferred_item_unless_asked():
    f = facts(backup_target=False, deferred=frozenset({"backup_target"}))
    assert fb.nudge(f, "turn_close") is None
    assert fb.nudge(f, "panel_card") is None
    assert fb.nudge(f, "asked").id == "backup_target"


def test_optional_items_only_on_a_card_or_when_asked():
    f = facts(music=False)
    assert fb.nudge(f, "turn_close") is None
    assert fb.nudge(f, "panel_card").id == "music"
    assert fb.nudge(f, "asked").id == "music"


def test_summary_counts():
    s = fb.summary(facts(owner_pin=False, owner_voice=False))
    assert s["ready"] is False and s["next"] == "owner_pin" and s["counts"][fb.WAITING] == 1  # owner_voice



@pytest.mark.parametrize("surface", fb.SURFACES)
@pytest.mark.parametrize("item_id", [i.id for i in fb.ITEMS if i.tier != fb.OPTIONAL])
def test_unknown_required_item_prevents_optional_nudge(surface, item_id):
    f = facts(**{item_id: None, "more_members": False})
    assert fb.next_step(f) is None
    assert fb.nudge(f, surface) is None


@pytest.mark.parametrize("surface", fb.SURFACES)
@pytest.mark.parametrize("item_id", [i.id for i in fb.ITEMS if i.tier == fb.BLOCKING])
def test_deferred_blocking_item_prevents_optional_nudge(surface, item_id):
    f = facts(**{item_id: False, "more_members": False, "deferred": frozenset({item_id})})
    assert fb.next_step(f) is None
    step = fb.nudge(f, surface)
    if surface == "asked":
        assert step.id == item_id and step.state == fb.DEFERRED
    else:
        assert step is None


@pytest.mark.parametrize("surface", ["panel_card", "asked"])
def test_all_required_done_allows_optional_nudge(surface):
    assert fb.nudge(facts(more_members=False), surface).id == "more_members"


def test_deferred_deferrable_still_allows_optional_panel_card():
    f = facts(backup_target=False, more_members=False, deferred=frozenset({"backup_target"}))
    assert fb.nudge(f, "panel_card").id == "more_members"
