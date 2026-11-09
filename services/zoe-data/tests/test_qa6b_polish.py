"""QA round 6b polish (2026-11-09): the speaker's own name is never a contact to add, a named place beats the household default
(even with a clause after it, and for two places), corrections apply (a day, an age with its half, a value stated "for <name>"),
a correction turn invents no field, a simple question is answered rather than briefed, and the forget confirmation does not hand
the erased text back to the brain. Synthetic names and places only. Slim: in-memory SQLite behind an asyncpg-shaped shim."""
import contextlib
import json
import os
import re
import sys
import types

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import expert_dispatch  # noqa: E402
import flue_fastpath_context as ffc  # noqa: E402
import own_name  # noqa: E402
import pending_suggestions as ps  # noqa: E402
import research_evidence  # noqa: E402
import value_correction as vc  # noqa: E402
from intent_router import detect_intent  # noqa: E402
from weather_speech import weather_place, weather_places  # noqa: E402

pytestmark = pytest.mark.ci_safe

USER = "demo_bar_0a1b2c3d"


# ── A. the speaker's own name ─────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,name", [
    ("its walter. what tablets do i take again", "Walter"),
    ("hey zoe, nadia here. mum of two, running on caffeine", "Nadia"),
    ("hello zoe walter here. what did i tell you about my chest", "Walter"),
    ("hello zoe. my name is walter, im 78", "Walter"),
    ("hi zoe, anika. what happened with my team", "Anika"),
    ("hi zoe, its anika, back again", "Anika"),
])
def test_a_self_introduction_names_the_speaker(text, name):
    assert own_name.introduced_name(text) == name


@pytest.mark.parametrize("text", [
    "its cold today", "im tired", "this is great", "hi zoe, thanks.", "hey zoe, whats the time", "its here",
    "my neighbour darren is lending us a ladder", "im going to the shops",
    # a PRESENTATION of somebody else is not the speaker's own name (cross-review: Margaret/Darren would have been hidden for good)
    "this is Margaret, my wife", "it's Margaret, our neighbour", "this is Darren and he is lending us a ladder",
    "its darren who lives next door", "this is Priya, a friend from work",
])
def test_negative_control_not_every_its_is_a_name(text):
    assert own_name.introduced_name(text) == ""


@pytest.mark.parametrize("uid,own", [
    ("demo_bar_0a1b2c3d", set()),                     # a generated id is nobody's name: a contact called Bar stays offerable
    ("jason", {"jason"}), ("jason_2", {"jason"}), ("jason.smith", {"jason", "smith"}),
])
@pytest.mark.asyncio
async def test_account_id_tokens_only_for_handle_accounts(monkeypatch, uid, own):
    _wire_identity(monkeypatch, [])
    got = await own_name.names(uid)
    assert {t for t in got if " " not in t} == own


@pytest.mark.asyncio
async def test_note_introduction_persists_and_names_reads_it_back(monkeypatch):
    """detect_and_store -> note_introduction -> user_preferences -> names(): the persistence wiring, not an injected list."""
    store = {}
    prefs = types.ModuleType("user_prefs")

    async def get_pref(user_id, key, default=None, *, db=None):
        return store.get((user_id, key), default)

    async def set_pref(user_id, key, value, *, db=None):
        store[(user_id, key)] = value

    async def is_memory_opted_out(user_id, **_k):
        return False

    prefs.get_pref, prefs.set_pref, prefs.is_memory_opted_out = get_pref, set_pref, is_memory_opted_out
    monkeypatch.setitem(sys.modules, "user_prefs", prefs)
    ident = types.ModuleType("identity_facts")

    async def resolve_identity(*a, **k):
        return None

    ident.resolve_identity = resolve_identity
    monkeypatch.setitem(sys.modules, "identity_facts", ident)
    assert await own_name.note_introduction(USER, "its walter. what tablets do i take again") == "Walter"
    assert await own_name.note_introduction(USER, "this is Margaret, my wife") == ""          # a third person is not noted
    assert await own_name.note_introduction(USER, "its walter again") == "Walter"            # idempotent
    assert store[(USER, own_name.PREF_KEY)] == ["Walter"]
    got = await own_name.names(USER)
    assert own_name.is_own("Walter", got) and not own_name.is_own("Margaret", got) and not own_name.is_own("Bar", got)


