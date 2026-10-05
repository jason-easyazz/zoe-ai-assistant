"""The durable forget ledger (``memory_forgotten``, audit P2.2): the half of "forgetting that stays forgotten"
that outlives the 300 s tombstone in ``memory_tombstones``.

The properties pinned here, with synthetic names only (no network, no model, no live store, ``ci_safe``):

  * it stores ONLY a salted hash - the table has no text column and no name or topic ever reaches it
    (asserted on the table CONTENTS, on the in-process backend and on the real SQL over SQLite);
  * the hash is per user, case-blind and whole-word, and a name inside a longer phrase is found by hashing its
    word runs (nothing but the hash is ever stored);
  * the shield window ends, an explicit re-teach releases, a rotated secret orphans the hashes;
  * unconfigured it is inert and says so once; a lookup failure keeps the last known set (fail-open) and a
    failed DB write still shields this process;
  * the migration (0038) is schema-only, idempotent and reversible.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

import memory_forgotten as mf
from forgotten_support import (  # noqa: F401 - the autouse fixture + helpers
    FRIEND, OTHER, SALT, TRANSCRIPT, USER, ledger_env, load_migration, migration_sql, open_forgotten_db,
    table_dump, use_db,
)

pytestmark = pytest.mark.ci_safe


# ── the hash ─────────────────────────────────────────────────────────────────

def test_hash_is_per_user_salted_and_case_blind(monkeypatch):
    a = mf.key_hash(USER, "Dana")
    assert len(a) == 64 and int(a, 16) >= 0
    assert a == mf.key_hash(USER, "  DANA ")                      # case + whitespace blind
    assert a != mf.key_hash(OTHER, "Dana")                        # one user's hash is not another's
    assert FRIEND.lower() not in a
    monkeypatch.setenv(mf.SALT_ENV, SALT + "-rotated")
    assert a != mf.key_hash(USER, "Dana")                         # the secret IS the salt


@pytest.mark.asyncio
async def test_match_is_whole_word_and_finds_a_name_inside_a_longer_phrase():
    await mf.add(USER, "Dana")
    assert await mf.matches(USER, TRANSCRIPT)                      # inside a long sentence
    assert await mf.matches(USER, "Dana's birthday is in March")   # possessive
    assert await mf.matches(USER, "met DANA yesterday")
    assert not await mf.matches(USER, "Cordelia loves gardening")  # no substring nuking
    assert not await mf.matches(USER, "Danae rang")
    assert not await mf.matches(OTHER, TRANSCRIPT)                 # another user's same name is unaffected


@pytest.mark.asyncio
async def test_multiword_entity_needs_the_whole_phrase():
    await mf.add(USER, "Lindsay Cannon")
    assert await mf.matches(USER, "we met Lindsay Cannon at the harbour")
    assert not await mf.matches(USER, "we met Lindsay at the harbour")
    assert not await mf.matches(USER, "Cannon went home")


# ── it stores no text ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_ledger_holds_no_text_in_process(ledger_env):
    await mf.add(USER, "Dana Whitfield", actor=USER)
    await mf.add(USER, "the quiet topic about the harbour")
    rows = list(ledger_env.rows.values())
    assert len(rows) == 2
    blob = " ".join(str(v) for r in rows for v in r.values()).lower()
    for needle in ("dana", "whitfield", "harbour", "quiet", "topic"):
        assert needle not in blob
    assert set(rows[0]) == {"user_id", "key_hash", "scope", "actor", "forgotten_at", "shield_until"}


@pytest.mark.asyncio
async def test_the_ledger_table_holds_no_text_on_real_sql(monkeypatch):
    db = await open_forgotten_db()
    try:
        use_db(monkeypatch, db)
        mf.set_backend(mf.PostgresBackend())
        assert await mf.add(USER, "Dana Whitfield", actor=USER) is True
        assert await mf.add(USER, "Dana Whitfield", actor=USER) is True        # a repeat is an upsert
        rows = await table_dump(db, "memory_forgotten")
        assert len(rows) == 1
        user_id, key_hash, scope, actor, forgotten_at, shield_until = rows[0]
        assert (user_id, scope, actor) == (USER, "entity", USER)
        assert len(key_hash) == 64 and key_hash == mf.key_hash(USER, "Dana Whitfield")
        assert shield_until > forgotten_at
        everything = " ".join(str(v) for v in rows[0]).lower()
        assert "dana" not in everything and "whitfield" not in everything
        # the table cannot even hold a name: no text-bearing column exists
        async with db.execute("PRAGMA table_info(memory_forgotten)") as cur:
            cols = [r[1] for r in await cur.fetchall()]
        assert cols == ["user_id", "key_hash", "scope", "actor", "forgotten_at", "shield_until"]
        # and the SQL path answers "is this forgotten?" without ever being able to list what was
        said = "so then Dana Whitfield rang about the weekend"
        assert await mf.matches(USER, said) is True
        assert await mf.matches(OTHER, said) is False
    finally:
        await db.close()


# ── lifecycle ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_shield_window_ends(monkeypatch):
    t0 = mf._now()
    await mf.add(USER, "Dana", shield_for_days=10)
    assert await mf.matches(USER, TRANSCRIPT)
    monkeypatch.setattr(mf, "_now", lambda: t0 + timedelta(days=9))
    mf._cache.clear()
    assert await mf.matches(USER, TRANSCRIPT)
    monkeypatch.setattr(mf, "_now", lambda: t0 + timedelta(days=11))
    mf._cache.clear()
    mf._overlay.clear()
    assert not await mf.matches(USER, TRANSCRIPT)


@pytest.mark.asyncio
async def test_default_shield_is_a_year_and_env_tunable(monkeypatch, ledger_env):
    await mf.add(USER, "Dana")
    (row,) = ledger_env.rows.values()
    days = (mf.datetime.strptime(row["shield_until"], "%Y-%m-%dT%H:%M:%SZ")
            - mf.datetime.strptime(row["forgotten_at"], "%Y-%m-%dT%H:%M:%SZ")).days
    assert days == mf.DEFAULT_SHIELD_DAYS == 365
    monkeypatch.setenv(mf.SHIELD_DAYS_ENV, "30")
    assert mf.shield_days() == 30
    monkeypatch.setenv(mf.SHIELD_DAYS_ENV, "garbage")
    assert mf.shield_days() == 365


@pytest.mark.asyncio
async def test_release_drops_only_the_named_entity(ledger_env):
    await mf.add(USER, "Dana")
    await mf.add(USER, "Tove")
    assert await mf.release(USER, "remember that Dana lives in Lisbon") == 1
    assert not await mf.matches(USER, "Dana rang")
    assert await mf.matches(USER, "Tove rang")
    assert len(ledger_env.rows) == 1
    assert await mf.release(USER, "nothing forgotten in here") == 0


@pytest.mark.asyncio
async def test_a_rotated_secret_orphans_every_hash(monkeypatch):
    await mf.add(USER, "Dana")
    assert await mf.matches(USER, "Dana rang")
    monkeypatch.setenv(mf.SALT_ENV, SALT + "-rotated")
    mf._cache.clear()
    mf._overlay.clear()
    assert not await mf.matches(USER, "Dana rang")   # documented: re-forget what must stay forgotten


@pytest.mark.asyncio
async def test_keep_unforgotten_filters_turns_and_counts():
    await mf.add(USER, "Dana")
    kept, dropped = await mf.keep_unforgotten(USER, ["I made tea", TRANSCRIPT, "the weather is fine"])
    assert kept == ["I made tea", "the weather is fine"] and dropped == 1
    rows = [("a Dana row",), ("another row",)]
    kept, dropped = await mf.keep_unforgotten(USER, rows, text_of=lambda r: r[0])
    assert kept == [("another row",)] and dropped == 1
    kept, dropped = await mf.keep_unforgotten(OTHER, rows, text_of=lambda r: r[0])
    assert len(kept) == 2 and dropped == 0


# ── unconfigured / failure modes ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unconfigured_is_inert_and_says_so_once(monkeypatch, caplog, ledger_env):
    monkeypatch.delenv(mf.SALT_ENV)
    caplog.set_level(logging.WARNING, logger=mf.logger.name)
    assert mf.configured() is False
    assert await mf.add(USER, "Dana") is False
    assert await mf.add(USER, "Tove") is False
    assert ledger_env.rows == {}
    assert not await mf.matches(USER, TRANSCRIPT)
    assert mf.key_hash(USER, "Dana") == ""
    assert sum("durable forget ledger is OFF" in r.message for r in caplog.records) == 1
    assert "Dana" not in caplog.text


@pytest.mark.asyncio
async def test_a_short_secret_does_not_count_as_configured(monkeypatch):
    monkeypatch.setenv(mf.SALT_ENV, "short")
    assert mf.configured() is False


class _Boom(mf.MemoryBackend):
    def __init__(self):
        super().__init__()
        self.fail_reads = False
        self.fail_writes = False

    async def active_hashes(self, user_id, now_iso):
        if self.fail_reads:
            raise RuntimeError("db down")
        return await super().active_hashes(user_id, now_iso)

    async def upsert(self, *a, **kw):
        if self.fail_writes:
            raise RuntimeError("db down")
        return await super().upsert(*a, **kw)


@pytest.mark.asyncio
async def test_a_lookup_failure_keeps_the_last_known_set():
    b = _Boom()
    mf.set_backend(b)
    await mf.add(USER, "Dana")
    assert await mf.matches(USER, "Dana rang")        # loads + caches
    b.fail_reads = True
    mf._cache[USER] = (0.0, mf._cache[USER][1])        # the cache has expired; the DB is down
    mf._fail_until.clear()
    assert await mf.matches(USER, "Dana rang")        # fail-open on the last known set, not "forget nothing"
    assert not await mf.matches(OTHER, "Dana rang")   # and never invents entries


@pytest.mark.asyncio
async def test_a_failed_db_write_still_shields_this_process(caplog):
    b = _Boom()
    b.fail_writes = True
    b.fail_reads = True
    mf.set_backend(b)
    caplog.set_level(logging.WARNING, logger=mf.logger.name)
    assert await mf.add(USER, "Dana") is False         # not durable ...
    assert await mf.matches(USER, TRANSCRIPT)          # ... but the overlay shields the process
    assert "in-process only" in caplog.text and "Dana" not in caplog.text
    assert await mf.release(USER, "remember that Dana rang") in (0, 1)  # releasing never raises


# ── who may bypass the ledger ────────────────────────────────────────────────

@pytest.mark.parametrize("writer,verified,expected", [
    ("voice_fact", None, True),
    ("voice_fact", True, True),
    ("voice_fact", False, False),        # the speaker-id rejected the member: not the user's own words
    ("review_ui", None, True),
    ("explicit_teach", None, True),      # the brain acting on the user's explicit "remember ..." turn
    ("brain_tool", None, False),         # the 4B brain's own paraphrase has no exemption any more
    ("digest", None, False),
    ("turn_digest", None, False),
    ("idle_consolidation", None, False),
    ("chat_regex", None, False),         # mined, not an explicit teach
    ("voice_regex", None, False),
    ("mcp", None, False),
    ("", None, False),
])
def test_only_an_explicit_reteach_by_a_verified_speaker_bypasses(writer, verified, expected):
    assert mf.is_explicit_reteach(writer, user_id=USER, speaker_verified=verified) is expected


# ── the migration ────────────────────────────────────────────────────────────

def test_migration_0038_is_schema_only_idempotent_and_reversible():
    migration = load_migration()
    assert (migration.revision, migration.down_revision) == ("0038", "0037")
    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()                                  # IF NOT EXISTS: a rerun is safe
        cols = {r[1]: r for r in conn.execute(text("PRAGMA table_info(memory_forgotten)")).fetchall()}
        assert list(cols) == ["user_id", "key_hash", "scope", "actor", "forgotten_at", "shield_until"]
        assert [cols[c][5] for c in ("user_id", "key_hash")] == [1, 2]            # composite primary key
        assert conn.execute(text("SELECT count(*) FROM memory_forgotten")).scalar() == 0   # no backfill
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
            migration.downgrade()
        assert conn.execute(text(
            "SELECT count(*) FROM sqlite_master WHERE name = 'memory_forgotten'")).scalar() == 0


def test_migration_sql_has_no_text_column():
    sql = migration_sql("upgrade").lower()
    assert "key_hash text" in sql and "shield_until text" in sql
    for forbidden in ("name", "topic", "entity text", "phrase", "content"):
        assert forbidden not in sql.replace("memory_forgotten", "")
