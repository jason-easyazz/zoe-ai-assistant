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
            # (name, relationship-or-None [COALESCE keeps the old one], updated_at, id, user_id)
            for p in self.people:
                if p["id"] == params[3] and not p.get("deleted"):
                    p["name"] = params[0]
                    p["relationship"] = params[1] or p.get("relationship")
                    p["is_partial"] = 0
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
        if "lower(trim(relationship)) IN" in s:  # whole-value, any alias, relationship ONLY
            aliases = set(params[:-1])
            return sorted((p for p in self.people
                           if (p.get("relationship") or "").strip().lower() in aliases),
                          key=lambda p: p["name"])
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
    cc._ASKED.clear()
    cc._SAME_PENDING.clear()

    # No live memory store in these tests: nothing is linked, the mirror refresh is a no-op.
    async def no_links(_uid, ids, timeout=1.5):
        return set()

    async def no_mirror(*_a, **_k):
        return None

    monkeypatch.setattr(cc, "linked_memory_ids", no_links)
    monkeypatch.setattr(cc, "refresh_person_mirror", no_mirror)


def P(pid, name, rel=None, **kw):
    """A people row. relationship=None is a NULL-relationship stub; is_partial rides along."""
    return {"id": pid, "name": name, "relationship": rel, "birthday": None,
            "phone": None, "email": None, "notes": None, "how_we_met": None,
            "deleted": 0, "is_partial": 0, **kw}


# ═════════════ Class 1: list-all, never a non-name search ═════════════════════


@pytest.mark.parametrize("text", [
    "Who is in my contacts", "Who is on my contacts", "who's in my contacts?",
    "who is on my contacts?", "list my contacts", "list all my contacts",
    "show me my contacts", "who do I have saved", "who do I have in my contacts",
    "read out my address book",
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


# Review finding 1: whole-value relationship match, any alias, never the name.
RELS = [P("1", "Jason Pell", "friend"), P("2", "Mason Reed", None), P("3", "Sonia Hale", "colleague"),
        P("4", "Wilson Ardent", "friend"), P("5", "Tobias Fenn", "grandson"), P("6", "Dunstan Fenn", "son"),
        P("7", "Odette Fenn", "mum"), P("8", "Marguerite Fenn", "grandmother"),
        P("9", "Ines Fenn", "Mother"), P("10", "Ronan Vale", "sister-in-law"), P("11", "Beck Vale", "sister")]


@pytest.mark.asyncio
async def test_who_is_my_son_matches_the_relationship_not_names_or_substrings(monkeypatch):
    _install(monkeypatch, _PeopleDB(RELS))
    out = await _execute_people_search_direct(Intent("people_search", {"query": "my son"}), USER)
    assert "Dunstan Fenn" in out
    for wrong in ("Jason", "Mason", "Sonia", "Wilson", "Tobias"):  # name / 'grandson' substring hits
        assert wrong not in out, wrong


@pytest.mark.asyncio
async def test_who_is_my_mum_finds_free_text_aliases_and_echoes_the_users_word(monkeypatch):
    _install(monkeypatch, _PeopleDB(RELS))
    out = await _execute_people_search_direct(Intent("people_search", {"query": "my mum"}), USER)
    assert "Odette Fenn" in out and "Ines Fenn" in out          # 'mum' and 'Mother' both mean it
    assert "Marguerite" not in out                               # 'grandmother' is not 'mother'
    nobody = await _execute_people_search_direct(Intent("people_search", {"query": "my dad"}), USER)
    assert nobody == 'No contacts found for "dad".'              # the user's word, not "father"


@pytest.mark.asyncio
async def test_relation_not_found_echoes_the_users_word_when_conversational(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    _install(monkeypatch, _PeopleDB(RELS))
    out = await _execute_people_search_direct(Intent("people_search", {"query": "my dad"}), USER)
    assert out == "I don't have your dad saved in your contacts."


@pytest.mark.asyncio
async def test_sister_does_not_pull_in_sister_in_law(monkeypatch):
    _install(monkeypatch, _PeopleDB(RELS))
    out = await _execute_people_search_direct(Intent("people_search", {"query": "my sister"}), USER)
    assert "Beck Vale" in out and "Ronan" not in out


def test_relation_aliases_are_whole_spellings():
    assert set(cc.relation_aliases("mother")) == {"mother", "mum", "mom", "mummy", "mommy"}
    assert "grandmother" not in cc.relation_aliases("mother") and "son" in cc.relation_aliases("son")
    assert "stepbrother" in cc.relation_aliases("step brother") and "brother" not in cc.relation_aliases("step brother")


# Review finding 6: a count question gets the count; no "(None)".
@pytest.mark.asyncio
async def test_how_many_contacts_answers_with_the_count_flag_off(monkeypatch):
    got = detect_intent("how many contacts do I have", log_miss=False)
    assert got.name == "people_search" and got.slots == {"query": "", "mode": "count"}
    _install(monkeypatch, _PeopleDB(RELS))
    out = await _execute_people_search_direct(got, USER)
    assert out == "You have 11 contacts."
    assert "Found:" not in out


@pytest.mark.asyncio
async def test_lookup_never_prints_none_for_a_missing_relationship(monkeypatch):
    _install(monkeypatch, _PeopleDB([P("2", "Mason Reed", None)]))
    out = await _execute_people_search_direct(Intent("people_search", {"query": "mason"}), USER)
    assert out == "Found:\n  - Mason Reed" and "None" not in out and "?" not in out


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


@pytest.mark.parametrize("slot, phrase, expect", [
    ("friend", "brother", "brother"), ("family", "brother", "brother"), ("", "brother", "brother"),
    ("colleague", "brother", "colleague"), (None, None, None), ("sister", None, "sister"),
])
def test_merge_relationship(slot, phrase, expect):
    assert cc.merge_relationship(slot, phrase) == expect


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
            P("3", "Caitlin Hale", "colleague")]          # a clashing relation: not a candidate
    out = cc.collapse_duplicates(rows)
    assert [r["name"] for r in out] == ["Caitlin Farrell", "Caitlin Hale"]
    assert out[0]["phone"] == "555" and out[0]["_merged_ids"] == ["1"]


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 0, 2), (1, 2, 0)])
def test_collapse_never_folds_a_stub_into_one_of_several_fuller_people(order):
    """Review finding 5: Dan next to Dan Smith AND Dan Jones could be either, so it
    folds into neither - and the answer does not depend on row order."""
    base = [P("1", "Dan", "friend", phone="555", notes="neighbour"),
            P("2", "Dan Smith", "friend"), P("3", "Dan Jones", "friend")]
    out = cc.collapse_duplicates([base[i] for i in order])
    assert sorted(r["name"] for r in out) == ["Dan", "Dan Jones", "Dan Smith"]
    assert not any(r.get("_merged_ids") for r in out)
    assert all(not r.get("phone") for r in out if r["name"] != "Dan")   # the stub's phone stays on the stub


