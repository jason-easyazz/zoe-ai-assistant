"""Integration tests: People CRM — PostgreSQL + MemPalace sync.

Tests the dual fan-out: writing a person + activity results in a MemPalace
entry with entity_id=<DB UUID>.

These tests require:
  - POSTGRES_URL set in environment (or auto-loaded from services/zoe-data/.env)
  - MemPalace directory accessible (MEMPALACE_DATA env or default path)

Run with:
  pytest tests/integration/test_people_mempalace_sync.py -v
"""

import os
import sys
import uuid
from datetime import datetime

import pytest

# Load env from services/zoe-data/.env if POSTGRES_URL not set
if not os.environ.get('POSTGRES_URL'):
    _dotenv = os.path.join(os.path.dirname(__file__), '../../services/zoe-data/.env')
    if os.path.exists(_dotenv):
        for line in open(_dotenv):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/zoe-data'))


@pytest.fixture(scope="module")
async def db_conn():
    """Open an asyncpg connection for tests."""
    import asyncpg
    url = os.environ.get('POSTGRES_URL', '')
    if not url:
        pytest.skip("POSTGRES_URL not set")
    try:
        conn = await asyncpg.connect(url)
    except Exception as e:
        pytest.skip(f"Cannot connect to PostgreSQL: {e}")
        return
    yield conn
    await conn.close()


class AsyncpgCompat:
    """Minimal compatibility wrapper so person_extractor works with raw asyncpg."""
    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql, *args):
        pg_sql = sql.replace('?', '$1')
        i = [0]
        def repl(m):
            i[0] += 1
            return f'${i[0]}'
        import re
        pg_sql = re.sub(r'\?', repl, sql)
        rows = await self._conn.fetch(pg_sql, *args)
        return _FakeCursor(rows)

    async def commit(self):
        pass  # autocommit in asyncpg


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


async def _cleanup_person_fanout_artifacts(
    db_conn,
    memory_service,
    *,
    person_id: str,
    user_id: str,
    mem_ids: set[str],
) -> None:
    """Remove only artifacts created by the dual-fanout integration test."""
    try:
        collection = memory_service._collection()
        result = collection.get(
            where={"$and": [{"user_id": user_id}, {"entity_id": person_id}]},
            include=[],
        )
        mem_ids.update(str(mid) for mid in (result.get("ids") or []) if mid)
    except Exception:
        pass

    if mem_ids:
        try:
            await memory_service._run_sync(memory_service._delete_ids, sorted(mem_ids))
        except Exception:
            pass

    await db_conn.execute("DELETE FROM person_activities WHERE person_id=$1 AND user_id=$2", person_id, user_id)
    await db_conn.execute("DELETE FROM people WHERE id=$1 AND user_id=$2", person_id, user_id)


@pytest.mark.asyncio
async def test_create_person_has_crm_columns(db_conn):
    """New people table has circle, health_score, notification_count columns."""
    row = await db_conn.fetchrow(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='people' AND column_name='circle' LIMIT 1"
    )
    assert row is not None, "people.circle column missing — run alembic upgrade head"


@pytest.mark.asyncio
async def test_new_tables_exist(db_conn):
    """All CRM tables must exist."""
    for table in [
        'person_activities', 'person_important_dates',
        'person_gift_ideas', 'person_bucket_list', 'person_relationships',
    ]:
        row = await db_conn.fetchrow(
            "SELECT table_name FROM information_schema.tables WHERE table_name=$1", table
        )
        assert row is not None, f"Table {table} is missing"


@pytest.mark.asyncio
async def test_new_columns_exist(db_conn):
    """0007 migration columns must exist on people table."""
    for col in ['context', 'is_partial', 'how_we_met', 'first_met_date', 'introduced_by_person_id']:
        row = await db_conn.fetchrow(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='people' AND column_name=$1", col
        )
        assert row is not None, f"people.{col} column missing — run alembic upgrade head"


