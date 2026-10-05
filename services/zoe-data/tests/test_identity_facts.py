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
    def __init__(self, *, auth=True, users_name=REAL.lower(), prefs=None, wp=None, sysloc=SYSLOC):
        self.tables = {
            "auth_users": {"username": REAL.lower(), "settings": "{}"} if auth else None,
            "users": {"name": users_name} if users_name is not None else None,
            "user_preferences": {"prefs": json.dumps(prefs)} if prefs is not None else None,
            "weather_preferences": wp,
            "system_preferences": {"value": json.dumps(sysloc)} if sysloc is not None else None,
        }
        self.queries: list[str] = []

    async def execute(self, sql, params=()):
        self.queries.append(sql)
        for table, row in self.tables.items():
            if f"FROM {table}" in sql:
                return _Cur(row)
        raise AssertionError(f"unexpected SQL {sql!r}")


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
    "fullname": ["what's my full name", "whats my last name", "what is my surname"],
    "call": ["what do you call me", "what am I called", "who do you call me"],
    "self": ["who am I", "Who am I?", "hey zoe, who am i"],
    "home": ["where do I live", "where do we live", "what city am I in", "which city do I live in",
             "what suburb do we live in", "where's my home", "what country are we in"],
    "address": ["what's my address", "what is my home address", "whats our address"],
}
NEG = ["who am I meeting tomorrow", "who am I seeing on Friday", "what's my name for the booking",
       "my name", "what is my email address", "what's my work address", "where do I work",
       "where was I born", "what's the weather", "what's my schedule", "who are you",
       "what's the name of my dentist", "who is Marisol", "tell me about myself",
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


def test_no_account_name_no_identity():
    assert idf.build_identity(UID, username="", users_name="", sysloc=SYSLOC) is None


@pytest.mark.parametrize("uid", ["", "guest", "voice-guest", "default"])
def test_shared_identities_have_none(uid):
    assert asyncio.run(idf.resolve_identity(uid, db=FakeDB())) is None


def test_unregistered_id_has_none_even_with_a_users_row():
    """Harness/demo ids get a ``users`` row from the chat path but no auth account."""
    assert asyncio.run(idf.resolve_identity("bar-s1", db=FakeDB(auth=False))) is None


def test_resolve_reads_the_account_tables_and_caches():
    db = FakeDB(prefs={"preferred_name": "zeddy", "home_address": "1 Test Lane"})
    first = asyncio.run(idf.resolve_identity(UID, db=db))
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
    ("what city am I in", "You live in Hobart, Tasmania."),
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


@pytest.mark.parametrize("source", sorted(idf.AUTOMATIC_SOURCES))
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


@pytest.mark.parametrize("source", ["voice_fact", "brain_tool", "review_ui", "identity_audit"])
def test_explicit_teach_and_operator_sources_still_store(svc, source):
    assert _ingest(svc, f"User's name is {REAL}.", source) is not None


@pytest.mark.parametrize("text", ["Marisol's name is Mika Vale.", "User's dog is named Biscuit.",
                                  "User lives in Hobart.", "User asked me to remember: my name is a secret"])
def test_other_facts_from_automatic_writers_are_untouched(svc, text):
    assert _ingest(svc, text, "chat_regex") is not None


def test_break_the_fix_control_the_wall_is_what_blocks(svc, monkeypatch):
    """If AUTOMATIC_SOURCES stops covering the writer the row is stored: the tests above
    are measuring the wall, not an accident of the fake."""
    monkeypatch.setattr(idf, "AUTOMATIC_SOURCES", frozenset())
    assert _ingest(svc, f"User's name is {WRONG}.", "chat_regex") is not None


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
    monkeypatch.setattr(idf, "AUTOMATIC_SOURCES", frozenset())
    asyncio.run(memory_digest.run_memory_digest(UID))
    assert rows[seed.id][1]["status"] == "approved"

    # break-the-fix control: with BOTH walls removed the SAME replay reproduces the incident
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    asyncio.run(memory_digest.run_memory_digest(UID))
    assert rows[seed.id][1]["status"] == "superseded"
    assert any(WRONG in doc for doc, _ in rows.values())
