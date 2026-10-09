"""Forgetting that stays forgotten (audit P2.2, ZMB cell F3): the durable ``memory_forgotten`` ledger end to end.

The defect, measured by the Zoe Memory Bench lab (cell F3): six minutes after "forget Dana", once the in-process
300 s tombstone has expired, the nightly digest re-reads the day's ``chat_messages`` (which still hold the
forgotten turns) and the name comes back. The F3 inputs are copied from ``scripts/perf/zmb/scenarios/forgetting.json``
(seed fact, forget, +360 s on the tombstone clock, the digest writer over the same transcript line).

Real code under test: ``MemoryService.ingest`` / ``review`` over a where-honouring in-memory collection, the REAL
``memory_forget_entity`` intent handler, the REAL nightly digest (``run_memory_digest``: only the model calls are
scripted), the REAL transcript loader over a fake database, the REAL forget cascade over in-memory SQLite (the people
graph + the derived stores + the real 0038 table). No network, no model, no live store (``ci_safe``).

Every claim has its break-the-fix control: ledger lookups disabled -> the name comes back, cascade skipped -> the
summary keeps the name, transcript filter off -> the loader returns the forgotten turn.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

import intent_router
import memory_digest as md
import memory_forgotten as mf
import memory_forget_cascade
import memory_tombstones
from forgotten_support import (  # noqa: F401 - fixtures + the F3 inputs
    FRIEND, HOME, LIMA, OTHER, PROPOSED, RETEACH_FACT, SEED_FACT, TRANSCRIPT, USER, TombstoneClock,
    ledger_env, no_offers, open_forgotten_db, rows_naming, svc, table_dump, use_db,
)

pytestmark = pytest.mark.ci_safe


# ── helpers ──────────────────────────────────────────────────────────────────

async def _forget(name: str = FRIEND, user: str = USER):
    return await intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": name}), user)


async def _teach(svc, text: str = SEED_FACT, *, user: str = USER, source: str = "voice_fact", **kw):
    return await svc.ingest(text, user_id=user, source=source, confidence=0.9, **kw)


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _TranscriptDb:
    """The nightly digest's ``chat_messages`` read: one ``(content,)`` row per user turn."""

    def __init__(self, turns):
        self.turns = turns

    async def execute(self, sql, params=()):
        return _Cur([(t,) for t in self.turns])