@pytest.mark.asyncio
async def test_person_extractor_dual_fanout(db_conn):
    """Create a person, run process_text → verify DB activity row AND MemPalace entry."""
    from person_extractor import process_text
    from memory_service import get_memory_service

    person_id = str(uuid.uuid4())
    test_user = f"test-crm-sync-user-{person_id[:8]}"
    person_name = f"Zynthia_{person_id[:6]}"
    memory_service = get_memory_service()
    created_mem_ids: set[str] = set()

    try:
        memory_service._collection()
    except Exception as e:
        pytest.skip(f"MemPalace/vector store unavailable: {e}")

    try:
        # Insert a test person
        await db_conn.execute(
            "INSERT INTO people (id, user_id, name, relationship, circle, context, visibility) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            person_id, test_user, person_name, "friend", "circle", "personal", "family",
        )

        db_compat = AsyncpgCompat(db_conn)
        count = await process_text(
            f"{person_name} loves hiking in the mountains.",
            user_id=test_user,
            source="test",
            db=db_compat,
        )

        assert count == 1, "Expected process_text to write the detected person preference"

        # Verify: DB activity row written and linked to the MemPalace write.
        row = await db_conn.fetchrow(
            "SELECT id, activity_type, description, mem_id FROM person_activities WHERE person_id=$1 LIMIT 1",
            person_id,
        )
        assert row is not None, "Expected a person_activities row for the extracted preference"
        assert row["activity_type"] == "fact"
        assert person_name in row["description"]
        assert "hiking in the mountains" in row["description"]
        assert row["mem_id"], "Expected person_activities.mem_id to reference the MemPalace record"
        created_mem_ids.add(str(row["mem_id"]))

        # Verify: actual MemPalace/vector side effect is readable, not patched away.
        memory = await memory_service.get(str(row["mem_id"]))
        assert memory is not None, f"MemPalace record {row['mem_id']} was not found"
        assert memory.metadata["entity_id"] == person_id
        assert memory.metadata["entity_type"] == "person"
        assert memory.metadata["user_id"] == test_user
        assert "hiking in the mountains" in memory.text

        people_row = await db_conn.fetchrow(
            "SELECT notification_count, health_score, last_contacted_at FROM people WHERE id=$1",
            person_id,
        )
        assert people_row is not None
        assert people_row["notification_count"] > 0, "_post_write_hooks should increment notification_count"
        assert people_row["last_contacted_at"] is not None, "_post_write_hooks should update last_contacted_at"
        assert people_row["health_score"] is not None, "_post_write_hooks should recalculate health_score"
    finally:
        await _cleanup_person_fanout_artifacts(
            db_conn,
            memory_service,
            person_id=person_id,
            user_id=test_user,
            mem_ids=created_mem_ids,
        )


@pytest.mark.asyncio
async def test_health_score_recalc(db_conn):
    """recalc_and_save updates health_score in DB."""
    from person_health import recalc_and_save

    test_user = "test-health-user"
    person_id = str(uuid.uuid4())

    await db_conn.execute(
        "INSERT INTO people (id, user_id, name, circle, context, last_contacted_at, contact_count, health_score, visibility) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        person_id, test_user, f"HealthTest_{person_id[:4]}", "circle", "personal",
        datetime.utcnow().isoformat() + "Z", 5, 0.5, "family",
    )

    db_compat = AsyncpgCompat(db_conn)
    score = await recalc_and_save(person_id, test_user, db_compat)
    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"

    # Clean up
    await db_conn.execute("DELETE FROM people WHERE id=$1", person_id)


def test_fields_route_order_is_correct():
    """The /api/people/fields route fix: /fields must come before /{person_id} in source."""
    source_path = os.path.join(os.path.dirname(__file__), '../../services/zoe-data/routers/people.py')
    with open(source_path) as f:
        source = f.read()
    try:
        fields_pos = source.index('@router.get("/fields")')
        person_id_pos = source.index('@router.get("/{person_id}")')
        assert fields_pos < person_id_pos, \
            "/fields route must be declared BEFORE /{person_id} to avoid route collision"
    except ValueError as e:
        pytest.fail(f"Route declaration not found: {e}")
