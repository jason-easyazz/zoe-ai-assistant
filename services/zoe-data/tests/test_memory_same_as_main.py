"""Nothing any writer makes the memory store do has changed against ``main``, and an automatic writer still cannot overwrite or
demote a row a member authored.

``tests/fixtures/memory_as_on_main.json`` is the record of ``main`` (c1a350458), made by running ``tests/memory_scenarios.py``
against an export of that commit under its own test configuration, never by this branch's code. The store is a collection, not
SQL: the scenarios run on a RECORDING stand-in that logs every write call with the FULL metadata it carried (status, authority,
its class and basis, source and origin, visibility, owner, the supersede links), every audit entry, and what each call returned
or raised. The same scenarios run here and must give the same record.

Covered (219 scenarios). A MEMBER: remembering through each entry point's labels (spoken teach, brain tool, fast tier, MCP and
agent tools, Skybridge, the screen's proposal, a curator, the day review, a correction, the note / journal / contact mirrors),
alone and over an earlier fact; correcting as each corrector; approving and rejecting a held row; forgetting and archiving a
row; another member's row; a household row; curated standing; a guest. An AUTOMATIC writer, each over a row the member STATED
and one they CONFIRMED: storing a contradicting fact and the same fact (16 writers), editing, superseding, archiving and
rejecting (11 actors), the night merge both ways, retire-with-quote, the decay sweep, a held candidate, relinking and archiving
by person. And 48 through the REAL entry point (``tests/memory_entry_scenarios.py``): forget a row, a guardian's forget,
correct a row (the correction path ON, as a member's correction runs it), with a twin the same utterance left, accept and reject
a dispute, a trait challenge that flags and one that does not, forget a topic, the fast tier's teach and its supersede,
Skybridge, the agent's tool, the MCP tools (add, edit, forget), the screen's proposal approved and its edit, the day review's
owner fact, person fact and its forget, hold-the-fact, the value, date, pet and row corrections, the provenance fix, forget-it
and forget-the-quoted-turn, "forget that", the brain's store and forget-a-person intents, a curator's add, edit, forget, held
note and an adult's opt-out, the three mirrors and a contact rename, the per-turn extractor, the person and named-relation
extractors, the background save, the nightly conflict pass, the per-turn supersede, expiring stale disputes.

And 21 through the call sites that can write a row a member authored and that only an automatic pass, a model answer or a
database row reaches (``tests/memory_writer_scenarios.py``; the model's reply, the judge's pick and the people rows are
scripted at the seam, the function and the memory service are the real ones): an admin's whole-member forget
(``delete_user``), a parked forget finished, the fast tier's ``review(archive)`` of the old row on a service without
``supersede_by`` (the only state that reaches it), the weekly consolidation (the duplicate merge, the contradiction judge, the
decay sweep) and the nightly digest (its two edits, its stores, the emotional pass), each with the authority wall ACTING and
only WATCHING (``ZOE_MEMORY_AUTHORITY=shadow``), the per-turn digest replacing the member's row on their own verbatim words,
the nightly conflict pass and the claim-key retirement over two rows the member stated, quote-backed retirement on the
judge's pick, the idle person-link resolver, the person extractor's row writer (a new fact, a new value for the same kind by
rule and as the model, a richer fact) and its held beliefs, a named-relations list (store, mint, relink) and its plain fact,
the per-turn extractor's new row and the background save's fallback.

Left out on purpose: access ticks (bookkeeping on every recall). The ten call sites in ``NOT_RUN`` below cannot write a row a
member authored (derivation: the docstring of ``NOT_RUN``).

Every scenario runs with NO model and NO database: an unscripted request to the model fails as an unreachable endpoint does
(``memory_scenarios.Model``), so a record never depends on whether this box's brain is listening, and the stores
``tests/conftest.py`` isolates per test are isolated per scenario too (``memory_scenarios._private_stores``).

Negative controls run (each broke the code, saw the named test fail, restored): the authority wall in ``memory_authority``
answering "may change" for every writer -> ``auto_edit_stated_digest`` and the demotion test; a household row stored
``personal`` -> ``member_household_row``; an edit's audit entry mislabelled -> ``member_correct_the_member``; the note mirror's
writer label changed -> ``entry_mirror_note``; the row correction run with its flag off again ->
``entry_member_correction_row``; the MCP tool's ``memory_add`` source label changed in ``mcp_server._execute_tool`` ->
``entry_member_mcp_add``; the curator's add confidence changed in ``memory_curation._add_locked`` -> ``entry_member_curator_add``;
one entry scenario dropped -> the coverage test (its site neither run nor listed). For the 21 writer scenarios: one control
per scenario, at the call site it drives, named in ``DRIVEN`` below.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory_authority as ma
import memory_scenarios as scenarios

pytestmark = pytest.mark.ci_safe
ON_MAIN = scenarios.unpack(json.loads((Path(__file__).parent / "fixtures" / "memory_as_on_main.json").read_text("utf-8")))
#: what a row must still say for it to be the row its author stored
STANDING = ("status", "authority", "authority_class", "authority_basis", "source", "visibility", "user_id")


def run_with_spy():
    """``(the scenarios' record, {scenario: the call sites it reached})``. A spy on every mutating ``MemoryService`` method only
    NOTES, for each call, the function that MADE it (module, function, method: the direct caller, not everything on the stack -
    a helper's write does not count for the function that called the helper); the call passes through untouched, so the record
    is the same with or without it (the comparison with main below would fail otherwise)."""
    import functools
    import sys

    import memory_service
    import memory_writers

    root = Path(__file__).resolve().parents[1]
    reached: set = set()
    kept = {}
    for name in memory_writers.MUTATING:
        real = kept[name] = getattr(memory_service.MemoryService, name)

        def spy(self, *a, _real=real, _name=name, **k):
            frame = sys._getframe(1)
            try:
                reached.add((Path(frame.f_code.co_filename).resolve().relative_to(root).as_posix(), frame.f_code.co_name, _name))
            except ValueError:
                pass
            return _real(self, *a, **k)

        setattr(memory_service.MemoryService, name, functools.wraps(real)(spy))     # (its signature stays readable: the scenarios ask it)
    try:
        record, by = {}, {}
        for name in sorted(scenarios.scenarios()):
            reached.clear()
            record[name] = scenarios.run(name)
            by[name] = set(reached)
        return record, by
    finally:
        for name, real in kept.items():
            setattr(memory_service.MemoryService, name, real)


_RAN: dict = {}


@pytest.fixture(scope="module")
def here():
    record, _RAN["by"] = run_with_spy()
    _RAN["reached"] = set().union(*_RAN["by"].values())
    return record


def test_every_scenario_gets_the_stores_the_test_configuration_isolates_only_per_test(monkeypatch):
    """Outside pytest (the capture of main's record) no autouse fixture runs: the scenarios module itself points the forget-epoch
    file and the self-review / night-extract directories away from the box's own before any scenario (found 2026-11-23: a
    capture had written an invented id into the live forget-epoch file). Nothing is written here: only where a write WOULD
    go is read. Control run: with ``_private_stores`` not re-pointing ``forget_session._PATH`` this fails."""
    import os

    import forget_session

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    monkeypatch.setattr(forget_session, "_PATH", forget_session._REAL_PATH)
    for var in ("ZOE_SELF_REVIEW_DIR", "ZOE_SELF_REVIEW_IMPROVER_DIR", "ZOE_NIGHT_EXTRACT_DIR"):
        monkeypatch.delenv(var, raising=False)
    try:
        scenarios._private_stores()
        assert not str(forget_session._PATH).startswith(live) and forget_session._EPOCH == {}
        for var in ("ZOE_SELF_REVIEW_DIR", "ZOE_SELF_REVIEW_IMPROVER_DIR", "ZOE_NIGHT_EXTRACT_DIR"):
            assert os.environ[var] and not os.path.abspath(os.environ[var]).startswith(live), var
    finally:
        scenarios._restore()
    assert forget_session._PATH == forget_session._REAL_PATH        # (given back; monkeypatch then restores the fixture's own)


def _seed(record):
    """The member's row: (id, text, metadata) as first stored."""
    first = next(e for e in record if e[0] == "rows" and e[1] == "upsert")
    return first[2], first[3], first[4]


def _row(record, row_id):
    return next(((e[2], e[3]) for e in record if e[0] == "row" and e[1] == row_id), None)


#: the call sites of a mutating ``MemoryService`` method (``tests/memory_writers.py`` ``SITES``, itself checked against the code
#: by ``test_memory_one_writer.py``) that NO scenario runs through its real function: (module, function, method). Exact, so
#: the list cannot grow unseen: a new call site must be run by an entry scenario or be added here.
#: What is left is the ten sites that cannot create, edit, relink, archive, supersede or delete a row a MEMBER authored
#: (re-derived from the code, 2026-11-23): each only ``ingest``s a NEW row under an inferred writer label that the service
#: never ranks as the member's words (``ingest`` never retires or rewrites another row: only a same-source row still pending
#: under the same id is overwritten) - the hindsight candidate (pending), the synthesis insight, the music-taste digest, the
#: profile analysis, the baseline and prompt-packet seeders; or erases only ids its own night write just CREATED (the
#: synthesis undo, ``_undo_night_write``); or records a held candidate under the model's label (``_edge_may_change``); or
#: deletes a harness-shaped id that is refused when it is a registered account (``forget_synthetic_user``).
NOT_RUN = {
    ("hindsight_retain_candidates.py", "create_hindsight_retain_candidate", "ingest"),
    ("memory_digest.py", "_synthesis_pass", "erase_rows"), ("memory_digest.py", "_synthesis_pass", "ingest"),
    ("memory_digest.py", "run_music_taste_digest", "ingest"), ("mempalace_baseline.py", "run_mempalace_baseline", "ingest"),
    ("person_extractor.py", "_edge_may_change", "record_candidate"),
    ("routers/memories.py", "forget_synthetic_user", "delete_user"), ("routers/user_profile.py", "analyze_profile", "ingest"),
    ("zoe_memory_prompt_packet_measure.py", "seed_prompt_packet_measure_memories", "ingest"),
    ("memory_service.py", "_undo_night_write", "erase_rows"),
}

#: the writer scenarios: what each must reach AS THE DIRECT CALLER, and the negative control run for it (the change made at that
#: call site; with it the scenario's comparison with main failed; restored).
DRIVEN = {
    "entry_member_fast_tier_archives_old": ({("expert_dispatch.py", "_ingest_or_supersede", "review")},
                                            "the old row rejected instead of archived"),
    "entry_member_forget_everything": ({("routers/memories.py", "forget_user", "delete_user")},
                                       "the delete attributed to another actor"),
    "entry_member_parked_forget_finished": ({("turn_forget.py", "finish_parked", "erase_rows")},
                                            "the first parked row not erased"),
    "entry_auto_extractor_new_fact": ({("memory_extractor.py", "extract_and_ingest", "ingest")},
                                      "the new row stored pending"),
    "entry_auto_named_relation_new_fact": ({("named_relations.py", "_ingest_user_fact", "ingest")},
                                           "the fact stored as a note"),
    "entry_auto_background_save_fallback": ({("zoe_agent.py", "_background_memory_save", "ingest")},
                                            "the fallback's confidence changed"),
    "entry_auto_held_person_beliefs": ({("person_extractor.py", "_hold_fact_belief", "record_candidate"),
                                        ("person_extractor.py", "_hold_edge_belief", "record_candidate")},
                                       "the held relationship, then the held fact, stored disputed instead of pending (one control each)"),
    "entry_auto_nightly_conflicts_own_words": ({("memory_supersede.py", "nightly_conflict_pass", "supersede_by")},
                                               "the pair superseded the wrong way round"),
    "entry_auto_retire_by_claims": ({("memory_supersede.py", "retire_by_claims", "supersede_by")},
                                    "the retirement attributed to another actor"),
    "entry_auto_pending_person_link": ({("memory_digest.py", "_resolve_pending_person_links", "relink_entity")},
                                       "the fact relinked as a pet"),
    "entry_auto_person_fact_new": ({("person_extractor.py", "_ingest_to_mempalace", "ingest")},
                                   "a person not yet a contact stored as a resolved one"),
    "entry_auto_person_fact_changes_value": ({("person_extractor.py", "_reconcile_same_kind_entity_row", "review"),
                                              ("person_extractor.py", "_reconcile_same_kind_entity_row", "relink_entity")},
                                             "the stale duplicate rejected instead of archived; the relink made as a pet (one control each)"),
    "entry_auto_person_fact_changes_value_model": ({("person_extractor.py", "_reconcile_same_kind_entity_row", "review")},
                                                   "the model's edit attributed to the rule-based reader"),
    "entry_auto_person_fact_extends": ({("person_extractor.py", "_ingest_to_mempalace", "review"),
                                        ("person_extractor.py", "_ingest_to_mempalace", "relink_entity")},
                                       "the supersede attributed to the operator; the relink after it made as a pet (one control each)"),
    "entry_auto_named_relations_owned_list": ({("named_relations.py", "_apply_one", "relink_entity")},
                                              "the relink to the minted contact made as a pet"),
    "entry_auto_quote_retire": ({("memory_retire.py", "decide", "retire_with_quote")},
                                "the retirement's lane recorded as voice"),
    "entry_auto_turn_digest_change_of_mind": ({("memory_digest.py", "run_turn_digest", "review"),
                                               ("memory_digest.py", "run_turn_digest", "ingest")},
                                              "the edit attributed to the nightly digest; the store's confidence changed (one control each)"),
    "entry_auto_night_digest": ({("memory_digest.py", "run_memory_digest", "review"), ("memory_digest.py", "run_memory_digest", "ingest"),
                                 ("memory_digest.py", "_emotional_memory_pass", "ingest")},
                                "the contradiction edit attributed to the operator; the confidence of the held observation, of the plain store and of the\n"
                                "emotional row changed (one control each)"),
    "entry_auto_night_digest_wall_shadow": ({("memory_digest.py", "run_memory_digest", "review")},
                                            "the reconcile edit attributed to the weekly pass"),
    "entry_auto_weekly_consolidation": ({("memory_digest.py", "_merge_near_duplicates", "archive_duplicate"),
                                         ("memory_digest.py", "_resolve_contradictions", "review"),
                                         ("memory_digest.py", "run_weekly_consolidation", "sweep_soft_archive")},
                                        "the contradiction pass, then the decay sweep, attributed to the operator (one control each)"),
    "entry_auto_weekly_consolidation_wall_shadow": ({("memory_digest.py", "_merge_near_duplicates", "archive_duplicate"),
                                                     ("memory_digest.py", "_resolve_contradictions", "review"),
                                                     ("memory_digest.py", "run_weekly_consolidation", "sweep_soft_archive")},
                                                    "the duplicate merge's actor label changed"),
}


def test_the_fixture_covers_every_scenario(here):
    """Coverage is read from the code and from what RAN, not counted by name prefix. Every mutating method of the service is
    reached by a scenario. Every call site of one, as the code holds them, is either run through its real function by an
    entry scenario (the site's function is the DIRECT caller of the service method) or named in ``NOT_RUN``; nothing is in both.
    Controls run: a call site added to ``memory_writers.SITES`` (``("memory_curation.py", "sneak", "ingest")``: what
    ``test_memory_one_writer`` demands for any new call in the code) fails this as "not run, not listed"; with
    ``person_extractor._ingest_to_mempalace``'s own update branch made unreachable this fails the same way for its review and
    relink - and passed while the spy noted every function on the stack, as it used to (the reconcile helper's edit and relink,
    made inside ``_ingest_to_mempalace``, counted for it too)."""
    import memory_writers

    assert set(ON_MAIN) == set(scenarios.scenarios()) and len(ON_MAIN) >= 219
    reached = _RAN["reached"]
    assert set(memory_writers.MUTATING) == {method for _m, _f, method in reached}
    sites = {(module, fn.split(".")[-1], method) for module, fn, method in memory_writers.SITES}
    run = {site for site in sites if site in reached}
    assert len(run) >= 79
    assert sites - run == NOT_RUN, {"not run, not listed": sorted(sites - run - NOT_RUN), "listed, but run or gone": sorted(NOT_RUN - (sites - run))}


def test_each_writer_scenario_makes_its_write_through_the_call_site_it_names(here):
    """A writer scenario is there for ONE call site: it must reach it (as the direct caller), whatever else reaches the same
    site, and the service must have been asked to write (a ``rows`` entry after the seeded ones, or a delete). Control run: the
    quote-retire scenario with its judge answering 0 (no pick) no longer reaches ``memory_retire.decide``'s write and fails here."""
    import memory_writer_scenarios

    assert set(DRIVEN) == set(memory_writer_scenarios.scenarios())
    for name, (sites, control) in DRIVEN.items():
        assert sites <= _RAN["by"][name], (name, sorted(sites - _RAN["by"][name]))
        assert control and any(e[0] == "rows" for e in here[name]), name


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_writer_makes_the_store_do_exactly_what_it_did_on_main(here, name):
    assert scenarios.writes_only(here[name]) == ON_MAIN[name]


def test_the_record_holds_the_full_metadata_of_each_write(here):
    """The comparison can see a demotion: a write's record carries the row's whole metadata, and a supersede shows as the old
    row's status changing with a link to its successor."""
    _id, text, meta = _seed(here["member_remember_fast_tier"])
    assert text == scenarios.OLD and set(STANDING) <= set(meta)
    assert (meta["status"], meta["authority"], meta["source"], meta["visibility"], meta["user_id"]) == (
        "approved", ma.USER_STATED, "voice_fact", "personal", scenarios.ANN)
    record = here["member_correct_the_member"]
    old_id = _seed(record)[0]
    assert _row(record, old_id)[1]["status"] == "superseded" and _row(record, old_id)[1]["superseded_by_id"]
    assert any(e[0] == "audit" and e[1] == "edit" for e in record)
    assert here["member_household_row"][0][4]["visibility"] == "family"


def test_a_scenario_run_on_its_own_gives_the_record_it_gives_among_the_others(here):
    """No record depends on what ran before it (a forget in an earlier scenario shields the process against the same words).
    EVERY scenario is run alone and compared. Control runs: with ``memory_scenarios.run`` not calling ``_fresh`` this fails; with
    ``_fresh`` not clearing ``memory_tombstones`` (the brain's "forget Teddy" then blocks every later row naming Teddy, on main
    and here alike, so the fixture comparison alone cannot see it) this fails."""
    for name in sorted(here):
        assert scenarios.run(name) == here[name], name
    for name in ("member_remember_skybridge", "member_remember_mcp_tool", "entry_member_agent_tool_add", "member_forget_row",
                 "entry_auto_turn_extractor", "entry_member_proposal_approved", "entry_member_curator_add",
                 "entry_member_forget_everything", "entry_auto_night_digest", "entry_auto_weekly_consolidation"):
        assert any(e[0] == "rows" for e in here[name]), name


def _below_the_member(writer: str) -> bool:
    """Does ``memory_authority`` rank this writer, with nothing from the member behind it, below the member's own words?"""
    return ma.RANK[ma.writer_class(writer)] < ma.USER_RANK


def test_an_automatic_writer_cannot_overwrite_or_demote_a_row_a_member_authored(here):
    """The owner's rule (user-stated > confirmed > inferred; an inference never supersedes), as ``main`` keeps it: for every
    automatic writer that ranks below the member's own words, after it stored a contradicting fact, the same fact, an edit, a
    supersede, an archive or a reject, the member's row is the row the member stored. Control run: with the authority wall
    answering "may change" for every writer, the digest's edit supersedes the member's row and this fails."""
    checked = 0
    ingest = ("auto_ingest_over_stated_", "auto_ingest_over_confirmed_", "auto_ingest_same_")
    acts = ("auto_edit_stated_", "auto_edit_confirmed_", "auto_supersede_stated_", "auto_archive_stated_", "auto_reject_stated_")
    for name, record in here.items():
        prefix = next((p for p in ingest + acts if name.startswith(p)), None)
        if prefix is None:
            continue
        label = name[len(prefix):]
        kw = scenarios.AUTOMATIC_INGEST.get(label, {}) if prefix in ingest else {}
        writer = (kw.get("origin") or kw["source"]) if prefix in ingest else label
        if not _below_the_member(writer):
            continue
        row_id, text, meta = _seed(record)
        after = _row(record, row_id)
        assert after is not None, name
        assert after[0] == text and {k: after[1].get(k) for k in STANDING} == {k: meta.get(k) for k in STANDING}, name
        checked += 1
    assert checked >= 60
    for name in ("auto_merge_member_row_into_inferred", "auto_sweep", "auto_candidate", "entry_auto_nightly_conflicts",
                 "entry_auto_turn_supersede", "entry_auto_person_own_fact", "entry_auto_disputes_expire",
                 "entry_auto_night_digest", "entry_auto_person_fact_changes_value_model"):
        row_id, text, meta = _seed(here[name])
        after = _row(here[name], row_id)
        assert after[0] == text and after[1]["status"] == meta["status"] and after[1]["authority"] == meta["authority"], name


def _status_of(record, text) -> list:
    return sorted(e[3]["status"] for e in record if e[0] == "row" and e[2] == text)


def test_the_wall_is_what_stops_the_night_passes_from_changing_what_a_member_said(here):
    """The nightly digest, the weekly contradiction pass and the decay sweep all TARGET rows a member stated; on ``main`` it is
    the authority wall that refuses them. Stated both ways, from the real passes: with the wall acting the member's rows stand
    (the digest's fact is held as a disputed candidate for them); with the wall only watching (``ZOE_MEMORY_AUTHORITY=shadow``)
    the same passes supersede and archive them. The duplicate merge needs no wall: an identical copy of the member's own row
    is archived either way and the other copy stays. Control run: the shadow scenarios run with the wall acting give the
    acting record and this fails."""
    acting, watching = here["entry_auto_night_digest"], here["entry_auto_night_digest_wall_shadow"]
    assert _status_of(acting, "User lives in Perth.") == ["approved"] and _status_of(acting, "User lives in Hobart.") == ["disputed"]
    assert _status_of(watching, "User lives in Perth.") == ["superseded"] and _status_of(watching, "User lives in Hobart.") == ["approved"]
    # (the digest's richer wording of the dog's row: refused as an edit, it is stored beside the member's row; watching, it replaces it)
    assert _status_of(acting, "User's dog is called Teddy.") == ["approved"] and _status_of(watching, "User's dog is called Teddy.") == ["superseded"]
    for record in (acting, watching):       # the feeling the member stated is kept either way, as the digest's own row
        assert [e[3]["source"] for e in record if e[0] == "row" and e[3].get("memory_type") == "emotional_moment"] == ["digest"]
    acting, watching = here["entry_auto_weekly_consolidation"], here["entry_auto_weekly_consolidation_wall_shadow"]
    copy = scenarios.OLD.rstrip(".") + " "
    for record in (acting, watching):
        assert _status_of(record, scenarios.OLD) == ["archived"]                            # the identical duplicate
        assert _status_of(record, "User was thinking about a new bike.") == ["archived"]    # an old inferred row: swept
    assert _status_of(acting, copy) == ["approved"] and _status_of(acting, scenarios.OTHER) == ["approved"]
    assert _status_of(acting, scenarios.NEW) == ["approved", "disputed"]      # the member's row, and the pass's refused edit held
    assert _status_of(watching, copy) == ["superseded"] and _status_of(watching, scenarios.OTHER) == ["archived"]


def test_a_members_own_later_words_are_what_lets_an_automatic_pass_retire_their_row(here):
    """Where ``main`` lets an automatic pass through the wall, it is on the MEMBER'S word: their own verbatim turn (the
    per-turn digest), their own later row (the nightly conflict pass, the claim-key retirement), their own sentence that a
    thing ended (quote retirement), a new value read by rule from their turn (the person extractor). The same person-fact
    change attributed to the MODEL half of the extractor is refused and held. Each first row here is the member's. Control
    run: with the verbatim-span promotion in ``memory_authority`` switched off the per-turn digest's edit is refused and this
    fails."""
    for name, old, status in (("entry_auto_turn_digest_change_of_mind", scenarios.OLD, "superseded"),
                              ("entry_auto_nightly_conflicts_own_words", "User plays the cello.", "superseded"),
                              ("entry_auto_retire_by_claims", "User lives in Perth.", "superseded"),
                              ("entry_auto_quote_retire", "User plays the cello.", "superseded"),
                              ("entry_auto_person_fact_changes_value", "Kip: March 15", "superseded"),
                              ("entry_auto_person_fact_extends", "Kip's dog is called Teddy.", "superseded"),
                              ("entry_auto_person_fact_changes_value_model", "Kip: March 15", "approved")):
        row_id, text, meta = _seed(here[name])
        assert text == old and meta["authority"] == ma.USER_STATED and _row(here[name], row_id)[1]["status"] == status, name
    blocked = here["entry_auto_person_fact_changes_value_model"]
    assert _status_of(blocked, "Kip: March 25") == ["disputed"] and _status_of(blocked, "Kip: 15th of March") == ["approved"]
    forgot = here["entry_member_forget_everything"]
    assert [e[3]["user_id"] for e in forgot if e[0] == "row"] == [scenarios.BEN]      # every row of the member, nobody else's


def test_the_writers_main_lets_change_a_members_row_are_exactly_these(here):
    """What ``main`` allows, stated so a change in either direction shows: a writer that is the member's own turn read by rule
    (``chat_regex``, ``conversation``), the operator and the service's own ``system`` actor edit, archive and reject; the
    member's own quoted retirement supersedes. Nothing else that is automatic changes the member's row."""
    changed = set()
    for name, record in here.items():
        if not name.startswith(("auto_", "entry_auto_")):
            continue
        row_id, text, meta = _seed(record)
        after = _row(record, row_id)
        if after is None or after[0] != text or any(after[1].get(k) != meta.get(k) for k in ("status", "authority", "entity_type", "entity_id")):
            changed.add(name)
    by_rule = {f"auto_{what}_{actor}" for actor in ("chat_regex", "conversation", "operator", "system")
               for what in ("edit_stated", "edit_confirmed", "archive_stated", "reject_stated")}
    # (the contact rename archives the stale "Person in contacts" mirror row it wrote itself and writes the new one: a mirror
    # of the contact, not the member's own words)
    # (the writer scenarios: the duplicate merge, both ways of the wall; the passes the member's own later words let through;
    # the nightly digest only with the wall watching; the link resolver, which changes only which contact the row is about)
    assert changed == by_rule | {"auto_retire_with_quote", "auto_archive_entity", "entry_auto_turn_extractor",
                                 "entry_auto_named_relation_fact", "entry_auto_background_save", "entry_auto_contact_rename_mirror",
                                 "entry_auto_weekly_consolidation", "entry_auto_weekly_consolidation_wall_shadow",
                                 "entry_auto_night_digest_wall_shadow", "entry_auto_turn_digest_change_of_mind",
                                 "entry_auto_nightly_conflicts_own_words", "entry_auto_retire_by_claims", "entry_auto_quote_retire",
                                 "entry_auto_person_fact_changes_value", "entry_auto_person_fact_extends",
                                 "entry_auto_pending_person_link"}