def _script_digest(monkeypatch, proposes):
    """The real digest with only the model calls scripted (the extraction returns ``proposes``; the
    contradiction judge says yes, as the incident's did; the emotional pass writes nothing)."""
    stub = types.ModuleType("zoe_agent")

    async def no_blob(*_a, **_k):
        return ""
    stub._mempalace_load_user_facts = no_blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)

    async def extract(_text):
        return [{"fact": f, "type": "profile"} for f in proposes]

    async def contradiction(*_a, **_k):
        return True

    async def no_emotions(*_a, **_k):
        return 0
    monkeypatch.setattr(md, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(md, "_is_contradiction", contradiction)
    monkeypatch.setattr(md, "_emotional_memory_pass", no_emotions)


async def _nightly(monkeypatch, turns=(TRANSCRIPT,), proposes=(PROPOSED,), user: str = USER):
    _script_digest(monkeypatch, list(proposes))
    return await md.run_memory_digest(user, _TranscriptDb(list(turns)))


# ── F3 itself ────────────────────────────────────────────────────────────────

NIGHTLY_FACT = f"{FRIEND} is visiting at the weekend."   # the digest's own guard drops "User's friend ..." claims


async def _digest_writer(svc, user: str = USER):
    """The lab's F3 write: the digest writer proposes a fact over the transcript line (``ingest`` under the
    writer's label, the turn text as its anchor)."""
    return await svc.ingest(PROPOSED, user_id=user, source="digest", anchor_text=TRANSCRIPT, confidence=0.7,
                            user_turn_id="w-f3-0")


@pytest.mark.asyncio
async def test_f3_six_minutes_after_the_forget_the_digest_does_not_resurrect_the_name(
        svc, monkeypatch, no_offers):
    """ZMB F3, the cell's own events: seed, forget, +360 s on the tombstone clock (300 s TTL gone), the digest
    writer over the same transcript line. The name must not come back."""
    clock = TombstoneClock(monkeypatch)
    await _teach(svc)
    assert rows_naming(svc, FRIEND)                                   # seeded
    reply = await _forget()
    assert "forgotten 1 thing about Dana" in reply
    assert not rows_naming(svc, FRIEND)                               # the sweep archived it
    assert memory_tombstones.matching_tombstone(USER, TRANSCRIPT)     # fast path live right now
    clock.advance(360)
    assert memory_tombstones.matching_tombstone(USER, TRANSCRIPT) is None   # ...and gone after six minutes

    assert await _digest_writer(svc) is None
    assert not rows_naming(svc, FRIEND), "the forgotten name came back after the tombstone expired"


@pytest.mark.asyncio
async def test_f3_control_with_the_ledger_off_the_name_returns(svc, monkeypatch, no_offers):
    """Negative control: take the durable half away (what shipped before) and F3 turns red - the instrument sees
    the resurrection."""
    clock = TombstoneClock(monkeypatch)
    await _teach(svc)
    await _forget()
    clock.advance(360)

    async def never(*_a, **_k):
        return False
    monkeypatch.setattr(mf, "matches", never)

    assert await _digest_writer(svc) is not None
    assert rows_naming(svc, FRIEND), "control: without the ledger lookup the digest writer resurrects the name"


@pytest.mark.asyncio
async def test_f3_control_unconfigured_secret_is_the_old_behaviour(svc, monkeypatch, no_offers):
    clock = TombstoneClock(monkeypatch)
    monkeypatch.delenv(mf.SALT_ENV)
    await _teach(svc)
    await _forget()
    clock.advance(360)
    assert await _digest_writer(svc) is not None
    assert rows_naming(svc, FRIEND)


@pytest.mark.asyncio
async def test_f3_through_the_real_nightly_digest_run(svc, monkeypatch, no_offers):
    """The same incident through ``run_memory_digest`` itself: the real transcript loader, the real fact loop, the
    real contradiction / reconcile / ingest path - only the model calls are scripted."""
    clock = TombstoneClock(monkeypatch)
    keep = await _teach(svc, "User likes quiet mornings in the garden.")
    await _teach(svc)
    await _forget()
    clock.advance(360)

    result = await _nightly(monkeypatch, proposes=(NIGHTLY_FACT,))
    assert not rows_naming(svc, FRIEND), "the nightly digest resurrected the forgotten name"
    assert result.get("new", 0) == 0 and result.get("superseded", 0) == 0
    assert result.get("skipped_reason") == "insufficient_activity"   # the forgotten turn was never read
    assert keep is not None


@pytest.mark.asyncio
async def test_f3_digest_control_with_the_ledger_off_the_digest_resurrects(svc, monkeypatch, no_offers):
    clock = TombstoneClock(monkeypatch)
    await _teach(svc)
    await _forget()
    clock.advance(360)

    async def never(*_a, **_k):
        return False

    async def passthrough(user_id, items, **_kw):
        return list(items), 0
    monkeypatch.setattr(mf, "matches", never)
    monkeypatch.setattr(mf, "keep_unforgotten", passthrough)

    result = await _nightly(monkeypatch, proposes=(NIGHTLY_FACT,))
    assert rows_naming(svc, FRIEND), "control: with the ledger off the real digest run brings the name back"
    assert result["new"] == 1


@pytest.mark.asyncio
async def test_each_wall_alone_holds_the_name_out(svc, monkeypatch, no_offers):
    """Defence in depth: with the loader and the digest's fact filter off, only ``MemoryService.ingest``'s ledger
    wall is left, and it still refuses the model's proposal."""
    clock = TombstoneClock(monkeypatch)
    await _teach(svc)
    await _forget()
    clock.advance(360)

    async def passthrough(user_id, items, reader):
        return list(items)
    monkeypatch.setattr(md, "_skip_forgotten_turns", passthrough)
    result = await _nightly(monkeypatch, proposes=(NIGHTLY_FACT,))
    assert result["extracted"] == 1                                    # the model DID propose the fact
    assert not rows_naming(svc, FRIEND)                                # ...and the ledger refused it at ingest
    assert result.get("new", 0) == 0


@pytest.mark.asyncio
async def test_ledger_survives_a_restart_it_is_not_in_process_state(svc, monkeypatch, no_offers, ledger_env):
    """F15: the ledger is rows in a store, not the tombstone's dict - drop every in-process view and the
    forget still holds."""
    await _teach(svc)
    await _forget()
    memory_tombstones.clear_all()                                      # the tombstone dies with the process
    mf.reset_state()                                                   # so does every cache and the overlay
    assert await mf.matches(USER, TRANSCRIPT)                          # the ledger row is what answers
    assert await svc.ingest(PROPOSED, user_id=USER, source="digest", anchor_text=TRANSCRIPT) is None


# ── the transcript loaders ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_digest_transcript_loader_skips_turns_naming_a_forgotten_entity(monkeypatch):
    await mf.add(USER, FRIEND)
    other_turn = "I picked up some tea and then spent the afternoon sorting the garden shed out"
    db = _TranscriptDb([TRANSCRIPT, other_turn])
    text = await md._load_todays_messages(USER, db)
    assert text == other_turn
    assert FRIEND not in text
    # another user's transcript is untouched
    assert FRIEND in await md._load_todays_messages(OTHER, db)


@pytest.mark.asyncio
async def test_loader_control_filter_off_returns_the_forgotten_turn(monkeypatch):
    await mf.add(USER, FRIEND)

    async def passthrough(user_id, items, reader):
        return list(items)
    monkeypatch.setattr(md, "_skip_forgotten_turns", passthrough)
    assert FRIEND in await md._load_todays_messages(USER, _TranscriptDb([TRANSCRIPT]))


@pytest.mark.asyncio
async def test_turn_digest_does_not_mine_a_turn_naming_a_forgotten_entity(monkeypatch):
    await mf.add(USER, FRIEND)

    class _NoNetwork:
        def __init__(self, *a, **k):
            raise AssertionError("the model must not be called for a forgotten turn")
    monkeypatch.setattr(md.httpx, "AsyncClient", _NoNetwork)
    out = await md.run_turn_digest(USER, TRANSCRIPT, "")
    assert out.get("skipped_reason") == "forgotten_entity" and out["new"] == 0


@pytest.mark.asyncio
async def test_idle_consolidation_skips_forgotten_turns(monkeypatch, svc):
    import contextlib
    import memory_idle_consolidation as idle

    await mf.add(USER, FRIEND)
    seen: dict = {}

    class _Conn:
        async def fetch(self, *_a, **_k):
            return [
                {"role": "user", "content": TRANSCRIPT, "metadata": {"user_id": USER}, "at": 1},
                {"role": "user", "content": "the shed roof needs a new coat of paint soon",
                 "metadata": {"user_id": USER}, "at": 2},
                {"role": "assistant", "content": "noted", "metadata": None, "at": 3},
            ]

        async def execute(self, *_a, **_k):
            seen["watermark"] = True

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Conn()

    async def extract(transcript):
        seen["transcript"] = transcript
        return []
    monkeypatch.setattr(md, "_extract_facts_with_gemma", extract)
    await idle.consolidate_session("s1", USER, since=None, get_ctx=ctx)
    assert seen["transcript"] == "the shed roof needs a new coat of paint soon"
    assert FRIEND not in seen["transcript"] and seen["watermark"]


# ── the ingest chokepoint ────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["digest", "turn_digest", "idle_consolidation", "chat_regex", "voice_regex",
                                    "conversation", "brain_tool", "mcp", "person_extractor_llm"])