def test_find_duplicate_groups_does_not_bridge_two_people_through_a_stub():
    rows = [P("1", "Dan", "friend", user_id="u1"), P("2", "Dan Smith", "friend", user_id="u1"),
            P("3", "Dan Jones", "friend", user_id="u1"),
            P("4", "Wren", "niece", user_id="u1"), P("5", "Wren Alder", "niece", user_id="u1")]
    groups = cc.find_duplicate_groups(rows)
    assert [sorted(r["id"] for r in g) for g in groups] == [["4", "5"]]


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
async def test_save_of_fuller_name_upgrades_a_provably_safe_stub_in_place(monkeypatch):
    """Specific equal relationship, no contact data, no linked memories: nothing can be lost."""
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Wren", "niece", is_partial=1)])
    _install(monkeypatch, db)
    mirrored = []

    async def mirror(uid, pid, name, rel):
        mirrored.append((pid, name, rel))

    monkeypatch.setattr(cc, "refresh_person_mirror", mirror)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "Wren Alder", "relationship": "niece"}), USER)
    assert "updated that contact to Wren Alder" in out
    assert db.inserts() == [] and [p["name"] for p in db.people] == ["Wren Alder"]
    assert db.people[0]["is_partial"] == 0                      # now a full contact
    assert mirrored == [("1", "Wren Alder", "niece")]           # the memory mirror follows the rename


# Review finding 4: a stub that may be a DIFFERENT person is asked about, never renamed.
@pytest.mark.asyncio
@pytest.mark.parametrize("stub", [
    P("1", "Dan", None, is_partial=1),                      # extractor stub: NULL relationship
    P("1", "Dan", "friend"),                                # the brain tool's default
    P("1", "Dan", "friend", phone="0400 000 000"),
    P("1", "Dan", "friend", notes="lives next door"),
])
async def test_generic_or_data_bearing_stub_is_asked_not_renamed(monkeypatch, stub):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([stub])
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "Dan Murphy", "relationship": "friend"}), USER)
    assert out == "I already have a Dan saved. Is Dan Murphy the same person?"
    assert db.inserts() == [] and db.people[0]["name"] == "Dan"           # nothing written
    assert not [c for c in db.calls if c[0].lstrip().startswith("UPDATE")]
    assert cc.peek_same_person(USER)["name"] == "Dan Murphy"             # queued for the answer


