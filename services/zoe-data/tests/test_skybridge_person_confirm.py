"""Skybridge person_confirm card — ADR-contacts-production-hardening P4.

The kiosk home is Skybridge (not chat.html). A pending `person_create` offer
surfaces as a tappable "Add {name} as your {relationship}?" card whose Add button
re-issues a people_create command via /api/skybridge/resolve — the contact is
ALWAYS created server-side under the authenticated panel user, never trusted from
the client payload. Covers the classify trigger, the card shape + the conforming
Add action, and the resolve path (offers → cards, empty → status).
"""
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import skybridge_service as sky
from card_contract import validate_component_action


@pytest.mark.parametrize("msg", [
    "any contacts to add?",
    "show contact suggestions",
    "who can I add",
    "people to add",
    "pending contacts",
])
def test_classify_surfaces_pending_offers(msg):
    intent = sky.classify_skybridge_intent(msg)
    assert intent is not None, msg
    assert intent.domain == "people" and intent.action == "pending_offers", msg


def test_classify_does_not_steal_plain_directory():
    # "show my contacts" is a directory browse, NOT an offer surface — must still
    # route to the people directory (action="show"), not pending_offers.
    intent = sky.classify_skybridge_intent("show my contacts")
    assert intent is not None and intent.domain == "people" and intent.action == "show"


def test_person_confirm_card_with_relationship():
    card = sky._person_confirm_card("Daniel", "brother", suggestion_id="s1")
    assert card["component"] == "person_confirm"
    props = card["props"]
    assert props["title"] == "Add Daniel as your brother?"
    assert props["name"] == "Daniel" and props["relationship"] == "brother"
    assert props["suggestion_id"] == "s1"
    add, dismiss = props["actions"]
    # Add re-issues a real people_create command (server-side, deduped).
    assert add["type"] == "query"
    assert add["query"] == "add Daniel as my brother"
    assert add["kind"] == "primary"
    # "Not now" is a client-only dismiss — no server round-trip, no query.
    assert dismiss["type"] == "dismiss" and dismiss["kind"] == "normal"
    assert "query" not in dismiss
    # the server-dispatched Add action conforms to the component-action contract
    validated = validate_component_action(add)
    assert validated["query"] == "add Daniel as my brother" and validated["kind"] == "primary"


def test_person_confirm_card_without_relationship():
    card = sky._person_confirm_card("Priya Sharma", None)
    props = card["props"]
    assert props["title"] == "Add Priya Sharma?"
    assert props["actions"][0]["query"] == "add Priya Sharma to my contacts"


@pytest.mark.asyncio
async def test_resolve_pending_offers_builds_cards(monkeypatch):
    async def fake_list(user_id, *, limit=6):
        return [
            {"id": "s1", "name": "Daniel", "relationship": "brother"},
            {"id": "s2", "name": "Fiona", "relationship": None},
            {"id": "s3", "name": "", "relationship": "x"},  # skipped: no name
        ]

    import pending_suggestions
    monkeypatch.setattr(pending_suggestions, "list_pending_contacts", fake_list)

    result = await sky._resolve_people_pending_offers("u1", None)
    assert result["handled"] is True
    assert result["intent"] == {"domain": "people", "action": "pending_offers"}
    cards = result["cards"]
    assert len(cards) == 2  # the nameless offer is dropped
    assert cards[0]["component"] == "person_confirm"
    assert cards[0]["props"]["title"] == "Add Daniel as your brother?"
    assert cards[0]["props"]["suggestion_id"] == "s1"
    assert cards[1]["props"]["title"] == "Add Fiona?"


@pytest.mark.asyncio
async def test_resolve_pending_offers_empty(monkeypatch):
    async def fake_list(user_id, *, limit=6):
        return []

    import pending_suggestions
    monkeypatch.setattr(pending_suggestions, "list_pending_contacts", fake_list)

    result = await sky._resolve_people_pending_offers("u1", None)
    assert result["handled"] is True
    assert len(result["cards"]) == 1
    assert result["cards"][0]["component"] == "status"
    assert result["cards"][0]["props"]["title"] == "No contact suggestions"