async def test_every_non_explicit_source_is_refused_after_the_tombstone_is_gone(svc, monkeypatch, no_offers, source):
    clock = TombstoneClock(monkeypatch)
    await _forget()
    clock.advance(360)
    assert await svc.ingest(PROPOSED, user_id=USER, source=source, anchor_text=TRANSCRIPT) is None
    assert not rows_naming(svc, FRIEND)


@pytest.mark.asyncio
async def test_brain_tool_lost_its_tombstone_exemption(svc, no_offers):
    """A 4B-brain paraphrase right after the forget (the tombstone is live) used to bypass it."""
    await _forget()
    assert await svc.ingest(PROPOSED, user_id=USER, source="brain_tool") is None
    assert not rows_naming(svc, FRIEND)


@pytest.mark.asyncio
async def test_a_fact_mined_from_a_forgotten_turn_is_refused_even_when_it_drops_the_name(svc, no_offers):
    await _forget()
    anonymised = "User has a friend who is visiting at the weekend."
    assert await svc.ingest(anonymised, user_id=USER, source="digest", anchor_text=TRANSCRIPT) is None
    assert await svc.ingest(anonymised, user_id=USER, source="digest",
                            anchor_text="we had a quiet weekend at home") is not None


@pytest.mark.asyncio
async def test_refusals_are_counted_as_forgotten_drops(svc, monkeypatch, no_offers):
    bumps = []
    monkeypatch.setattr(type(svc), "_bump", lambda self, status, source: bumps.append((status, source)))
    await _forget()
    await svc.ingest(PROPOSED, user_id=USER, source="digest", anchor_text=TRANSCRIPT)
    assert ("forgotten_drop", "digest") in bumps or ("tombstone_drop", "digest") in bumps
    assert "forgotten_drop" in svc._REFUSED_STATUSES