@pytest.mark.asyncio
async def test_stub_with_linked_memories_is_asked_not_renamed(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Wren", "niece")])

    async def linked(uid, ids, timeout=1.5):
        return set(ids)

    monkeypatch.setattr(cc, "linked_memory_ids", linked)
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "Wren Alder", "relationship": "niece"}), USER)
    assert out.endswith("Is Wren Alder the same person?") and db.people[0]["name"] == "Wren"


async def _prev(monkeypatch, text):
    async def prev(_uid):
        return text

    monkeypatch.setattr("intent_router._previous_assistant_message", prev)


@pytest.mark.asyncio
async def test_same_person_yes_renames_and_no_adds_separately(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    for reply, renamed in (("yes, same person", True), ("no, a different person", False)):
        db = _PeopleDB([P("1", "Dan", "friend", phone="555")])
        _install(monkeypatch, db)
        q = await _execute_people_create_direct(
            Intent("people_create", {"name": "Dan Murphy", "relationship": "friend"}), USER)
        await _prev(monkeypatch, f"Sure thing. {q}")
        got = await intent_router._match_same_person_reply(reply, USER)
        assert got is not None and got.name == "people_same_person_reply"
        await execute_intent(got, USER)
        names = sorted(p["name"] for p in db.people)
        assert names == (["Dan Murphy"] if renamed else ["Dan", "Dan Murphy"]), reply
        assert db.people[0]["phone"] == "555"                 # the stub's data is untouched either way
        assert cc.peek_same_person(USER) is None


@pytest.mark.asyncio
async def test_same_person_reply_binds_only_to_the_question_just_asked(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    _install(monkeypatch, _PeopleDB([P("1", "Dan", "friend")]))
    q = await _execute_people_create_direct(
        Intent("people_create", {"name": "Dan Murphy", "relationship": "friend"}), USER)
    await _prev(monkeypatch, "Anything else I can help with?")           # the question was not asked
    assert await intent_router._match_same_person_reply("yes", USER) is None
    await _prev(monkeypatch, f"{q} Also, want me to read your list?")    # a later question wins
    assert await intent_router._match_same_person_reply("yes", USER) is None
    await _prev(monkeypatch, None)
    assert await intent_router._match_same_person_reply("yes", USER) is None
    await _prev(monkeypatch, q)
    assert await intent_router._match_same_person_reply("what's the weather", USER) is None


@pytest.mark.asyncio
async def test_a_stub_next_to_two_fuller_people_is_a_new_person_not_a_guess(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    db = _PeopleDB([P("1", "Dan", "friend"), P("2", "Dan Smith", "friend")])
    _install(monkeypatch, db)
    out = await _execute_people_create_direct(
        Intent("people_create", {"name": "Dan Jones", "relationship": "friend"}), USER)
    assert out == "Added Dan Jones, your friend." and len(db.inserts()) == 1


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


_UNSET = object()


def _offers(monkeypatch, offers, previous=_UNSET, record=True):
    """Surfaced offers + (batch mode) the recorded ASKED set and the previous
    assistant message. `previous` defaults to a message ending with the question."""
    import pending_suggestions as ps

    async def surfaced(_uid, *, limit=5):
        return [dict(o) for o in offers[:limit]]

    monkeypatch.setattr(ps, "surfaced_person_offers", surfaced)
    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    question = cc.offer_question(offers)
    if record:
        cc.record_asked(USER, question, offers)

    async def prev(_uid):
        return f"Got it. {question}" if previous is _UNSET else previous

    monkeypatch.setattr("intent_router._previous_assistant_message", prev)


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
    asked = cc.get_asked(USER)                     # the set a following yes/no may bind to
    assert asked["ids"] == ["o1", "o2", "o3"] and asked["question"] in block


# Review finding 2: a yes/no binds ONLY to the question Zoe just asked.
@pytest.mark.asyncio
@pytest.mark.parametrize("previous", [
    "Anything else I can help with?",                                         # the offer was never voiced
    None,                                                                    # no history: unknown
    "Would you like me to add Rodrigo (your brother) to your contacts?",     # a different set
    ("Would you like me to add Rodrigo (your brother), Jessika (your sister) and Wren to your "
     "contacts? Also, want milk on the shopping list?"),                     # another question comes LAST
])
@pytest.mark.parametrize("reply", ["yes", "no", "everyone", "both", "sure, all of them"])
async def test_a_reply_meant_for_another_question_does_not_bind_the_offers(monkeypatch, previous, reply):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY, previous=previous)
    assert await _match_pending_offer_reply(reply, USER) is None


@pytest.mark.asyncio
async def test_nothing_binds_without_a_recorded_asked_set(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY, record=False)               # surfaced, but never asked
    assert await _match_pending_offer_reply("yes", USER) is None


@pytest.mark.asyncio
async def test_the_question_may_follow_other_chat_when_it_is_the_last_question(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    q = cc.offer_question(FAMILY)
    _offers(monkeypatch, FAMILY, previous=f"Added milk to your list. {q}")
    got = await _match_pending_offer_reply("yes", USER)
    assert got is not None and got.slots["suggestion_ids"] == ["o1", "o2", "o3"]


@pytest.mark.asyncio
async def test_an_answered_question_cannot_bind_twice(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY)
    assert await _match_pending_offer_reply("yes", USER) is not None
    assert await _match_pending_offer_reply("yes", USER) is None


# Review finding 3: "yes, not X" is accept-all-but-X, never dismiss-all.
@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["yes, not Jessika", "ok don't add Jessika", "yes but not Jessika"])
async def test_a_yes_with_a_named_exception_accepts_the_rest(monkeypatch, reply):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY)
    got = await _match_pending_offer_reply(reply, USER)
    assert got is not None and got.name == "pending_offer_accept", reply
    assert got.slots["suggestion_ids"] == ["o1", "o3"] and got.slots["dismiss_ids"] == ["o2"]


@pytest.mark.asyncio
async def test_an_unnamed_negation_still_dismisses_and_a_named_no_goes_to_the_brain(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
    _offers(monkeypatch, FAMILY)
    got = await _match_pending_offer_reply("ok don't", USER)
    assert got.name == "pending_offer_dismiss" and got.slots["suggestion_ids"] == ["o1", "o2", "o3"]
    _offers(monkeypatch, FAMILY)
    assert await _match_pending_offer_reply("no Jessika", USER) is None


# The asked-set == accepted-set invariant, against the REAL pending_suggestions rows.
class _SqliteConn:
    def __init__(self, db):
        self._db = db

    @staticmethod
    def _tr(sql):
        return re.sub(r"\$(\d+)", "?", sql)

    async def execute(self, sql, *args):
        await self._db.execute(self._tr(sql), args)
        await self._db.commit()

    async def fetch(self, sql, *args):
        async with self._db.execute(self._tr(sql), args) as c:
            rows = await c.fetchall()
        await self._db.commit()
        return rows


@pytest.mark.asyncio
async def test_accepted_set_is_exactly_the_asked_set_over_real_rows(monkeypatch):
    from contextlib import asynccontextmanager

    import aiosqlite

    import pending_suggestions as ps

    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
        await db.execute(
            """CREATE TABLE pending_suggestions (
                id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, action_type TEXT,
                description TEXT, list_type TEXT, when_hint TEXT, amount_hint TEXT,
                offer_phrase TEXT, pre_filled_slots TEXT, created_at TEXT,
                turns_elapsed INTEGER DEFAULT 0, expire_after_turns INTEGER DEFAULT 2,
                resolved INTEGER DEFAULT 0)""")
        await db.commit()

        @asynccontextmanager
        async def ctx():
            yield _SqliteConn(db)

        monkeypatch.setattr(ps, "get_db_ctx", ctx)
        monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
        monkeypatch.setenv("ZOE_CONTACT_OFFER_BATCH", "1")
        names = ["Alder", "Birch", "Cedar", "Dunn", "Elm", "Fenn", "Gorse"]
        for i in range(0, len(names), 3):
            await ps.store_suggestions(USER, "s1", [
                {"action_type": "person_create", "description": f"add {n}", "offer_phrase": f"Add {n}?",
                 "pre_filled_slots": {"name": n, "relationship": "cousin"}} for n in names[i:i + 3]])

        # The prompt builder surfaces (and asks about) at most OFFER_BATCH_MAX offers.
        asked_offers = await ps.surface_pending_contacts_for_prompt(USER, limit=cc.OFFER_BATCH_MAX)
        assert len(asked_offers) == 5
        question = cc.offer_question(asked_offers)
        cc.record_asked(USER, question, asked_offers)
        # Another builder later surfaces the remaining two (not part of the question).
        assert len(await ps.surface_pending_contacts_for_prompt(USER, limit=7)) == 7

        async def prev(_uid):
            return question

        monkeypatch.setattr("intent_router._previous_assistant_message", prev)
        got = await _match_pending_offer_reply("yes please", USER)   # REAL surfaced_person_offers
        assert got is not None and got.name == "pending_offer_accept"
        assert sorted(got.slots["suggestion_ids"]) == sorted(o["id"] for o in asked_offers)
        assert len(got.slots["suggestion_ids"]) == 5            # never the two that were not asked

        executed = []

        async def fake_execute(sid, uid):
            executed.append(sid)
            return {"ok": True, "result": {"created": True}}

        monkeypatch.setattr(ps, "execute_suggestion", fake_execute)
        out = await execute_intent(got, USER)
        assert sorted(executed) == sorted(o["id"] for o in asked_offers)
        assert out.startswith("Done — I've added") and "Fenn" not in out and "Gorse" not in out
    finally:
        await db.close()


# Review finding 7: the offer-accept path runs the same merge/ask logic as a typed save.
class _Conn:
    def __init__(self, people=()):
        self.people, self.writes = [dict(p) for p in people], []

    async def fetchrow(self, sql, *args):
        return None                                  # no exact-name row

    async def fetch(self, sql, *args):
        pre = args[1].rstrip("%").lower()
        return [dict(p) for p in self.people if p["name"].lower().startswith(pre)]

    async def execute(self, sql, *args):
        self.writes.append((" ".join(sql.split())[:30], args))


async def _accept(monkeypatch, conn, slots, flag=True):
    import pending_suggestions as ps

    monkeypatch.setenv("ZOE_PERSON_SUGGEST_ENABLED", "1")
    if flag:
        monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")

    async def execute_suggestion(sid, uid):
        return {"ok": True, "action": "person_create",
                "result": await ps._execute_action(conn, "person_create", slots, uid)}

    monkeypatch.setattr(ps, "execute_suggestion", execute_suggestion)
    return await execute_intent(
        Intent("pending_offer_accept", {"suggestion_id": "x", "name": slots["name"]}), USER)


@pytest.mark.asyncio
async def test_accepting_a_first_name_offer_next_to_a_fuller_contact_adds_nothing(monkeypatch):
    conn = _Conn([P("2", "Caitlin Farrell", "friend")])
    out = await _accept(monkeypatch, conn, {"name": "Caitlin", "relationship": "friend"})
    assert "already have Caitlin Farrell" in out and "added" not in out.lower()
    assert not [w for w in conn.writes if w[0].startswith("INSERT")]


@pytest.mark.asyncio
async def test_accepting_an_offer_for_a_name_that_may_be_a_stub_asks(monkeypatch):
    conn = _Conn([P("1", "Dan", None, is_partial=1, phone="555")])
    out = await _accept(monkeypatch, conn, {"name": "Dan Murphy", "relationship": "friend"})
    assert out == "I already have a Dan saved. Is Dan Murphy the same person?"
    assert not conn.writes and cc.peek_same_person(USER)["name"] == "Dan Murphy"


@pytest.mark.asyncio
async def test_accepting_an_offer_renames_only_a_provably_safe_stub(monkeypatch):
    conn = _Conn([P("1", "Wren", "niece", is_partial=1)])
    out = await _accept(monkeypatch, conn, {"name": "Wren Alder", "relationship": "niece"})
    assert "updated that contact to Wren Alder" in out
    assert [w[0] for w in conn.writes] == ["UPDATE people SET name=$1, rel"]


@pytest.mark.asyncio
async def test_accept_negative_controls_flag_off_and_new_person(monkeypatch):
    conn = _Conn([P("2", "Caitlin Farrell", "friend")])
    out = await _accept(monkeypatch, conn, {"name": "Caitlin", "relationship": "friend"}, flag=False)
    assert out == "Done — I've added Caitlin to your contacts."          # legacy: exact-name dedupe only
    assert [w[0] for w in conn.writes if w[0].startswith("INSERT")]
    monkeypatch.setenv("ZOE_CONTACTS_CONVERSATIONAL", "1")
    conn = _Conn([P("2", "Caitlin Farrell", "friend")])
    out = await _accept(monkeypatch, conn, {"name": "Delia Mott", "relationship": "friend"})
    assert out == "Done — I've added Delia Mott to your contacts." and conn.writes


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
