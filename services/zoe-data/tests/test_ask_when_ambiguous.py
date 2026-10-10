"""Ask when ambiguous, do when clear (ZOE_ASK_WHEN_AMBIGUOUS) - the person bench's P7.

Baseline (live, 2026-10-09): "Tell me about Marisol." with two Marisols among the contacts drew exactly one question
that names the choice in 4 of 20 asks (the rest guessed, listed both without asking, or answered about neither); the
clear turns - one contact, no question - were 20 of 20 and MUST stay so. The detector is a deterministic tier ahead of
the router and the brain, so the question comes before any tool call or write.

Negative controls: a clear turn is never asked (the "never asks" and "always asks" stub policies each fail one half);
a statement is never asked about; a full name or a settling role word settles it; one question per request - no
loop. Synthetic names only (ci_safe: fakes, no DB).
"""
from __future__ import annotations

import asyncio

import pytest

import ask_when_ambiguous as awa

pytestmark = pytest.mark.ci_safe

C = awa.Candidate
UID = "demo_bar_0a1b2c3d"
SID = "sess-7"
PEOPLE = [C("1", "Marisol Okafor", "colleague"), C("2", "Marisol Vance", "sister"), C("3", "Percival Dunmore", "brother"),
          C("4", "Priya Nair", "friend"), C("5", "Priya Shah", ""), C("6", "Tomas Reyes", "neighbour")]
AMBIG = ["Tell me about Marisol.", "What's Marisol's birthday?", "Remind me who Marisol is.", "Where does Marisol live?",
         "Is Marisol coming on Thursday?"]
CLEAR = ["Tell me about Percival.", "Who is Percival?", "What do you know about Percival Dunmore?",
         "Remind me who Percival is.", "Where does Percival fit in my family?"]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(awa.ENV, raising=False)
    awa.forget_roster()
    awa._PENDING.clear()

    async def people(_uid):
        return list(PEOPLE)

    async def fingerprint(_uid):
        return (str(len(PEOPLE)), "", str(sum(len(c.name) for c in PEOPLE)))

    monkeypatch.setattr(awa, "_load_people", people)
    monkeypatch.setattr(awa, "_people_fingerprint", fingerprint)
    yield
    awa.forget_roster()
    awa._PENDING.clear()


def _ask(text, monkeypatch, mode="enforce"):
    monkeypatch.setenv(awa.ENV, mode)
    return _run(awa.handle(text, UID, SID))


# -- the question -----------------------------------------------------------------------------------

@pytest.mark.parametrize("text", AMBIG)
def test_an_ambiguous_request_gets_one_question_that_names_the_choice(text, monkeypatch):
    q = _ask(text, monkeypatch)
    assert q == "Which Marisol do you mean: Marisol Okafor, your colleague, or Marisol Vance, your sister?"
    assert q.count("?") == 1 and "Marisol Okafor" in q and "Marisol Vance" in q


@pytest.mark.parametrize("text", CLEAR)
def test_a_clear_request_is_never_asked(text, monkeypatch):
    assert _ask(text, monkeypatch) == ""


@pytest.mark.parametrize("text", [
    "Tell me about Marisol Vance.", "What's Marisol Okafor's birthday?", "Call my sister Marisol", "Text Marisol from work",
    "Remind me who Marisol the colleague is", "tell me about my sister Marisol",
])
def test_a_name_that_settles_which_one_is_never_asked(text, monkeypatch):
    assert _ask(text, monkeypatch) == ""


@pytest.mark.parametrize("text", ["Marisol is coming over on Thursday.", "I saw Marisol yesterday", "Marisol called me earlier",
                                  "Thanks, Marisol is lovely"])
def test_a_statement_is_never_asked_about(text, monkeypatch):
    assert _ask(text, monkeypatch) == ""


@pytest.mark.parametrize("text", ["Call Priya", "text priya please", "Remind me to ring Priya at five", "Remember that Priya likes tea"])
def test_actions_are_asked_before_they_run(text, monkeypatch):
    q = _ask(text, monkeypatch)
    assert q.startswith("Which Priya do you mean: Priya Nair, your friend, or Priya Shah?") and q.count("?") == 1