@pytest.mark.asyncio
async def test_review_edit_by_a_model_actor_cannot_write_a_forgotten_name(svc, monkeypatch, no_offers):
    """The digest's contradiction pass supersedes via review(edit), which writes a row without ingest."""
    clock = TombstoneClock(monkeypatch)
    keep = await _teach(svc, "User likes quiet mornings in the garden.")
    await _forget()
    clock.advance(360)
    assert await svc.review(keep.id, decision="edit", edits=PROPOSED, actor="digest",
                            anchor_text=TRANSCRIPT) is None
    assert not rows_naming(svc, FRIEND)
    # the person's own edit (their account in the review UI) is theirs to make
    done = await svc.review(keep.id, decision="edit", edits=RETEACH_FACT, actor=USER)
    assert done is not None and rows_naming(svc, FRIEND)


# ── re-teach restores ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_explicit_reteach_by_the_user_restores_the_name(svc, monkeypatch, no_offers):
    clock = TombstoneClock(monkeypatch)
    await _teach(svc)
    await _forget()
    clock.advance(360)
    assert await svc.ingest(PROPOSED, user_id=USER, source="digest", anchor_text=TRANSCRIPT) is None

    ref = await svc.ingest(RETEACH_FACT, user_id=USER, source="voice_fact", confidence=0.9)
    assert ref is not None and ref.metadata["status"] == "approved"
    assert rows_naming(svc, FRIEND) == [RETEACH_FACT]
    assert not await mf.matches(USER, TRANSCRIPT), "the ledger entry is released once the re-teach is stored"
    # and the name is now ordinary again for the automatic writers
    assert await svc.ingest(PROPOSED, user_id=USER, source="digest", anchor_text=TRANSCRIPT) is not None


@pytest.mark.asyncio
async def test_the_brain_acting_on_an_explicit_remember_turn_restores_the_name(svc, no_offers):
    await _forget()
    ref = await svc.ingest(RETEACH_FACT, user_id=USER, source="brain_tool", origin="explicit_teach",
                           anchor_text=f"remember that {FRIEND} lives in Lisbon")
    assert ref is not None and not await mf.matches(USER, TRANSCRIPT)


