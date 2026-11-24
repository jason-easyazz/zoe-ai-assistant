"""The memory scenarios that go through a REAL entry point (``tests/memory_scenarios.py`` runs them with the rest).

Each drives the function a member's request or an automatic pass really calls, on the same recording collection, so the record
shows what that caller makes the store do with its own labels. Rows a MEMBER authored are stored first.
"""
from __future__ import annotations

from memory_scenarios import ANN, BEN, NEW, OLD, OTHER, World, _set, _setenv


async def _entry(w: World, awaitable, keep: bool = True):
    """Run one entry point; what it returned or raised goes in the record (``keep=False``: the caller records the return itself)."""
    try:
        out = await awaitable
    except Exception as exc:  # noqa: BLE001 - what the entry point raised is part of the record
        w.log.append(["entry-raised", type(exc).__name__, str(exc)[:120]])
        return None
    if not keep:
        return out
    if hasattr(out, "status_code") and hasattr(out, "body"):     # an HTTP response object: its status and body, not its address
        w.log.append(["entry-returned", {"status_code": out.status_code, "body": bytes(out.body).decode("utf-8", "replace")}])
        return out
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

    _on("ZOE_CORRECTION_APPLY")         # the correction path ON: off, ``correct_row`` changes nothing and pins nothing
    row = await w.said(OLD)
    await _entry(w, memory_space.correct_row(ANN, row.id, NEW, utterance="no, my dog is named Rex", svc=w.svc))


async def _correction_row(w):
    import correction_apply

    _on("ZOE_CORRECTION_APPLY")         # ON: the review(edit) ``apply_row_correction`` makes is what is pinned
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


# ── more member entry points, each through its REAL function (review round 2) ───────────────────────────────────────────────
def _on(*names):
    """Turn a flag-dark feature ON for this one scenario (undone with the rest when it ends)."""
    for name in names:
        _setenv(name, "1")


async def _space_correct_row_twins(w):
    """A correction of a row the same utterance left a twin of: the twin holding the old value is retired too."""
    import memory_space

    _on("ZOE_CORRECTION_APPLY")
    row = await w.said(OLD, excerpt="my dog is named Teddy", user_turn_id="turn0001")
    await w.said("User has a dog named Teddy.", excerpt="my dog is named Teddy", user_turn_id="turn0001")
    await _entry(w, memory_space.correct_row(ANN, row.id, NEW, utterance="no, my dog is named Rex", svc=w.svc))


def _mcp(tool, args_of):
    async def run(w):
        import mcp_server

        row = await w.said(OLD)
        await _entry(w, mcp_server._execute_tool(None, tool, args_of(row), {"user_id": ANN, "source": "transport_meta"}))
    return run


async def _proposal(w):
    import models
    from routers import memories

    _set(memories, "get_memory_service", lambda: w.svc)
    _set(memories, "require_feature_access", _allowed)
    out = await _entry(w, memories.create_memory_proposal(
        models.MemoryProposalCreate(content=OLD, memory_type="fact", confidence=0.6, source_type="manual"),
        user={"user_id": ANN, "role": "member"}, db=None))
    if isinstance(out, dict) and out.get("id"):
        await _entry(w, memories.review_memory(out["id"], models.MemoryReviewBody(action="approve"),
                                               user={"user_id": ANN, "role": "member"}, db=None))


async def _screen_review_edit(w):
    import models
    from routers import memories

    _set(memories, "get_memory_service", lambda: w.svc)
    _set(memories, "require_feature_access", _allowed)
    row = await w.said(OLD)
    await _entry(w, memories.review_memory(row.id, models.MemoryReviewBody(action="edit", content=NEW),
                                           user={"user_id": ANN, "role": "member"}, db=None))


async def _allowed(*_a, **_k):
    return None


async def _day_review_owner_fact(w):
    import omi_review_apply

    await w.said(OTHER)
    c = {"id": "cand0001", "day_local": "2030-01-02", "recording_ids": ["rec0001"],
         "payload": {"fact": NEW}, "evidence": [{"quote": "my dog is named Rex", "recording_id": "rec0001"}]}
    await _entry(w, omi_review_apply.write_owner_fact(ANN, c, "panel"))


