"""Partial erases retain their retry targets; legacy excerpts resolve before erasing."""
import contextlib
import sqlite3

import pytest

import forget_record
import forget_topic
import night_store
import turn_forget
import exact_words as xw
from forgotten_support import ledger_env  # noqa: F401 - fixture
from test_forget_by_turn_id import UID, run, world  # noqa: F401 - fixture
from test_forget_record_matrix import _Sqlite, _mig

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize("native_transaction", [False, True], ids=["sqlite", "postgres-shaped"])
def test_orphan_delete_failure_keeps_observations_and_record_open(world, monkeypatch, tmp_path, native_transaction):
    svc, _chat = world
    db = _Sqlite(str(tmp_path / "night.db"))
    migration = _mig("0040_night_mind")
    monkeypatch.setattr(migration.op, "execute", db.conn.execute)
    migration.upgrade()
    db.conn.commit()
    if native_transaction:
        # Like asyncpg: each statement commits unless inside transaction(), and commit() is a no-op.
        db.conn.isolation_level = None

        @contextlib.asynccontextmanager
        async def transaction():
            db.conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                db.conn.rollback()
                raise
            else:
                db.conn.commit()

        async def commit():
            pass
        db.transaction = transaction
        db.commit = commit

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(night_store.SqlBackend, "_ctx", staticmethod(ctx))
    backend = night_store.SqlBackend()
    night_store.set_backend(backend)
    run(backend.put_thread(night_store.thread_row(id="t-zindle", user_id=UID, title="zindle florns", anchors="zindle")))
    run(backend.put_observation(night_store.obs_row(id="o-zindle", user_id=UID, thread_id="t-zindle", quote="zindle florns")))
    db.fail["night_threads"] = 1

    async def resolve():
        return forget_record.Plan(obs=["o-zindle"])

    def forget():
        return run(forget_record.forget(UID, code="topic", key="zindle-retry", svc=svc, resolve=resolve))

    first = forget()
    assert not first.complete and "night_mind" in first.failed
    assert db.q("SELECT id FROM night_observations") == [("o-zindle",)]
    assert db.q("SELECT id FROM night_threads") == [("t-zindle",)]
    # A separate connection proves these are committed rows, not just a pending rollback.
    with sqlite3.connect(str(tmp_path / "night.db")) as reader:
        assert reader.execute("SELECT id FROM night_observations").fetchall() == [("o-zindle",)]
    assert run(forget_record.get_backend().open_records(UID))
    assert forget().complete
    assert not db.q("SELECT id FROM night_observations")
    assert not db.q("SELECT id FROM night_threads")
    assert not run(forget_record.get_backend().open_records(UID))


def test_legacy_topic_after_restart_resolves_excerpt_to_saved_turn(world):
    svc, chat = world
    said = "My zindle florns beside the vesk"
    mid = "m-zindle"
    chat.rows[mid] = [said, "{}"]
    ref = run(svc.ingest("User's zindle florns beside the vesk", user_id=UID, source="turn_digest",
                         status="approved", source_excerpt=said))
    assert ref
    meta = svc._collection().rows[ref.id][1]
    meta.pop("source_kind", None)
    meta.pop("source_turn", None)
    run(night_store.get_backend().put_observation(night_store.obs_row(
        id="o-zindle", user_id=UID, turn_id=mid, quote=said)))
    turn_forget.reset()
    assert not xw.get_backend().rows
    res = run(forget_topic.forget(svc, UID, "zindle"))
    assert res.forgotten and not (res.store_down or res.failed)
    assert chat.off(mid)
    assert not run(night_store.get_backend().observations(UID))
    assert not run(forget_record.get_backend().open_records(UID))


def test_memory_orphan_failure_keeps_observation_for_retry(world):
    svc, _chat = world
    backend = night_store.get_backend()

    class Flaky(dict):
        left = 1

        def __delitem__(self, key):
            if self.left:
                self.left -= 1
                raise ValueError("zindle")
            super().__delitem__(key)

    backend.thread_rows = Flaky()
    run(backend.put_thread(night_store.thread_row(id="t-zindle", user_id=UID, title="zindle")))
    run(backend.put_observation(night_store.obs_row(id="o-zindle", user_id=UID, thread_id="t-zindle", quote="zindle florns")))

    async def resolve():
        return forget_record.Plan(obs=["o-zindle"])

    def forget():
        return run(forget_record.forget(UID, code="topic", key="zindle-memory", svc=svc, resolve=resolve))

    assert not forget().complete
    assert "o-zindle" in backend.obs_rows
    assert run(forget_record.get_backend().open_records(UID))
    assert forget().complete
    assert not backend.obs_rows and not backend.thread_rows
    assert not run(forget_record.get_backend().open_records(UID))