@pytest.mark.asyncio
async def test_a_voice_turn_the_speaker_gate_rejected_cannot_re_teach(svc, no_offers):
    await _forget()
    assert await svc.ingest(RETEACH_FACT, user_id=USER, source="voice_fact", speaker_verified=False) is None
    assert await mf.matches(USER, TRANSCRIPT)                          # still forgotten
    assert not rows_naming(svc, FRIEND)
    # unreported (None) is not a rejection: today's lanes report no verdict
    assert await svc.ingest(RETEACH_FACT, user_id=USER, source="voice_fact", speaker_verified=None) is not None


@pytest.mark.asyncio
async def test_a_failed_reteach_keeps_the_shield(svc, no_offers):
    await _forget()
    # PII the scrubber rejects: the store fails, so the shield must stay
    assert await svc.ingest(f"{FRIEND}'s card is 4111 1111 1111 1111", user_id=USER, source="voice_fact") is None
    assert await mf.matches(USER, TRANSCRIPT)


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_different_users_same_name_is_unaffected(svc, monkeypatch, no_offers):
    clock = TombstoneClock(monkeypatch)
    await _teach(svc, user=USER)
    await _teach(svc, user=OTHER)
    await _forget(FRIEND, USER)
    clock.advance(360)
    assert not rows_naming(svc, FRIEND, user=USER)
    assert rows_naming(svc, FRIEND, user=OTHER)                        # the other member's Dana was never touched
    assert await svc.ingest(PROPOSED, user_id=OTHER, source="digest", anchor_text=TRANSCRIPT) is not None
    assert await svc.ingest(PROPOSED, user_id=USER, source="digest", anchor_text=TRANSCRIPT) is None
    kept = await md._load_todays_messages(OTHER, _TranscriptDb([TRANSCRIPT]))
    assert FRIEND in kept


# ── the forget handler ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forget_with_no_matching_rows_still_writes_the_ledger(svc, no_offers, ledger_env):
    """The in-flight race: the forget lands before any row exists. The ledger covers that exit too."""
    reply = await _forget()
    assert "don't have anything saved about Dana" in reply
    assert len([r for r in ledger_env.rows.values() if r["scope"] != mf.SCOPE_NEAR]) == 1   # the near probes are extra rows, never the entry
    assert await mf.matches(USER, "Dana rang")


@pytest.mark.asyncio
async def test_no_name_or_topic_text_reaches_the_ledger_or_the_logs(svc, no_offers, ledger_env, caplog):
    caplog.set_level(logging.DEBUG)
    await _teach(svc)
    await _forget("Dana Whitfield")
    await _forget()
    dump = " ".join(str(v) for r in ledger_env.rows.values() for v in r.values()).lower()
    assert "dana" not in dump and "whitfield" not in dump and "hobart" not in dump
    for rec in caplog.records:
        if rec.name in (mf.logger.name, memory_forget_cascade.logger.name):
            assert "dana" not in rec.getMessage().lower()


@pytest.mark.asyncio
async def test_ledger_down_never_breaks_the_forget(svc, monkeypatch, no_offers):
    async def boom(*_a, **_k):
        raise RuntimeError("ledger exploded")
    monkeypatch.setattr(mf, "add", boom)
    await _teach(svc)
    reply = await _forget()
    assert "forgotten 1 thing" in reply and not rows_naming(svc, FRIEND)
    assert memory_tombstones.matching_tombstone(USER, TRANSCRIPT)       # the fast path still shields


# ── the cascade ──────────────────────────────────────────────────────────────