def test_the_question_is_built_from_the_roster_not_a_fixed_pair():
    group = [C("1", "Anika Lund", "wife"), C("2", "Anika Roy", "colleague"), C("3", "Anika Holt", "")]
    assert awa.build_question("Anika", group) == \
        "Which Anika do you mean: Anika Lund, your wife, Anika Roy, your colleague, or Anika Holt?"


def test_everyday_word_names_are_not_asked_in_lower_case(monkeypatch):
    people = [C("1", "Will Archer", "friend"), C("2", "Will Tan", "colleague")]

    async def load(_uid):
        return people

    monkeypatch.setattr(awa, "_load_people", load)
    assert _ask("will you remind me at five?", monkeypatch) == ""
    awa.forget_roster()
    assert "Which Will" in _ask("Call Will", monkeypatch)


# -- modes -----------------------------------------------------------------------------------------

def test_the_default_is_shadow_and_shadow_asks_nothing(monkeypatch):
    assert awa.mode() == "shadow"
    assert _run(awa.handle("Tell me about Marisol.", UID, SID)) == ""
    assert not awa.has_pending(UID, SID)


def test_off_reads_nothing(monkeypatch):
    calls = []

    async def load(_uid):
        calls.append(1)
        return PEOPLE

    monkeypatch.setattr(awa, "_load_people", load)
    assert _ask("Tell me about Marisol.", monkeypatch, "off") == "" and calls == []


def test_guests_are_not_asked(monkeypatch):
    monkeypatch.setenv(awa.ENV, "enforce")
    assert _run(awa.handle("Tell me about Marisol.", "guest", SID)) == ""


def test_a_turn_naming_nobody_costs_no_query_after_the_first(monkeypatch):
    calls = []

    async def load(_uid):
        calls.append(1)
        return PEOPLE

    monkeypatch.setattr(awa, "_load_people", load)
    monkeypatch.setenv(awa.ENV, "enforce")
    for t in ("What's the weather?", "Turn off the kitchen light", "Set a timer for ten minutes"):
        assert _run(awa.handle(t, UID, SID)) == ""
    assert len(calls) == 1                                   # one roster read (60 s cache), however many turns


def test_a_failing_roster_read_is_nobody_ambiguous(monkeypatch):
    async def boom(_uid):
        raise RuntimeError("no database")

    monkeypatch.setattr(awa, "_load_people", boom)
    assert _ask("Tell me about Marisol.", monkeypatch) == ""


# -- the short answer ------------------------------------------------------------------------------

@pytest.mark.parametrize("answer,want", [
    ("the sister", "Tell me about Marisol Vance."), ("my sister", "Tell me about Marisol Vance."),
    ("Okafor", "Tell me about Marisol Okafor."), ("the colleague", "Tell me about Marisol Okafor."),
    ("the one from work", "Tell me about Marisol Okafor."), ("the first one", "Tell me about Marisol Okafor."),
    ("second", "Tell me about Marisol Vance."), ("Marisol Vance", "Tell me about Marisol Vance."),
])
def test_the_owners_short_answer_is_folded_into_the_original_request(answer, want, monkeypatch):
    _ask("Tell me about Marisol.", monkeypatch)
    assert awa.has_pending(UID, SID)
    assert awa.resolve_followup(answer, UID, SID) == want
    assert not awa.has_pending(UID, SID)


def test_the_possessive_and_the_rest_of_the_request_survive(monkeypatch):
    _ask("What's Marisol's birthday?", monkeypatch)
    assert awa.resolve_followup("the sister", UID, SID) == "What's Marisol Vance's birthday?"


def test_an_answer_that_settles_nothing_is_dropped_and_never_asked_twice(monkeypatch):
    _ask("Tell me about Marisol.", monkeypatch)
    assert awa.resolve_followup("hmm I'm not sure", UID, SID) is None
    assert not awa.has_pending(UID, SID)


def test_no_loop_while_the_question_is_unanswered(monkeypatch):
    assert _ask("Tell me about Marisol.", monkeypatch)
    assert _ask("Tell me about Marisol.", monkeypatch) == ""           # asked once; the answer is not a new ask


