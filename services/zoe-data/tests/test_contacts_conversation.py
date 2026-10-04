"""Contacts conversation classes (2026-10-04, "it doesn't work like a human assistant").

Class 1  "who is in/on my contacts" is a LIST request, never a name search.
Class 2  "save a contact for my brother Kyle" -> name Kyle, relationship brother.
Class 3  several pending contact offers are ONE question; a yes answers the set.
Class 4  lookups are sentences, de-duplicated (first-name-only + fuller record),
         and a save merges into the fuller record instead of minting a second row.

Names here are synthetic. Every behaviour change has a negative control: the
flag-OFF assertion (old behaviour byte-for-byte) or the "must NOT" case beside it.
"""
import contextlib
import re

import pytest

pytestmark = pytest.mark.ci_safe

import contacts_conversation as cc
import intent_router
from intent_router import (
    Intent,
    _execute_people_create_direct,
    _execute_people_search_direct,
    _match_pending_offer_reply,
    detect_intent,
    execute_intent,
)

USER = "demo_contacts_conv_user"  # a DEMO user, never a real person


# ── A tiny people-table fake that understands the handlers' SQL ───────────────


class _Cur:
    def __init__(self, rows=()):
        self._rows = list(rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _PeopleDB:
    def __init__(self, people=()):
        self.people = [dict(p) for p in people]
        self.calls = []

    async def execute(self, sql, params=()):
        params = tuple(params)
        self.calls.append((sql, params))
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO users"):
            return _Cur()
        if s.startswith("INSERT INTO people"):
            self.people.append({"id": f"new-{len(self.people)}", "name": params[2],
                                "relationship": params[3], "deleted": 0})
            return _Cur()
        if s.startswith("UPDATE people SET name"):
            for p in self.people:
                if p["id"] == params[3]:
                    p["name"], p["relationship"] = params[0], params[1]
            return _Cur()
        if "lower(name) = lower(?)" in s:  # exact-name dedupe
            return _Cur([p for p in self.people if p["name"].lower() == params[1].lower()][:1])
        if "lower(name) LIKE lower(?)" in s:  # first-name prefix (merge-on-save)
            pre = params[1].rstrip("%").lower()
            return _Cur([p for p in self.people if p["name"].lower().startswith(pre)])
        if s.startswith("SELECT") and "FROM people" in s:
            return _Cur(self._select(s, params))
        return _Cur()

    def _select(self, s, params):
        if "relationship ILIKE" in s:
            pat = params[0].strip("%").replace("\\", "").lower()
            return [p for p in self.people
                    if pat in p["name"].lower() or pat in (p.get("relationship") or "").lower()]
        if "name ILIKE" in s:
            pat = params[0].strip("%").replace("\\", "").lower()
            return [p for p in self.people if pat in p["name"].lower()]
        return list(self.people)

    def inserts(self):
        return [c for c in self.calls if c[0].lstrip().startswith("INSERT INTO people")]


def _install(monkeypatch, db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr("database.get_db_ctx", ctx)
    monkeypatch.setattr("intent_router._notify_ui_channel", noop)

    async def no_mcporter(_cmd):
        raise AssertionError("the direct path must not fall back to mcporter")

    monkeypatch.setattr("intent_router._run_mcporter", no_mcporter)


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in ("ZOE_CONTACTS_CONVERSATIONAL", "ZOE_CONTACT_OFFER_BATCH",
              "ZOE_PERSON_SUGGEST_ENABLED", "ZOE_SEAM_OFFER_INJECT"):
        monkeypatch.delenv(f, raising=False)


def P(pid, name, rel=None, **kw):
    return {"id": pid, "name": name, "relationship": rel, "birthday": None,
            "phone": None, "email": None, "notes": None, "deleted": 0, **kw}


# ═════════════ Class 1: list-all, never a non-name search ═════════════════════


@pytest.mark.parametrize("text", [
    "Who is in my contacts", "Who is on my contacts", "who's in my contacts?",
    "who is on my contacts?", "list my contacts", "list all my contacts",
    "show me my contacts", "who do I have saved", "who do I have in my contacts",
    "read out my address book", "how many contacts do I have",
])
def test_list_all_phrasings_route_to_the_empty_query_list(text):
    got = detect_intent(text, log_miss=False)
    assert got is not None and got.name == "people_search" and got.slots == {"query": ""}, text


@pytest.mark.parametrize("text, query", [
    ("Who is Caitlin", "caitlin"),            # a real name still searches
    ("who is my dentist", "my dentist"),      # a person reference still searches
])
def test_name_lookups_are_unchanged(text, query):  # negative control for the list-all patterns
    got = detect_intent(text, log_miss=False)
    assert got is not None and got.name == "people_search" and got.slots["query"] == query


def test_clause_is_still_not_a_contacts_lookup():  # the 2026-09-28 S1 guard survives
    got = detect_intent("Who is flying in on Thursday, and where from?", log_miss=False)
    assert got is None or got.name != "people_search"


@pytest.mark.parametrize("q, expect", [
    ("in my contacts", ("list", "")), ("on my contacts", ("list", "")),
    ("who is in my contacts", ("list", "")), ("my contacts list", ("list", "")),
    ("Caitlin in my contacts", ("name", "Caitlin")), ("caitlin", ("name", "caitlin")),
    ("my brother", ("rel", "brother")), ("the dentist", ("rel", "dentist")),
    ("on thursday", ("none", "")), ("flying in on thursday, and where from", ("none", "")),
])
def test_classify_contacts_query(q, expect):
    assert cc.classify_contacts_query(q) == expect


@pytest.mark.asyncio
@pytest.mark.parametrize("q", ["in my contacts", "on my contacts"])
async def test_handler_lists_instead_of_searching_a_non_name(monkeypatch, q):
    db = _PeopleDB([P("1", "Odile Marsh", "friend"), P("2", "Kyle", "brother")])
    _install(monkeypatch, db)
    out = await _execute_people_search_direct(Intent("people_search", {"query": q}), USER)
    assert "No contacts found" not in out
    assert "Odile Marsh" in out and "Kyle" in out
    # the phrase never reached a LIKE pattern
    assert not any(q in str(p) for _s, p in db.calls)


@pytest.mark.asyncio
async def test_handler_negative_control_real_name_still_searches(monkeypatch):
    db = _PeopleDB([P("1", "Odile Marsh", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_search_direct(Intent("people_search", {"query": "odile"}), USER)
    assert "Odile Marsh" in out
    miss = await _execute_people_search_direct(Intent("people_search", {"query": "zzz"}), USER)
    assert miss == 'No contacts found for "zzz".'


@pytest.mark.asyncio
async def test_handler_never_searches_a_clause(monkeypatch):
    db = _PeopleDB([P("1", "Odile Marsh", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_search_direct(Intent("people_search", {"query": "on thursday"}), USER)
    assert "No contacts found" not in out and "What name" in out
    assert db.calls == []  # no DB work for a non-name


@pytest.mark.asyncio
async def test_my_relation_searches_the_relationship_field(monkeypatch):
    db = _PeopleDB([P("1", "Kyle", "brother"), P("2", "Odile Marsh", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_search_direct(Intent("people_search", {"query": "my brother"}), USER)
    assert "Kyle" in out and "Odile" not in out


@pytest.mark.asyncio
async def test_empty_contacts_reply_is_not_a_failed_search(monkeypatch):
    _install(monkeypatch, _PeopleDB())
    out = await _execute_people_search_direct(Intent("people_search", {"query": ""}), USER)
    assert out == "You don't have any contacts saved yet."


@pytest.mark.asyncio
async def test_list_summary_is_conversational_when_flag_on(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Odile Marsh", "friend"), P("2", "Wren", "friend"),
                    P("3", "Kyle", "brother"), P("4", "Niel", "father")])
    _install(monkeypatch, db)
    out = await _execute_people_search_direct(Intent("people_search", {"query": "in my contacts"}), USER)
    assert out.startswith("You have 4 contacts.")
    assert "Friends: Odile Marsh and Wren." in out and "Brother: Kyle." in out
    assert "Found:" not in out
    # flag OFF keeps the old bare list (negative control)
    monkeypatch.delenv("ZOE_CONTACTS_CONVERSATIONAL")
    old = await _execute_people_search_direct(Intent("people_search", {"query": ""}), USER)
    assert old.startswith("Found:")


def test_list_summary_caps_names_with_and_n_more():
    rows = [P(str(i), f"Person{chr(65 + i)}", "friend") for i in range(15)]
    out = cc.format_contact_list(rows)
    assert out.startswith("You have 15 contacts.") and out.endswith("And 3 more.")


# ═════════════ Class 2: relation phrase out of the name ═══════════════════════


def test_create_regex_strips_the_relation_phrase():
    got = detect_intent("Save a contact for my brother Kyle", log_miss=False)
    assert got.name == "people_create"
    assert got.slots["name"] == "Kyle" and got.slots["relationship"] == "brother"


@pytest.mark.parametrize("text, name, rel", [
    ("add a contact for my niece Wren Alder", "Wren Alder", "niece"),
    ("add a contact named Pell as my mate", "Pell", "friend"),
    ("add a contact for my boss Odile", "Odile", "boss"),
    ("save a contact for my friend Sam", "Sam", "friend"),
])
def test_create_regex_relation_variants(text, name, rel):
    got = detect_intent(text, log_miss=False)
    assert got.slots["name"] == name and got.slots["relationship"] == rel


def test_create_regex_negative_control_plain_name_unchanged():
    got = detect_intent("add a contact named Pell Ardent", log_miss=False)
    assert got.slots["name"] == "Pell Ardent" and got.slots["relationship"] == "friend"


@pytest.mark.parametrize("raw, name, rel", [
    ("My Brother Kyle", "Kyle", "brother"),
    ("my mum Odile", "Odile", "mother"),
    ("Kyle, my mate", "Kyle", "friend"),
    ("Kyle (my boss)", "Kyle", "boss"),
    ("Pop Tarr", "Pop Tarr", None),            # a name that merely contains a relation word
    ("Kyle", "Kyle", None),
])
def test_split_relation_from_name(raw, name, rel):
    assert cc.split_relation_from_name(raw) == (name, rel)


@pytest.mark.asyncio
async def test_brain_tool_slots_are_repaired_in_the_handler(monkeypatch):
    """The Flue people tool passes name="My Brother Kyle" + the default 'friend'."""
    db = _PeopleDB()
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "My Brother Kyle", "relationship": "friend"}), USER)
    assert out == "Added Kyle as your brother to your contacts."     # flag OFF wording unchanged
    (_sql, params), = db.inserts()
    assert "Kyle" in params and "brother" in params and "My Brother Kyle" not in params


@pytest.mark.asyncio
async def test_conversational_create_reply(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB()
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "my brother Kyle", "relationship": "friend"}), USER)
    assert out == "Added Kyle, your brother."


@pytest.mark.asyncio
async def test_specific_slot_relationship_beats_nothing_but_generic(monkeypatch):
    db = _PeopleDB()
    _install(monkeypatch, db)
    await _execute_people_create_direct(
        Intent("people_create", {"name": "my friend Sam", "relationship": "colleague"}), USER)
    (_sql, params), = db.inserts()
    assert "colleague" in params  # an explicit non-generic relationship is kept


@pytest.mark.asyncio
async def test_bare_relation_is_asked_not_saved(monkeypatch):
    db = _PeopleDB()
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(Intent("people_create", {"name": "my boss"}), USER)
    assert out == "What's your boss's name?" and db.inserts() == []


# ═════════════ Class 4: sentences, duplicates, merge-on-save ══════════════════


@pytest.mark.parametrize("a, ra, b, rb, kind", [
    ("Caitlin", "friend", "Caitlin Farrell", "friend", "a_stub"),
    ("Caitlin Farrell", "friend", "Caitlin", None, "b_stub"),
    ("Caitlin", "friend", "Caitlin", "friend", "same"),
    ("Caitlin Farrell", "friend", "Caitlin Hale", "friend", None),   # different surnames
    ("Caitlin", "friend", "Caitlin Farrell", "niece", None),         # clashing relations
    ("Cat", "friend", "Caitlin Farrell", "friend", None),            # different first names
])
def test_duplicate_kind(a, ra, b, rb, kind):
    assert cc.duplicate_kind(a, ra, b, rb) == kind


def test_collapse_keeps_the_fuller_record_and_fills_blanks():
    rows = [P("1", "Caitlin", "friend", phone="555"), P("2", "Caitlin Farrell", "friend"),
            P("3", "Caitlin Hale", "friend")]
    out = cc.collapse_duplicates(rows)
    assert [r["name"] for r in out] == ["Caitlin Farrell", "Caitlin Hale"]
    assert out[0]["phone"] == "555" and out[0]["_merged_ids"] == ["1"]


def test_find_duplicate_groups_reports_per_user_only():
    rows = [P("1", "Caitlin", "friend", user_id="u1"), P("2", "Caitlin Farrell", "friend", user_id="u1"),
            P("3", "Caitlin Farrell", "friend", user_id="u2")]
    groups = cc.find_duplicate_groups(rows)
    assert len(groups) == 1 and {r["id"] for r in groups[0]} == {"1", "2"}


@pytest.mark.asyncio
async def test_lookup_is_one_deduped_sentence_with_facts(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Caitlin", "friend"), P("2", "Caitlin Farrell", "friend")])
    _install(monkeypatch, db)

    async def facts(_uid, person, limit=2):
        assert person["id"] == "2" and person["_merged_ids"] == ["1"]
        return ["Caitlin is allergic to shellfish and nuts"]

    monkeypatch.setattr("intent_router._contact_facts", facts)
    out = await _execute_people_search_direct(Intent("people_search", {"query": "caitlin"}), USER)
    assert out == ("Caitlin Farrell is your friend. "
                   "You've told me Caitlin is allergic to shellfish and nuts.")
    assert "Found:" not in out and out.count("Caitlin Farrell") == 1


@pytest.mark.asyncio
async def test_lookup_flag_off_keeps_the_old_list(monkeypatch):  # negative control
    db = _PeopleDB([P("1", "Caitlin", "friend"), P("2", "Caitlin Farrell", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_search_direct(Intent("people_search", {"query": "caitlin"}), USER)
    assert out == "Found:\n  - Caitlin (friend)\n  - Caitlin Farrell (friend)"


@pytest.mark.asyncio
async def test_two_different_people_are_both_named(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Caitlin Farrell", "friend"), P("2", "Caitlin Hale", "colleague")])
    _install(monkeypatch, db)
    out = await _execute_people_search_direct(Intent("people_search", {"query": "caitlin"}), USER)
    assert out.startswith("I have 2 people matching") and "Caitlin Hale, your colleague" in out


@pytest.mark.asyncio
async def test_not_found_is_a_sentence_when_flag_on(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    _install(monkeypatch, _PeopleDB([P("1", "Odile Marsh")]))
    out = await _execute_people_search_direct(Intent("people_search", {"query": "zzz"}), USER)
    assert out == 'I don\'t have anyone called "zzz" in your contacts.'


def test_fact_clause_revoices_compact_rows():
    assert cc.fact_clause("Caitlin: March 15", "Caitlin Farrell", "birthday") == "Caitlin's birthday is March 15"
    assert cc.fact_clause("Caitlin is allergic to nuts.", "Caitlin Farrell") == "Caitlin is allergic to nuts"


@pytest.mark.asyncio
async def test_save_of_first_name_when_fuller_record_exists_adds_nothing(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("2", "Caitlin Farrell", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "Caitlin", "relationship": "friend"}), USER)
    assert "already have Caitlin Farrell" in out and db.inserts() == []


@pytest.mark.asyncio
async def test_save_of_fuller_name_upgrades_the_stub_in_place(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Caitlin", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "Caitlin Farrell", "relationship": "friend"}), USER)
    assert "updated that contact to Caitlin Farrell" in out
    assert db.inserts() == [] and [p["name"] for p in db.people] == ["Caitlin Farrell"]


@pytest.mark.asyncio
async def test_ambiguous_first_name_is_asked_not_guessed(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Caitlin Farrell", "friend"), P("2", "Caitlin Hale", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "Caitlin", "relationship": "friend"}), USER)
    assert "Which one do you mean?" in out and db.inserts() == []


@pytest.mark.asyncio
async def test_save_negative_controls_new_person_and_flag_off(monkeypatch):
    db = _PeopleDB([P("1", "Caitlin", "friend")])
    _install(monkeypatch, db)
    # flag OFF: the old behaviour (a second row) is untouched
    await _execute_people_create_direct(
        Intent("people_create", {"name": "Caitlin Farrell", "relationship": "friend"}), USER)
    assert len(db.inserts()) == 1
    # flag ON: a different surname is a different person
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    await _execute_people_create_direct(
        Intent("people_create", {"name": "Caitlin Hale", "relationship": "friend"}), USER)
    assert len(db.inserts()) == 2


# ═════════════ Class 3: one enumerated question, one yes ══════════════════════

FAMILY = [
    {"id": "o1", "name": "Rodrigo", "relationship": "brother"},
    {"id": "o2", "name": "Jessika", "relationship": "sister"},
    {"id": "o3", "name": "Wren", "relationship": ""},
]


def _offers(monkeypatch, offers):
    import pending_suggestions as ps

    async def surfaced(_uid, *, limit=5):
        return [dict(o) for o in offers[:limit]]

    monkeypatch.setattr(ps, "surfaced_person_offers", surfaced)
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")


def test_offer_question_enumerates_and_keeps_the_single_wording():
    assert cc.offer_question(FAMILY) == (
        "Would you like me to add Rodrigo (your brother), Jessika (your sister) and Wren to your contacts?")
    assert cc.offer_question(FAMILY[:1]) == "Would you like me to add Rodrigo (your brother) as a contact?"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "yes", "Yes please", "yes!", "all of them", "add them all", "add his whole family",
    "yes add everyone", "sure, all of them", "yes add Rodrigo and Jessika and Wren",
])
async def test_a_yes_accepts_the_whole_set(monkeypatch, reply):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY)
    got = await _match_pending_offer_reply(reply, USER)
    assert got is not None and got.name == "pending_offer_accept", reply
    assert got.slots["suggestion_ids"] == ["o1", "o2", "o3"] and got.slots["dismiss_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, accepted, dropped", [
    ("just Rodrigo", ["o1"], ["o2", "o3"]),
    ("only Jessika please", ["o2"], ["o1", "o3"]),
    ("yes add Rodrigo", ["o1"], []),
    ("yes Rodrigo and Wren", ["o1", "o3"], []),
])
async def test_a_named_reply_accepts_just_those(monkeypatch, reply, accepted, dropped):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY)
    got = await _match_pending_offer_reply(reply, USER)
    assert got.name == "pending_offer_accept"
    assert got.slots["suggestion_ids"] == accepted and got.slots["dismiss_ids"] == dropped


@pytest.mark.asyncio
async def test_a_no_drops_the_whole_set(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY)
    got = await _match_pending_offer_reply("no thanks", USER)
    assert got.name == "pending_offer_dismiss" and got.slots["suggestion_ids"] == ["o1", "o2", "o3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["yes let's book the flight", "just kidding", "all good thanks"])
async def test_unrelated_replies_are_not_hijacked(monkeypatch, reply):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY)
    assert await _match_pending_offer_reply(reply, USER) is None


@pytest.mark.asyncio
async def test_flag_off_a_bare_yes_still_binds_only_the_oldest(monkeypatch):  # negative control
    _offers(monkeypatch, FAMILY)
    got = await _match_pending_offer_reply("yes", USER)
    assert got.slots["suggestion_id"] == "o1" and "suggestion_ids" not in got.slots
    assert await _match_pending_offer_reply("all of them", USER) is None
    assert await _match_pending_offer_reply("add his whole family", USER) is None


@pytest.mark.asyncio
async def test_batch_accept_executes_every_offer_and_names_them(monkeypatch):
    import pending_suggestions as ps

    done, cleared = [], []

    async def execute(sid, uid):
        done.append(sid)
        return {"ok": sid != "o3"}

    async def resolved(sid, uid):
        cleared.append(sid)
        return True

    monkeypatch.setattr(ps, "execute_suggestion", execute)
    monkeypatch.setattr(ps, "mark_resolved", resolved)
    intent = Intent("pending_offer_accept", {
        "suggestion_id": "o1", "name": "Rodrigo",
        "suggestion_ids": ["o1", "o2", "o3"], "names": ["Rodrigo", "Jessika", "Wren"],
        "dismiss_ids": [], "dismiss_names": []})
    out = await execute_intent(intent, USER)
    assert done == ["o1", "o2", "o3"]
    assert out.startswith("Done — I've added Rodrigo and Jessika to your contacts.")
    assert "couldn't save Wren" in out  # a failure is named, never reported as saved
    # "just Rodrigo": one accepted, the rest cleared
    done.clear()
    just = Intent("pending_offer_accept", {
        "suggestion_id": "o1", "name": "Rodrigo", "suggestion_ids": ["o1"], "names": ["Rodrigo"],
        "dismiss_ids": ["o2", "o3"], "dismiss_names": ["Jessika", "Wren"]})
    out = await execute_intent(just, USER)
    assert done == ["o1"] and cleared == ["o2", "o3"]
    assert out == "Done — I've added Rodrigo to your contacts. I've left Jessika and Wren out."


@pytest.mark.asyncio
async def test_single_offer_execution_text_is_unchanged(monkeypatch):  # negative control
    import pending_suggestions as ps

    async def execute(sid, uid):
        return {"ok": True}

    monkeypatch.setattr(ps, "execute_suggestion", execute)
    out = await execute_intent(
        Intent("pending_offer_accept", {"suggestion_id": "o1", "name": "Rodrigo"}), USER)
    assert out == "Done — I've added Rodrigo to your contacts."


def test_seam_offer_block_is_one_question_when_batched(monkeypatch):
    import asyncio

    import pending_suggestions as ps
    import zoe_flue_client as zf

    monkeypatch.setenv("ZOE_SEAM_OFFER_INJECT", "1")
    monkeypatch.setattr(ps, "person_suggestions_enabled", lambda: True)

    async def surface(_uid, *, limit=3):
        return [dict(o) for o in FAMILY[:limit]]

    monkeypatch.setattr(ps, "surface_pending_contacts_for_prompt", surface)
    # flag OFF: legacy shape - one ask line per offer (capped at two), asked one by one
    legacy = asyncio.run(zf._pending_offer_block(USER))
    assert legacy.count("ask the user exactly") == 2 and "Wren" not in legacy
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    block = asyncio.run(zf._pending_offer_block(USER))
    assert block.count("ask the user exactly") == 1
    assert "add Rodrigo (your brother), Jessika (your sister) and Wren to your contacts?" in block


# ═════════════ "add his whole family" is people, not shopping ═════════════════


@pytest.mark.parametrize("text", [
    "add his whole family", "add her whole family", "add everyone", "add them all",
    "add all of them", "add the kids", "please add their children",
])
def test_adding_people_is_never_a_shopping_item(text):
    got = detect_intent(text, log_miss=False)
    assert got is None or got.name != "list_add", (text, got)


@pytest.mark.parametrize("text, item", [
    ("add milk", "milk"), ("add kids cereal", "kids cereal"), ("add family size pizza", "family size pizza"),
])
def test_shopping_adds_are_unchanged(text, item):  # negative control
    got = detect_intent(text, log_miss=False)
    assert got.name == "list_add" and got.slots["item"] == item


def test_list_all_inherits_the_guest_gate():  # the list-all path is the same people_search intent
    assert any("people_search".startswith(p) for p in intent_router._GUEST_GATED_INTENTS)