async def _seed_derived(db):
    ins = db.execute
    await ins("INSERT INTO people (id, user_id, name, deleted, visibility) VALUES ('p-dana', ?, 'Dana Whitfield', 0, 'family')", (USER,))
    await ins("INSERT INTO people (id, user_id, name, deleted, visibility) VALUES ('p-tove', ?, 'Tove', 0, 'family')", (USER,))
    await ins("INSERT INTO people (id, user_id, name, deleted, visibility) VALUES ('p-other', ?, 'Dana', 0, 'family')", (OTHER,))
    for rid, a, b in (("e1", "p-dana", "p-tove"),):
        await ins("INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, "
                  "rel_b_to_a, rel_group, created_at, updated_at, valid_from) VALUES (?,?,?,?, 'friend','Friend','Friend',"
                  "'personal','2026-01-01','2026-01-01','2026-01-01')", (rid, USER, a, b))
    await ins("INSERT INTO user_portraits (user_id, portrait_text) VALUES (?, 'Loves the garden. Close to Dana, who visits often.')", (USER,))
    await ins("INSERT INTO user_portraits (user_id, portrait_text) VALUES (?, 'Dana is a friend of the family.')", (OTHER,))
    await ins("INSERT INTO user_model_cards (user_id, card_json, card_text) VALUES (?, '{\"items\":[\"friend Dana\"]}', 'Friend: Dana')", (USER,))
    await ins("INSERT INTO open_loops (user_id, loop_text, follow_up_hint) VALUES (?, 'Ring Dana back about the weekend', '')", (USER,))
    await ins("INSERT INTO open_loops (user_id, loop_text, follow_up_hint) VALUES (?, 'Book the dentist', '')", (USER,))
    await ins("INSERT INTO open_loops (user_id, loop_text, follow_up_hint) VALUES (?, 'Ring Dana back', '')", (OTHER,))
    await ins("INSERT INTO proactive_candidates (id, user_id, text, hint) VALUES ('c1', ?, 'Dana is visiting', 'ask about Dana')", (USER,))
    await ins("INSERT INTO proactive_candidates (id, user_id, text, hint) VALUES ('c2', ?, 'Dentist booking', '')", (USER,))
    await db.commit()


async def _alive(db, table, where):
    async with db.execute(f"SELECT count(*) FROM {table} WHERE {where}") as cur:
        return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_the_cascade_clears_everything_derived_and_says_so(svc, monkeypatch, no_offers):
    db = await open_forgotten_db()
    try:
        use_db(monkeypatch, db)
        await _seed_derived(db)
        await _teach(svc)
        reply = await _forget()

        assert "forgotten 1 thing about Dana" in reply
        assert "I've also removed them from your contacts and your summary" in reply
        assert "say 'keep the contact' to undo" in reply
        assert await _alive(db, "user_portraits", f"user_id = '{USER}'") == 0
        assert await _alive(db, "user_model_cards", f"user_id = '{USER}'") == 0
        assert await _alive(db, "open_loops", f"user_id = '{USER}' AND resolved IS NOT TRUE") == 1   # only the dentist
        assert await _alive(db, "open_loops", f"user_id = '{USER}' AND resolved IS TRUE") == 1       # archived, kept
        assert await _alive(db, "proactive_candidates", f"user_id = '{USER}'") == 1                   # only the dentist
        # the people row is soft-deleted (history kept) and so are its edges' validity
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 1") == 1
        assert await _alive(db, "people", "id = 'p-tove' AND deleted = 0") == 1
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NOT NULL") == 1
        # another member's Dana, portrait and loops were never touched
        assert await _alive(db, "people", "id = 'p-other' AND deleted = 0") == 1
        assert await _alive(db, "user_portraits", f"user_id = '{OTHER}'") == 1
        assert await _alive(db, "open_loops", f"user_id = '{OTHER}' AND resolved IS NOT TRUE") == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cascade_control_skipped_the_summary_keeps_the_name(svc, monkeypatch, no_offers):
    db = await open_forgotten_db()
    try:
        use_db(monkeypatch, db)
        await _seed_derived(db)
        await _teach(svc)

        async def noop(user_id, name, **_kw):
            return memory_forget_cascade.CascadeResult()
        monkeypatch.setattr(memory_forget_cascade, "cascade_forget", noop)
        reply = await _forget()
        assert "removed them" not in reply and "summary" not in reply
        assert await _alive(db, "user_portraits", f"user_id = '{USER}'") == 1
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 0") == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_spoken_note_names_only_what_was_removed(svc, monkeypatch, no_offers):
    db = await open_forgotten_db()
    try:
        use_db(monkeypatch, db)
        await db.execute("INSERT INTO people (id, user_id, name, deleted, visibility) VALUES ('p1', ?, 'Dana', 0, 'family')", (USER,))
        await db.commit()
        reply = await _forget()
        assert reply.endswith("I've also removed them from your contacts — say 'keep the contact' to undo.")
        assert "summary" not in reply
        await db.execute("INSERT INTO user_portraits (user_id, portrait_text) VALUES (?, 'Dana waves.')", (USER,))
        await db.commit()
        reply = await _forget()          # the contact is already gone: only the summary is left to clear
        assert reply.endswith("I've also cleared them from your summary.")
        assert "contacts" not in reply
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_keep_the_contact_undoes_the_contact_and_nothing_else(svc, monkeypatch, no_offers):
    db = await open_forgotten_db()
    try:
        use_db(monkeypatch, db)
        await _seed_derived(db)
        await _teach(svc)
        await _forget()

        intent = intent_router.detect_intent("keep the contact", log_miss=False)
        assert intent is not None and intent.name == "memory_forget_entity" and intent.slots.get("undo") is True
        reply = await intent_router.execute_intent(intent, USER)
        assert "kept the contact" in reply
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 0") == 1
        assert await _alive(db, "person_relationships", "id = 'e1' AND valid_to IS NULL") == 1
        # what was FORGOTTEN stays forgotten: the memory row, the summary, and the ledger entry
        assert not rows_naming(svc, FRIEND)
        assert await _alive(db, "user_portraits", f"user_id = '{USER}'") == 0
        assert await mf.matches(USER, TRANSCRIPT)
        # a second undo has nothing to restore, and says so honestly
        again = await intent_router.execute_intent(intent, USER)
        assert "no contact I removed" in again
    finally:
        await db.close()


