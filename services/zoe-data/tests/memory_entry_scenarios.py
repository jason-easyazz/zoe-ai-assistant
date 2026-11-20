"""The memory scenarios that go through a REAL entry point (``tests/memory_scenarios.py`` runs them with the rest).

Each drives the function a member's request or an automatic pass really calls, on the same recording collection, so the record
shows what that caller makes the store do with its own labels. Rows a MEMBER authored are stored first.
"""
from __future__ import annotations

from memory_scenarios import ANN, BEN, NEW, OLD, OTHER, World


async def _entry(w: World, awaitable):
    try:
        out = await awaitable
    except Exception as exc:  # noqa: BLE001 - what the entry point raised is part of the record
        w.log.append(["entry-raised", type(exc).__name__, str(exc)[:120]])
        return None
    w.log.append(["entry-returned", w._brief(out) if not isinstance(out, (dict, list, tuple, str, int, float, bool, type(None))) else out])
    return out


# ── a member acting ──────────────────────────────────────────────────────────────────────────────────────────────────────────
async def _space_forget_row(w):
    import memory_space

    row = await w.said(OLD)
    await w.said(OTHER)
    await _entry(w, memory_space.forget_row(ANN, row.id, svc=w.svc))


async def _space_guardian_forget(w):
    import memory_space

    row = await w.said(OLD, user=BEN)
    await _entry(w, memory_space.guardian_forget(BEN, row.id, actor=ANN, svc=w.svc))


async def _space_correct_row(w):
    import memory_space

    row = await w.said(OLD)
    await _entry(w, memory_space.correct_row(ANN, row.id, NEW, utterance="no, my dog is named Rex", svc=w.svc))


async def _correction_row(w):
    import correction_apply

    row = await w.said(OLD)
    await _entry(w, correction_apply.apply_row_correction(ANN, row.id, NEW, utterance="no, my dog is named Rex", svc=w.svc))


def _dispute(accept):
    async def run(w):
        import memory_disputes

        await w.said(OLD)
        held = await w.call("ingest", NEW, user_id=ANN, source="brain_tool", confidence=0.8)
        if held is not None:
            await _entry(w, memory_disputes.resolve(ANN, held.id, accept=accept, svc=w.svc))
    return run


async def _trait_challenge(w):
    import trait_challenge

    await w.call("ingest", OTHER, user_id=ANN, source="synthesis", memory_type="insight", confidence=0.6)
    await _entry(w, trait_challenge.apply_challenge(ANN, "that's not true, I don't like quiet mornings", svc=w.svc))


async def _forget_topic(w):
    import forget_topic

    await w.said(OLD)
    await w.said(OTHER)
    await _entry(w, forget_topic._forget(w.svc, ANN, "dog"))


async def _fast_tier_teach(w):
    import expert_dispatch

    await w.said(OLD)
    await _entry(w, expert_dispatch._ingest_or_supersede(
        w.svc, NEW, user_id=ANN, source="voice_fact", session_id=None, user_turn_id="turn0002", memory_type="fact",
        confidence=0.9, tags=["explicit"], source_excerpt="my dog is named Rex", **w.who({}, ANN)))


async def _skybridge_fact(w):
    import skybridge_service

    await _entry(w, skybridge_service._store_skybridge_memory_fact(OLD, user_id=ANN))


async def _agent_tool_add(w):
    import zoe_agent

    await _entry(w, zoe_agent._mempalace_add(OLD, ANN))


# ── mirrors of a change another area already made ───────────────────────────────────────────────────────────────────────────
async def _note_mirror(w):
    from routers import notes

    await _entry(w, notes._store_note_memory(None, ANN, {"id": "note0001", "title": "Bins", "content": "The bins go out on Tuesday."}, "created"))


async def _journal_mirror(w):
    from routers import journal

    await _entry(w, journal._store_journal_memory(None, ANN, {"id": "jrn0001", "title": "Today", "content": "A quiet day at home."}, "created"))


async def _person_mirror(w):
    from routers import people

    person = {"id": "per0001", "name": "Kip", "relationship": "brother", "notes": "likes kites"}
    await _entry(w, people._store_person_memory(None, ANN, person, "created"))
    await _entry(w, people._archive_person_mempalace("per0001", ANN))


# ── automatic writers, over a row the member authored ───────────────────────────────────────────────────────────────────────
async def _turn_extractor(w):
    import memory_extractor

    await w.said(OLD)
    await _entry(w, memory_extractor.extract_and_ingest("my dog is named Rex", "", user_id=ANN))


async def _person_own_fact(w):
    import person_extractor

    await w.said(OLD)
    await _entry(w, person_extractor._ingest_own_fact(NEW, ANN, source="conversation", origin="person_extractor_llm",
                                                      source_excerpt="Kip said the dog is named Rex"))


async def _named_relation_fact(w):
    import named_relations

    await w.said(OLD)
    await _entry(w, named_relations._ingest_user_fact(NEW, ANN, "conversation", None, "my dog is named Rex"))


async def _background_save(w):
    import zoe_agent

    await w.said(OLD)
    await _entry(w, zoe_agent._background_memory_save("my dog is named Rex", "Noted.", ANN))


async def _nightly_conflicts(w):
    import memory_supersede

    await w.said(OLD)
    await w.call("ingest", NEW, user_id=ANN, source="digest", confidence=0.8)
    await _entry(w, memory_supersede.nightly_conflict_pass(w.svc, ANN))


async def _turn_supersede(w):
    import memory_supersede

    await w.said(OLD)
    new = await w.call("ingest", "User's dog is named Rex now, not Teddy.", user_id=ANN, source="turn_digest", confidence=0.8,
                       source_excerpt="Kip thinks the dog is named Rex now, not Teddy")
    if new is not None:
        await _entry(w, memory_supersede.supersede_for_turn(w.svc, ANN, "now, not", [new]))


async def _disputes_expire(w):
    import memory_disputes

    await w.said(OLD)
    await w.call("ingest", NEW, user_id=ANN, source="brain_tool", confidence=0.8)
    await _entry(w, memory_disputes.expire_stale(w.svc, ANN, now=4102444800.0))


def scenarios() -> dict:
    return {
        "entry_member_forget_row": _space_forget_row, "entry_member_guardian_forget": _space_guardian_forget,
        "entry_member_correct_row": _space_correct_row, "entry_member_correction_row": _correction_row,
        "entry_member_dispute_accept": _dispute(True), "entry_member_dispute_reject": _dispute(False),
        "entry_member_trait_challenge": _trait_challenge, "entry_member_forget_topic": _forget_topic,
        "entry_member_fast_tier_teach": _fast_tier_teach, "entry_member_skybridge_fact": _skybridge_fact,
        "entry_member_agent_tool_add": _agent_tool_add,
        "entry_mirror_note": _note_mirror, "entry_mirror_journal": _journal_mirror, "entry_mirror_person": _person_mirror,
        "entry_auto_turn_extractor": _turn_extractor, "entry_auto_person_own_fact": _person_own_fact,
        "entry_auto_named_relation_fact": _named_relation_fact, "entry_auto_background_save": _background_save,
        "entry_auto_nightly_conflicts": _nightly_conflicts, "entry_auto_turn_supersede": _turn_supersede,
        "entry_auto_disputes_expire": _disputes_expire,
    }
