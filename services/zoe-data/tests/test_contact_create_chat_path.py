"""A contact-create COMMAND must end in a people row, on every fast-tier channel.

Samantha bar S13/S14 (2026-10-05): "Save a contact for my brother Percival." reached
the people domain, ``expert_dispatch._plan`` filed it as the *expert* kind (taught fact),
``store_fact`` answered "Got it — I'll remember save a contact for your brother
Percival." and NO contact was created; "Who is Percival?" then found nobody.

Class: a regex-recognised WRITE (people_create) must never be handled by the
memory-fact expert. It is kind "direct": executed with its regex slots where writes
are allowed, deferred to the channel's own intent lane (chat) where they are not.

Synthetic names only. Negative control: revert the ``_plan`` branch and the
``*_creates_the_row`` / ``test_never_stored_as_a_fact_regression`` cases go red.
"""
import pytest

pytestmark = pytest.mark.ci_safe

import expert_dispatch
import fast_tiers
from intent_router import detect_and_extract_intent, detect_intent, execute_intent

from tests.test_contacts_conversation import _PeopleDB, _install  # the same tiny people-table fake

USER = "demo_contact_create_user"  # a DEMO user, never a real person

SETUP_TURN = "Save a contact for my brother Percival."
CASES = [
    (SETUP_TURN, "Percival", "brother"),
    ("save my brother Percival as a contact", "Percival", "brother"),
    ("add my brother Percival", "Percival", "brother"),
    ("Save a contact for my brother Percival Jones", "Percival Jones", "brother"),
    # lowercase typing / STT: nothing shows a capital, the relation phrase identifies the person
    ("add my brother percival", "Percival", "brother"),
    ("Add my brother percival.", "Percival", "brother"),
    ("save my brother percival as a contact", "Percival", "brother"),
]
PEOPLE_ROUTE = {"domain": "people", "score": 0.95, "two_stage": True}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for f in ("ZOE_CONTACT_OFFER_BATCH", "ZOE_PERSON_SUGGEST_ENABLED", "ZOE_SEAM_OFFER_INJECT"):
        monkeypatch.delenv(f, raising=False)
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "1")
    monkeypatch.setenv("ZOE_EXPERT_MODE", "active")
    monkeypatch.setenv("ZOE_EXPERT_ACTIVE_DOMAINS", "people,memory")
    monkeypatch.setenv("ZOE_EXPERT_ALLOW_WRITES", "1")
    monkeypatch.setenv("ZOE_INTENT_ROUTER_GATE", "0")  # the head is not under test here
    import contacts_conversation as cc

    cc._ASKED.clear()
    cc._SAME_PENDING.clear()

    async def no_links(_uid, ids, timeout=1.5):
        return set()

    async def no_mirror(*_a, **_k):
        return None

    monkeypatch.setattr(cc, "linked_memory_ids", no_links)
    monkeypatch.setattr(cc, "refresh_person_mirror", no_mirror)

    async def no_slot_llm(*_a, **_k):
        raise AssertionError("a regex-complete contact command needs no LLM slot extraction")

    monkeypatch.setattr("nlu_extractor.extract_slots_for_intent", no_slot_llm)


def _rows(db):
    return [{"name": p["name"], "relationship": p["relationship"]} for p in db.people]


FACT_REPLY = "Got it — I'll remember save a contact for your brother Percival."
STORE_FACT_CALLS = []


def _facts_must_not_be_stored(monkeypatch):
    """store_fact answers like production did (a non-None reply). Raising instead is
    swallowed by dispatch's broad except -> None, which hides a revert."""
    STORE_FACT_CALLS.clear()

    async def old_store_fact(domain, text, *a, **k):
        STORE_FACT_CALLS.append(text)
        return FACT_REPLY

    monkeypatch.setattr(expert_dispatch, "store_fact", old_store_fact)


# ── detection ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, name, rel", CASES)
def test_every_phrasing_detects_people_create(text, name, rel):
    got = detect_intent(text, log_miss=False)
    assert got is not None and got.name == "people_create"
    assert got.slots["name"] == name and got.slots["relationship"] == rel


@pytest.mark.parametrize("text", [
    "add my dad some beer",                   # a list item: no capitalised name, no contact cue
    "add my mate beer to the shopping list",  # explicit list target
    "add my mum a gift",
    "add my mum",                             # a bare relation asks for the name elsewhere
    "add my brother's present",
    # CAPITALISED items / days / shops (the capital letter alone is no guard)
    "add my dad Beer", "add my mum Pizza", "add my wife Eggs", "add my mate Bunnings",
    "add my dad Ice Cream", "add my brother Percival Friday", "add my mate Dan Saturday",
    "add my mum Sarah Sunday", "add my brother Percival Jones Smith",
    "Add my dad pizza", "add my dad socks", "add my brother percival friday",
    "save my brother Percival Friday as a contact",   # a day word is not a name even WITH a cue
])
def test_relation_first_negative_controls_stay_list_turns(text):
    got = detect_intent(text, log_miss=False)
    assert got is None or got.name != "people_create"


# ── the fast-tier channels (Tier-1.5, the exact hop that swallowed the write) ──