def test_the_pending_question_expires(monkeypatch):
    _ask("Tell me about Marisol.", monkeypatch)
    for p in awa._PENDING.values():
        p.at -= awa.PENDING_TTL_S + 1
    assert not awa.has_pending(UID, SID) and awa.resolve_followup("the sister", UID, SID) is None


def test_the_seam_rewrites_the_answer_for_the_brain(monkeypatch):
    import zoe_flue_client as zc

    _ask("Tell me about Marisol.", monkeypatch)
    assert zc._resolve_clarification("the sister", UID, SID) == "Tell me about Marisol Vance."
    assert zc._resolve_clarification("the sister", UID, SID) == "the sister"      # consumed once
    monkeypatch.setenv(awa.ENV, "off")
    assert zc._resolve_clarification("anything", UID, SID) == "anything"


def test_the_never_ask_and_always_ask_policies_each_fail_one_half(monkeypatch):
    """The instrument: a stub that never asks fails the ambiguous half; one that always asks fails the clear half.
    The real tier passes both (20 of 20 clear, every ambiguous one asked)."""
    def real(text):
        awa._PENDING.clear()                 # each ask is its own request
        return bool(_ask(text, monkeypatch))

    never = lambda text: False          # noqa: E731
    always = lambda text: True          # noqa: E731
    for policy, ambiguous_ok, clear_ok in ((real, True, True), (never, False, True), (always, True, False)):
        asked_ambig = sum(policy(t) for t in AMBIG)
        asked_clear = sum(policy(t) for t in CLEAR)
        assert (asked_ambig == len(AMBIG)) is ambiguous_ok
        assert (asked_clear == 0) is clear_ok
        awa._PENDING.clear()


@pytest.mark.parametrize("answer", ["Not my sister", "no, not the sister", "not the first one", "it isn't the colleague",
                                    "anyone but Okafor", "I don't mean the sister"])
def test_a_refusal_never_chooses_a_person(answer, monkeypatch):
    """A refusal names a role or surname but chooses nobody: the owner's words go on untouched, no rewrite."""
    _ask("Tell me about Marisol.", monkeypatch)
    assert awa.resolve_followup(answer, UID, SID) is None
    assert not awa.has_pending(UID, SID)


def test_the_flag_reader_goes_through_typed_env(monkeypatch):
    import inspect
    assert "os.environ" not in inspect.getsource(awa)
    monkeypatch.setenv(awa.ENV, " Enforce ")
    assert awa.mode() == "enforce"


# -- the stale roster (live person bench, 2026-10-10: P7.a 10/20) ------------------------------------------------------
# The bench seeds the household through the chat ("Save a contact for my colleague Marisol Okafor", then "...my sister
# Marisol Vance"). Those seeding turns are requests; the roster read between them saw ONE Marisol, cached "nobody is
# ambiguous" for 60 s, and the first 12 ambiguous asks went to the brain. The 8 after the cache expired were asked.

def _live_world(monkeypatch, people):
    """A contacts table that CHANGES under the module: ``people`` is the list the fakes read on every call."""
    reads = {"load": 0, "fp": 0}

    async def load(_uid):
        reads["load"] += 1
        return list(people)

    async def fingerprint(_uid):
        reads["fp"] += 1
        return (str(len(people)), "", str(sum(len(c.name) for c in people)))

    monkeypatch.setattr(awa, "_load_people", load)
    monkeypatch.setattr(awa, "_people_fingerprint", fingerprint)
    return reads


def test_a_contact_added_a_moment_ago_is_not_missed_by_the_cache(monkeypatch):
    people = [C("1", "Marisol Okafor", "colleague"), C("3", "Percival Dunmore", "brother")]
    _live_world(monkeypatch, people)
    assert _ask("Save a contact for my sister Marisol Vance.", monkeypatch) == ""      # the seeding turn reads the roster
    assert _ask("Tell me about Marisol.", monkeypatch) == ""                            # one Marisol: nobody is ambiguous
    people.append(C("2", "Marisol Vance", "sister"))                                    # the second Marisol lands
    q = _ask("What's Marisol's birthday?", monkeypatch)                                 # well inside the 60 s TTL
    assert q == "Which Marisol do you mean: Marisol Okafor, your colleague, or Marisol Vance, your sister?"


