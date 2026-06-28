import os
import sys
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import journal


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingDb:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for needle, rows in self.routes.items():
            if needle in sql:
                return _Cursor(rows)
        return _Cursor([])

    async def commit(self):
        pass


async def _allow_feature(*_args, **_kwargs):
    return None


def _user():
    return {"user_id": "U1"}


@pytest.mark.asyncio
async def test_list_entries_uses_postgres_date_casts(monkeypatch):
    monkeypatch.setattr(journal, "require_feature_access", _allow_feature)
    db = _RecordingDb()

    await journal.list_entries(
        limit=10,
        offset=0,
        mood=None,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 28),
        search="field note",
        user=_user(),
        db=db,
    )

    sql, params = db.calls[0]
    assert "date(created_at)" not in sql
    assert "THEN created_at::timestamp::date END >= ?" in sql
    assert "THEN created_at::timestamp::date END <= ?" in sql
    assert "LIKE ? ESCAPE '\\'" in sql
    assert params == [
        "U1",
        date(2026, 6, 1),
        date(2026, 6, 28),
        "%field note%",
        "%field note%",
        10,
        0,
    ]


@pytest.mark.asyncio
async def test_on_this_day_uses_postgres_to_char_and_current_date(monkeypatch):
    monkeypatch.setattr(journal, "require_feature_access", _allow_feature)
    db = _RecordingDb()

    await journal.list_on_this_day(user=_user(), db=db)

    sql, params = db.calls[0]
    assert "strftime" not in sql
    assert "date('now')" not in sql
    assert "to_char((CASE WHEN created_at ~" in sql
    assert "THEN created_at::timestamp END), 'MM-DD')" in sql
    assert "THEN created_at::timestamp::date END < CURRENT_DATE" in sql
    assert params == ["U1", date.today().strftime("%m-%d")]


@pytest.mark.asyncio
async def test_streak_uses_postgres_date_cast_and_date_objects(monkeypatch):
    monkeypatch.setattr(journal, "require_feature_access", _allow_feature)
    today = date.today()
    db = _RecordingDb(
        {
            "COUNT(*)": [(3,)],
            "created_at::timestamp::date": [
                (today,),
                (today - timedelta(days=1),),
                (today - timedelta(days=3),),
            ],
        }
    )

    result = await journal.get_streak_stats(user=_user(), db=db)

    assert result == {"current_streak": 2, "longest_streak": 2, "total_entries": 3}
    sql, _params = db.calls[1]
    assert "date(created_at)" not in sql
    assert "DISTINCT CASE WHEN created_at ~" in sql
    assert "THEN created_at::timestamp::date END as d" in sql


@pytest.mark.asyncio
async def test_search_escapes_like_wildcards(monkeypatch):
    monkeypatch.setattr(journal, "require_feature_access", _allow_feature)
    db = _RecordingDb()

    await journal.list_entries(
        limit=10,
        offset=0,
        mood=None,
        start_date=None,
        end_date=None,
        search=r"100%_done\ok",
        user=_user(),
        db=db,
    )

    sql, params = db.calls[0]
    assert "LIKE ? ESCAPE '\\'" in sql
    assert params[1:3] == [r"%100\%\_done\\ok%", r"%100\%\_done\\ok%"]


def test_list_entries_rejects_malformed_date_and_overlong_search(monkeypatch):
    monkeypatch.setattr(journal, "require_feature_access", _allow_feature)
    app = FastAPI()
    app.include_router(journal.router)
    app.dependency_overrides[journal.get_current_user] = _user
    app.dependency_overrides[journal.get_db] = lambda: _RecordingDb()
    client = TestClient(app)

    malformed_date = client.get("/api/journal/entries?start_date=2026-6-28")
    assert malformed_date.status_code == 422

    impossible_date = client.get("/api/journal/entries?end_date=2026-02-31")
    assert impossible_date.status_code == 422

    long_search = client.get("/api/journal/entries", params={"search": "x" * 201})
    assert long_search.status_code == 422


def _anniversary(today):
    year = today.year - 1
    while True:
        try:
            return today.replace(year=year)
        except ValueError:
            year -= 1


@pytest.mark.asyncio
async def test_journal_router_queries_run_against_text_created_at_on_postgres(monkeypatch):
    postgres_url = os.environ.get("POSTGRES_URL")
    if not postgres_url:
        pytest.skip("POSTGRES_URL not set - no live Postgres to validate TEXT casts")
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        pytest.skip("asyncpg not installed")

    import db_pool

    monkeypatch.setattr(journal, "require_feature_access", _allow_feature)
    conn = await asyncpg.connect(postgres_url)
    try:
        await conn.execute(
            """CREATE TEMP TABLE journal_entries (
                   id text, user_id text, title text, content text, mood text,
                   mood_score int, tags text, weather text, location text,
                   photos text, privacy_level text, visibility text,
                   created_at text, updated_at text, deleted int
               )"""
        )
        today = date.today()
        anniv = _anniversary(today)

        async def insert_entry(entry_id, user_id, title, created_day):
            ts = f"{created_day.isoformat()} 09:00:00+00"
            await conn.execute(
                """INSERT INTO journal_entries VALUES (
                       $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15
                   )""",
                entry_id,
                user_id,
                title,
                title,
                "ok",
                5,
                None,
                None,
                None,
                None,
                "private",
                "personal",
                ts,
                ts,
                0,
            )

        await insert_entry("anniv", "U1", "one year ago", anniv)
        await insert_entry("today", "U1", "today", today)
        await insert_entry("s0", "U2", "day zero", today)
        await insert_entry("s1", "U2", "day one", today - timedelta(days=1))
        await conn.execute(
            """INSERT INTO journal_entries VALUES (
                   $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15
               )""",
            "bad",
            "U1",
            "bad timestamp",
            "bad timestamp",
            "ok",
            5,
            None,
            None,
            None,
            None,
            "private",
            "personal",
            "not-a-date",
            "not-a-date",
            0,
        )
        await conn.execute(
            """INSERT INTO journal_entries VALUES (
                   $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15
               )""",
            "bad-prefix",
            "U1",
            "bad timestamp prefix",
            "bad timestamp prefix",
            "ok",
            5,
            None,
            None,
            None,
            None,
            "private",
            "personal",
            "2026-02-30 25:00:00",
            "2026-02-30 25:00:00",
            0,
        )

        db = db_pool.AsyncpgCompat(conn)

        listed = await journal.list_entries(
            limit=50,
            offset=0,
            mood=None,
            start_date=anniv,
            end_date=anniv,
            search=None,
            user={"user_id": "U1"},
            db=db,
        )
        assert [entry["id"] for entry in listed["entries"]] == ["anniv"]

        on_this_day = await journal.list_on_this_day(user={"user_id": "U1"}, db=db)
        ids = {entry["id"] for entry in on_this_day["entries"]}
        assert "anniv" in ids
        assert "today" not in ids

        streak = await journal.get_streak_stats(user={"user_id": "U2"}, db=db)
        assert streak["total_entries"] == 2
        assert streak["current_streak"] == 2
    finally:
        await conn.close()