async def _hold_the_fact_update(w):
    import hold_the_fact

    row = await w.said("User's dentist appointment is on Tuesday.")
    held, pushed = hold_the_fact.contradiction("on Tuesday", "on Thursday")
    await _entry(w, hold_the_fact._apply_update(ANN, "sess0001", row, held, pushed, "no, it is on Thursday"))


async def _value_correction(w):
    import value_correction

    await w.said("User's nephew Kip is 6.")
    await _entry(w, value_correction.apply("kip is 7 not 6", ANN, None, svc=w.svc))


def _explained(row, **kw):
    import memory_provenance as mp

    return mp.Explained(seq=1, row_id=row.id, ts=0.0, text=row.text, session_id="sess0001", **kw)


async def _provenance_fix(w):
    import provenance_answers

    _on("ZOE_CORRECTION_APPLY")
    row = await w.said(OLD, excerpt="my dog is named Teddy", user_turn_id="turn0001")
    await w.said("User has a dog named Teddy.", excerpt="my dog is named Teddy", user_turn_id="turn0001")
    await _entry(w, provenance_answers.fix(ANN, "sess0001", "that's wrong, it's Rex", "Rex", _explained(row), svc=w.svc))


async def _provenance_forget(w):
    import provenance_answers

    _on("ZOE_MEMORY_PHYSICAL_ERASE")
    row = await w.said(OLD)
    await w.said(OTHER)
    await _entry(w, provenance_answers.forget_it(ANN, _explained(row), svc=w.svc))


async def _trait_challenge_flags(w):
    import trait_challenge

    await w.call("ingest", "User is a mountain person.", user_id=ANN, source="synthesis", memory_type="insight", confidence=0.6)
    await _entry(w, trait_challenge.apply_challenge(ANN, "Well, I'm not sure I'm a mountain person, but...", svc=w.svc))


async def _fast_tier_supersede(w):
    import expert_dispatch

    await w.said("User's dog is called Teddy.")
    await _entry(w, expert_dispatch._ingest_or_supersede(
        w.svc, "User's dog is called Teddy Bear, a spaniel.", user_id=ANN, source="voice_fact", session_id=None,
        user_turn_id="turn0002", memory_type="fact", confidence=0.9, tags=["explicit"],
        source_excerpt="my dog is called Teddy Bear, a spaniel", **w.who({}, ANN)))


async def _date_correction(w):
    import correction_apply

    await w.said("User's friend Kip's birthday is 7/8/1991.", excerpt="Kip's birthday is 7/8/1991")
    await _entry(w, correction_apply.apply_date_correction(
        "That date is wrong, we put the day before the month here", ANN, ["my friend Kip's birthday is 7/8/1991"], svc=w.svc))


async def _provenance_forget_turn(w):
    """"forget it" after an answer that quoted the member's own words: the rows that utterance produced are rejected."""
    import memory_provenance as mp
    import provenance_answers

    await w.said(OLD, excerpt="my dog is named Teddy")
    await w.said(OTHER)
    exp = mp.Explained(seq=1, row_id="", ts=0.0, quote="my dog is named Teddy", turn_id="turn0001", session_id="sess0001")
    await _entry(w, provenance_answers.forget_it(ANN, exp, svc=w.svc))


async def _forget_last(w):
    """"forget that": the member's previous turn is retracted, and the rows written from it with it."""
    import intent_router
    import turn_forget

    _on("ZOE_MEMORY_PHYSICAL_ERASE")
    turn_forget.note_user_turn(ANN, "my dog is named Teddy")
    await w.said(OLD, excerpt="my dog is named Teddy")
    await w.said(OTHER)
    turn_forget.note_user_turn(ANN, "forget that")
    await _entry(w, intent_router._execute_forget_last(ANN))