@pytest.mark.parametrize("phrase", ["keep the contact", "Keep that contact.", "please restore the contact",
                                    "bring back their contact"])
def test_keep_the_contact_phrases_route_to_the_undo(phrase):
    got = intent_router.detect_intent(phrase, log_miss=False)
    assert got is not None and got.name == "memory_forget_entity" and got.slots.get("undo") is True


@pytest.mark.parametrize("phrase", ["keep the contact details private", "keep the contact list tidy",
                                    "keep in contact with her", "forget about it"])
def test_keep_the_contact_is_anchored_to_the_whole_utterance(phrase):
    got = intent_router.detect_intent(phrase, log_miss=False)
    assert got is None or not got.slots.get("undo")


@pytest.mark.asyncio
async def test_undo_with_nothing_to_undo_changes_nothing(svc, monkeypatch, no_offers):
    reply = await intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "", "undo": True}), USER)
    assert "no contact I removed" in reply or "couldn't reach" in reply


@pytest.mark.asyncio
async def test_the_cascade_without_a_database_is_a_quiet_noop(svc, no_offers):
    """No pool in this process: the forget itself still completes (the sweep + the ledger), no cascade claim."""
    await _teach(svc)
    reply = await _forget()
    assert "forgotten 1 thing" in reply and "contacts" not in reply and "summary" not in reply


@pytest.mark.asyncio
async def test_the_table_holds_no_text_after_a_full_forget(svc, monkeypatch, no_offers):
    """End to end on the real SQL: seed, forget, cascade, then read EVERY memory_forgotten column."""
    db = await open_forgotten_db()
    try:
        use_db(monkeypatch, db)
        mf.set_backend(mf.PostgresBackend())
        await _seed_derived(db)
        await _teach(svc)
        await _forget("Dana Whitfield")
        await _forget()
        rows = [r for r in await table_dump(db, "memory_forgotten") if r[2] != "near"]
        assert len(rows) == 2
        blob = " ".join(str(v) for r in rows for v in r).lower()
        for needle in ("dana", "whitfield", "hobart", "garden", "friend"):
            assert needle not in blob
        # reconcile: every entry is answerable by hash, never listable by name
        assert await mf.matches(USER, "Dana Whitfield rang") and await mf.matches(USER, "Dana rang")
    finally:
        await db.close()