def _wire_identity(monkeypatch, introduced):
    prefs = types.ModuleType("user_prefs")

    async def get_pref(user_id, key, default=None, *, db=None):
        return list(introduced) if key == own_name.PREF_KEY else default

    prefs.get_pref = get_pref
    monkeypatch.setitem(sys.modules, "user_prefs", prefs)
    ident = types.ModuleType("identity_facts")

    async def resolve_identity(*a, **k):
        return None

    ident.resolve_identity = resolve_identity
    monkeypatch.setitem(sys.modules, "identity_facts", ident)


def test_own_matching_is_a_name_not_a_family():
    own = frozenset({"walter", "walter smith"})
    assert own_name.is_own("Walter", own)
    assert own_name.is_own("walter smith", own)
    assert not own_name.is_own("Walter Jr", own)           # somebody else who shares a first name
    assert not own_name.is_own("Margaret", own)
    assert not own_name.is_own("", own)


class _Shim:
    def __init__(self, conn):
        self._c = conn

    @staticmethod
    def _q(sql):
        return re.sub(r"\$\d+", "?", sql)

    async def execute(self, sql, *params):
        await self._c.execute(self._q(sql), params)
        await self._c.commit()

    async def fetch(self, sql, *params):
        async with self._c.execute(self._q(sql), params) as cur:
            return list(await cur.fetchall())

    async def fetchrow(self, sql, *params):
        async with self._c.execute(self._q(sql), params) as cur:
            return await cur.fetchone()


async def _open():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    await db.execute("CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, deleted INTEGER DEFAULT 0)")
    await db.execute(
        "CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT, action_type TEXT,"
        " description TEXT, list_type TEXT, when_hint TEXT, amount_hint TEXT, offer_phrase TEXT, pre_filled_slots TEXT,"
        " created_at TEXT, turns_elapsed INTEGER, expire_after_turns INTEGER, resolved INTEGER)")
    await db.commit()
    return db


@contextlib.asynccontextmanager
async def _ctx_of(db):
    yield _Shim(db)


def _wire(monkeypatch, db, introduced):
    ctx = lambda: _ctx_of(db)  # noqa: E731
    monkeypatch.setattr(ps, "get_db_ctx", ctx)
    fake = types.ModuleType("db_pool")
    fake.get_db_ctx = ctx
    monkeypatch.setitem(sys.modules, "db_pool", fake)
    prefs = types.ModuleType("user_prefs")

    async def get_pref(user_id, key, default=None, *, db=None):
        return list(introduced) if key == own_name.PREF_KEY else default

    prefs.get_pref = get_pref
    monkeypatch.setitem(sys.modules, "user_prefs", prefs)
    ident = types.ModuleType("identity_facts")

    async def resolve_identity(user_id, db=None, *, budget_s=None):
        return None

    ident.resolve_identity = resolve_identity
    monkeypatch.setitem(sys.modules, "identity_facts", ident)


def _offer(name, rel=""):
    return {"action_type": "person_create", "description": f"Add {name} to contacts", "offer_phrase": f"Add {name}?",
            "pre_filled_slots": {"name": name, "relationship": rel} if rel else {"name": name}}