async def _day_review_person_fact(w):
    import contextlib
    import sqlite3

    import db_compat
    import omi_review_apply

    db = _Sqlite()
    db.conn.row_factory = sqlite3.Row
    db.conn.execute("CREATE TABLE people (id TEXT, user_id TEXT, name TEXT, birthday TEXT, relationship TEXT, visibility TEXT, "
                    "deleted INTEGER)")
    db.conn.execute("INSERT INTO people VALUES ('per0001', ?, 'Kip', NULL, 'friend', 'personal', 0)", (ANN,))

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    _set(db_compat, "get_compat_db", fake_db)
    await w.said(OTHER)
    c = {"id": "cand0002", "day_local": "2030-01-02", "recording_ids": ["rec0001"],
         "payload": {"person_id": "per0001", "fact": "likes kites", "segment_ids": ["seg0001"]},
         "evidence": [{"quote": "Kip really likes kites", "recording_id": "rec0001"}]}
    return await _entry(w, omi_review_apply.write_person_fact(ANN, c, "panel"))


async def _day_review_unwrite(w):
    """The member forgets what an approved day-review fact wrote: its memory row is hard-erased."""
    import omi_review_apply

    _on("ZOE_MEMORY_PHYSICAL_ERASE")
    ref = await _day_review_person_fact(w)
    if isinstance(ref, str) and ref.startswith("memory:"):
        await _entry(w, omi_review_apply.unwrite(ANN, [{"kind": "person_fact", "written_ref": ref, "id": "cand0002"}]))


async def _contact_rename_mirror(w):
    """A contact renamed: the stale contact mirror row is archived and the new one written."""
    import contacts_conversation
    from routers import people

    await _entry(w, people._store_person_memory(None, ANN, {"id": "per0001", "name": "Kip", "relationship": "brother",
                                                             "notes": None}, "created"))
    await _entry(w, contacts_conversation.refresh_person_mirror(ANN, "per0001", "Kipper", "brother"))


async def _pet_correction(w):
    """"Womble is their dog": a row that called Womble one of the children is rewritten, and the pet fact is stored."""
    import correction_apply

    await w.said("User has two children: Kip and Womble.", excerpt="I have two children, Kip and Womble")
    await _entry(w, correction_apply.apply_pet_correction("Womble is their dog", ANN, svc=w.svc, db=None))


def _brain_intent(name, slots):
    async def run(w):
        import intent_router

        await w.said(OLD)
        await w.said(OTHER)
        await _entry(w, intent_router.execute_intent(intent_router.Intent(name, dict(slots)), ANN))
    return run


def more_scenarios() -> dict:
    return {
        "entry_member_correct_row_twins": _space_correct_row_twins,
        "entry_member_mcp_add": _mcp("memory_add", lambda row: {"content": NEW}),
        "entry_member_mcp_edit": _mcp("memory_review", lambda row: {"memory_id": row.id, "decision": "edit", "edits": NEW}),
        "entry_member_mcp_forget": _mcp("memory_forget", lambda row: {"memory_id": row.id}),
        "entry_member_proposal_approved": _proposal, "entry_member_screen_edit": _screen_review_edit,
        "entry_member_day_review_fact": _day_review_owner_fact, "entry_member_hold_the_fact": _hold_the_fact_update,
        "entry_member_value_correction": _value_correction, "entry_member_provenance_fix": _provenance_fix,
        "entry_member_provenance_forget": _provenance_forget, "entry_member_trait_challenge_flags": _trait_challenge_flags,
        "entry_member_fast_tier_supersede": _fast_tier_supersede, "entry_member_date_correction": _date_correction,
        "entry_member_provenance_forget_turn": _provenance_forget_turn, "entry_member_forget_last": _forget_last,
        "entry_member_day_review_person_fact": _day_review_person_fact, "entry_member_pet_correction": _pet_correction,
        "entry_member_brain_store": _brain_intent("memory_store", {"text": NEW}),
        "entry_member_brain_forget_entity": _brain_intent("memory_forget_entity", {"name": "Teddy"}),
        "entry_member_day_review_unwrite": _day_review_unwrite, "entry_auto_contact_rename_mirror": _contact_rename_mirror,
    }


# ── a curator (an adult admin) curating a child's or an adult's memories (``memory_curation``; ZOE_MEMORIES_CURATORS) ──────────
CURATOR, KID, GROWN = "member-ova", "member-kit", "member-wren"


class _Sqlite:
    """An in-memory SQLite account store shaped as ``db_pool``'s cursor (what ``memory_curation`` reads and audits into)."""

    def __init__(self):
        import sqlite3

        self.conn = sqlite3.connect(":memory:", check_same_thread=False)

    def execute(self, sql, params=()):
        from db_pool import _Cursor, _ExecResult

        async def _run():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()


