"""Identity facts come from the ACCOUNT, never from memory.

Live 2026-10-05: "whats my name" was answered with a full name that belongs to nobody in
the household. A nightly-digest pass had written ``User's name is <X>`` from a speech-to-text
fragment naming a third person, and its contradiction pass superseded the genuine name fact
with it. The class: a recall store is the wrong authority for "who am I". These tests pin

  * the question shapes (and the near-misses that must NOT match),
  * the account -> Identity rules (override > account, region, harness ids have none),
  * the answer is built from the account and a conflicting memory row cannot change it
    (negative control) but IS logged as ``IDENTITY_CONFLICT`` with no text,
  * the tier runs for chat/telegram only (voice keeps its scope gate),
  * the writer wall: AUTOMATIC sources can neither ingest nor supersede into a name
    assertion; explicit teach and operator actors can — and the digest replay of the live
    incident (synthetic names) leaves the genuine row approved. Each wall has a
    break-the-fix control.

Synthetic names/places only. Fake DB, fake Chroma collection: no network, no model (ci_safe).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import types

import pytest

import identity_facts as idf
import memory_service
from memory_service import MemoryRef, MemoryService

pytestmark = pytest.mark.ci_safe

UID = "member-a"
REAL = "Zed"
WRONG = "Mika Vale"  # the polluted name in these tests (synthetic)
SYSLOC = {"city": "Hobart", "country": "AU", "timezone": "Australia/Hobart"}


# ── fake DB (rows keyed by SQL fragment) ──────────────────────────────────────

class _Cur:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class FakeDB:
    """Answers the ONE joined identity query (``_IDENTITY_SQL``) from plain fields."""

    def __init__(self, *, auth=True, users_name=REAL.lower(), prefs=None, wp=None, sysloc=SYSLOC):
        self.auth, self.users_name, self.prefs, self.wp, self.sysloc = auth, users_name, prefs, wp, sysloc
        self.queries: list[str] = []

    async def execute(self, sql, params=()):
        self.queries.append(sql)
        assert "FROM auth_users" in sql, sql
        if not self.auth:
            return _Cur(None)
        wp = self.wp or {}
        return _Cur({
            "username": REAL.lower(), "settings": "{}", "users_name": self.users_name,
            "prefs": json.dumps(self.prefs) if self.prefs is not None else None,
            "wp_city": wp.get("city"), "wp_country": wp.get("country"), "wp_current": wp.get("current"),
            "sysloc": json.dumps(self.sysloc) if self.sysloc is not None else None,
        })


@pytest.fixture(autouse=True)
def _fresh_cache():
    idf.clear_cache()
    yield
    idf.clear_cache()


def _ident(**kw):
    return idf.build_identity(UID, username=REAL.lower(), users_name=REAL.lower(), sysloc=SYSLOC, **kw)


# ── question shapes ───────────────────────────────────────────────────────────

POS = {
    "name": ["whats my name", "What's my name?", "what is my name", "do you know my name",
             "tell me my name", "zoe, what's my name again", "what is my first name"],
    "fullname": ["what's my full name", "what is my full name"],
    "surname": ["whats my last name", "what is my surname"],
    "call": ["what do you call me", "what am I called", "who do you call me"],
    "self": ["who am I", "Who am I?", "hey zoe, who am i"],
    "home": ["where do I live", "where do we live", "where's my home"],
    "home_city": ["which city do I live in", "what town do we live in"],
    "home_region": ["what state do we live in", "which region do I live in"],
    "home_country": ["which country do I live in", "what country do we live in"],
    "address": ["what's my address", "what is my home address", "whats our address"],
}
NEG = ["who am I meeting tomorrow", "who am I seeing on Friday", "what's my name for the booking",
       "my name", "what is my email address", "what's my work address", "where do I work",
       "where was I born", "what's the weather", "what's my schedule", "who are you",
       "what's the name of my dentist", "who is Marisol", "tell me about myself",
       # past tense / present LOCATION / finer than the account knows: all fall to the brain
       "where did I live", "where did we live", "what city am I in", "what country are we in",
       "which suburb do I live in", "what area do we live in", "what's my middle name",
       "what do you know about me", "where am I from", "what's your name", "x" * 200]


@pytest.mark.parametrize("kind,text", [(k, t) for k, ts in POS.items() for t in ts])
def test_identity_question_kinds(kind, text):
    assert idf.identity_question_kind(text) == kind


@pytest.mark.parametrize("text", NEG)
def test_near_misses_are_not_identity_questions(text):
    assert idf.identity_question_kind(text) == ""


# ── account -> Identity ───────────────────────────────────────────────────────

def test_name_from_account_is_title_cased_and_never_the_raw_id():
    i = _ident()
    assert (i.name, i.account_name, i.has_preferred_name) == ("Zed", "Zed", False)


def test_preferred_name_override_comes_from_the_settings_field_only():
    i = _ident(prefs={"preferred_name": "zeddy"})
    assert i.name == "Zeddy" and i.account_name == "Zed" and i.has_preferred_name


def test_settings_display_name_beats_username():
    i = idf.build_identity(UID, username="zed", settings={"display_name": "Zed Quill"}, users_name="zed")
    assert i.name == "Zed Quill"


def test_place_region_and_country_for_the_household_default():
    i = _ident()
    assert (i.city, i.region, i.country) == ("Hobart", "Tasmania", "Australia")
    assert idf.identity_line(i) == (
        "You are talking to Zed, a member of this household in Hobart, Tasmania, Australia.")


def test_a_users_own_city_gets_no_borrowed_region():
    i = _ident(city="Dunedin", country="NZ")
    assert (i.city, i.region, i.country) == ("Dunedin", "", "New Zealand")


def test_no_location_means_no_place_clause(monkeypatch):
    for k in ("ZOE_LOCATION_CITY", "ZOE_LOCATION_REGION", "ZOE_LOCATION_COUNTRY", "ZOE_TIMEZONE"):
        monkeypatch.delenv(k, raising=False)
    i = idf.build_identity(UID, username="zed", sysloc={})
    assert idf.identity_line(i) == "You are talking to Zed, a member of this household."
    assert idf.reply_for("home", i) is None  # nothing to say -> falls through to the brain


def test_current_location_users_city_is_not_home():
    """weather_preferences.city is a WEATHER location; with use_current_location it follows the
    device, so a travel city must not be reported as home - the household default stands in."""
    trip = _ident(city="Dunedin", country="NZ", use_current_location=True)
    assert (trip.city, trip.region, trip.country) == ("Hobart", "Tasmania", "Australia")
    fixed = _ident(city="Dunedin", country="NZ", use_current_location=False)
    assert fixed.city == "Dunedin"


def test_current_location_with_no_household_default_means_no_home(monkeypatch):
    for k in ("ZOE_LOCATION_CITY", "ZOE_LOCATION_REGION", "ZOE_LOCATION_COUNTRY", "ZOE_TIMEZONE"):
        monkeypatch.delenv(k, raising=False)
    i = idf.build_identity(UID, username="zed", city="Dunedin", country="NZ", sysloc={},
                           use_current_location=True)
    assert i.city == "" and idf.reply_for("home", i) is None


def test_no_account_name_no_identity():
    assert idf.build_identity(UID, username="", users_name="", sysloc=SYSLOC) is None


@pytest.mark.parametrize("uid", ["", "guest", "voice-guest", "default"])
def test_shared_identities_have_none(uid):
    assert asyncio.run(idf.resolve_identity(uid, db=FakeDB())) is None


def test_unregistered_id_has_none_even_with_a_users_row():
    """Harness/demo ids get a ``users`` row from the chat path but no auth account."""
    assert asyncio.run(idf.resolve_identity("bar-s1", db=FakeDB(auth=False))) is None


def test_resolve_reads_the_account_tables_in_ONE_query_and_caches():
    db = FakeDB(prefs={"preferred_name": "zeddy", "home_address": "1 Test Lane"})
    first = asyncio.run(idf.resolve_identity(UID, db=db))
    assert len(db.queries) == 1
    n = len(db.queries)
    assert (first.name, first.street_address, first.city) == ("Zeddy", "1 Test Lane", "Hobart")
    assert asyncio.run(idf.resolve_identity(UID, db=db)) is first and len(db.queries) == n


def test_a_failing_db_is_negative_cached_not_retried_every_turn():
    class Boom:
        calls = 0

        async def execute(self, *a, **k):
            Boom.calls += 1
            raise RuntimeError("db down")

    assert asyncio.run(idf.resolve_identity(UID, db=Boom())) is None
    n = Boom.calls
    assert asyncio.run(idf.resolve_identity(UID, db=Boom())) is None
    assert Boom.calls == n  # second turn did not touch the DB


# ── the answer ────────────────────────────────────────────────────────────────

@pytest.fixture
def account(monkeypatch):
    async def _res(uid, db=None):
        return _ident() if uid == UID else None
    monkeypatch.setattr(idf, "resolve_identity", _res)
    return _ident()


@pytest.mark.parametrize("text,reply", [
    ("whats my name", "Your name is Zed."),
    ("what do you call me", "I call you Zed."),
    ("who am I", "You're Zed, a member of this household in Hobart, Tasmania."),
    ("where do i live", "You live in Hobart, Tasmania."),
    ("which city do I live in", "You live in Hobart."),
    ("what state do we live in", "You live in Tasmania."),
    ("which country do I live in", "You live in Australia."),
    ("what's my address",
     "I only have your location, Hobart, Tasmania, not a street address."),
])
def test_one_sentence_answers_from_the_account(account, text, reply):
    kind, got = asyncio.run(idf.maybe_answer(text, UID))
    assert got == reply and got.count(". ") == 0


def test_street_address_only_when_the_settings_field_exists(monkeypatch):
    async def _res(uid, db=None):
        return _ident(prefs={"home_address": "1 Test Lane"})
    monkeypatch.setattr(idf, "resolve_identity", _res)
    assert asyncio.run(idf.maybe_answer("what's my address", UID))[1] == "Your address is 1 Test Lane."


def test_guests_and_non_questions_fall_through(account):
    assert asyncio.run(idf.maybe_answer("whats my name", "guest")) is None
    assert asyncio.run(idf.maybe_answer("whats my name", "someone-else")) is None
    assert asyncio.run(idf.maybe_answer("what's the weather", UID)) is None


# ── NEGATIVE CONTROL: a memory row cannot change the answer, but is logged ────

def _polluted_service(text=f"User's name is {WRONG}."):
    class Svc:
        listed = 0

        async def list_by_status(self, **kw):
            Svc.listed += 1
            return [MemoryRef(id="mem-1", text=text,
                              metadata={"source": "chat_regex", "added_by": "chat_regex",
                                        "reviewed_by": "digest", "session_id": "s-old"}),
                    MemoryRef(id="mem-2", text=f"User's name is {REAL}.", metadata={})]
    return Svc()


def test_negative_control_memory_row_cannot_change_the_name(account, monkeypatch):
    svc = _polluted_service()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)

    async def go():
        hit = await idf.maybe_answer("whats my name", UID)
        await asyncio.gather(*idf._BG)  # let the background conflict check finish
        return hit

    kind, reply = asyncio.run(go())
    assert reply == "Your name is Zed." and WRONG.split()[0] not in reply
    assert svc.listed == 1  # memory was consulted only AFTER the answer, to log


def test_conflict_is_logged_without_text(caplog):
    svc = _polluted_service()
    caplog.set_level(logging.INFO, logger=idf.logger.name)
    n = asyncio.run(idf._log_conflicts(UID, "name", _ident(), svc=svc))
    assert n == 1  # the genuine row is not a conflict; the polluted one is
    assert f"IDENTITY_CONFLICT user={UID} kind=name" in caplog.text
    assert WRONG.split()[0] not in caplog.text and WRONG.split()[1] not in caplog.text


@pytest.mark.parametrize("asserted,conflict", [
    ("Zed", False), ("Zed Quill", False),     # a longer form of the real name is fine
    ("zed", False), ("Mika Vale", True), ("Mika", True),
])
def test_name_conflict_rule(asserted, conflict):
    assert idf.name_conflicts(asserted, _ident()) is conflict


@pytest.mark.parametrize("asserted,meta,verdict", [
    ("Zed Quill", {}, "match"),
    ("Mika Vale", {"source": "chat_regex"}, "conflict"),
    ("Mika Vale", {"source": "voice_fact"}, "needs_review"),   # the user dictated it
    ("Zeddy", {"source": "chat_regex"}, "needs_review"),        # a plausible nickname of Zed
    ("Zeddy", {}, "needs_review"),
    ("Zedd Quill", {"source": "digest"}, "needs_review"),
])
def test_name_assertion_classes(asserted, meta, verdict):
    assert idf.classify_name_assertion(asserted, _ident(), meta) == verdict
    assert idf.name_conflicts(asserted, _ident(), meta) is (verdict == "conflict")


def test_a_nickname_row_is_not_logged_as_pollution(caplog):
    class Svc:
        async def list_by_status(self, **kw):
            return [MemoryRef(id="m", text="User's name is Zeddy.", metadata={"source": "chat_regex"})]

    caplog.set_level(logging.INFO, logger=idf.logger.name)
    assert asyncio.run(idf._log_conflicts(UID, "name", _ident(), svc=Svc())) == 0
    assert "IDENTITY_CONFLICT" not in caplog.text


def test_preferred_name_is_not_a_conflict():
    assert idf.name_conflicts("Zeddy", _ident(prefs={"preferred_name": "zeddy"})) is False


def test_home_conflict_rule():
    assert idf.home_conflicts("Perth", _ident()) is True
    assert idf.home_conflicts("Hobart, Tasmania", _ident()) is False


# ── fast_tiers: chat/telegram yes, voice no ───────────────────────────────────

def test_profiles_gate_the_tier():
    import fast_tiers

    assert fast_tiers.profile_for("chat").get("identity_tier")
    assert fast_tiers.profile_for("telegram").get("identity_tier")
    for ch in ("voice", "livekit", None, "unknown"):
        assert not fast_tiers.profile_for(ch).get("identity_tier")


def test_resolve_answers_from_the_account_before_anything_else(account):
    import fast_tiers

    res = asyncio.run(fast_tiers.resolve("whats my name", UID, "s1", channel="chat"))
    assert res is not None and res.reply == "Your name is Zed."
    assert (res.domain, res.intent, res.tier) == ("identity", "identity_name", "identity")
    tg = asyncio.run(fast_tiers.resolve("where do i live", UID, "s1", channel="telegram"))
    assert tg.reply == "You live in Hobart, Tasmania."


def test_resolve_leaves_voice_to_its_scope_gate(account, monkeypatch):
    import fast_tiers
    import semantic_router

    called = []

    async def spy(text, uid):
        called.append(text)

    monkeypatch.setattr(fast_tiers, "_identity_tier", spy)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)
    assert asyncio.run(fast_tiers.resolve("whats my name", UID, "s1", channel="voice")) is None
    assert called == []


def test_resolve_does_not_claim_other_questions(account, monkeypatch):
    import fast_tiers
    import semantic_router

    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)
    assert asyncio.run(fast_tiers.resolve("who am I meeting tomorrow", UID, "s1", channel="chat")) is None


# ── the writer wall (MemoryService) ───────────────────────────────────────────

class _Col:
    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, *, ids, documents, metadatas, **_kw):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    def update(self, *, ids, metadatas, **_kw):
        for i, m in zip(ids, metadatas):
            self.rows[i] = (self.rows[i][0], dict(m))

    def get(self, *, ids=None, where=None, include=None, **_kw):
        keys = [i for i in ids if i in self.rows] if ids is not None else list(self.rows)
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys],
                "metadatas": [dict(self.rows[i][1]) for i in keys]}


@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-identity-wall")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def _ingest(svc, text, source):
    return asyncio.run(svc.ingest(text, user_id=UID, source=source, status="approved", confidence=0.9))


# The wall is an ALLOW-list of direct sources, so every other label - the known mining
# lanes, the zoe_agent regex FALLBACK (used when memory_extractor cannot be imported), the
# agent tools, and a label nobody has invented yet - is walled.
AUTOMATIC = ["chat_regex", "chat_regex_fallback", "turn_digest", "conversation", "ambient", "digest",
             "consolidation", "synthesis", "music_digest", "voice_regex", "voice_turn_digest",
             "idle_consolidation", "mcp", "zoe_agent", "profile-analysis", "some_future_extractor", ""]


class _Everything:
    """A DIRECT_USER_SOURCES stand-in that allows every label (= the wall removed)."""

    def __contains__(self, _):
        return True


@pytest.mark.parametrize("source", AUTOMATIC)
def test_automatic_writers_cannot_store_the_users_name(svc, source, caplog):
    caplog.set_level(logging.INFO, logger=memory_service.logger.name)
    assert _ingest(svc, f"User's name is {WRONG}.", source) is None
    assert not svc._col.rows
    assert f"IDENTITY_FACT_BLOCKED user={UID} source={source} kind=name" in caplog.text
    assert WRONG.split()[0] not in caplog.text


@pytest.mark.parametrize("text", ["The user's full name is Mika Vale", "User is called Mika Vale",
                                  "user's name: Mika Vale"])
def test_phrasing_variants_are_walled_too(svc, text):
    assert _ingest(svc, text, "digest") is None


@pytest.mark.parametrize("source", ["voice_fact", "brain_tool", "review_ui", "proposal", "identity_audit"])
def test_explicit_teach_and_operator_sources_still_store(svc, source):
    assert _ingest(svc, f"User's name is {REAL}.", source) is not None


@pytest.mark.parametrize("text", ["Marisol's name is Mika Vale.", "User's dog is named Biscuit.",
                                  "User lives in Hobart.", "User asked me to remember: my name is a secret"])
def test_other_facts_from_automatic_writers_are_untouched(svc, text):
    assert _ingest(svc, text, "chat_regex") is not None


def test_break_the_fix_control_the_wall_is_what_blocks(svc, monkeypatch):
    """With the wall removed the row is stored - for the fallback label too: the tests above
    are measuring the wall, not an accident of the fake."""
    monkeypatch.setattr(idf, "DIRECT_USER_SOURCES", _Everything())
    assert _ingest(svc, f"User's name is {WRONG}.", "chat_regex") is not None
    assert _ingest(svc, f"User's name is {WRONG} Jr.", "chat_regex_fallback") is not None


def test_automatic_edit_cannot_supersede_into_a_name_assertion(svc):
    seed = _ingest(svc, f"User's name is {REAL}.", "voice_fact")
    assert asyncio.run(svc.review(seed.id, decision="edit", actor="digest",
                                  edits=f"User's name is {WRONG}.")) is None
    assert svc._col.rows[seed.id][1]["status"] == "approved"
    assert len(svc._col.rows) == 1
    # an operator/UI edit is a different thing
    ok = asyncio.run(svc.review(seed.id, decision="edit", actor="review_ui",
                                edits=f"User's name is {REAL} Quill."))
    assert ok is not None and svc._col.rows[seed.id][1]["status"] == "superseded"


def test_digest_replay_of_the_live_incident_leaves_the_genuine_row(svc, monkeypatch):
    """Synthetic replay: today's transcript yields ``User's name is <third person>``; the
    contradiction pass says it contradicts the stored name. Before the wall the digest
    superseded the genuine row (source/session carried forward, so it LOOKED like a regex
    row). Now the genuine row stays approved and nothing new is written."""
    import memory_digest

    seed = _ingest(svc, f"User's name is {REAL}.", "voice_fact")

    async def todays(*a, **k):
        return "user said a long enough synthetic transcript " * 6

    async def extract(_text):
        return [{"fact": f"User's name is {WRONG}.", "type": "fact"}]

    async def always_contradiction(*a, **k):
        return True

    async def search(*a, **k):
        return [types.SimpleNamespace(id=seed.id, text=seed.text)]

    async def no_blob(*a, **k):
        return ""

    stub = types.ModuleType("zoe_agent")
    stub._mempalace_load_user_facts = no_blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    monkeypatch.setattr(memory_digest, "_load_todays_messages", todays)
    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(memory_digest, "_is_contradiction", always_contradiction)
    svc.search = search

    asyncio.run(memory_digest.run_memory_digest(UID))
    rows = svc._col.rows
    assert rows[seed.id][1]["status"] == "approved"
    assert not any(WRONG in doc for doc, _ in rows.values())

    # defence in depth: the identity wall is the SPECIAL CASE of the authority rule
    # (memory_authority), so removing it alone still leaves the genuine row standing
    monkeypatch.setattr(idf, "DIRECT_USER_SOURCES", _Everything())
    asyncio.run(memory_digest.run_memory_digest(UID))
    assert rows[seed.id][1]["status"] == "approved"

    # the nightly observation gate (ZMB K1/K5) is a THIRD wall in front of the contradiction pass: with the identity wall
    # AND the authority rule removed, a fragment-derived name still cannot retire the genuine row - the gate holds it
    # (never an approved row, never served)
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    asyncio.run(memory_digest.run_memory_digest(UID))
    assert rows[seed.id][1]["status"] == "approved"
    assert not any(m.get("status") == "approved" and WRONG in doc for doc, m in rows.values())

    # break-the-fix control: with ALL THREE walls removed the SAME replay reproduces the incident
    monkeypatch.setenv("ZOE_DIGEST_OBSERVATION_GATE", "off")
    asyncio.run(memory_digest.run_memory_digest(UID))
    assert rows[seed.id][1]["status"] == "superseded"
    assert any(WRONG in doc for doc, _ in rows.values())


# ── surname replies never deny what the account merely lacks ──────────────────

def test_surname_questions_fall_through_for_a_first_name_only_account():
    one = _ident()
    assert idf.reply_for("fullname", one) is None and idf.reply_for("surname", one) is None


def test_surname_and_full_name_answer_from_a_multi_token_name_without_negation():
    two = idf.build_identity(UID, username="zed", settings={"display_name": "Sam Rivers"}, sysloc=SYSLOC)
    assert idf.reply_for("fullname", two) == "Your full name is Sam Rivers."
    assert idf.reply_for("surname", two) == "Your surname is Rivers."
    for kind in ("fullname", "surname", "name", "call", "self", "home"):
        reply = idf.reply_for(kind, two) or ""
        assert "don't" not in reply and "no surname" not in reply and " not " not in f" {reply} "


# ── home: present-tense home only, the right granularity ──────────────────────

def test_home_questions_answer_at_the_granularity_asked():
    i = _ident()
    assert idf.reply_for("home_city", i) == "You live in Hobart."
    assert idf.reply_for("home_region", i) == "You live in Tasmania."
    assert idf.reply_for("home_country", i) == "You live in Australia."
    nz = _ident(city="Dunedin", country="NZ")  # no region known for a user's own city
    assert idf.reply_for("home_region", nz) is None and idf.reply_for("home_country", nz) == "You live in New Zealand."


# ── an explicit rename (the one legitimate way a name changes) ────────────────

RENAME_OK = [("call me Jay", "Jay"), ("Call me jay please", "Jay"), ("you can call me Jay", "Jay"),
             ("my name is Jay", "Jay"), ("My name's Jay", "Jay"), ("actually my name is Mika Vale", "Mika Vale"),
             ("I go by Jay", "Jay"), ("hey zoe, call me Jay", "Jay"), ("everyone calls me Jay", "Jay")]
RENAME_NO = ["call me later", "can you call me a taxi", "call me when you are ready", "call me back",
             "my name is Jay and I live here", "what is my name?", "my name is?", "actually it's Jay",
             "call me", "call me please", "don't call me that", "call me tomorrow", "who calls me Jay"]


@pytest.mark.parametrize("text,name", RENAME_OK)
def test_rename_shapes(text, name):
    assert idf.rename_request(text) == name


@pytest.mark.parametrize("text", RENAME_NO)
def test_non_renames(text):
    assert idf.rename_request(text) == ""


@pytest.fixture
def live_account(monkeypatch):
    """A registered account backed by FakeDB; ``user_prefs.set_pref`` writes into it."""
    import user_prefs

    db = FakeDB()
    real = idf.resolve_identity
    writes = []

    async def bound(uid, db_=None, **kw):
        return await real(uid, db=db, **kw) if uid == UID else None

    async def set_pref(uid, key, value, *, db=None):
        writes.append((uid, key, value))
        fake.prefs = {**(fake.prefs or {}), key: value}

    fake = db
    monkeypatch.setattr(idf, "resolve_identity", bound)
    monkeypatch.setattr(user_prefs, "set_pref", set_pref)
    db.writes = writes
    return db


def test_rename_via_chat_updates_the_answer_the_line_and_the_greeting(live_account):
    import fast_tiers

    res = asyncio.run(fast_tiers.resolve("call me Jay", UID, "s1", channel="chat"))
    assert res.reply == "I'll call you Jay." and res.intent == "identity_rename" and res.tier == "identity"
    assert live_account.writes == [(UID, "preferred_name", "Jay")]
    assert asyncio.run(fast_tiers.resolve("whats my name", UID, "s1", channel="chat")).reply == "Your name is Jay."
    assert asyncio.run(fast_tiers.resolve("what do you call me", UID, "s1", channel="telegram")).reply == "I call you Jay."
    ident = asyncio.run(idf.resolve_identity(UID))
    assert idf.identity_line(ident).startswith("You are talking to Jay,")
    assert asyncio.run(idf.preferred_name(UID)) == "Jay"  # what the greeting reads: ONE source


def test_greeting_reads_the_same_field(live_account, monkeypatch):
    import intent_router

    class _Intent:
        slots = {"time_of_day": "morning"}

    assert asyncio.run(intent_router._execute_greeting(_Intent(), UID)).startswith("Good morning!")  # never chose a name
    asyncio.run(idf.apply_rename(UID, "Jay"))
    assert asyncio.run(intent_router._execute_greeting(_Intent(), UID)).startswith("Good morning, Jay!")


def test_rename_is_for_registered_accounts_on_chat_and_telegram_only(live_account, monkeypatch):
    import fast_tiers
    import semantic_router

    assert asyncio.run(idf.maybe_answer("call me Jay", "guest")) is None
    assert asyncio.run(idf.maybe_answer("call me Jay", "someone-else")) is None
    assert live_account.writes == []
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)
    assert asyncio.run(fast_tiers.resolve("call me Jay", UID, "s1", channel="voice")) is None
    assert live_account.writes == []


def test_a_failed_rename_write_falls_through_instead_of_lying(live_account, monkeypatch):
    import user_prefs

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(user_prefs, "set_pref", boom)
    assert asyncio.run(idf.maybe_answer("call me Jay", UID)) is None


def test_rename_and_wall_are_logged_as_different_things(live_account, svc, caplog):
    """The user's own rename is an accepted settings write; the same words from an
    automatic writer are refused - and the two log lines say which is which."""
    caplog.set_level(logging.INFO)
    assert asyncio.run(idf.maybe_answer("my name is Jay", UID))[0] == "rename"
    assert f"IDENTITY_RENAME user={UID} origin=explicit" in caplog.text
    assert _ingest(svc, "User's name is Jay.", "chat_regex") is None  # the extractor's copy of it
    assert f"IDENTITY_FACT_BLOCKED user={UID} source=chat_regex kind=name origin=automatic" in caplog.text


def test_after_a_rename_a_digest_asserted_name_still_cannot_land(live_account, svc):
    asyncio.run(idf.maybe_answer("call me Jay", UID))
    assert _ingest(svc, f"User's name is {WRONG}.", "digest") is None
    assert asyncio.run(idf.maybe_answer("whats my name", UID))[1] == "Your name is Jay."


# ── cold lookup budget + stale fallback ───────────────────────────────────────

def test_cold_lookup_over_budget_returns_none_then_warms_in_the_background():
    class Slow(FakeDB):
        async def execute(self, sql, params=()):
            await asyncio.sleep(0.15)
            return await super().execute(sql, params)

    async def go():
        db = Slow()
        t0 = asyncio.get_running_loop().time()
        first = await idf.resolve_identity(UID, db=db, budget_s=0.02)
        waited = asyncio.get_running_loop().time() - t0
        await asyncio.sleep(0.3)  # the load finishes on its own
        return first, waited, await idf.resolve_identity(UID, db=db, budget_s=0.02), len(db.queries)

    first, waited, second, queries = asyncio.run(go())
    assert first is None and waited < 0.1
    assert second is not None and second.name == "Zed" and queries == 1  # one load, not two


def test_expired_entry_is_the_fallback_when_the_refresh_fails():
    import time

    stale = _ident()
    idf._cache[UID] = (time.monotonic() - 10_000, stale)

    class Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert asyncio.run(idf.resolve_identity(UID, db=Boom())) is stale


def test_names_are_compared_whole_not_by_one_shared_token():
    jason = idf.build_identity(UID, username="x", settings={"display_name": "Jason Smith"}, sysloc=SYSLOC)
    for asserted, verdict in [("Jason Smith", "match"), ("Jason", "match"), ("Jason Q Smith", "match"),
                              ("Michael Smith", "conflict"), ("Smith", "match"), ("Michael", "conflict")]:
        assert idf.classify_name_assertion(asserted, jason, {"source": "digest"}) == verdict, asserted


def test_full_name_and_surname_come_from_the_account_name_not_a_nickname():
    i = idf.build_identity(UID, username="x", settings={"display_name": "Sam Rivers"},
                           prefs={"preferred_name": "Sammy"}, sysloc=SYSLOC)
    assert i.name == "Sammy"
    assert idf.reply_for("fullname", i) == "Your full name is Sam Rivers."
    assert idf.reply_for("surname", i) == "Your surname is Rivers."
    assert idf.reply_for("name", i) == "Your name is Sammy."


def test_the_zoe_agent_regex_fallback_cannot_store_the_name_end_to_end(svc, monkeypatch):
    """zoe_agent._background_memory_save falls back to its own patterns when memory_extractor
    cannot be imported; they emit "User's name is ..." with source=chat_regex_fallback."""
    import zoe_agent

    src = open(zoe_agent.__file__).read()
    assert 'source="chat_regex_fallback"' in src  # the label this test stands for is really in use
    assert _ingest(svc, f"User's name is {WRONG}", "chat_regex_fallback") is None
    assert not svc._col.rows


def test_the_owner_reviewing_their_own_memory_is_a_direct_edit(svc):
    """The review UI passes the user id as the actor; that is the user, not an extractor."""
    seed = _ingest(svc, f"User's name is {REAL}.", "voice_fact")
    ok = asyncio.run(svc.review(seed.id, decision="edit", actor=UID, edits=f"User's name is {REAL} Quill."))
    assert ok is not None
    assert asyncio.run(svc.review(ok.id, decision="edit", actor="some_extractor",
                                  edits=f"User's name is {WRONG}.")) is None


# ── every self-name template is walled, not only "name is" (ZMB H5) ───────────
#
# The regex extractor's own "call me X" template emits "User goes by X" (zoe_agent's fallback:
# "User goes by: X") and the model writers paraphrase ("prefers to be called", "is known as",
# "nickname is", "name's"). The wall only knew "User's name is X" / "User is called X", so those
# rows were stored and a name nobody in the account goes by could stand beside the real one.

SELF_NAME_TEMPLATES = [
    "User goes by Mika Vale.",
    "User goes by: Mika Vale",                    # zoe_agent's regex fallback
    "User goes by the name Mika Vale.",
    "The user also goes by Mika Vale.",
    "User prefers to be called Mika Vale.",
    "User likes to be called Mika.",
    "User would like to be addressed as Mika.",
    "User prefers being called Mika.",
    "User wants to be known as Mika.",
    "User asked me to call them Mika.",
    "User is called Mika Vale.",
    "User is also known as Mika Vale.",
    "User is named Mika Vale.",
    "User's name's Mika Vale.",
    "User's nickname is Mika.",
    "The user's preferred name is Mika.",
    "User's first name is Mika.",
    "User's alias: Mika",
    "User introduced themselves as Mika.",
    "User calls themselves Mika.",
    "User says their name is Mika.",
    "The owner goes by Mika.",
    "Account holder goes by Mika.",
    "user goes by mika",                          # a lower-case transcript is still a name claim
]


@pytest.mark.parametrize("text", SELF_NAME_TEMPLATES)
def test_every_self_name_template_is_a_name_assertion(text):
    assert idf.is_user_name_assertion(text), text
    assert idf.asserted_user_name(text).lower().startswith("mika")


@pytest.mark.parametrize("text", SELF_NAME_TEMPLATES)
@pytest.mark.parametrize("source", ["digest", "chat_regex", "consolidation"])
def test_an_automatic_writer_cannot_store_any_self_name_template(svc, text, source):
    assert _ingest(svc, text, source) is None
    assert not svc._col.rows


@pytest.mark.parametrize("text", ["User goes by Mika Vale.", "User prefers to be called Mika.",
                                  "User is also known as Mika Vale.", "User's nickname is Mika."])
def test_the_automatic_edit_path_is_walled_for_every_template_too(svc, text):
    seed = _ingest(svc, f"User's name is {REAL}.", "voice_fact")
    assert asyncio.run(svc.review(seed.id, decision="edit", actor="digest", edits=text)) is None


@pytest.mark.parametrize("text", ["User goes by Jay.", "User prefers to be called Jay.",
                                  "User is also known as Jay.", "User's nickname is Jay."])
@pytest.mark.parametrize("source", ["voice_fact", "brain_tool", "review_ui", "proposal"])
def test_control_the_users_own_explicit_teach_still_stores(svc, text, source):
    assert _ingest(svc, text, source) is not None


@pytest.mark.parametrize("text", [
    "User goes by bus.", "User goes by train to work.", "User goes by the book.", "User goes by car.",
    "User is called a nerd by friends.", "User goes by foot.",
    "Marisol goes by Mika.", "User's dog is called Biscuit.", "User's friend Dana goes by Dee.",
    "User's sister is known as Bea.", "User asked me to remember: call me Mika",
])
def test_control_not_a_self_name_claim_is_untouched(svc, text):
    assert not idf.is_user_name_assertion(text)
    assert _ingest(svc, text, "digest") is not None


def test_control_the_users_own_rename_still_works_and_the_wall_still_holds(live_account, svc):
    """"call me Jay" from the user's turn renames the ACCOUNT (a settings write, not a memory row);
    the extractors' copies of the same words are refused, in every template."""
    assert asyncio.run(idf.maybe_answer("call me Jay", UID))[0] == "rename"
    assert live_account.writes == [(UID, "preferred_name", "Jay")]
    for text in ("User goes by Jay.", "User goes by: Jay", "User prefers to be called Jay.",
                 "User is also known as Jay.", "User's nickname is Jay."):
        assert _ingest(svc, text, "chat_regex") is None
    assert asyncio.run(idf.maybe_answer("whats my name", UID))[1] == "Your name is Jay."


def test_the_regex_extractors_own_template_is_walled_end_to_end(svc):
    """The text memory_extractor itself emits for "call me X" is what the wall must know."""
    import memory_extractor

    cands = [c.text for c in memory_extractor.extract_candidates("you can call me Mika", "")]
    assert "User goes by Mika" in cands
    assert all(_ingest(svc, t, "chat_regex") is None for t in cands)
    assert not svc._col.rows


def test_break_the_fix_control_the_new_templates_are_what_block_them(svc, monkeypatch):
    """With the vocabulary gap re-opened (only the original two templates) the goes-by row is stored."""
    monkeypatch.setattr(idf, "_NAME_ASSERT_RES", idf._NAME_ASSERT_RES[:2])
    monkeypatch.setattr(idf, "_EXPLICIT_ATTR_TEMPLATES", (0,))
    assert _ingest(svc, "User goes by Mika Vale.", "digest") is not None
