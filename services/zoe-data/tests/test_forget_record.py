"""The forget RECORD's own contract (``forget_record``): ids only, a durable store forget itself reaches, compare-and-set writes,
nothing destroyed before the record is written, abandoned records finished by the existing sweep (never expired), and the step
erasers reachable from nowhere but the record. The per-entry-point x per-step failure matrix is ``test_forget_record_matrix.py``.
Synthetic placeholder ids and words only."""
from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
from pathlib import Path

import pytest

import forget_record as fr
from test_forget_record_matrix import FACTS, UID, Injected, _approved, _left, w, world  # noqa: F401 - fixtures
from forgotten_support import ledger_env  # noqa: F401 - fixture

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


def _forget_last():
    import intent_router
    return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))


def test_a_plan_refuses_anything_that_is_not_an_id():
    assert fr.Plan(rows=["zoe_row_1"], mids=["m-1"]).rows == ["zoe_row_1"]
    for bad in ("two words", "x" * 201, "line\nbreak"):
        with pytest.raises(ValueError):
            fr.Plan(rows=[bad])
    assert fr.Plan(reg_turns=[None, "ut-1"]).reg_turns == [None, "ut-1"]     # "could not be computed" is a code, not words


def test_an_open_record_holds_ids_only_and_the_retry_finishes_it_from_the_record(w):
    w.db.fail["user_model_cards"] = 1
    assert not _forget_last().startswith("Done")
    (rec,) = run(fr.get_backend().open_records(UID))
    raw = rec.raw.lower()
    for word in ("plumber", "example", "street", "dentist", "works"):
        assert word not in raw, f"the record holds the word {word!r}"
    assert set(rec.plan.rows) == {w.fact.id, w.copy.id} and w.mid in rec.plan.mids and "card" not in rec.done
    assert set(rec.done) >= {"reject", "erase_rows", "transcript", "exact_words"}       # the rest ran (fail closed, erase all)
    rows = w.db.q("SELECT key FROM app_settings")
    assert [r[0] for r in rows] == [rec.key] and rec.key.startswith(fr.PREFIX + UID + ":")   # the REAL table, by a hash of the key
    assert _forget_last().startswith("Done")
    assert not any(_left(w, "forget_last").values()) and not w.db.q("SELECT key FROM app_settings")


def test_nothing_is_destroyed_when_the_record_cannot_be_written(w, monkeypatch):
    import intent_router

    async def down(*_a):
        raise Injected("app_settings down")
    with monkeypatch.context() as patch:
        patch.setattr(fr.SqlBackend, "create", down)
        assert _forget_last() == intent_router.FORGET_STORE_DOWN_REPLY
        assert FACTS["a"] in _approved(w.svc) and not w.chat.off(w.mid)         # write-ahead: not a thing was touched
    assert _forget_last().startswith("Done")


def test_an_abandoned_record_is_finished_by_the_existing_sweep_and_never_expires(w, caplog):
    import memory_forget_cascade

    w.db.fail["conversation_notes"] = 99
    assert not _forget_last().startswith("Done")
    (rec,) = run(fr.get_backend().open_records(UID))
    rec.tries = fr.STUCK_TRIES
    run(fr.get_backend().cas(rec.key, rec.raw, rec.dump()))
    caplog.set_level(logging.ERROR)
    run(memory_forget_cascade.retry_pending(UID))                    # the nightly pre-read's sweep: still failing
    assert run(fr.get_backend().open_records(UID)), "a record that cannot finish is KEPT, never dropped"
    assert any("FORGET_RECORD code=stuck" in r.getMessage() for r in caplog.records)
    w.db.fail.clear()
    run(memory_forget_cascade.retry_pending(UID))                    # nobody repeated the forget: the sweep finishes it
    assert not run(fr.get_backend().open_records(UID)) and not any(_left(w, "forget_last").values())


def test_the_member_erase_removes_the_members_open_records_only(w):
    w.db.fail["user_model_cards"] = 1
    _forget_last()
    run(fr.get_backend().create(fr.storage_key("demo_bar_0000id09", "row:x"), '{"v": 1}'))
    assert run(fr.forget_user(UID)) == 1
    assert [r[0] for r in w.db.q("SELECT key FROM app_settings")] == [fr.storage_key("demo_bar_0000id09", "row:x")]


def test_a_record_changed_under_an_attempt_is_not_confirmed(w):
    """Compare-and-set: another process advanced the record (or the member erase removed it) - this attempt stops, unconfirmed."""
    w.db.fail["user_model_cards"] = 1
    _forget_last()
    (rec,) = run(fr.get_backend().open_records(UID))
    assert not run(fr.get_backend().cas(rec.key, rec.raw + " ", rec.dump()))          # a stale copy never overwrites
    run(fr.forget_user(UID))
    out = run(fr._run(rec, w.svc))
    assert not out.complete and isinstance(out.error, fr.Conflict)


def test_two_requests_for_the_same_target_share_one_record(w):
    w.db.fail["user_model_cards"] = 1
    import intent_router

    async def both():
        return await asyncio.gather(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID),
                                    intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))
    replies = run(both())
    assert sum(r.startswith("Done") for r in replies) == 1                # the second waited, then finished the first's record
    assert not run(fr.get_backend().open_records(UID)) and not any(_left(w, "forget_last").values())


def test_a_process_that_never_had_a_database_keeps_records_in_process(monkeypatch):
    import db_compat

    @contextlib.asynccontextmanager
    async def never():
        raise RuntimeError("db pool not initialised")
        yield
    monkeypatch.setattr(db_compat, "get_compat_db", never)
    fr.set_backend(fr.SqlBackend())
    assert run(fr.get_backend().create("forget_rec:u:k", "{}")) and run(fr.get_backend().get("forget_rec:u:k")) == "{}"


#: the step erasers: called from forget_record's steps and nowhere else in the service (the module that defines one may use it)
STEP_ERASERS = {"flag_transcript_turns", "erase_entries", "erase_observations", "erase_for_turns", "erase_ids", "erase_for_turn_ids",
                "withdraw_ids", "invalidate_for_rows", "erase_turns", "erase_text", "forget_transcript", "erase_turn_words",
                "run_plan", "erase_words"}


def test_no_forget_path_runs_a_step_eraser_outside_the_record():
    """A new forget entry point must go through ``forget_record.forget`` (and so into the matrix): calling a step's eraser directly
    from another module is the per-site pattern this record replaced, and fails here."""
    stray = []
    for p in sorted(ROOT.rglob("*.py")):
        if "tests" in p.parts or "alembic" in p.parts or p.name == "forget_record.py":
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in STEP_ERASERS \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id != "self":
                if (p.name, node.func.attr) not in ALLOWED:
                    stray.append(f"{p.name}:{node.lineno}:{node.func.value.id}.{node.func.attr}")
    assert stray == [], stray


#: ``turn_forget.erase_turn_words`` (the steps without a record, for a caller with no rows to hide) is itself a STEP_ERASER, so no
#: service code may call it: it has test callers only
ALLOWED = {("turn_forget.py", "run_plan")}


def test_the_stray_eraser_check_sees_a_direct_call():
    """Negative control for the structural check above."""
    src = "async def f(u):\n    import exact_words\n    await exact_words.flag_transcript_turns(u, ['m'])\n"
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in STEP_ERASERS]
    assert len(calls) == 1
