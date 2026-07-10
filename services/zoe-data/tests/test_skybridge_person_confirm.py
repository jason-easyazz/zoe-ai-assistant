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


def test_classify_offers_regex_does_not_steal_add_to_group():
    # "contacts to add to my family" is adding someone to a group (a directory op),
    # NOT a request to surface pending offers — the (?!\s+to\b) guard must hold.
    intent = sky.classify_skybridge_intent("contacts to add to my family")
    assert intent is not None and intent.action != "pending_offers"


@pytest.mark.parametrize("msg,name,rel", [
    ("add Daniel as my brother", "Daniel", "brother"),
    ("add John Smith as my friend", "John Smith", "friend"),
    ("add Jean-Claude van Damme as my friend", "Jean-Claude van Damme", "friend"),
    ("add Mary Jane Watson to my contacts", "Mary Jane Watson", ""),
    ("add José as my brother", "José", "brother"),           # accented name
    ("add Sarah as my mom's friend", "Sarah", "mom's friend"),  # apostrophe in relationship
    ("add O'Brien to my contacts", "O'Brien", ""),           # apostrophe in name
    ("add Sarah to my contacts", "Sarah", ""),
    ("save Priya as my colleague", "Priya", "colleague"),
])
def test_classify_people_create(msg, name, rel):
    intent = sky.classify_skybridge_intent(msg)
    assert intent is not None, msg
    assert intent.domain == "people" and intent.action == "create", msg
    assert intent.person_name == name and intent.relationship == rel, msg


@pytest.mark.asyncio
async def test_resolve_people_create_success(monkeypatch):
    calls = {}

    async def fake_create(intent, user_id):
        calls["name"] = intent.slots.get("name")
        calls["rel"] = intent.slots.get("relationship")
        calls["user"] = user_id
        return f"Added {intent.slots.get('name')}."

    import intent_router
    monkeypatch.setattr(intent_router, "_execute_people_create_direct", fake_create)

    # the matching pending offer must be resolved so the card doesn't re-surface
    resolved = {}

    async def fake_resolve(user_id, name):
        resolved["user"] = user_id
        resolved["name"] = name
        return 1

    import pending_suggestions
    monkeypatch.setattr(pending_suggestions, "resolve_person_offers_by_name", fake_resolve)

    intent = sky.SkybridgeIntent(domain="people", action="create", person_name="Daniel", relationship="brother")
    result = await sky._resolve_people_create(intent, "u1", None)
    assert result["handled"] is True
    assert calls == {"name": "Daniel", "rel": "brother", "user": "u1"}
    assert resolved == {"user": "u1", "name": "Daniel"}  # offer resolved after create
    assert "Added Daniel" in result["spoken_summary"]
    assert result["cards"][0]["props"]["title"] == "Added Daniel"


@pytest.mark.asyncio
async def test_resolve_person_offers_collapses_whitespace(monkeypatch):
    # A stored offer name with a double space must still match the collapsed name
    # that was created, so the offer is resolved (doesn't re-surface).
    import contextlib
    import pending_suggestions as ps

    executed = []

    class _DB:
        async def fetch(self, sql, *args):
            return [{"id": "s1", "pre_filled_slots": '{"name": "Daniel  Smith"}'}]  # 2 spaces

        async def execute(self, sql, *args):
            executed.append(args)

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield _DB()

    monkeypatch.setattr(ps, "get_db_ctx", fake_ctx)
    n = await ps.resolve_person_offers_by_name("u1", "Daniel Smith")  # single space
    assert n == 1 and executed, "offer must match despite the whitespace difference"


@pytest.mark.asyncio
async def test_resolve_people_create_failure_surfaces_card(monkeypatch):
    async def fake_create(intent, user_id):
        return None  # genuine failure

    import intent_router
    monkeypatch.setattr(intent_router, "_execute_people_create_direct", fake_create)

    intent = sky.SkybridgeIntent(domain="people", action="create", person_name="Daniel", relationship="brother")
    result = await sky._resolve_people_create(intent, "u1", None)
    assert result["handled"] is True
    assert result["cards"][0]["props"]["title"] == "Couldn't add contact"


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
