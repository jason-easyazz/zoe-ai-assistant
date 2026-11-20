"""Nothing any writer makes the memory store do has changed against ``main``, and an automatic writer still cannot overwrite or
demote a row a member authored.

``tests/fixtures/memory_as_on_main.json`` is the record of ``main`` (9ecea063c), made by running ``tests/memory_scenarios.py``
against an export of that commit under its own test configuration, never by this branch's code. The store is a collection, not
SQL: the scenarios run on a RECORDING stand-in that logs every write call with the FULL metadata it carried (status, authority,
its class and basis, source and origin, visibility, owner, the supersede links), every audit entry, and what each call returned
or raised. The same scenarios run here and must give the same record.

Covered (171 scenarios). A MEMBER: remembering through each entry point's labels (spoken teach, brain tool, fast tier, MCP and
agent tools, Skybridge, the screen's proposal, a curator, the day review, a correction, the note / journal / contact mirrors),
alone and over an earlier fact; correcting as each corrector; approving and rejecting a held row; forgetting and archiving a
row; another member's row; a household row; curated standing; a guest. An AUTOMATIC writer, each over a row the member STATED
and one they CONFIRMED: storing a contradicting fact and the same fact (16 writers), editing, superseding, archiving and
rejecting (11 actors), the night merge both ways, retire-with-quote, the decay sweep, a held candidate, relinking and archiving
by person. And 21 through the REAL entry point (``tests/memory_entry_scenarios.py``): forget a row, a guardian's forget,
correct a row twice over, accept and reject a dispute, a trait challenge, forget a topic, the fast tier's teach, Skybridge, the
agent's tool, the three mirrors, the per-turn extractor, the person and named-relation extractors, the background save, the
nightly conflict pass, the per-turn supersede, expiring stale disputes.

Left out on purpose: access ticks (bookkeeping on every recall); ``delete_user`` (its erasers reach stores this record cannot
isolate one by one; ``tests/test_os_shadow_forget.py`` and the erase suites own it); the model-driven night digest passes are
pinned by their labels at the service, not by running a model.

Negative controls run (each broke the code, saw the named test fail, restored): the authority wall in ``memory_authority``
answering "may change" for every writer -> ``auto_edit_stated_digest`` and the demotion test; a household row stored
``personal`` -> ``member_household_row``; an edit's audit entry mislabelled -> ``member_correct_the_member``; the note mirror's
writer label changed -> ``entry_mirror_note``.
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


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


def _seed(record):
    """The member's row: (id, text, metadata) as first stored."""
    first = next(e for e in record if e[0] == "rows" and e[1] == "upsert")
    return first[2], first[3], first[4]


def _row(record, row_id):
    return next(((e[2], e[3]) for e in record if e[0] == "row" and e[1] == row_id), None)


def test_the_fixture_covers_every_scenario():
    assert set(ON_MAIN) == set(scenarios.scenarios()) and len(ON_MAIN) >= 170
    for prefix, least in (("member_remember_", 26), ("member_correct_", 5), ("auto_ingest_", 48), ("auto_edit_", 22),
                          ("auto_supersede_", 11), ("entry_member_", 11), ("entry_auto_", 7), ("entry_mirror_", 3)):
        assert sum(name.startswith(prefix) for name in ON_MAIN) >= least, prefix


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
    Control run: with ``memory_scenarios.run`` not calling ``_fresh`` this fails."""
    for name in ("member_remember_skybridge", "member_remember_mcp_tool", "entry_member_agent_tool_add", "member_forget_row",
                 "entry_auto_turn_extractor"):
        assert scenarios.run(name) == here[name], name
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
                 "entry_auto_turn_supersede", "entry_auto_person_own_fact", "entry_auto_disputes_expire"):
        row_id, text, meta = _seed(here[name])
        after = _row(here[name], row_id)
        assert after[0] == text and after[1]["status"] == meta["status"] and after[1]["authority"] == meta["authority"], name


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
        if after is None or after[0] != text or after[1].get("status") != meta.get("status") or after[1].get("authority") != meta.get("authority"):
            changed.add(name)
    by_rule = {f"auto_{what}_{actor}" for actor in ("chat_regex", "conversation", "operator", "system")
               for what in ("edit_stated", "edit_confirmed", "archive_stated", "reject_stated")}
    assert changed == by_rule | {"auto_retire_with_quote", "auto_archive_entity", "entry_auto_turn_extractor",
                                 "entry_auto_named_relation_fact", "entry_auto_background_save"}
