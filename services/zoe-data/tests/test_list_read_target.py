"""A list READ names its list exactly as an add does - never the household shopping list by default (QA round 3b, 2026-11-07).

Live bug: "whats on my to do list now" (a trailing filler word) missed the fast-path regex, ``expert_dispatch._plan`` returned
``list_show`` with EMPTY slots, and ``_execute_list_show_direct`` defaulted an empty read to the household shopping list. Same
default-to-shared class #2320 fixed for adds. And the brain's list tools sent the name the model used (``list_type="tasks/to-do"``,
"pharmacy study list"), which the sidecar mapped to shopping - now zoe-data resolves such a name like a spoken one.

Real SQL against an in-memory SQLite (the fixture of test_list_target_privacy). All data is invented.
"""
import pytest

import expert_dispatch
import intent_router
from intent_router import Intent, detect_intent

from test_list_target_privacy import ME, OTHER, db  # noqa: F401 - the shared SQLite fixture

pytestmark = pytest.mark.ci_safe


def _plan_slots(text):
    planned = expert_dispatch._plan("lists", text)
    assert planned is not None and planned[0] == "list_show", (text, planned)
    return planned[1]


# ---- the plan: a read that the regex missed still carries its list --------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("whats on my to do list now", {"list_type": "personal"}),
    ("what's on my to-do list at the moment", {"list_type": "personal"}),
    ("is there anything on my tasks list", {"list_type": "tasks"}),
    ("whats on my pharmacy list now", {"list_type": "", "list_name": "pharmacy"}),
    ("what's on the list at the moment", {"list_type": "shopping"}),          # a bare "the list": the household list
    ("do i have anything on my to dos", {"list_type": "personal"}),           # a type word with no "list"
])
def test_show_plan_names_the_list(text, expected):
    assert _plan_slots(text) == expected


def test_a_long_list_name_is_still_named():
    assert _plan_slots("what's on my very important pharmacy study revision list at the moment") == {
        "list_type": "", "list_name": "very important pharmacy study revision"}


def test_a_list_it_cannot_read_goes_to_the_brain_never_the_shopping_list():
    # "list" is said but no my/the/our name and no type word: no guess - the brain asks
    assert expert_dispatch._plan("lists", "what's on pharmacy revision list at the moment") is None
    # negative control: no list named at all is the household list (the fridge rule)
    assert _plan_slots("what do i have to get, is there anything on it") == {}


def test_detect_intent_tolerates_a_trailing_filler_word():
    intent = detect_intent("whats on my to do list now", log_miss=False)
    assert intent is not None and intent.name == "list_show" and intent.slots.get("list_type") == "personal"
    # negative control: a different question is still not a list read
    assert getattr(detect_intent("whats on tv now", log_miss=False), "name", "") != "list_show"


# ---- the executor: the member's own list, never the household shopping list ------------------------------------------------
async def test_my_todo_read_shows_my_list_not_the_shopping_list(db):  # noqa: F811
    db.lst("mine", ME, "Personal", "personal", "personal")
    db.item("m1", "mine", "renew registration")
    out = await intent_router._execute_list_show_direct(Intent("list_show", _plan_slots("whats on my to do list now")), ME)
    assert "renew registration" in out.lower() and "milk" not in out.lower()


async def test_negative_control_an_empty_read_is_the_household_list(db):  # noqa: F811
    out = await intent_router._execute_list_show_direct(Intent("list_show", {}), ME)
    assert "milk" in out.lower()


async def test_unknown_named_read_asks(db):  # noqa: F811
    out = await intent_router._execute_list_show_direct(Intent("list_show", _plan_slots("whats on my pharmacy list now")), ME)
    assert "milk" not in out.lower() and "pharmacy" in out and "which list" in out.lower()


# ---- the brain tool's name: resolved like a spoken name ---------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("tasks/to-do", {"list_type": "personal", "list_phrase": "tasks to do"}),
    ("pharmacy study list", {"list_type": "", "list_name": "pharmacy study"}),
])
def test_a_non_type_list_type_resolves_like_a_spoken_name(raw, expected):
    out = intent_router._canonical_list_slots(Intent("list_add", {"item": "x", "list_type": raw}))
    assert out.slots == {"item": "x", **expected}


@pytest.mark.parametrize("slots", [
    {"item": "x", "list_type": "shopping"},
    {"item": "x", "list_type": "personal"},
    {"item": "x"},
    {"item": "x", "list_type": "", "list_name": "pharmacy"},
])
def test_negative_control_real_types_and_named_lists_are_untouched(slots):
    intent = Intent("list_add", dict(slots))
    assert intent_router._canonical_list_slots(intent) is intent


async def test_brain_add_with_a_todo_name_lands_on_my_own_list(db):  # noqa: F811
    out = await intent_router._execute_list_add_direct(
        Intent("list_add", {"item": "swim goggles", "list_type": "tasks/to-do"}), ME)
    assert db.open_items("fam") == ["milk"]                      # never the household shopping list
    assert db.open_items("theirs") == ["renew registration"]     # never another member's shared to-do list
    mine = [r for r in db.lists() if r["user_id"] == ME]
    assert len(mine) == 1 and mine[0]["visibility"] == "personal" and db.open_items(mine[0]["id"]) == ["swim goggles"]
    assert "shopping" not in out.lower()


async def test_brain_add_to_an_unknown_name_asks_and_writes_nothing(db):  # noqa: F811
    before = db.lists()
    out = await intent_router._execute_list_add_direct(
        Intent("list_add", {"item": "flashcards", "list_type": "pharmacy study list"}), ME)
    assert "which list" in out.lower() and db.lists() == before and db.open_items("fam") == ["milk"]


async def test_negative_control_brain_add_to_shopping_still_lands_on_the_household_list(db):  # noqa: F811
    await intent_router._execute_list_add_direct(Intent("list_add", {"item": "bread", "list_type": "shopping"}), OTHER)
    assert db.open_items("fam") == ["milk", "bread"]