@pytest.mark.asyncio
async def test_the_store_refuses_an_offer_to_add_the_speaker_themself(monkeypatch):
    db = await _open()
    try:
        _wire(monkeypatch, db, ["Walter"])
        assert await ps.store_suggestions(USER, "s", [_offer("Walter", "customer")]) == 0
        assert await ps.store_suggestions(USER, "s", [_offer("Margaret", "wife")]) == 1      # somebody else is still offered
        assert [p["name"] for p in await ps.list_pending_contacts(USER)] == ["Margaret"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_negative_control_without_the_introduction_the_offer_is_stored(monkeypatch):
    db = await _open()
    try:
        _wire(monkeypatch, db, [])                       # nobody ever introduced themself: nothing says Walter is the speaker
        assert await ps.store_suggestions(USER, "s", [_offer("Walter", "customer")]) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_offer_already_in_the_queue_for_the_speaker_closes_when_read(monkeypatch):
    """The rows that were sitting in the three demo queues ("add Walter (your customer)") clear on the next read, no "no" needed."""
    db = await _open()
    try:
        _wire(monkeypatch, db, ["Walter"])
        for n in ("Walter", "Margaret"):
            await db.execute(
                "INSERT INTO pending_suggestions VALUES (?, ?, 's', 'person_create', 'd', NULL, NULL, NULL, 'o', ?, 'now', 0, 6, 0)",
                (f"id-{n}", USER, json.dumps({"name": n})))
        await db.commit()
        out = await ps.surface_pending_contacts_for_prompt(USER, limit=3)
        assert [o["name"] for o in out] == ["Margaret"]
        async with db.execute("SELECT resolved FROM pending_suggestions WHERE id='id-Walter'") as c:
            assert (await c.fetchone())[0] == 1            # closed, not merely hidden: it never comes back
        async with db.execute("SELECT resolved FROM pending_suggestions WHERE id='id-Margaret'") as c:
            assert (await c.fetchone())[0] == 0
    finally:
        await db.close()


# ── B. weather: the named place ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,places", [
    ("hows the weather looking in bunbury today, were driving down saturday", ["Bunbury"]),
    ("whats the weather like in albany this weekend, jonas and i are going", ["Albany"]),
    ("weather in fremantle today? and mandurah", ["Fremantle", "Mandurah"]),
    ("whats the weather in kalgoorlie, and in fremantle", ["Kalgoorlie", "Fremantle"]),
    ("whats the weather in perth and what about bunbury", ["Perth", "Bunbury"]),
    ("what's the forecast for broome tomorrow", ["Broome"]),
    ("weather in port hedland and my dog is hungry", ["Port Hedland"]),
])
def test_the_named_places(text, places):
    assert weather_places(text) == places
    assert weather_place(text) == places[0]


@pytest.mark.parametrize("text", [
    "what's the weather", "whats the weather like", "weather today", "is it cold in here", "whats the temp in the living room",
    "weather in the oven", "hows the weather looking today, were driving down saturday", "oven temp for roast potatoes in bunbury",
])
def test_negative_control_no_named_place_is_the_household_default(text):
    assert weather_places(text) == []


def test_every_path_carries_all_the_places():
    plan = expert_dispatch._plan("weather", "hows the weather looking in bunbury today, were driving down saturday")
    assert plan[1]["location"] == "Bunbury"
    plan = expert_dispatch._plan("weather", "weather in fremantle today? and mandurah")
    assert plan[1]["location"] == "Fremantle" and plan[1]["also_locations"] == ["Mandurah"]
    assert "location" not in expert_dispatch._plan("weather", "whats the weather like")[1]
    slots = detect_intent("whats the weather in perth and what about bunbury", log_miss=False).slots
    assert slots["location"] == "Perth" and slots["also_locations"] == ["Bunbury"]


@pytest.mark.asyncio
async def test_a_two_place_question_answers_both(monkeypatch):
    import intent_router as ir

    asked = []

    async def fake(user_id, forecast=False, advice="", location="", focus="", day=""):
        asked.append(location)
        return f"It's 20 degrees in {location}."

    monkeypatch.setattr(ir, "_execute_weather_direct", fake)
    out = await ir.execute_intent(ir.Intent("weather", {"location": "Fremantle", "also_locations": ["Mandurah"]}), "demo_bar_x")
    assert asked == ["Fremantle", "Mandurah"]
    assert "Fremantle" in out and "Mandurah" in out


# ── C. corrections ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_day_correction_about_a_thing_applies():
    c = vc.statement("actually the pitch is wednesday not thursday")
    assert c is not None and (c.new, c.old) == ("wednesday", "thursday")
    assert vc.rewrite("User has a board pitch on Thursday", c) == "User has a board pitch on Wednesday"
    assert vc.rewrite("User's dog is called Otis", c) is None                        # a row that is not about the pitch is untouched
    assert vc.rewrite("User has a board pitch on Friday", c) is None                 # nor one that does not hold the old day


