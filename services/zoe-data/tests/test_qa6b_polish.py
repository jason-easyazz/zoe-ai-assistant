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
])
def test_negative_control_not_every_its_is_a_name(text):
    assert own_name.introduced_name(text) == ""


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