def test_plan_files_a_contact_command_as_a_direct_write_not_an_expert_fact():
    assert expert_dispatch._plan("people", SETUP_TURN)[2] == "direct"
    # negative control: a taught fact and a lookup keep their kinds
    assert expert_dispatch._plan("people", "My brother Percival is allergic to nuts")[2] == "expert"
    assert expert_dispatch._plan("people", "Find a contact named Percival")[2] == "read"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize("text, name, rel", CASES)
async def test_writable_channel_creates_the_row(monkeypatch, text, name, rel, flag):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", flag)
    db = _PeopleDB()
    _install(monkeypatch, db)
    _facts_must_not_be_stored(monkeypatch)
    res = await fast_tiers.resolve(text, USER, "sess-1", channel="telegram", router_decision=PEOPLE_ROUTE)
    assert res is not None and res.intent == "people_create" and res.reply != FACT_REPLY
    assert _rows(db) == [{"name": name, "relationship": rel}] and not STORE_FACT_CALLS


@pytest.mark.asyncio
@pytest.mark.parametrize("text, name, rel", CASES)
async def test_chat_defers_to_its_own_intent_lane_which_writes(monkeypatch, text, name, rel):
    """chat: allow_writes=False -> resolve() hands the turn on, then the REAL
    detect_and_extract_intent -> execute_intent pair creates the row."""
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB()
    _install(monkeypatch, db)
    _facts_must_not_be_stored(monkeypatch)
    res = await fast_tiers.resolve(text, USER, "sess-2", channel="chat", router_decision=PEOPLE_ROUTE)
    assert res is None and _rows(db) == [] and not STORE_FACT_CALLS   # deferred, nothing swallowed it
    intent = await detect_and_extract_intent(text, USER)
    assert intent is not None and intent.name == "people_create"
    reply = await execute_intent(intent, USER)
    assert name in reply and rel in reply
    assert _rows(db) == [{"name": name, "relationship": rel}]


@pytest.mark.asyncio
async def test_never_stored_as_a_fact_regression(monkeypatch):
    """The 2026-10-05 reply must be unreachable even if the fact expert would answer it."""
    db = _PeopleDB()
    _install(monkeypatch, db)

    async def old_store_fact(domain, text, *a, **k):
        return "Got it — I'll remember save a contact for your brother Percival."

    monkeypatch.setattr(expert_dispatch, "store_fact", old_store_fact)
    res = await fast_tiers.resolve(SETUP_TURN, USER, "sess-3", channel="telegram", router_decision=PEOPLE_ROUTE)
    assert res is not None and "remember" not in res.reply.lower()
    assert _rows(db) == [{"name": "Percival", "relationship": "brother"}]


# ── channels that cannot bind a follow-up, and the acting user ───────────────


@pytest.mark.asyncio
async def test_livekit_states_the_outcome_instead_of_asking_an_unanswerable_question(monkeypatch):
    import contacts_conversation as cc

    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    stub = {"id": "1", "name": "Dan", "relationship": "friend", "birthday": None, "phone": "555",
            "email": None, "notes": None, "how_we_met": None, "deleted": 0, "is_partial": 0}
    text = "Save a contact for my friend Dan Murphy"
    # control: telegram CAN bind "yes", so it asks and queues the question
    db = _PeopleDB([dict(stub)])
    _install(monkeypatch, db)
    res = await fast_tiers.resolve(text, USER, "s", channel="telegram", router_decision=PEOPLE_ROUTE)
    assert "same person" in res.reply and cc.peek_same_person(USER) is not None
    cc._SAME_PENDING.clear()
    # livekit cannot: no question, nothing queued, nothing written, the reply says why
    db = _PeopleDB([dict(stub)])
    _install(monkeypatch, db)
    res = await fast_tiers.resolve(text, USER, "s", channel="livekit", router_decision=PEOPLE_ROUTE)
    assert "?" not in res.reply
    assert "already have a Dan" in res.reply and "Dan Murphy" in res.reply
    assert cc.peek_same_person(USER) is None and _rows(db) == [{"name": "Dan", "relationship": "friend"}]


@pytest.mark.asyncio
async def test_direct_write_runs_as_the_acting_user_not_the_guest_alias(monkeypatch):
    db = _PeopleDB()
    _install(monkeypatch, db)
    res = await fast_tiers.resolve(SETUP_TURN, "family-admin", "s", channel="telegram", router_decision=PEOPLE_ROUTE)
    assert res is not None
    (_sql, params), = db.inserts()
    assert params[1] == "family-admin"          # not "guest"


def test_confirm_prompt_names_the_relationship_the_user_said():
    import contacts_conversation as cc

    assert cc.confirm_create_phrase("Percival", "brother") == "Add Percival, your brother, to your contacts?"
    # the detector's default 'friend' / no relation was never said: the old prompt stays
    assert cc.confirm_create_phrase("Ottoline", "friend") == "Add contact Ottoline. Shall I confirm?"
    assert cc.confirm_create_phrase("Ottoline", None) == "Add contact Ottoline. Shall I confirm?"