def test_an_age_with_its_half_applies():
    c = vc.statement("actually wren is 3 and a half, not 3. she gets cross if you forget the half")
    assert c is not None and c.subject == "wren"
    assert vc.rewrite("User's daughter Wren is three.", c) == "User's daughter Wren is three and a half."
    assert vc.rewrite("Wren is 3 years old", c) == "Wren is 3 and a half years old"
    assert vc.rewrite("User's daughter Felix is three.", c) is None                  # another child's age is not touched
    assert vc.rewrite("User's daughter Wren is three and a half.", c) is None        # already corrected: nothing to rewrite


def test_a_value_stated_for_a_name_applies():
    c = vc.statement("actually its 76 for margaret, not 78. i am 78")
    assert c is not None and (c.subject, c.new, c.old) == ("margaret", "76", "78")
    assert vc.rewrite("User's wife Margaret is 78 and has high blood pressure", c) == \
        "User's wife Margaret is 76 and has high blood pressure"
    assert vc.rewrite("Walter is 78", c) is None                                     # the speaker's own 78 stays


@pytest.mark.parametrize("text", [
    "the weather is nice, not bad", "tea is hot not cold", "its tall for a dog, not short", "my plan is a sketch, not a map",
])
def test_negative_control_a_description_is_not_a_correction(text):
    assert vc.statement(text) is None


def test_a_correction_turn_invents_no_field():
    m = "actually its 76 for margaret, not 78. i am 78"
    assert vc.invented_on_correction_turn("User's blood pressure is 78", m) is True       # the live fault
    assert vc.invented_on_correction_turn("User is 78 years old", m) is False
    assert vc.invented_on_correction_turn("Margaret is 76", m) is False
    # a fact that names the corrected subject is about the correction, however much context it carries (cross-review)
    assert vc.invented_on_correction_turn("User's wife Margaret is 76", m) is False
    assert vc.invented_on_correction_turn("User has a board pitch on Wednesday", "actually the pitch is wednesday not thursday") is False
    assert vc.invented_on_correction_turn("User's blood pressure is 76", "actually the pitch is wednesday not 76") is True
    # not a correction turn: nothing is dropped, however odd the fact
    assert vc.invented_on_correction_turn("User's blood pressure is 78", "my blood pressure is high, 78 they said") is False


# ── D. a simple question is answered, not briefed ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "whats the best oven temp for roast potatoes", "where can i get a decent pie in northam on the way",
    "whats the best way to cook rice", "what's the best time to plant tomatoes",
])
def test_a_simple_question_is_not_a_research_brief(text):
    assert research_evidence.classify_query(text) == "general"


@pytest.mark.parametrize("text", [
    "find me the cheapest flight to bali", "where can i buy a cheap kettle", "what's the cheapest laptop",
    "search for hotels in perth", "can you compare the prices of these two",
])
def test_negative_control_a_shopping_hunt_still_gets_the_brief(text):
    assert research_evidence.classify_query(text) == "research"


# ── E. same-session forget ───────────────────────────────────────────────────────────────────────────────────────────

def test_the_forget_confirmation_does_not_carry_the_erased_text_to_the_brain():
    quoted = 'Done — I forgot: "User\'s pet Wren had a meltdown at the shops and screamed for twenty minutes".'
    assert ffc.redact_forget_quote(quoted) == "Done — I forgot that."
    assert ffc.redact_forget_quote('Done - I forgot: "x y z" (and 2 more).') == "Done - I forgot that (and 2 more)."
    block = ffc.render([("user", "forget what i said about the shops meltdown"), ("assistant", quoted)])
    assert "meltdown at the shops" not in block.split("User:")[1].split("Zoe:")[1]
    assert "Zoe: Done — I forgot that." in block
    assert "screamed" not in block


def test_negative_control_other_replies_are_carried_unchanged():
    assert ffc.redact_forget_quote("Reminder set for 5pm.") == "Reminder set for 5pm."
    block = ffc.render([("user", "remind me to call dad"), ("assistant", 'Done — reminder: "call dad" at 5pm.')])
    assert 'reminder: "call dad"' in block


# -- review round (Codex on #2355) ---------------------------------------------------------------------------

def test_a_possessive_correction_never_rewrites_another_persons_attribute():
    import value_correction as vc

    corr = vc.statement("Actually my birthday is June not July")
    assert vc.rewrite("Oliver's birthday is in July", corr) is None            # a contact's birthday is not the speaker's
    assert vc.rewrite("User's birthday is in July", corr) == "User's birthday is in June"      # negative control


