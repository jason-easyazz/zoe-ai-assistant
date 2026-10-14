"""A forget whose derived stores could not be cleared is FINISHED later, by itself, without the name ever being stored.

Before this, the entity name was never kept (only salted hashes), so a cascade that lost its pool or hit a locked table was redone
only if the member said the forget again. The durable retry (``memory_forget_cascade.retry_pending``):

  * leaves, for each step that failed, a marker in the forget ledger: the salted hash of a FIXED step label (``portrait``, ``card`` ...),
    never the name, never a hash of the name, never a regex;
  * re-runs ONLY those steps on the next healthy pass, matching with the ledger's own hashes (``memory_forgotten.LedgerPattern``), then
    deletes the markers it satisfied;
  * runs from the nightly digest's pre-read (``memory_digest._redact_forgotten_transcript``) and after any later COMPLETE forget.

Every claim has a break-the-fix control. Names are the lab's invented ones (Dana / Tove), no household data.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import memory_digest
import memory_forget_cascade as cascade
import memory_forgotten
from forgotten_support import (  # noqa: F401 - fixtures
    FRIEND, OTHER, USER, ledger_env, no_offers, open_forgotten_db, svc, use_db,
)
from test_forget_cascade_fail_loud import _FlakyDb, _lost_pool
from test_forget_durable_ledger import _alive, _forget, _seed_derived, _teach

pytestmark = pytest.mark.ci_safe

UW = "user_id = '" + USER + "'"
OW = "user_id = '" + OTHER + "'"


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)


async def _forget_in_ledger(name: str = FRIEND, user: str = USER) -> None:
    """What ``memory_forget_entity`` does before the cascade: the name goes into the (hash-only) ledger."""
    assert await memory_forgotten.add(user, name, actor=user)


def _backend_text(backend) -> str:
    return " ".join(str(v) for row in backend.rows.values() for v in row.values()).lower()


@pytest.mark.asyncio
async def test_a_failed_step_leaves_a_marker_with_no_name_in_it(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        res = await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert res.failed == ["portrait"]
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == ["portrait"]
        markers = [r for r in ledger_env.rows.values() if r["scope"] == memory_forgotten.SCOPE_PENDING]
        assert len(markers) == 1
        # nothing in the ledger table is the name, a regex of it, or a hash of it other than the entity row itself
        assert FRIEND.lower() not in _backend_text(ledger_env)
        assert markers[0]["key_hash"] != memory_forgotten.key_hash(USER, FRIEND)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_retry_finishes_the_failed_step_without_the_name_and_removes_its_marker(monkeypatch, ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        res = await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert res.failed == ["portrait"]
        assert await _alive(db, "user_portraits", UW) == 1             # still naming her

        # from here on the NAME is not available to anything: any use of it would blow up
        def boom(name):
            raise AssertionError("the retry used the name")
        monkeypatch.setattr(cascade, "_name_re", boom)
        out = await cascade.retry_pending(USER, db=db)

        assert out is not None and out.complete and out.portrait == 1
        assert await _alive(db, "user_portraits", UW) == 0
        assert await _alive(db, "user_portraits", OW) == 1             # another member's row is not this member's forget
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []       # the marker went with the work
        assert [r for r in ledger_env.rows.values() if r["scope"] == memory_forgotten.SCOPE_PENDING] == []
        assert await cascade.retry_pending(USER, db=db) is None                            # idempotent: nothing left
    finally:
        await db.close()

@pytest.mark.asyncio
async def test_without_the_marker_the_old_behaviour_is_that_nothing_redoes_it_break_the_fix(monkeypatch, ledger_env):
    """Control: neuter the marker write (the pre-fix cascade) and the same retry finds nothing to do - the portrait stays."""
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()

        async def no_marker(user_id, labels, **kw):
            return False
        monkeypatch.setattr(memory_forgotten, "mark_pending", no_marker)
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert await cascade.retry_pending(USER, db=db) is None
        assert await _alive(db, "user_portraits", UW) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_retry_only_reruns_the_steps_that_failed_a_kept_contact_stays_kept(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 1") == 1           # the people step ran the first time
        # the member then says "keep the contact": the contact is back, the name is still forgotten
        await db.execute("UPDATE people SET deleted = 0 WHERE id = 'p-dana'")
        await db.commit()
        await cascade.retry_pending(USER, db=db)
        assert await _alive(db, "user_portraits", UW) == 0
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 0") == 1           # the people step was not pending: untouched
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_retry_that_fails_again_keeps_the_marker_and_a_later_healthy_pass_finishes(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        again = await cascade.retry_pending(USER, db=_FlakyDb(db, "FROM user_portraits"))
        assert again is not None and again.failed == ["portrait"]
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == ["portrait"]        # still owed
        assert await _alive(db, "user_portraits", UW) == 1
        done = await cascade.retry_pending(USER, db=db)
        assert done is not None and done.complete
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_marker_survives_a_restart_it_is_in_the_table_not_in_memory(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        memory_forgotten.reset_state()                       # a new process: the overlay, the cache and every in-memory view are gone
        cascade.reset_state()
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == ["portrait"]
        out = await cascade.retry_pending(USER, db=db)
        assert out is not None and out.portrait == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_lost_pool_leaves_every_database_step_pending_and_the_retry_clears_them_all(monkeypatch, ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        _lost_pool(monkeypatch, ever_initialised=True)
        res = await cascade.cascade_forget(USER, FRIEND)
        assert "database" in res.failed and not res.complete
        assert "database" in await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS)
        assert await _alive(db, "user_portraits", UW) == 1

        use_db(monkeypatch, db)                              # the pool is back
        out = await cascade.retry_pending(USER)
        assert out is not None and out.complete
        assert await _alive(db, "user_portraits", UW) == 0
        assert await _alive(db, "user_model_cards", UW) == 0
        assert await _alive(db, "open_loops", UW + " AND resolved IS TRUE") == 1
        assert await _alive(db, "proactive_candidates", UW + " AND id = 'c1'") == 0
        assert await _alive(db, "proactive_candidates", UW + " AND id = 'c2'") == 1       # the dentist booking stays
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 1") == 1
        assert await _alive(db, "people", "id = 'p-tove' AND deleted = 0") == 1           # Tove was never forgotten
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NOT NULL") == 1     # the edge to Dana closed
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_edges_only_failure_is_recovered_from_the_contacts_already_removed(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        res = await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "UPDATE person_relationships"))
        assert res.failed == ["edges"]
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 1") == 1
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NULL") == 1         # still open
        out = await cascade.retry_pending(USER, db=db)
        assert out is not None and out.complete and out.edges == 1
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NOT NULL") == 1
    finally:
        await db.close()

@pytest.mark.asyncio
async def test_the_retry_clears_only_what_names_a_forgotten_entity(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await db.execute("UPDATE user_portraits SET portrait_text = 'Loves the garden and the dentist.' WHERE user_id = ?", (USER,))
        await db.commit()
        await _forget_in_ledger()
        await memory_forgotten.mark_pending(USER, ["portrait"])
        assert await cascade.retry_pending(USER, db=db) is not None
        assert await _alive(db, "user_portraits", UW) == 1              # it never named her: it stays
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_later_complete_forget_also_finishes_an_earlier_forgets_pending_steps(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert await _alive(db, "user_portraits", UW) == 1
        await _forget_in_ledger("Tove")
        res = await cascade.cascade_forget(USER, "Tove", db=db)
        assert res.complete
        assert await _alive(db, "user_portraits", UW) == 0              # Dana's leftover went with Tove's healthy pass
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_released_entity_leaves_nothing_to_retry_and_the_marker_goes(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert await memory_forgotten.release(USER, FRIEND + " is coming to dinner") == 1     # the member taught her back, explicitly
        out = await cascade.retry_pending(USER, db=db)
        assert out is not None and out.summary_cleared == 0
        assert await _alive(db, "user_portraits", UW) == 1               # nothing is forgotten, so nothing is cleared
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_without_the_ledger_secret_there_is_no_marker_and_it_says_so(monkeypatch, caplog, ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        monkeypatch.delenv(memory_forgotten.SALT_ENV, raising=False)
        with caplog.at_level(logging.ERROR):
            res = await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert res.failed == ["portrait"]
        assert "FORGET_CASCADE_RETRY_NOT_RECORDED" in caplog.text
        assert FRIEND not in caplog.text
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
        assert await cascade.retry_pending(USER, db=db) is None
        assert ledger_env.rows == dict()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failed_marker_write_keeps_the_marker_in_this_process(monkeypatch, ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        real = ledger_env.upsert

        async def failing(user_id, key_hash, *, scope, **kw):
            if scope == memory_forgotten.SCOPE_PENDING:
                raise RuntimeError("table locked")
            return await real(user_id, key_hash, scope=scope, **kw)
        monkeypatch.setattr(ledger_env, "upsert", failing)
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM user_portraits"))
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == ["portrait"]       # the overlay still carries it
        out = await cascade.retry_pending(USER, db=db)
        assert out is not None and out.portrait == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_restraint_mute_retry_matches_by_the_ledger_not_the_name(monkeypatch, ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        import restraint
        seen = []

        async def spy(user_id, pred, *, strict=False):
            seen.append((user_id, pred("dana surgery"), pred("dentist"), strict))
            return 0
        monkeypatch.setattr(restraint, "erase_matching", spy)
        await memory_forgotten.mark_pending(USER, ["restraint_mutes"])
        out = await cascade.retry_pending(USER, db=db)
        assert out is not None and out.complete
        assert seen == [(USER, True, False, True)]
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
    finally:
        await db.close()


# -- the real caller: memory_forget_entity -> a nightly pass --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_spoken_forget_with_a_failing_store_is_finished_by_the_nightly_pass(svc, monkeypatch, no_offers, ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _teach(svc)
        use_db(monkeypatch, _FlakyDb(db, "FROM user_portraits"))
        reply = await _forget()
        assert "can't say it's forgotten yet" in reply
        assert await _alive(db, "user_portraits", UW) == 1

        use_db(monkeypatch, db)                                # the store is healthy again; the member says nothing
        await memory_digest._redact_forgotten_transcript(USER, db)
        assert await _alive(db, "user_portraits", UW) == 0
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_nightly_pre_read_calls_the_retry_for_that_member(monkeypatch):
    calls = []

    async def spy(user_id, *, db=None):
        calls.append(user_id)
        return None
    monkeypatch.setattr(cascade, "retry_pending", spy)
    await memory_digest._redact_forgotten_transcript(USER, None)
    assert calls == [USER]


# -- the matcher ----------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_ledger_pattern_finds_a_forgotten_word_without_the_word_and_nothing_else(ledger_env):
    await _forget_in_ledger()
    rx = await memory_forgotten.ledger_pattern(USER)
    assert rx is not None
    assert rx.search("Ring Dana back about the weekend") and rx.search("friend DANA, who visits")
    assert rx.search("Ring Dane back") is None and rx.search("Danae visits") is None               # exact words only: no near spelling
    assert rx.sub("[x]", "Ring Dana back, Dana") == "Ring [x] back, [x]"
    assert await memory_forgotten.ledger_pattern(OTHER) is None                                     # another member forgot nothing
    other = memory_forgotten.LedgerPattern(OTHER, await memory_forgotten.active_hashes(USER))
    assert other.search("Dana") is None                                                             # per-member salt


def test_a_marker_key_can_never_equal_a_tokenised_text():
    assert memory_forgotten._PENDING_PREFIX.endswith(chr(124))
    assert chr(124) not in memory_forgotten.normalise_key("pending portrait")
    assert asyncio.run(memory_forgotten.pending_labels(USER, ["portrait"])) == []


async def _clear_pending_probe(ledger_env, label_for_entity: str) -> tuple[bool, bool, int]:
    """Ledger holding one forgotten entity + one pending marker, then ``clear_pending`` called with the step label AND a label that
    spells the entity. Returns (entity row survives, marker survives, rows removed)."""
    assert await memory_forgotten.add(USER, FRIEND, actor=USER)
    assert await memory_forgotten.mark_pending(USER, ["portrait"], actor=USER)
    entity_rows = {k for k, r in ledger_env.rows.items() if r["scope"] != memory_forgotten.SCOPE_PENDING}
    marker_rows = {k for k, r in ledger_env.rows.items() if r["scope"] == memory_forgotten.SCOPE_PENDING}
    assert entity_rows and len(marker_rows) == 1
    removed = await memory_forgotten.clear_pending(USER, ["portrait", label_for_entity])
    return entity_rows <= set(ledger_env.rows), bool(marker_rows & set(ledger_env.rows)), removed


@pytest.mark.asyncio
async def test_clear_pending_can_only_remove_pending_markers(ledger_env):
    """``clear_pending`` is the one ledger delete outside ``release`` that the memory-loss scanner allow-lists
    (test_memory_loss_class._REMOVERS): it must never be able to drop a forgotten entity's shield. Even handed the entity's own
    name as a 'label', the digest it computes is the pending-prefixed one, so the entity (and its near probes) stay."""
    entity_kept, marker_kept, removed = await _clear_pending_probe(ledger_env, FRIEND)
    assert entity_kept and not marker_kept and removed == 1
    assert memory_forgotten.LedgerPattern(USER, await memory_forgotten.active_hashes(USER)).search(FRIEND)


@pytest.mark.asyncio
async def test_clear_pending_control_an_unprefixed_digest_would_drop_the_shield(monkeypatch, ledger_env):
    """Break-the-fix control for the test above: with the pipe prefix removed from the marker digest, handing the entity's name
    as a label DOES delete the entity row - so the test above is the thing that stands between a refactor and a lost shield."""
    monkeypatch.setattr(memory_forgotten, "_pending_digest",
                        lambda user_id, label: memory_forgotten._Hasher(user_id).hash(memory_forgotten.normalise_key(label)))
    entity_kept, _marker_kept, _removed = await _clear_pending_probe(ledger_env, FRIEND)
    assert not entity_kept, "the control did not bite: an unprefixed marker digest must be able to delete an entity row"


@pytest.mark.asyncio
async def test_a_people_failure_leaves_edges_pending_too_and_the_retry_closes_them(ledger_env):
    """Codex P1 (#2022): when ``people`` fails, ``res.person_ids`` is empty so ``edges`` 'succeeds' with nothing to do; only ``people`` was marked,
    the retry ran just ``people`` and the forgotten contact's open relationships stayed open forever. A dependent step is pending with its parent."""
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        res = await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM people"))
        assert "people" in res.failed and "edges" in res.failed          # edges is not 'done' just because it had no ids to close
        assert set(await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS)) == set(["people", "edges"])
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NULL") == 1
        out = await cascade.retry_pending(USER, db=db)
        assert out is not None and out.complete and out.people == 1 and out.edges == 1
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 1") == 1
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NOT NULL") == 1
        assert await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_retry_whose_people_step_fails_again_keeps_edges_pending(ledger_env):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        await cascade.cascade_forget(USER, FRIEND, db=_FlakyDb(db, "FROM people"))
        again = await cascade.retry_pending(USER, db=_FlakyDb(db, "FROM people"))
        assert again is not None and "people" in again.failed and "edges" in again.failed
        assert set(await memory_forgotten.pending_labels(USER, cascade.ALL_LABELS)) == set(["people", "edges"])
        done = await cascade.retry_pending(USER, db=db)
        assert done is not None and done.complete
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NOT NULL") == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_only_people_pending_still_runs_edges_the_marker_set_is_not_trusted(ledger_env):
    """A marker set written by an older build (people alone) must still close the edges: edges always follows a people retry."""
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _forget_in_ledger()
        await memory_forgotten.mark_pending(USER, ["people"])
        out = await cascade.retry_pending(USER, db=db)
        assert out is not None and out.complete
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NOT NULL") == 1
    finally:
        await db.close()