def test_a_contact_removed_a_moment_ago_is_not_asked_about(monkeypatch):
    people = list(PEOPLE)
    _live_world(monkeypatch, people)
    assert _ask("Tell me about Marisol.", monkeypatch) != ""
    awa._PENDING.clear()
    people[:] = [c for c in people if c.name != "Marisol Vance"]
    assert _ask("Where does Marisol live?", monkeypatch) == ""


def test_an_unchanged_roster_is_read_once_however_many_turns(monkeypatch):
    reads = _live_world(monkeypatch, list(PEOPLE))
    for t in ("What's the weather?", "What time is it?", "Is it raining?"):
        assert _ask(t, monkeypatch) == ""
    assert reads["load"] == 1 and reads["fp"] == 3          # one fingerprint per request turn, one roster read


def test_a_failed_roster_read_is_not_cached(monkeypatch):
    state = {"fail": True}

    async def load(_uid):
        if state["fail"]:
            raise RuntimeError("db down")
        return list(PEOPLE)

    monkeypatch.setattr(awa, "_load_people", load)
    assert _ask("Tell me about Marisol.", monkeypatch) == ""            # unreadable: nobody is ambiguous, this turn only
    state["fail"] = False
    assert _ask("Tell me about Marisol.", monkeypatch) != ""            # the next turn reads again (it was cached as {} for 60 s)


def test_a_failed_fingerprint_serves_a_fresh_cache_not_a_wrong_answer(monkeypatch):
    reads = _live_world(monkeypatch, list(PEOPLE))
    assert _ask("Tell me about Marisol.", monkeypatch) != ""
    awa._PENDING.clear()

    async def boom(_uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(awa, "_people_fingerprint", boom)
    assert _ask("Tell me about Marisol.", monkeypatch) != ""            # the cache is still inside its TTL
    assert reads["load"] == 1


# -- the real queries (SQL text), over an in-memory SQLite that speaks the same dialect for these two statements ------------
# A fingerprint query that raised would be swallowed (the cache would then be served for its TTL) and the stale-roster bug
# would be back with every test above still green - so the statements themselves run here.

def test_the_fingerprint_and_roster_queries_run_and_see_a_new_contact(monkeypatch):
    import contextlib
    import sys
    import types

    import aiosqlite

    async def scenario():
        db = await aiosqlite.connect(":memory:")
        await db.execute("CREATE TABLE people (id TEXT, user_id TEXT, name TEXT, relationship TEXT, deleted INTEGER DEFAULT 0, "
                         "updated_at TEXT NOT NULL DEFAULT '2026-10-10T00:00:00')")
        await db.execute("INSERT INTO people (id, user_id, name, relationship) VALUES ('1', ?, 'Marisol Okafor', 'colleague')", (UID,))
        await db.commit()

        @contextlib.asynccontextmanager
        async def ctx():
            yield db

        fake = types.ModuleType("db_pool")
        fake.get_db_ctx = ctx
        monkeypatch.setitem(sys.modules, "db_pool", fake)
        monkeypatch.undo()                       # keep the fixture's stubs off: the REAL _load_people / _people_fingerprint run
        monkeypatch.setitem(sys.modules, "db_pool", fake)
        monkeypatch.setenv(awa.ENV, "enforce")
        awa.forget_roster()
        first = await awa._people_fingerprint(UID)
        assert await awa.handle("Tell me about Marisol.", UID, "a") == ""        # one Marisol
        await db.execute("INSERT INTO people (id, user_id, name, relationship) VALUES ('2', ?, 'Marisol Vance', 'sister')", (UID,))
        await db.commit()
        assert await awa._people_fingerprint(UID) != first
        q = await awa.handle("What's Marisol's birthday?", UID, "b")
        await db.close()
        return q

    assert _run(scenario()) == "Which Marisol do you mean: Marisol Okafor, your colleague, or Marisol Vance, your sister?"