def test_a_quoted_introduction_is_not_the_speakers_name():
    import own_name

    assert own_name.introduced_name("Dana says: my name is Priya.") == ""
    assert own_name.introduced_name("my name is Priya") == "Priya"               # negative control: their own words still count


@pytest.mark.parametrize("text,place", [("weather in St. Louis", "St Louis"), ("what is the weather in Mt. Barker today", "Mt Barker"),
                                        ("weather in Perth. and tomorrow", "Perth")])
def test_an_abbreviation_period_stays_in_the_place_name(text, place):
    import weather_speech as ws

    assert ws.weather_place(text) == place


def test_the_forget_redaction_covers_a_row_that_holds_quotes():
    import flue_fastpath_context as ffc

    out = ffc.redact_forget_quote('Done — I forgot: "User called the incident a "meltdown" at the shops".')
    assert "meltdown" not in out and out.startswith("Done — I forgot that")


def test_a_town_after_a_temporal_preposition_is_still_found():
    import weather_speech as ws

    assert ws.weather_place("weather forecast for the weekend in Perth") == "Perth"
    assert ws.weather_place("weather for this weekend in Albany") == "Albany"
    assert ws.weather_place("weather in Perth") == "Perth"                     # negative control
    assert ws.weather_place("weather for the weekend") == ""                   # no town at all: the household default


def test_the_digest_log_line_for_a_dropped_fact_carries_a_tag_not_the_words():
    import pathlib

    import memory_digest

    src = pathlib.Path(memory_digest.__file__).read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if "a fact a correction turn cannot have said" in ln)
    assert "utterance_tag(fact)" in line and "fact[:50]" not in line


def test_a_member_erase_clears_the_introduced_names(monkeypatch):
    import asyncio

    import own_name
    import user_prefs

    store = {"names": ["Priya"]}

    async def delete_pref(uid, key, **_k):
        store.pop("names", None)

    monkeypatch.setattr(user_prefs, "delete_pref", delete_pref)
    asyncio.run(own_name.forget_user("member-a"))
    assert "names" not in store


def test_a_forgotten_introduced_name_stops_counting(monkeypatch):
    import asyncio

    import memory_forgotten
    import own_name
    import user_prefs

    async def get_pref(uid, key, default=None, **_k):
        return ["Priyanka", "Walter"] if key == own_name.PREF_KEY else default

    async def matches(uid, text, **_k):
        return "priyanka" in text.lower()

    monkeypatch.setattr(user_prefs, "get_pref", get_pref)
    monkeypatch.setattr(memory_forgotten, "matches", matches)
    got = asyncio.run(own_name.names("member-a"))
    assert "walter" in got and "priyanka" not in got


def test_an_opted_out_member_s_introduction_is_not_stored(monkeypatch):
    # Codex on #2355 (P1): note_introduction persisted the spoken name whatever the member's memory opt-out said
    import asyncio

    import own_name
    import user_prefs

    saved = {}

    async def get_pref(uid, key, default=None, **_k):
        return default

    async def set_pref(uid, key, value, **_k):
        saved[key] = value

    async def opted(flag):
        return flag

    monkeypatch.setattr(user_prefs, "get_pref", get_pref)
    monkeypatch.setattr(user_prefs, "set_pref", set_pref)
    monkeypatch.setattr(user_prefs, "is_memory_opted_out", lambda uid, **_k: opted(True))
    assert asyncio.run(own_name.note_introduction("member-a", "my name is Priya")) == "" and not saved
    # negative control: not opted out, the same words are stored
    monkeypatch.setattr(user_prefs, "is_memory_opted_out", lambda uid, **_k: opted(False))
    assert asyncio.run(own_name.note_introduction("member-a", "my name is Priya")) == "Priya" and saved


@pytest.mark.parametrize("name", ["Emily", "Sally", "Molly"])
def test_an_explicit_my_name_is_accepts_names_ending_in_ly(name):
    # Codex on #2355: the adverb filter rejected real names even after "my name is"
    import own_name

    assert own_name.introduced_name(f"my name is {name}") == name
    # negative control: the loose shape still refuses an adverb
    assert own_name.introduced_name("im really") == ""
