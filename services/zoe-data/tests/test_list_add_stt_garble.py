"""STT-garble-tolerant list adds (panel bug 2026-07-12).

Moonshine renders a leading "Add" as near-homophones: Jason's "Add council to
my work list" arrived as "I'd go to council to my work list", the add regexes
missed, and the semantic router drifted the utterance to calendar — so the add
silently never happened and a later show answered "work has 0 items".

detect_intent must recognise an utterance that ENDS with "to my <known> list"
as a list_add (stripping the garbled lead-in), while bare navigation like
"go to my work list" keeps falling through to the show/open patterns.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from intent_router import detect_intent


def _intent(text):
    return detect_intent(text, log_miss=False)


@pytest.mark.parametrize(
    "text,item,list_type",
    [
        # the exact production garble
        ("I'd go to council to my work list.", "council", "work"),
        ("I'd council to my work list", "council", "work"),
        ("And milk to my shopping list", "milk", "shopping"),
        ("At bread to the shopping list", "bread", "shopping"),
        ("It dentist appointment to my personal list", "dentist appointment", "personal"),
    ],
)
def test_garbled_add_recovers(text, item, list_type):
    i = _intent(text)
    assert i is not None and i.name == "list_add", f"{text!r} -> {i and i.name}"
    assert i.slots["item"] == item
    assert i.slots["list_type"] == list_type


@pytest.mark.parametrize(
    "text",
    [
        # bare navigation must NOT become an add
        "go to my work list",
        "take me to my work list",
    ],
)
def test_navigation_not_swallowed(text):
    i = _intent(text)
    assert i is None or i.name != "list_add", f"{text!r} wrongly parsed as add: {i.slots if i else None}"


def test_clean_add_still_works():
    i = _intent("add council to my work list")
    assert i is not None and i.name == "list_add"
    assert i.slots["item"] == "council"
    assert i.slots["list_type"] == "work"