def _curation_world(w):
    import importlib.util
    from pathlib import Path

    import provenance_answers as pa

    _on("ZOE_MEMORIES_CURATORS", "ZOE_MEMORY_PHYSICAL_ERASE")
    db = _Sqlite()
    db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT, name TEXT)")
    db.conn.execute("CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, role TEXT)")
    db.conn.execute("CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT, minor INTEGER)")
    people = [(CURATOR, "admin", "Ova Womble"), (KID, "child", "Kit Womble"), (GROWN, "user", "Wren Womble")]
    db.conn.executemany("INSERT INTO users VALUES (?, ?, ?)", [(u, "member", n) for u, _r, n in people])
    db.conn.executemany("INSERT INTO auth_users VALUES (?, ?)", [(u, r) for u, r, _n in people])
    spec = importlib.util.spec_from_file_location(
        "mig_0066", Path(__import__("memory_curation").__file__).parent / "alembic/versions/0066_memory_curation.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    for sql in mig.statements("sqlite"):
        db.conn.execute(sql)
    db.conn.commit()

    async def none(*_a, **_k):
        return frozenset()

    async def no_people(_uid):
        return []

    _set(pa, "_own_names", none)
    _set(pa, "account_names", none)
    _set(pa, "load_people", no_people)
    return db


async def _curator(db):
    import memory_curation as mc

    return await mc.account(db, CURATOR)


async def _curate_add(w):
    import memory_curation as mc

    db = _curation_world(w)
    await _entry(w, mc.add(db, await _curator(db), KID, "Kit's swimming lesson is on Tuesday.", "routines", svc=w.svc))


async def _curate_edit(w):
    import memory_curation as mc

    db = _curation_world(w)
    out = await _entry(w, mc.add(db, await _curator(db), KID, "Kit's swimming lesson is on Tuesday.", "routines", svc=w.svc))
    if isinstance(out, dict) and out.get("id"):
        await _entry(w, mc.edit(db, await _curator(db), KID, out["id"], "Kit's swimming lesson is on Thursday.", svc=w.svc))


async def _curate_forget(w):
    import memory_curation as mc

    db = _curation_world(w)
    out = await _entry(w, mc.add(db, await _curator(db), KID, "Kit's swimming lesson is on Tuesday.", "routines", svc=w.svc))
    if isinstance(out, dict) and out.get("id"):
        await _entry(w, mc.forget(db, await _curator(db), KID, out["id"], svc=w.svc))


async def _curate_held(w):
    """The child says something that disagrees with a curated row: it is held, the curator accepts it (the curated note is
    edited to what the child said) and the held note is closed."""
    import memory_curation as mc

    db = _curation_world(w)
    await _entry(w, mc.add(db, await _curator(db), KID, "Kit's teacher is Ms Lee.", "school", svc=w.svc))
    await w.call("ingest", "User's teacher is Mr Brown.", user_id=KID, source="chat_regex", status="approved")
    held = await _entry(w, mc.held(db, await _curator(db), KID, svc=w.svc))
    for note in held or []:
        await _entry(w, mc.decide_held(db, await _curator(db), KID, note["id"], "accept", svc=w.svc))


async def _curate_optout(w):
    """An adult opts a category in, a curator adds a note there, the adult opts out: the note is released to their own word."""
    import memory_curation as mc

    db = _curation_world(w)
    await _entry(w, mc.set_my_optin(db, GROWN, "likes", True, svc=w.svc))
    await _entry(w, mc.add(db, await _curator(db), GROWN, "Wren likes kites.", "likes", svc=w.svc))
    await _entry(w, mc.set_my_optin(db, GROWN, "likes", False, svc=w.svc))


def curation_scenarios() -> dict:
    return {"entry_member_curator_add": _curate_add, "entry_member_curator_edit": _curate_edit,
            "entry_member_curator_forget": _curate_forget, "entry_member_curator_held": _curate_held,
            "entry_member_curator_optout": _curate_optout}


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
        **more_scenarios(), **curation_scenarios(),
    }
