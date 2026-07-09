"""P4 — person_create confirm card (ADR-contacts-production-hardening).

`ui_components_for_suggestions` must emit a contact-specific Add/Dismiss card for
`person_create` proposals while leaving every other action type's generic Save
card unchanged.
"""

import pytest

from pending_suggestions import ui_components_for_suggestions

pytestmark = pytest.mark.ci_safe


def _find(cards, sid):
    return next(c for c in cards if any(a.get("suggestion_id") == sid for a in c["actions"]))


def test_person_create_card_with_relationship():
    cards = ui_components_for_suggestions([
        {
            "id": "sug-1",
            "action_type": "person_create",
            "offer_phrase": "Save this?",
            "pre_filled_slots": {"name": "Daniel", "relationship": "brother"},
        }
    ])
    assert len(cards) == 1
    card = cards[0]
    assert card["type"] == "action_card"
    assert card["title"] == "Add Daniel as your brother?"
    labels = [(a["label"], a["action"], a["suggestion_id"]) for a in card["actions"]]
    assert labels == [
        ("Add", "pending_suggestion_accept", "sug-1"),
        ("Dismiss", "pending_suggestion_dismiss", "sug-1"),
    ]


def test_person_create_card_without_relationship():
    cards = ui_components_for_suggestions([
        {
            "id": "sug-2",
            "action_type": "person_create",
            "pre_filled_slots": {"name": "Teneeka"},
        }
    ])
    assert cards[0]["title"] == "Add Teneeka?"
    assert [a["action"] for a in cards[0]["actions"]] == [
        "pending_suggestion_accept",
        "pending_suggestion_dismiss",
    ]


def test_non_person_create_keeps_generic_card():
    cards = ui_components_for_suggestions([
        {
            "id": "sug-3",
            "action_type": "list_add",
            "offer_phrase": "Add milk to your shopping list?",
            "pre_filled_slots": {"item": "milk", "list_type": "shopping"},
        }
    ])
    # Byte-for-byte the pre-existing generic card.
    assert cards == [
        {
            "type": "action_card",
            "title": "Add milk to your shopping list?",
            "actions": [
                {
                    "label": "Save",
                    "action": "pending_suggestion_accept",
                    "suggestion_id": "sug-3",
                }
            ],
        }
    ]


def test_person_create_sanitises_untrusted_slots():
    cards = ui_components_for_suggestions([
        {
            "id": "sug-4",
            "action_type": "person_create",
            "pre_filled_slots": {
                "name": "Dan\n\n# INJECTED",
                "relationship": "  step   brother ",
            },
        }
    ])
    title = cards[0]["title"]
    assert "\n" not in title
    assert "#" not in title
    # whitespace-collapse runs before char-strip (mirrors _safe_prompt_inline),
    # so removing "#" from "Dan # INJECTED" leaves a residual double space.
    assert title == "Add Dan  INJECTED as your step brother?"


@pytest.mark.parametrize("bad_slots", [["Daniel"], "Daniel", 42, None])
def test_person_create_survives_non_dict_slots(bad_slots):
    # A legacy/malformed row could decode pre_filled_slots as a non-dict; the card
    # must degrade to the generic-name fallback, never raise (a raise makes the
    # chat caller drop ALL suggestion cards for the turn).
    cards = ui_components_for_suggestions([
        {"id": "sug-5", "action_type": "person_create", "pre_filled_slots": bad_slots}
    ])
    assert cards[0]["title"] == "Add this contact?"
    assert [a["action"] for a in cards[0]["actions"]] == [
        "pending_suggestion_accept",
        "pending_suggestion_dismiss",
    ]
